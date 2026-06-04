from pathlib import Path

import pytest

from mini_codex.models import Edit
from mini_codex.workspace import apply_edits, apply_unified_patch, build_workspace_context, safe_workspace_path


def test_safe_workspace_path_blocks_escape(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_workspace_path(tmp_path, "../outside.py")


def test_safe_workspace_path_blocks_env(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_workspace_path(tmp_path, ".env")


def test_apply_edits_creates_file(tmp_path: Path):
    applied = apply_edits(
        tmp_path,
        [Edit(path="src/app.py", action="create", content="print('ok')\n")],
    )

    assert applied[0].changed is True
    assert applied[0].added_lines == 1
    assert applied[0].removed_lines == 0
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_apply_edits_reports_line_delta_for_update_and_delete(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    updated = apply_edits(
        tmp_path,
        [Edit(path="app.py", action="update", content="one\nTWO\nfour\n")],
    )
    deleted = apply_edits(tmp_path, [Edit(path="app.py", action="delete")])

    assert updated[0].changed is True
    assert updated[0].added_lines == 2
    assert updated[0].removed_lines == 2
    assert deleted[0].changed is True
    assert deleted[0].added_lines == 0
    assert deleted[0].removed_lines == 3


def test_apply_edits_applies_unified_patch(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    applied = apply_edits(
        tmp_path,
        [
            Edit(
                path="app.py",
                action="patch",
                content="@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n",
            )
        ],
    )

    assert applied[0].changed is True
    assert applied[0].added_lines == 1
    assert applied[0].removed_lines == 1
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_apply_unified_patch_rejects_mismatched_context():
    with pytest.raises(ValueError, match="does not match"):
        apply_unified_patch("one\ntwo\n", "@@ -1,2 +1,2 @@\n one\n-three\n+THREE\n")


def test_apply_edits_rejects_patch_for_missing_file(tmp_path: Path):
    with pytest.raises(ValueError, match="Cannot patch missing file"):
        apply_edits(tmp_path, [Edit(path="missing.py", action="patch", content="@@ -1 +1 @@\n-old\n+new\n")])


def test_apply_edits_refuses_to_delete_directory(tmp_path: Path):
    (tmp_path / "src").mkdir()

    with pytest.raises(ValueError, match="Refusing to delete directory"):
        apply_edits(tmp_path, [Edit(path="src", action="delete")])


def test_workspace_context_excludes_env(tmp_path: Path):
    (tmp_path / ".env").write_text("APIM_KEY=secret\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    context = build_workspace_context(
        tmp_path,
        max_files=10,
        max_file_bytes=10_000,
        max_context_bytes=20_000,
    )

    assert "APIM_KEY" not in context
    assert "app.py" in context


def test_workspace_context_excludes_windows_venv(tmp_path: Path):
    (tmp_path / ".venv-win" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv-win" / "Scripts" / "activate.py").write_text("venv noise\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    context = build_workspace_context(
        tmp_path,
        max_files=10,
        max_file_bytes=10_000,
        max_context_bytes=20_000,
    )

    assert ".venv-win" not in context
    assert "venv noise" not in context
    assert "app.py" in context
