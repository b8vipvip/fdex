from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import employee_chat_runtime as chat_runtime
from app.employee_agent_tools import EmployeeToolContext

OWNER = "usr_1234567890abcdef12345678"


class FakeProjectStore:
    def __init__(self, projects: list[dict[str, object]]) -> None:
        self.projects = projects

    def list_projects(self, owner_id: str, *, enabled_only: bool = False):
        assert owner_id == OWNER
        if enabled_only:
            return [item for item in self.projects if bool(item.get("enabled"))]
        return list(self.projects)


def _project(project_id: int, repo: str, *, enabled: bool = True) -> dict[str, object]:
    return {
        "id": project_id,
        "name": repo.rsplit("/", 1)[-1],
        "repo_full_name": repo,
        "base_branch": "main",
        "enabled": enabled,
    }


def test_repository_execution_classifier_separates_metadata_from_source_work() -> None:
    assert chat_runtime._repository_execution_requested("当前项目中是否真实存在代码，代码是否完整") is True
    assert chat_runtime._repository_execution_requested("读取 wikia2/test-git 的 README 文件") is True
    assert chat_runtime._repository_execution_requested("请修复当前仓库里的登录 bug 并运行测试") is True
    assert chat_runtime._repository_execution_requested("你检查一下我当前 github 仓库是否公开？") is False
    assert chat_runtime._repository_execution_requested("当前 GitHub 有几个仓库？") is False


def test_repository_project_resolution_uses_recent_conversation_context(monkeypatch) -> None:
    projects = [_project(1, "wikia2/alpha"), _project(2, "wikia2/test-git")]
    monkeypatch.setattr(chat_runtime, "agent_project_store", lambda: FakeProjectStore(projects))

    selected = chat_runtime._resolve_repository_project(
        OWNER,
        "当前项目中是否真实存在代码，代码是否完整",
        [
            {"role": "user", "content": "先连接 wikia2/test-git"},
            {"role": "assistant", "content": "已连接"},
        ],
    )

    assert selected["id"] == 2
    assert selected["repo_full_name"] == "wikia2/test-git"


def test_repository_metadata_answer_does_not_call_generic_ai(monkeypatch) -> None:
    from app import user_app_routes as routes

    monkeypatch.setattr(
        chat_runtime,
        "collect_employee_tool_context",
        lambda *_args, **_kwargs: EmployeeToolContext(
            answer_prefix="【GitHub 实时检查】wikia2/test-git：公开，可 Push/PR",
            events=[{"tool": "github.installation.repositories", "status": "completed", "summary": "checked"}],
        ),
    )

    async def fail_client_ai(*_args, **_kwargs):
        raise AssertionError("generic client_ai must not run for deterministic repository metadata")

    monkeypatch.setattr(routes, "client_ai", fail_client_ai)
    request = SimpleNamespace(scope={})
    answer = asyncio.run(
        chat_runtime.ask_employee_with_tools(
            request,
            OWNER,
            {"coding_agent": True, "name": "淘小宝"},
            "你检查一下我当前 github 仓库是否公开？",
            [],
            None,
        )
    )

    assert answer.startswith("【GitHub 实时检查】")
    assert request.scope["fdex_employee_tool_events"][0]["tool"] == "github.installation.repositories"


def test_repository_source_request_routes_to_coding_agent_not_generic_ai(monkeypatch) -> None:
    from app import user_app_routes as routes

    monkeypatch.setattr(
        chat_runtime,
        "collect_employee_tool_context",
        lambda *_args, **_kwargs: EmployeeToolContext(
            answer_prefix="【GitHub 实时检查】metadata",
            events=[{"tool": "github.installation.repositories", "status": "completed", "summary": "checked"}],
        ),
    )

    calls: list[tuple[str, str]] = []

    async def fake_agent(request, owner_id, prompt, history):
        calls.append((owner_id, prompt))
        request.scope["fdex_employee_tool_events"].append(
            {
                "tool": "coding_agent.repository_task",
                "status": "completed",
                "summary": "Coding Agent 已实际执行 wikia2/test-git",
            }
        )
        return "【Coding Agent 实际执行】\n仓库：wikia2/test-git\n代码已检查"

    async def fail_client_ai(*_args, **_kwargs):
        raise AssertionError("generic client_ai must not receive repository source/read-write work")

    monkeypatch.setattr(chat_runtime, "_run_repository_agent", fake_agent)
    monkeypatch.setattr(routes, "client_ai", fail_client_ai)
    request = SimpleNamespace(scope={})
    answer = asyncio.run(
        chat_runtime.ask_employee_with_tools(
            request,
            OWNER,
            {"coding_agent": True, "name": "淘小宝"},
            "当前项目中是否真实存在代码，代码是否完整",
            [{"role": "user", "content": "我刚才在看 wikia2/test-git"}],
            None,
        )
    )

    assert calls == [(OWNER, "当前项目中是否真实存在代码，代码是否完整")]
    assert answer.startswith("【Coding Agent 实际执行】")
    assert any(item["tool"] == "coding_agent.repository_task" for item in request.scope["fdex_employee_tool_events"])
