from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.codex_task_inputs import CodexTaskInputStore

OWNER = "usr_phase729_owner"
OTHER = "usr_phase729_other"
TASK = "a" * 32
OTHER_TASK = "b" * 32


def _store(tmp_path: Path) -> CodexTaskInputStore:
    store = CodexTaskInputStore(tmp_path / "inputs.sqlite3", tmp_path / "assets")
    store.init()
    return store


def test_official_user_input_shape_for_image_audio_skill_and_mention(tmp_path: Path) -> None:
    store = _store(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    source = worktree / "server" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    codex_home = tmp_path / "codex-home"
    skill = codex_home / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review\n", encoding="utf-8")

    image = store.add_media(
        OWNER,
        TASK,
        kind="localImage",
        filename="screen.png",
        mime="image/png",
        data=b"\x89PNG\r\n\x1a\n" + b"x" * 32,
    )
    audio = store.add_media(
        OWNER,
        TASK,
        kind="localAudio",
        filename="note.mp3",
        mime="audio/mpeg",
        data=b"ID3" + b"x" * 32,
    )
    store.add_skill(OWNER, TASK, "review")
    store.add_mention(OWNER, TASK, "server/app.py")

    items = store.build_user_inputs(
        OWNER,
        TASK,
        prompt="Fix this",
        worktree=worktree,
        codex_home=codex_home,
    )
    assert items[0] == {"type": "text", "text": "Fix this", "text_elements": []}
    assert {item["type"] for item in items[1:]} == {"localImage", "localAudio", "skill", "mention"}
    image_item = next(item for item in items if item["type"] == "localImage")
    audio_item = next(item for item in items if item["type"] == "localAudio")
    assert Path(image_item["path"]).is_file()
    assert Path(audio_item["path"]).is_file()
    assert Path(image_item["path"]).name.startswith("inp_")
    assert "screen.png" not in image_item["path"]
    assert image["path"] == image_item["path"]
    assert audio["path"] == audio_item["path"]
    mention = next(item for item in items if item["type"] == "mention")
    assert Path(mention["path"]) == source.resolve()
    skill_item = next(item for item in items if item["type"] == "skill")
    assert Path(skill_item["path"]) == (skill / "SKILL.md").resolve()


def test_media_type_size_and_magic_are_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="不支持"):
        store.add_media(OWNER, TASK, kind="localImage", filename="x.svg", mime="image/svg+xml", data=b"<svg/>")
    with pytest.raises(ValueError, match="不匹配"):
        store.add_media(OWNER, TASK, kind="localImage", filename="fake.png", mime="image/png", data=b"not-a-png")
    with pytest.raises(ValueError, match="不匹配"):
        store.add_media(OWNER, TASK, kind="localAudio", filename="fake.mp3", mime="audio/mpeg", data=b"not-audio")
    with pytest.raises(ValueError, match="大小"):
        store.add_media(OWNER, TASK, kind="localImage", filename="empty.png", mime="image/png", data=b"")


def test_owner_task_isolation_and_generated_asset_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = store.add_media(
        OWNER,
        TASK,
        kind="localImage",
        filename="../../secret.png",
        mime="image/png",
        data=b"\x89PNG\r\n\x1a\n" + b"z" * 16,
    )
    assert store.list(OWNER, TASK)[0]["id"] == row["id"]
    assert store.list(OTHER, TASK) == []
    assert store.list(OWNER, OTHER_TASK) == []
    assert Path(row["path"]).parent.name == TASK
    assert ".." not in Path(row["path"]).parts
    assert not store.delete(OTHER, TASK, row["id"])
    assert Path(row["path"]).exists()
    assert store.delete(OWNER, TASK, row["id"])
    assert not Path(row["path"]).exists()


