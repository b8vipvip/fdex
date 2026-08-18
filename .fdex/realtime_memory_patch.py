from __future__ import annotations

from pathlib import Path
import textwrap


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"patch anchor missing in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


def main() -> None:
    replace_once(
        "app/src/main/java/com/b8vipvip/fdex/network/RealtimeVoiceClient.kt",
        """class RealtimeVoiceSession(\n    context: Context,\n    private val system: String?,\n    private val onEvent: (RealtimeVoiceEvent) -> Unit,\n) {\n""",
        """class RealtimeVoiceSession(\n    context: Context,\n    private val system: String?,\n    private val memoryControl: String?,\n    private val onEvent: (RealtimeVoiceEvent) -> Unit,\n) {\n""",
    )
    replace_once(
        "app/src/main/java/com/b8vipvip/fdex/network/RealtimeVoiceClient.kt",
        """                if (!system.isNullOrBlank()) payload.put(\"system\", system)\n                webSocket.send(payload.toString())\n""",
        """                if (!system.isNullOrBlank()) payload.put(\"system\", system)\n                // Opaque FDEX-only ACL/scope metadata. The FDEX server consumes this field\n                // before opening the upstream realtime provider; it is never forwarded.\n                if (!memoryControl.isNullOrBlank()) payload.put(\"memory_control\", memoryControl)\n                webSocket.send(payload.toString())\n""",
    )

    replace_once(
        "app/src/main/java/com/b8vipvip/fdex/ui/RealtimeVoiceDialog.kt",
        """internal fun RealtimeVoiceBar(\n    employeeName: String,\n    system: String?,\n    modifier: Modifier = Modifier,\n""",
        """internal fun RealtimeVoiceBar(\n    employeeName: String,\n    system: String?,\n    memoryControl: String?,\n    modifier: Modifier = Modifier,\n""",
    )
    replace_once(
        "app/src/main/java/com/b8vipvip/fdex/ui/RealtimeVoiceDialog.kt",
        "DisposableEffect(permissionGranted, system) {\n",
        "DisposableEffect(permissionGranted, system, memoryControl) {\n",
    )
    replace_once(
        "app/src/main/java/com/b8vipvip/fdex/ui/RealtimeVoiceDialog.kt",
        "created = RealtimeVoiceSession(context, system) { event ->\n",
        "created = RealtimeVoiceSession(context, system, memoryControl) { event ->\n",
    )

    replace_once(
        "app/src/main/java/com/b8vipvip/fdex/ui/StreamingChatScreens.kt",
        """                RealtimeVoiceBar(\n                    employeeName = employee.name,\n                    system = employeeSystemPrompt(employee),\n                    modifier = Modifier\n""",
        """                RealtimeVoiceBar(\n                    employeeName = employee.name,\n                    system = employeeSystemPrompt(employee),\n                    memoryControl = knowledgeStore.remoteMemoryControl(\n                        repo = repo,\n                        employee = employee,\n                        conversationId = \"realtime:employee:${employee.id}\",\n                    ),\n                    modifier = Modifier\n""",
    )

    fdex_memory = "server/app/fdex_memory.py"
    replace_once(
        fdex_memory,
        """        return output[: self.settings.fdex_memory_recall_limit]\n\n    async def add_exchange(\n""",
        """        return output[: self.settings.fdex_memory_recall_limit]\n\n    async def recent(\n        self,\n        scope: MemoryScope,\n        allowed_employee_ids: set[str] | None = None,\n        limit: int | None = None,\n    ) -> list[dict[str, Any]]:\n        \"\"\"Return recent verbatim drawers without embedding/Qdrant.\n\n        Realtime needs cross-session context before the first new utterance exists, so a\n        semantic query is not yet available. Reading the newest ACL-authorized drawers\n        directly also keeps voice startup independent from the embedding provider.\n        \"\"\"\n        await self.initialize()\n        requested = max(1, min(limit or self.settings.fdex_memory_recall_limit * 2, 60))\n        rows = await anyio.to_thread.run_sync(\n            self._read_recent_drawers_sync,\n            scope,\n            allowed_employee_ids,\n            requested,\n        )\n        return [\n            {\n                \"drawer_id\": str(row[\"drawer_id\"]),\n                \"wing\": str(row[\"wing\"]),\n                \"room\": str(row[\"room\"]),\n                \"role\": str(row[\"role\"]),\n                \"conversation_id\": str(row[\"conversation_id\"]),\n                \"employee_id\": str(row[\"employee_id\"]),\n                \"text\": str(row[\"content\"]),\n                \"created_at\": str(row[\"created_at\"]),\n            }\n            for row in rows\n        ]\n\n    async def add_exchange(\n""",
    )
    replace_once(
        fdex_memory,
        """    async def aclose(self) -> None:\n        await self._embedding.aclose()\n        await self._qdrant.aclose()\n\n\nclass LettaMemory:\n""",
        """    def _read_recent_drawers_sync(\n        self,\n        scope: MemoryScope,\n        allowed_employee_ids: set[str] | None,\n        limit: int,\n    ) -> list[sqlite3.Row]:\n        employee_clause = \"\"\n        params: list[Any] = [scope.account_id, scope.vault_id]\n        if allowed_employee_ids is not None:\n            normalized = sorted({str(value) for value in allowed_employee_ids if str(value)})\n            if not normalized:\n                return []\n            employee_clause = \" AND employee_id IN (\" + \",\".join(\"?\" for _ in normalized) + \")\"\n            params.extend(normalized)\n        params.append(max(1, min(int(limit), 60)))\n        with self._connect() as connection:\n            return list(\n                connection.execute(\n                    f\"\"\"\n                    SELECT drawer_id,wing,room,role,conversation_id,employee_id,content,created_at\n                    FROM mempalace_drawers\n                    WHERE account_id=? AND vault_id=?{employee_clause}\n                    ORDER BY created_at DESC, rowid DESC\n                    LIMIT ?\n                    \"\"\",\n                    tuple(params),\n                ).fetchall()\n            )\n\n    async def aclose(self) -> None:\n        await self._embedding.aclose()\n        await self._qdrant.aclose()\n\n\nclass LettaMemory:\n""",
    )
    replace_once(
        fdex_memory,
        """    async def remember_exchange(\n        self,\n""",
        """    async def recall_recent(\n        self,\n        scope: MemoryScope,\n        *,\n        allowed_employee_ids: set[str] | None = None,\n        include_letta: bool = True,\n    ) -> MemoryRecall:\n        \"\"\"Bootstrap a new realtime session from cross-session long-term memory.\"\"\"\n        if not self.settings.fdex_memory_enabled:\n            return MemoryRecall()\n        if allowed_employee_ids is not None and not allowed_employee_ids:\n            raw_task = asyncio.sleep(0, result=([], \"\"))\n        else:\n            raw_task = self._recall_component(\n                \"mempalace\",\n                self.mempalace.recent(scope, allowed_employee_ids),\n                [],\n            )\n        if include_letta:\n            query = (\n                \"召回最近与当前用户、当前员工、正在进行的项目、偏好、决定、日期、待办和变化相关的\"\n                \"长期结构化记忆。只返回记忆事实和不确定性，不回答任何新任务。\"\n            )\n            letta_task = self._recall_component(\"letta\", self.letta.recall(query, scope), \"\")\n        else:\n            letta_task = asyncio.sleep(0, result=(\"\", \"\"))\n        raw, structured = await asyncio.gather(raw_task, letta_task)\n        raw_value, raw_error = raw\n        structured_value, letta_error = structured\n        rendered: list[str] = []\n        # SQL returns newest-first; present the selected window chronologically to the model.\n        for item in reversed(raw_value[: self.settings.fdex_memory_recall_limit * 2]):\n            role = \"用户\" if item.get(\"role\") == \"user\" else \"AI\"\n            rendered.append(\n                f\"- [{item.get('created_at', '')} conversation={item.get('conversation_id', '')}] \"\n                f\"{role}：{item.get('text', '')}\"\n            )\n        errors = tuple(code for code in (raw_error, letta_error) if code)\n        return MemoryRecall(\n            mempalace_raw=\"\\n\".join(rendered)[: self.settings.fdex_memory_context_max_chars],\n            letta_structured=str(structured_value)[: self.settings.fdex_memory_context_max_chars],\n            error_codes=errors,\n        )\n\n    async def remember_exchange(\n        self,\n""",
    )

    write(
        "server/app/realtime_memory.py",
        textwrap.dedent('''\
        from __future__ import annotations

        import asyncio
        import logging
        from collections.abc import Awaitable, Callable
        from typing import Any

        from app.config import fresh_settings
        from app.fdex_memory import MemoryRecall, MemoryScope, memory_coordinator
        from app.memory_middleware import MemoryControl, compose_system_layers, decode_memory_control, extract_local_context

        logger = logging.getLogger(__name__)
        MemoryWriter = Callable[..., Awaitable[dict[str, Any]]]
        Diagnostic = Callable[..., None]


        def clean_realtime_user_text(text: str) -> str:
            """Remove FDEX-only local context/control wrappers before long-term storage."""
            without_local, _ = extract_local_context(text or "")
            without_marker, _ = decode_memory_control(without_local)
            return without_marker.strip()


        async def prepare_realtime_memory(
            system: str,
            memory_control: str,
        ) -> tuple[str, MemoryControl | None, MemoryRecall]:
            """Consume opaque control data and bootstrap a new realtime session.

            The control marker is never returned in the system string, including malformed or
            legacy cases where a client accidentally placed it inside `system`.
            """
            clean_system, embedded_control = decode_memory_control(system or "")
            _, separate_control = decode_memory_control(memory_control or "")
            control = separate_control or embedded_control
            if control is None:
                return clean_system, None, MemoryRecall()

            settings = fresh_settings()
            recall = MemoryRecall()
            if settings.fdex_memory_enabled:
                try:
                    recall = await memory_coordinator().recall_recent(
                        MemoryScope(control.scope_token),
                        allowed_employee_ids=control.allowed_employee_ids,
                        include_letta=control.knowledge_read,
                    )
                except Exception:
                    logger.exception("FDEX realtime memory bootstrap failed open")
                    recall = MemoryRecall(error_codes=("realtime_memory_bootstrap_exception",))

            if recall.empty:
                return clean_system, control, recall
            rendered = compose_system_layers(
                clean_system,
                "",
                recall,
                max_chars=settings.fdex_memory_system_max_chars,
            )
            return rendered or clean_system, control, recall


        class RealtimeMemoryRecorder:
            """Persist only already-transcribed realtime text; audio is never accepted here."""

            def __init__(
                self,
                control: MemoryControl | None,
                *,
                diag: Diagnostic | None = None,
                writer: MemoryWriter | None = None,
            ) -> None:
                self.control = control
                self.diag = diag
                self.writer = writer
                self._pending_users: list[str] = []
                self._assistant_parts: list[str] = []
                self._tasks: set[asyncio.Task[Any]] = set()

            def add_user(self, text: str, *, source: str) -> None:
                value = clean_realtime_user_text(text)[:30000]
                if not value:
                    return
                if self._pending_users and self._pending_users[-1] == value:
                    self._emit("realtime_memory_user_deduped", source=source, chars=len(value))
                    return
                self._pending_users.append(value)
                if len(self._pending_users) > 20:
                    self._pending_users = self._pending_users[-20:]
                self._emit("realtime_memory_user_text", source=source, chars=len(value))

            def add_assistant_delta(self, delta: str) -> None:
                if not delta:
                    return
                current = sum(len(item) for item in self._assistant_parts)
                remaining = max(0, 60000 - current)
                if remaining:
                    self._assistant_parts.append(delta[:remaining])

            def set_assistant_final(self, text: str) -> None:
                value = (text or "").strip()[:60000]
                if not value:
                    return
                current = "".join(self._assistant_parts)
                if not current:
                    self._assistant_parts = [value]
                elif value.startswith(current) and len(value) > len(current):
                    self._assistant_parts.append(value[len(current):])

            def complete(self, *, reason: str) -> None:
                assistant = "".join(self._assistant_parts).strip()
                self._assistant_parts.clear()
                if not assistant:
                    return
                if not self._pending_users:
                    self._emit("realtime_memory_orphan_assistant", reason=reason, assistant_chars=len(assistant))
                    return
                user = self._pending_users.pop(0)
                if self.control is None:
                    self._emit(
                        "realtime_memory_no_control",
                        reason=reason,
                        user_chars=len(user),
                        assistant_chars=len(assistant),
                    )
                    return
                task = asyncio.create_task(self._persist(user, assistant, reason))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

            async def _persist(self, user: str, assistant: str, reason: str) -> None:
                assert self.control is not None
                try:
                    writer = self.writer or memory_coordinator().remember_exchange
                    outcome = await writer(
                        scope=MemoryScope(self.control.scope_token),
                        conversation_id=self.control.conversation_id,
                        employee_id=self.control.employee_id,
                        user_text=user,
                        assistant_text=assistant,
                        write_structured=self.control.knowledge_write,
                    )
                    self._emit(
                        "realtime_memory_saved",
                        reason=reason,
                        user_chars=len(user),
                        assistant_chars=len(assistant),
                        mempalace=bool(outcome.get("mempalace")),
                        letta=bool(outcome.get("letta")),
                        errors=list(outcome.get("errors") or [])[:8],
                    )
                except Exception as exc:
                    logger.exception("FDEX realtime text memory write failed")
                    self._emit(
                        "realtime_memory_save_failed",
                        reason=reason,
                        error_type=type(exc).__name__,
                    )

            async def drain_for_test(self) -> None:
                if self._tasks:
                    await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

            def _emit(self, event: str, **details: Any) -> None:
                if self.diag is not None:
                    self.diag(event, **details)
        '''),
    )

    realtime_voice = "server/app/realtime_voice.py"
    replace_once(
        realtime_voice,
        """from app.provider_manager import audio_model_candidates, provider_store\nfrom app.realtime_diagnostics import write_realtime_diagnostic\n""",
        """from app.provider_manager import audio_model_candidates, provider_store\nfrom app.realtime_diagnostics import write_realtime_diagnostic\nfrom app.realtime_memory import RealtimeMemoryRecorder, clean_realtime_user_text, prepare_realtime_memory\n""",
    )
    replace_once(
        realtime_voice,
        """    system = str(start.get(\"system\") or \"\").strip()\n    requested_voice = str(start.get(\"voice\") or \"\").strip()\n""",
        """    raw_system = str(start.get(\"system\") or \"\").strip()\n    raw_memory_control = str(start.get(\"memory_control\") or \"\").strip()\n    system, memory_control, memory_recall = await prepare_realtime_memory(raw_system, raw_memory_control)\n    memory_recorder = RealtimeMemoryRecorder(memory_control, diag=diag)\n    diag(\n        \"realtime_memory_bootstrap\",\n        control=memory_control is not None,\n        mempalace_chars=len(memory_recall.mempalace_raw),\n        letta_chars=len(memory_recall.letta_structured),\n        errors=list(memory_recall.error_codes),\n    )\n    requested_voice = str(start.get(\"voice\") or \"\").strip()\n""",
    )
    replace_once(
        realtime_voice,
        """            elif event_type == \"text\":\n                text = str(data.get(\"text\") or \"\").strip()\n                if not text:\n                    continue\n                diag(\"client_text_in_session\", chars=len(text))\n""",
        """            elif event_type == \"text\":\n                text = str(data.get(\"text\") or \"\").strip()\n                if not text:\n                    continue\n                memory_recorder.add_user(clean_realtime_user_text(text), source=\"text\")\n                diag(\"client_text_in_session\", chars=len(text))\n""",
    )
    replace_once(
        realtime_voice,
        """            if event:\n                if event.get(\"type\") == \"audio\" and chosen_protocol == OPENAI_REALTIME:\n""",
        """            if event:\n                memory_event_type = str(event.get(\"type\") or \"\")\n                if memory_event_type == \"user_transcript\":\n                    memory_recorder.add_user(str(event.get(\"text\") or \"\"), source=\"voice\")\n                elif memory_event_type == \"assistant_transcript\":\n                    memory_recorder.add_assistant_delta(str(event.get(\"delta\") or \"\"))\n                elif memory_event_type == \"interrupt\":\n                    memory_recorder.complete(reason=\"interrupt\")\n                elif memory_event_type == \"done\":\n                    if chosen_protocol == CHAT2API_LIVE:\n                        memory_recorder.set_assistant_final(str(data.get(\"text\") or \"\"))\n                    memory_recorder.complete(reason=\"done\")\n                if event.get(\"type\") == \"audio\" and chosen_protocol == OPENAI_REALTIME:\n""",
    )

    write(
        "server/tests/test_realtime_memory.py",
        textwrap.dedent('''\
        from __future__ import annotations

        import asyncio

        from app.config import Settings
        from app.fdex_memory import MemoryScope, MemPalaceStore
        from app.memory_middleware import MemoryControl
        from app.realtime_memory import RealtimeMemoryRecorder, clean_realtime_user_text


        def _control() -> MemoryControl:
            return MemoryControl(
                scope_token="R" * 32,
                conversation_id="realtime:employee:7",
                employee_id="7",
                knowledge_read=True,
                knowledge_write=True,
                chat_access_mode="self",
                readable_employee_ids=(),
            )


        def test_realtime_storage_cleans_fdex_context_and_keeps_plain_text_only() -> None:
            wrapped = (
                "继续说这个项目\\n\\n<fdex_company_context>\\n"
                "知识库候选资料：内部资料\\n</fdex_company_context>"
            )
            assert clean_realtime_user_text(wrapped) == "继续说这个项目"


        def test_realtime_recorder_pairs_transcripts_and_never_needs_audio() -> None:
            saved: list[dict[str, object]] = []

            async def writer(**kwargs):
                saved.append(kwargs)
                return {"mempalace": True, "letta": True, "errors": []}

            async def scenario() -> None:
                recorder = RealtimeMemoryRecorder(_control(), writer=writer)
                recorder.add_user("我今天决定把预算改成 100 万", source="voice")
                recorder.add_assistant_delta("好的，")
                recorder.add_assistant_delta("我记住这个决定。")
                recorder.complete(reason="done")
                await recorder.drain_for_test()

            asyncio.run(scenario())
            assert len(saved) == 1
            assert saved[0]["user_text"] == "我今天决定把预算改成 100 万"
            assert saved[0]["assistant_text"] == "好的，我记住这个决定。"
            assert saved[0]["write_structured"] is True
            assert all("audio" not in key.lower() for key in saved[0])


        def test_interrupt_then_done_does_not_duplicate_partial_reply() -> None:
            saved: list[dict[str, object]] = []

            async def writer(**kwargs):
                saved.append(kwargs)
                return {"mempalace": True, "letta": True, "errors": []}

            async def scenario() -> None:
                recorder = RealtimeMemoryRecorder(_control(), writer=writer)
                recorder.add_user("先回答这一句", source="voice")
                recorder.add_assistant_delta("回答到一半")
                recorder.complete(reason="interrupt")
                recorder.complete(reason="done")
                await recorder.drain_for_test()

            asyncio.run(scenario())
            assert len(saved) == 1
            assert saved[0]["assistant_text"] == "回答到一半"


        def test_mempalace_recent_obeys_employee_acl_without_embedding(tmp_path) -> None:
            async def scenario() -> None:
                settings = Settings(
                    fdex_memory_data_dir=str(tmp_path),
                    fdex_memory_proxy_token="test-token-for-local-unit-test-only",
                )
                store = MemPalaceStore(settings)
                scope = MemoryScope("scope-token-123456789012345678901234")
                await store.initialize()
                items7 = store._build_items(scope, "realtime:7", "员工7用户文本", "员工7回复", "7")
                items9 = store._build_items(scope, "realtime:9", "员工9用户文本", "员工9回复", "9")
                store._store_drawers_sync(items7 + items9)
                only7 = await store.recent(scope, {"7"}, limit=20)
                assert only7
                assert {item["employee_id"] for item in only7} == {"7"}
                assert any("员工7用户文本" in item["text"] for item in only7)
                assert all("员工9" not in item["text"] for item in only7)
                await store.aclose()

            asyncio.run(scenario())
        '''),
    )

    docs = "docs/memory-system-layers.md"
    replace_once(
        docs,
        """## 实时语音\n\nRealtime 会话仍保持现有“同一实时模型/同一会话、不回退普通供应商”的约束。客户端不会把远程 memory control 标记发送到 realtime 上游；实时语音当前继续使用员工角色 Prompt 和本机可用上下文。MemPalace/Letta 的逐轮语义召回首先用于普通文字、图片、文档与群聊 HTTP AI 路径，避免改变现有低延迟语音协议。后续若要把远程长期记忆加入 realtime，应在 FDEX WebSocket 服务端按转写文本召回，而不是把 scope token 暴露给上游模型。\n""",
        """## 实时语音\n\nRealtime 会话继续保持“同一实时模型 / 同一会话 / 不回退普通供应商”的约束。Android 在 FDEX WebSocket 的 `start` 帧中单独携带不透明 `memory_control`；该字段只由 FDEX 服务端消费，绝不会转发给 GPT-Live / OpenAI Realtime 上游。建连前，服务端按员工 ACL 读取最近 MemPalace 原始历史并召回 Letta 结构化长期记忆，再与员工角色 Prompt 组合成当前实时会话的 system/instructions。\n\n语音 PCM/Base64 只用于实时传输和播放，**不会写入 MemPalace 或 Letta**。长期记忆仅使用实时协议已经生成、同时会在 FDEX 聊天界面回显的文字：`user_transcript` / `transcript.final` 作为用户文本，`assistant_transcript` / `response.text.delta` 作为 AI 文本。每个完成或被打断且已经产生文字回显的问答异步写入 MemPalace；员工 `knowledgeWrite=true` 时同时更新 Letta。实时输入框里的文字仍通过当前 WebSocket 的 `input.text` / `conversation.item.create` 进入同一模型会话，并以去掉 FDEX 本地候选上下文后的可见文字写入长期记忆。\n\n当前 `chat2api-live-v1` 明确定义 `session.start`、文本输入、打断和结束，但没有会话中途更新 instructions/system 的事件，因此 FDEX 不伪造 `session.update`。跨会话长期记忆在新 Realtime 会话建立前装载；本次实时通话内部的新上下文由同一个 GPT-Live/Realtime 会话自身持续维护，通话完成后的文本记忆供后续新会话召回。\n""",
    )


if __name__ == "__main__":
    main()
