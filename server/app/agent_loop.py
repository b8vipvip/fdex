from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.agent_runtime import AgentRuntimeError, AgentTaskCancelled, FdexAgentRuntime
from app.config import get_settings
from app.multimodal_service import route_text

ModelCall = Callable[[str, str, int], Awaitable[str]]

_AGENT_SYSTEM = """You are the decision engine for FDEX Coding Agent.
All model inference comes from FDEX's shared provider-management pool; there is no Agent-specific model/API configuration.
You do not have arbitrary shell access and you may never write directly to the configured base branch.
You may request exactly one tool from the task's allowlist and must supply only that tool's documented JSON arguments.
Return exactly one JSON object and no markdown.
Allowed response forms:
{"action":"tool","tool":"<allowed tool>","args":{},"summary":"short public progress summary"}
{"action":"final","answer":"concise final answer to the user","summary":"short public completion summary"}
Never invent tool output. Read before editing. After edits, inspect the diff and run the relevant test suite. Commit validated work before push. Push only the generated fdex-agent branch. Create a pull request only when the project grants that capability.
The summary must be safe to show to the user and must not contain hidden chain-of-thought."""

_TOOL_GUIDE = """TOOL ARGUMENTS:
git_status: {}
git_diff: {}
git_log: {}
list_files: {"path":"relative/directory"}
read_file: {"path":"relative/file","start_line":1,"end_line":400}
write_file: {"path":"relative/file","content":"complete UTF-8 text"}
replace_text: {"path":"relative/file","old":"exact unique text","new":"replacement text"}
run_tests: {"suite":"server|android_unit|android_debug"}
git_commit: {"message":"short commit subject"}
git_push: {}
github_create_pr: {"title":"PR title","body":"optional PR body"}
Paths are relative to the current task worktree. Protected paths and traversal are rejected. Remote tools appear only when the selected project explicitly grants them."""


@dataclass(slots=True)
class AgentDecision:
    action: str
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    summary: str = ""


class AgentDecisionError(AgentRuntimeError):
    pass


async def _default_model_call(system: str, prompt: str, max_tokens: int) -> str:
    result = await route_text(system=system, prompt=prompt, max_tokens=max_tokens)
    if not result.ok:
        detail = "; ".join(result.errors[-5:]) or "no enabled text provider returned an answer"
        raise AgentRuntimeError(f"agent model unavailable from shared provider pool: {detail}")
    return result.content


