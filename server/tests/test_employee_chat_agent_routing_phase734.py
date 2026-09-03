from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import employee_chat_runtime as chat_runtime
from app.employee_agent_tools import EmployeeToolContext

OWNER = "usr_1234567890abcdef12345678"
TASK_ALPHA = "a" * 32
TASK_TEST = "b" * 32


class FakeProjectStore:
    def __init__(self, projects: list[dict[str, object]]) -> None:
        self.projects = projects

    def list_projects(self, owner_id: str, *, enabled_only: bool = False):
        assert owner_id == OWNER
        if enabled_only:
            return [item for item in self.projects if bool(item.get("enabled"))]
        return list(self.projects)


class FakeTaskStore:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows

    def get(self, owner_id: str, task_id: str):
        assert owner_id == OWNER
        return self.rows.get(task_id)


class FakeCodexStore:
    def __init__(self, bindings: dict[str, dict[str, object]]) -> None:
        self.bindings = bindings

    def task_binding(self, owner_id: str, task_id: str):
        assert owner_id == OWNER
        return self.bindings.get(task_id)


def _project(project_id: int, repo: str, *, enabled: bool = True) -> dict[str, object]:
    return {
        "id": project_id,
        "name": repo.rsplit("/", 1)[-1],
        "repo_full_name": repo,
        "base_branch": "main",
        "enabled": enabled,
    }


def _assistant_task_event(task_id: str, repo: str = "wikia2/test-git") -> dict[str, object]:
    return {
        "role": "assistant",
        "content": f"【Coding Agent / Agent Turn】\n项目：{repo}\n任务：{task_id}",
        "tool_events": [
            {
                "tool": "coding_agent.task",
                "status": "completed",
                "task_id": task_id,
                "repository": repo,
            }
        ],
    }


def test_coding_agent_routing_has_no_natural_language_classifier() -> None:
    assert not hasattr(chat_runtime, "_coding_agent_operation_requested")
    assert not hasattr(chat_runtime, "_repository_execution_requested")


@pytest.mark.parametrize(
    "prompt",
    [
        "当前项目中是否真实存在代码，代码是否完整",
        "读取 server/app/main.py 文件并检查路由",
        "运行测试并修复失败项",
        "执行 git status 和 git diff",
        "执行 bash 命令检查依赖版本",
        "你检查一下我当前 github 仓库是否公开？",
        "当前 GitHub 有几个仓库？",
        "什么是 Python 的 GIL？",
        "解释一下单元测试的基本概念",
        "你好，先说说你能做什么",
    ],
)
def test_every_coding_agent_message_enters_agent_runtime(monkeypatch, prompt: str) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_agent(request, owner_id, employee, text, history, upload=None):
        calls.append((owner_id, text))
        request.scope["fdex_employee_tool_events"] = [
            {"tool": "coding_agent.task", "status": "completed", "task_id": TASK_TEST}
        ]
        return "【Coding Agent / Agent Turn】\n已完成"

    async def fail_generic(*_args, **_kwargs):
        raise AssertionError("Coding Agent messages must never reach the generic employee AI path")

    monkeypatch.setattr(chat_runtime, "_run_coding_agent", fake_agent)
    monkeypatch.setattr(chat_runtime, "_run_generic_employee", fail_generic)
    request = SimpleNamespace(scope={})
    answer = asyncio.run(
        chat_runtime.ask_employee_with_tools(
            request,
            OWNER,
            {"coding_agent": True, "name": "淘小宝"},
            prompt,
            [],
            None,
        )
    )

    assert answer.startswith("【Coding Agent / Agent Turn】")
    assert calls == [(OWNER, prompt)]


