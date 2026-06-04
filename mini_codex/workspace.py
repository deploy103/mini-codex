from __future__ import annotations

import os
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .models import Edit


IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".venv-win",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    ".mini_codex",
}

IGNORED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".DS_Store",
}

SENSITIVE_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}


@dataclass(frozen=True)
class AppliedEdit:
    path: str
    action: str
    changed: bool
    added_lines: int = 0
    removed_lines: int = 0


def build_workspace_context(
    root: Path,
    *,
    max_files: int,
    max_file_bytes: int,
    max_context_bytes: int,
) -> str:
    root = root.resolve()
    files = list(_iter_context_files(root, max_files=max_files))

    tree = "\n".join(str(path.relative_to(root)) for path in files) or "(empty workspace)"
    sections = [f"Tree:\n{tree}\n"]
    used = len(sections[0].encode("utf-8"))

    for path in files:
        rel = str(path.relative_to(root))
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > max_file_bytes:
            continue
        if _looks_binary(raw):
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue

        section = f"\n--- {rel} ---\n{text}\n"
        size = len(section.encode("utf-8"))
        if used + size > max_context_bytes:
            sections.append("\n(Context truncated because max_context_bytes was reached.)\n")
            break
        sections.append(section)
        used += size

    return "".join(sections)


def apply_edits(root: Path, edits: list[Edit], *, dry_run: bool = False) -> list[AppliedEdit]:
    results: list[AppliedEdit] = []
    for edit in edits:
        target = safe_workspace_path(root, edit.path)
        rel = str(target.relative_to(root.resolve()))
        existing = target.read_text(encoding="utf-8") if target.exists() and target.is_file() else None

        if edit.action == "delete":
            if target.exists() and target.is_dir():
                raise ValueError(f"Refusing to delete directory: {rel}")
            changed = target.exists()
            added_lines, removed_lines = _line_delta(existing or "", "")
            if changed and not dry_run:
                target.unlink()
            results.append(
                AppliedEdit(
                    path=rel,
                    action=edit.action,
                    changed=changed,
                    added_lines=added_lines if changed else 0,
                    removed_lines=removed_lines if changed else 0,
                )
            )
            continue

        content = edit.content or ""
        changed = existing != content
        added_lines, removed_lines = _line_delta(existing or "", content)
        if changed and not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="")
        results.append(
            AppliedEdit(
                path=rel,
                action=edit.action,
                changed=changed,
                added_lines=added_lines if changed else 0,
                removed_lines=removed_lines if changed else 0,
            )
        )

    return results


def safe_workspace_path(root: Path, relative_path: str) -> Path:
    if not relative_path or relative_path.strip() != relative_path:
        raise ValueError(f"Invalid path: {relative_path!r}")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {relative_path}")
    if _is_sensitive_path(candidate):
        raise ValueError(f"Refusing to access sensitive path: {relative_path}")

    root = root.resolve()
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {relative_path}") from exc
    return target


def _iter_context_files(root: Path, *, max_files: int) -> list[Path]:
    found: list[Path] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".mypy_cache"))
        for file_name in sorted(files):
            path = current_path / file_name
            rel = path.relative_to(root)
            if _is_sensitive_path(rel):
                continue
            found.append(path)
            if len(found) >= max_files:
                return found
    return found


def _looks_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:4096]


def _line_delta(old: str, new: str) -> tuple[int, int]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    added = 0
    removed = 0
    matcher = SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed += old_end - old_start
        added += new_end - new_start
    return added, removed


def _is_sensitive_path(path: Path) -> bool:
    parts = set(path.parts)
    name = path.name
    if name in IGNORED_FILE_NAMES:
        return True
    if name.startswith(".env.") or name.endswith(".secret") or name.endswith(".secrets"):
        return True
    if any(part in IGNORED_DIRS for part in parts):
        return True
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
        return True
    if "credential" in lowered or "secret" in lowered:
        return True
    return False
