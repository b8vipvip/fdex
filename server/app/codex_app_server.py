from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.codex_notification_bus import publish_transport_notification
from app.codex_process_isolation import CodexProcessIsolationSpec, build_codex_process_isolation

JsonObject = dict[str, Any]
NotificationHandler = Callable[[str, JsonObject], Awaitable[None]]
ServerRequestHandler = Callable[[str, JsonObject], Awaitable[Any]]


class CodexRpcError(RuntimeError):
    def __init__(self, method: str, code: int, message: str, data: Any = None) -> None:
        self.method = method
        self.code = int(code)
        self.data = data
        super().__init__(f"Codex RPC {method} failed ({self.code}): {message}")


class CodexTransportClosed(RuntimeError):
    pass


class CodexServerRequestDenied(RuntimeError):
    """Raised by the FDEX policy layer to fail closed on a server-initiated request."""


class CodexAppServerClient:
    """Version-tolerant JSON-RPC client for the official `codex app-server`.

    The public Codex app-server protocol is the compatibility boundary. FDEX deliberately
    keeps this client schema-light: new methods/notifications can pass through without
    waiting for a matching Python SDK release. Typed validation belongs at the FDEX feature
    boundary that actually consumes a method.

    Phase 7.32 launches every FDEX-provider Host through an operator-owned transient systemd
    service. That service owns the complete cgroup beneath app-server, including shell commands,
    sub-agents, stdio MCP helpers and future executable plugins. Closing a transport must then
    terminate the whole control group, not merely the direct relay process. Low-level transport
    tests that intentionally start the public binary without an FDEX provider key stay direct.
    """

    def __init__(
        self,
        launch_args: tuple[str, ...],
        *,
        env: dict[str, str],
        cwd: Path,
        client_version: str,
        request_timeout: float = 30.0,
        notification_handler: NotificationHandler | None = None,
        server_request_handler: ServerRequestHandler | None = None,
        experimental_api: bool = True,
        process_isolation: CodexProcessIsolationSpec | None = None,
    ) -> None:
        if not launch_args:
            raise ValueError("Codex launch_args cannot be empty")
        self.launch_args = tuple(str(item) for item in launch_args)
        self.env = dict(env)
        self.cwd = cwd.resolve()
        self.client_version = str(client_version or "unknown")
        self.request_timeout = max(1.0, float(request_timeout))
        self.notification_handler = notification_handler
        self.server_request_handler = server_request_handler
        self.experimental_api = bool(experimental_api)
        self.process_isolation = process_isolation
        self._auto_isolation = process_isolation is None and bool(
            self.env.get("FDEX_CODEX_PROVIDER_KEY") and self.env.get("CODEX_HOME")
        )
        self._isolation_nonce = uuid.uuid4().hex
        self.effective_launch_args = self.launch_args

        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int | str, asyncio.Future[Any]] = {}
        self._request_methods: dict[int | str, str] = {}
        self._notifications: asyncio.Queue[tuple[str, JsonObject]] = asyncio.Queue(maxsize=2000)
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_lines: deque[str] = deque(maxlen=200)
        self._closed = False
        self.initialize_result: JsonObject = {}

    async def __aenter__(self) -> "CodexAppServerClient":
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_lines)

    @property
    def process_unit(self) -> str:
        return self.process_isolation.unit_name if self.process_isolation is not None else ""

    async def _resolve_process_isolation(self) -> None:
        if not self._auto_isolation or self.process_isolation is not None:
            return
        # CODEX_HOME is already owner-scoped by FDEX. It is hashed by the isolation module and is
        # never exposed in the unit name. A per-client nonce prevents read-only capability Hosts
        # that share one cwd from terminating each other.
        self.process_isolation = await asyncio.to_thread(
            build_codex_process_isolation,
            str(self.env.get("CODEX_HOME") or "owner"),
            f"{self.cwd}\0{self._isolation_nonce}",
        )

    async def start(self) -> JsonObject:
        if self.process is not None:
            return self.initialize_result
        if self._closed:
            raise CodexTransportClosed("Codex app-server client is closed")
        await self._resolve_process_isolation()
        launch_args = self.launch_args
        if self.process_isolation is not None:
            await asyncio.to_thread(self.process_isolation.prepare)
            launch_args = self.process_isolation.wrap_launch_args(self.launch_args, self.env)
        self.effective_launch_args = tuple(launch_args)
        self.process = await asyncio.create_subprocess_exec(
            *self.effective_launch_args,
            cwd=str(self.cwd),
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._reader_loop(), name="fdex-codex-app-server-reader")
        self._stderr_task = asyncio.create_task(self._stderr_loop(), name="fdex-codex-app-server-stderr")
        try:
            result = await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "fdex",
                        "title": "FDEX Coding Agent",
                        "version": self.client_version,
                    },
                    "capabilities": {
                        "experimentalApi": self.experimental_api,
                    },
                },
            )
            self.initialize_result = result if isinstance(result, dict) else {}
            # ClientNotification is exactly {"method":"initialized"}; no params are
            # permitted by the generated official app-server schema.
            await self.notify("initialized")
            return self.initialize_result
        except Exception:
            await self.close()
            raise

    async def request(
        self,
        method: str,
        params: JsonObject | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if self.process is None:
            raise CodexTransportClosed("Codex app-server has not been started")
        if self.process.returncode is not None:
            raise CodexTransportClosed(self._closed_message())
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        self._request_methods[request_id] = method
        payload: JsonObject = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            await self._send(payload)
            return await asyncio.wait_for(
                future,
                timeout=self.request_timeout if timeout is None else max(1.0, float(timeout)),
            )
        except asyncio.TimeoutError as exc:
            raise CodexRpcError(method, -32002, "request timed out") from exc
        finally:
            self._pending.pop(request_id, None)
            self._request_methods.pop(request_id, None)

    async def notify(self, method: str, params: JsonObject | None = None) -> None:
        payload: JsonObject = {"method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

    async def next_notification(self, *, timeout: float | None = None) -> tuple[str, JsonObject]:
        if timeout is None:
            return await self._notifications.get()
        try:
            return await asyncio.wait_for(self._notifications.get(), max(0.1, float(timeout)))
        except asyncio.TimeoutError as exc:
            raise CodexRpcError("notification", -32002, "notification wait timed out") from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self.process
        tree_error: Exception | None = None
        if proc is not None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                    await proc.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            if proc.returncode is None and self.process_isolation is not None:
                try:
                    await asyncio.to_thread(self.process_isolation.terminate_tree)
                except Exception as exc:
                    tree_error = exc
            if proc.returncode is None:
                if self.process_isolation is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    # With isolation this only kills the systemd-run relay. The cgroup controller
                    # above has already performed TERM -> KILL against the complete service tree.
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
        current = asyncio.current_task()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and task is not current:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._fail_pending(CodexTransportClosed(self._closed_message()))
        if tree_error is not None:
            raise CodexTransportClosed(f"Codex cgroup tree cleanup failed: {tree_error}") from tree_error

    async def _send(self, payload: JsonObject) -> None:
        proc = self.process
        if proc is None or proc.stdin is None:
            raise CodexTransportClosed("Codex app-server stdin is unavailable")
        if proc.returncode is not None:
            raise CodexTransportClosed(self._closed_message())
        wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            try:
                proc.stdin.write(wire.encode("utf-8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise CodexTransportClosed(self._closed_message()) from exc

    async def _reader_loop(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    asyncio.create_task(self._handle_server_request(message))
                    continue
                if "method" in message:
                    method = str(message.get("method") or "")
                    params = message.get("params")
                    normalized = params if isinstance(params, dict) else {}
                    await self._publish_notification(method, normalized)
                    continue
                if "id" in message:
                    self._resolve_response(message)
        except asyncio.CancelledError:
            raise
        finally:
            if not self._closed:
                return_code = await proc.wait()
                self._fail_pending(
                    CodexTransportClosed(
                        f"Codex app-server exited unexpectedly with code {return_code}. {self.stderr_tail[-2000:]}"
                    )
                )

    async def _stderr_loop(self) -> None:
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._stderr_lines.append(text[:4000])
        except asyncio.CancelledError:
            raise

    async def _publish_notification(self, method: str, params: JsonObject) -> None:
        # Persist the complete bounded protocol stream before higher-level handlers reduce it to
        # human progress summaries. The capture is a no-op outside an owner/task Host context.
        await publish_transport_notification(method, params)
        if self._notifications.full():
            try:
                self._notifications.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._notifications.put_nowait((method, params))
        if self.notification_handler is not None:
            await self.notification_handler(method, params)

    def _resolve_response(self, message: JsonObject) -> None:
        request_id = message.get("id")
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        method = self._request_methods.get(request_id, "unknown")
        error = message.get("error")
        if isinstance(error, dict):
            future.set_exception(
                CodexRpcError(
                    method,
                    int(error.get("code") or -32000),
                    str(error.get("message") or "unknown Codex RPC error"),
                    error.get("data"),
                )
            )
            return
        future.set_result(message.get("result"))

    async def _handle_server_request(self, message: JsonObject) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params")
        normalized = params if isinstance(params, dict) else {}
        try:
            if self.server_request_handler is None:
                raise CodexServerRequestDenied(
                    f"FDEX has no handler for server request {method}; request denied"
                )
            result = await self.server_request_handler(method, normalized)
            await self._send({"id": request_id, "result": result})
        except CodexServerRequestDenied as exc:
            await self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32003,
                        "message": str(exc)[:1000],
                    },
                }
            )
        except Exception as exc:
            await self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32603,
                        "message": f"FDEX server-request handler failed: {exc}"[:1000],
                    },
                }
            )

    def _fail_pending(self, exc: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)

    def _closed_message(self) -> str:
        proc = self.process
        code = proc.returncode if proc is not None else None
        suffix = f" exit={code}" if code is not None else ""
        if self.process_unit:
            suffix += f" unit={self.process_unit}"
        stderr = self.stderr_tail[-2000:]
        if stderr:
            suffix += f" stderr={stderr}"
        return f"Codex app-server transport closed.{suffix}"