def test_non_coding_employee_keeps_generic_ai_path(monkeypatch) -> None:
    calls: list[str] = []

    async def fail_agent(*_args, **_kwargs):
        raise AssertionError("ordinary employee must not be forced into Coding Agent")

    async def fake_generic(request, owner_id, employee, prompt, history, upload):
        calls.append(prompt)
        return "普通智体回复"

    monkeypatch.setattr(chat_runtime, "_run_coding_agent", fail_agent)
    monkeypatch.setattr(chat_runtime, "_run_generic_employee", fake_generic)
    answer = asyncio.run(
        chat_runtime.ask_employee_with_tools(
            SimpleNamespace(scope={}),
            OWNER,
            {"coding_agent": False, "name": "普通员工"},
            "解释一下单元测试",
            [],
            None,
        )
    )
    assert answer == "普通智体回复"
    assert calls == ["解释一下单元测试"]


def test_project_resolution_prefers_recent_durable_agent_task(monkeypatch) -> None:
    projects = [_project(1, "wikia2/alpha"), _project(2, "wikia2/test-git")]
    monkeypatch.setattr(chat_runtime, "agent_project_store", lambda: FakeProjectStore(projects))
    monkeypatch.setattr(
        chat_runtime,
        "agent_task_store",
        lambda: FakeTaskStore(
            {
                TASK_TEST: {
                    "id": TASK_TEST,
                    "owner_id": OWNER,
                    "project_id": 2,
                    "status": "succeeded",
                }
            }
        ),
    )

    selected = chat_runtime._resolve_repository_project(
        OWNER,
        "什么是 Python 的 GIL？",
        [_assistant_task_event(TASK_TEST)],
    )

    assert selected["id"] == 2
    assert selected["repo_full_name"] == "wikia2/test-git"


def test_explicit_project_reference_overrides_previous_agent_thread(monkeypatch) -> None:
    projects = [_project(1, "wikia2/alpha"), _project(2, "wikia2/test-git")]
    monkeypatch.setattr(chat_runtime, "agent_project_store", lambda: FakeProjectStore(projects))
    monkeypatch.setattr(
        chat_runtime,
        "agent_task_store",
        lambda: FakeTaskStore({TASK_TEST: {"project_id": 2, "status": "succeeded"}}),
    )

    selected = chat_runtime._resolve_repository_project(
        OWNER,
        "切换到 wikia2/alpha，然后解释一下这里的架构",
        [_assistant_task_event(TASK_TEST)],
    )
    assert selected["id"] == 1


def test_first_agent_turn_with_multiple_projects_fails_closed(monkeypatch) -> None:
    projects = [_project(1, "wikia2/alpha"), _project(2, "wikia2/test-git")]
    monkeypatch.setattr(chat_runtime, "agent_project_store", lambda: FakeProjectStore(projects))
    monkeypatch.setattr(chat_runtime, "agent_task_store", lambda: FakeTaskStore({}))

    with pytest.raises(ValueError, match="每个 Codex Turn 都需要一个明确的项目工作区"):
        chat_runtime._resolve_repository_project(OWNER, "你好", [])


def test_latest_compatible_codex_source_requires_same_project_terminal_task_and_binding(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_runtime,
        "agent_task_store",
        lambda: FakeTaskStore(
            {
                TASK_ALPHA: {"project_id": 1, "status": "succeeded"},
                TASK_TEST: {"project_id": 2, "status": "succeeded"},
            }
        ),
    )
    monkeypatch.setattr(
        chat_runtime,
        "codex_host_store",
        lambda: FakeCodexStore(
            {
                TASK_ALPHA: {"thread_id": "thread-alpha", "relation": "start"},
                TASK_TEST: {"thread_id": "thread-test", "relation": "start"},
            }
        ),
    )
    history = [_assistant_task_event(TASK_ALPHA, "wikia2/alpha"), _assistant_task_event(TASK_TEST)]

    assert chat_runtime._latest_compatible_codex_source(OWNER, history, 2) == TASK_TEST
    assert chat_runtime._latest_compatible_codex_source(OWNER, history, 1) == TASK_ALPHA


