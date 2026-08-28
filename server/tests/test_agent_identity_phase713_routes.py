from pathlib import Path


def test_general_agent_routes_precede_legacy_employee_routes() -> None:
    root = Path(__file__).resolve().parents[2]
    main = (root / "server/app/main.py").read_text(encoding="utf-8")
    assert main.index("app.include_router(agent_identity_router)") < main.index("app.include_router(user_app_router)")


def test_identity_runtime_precedes_coding_agent_wrapper() -> None:
    root = Path(__file__).resolve().parents[2]
    main = (root / "server/app/main.py").read_text(encoding="utf-8")
    assert main.index("install_agent_identity_runtime()") < main.index("install_employee_chat_runtime()")
