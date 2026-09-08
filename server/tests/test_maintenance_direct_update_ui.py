from pathlib import Path


def test_server_update_is_direct_green_action_without_confirmation_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "templates" / "maintenance.html").read_text(encoding="utf-8")

    assert 'id="server-update-form" method="post" action="/admin/update"' in page
    assert 'data-confirm="确定从 GitHub main 更新服务端吗？' not in page
    assert 'id="server-update-confirm"' not in page
    assert "我确认要拉取 main 并更新服务端" not in page

    # Keep the existing backend safety token as an implicit/default confirmation so one click
    # immediately starts the fixed update_server.sh workflow while CSRF/dirty-worktree guards stay intact.
    assert '<input type="hidden" name="confirm" value="update">' in page
    assert (
        'id="server-update-button" class="button full" '
        'style="background:#16a34a;color:#fff;border-color:#16a34a"' in page
    )
    assert '>更新</button>' in page
    assert "updateButton.textContent = running ? '服务端正在更新…' : '更新';" in page