def test_mention_rejects_absolute_parent_and_symlink_escape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for value in ("/etc/passwd", "../secret", "a/../../b", "~/secret"):
        with pytest.raises(ValueError):
            store.add_mention(OWNER, TASK, value)

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = worktree / "escape"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink unavailable")
    store.add_mention(OWNER, TASK, "escape")
    with pytest.raises(ValueError, match="越界"):
        store.build_user_inputs(OWNER, TASK, prompt="x", worktree=worktree, codex_home=tmp_path / "home")


def test_skill_is_owner_codex_home_scoped_and_symlink_escape_fails(tmp_path: Path) -> None:
    store = _store(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    codex_home = tmp_path / "home"
    skills = codex_home / "skills"
    skills.mkdir(parents=True)
    outside = tmp_path / "outside-skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text("nope", encoding="utf-8")
    try:
        (skills / "evil").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink unavailable")
    store.add_skill(OWNER, TASK, "evil")
    with pytest.raises(ValueError, match="越界"):
        store.build_user_inputs(OWNER, TASK, prompt="x", worktree=worktree, codex_home=codex_home)


def test_retry_clone_copies_media_without_reusing_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.add_media(
        OWNER,
        TASK,
        kind="localAudio",
        filename="voice.mp3",
        mime="audio/mpeg",
        data=b"ID3" + b"a" * 16,
    )
    store.add_mention(OWNER, TASK, "README.md")
    assert store.clone_task(OWNER, TASK, OTHER_TASK) == 2
    rows = store.list(OWNER, OTHER_TASK)
    cloned_media = next(row for row in rows if row["kind"] == "localAudio")
    assert cloned_media["path"] != original["path"]
    assert Path(cloned_media["path"]).read_bytes() == Path(original["path"]).read_bytes()
    assert next(row for row in rows if row["kind"] == "mention")["ref"] == "README.md"


def test_owner_cleanup_deletes_metadata_and_assets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = store.add_media(
        OWNER,
        TASK,
        kind="localImage",
        filename="x.png",
        mime="image/png",
        data=b"\x89PNG\r\n\x1a\n" + b"z" * 16,
    )
    store.add_skill(OTHER, OTHER_TASK, "safe")
    cleanup = store.delete_owner(OWNER)
    assert cleanup["records"] == 1
    assert cleanup["asset_directories"] == 1
    assert not Path(row["path"]).exists()
    assert store.list(OWNER, TASK) == []
    assert len(store.list(OTHER, OTHER_TASK)) == 1


def test_phase729_runtime_route_and_account_cleanup_wiring() -> None:
    root = Path(__file__).parents[1] / "app"
    host_entry = (root / "codex_host_entry.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")
    cleanup = (root / "account_cleanup.py").read_text(encoding="utf-8")
    routes = (root / "codex_task_input_routes.py").read_text(encoding="utf-8")
    template = (root / "templates" / "user_agent_inputs.html").read_text(encoding="utf-8")
    assert "codex_task_input_scope(task)" in host_entry
    assert "install_codex_task_input_runtime()" in host_entry
    assert "codex_task_input_router" in main
    assert "app.include_router(codex_task_input_router)" in main
    assert "codex_task_input_store().delete_owner(clean)" in cleanup
    assert "_require_editable(task)" in routes
    assert 'enctype="multipart/form-data"' in template
    assert "localImage" in template and "localAudio" in template
    assert "Skill" in template and "Mention" in template


def test_official_schema_contract_is_not_reencoded_as_prompt_only() -> None:
    # Keep the schema-light runtime contract explicit in tests: Phase 7.29 must emit the exact
    # discriminators used by Codex app-server UserInput instead of inventing FDEX tool prompts.
    source = (Path(__file__).parents[1] / "app" / "codex_task_inputs.py").read_text(encoding="utf-8")
    for discriminator in ("localImage", "localAudio", "skill", "mention"):
        assert discriminator in source
    installer = (Path(__file__).parents[1] / "app" / "codex_task_input_install.py").read_text(encoding="utf-8")
    assert 'payload["input"]' in installer
