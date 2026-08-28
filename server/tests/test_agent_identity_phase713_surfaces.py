from pathlib import Path


def test_general_web_surface_contains_no_retired_business_taxonomy() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/user_web_app_general.html").read_text(encoding="utf-8")
    for retired in ("AI 员工", "员工名称", "部门", "岗位", "行业", "企业知识库", "自动公司模式"):
        assert retired not in template
    assert "创建时只需填写身份定义提示词，也可以留空" in template
    assert "语文老师" in template
    assert "智体管理" in template


def test_runtime_uses_general_web_surface() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = (root / "server/app/agent_identity_runtime.py").read_text(encoding="utf-8")
    assert '"user_web_app_general.html"' in runtime
    assert "routes._render = generalized_render" in runtime


def test_android_project_surface_is_not_company_specific() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "app/src/main/java/com/b8vipvip/fdex/ui/WorkScreens.kt").read_text(encoding="utf-8")
    for retired in ("公司自动运营", "企业资料分析助手", "企业项目顾问", "行业", "部门"):
        assert retired not in source
    assert "自动协作" in source
