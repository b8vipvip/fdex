"""End-to-end smoke test for the MVP API.

Run from ai-business-assistant/backend after installing requirements:
    python scripts/smoke_test.py
    PYTHONPATH=. python scripts/smoke_test.py

The script uses an isolated temporary SQLite database and upload directory,
then verifies auth, JWT-protected requests, upload, asset analysis,
comprehensive report generation, and Markdown export.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_root = Path(tmpdir)
        os.environ["DATABASE_URL"] = f"sqlite:///{temp_root / 'smoke.db'}"
        os.environ["UPLOAD_DIR"] = str(temp_root / "uploads")
        os.environ["SECRET_KEY"] = "smoke-test-secret"

        # Import after environment variables are set so app settings use the
        # isolated smoke-test database/upload paths.
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services.file_classifier import classify_file

        for filename in ("server.log", "app.conf", "settings.ini", "config.yaml", "config.yml", ".env.example"):
            assert classify_file(filename) == "text", filename

        with TestClient(app) as client:
            health = client.get("/api/health")
            assert health.status_code == 200, health.text

            register_payload = {
                "email": "smoke@example.com",
                "name": "Smoke Tester",
                "password": "password123",
                "professional_level": "business",
            }
            registered = client.post("/api/auth/register", json=register_payload)
            assert registered.status_code == 200, registered.text
            token = registered.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            employees = client.get("/api/employees", headers=headers)
            assert employees.status_code == 200, employees.text
            assert len(employees.json()) >= 11
            project_manager = next(item for item in employees.json() if item["position"] == "项目经理")
            employee_id = project_manager["id"]
            conversation = client.post(
                f"/api/employees/{employee_id}/messages",
                headers=headers,
                json={"content": "帮我创建一个项目，做一个订单自动分析系统。"},
            )
            assert conversation.status_code == 200, conversation.text
            reply = conversation.json()["employee_message"]
            confirmed = client.post(
                f"/api/employees/{employee_id}/messages/{reply['id']}/confirm-create-project",
                headers=headers,
            )
            assert confirmed.status_code == 200, confirmed.text

            me = client.get("/api/auth/me", headers=headers)
            assert me.status_code == 200, me.text
            assert me.json()["email"] == register_payload["email"]

            logged_in = client.post(
                "/api/auth/login",
                json={"email": register_payload["email"], "password": register_payload["password"]},
            )
            assert logged_in.status_code == 200, logged_in.text
            assert logged_in.json()["access_token"]

            project = client.post(
                "/api/projects",
                headers=headers,
                json={
                    "title": "烟测项目",
                    "description": "开发一个内部资料分析系统，支持上传文件并生成报告。",
                    "professional_level": "business",
                },
            )
            assert project.status_code == 200, project.text
            project_id = project.json()["id"]

            uploaded = client.post(
                f"/api/projects/{project_id}/assets/upload",
                headers=headers,
                files={"file": ("requirements.txt", b"need website and api", "text/plain")},
            )
            assert uploaded.status_code == 200, uploaded.text
            asset = uploaded.json()
            assert asset["file_type"] == "text"

            assets = client.get(f"/api/projects/{project_id}/assets", headers=headers)
            assert assets.status_code == 200, assets.text
            assert len(assets.json()) == 1

            asset_analysis = client.post(f"/api/assets/{asset['id']}/analyze", headers=headers)
            assert asset_analysis.status_code == 200, asset_analysis.text
            assert "文本资料分析报告" in asset_analysis.json()["summary"]

            context = client.get(f"/api/projects/{project_id}/context", headers=headers)
            assert context.status_code == 200, context.text
            assert context.json()["project"]["id"] == project_id

            reports = client.post(f"/api/projects/{project_id}/analyze", headers=headers)
            assert reports.status_code == 200, reports.text
            report_list = reports.json()
            assert len(report_list) >= 4
            report_types = {report["report_type"] for report in report_list}
            assert "comprehensive_analysis" in report_types
            assert "requirement_analysis" in report_types

            exported = client.get(f"/api/reports/{report_list[0]['id']}/export-md", headers=headers)
            assert exported.status_code == 200, exported.text
            assert exported.headers["content-type"].startswith("text/markdown")
            assert exported.text.endswith("\n")

    print("Smoke test passed: auth, employees, chat confirmation, project context, upload, analysis, reports, and Markdown export are OK.")


if __name__ == "__main__":
    main()