def test_create_agent_turn_resumes_existing_codex_thread_without_replaying_chat_history(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Runtime:
        async def create_task(self, *_args, **_kwargs):
            raise AssertionError("existing Codex thread must use continuation, not create a fresh task")

    runtime = Runtime()
    monkeypatch.setattr(chat_runtime, "agent_runtime", lambda: runtime)
    monkeypatch.setattr(chat_runtime, "_latest_compatible_codex_source", lambda *_args, **_kwargs: TASK_TEST)

    async def fake_continuation(runtime_arg, *, owner_id, source_task_id, prompt, fork):
        captured.update(
            runtime=runtime_arg,
            owner_id=owner_id,
            source_task_id=source_task_id,
            prompt=prompt,
            fork=fork,
        )
        return SimpleNamespace(id="c" * 32)

    monkeypatch.setattr(chat_runtime, "create_codex_continuation", fake_continuation)
    context = EmployeeToolContext(prompt_context="[FDEX_TRUSTED_TOOL_DATA]\nFACT\n[/FDEX_TRUSTED_TOOL_DATA]")
    runtime_out, task, relation, source = asyncio.run(
        chat_runtime._create_coding_agent_turn(
            OWNER,
            _project(2, "wikia2/test-git"),
            "继续解释这个函数",
            [{"role": "user", "content": "OLD HISTORY MUST NOT BE REPLAYED"}],
            context,
        )
    )

    assert runtime_out is runtime
    assert task.id == "c" * 32
    assert relation == "resume"
    assert source == TASK_TEST
    assert captured["source_task_id"] == TASK_TEST
    assert captured["fork"] is False
    assert "继续解释这个函数" in str(captured["prompt"])
    assert "FACT" in str(captured["prompt"])
    assert "OLD HISTORY MUST NOT BE REPLAYED" not in str(captured["prompt"])


def test_create_first_agent_turn_bootstraps_recent_chat_once(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Runtime:
        async def create_task(self, prompt, *, owner_id, project_id):
            captured.update(prompt=prompt, owner_id=owner_id, project_id=project_id)
            return SimpleNamespace(id="d" * 32)

    runtime = Runtime()
    monkeypatch.setattr(chat_runtime, "agent_runtime", lambda: runtime)
    monkeypatch.setattr(chat_runtime, "_latest_compatible_codex_source", lambda *_args, **_kwargs: "")
    history = [
        {"role": "user", "content": "我们刚刚讨论的是登录路由"},
        {"role": "assistant", "content": "收到"},
    ]
    runtime_out, task, relation, source = asyncio.run(
        chat_runtime._create_coding_agent_turn(
            OWNER,
            _project(2, "wikia2/test-git"),
            "继续",
            history,
            EmployeeToolContext(),
        )
    )

    assert runtime_out is runtime
    assert task.id == "d" * 32
    assert relation == "start"
    assert source == ""
    assert captured["project_id"] == 2
    assert "我们刚刚讨论的是登录路由" in str(captured["prompt"])
    assert "FDEX CHAT BOOTSTRAP CONTEXT" in str(captured["prompt"])


def test_attachment_kind_uses_official_codex_user_input_and_fails_closed_for_documents() -> None:
    image = SimpleNamespace(filename="screen.png", content_type="image/png")
    audio = SimpleNamespace(filename="voice.m4a", content_type="audio/m4a")
    pdf = SimpleNamespace(filename="report.pdf", content_type="application/pdf")

    assert chat_runtime._attachment_kind(image) == ("localImage", 20 * 1024 * 1024)
    assert chat_runtime._attachment_kind(audio) == ("localAudio", 50 * 1024 * 1024)
    with pytest.raises(ValueError, match="不会回退给通用 AI"):
        chat_runtime._attachment_kind(pdf)


def test_trusted_github_metadata_is_context_for_agent_not_short_circuit() -> None:
    tool_context = EmployeeToolContext(
        prompt_context="[FDEX_TRUSTED_TOOL_DATA]\n{\"repository_count\":2}\n[/FDEX_TRUSTED_TOOL_DATA]",
        answer_prefix="【GitHub 实时检查】2 个仓库",
    )
    turn = chat_runtime._agent_turn_prompt("当前 GitHub 有几个仓库？", [], tool_context, bootstrap=False)
    assert "repository_count" in turn
    assert "CURRENT USER REQUEST" in turn
    assert "Decide within the Codex Agent Turn" in turn
