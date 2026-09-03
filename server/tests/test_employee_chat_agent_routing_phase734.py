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


def test_coding_agent_classifier_is_capability_first_not_github_first() -> None:
    assert chat_runtime._coding_agent_operation_requested("当前项目中是否真实存在代码，代码是否完整") is True
    assert chat_runtime._coding_agent_operation_requested("读取 wikia2/test-git 的 README 文件") is True
    assert chat_runtime._coding_agent_operation_requested("请修复当前仓库里的登录 bug 并运行测试") is True

    # No GitHub/repository wording is required when the requested operation is plainly an Agent tool job.
    assert chat_runtime._coding_agent_operation_requested("读取 server/app/main.py 文件并检查路由") is True
    assert chat_runtime._coding_agent_operation_requested("运行测试并修复失败项") is True
    assert chat_runtime._coding_agent_operation_requested("执行 git status 和 git diff") is True
    assert chat_runtime._coding_agent_operation_requested("修改登录模块代码，然后构建项目") is True
    assert chat_runtime._coding_agent_operation_requested("执行 bash 命令检查依赖版本") is True

    # Deterministic metadata and plain conceptual conversation do not need an Agent task.
    assert chat_runtime._coding_agent_operation_requested("你检查一下我当前 github 仓库是否公开？") is False
    assert chat_runtime._coding_agent_operation_requested("当前 GitHub 有几个仓库？") is False
    assert chat_runtime._coding_agent_operation_requested("什么是 Python 的 GIL？") is False
    assert chat_runtime._coding_agent_operation_requested("解释一下单元测试的基本概念") is False

    # Legacy helper must share the same new boundary.
    assert chat_runtime._repository_execution_requested("运行测试并修复失败项") is True


def test_repository_project_resolution_uses_recent_conversation_context(monkeypatch) -> None:
    projects = [_project(1, "wikia2/alpha"), _project(2, "wikia2/test-git")]
    monkeypatch.setattr(chat_runtime, "agent_project_store", lambda: FakeProjectStore(projects))

    selected = chat_runtime._resolve_repository_project(
        OWNER,
        "运行测试并修复失败项",
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


def test_any_coding_agent_operation_routes_to_agent_not_generic_ai(monkeypatch) -> None:
    from app import user_app_routes as routes

    monkeypatch.setattr(
        chat_runtime,
        "collect_employee_tool_context",
        lambda *_args, **_kwargs: EmployeeToolContext(),
    )

    calls: list[tuple[str, str]] = []

    async def fake_agent(request, owner_id, prompt, history):
        calls.append((owner_id, prompt))
        request.scope["fdex_employee_tool_events"].append(
            {
                "tool": "coding_agent.task",
                "status": "completed",
                "summary": "Coding Agent 已实际执行 wikia2/test-git",
            }
        )
        return "【Coding Agent 实际执行】\n项目：wikia2/test-git\n已执行"

    async def fail_client_ai(*_args, **_kwargs):
        raise AssertionError("generic client_ai must not receive Coding Agent capability work")

    monkeypatch.setattr(chat_runtime, "_run_coding_agent", fake_agent)
    monkeypatch.setattr(routes, "client_ai", fail_client_ai)

    prompts = [
        "当前项目中是否真实存在代码，代码是否完整",
        "读取 server/app/main.py 文件并检查路由",
        "运行测试并修复失败项",
        "执行 git status 和 git diff",
        "执行 bash 命令检查依赖版本",
    ]
    for prompt in prompts:
        request = SimpleNamespace(scope={})
        answer = asyncio.run(
            chat_runtime.ask_employee_with_tools(
                request,
                OWNER,
                {"coding_agent": True, "name": "淘小宝"},
                prompt,
                [{"role": "user", "content": "我刚才在看 wikia2/test-git"}],
                None,
            )
        )
        assert answer.startswith("【Coding Agent 实际执行】")
        assert any(item["tool"] == "coding_agent.task" for item in request.scope["fdex_employee_tool_events"])

    assert [prompt for _, prompt in calls] == prompts


def test_plain_conceptual_question_can_still_use_generic_ai(monkeypatch) -> None:
    from app import user_app_routes as routes

    monkeypatch.setattr(chat_runtime, "collect_employee_tool_context", lambda *_args, **_kwargs: EmployeeToolContext())

    class Result:
        content = "GIL 是 CPython 中的一把全局解释器锁。"
        media = []

    async def fake_client_ai(*_args, **_kwargs):
        return Result()

    async def fail_agent(*_args, **_kwargs):
        raise AssertionError("plain conceptual questions should not create an Agent task")

    monkeypatch.setattr(routes, "client_ai", fake_client_ai)
    monkeypatch.setattr(chat_runtime, "_run_coding_agent", fail_agent)
    monkeypatch.setattr(routes, "_conversation_context", lambda _history: "")
    monkeypatch.setattr(routes, "_employee_system", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr(routes, "_attachment_inputs", lambda _upload: asyncio.sleep(0, result=([], None, [], "")))

    request = SimpleNamespace(scope={})
    answer = asyncio.run(
        chat_runtime.ask_employee_with_tools(
            request,
            OWNER,
            {"coding_agent": True, "name": "淘小宝"},
            "什么是 Python 的 GIL？",
            [],
            None,
        )
    )
    assert "GIL" in answer
