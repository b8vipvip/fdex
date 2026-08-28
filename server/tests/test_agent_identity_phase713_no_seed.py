from pathlib import Path


def test_general_identity_runtime_does_not_seed_old_business_roles() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = (root / "server/app/agent_identity_runtime.py").read_text(encoding="utf-8")
    assert "资料中心" not in runtime
    assert "经营中心" not in runtime
    assert "运营中心" not in runtime
    assert "数据中心" not in runtime
