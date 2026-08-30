from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.responses import Response as StarletteResponse

from app.codex_provider_compatibility import codex_provider_compatibility_store

router = APIRouter(prefix="/internal/codex-provider-smoke-mcp", include_in_schema=False)
_MAX_BODY = 128 * 1024
_TOOL_NAME = "fdex_smoke_echo"


def _loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1"}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": int(code), "message": str(message)[:500]}}


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _handle_one(token: str, message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid JSON-RPC request")
    request_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params")
    params = params if isinstance(params, dict) else {}
    if not method:
        return _error(request_id, -32600, "missing method")

    store = codex_provider_compatibility_store()
    capability = store.smoke_capability(token)
    if capability is None:
        return _error(request_id, -32001, "expired smoke capability")

    if method == "initialize":
        requested_version = str(params.get("protocolVersion") or "2025-06-18")
        return _result(
            request_id,
            {
                "protocolVersion": requested_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fdex-codex-provider-smoke", "version": "1.0.0"},
                "instructions": "Call fdex_smoke_echo only when the smoke prompt explicitly requests it.",
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(
            request_id,
            {
                "tools": [
                    {
                        "name": _TOOL_NAME,
                        "description": "FDEX controlled Provider compatibility echo tool.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"marker": {"type": "string"}},
                            "required": ["marker"],
                            "additionalProperties": False,
                        },
                        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
                    }
                ]
            },
        )
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        marker = str(arguments.get("marker") or "")
        expected = str(capability.get("marker") or "")
        if name != _TOOL_NAME:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": f"unknown smoke tool: {name}"}],
                    "isError": True,
                },
            )
        if marker != expected:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": "smoke marker mismatch"}],
                    "isError": True,
                },
            )
        recorded = store.record_smoke_tool_call(token, marker)
        if recorded is None:
            return _error(request_id, -32001, "expired smoke capability")
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": f"FDEX_MCP_ECHO:{marker}"}],
                "isError": False,
            },
        )
    return _error(request_id, -32601, f"method not found: {method}")


@router.api_route("/{token}", methods=["POST", "GET", "DELETE"], response_model=None)
async def codex_provider_smoke_mcp(token: str, request: Request) -> StarletteResponse:
    if not _loopback(request):
        return PlainTextResponse("not found", status_code=404)
    if len(token) < 32 or len(token) > 256:
        return PlainTextResponse("not found", status_code=404)
    store = codex_provider_compatibility_store()
    if store.smoke_capability(token) is None:
        return PlainTextResponse("not found", status_code=404)
    if request.method == "DELETE":
        # Stateless smoke capabilities do not require a session teardown. Keep the capability alive
        # until the owning smoke runner revokes it so multiple staged turns can use one MCP server.
        return Response(status_code=204)
    if request.method == "GET":
        # Streamable HTTP GET/SSE is optional for this stateless, request/response-only smoke server.
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})

    length = request.headers.get("content-length", "").strip()
    if length:
        try:
            if int(length) > _MAX_BODY:
                return PlainTextResponse("request too large", status_code=413)
        except ValueError:
            return PlainTextResponse("invalid content-length", status_code=400)
    body = await request.body()
    if len(body) > _MAX_BODY:
        return PlainTextResponse("request too large", status_code=413)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(_error(None, -32700, "parse error"), status_code=400)

    if isinstance(payload, list):
        if not payload:
            return JSONResponse(_error(None, -32600, "empty batch"), status_code=400)
        responses = [result for item in payload if (result := _handle_one(token, item)) is not None]
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses, headers={"Cache-Control": "no-store"})
    result = _handle_one(token, payload)
    if result is None:
        return Response(status_code=202)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