def _extract_json_object(text: str) -> dict[str, object]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match is None:
            raise AgentDecisionError("agent model did not return JSON")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AgentDecisionError("agent model returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise AgentDecisionError("agent model response must be a JSON object")
    return parsed


def parse_decision(text: str, allowed_tools: tuple[str, ...]) -> AgentDecision:
    data = _extract_json_object(text)
    action = str(data.get("action") or "").strip().lower()
    summary = str(data.get("summary") or "").strip()[:500]
    if action == "final":
        answer = str(data.get("answer") or "").strip()
        if not answer:
            raise AgentDecisionError("agent final answer is empty")
        return AgentDecision(action="final", answer=answer, summary=summary)
    if action == "tool":
        tool = str(data.get("tool") or "").strip()
        if tool not in allowed_tools:
            raise AgentDecisionError(f"agent requested disallowed tool: {tool or '<empty>'}")
        args = data.get("args") or {}
        if not isinstance(args, dict):
            raise AgentDecisionError("agent tool args must be a JSON object")
        if len(json.dumps(args, ensure_ascii=False)) > 250000:
            raise AgentDecisionError("agent tool args exceed limit")
        return AgentDecision(action="tool", tool=tool, args=args, summary=summary)
    raise AgentDecisionError(f"unsupported agent action: {action or '<empty>'}")


async def _maybe_run_official_codex(runtime: FdexAgentRuntime, task_id: str) -> bool:
    """Run the durable official Codex Host when selected.

    Returns True when the task was handled by Codex (including strict-mode readiness
    failure), or False when the caller should continue through the legacy loop.
    """
    settings = get_settings()
    mode = (settings.fdex_agent_engine or "legacy").strip().lower()
    if mode not in {"codex", "auto"}:
        return False

    from app.codex_engine import codex_runtime_status
    from app.codex_host_guard import run_codex_task

    status = codex_runtime_status()
    if bool(status.get("ready")):
        await run_codex_task(runtime, task_id)
        return True
    reason = str(status.get("reason") or "Codex engine is not ready")
    if mode == "auto":
        task = await runtime.get_task(task_id)
        if task is not None:
            task.emit("engine.fallback", f"Codex not ready; using legacy FDEX engine: {reason}")
        return False
    await runtime.fail_task(task_id, reason)
    return True


class FdexAgentLoop:
    def __init__(self, runtime: FdexAgentRuntime, *, model_call: ModelCall | None = None, max_steps: int | None = None) -> None:
        settings = get_settings()
        self.runtime = runtime
        self.model_call = model_call or _default_model_call
        self.max_steps = max_steps or settings.fdex_agent_max_steps
        self.model_max_tokens = settings.fdex_agent_model_max_tokens

    async def run(self, task_id: str) -> None:
        task = await self.runtime.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        if task.status not in {"queued", "running"}:
            raise AgentRuntimeError(f"task cannot run from status: {task.status}")
        if self.model_call is _default_model_call and await _maybe_run_official_codex(self.runtime, task_id):
            return
        task.status = "running"
        task.emit("agent.started", f"Coding Agent started for {task.project_name}")
        transcript: list[str] = []
        try:
            for step in range(1, self.max_steps + 1):
                task = await self.runtime.get_task(task_id)
                if task is None:
                    raise AgentRuntimeError("task not found")
                await self.runtime._raise_if_cancelled(task)
                allowed_tools = self.runtime.allowed_tools_for_task(task)
                prompt = self._build_prompt(task, transcript, step, allowed_tools)
                task.emit("model.started", f"Planning step {step} via shared provider pool")
                raw = await self.model_call(_AGENT_SYSTEM, prompt, self.model_max_tokens)
                await self.runtime._raise_if_cancelled(task)
                decision = parse_decision(raw, allowed_tools)
                if decision.summary:
                    task.emit("agent.progress", decision.summary)
                if decision.action == "final":
                    await self.runtime.complete_task(task_id, decision.answer)
                    return
                output = await self.runtime.execute_tool(task_id, decision.tool, args=decision.args, terminal=False)
                transcript.append(
                    "STEP {step}\nTOOL: {tool}\nARGS: {args}\nOBSERVATION:\n{output}".format(
                        step=step,
                        tool=decision.tool,
                        args=json.dumps(decision.args, ensure_ascii=False, separators=(",", ":")),
                        output=output or "(no output)",
                    )
                )
            raise AgentRuntimeError(f"agent exceeded maximum steps ({self.max_steps})")
        except AgentTaskCancelled:
            return
        except Exception as exc:
            await self.runtime.fail_task(task_id, str(exc))

    def _build_prompt(self, task: Any, transcript: list[str], step: int, allowed_tools: tuple[str, ...]) -> str:
        observations = "\n\n".join(transcript) if transcript else "(none yet)"
        project = f"{task.project_name} ({task.repository or 'local workspace'})"
        return (
            f"USER TASK:\n{task.prompt}\n\nPROJECT:\n{project}\nBASE BRANCH:\n{task.base_branch}\nOWNER SCOPE:\n{task.owner_id}\n\n"
            f"ALLOWED TOOLS:\n{', '.join(allowed_tools)}\n\n{_TOOL_GUIDE}\n\n"
            f"TOOL OBSERVATIONS SO FAR:\n{observations}\n\nSTEP: {step}/{self.max_steps}\n"
            "Choose one next tool if work remains; otherwise return final."
        )