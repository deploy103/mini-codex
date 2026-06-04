from pathlib import Path

import pytest

from mini_codex.__main__ import main
from mini_codex.permissions import (
    PermissionProfile,
    builtin_permission_profile,
    create_permission_profile,
    load_permission_profile,
    permission_denial_reason,
    save_permission_profile,
)


def test_create_save_and_load_permission_profile(tmp_path: Path):
    profile = create_permission_profile("team", template="readonly", description="Team policy")

    path = save_permission_profile(tmp_path, profile)
    loaded = load_permission_profile(tmp_path, "team")

    assert path == tmp_path / ".mini_codex" / "permissions" / "team.json"
    assert loaded.name == "team"
    assert loaded.description == "Team policy"
    assert loaded.run_shell is False


def test_load_builtin_permission_profile_is_case_insensitive(tmp_path: Path):
    loaded = load_permission_profile(tmp_path, "Trusted")

    assert loaded.name == "trusted"
    assert loaded.builtin is True


def test_create_permission_profile_rejects_builtin_name():
    with pytest.raises(ValueError):
        create_permission_profile("restricted")
    with pytest.raises(ValueError):
        create_permission_profile("Trusted")


def test_save_permission_profile_rejects_builtin_name(tmp_path: Path):
    with pytest.raises(ValueError):
        save_permission_profile(tmp_path, PermissionProfile(name="readonly"))


def test_permission_denial_reason_enforces_allow_patterns():
    profile = PermissionProfile(name="custom", allow_patterns=(r"^pytest\b",))

    assert permission_denial_reason("pytest -q", profile) is None
    assert "does not match allowed patterns" in permission_denial_reason("python -m pytest", profile)


def test_permission_denial_reason_enforces_readonly_profile():
    profile = PermissionProfile(name="readonly", run_shell=False)

    assert "blocks shell commands" in permission_denial_reason("pytest -q", profile)


def test_restricted_profile_allows_windows_venv_pytest_command():
    profile = builtin_permission_profile("restricted")

    assert permission_denial_reason(r".venv\Scripts\python.exe -m pytest -q", profile) is None
    assert permission_denial_reason(r".venv-win\Scripts\python.exe -m pytest -q", profile) is None


def test_restricted_profile_blocks_shell_chaining_after_allowed_command():
    profile = builtin_permission_profile("restricted")

    reason = permission_denial_reason("pytest -q && cat .env", profile)
    substitution_reason = permission_denial_reason("pytest -q $(cat .env)", profile)

    assert "shell control operators" in (reason or "")
    assert "shell control operators" in (substitution_reason or "")


def test_permission_new_cli_accepts_slash_command(tmp_path: Path):
    code = main(["--workspace", str(tmp_path), "/permission/new", "team"])

    assert code == 0
    assert (tmp_path / ".mini_codex" / "permissions" / "team.json").exists()


def test_permission_new_cli_accepts_nested_slash_subcommand(tmp_path: Path):
    code = main(["--workspace", str(tmp_path), "permission", "/new", "team"])

    assert code == 0
    assert (tmp_path / ".mini_codex" / "permissions" / "team.json").exists()


def test_permission_new_cli_accepts_common_misspelling(tmp_path: Path):
    code = main(["--workspace", str(tmp_path), "permision", "/new", "team"])

    assert code == 0
    assert (tmp_path / ".mini_codex" / "permissions" / "team.json").exists()
