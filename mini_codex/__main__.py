from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .agent import AgentSettings, CodingAgent
from .config import ALLOWED_API_MODES, load_config, load_redaction_values, validate_api_mode
from .console import Console
from .permissions import (
    BUILTIN_TEMPLATE_NAMES,
    PermissionProfile,
    create_permission_profile,
    delete_permission_profile,
    list_permission_profiles,
    load_permission_profile,
    permission_profile_to_json,
    save_permission_profile,
)
from .preflight import inspect_git_state


PERMISSION_COMMAND_NAMES = {"permission", "permissions", "permision", "permisions"}
LOCAL_COMMAND_NAMES = {"config", "doctor", "dry", "gui", "test", "logs", "last", "status", "diff"}
APPROVAL_MODES = {"always", "never"}
RESUME_TRANSCRIPT_LIMIT = 20_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-codex",
        description="Run a small OpenAI-powered local coding agent.",
        epilog=(
            "Local commands: config, doctor, dry, gui, test, logs, last, status, diff, "
            "permission list/show/new/delete. Slash forms such as /permission/new are also supported."
        ),
    )
    parser.add_argument("task", nargs="*", help="Task to perform. If omitted, interactive mode starts.")
    parser.add_argument("--workspace", default=".", help="Workspace root. Defaults to current directory.")
    parser.add_argument("--model", help="Override OPENAI_MODEL from .env.")
    parser.add_argument("--api-mode", choices=sorted(ALLOWED_API_MODES), help="Override OPENAI_API_MODE from .env.")
    parser.add_argument("--request-timeout", type=float, help="OpenAI/APIM request timeout in seconds.")
    parser.add_argument("--max-iterations", type=int, default=3, help="Maximum repair iterations.")
    parser.add_argument("--max-files", type=int, default=80, help="Maximum files to include in model context.")
    parser.add_argument("--max-file-bytes", type=int, default=20_000, help="Per-file context byte limit.")
    parser.add_argument("--max-context-bytes", type=int, default=180_000, help="Total context byte limit.")
    parser.add_argument("--max-output-tokens", type=int, default=12_000, help="Maximum model output tokens.")
    parser.add_argument("--dry-run", action="store_true", help="Show proposed work without writing files or running commands.")
    parser.add_argument("--no-commands", action="store_true", help="Apply file edits but do not run shell commands.")
    parser.add_argument("--permission", help="Permission profile to apply to model-proposed shell commands.")
    parser.add_argument(
        "--approval-mode",
        choices=sorted(APPROVAL_MODES),
        default="never",
        help="Ask before running shell commands. Defaults to never.",
    )
    parser.add_argument("--print-prompt", action="store_true", help="Print the prompt sent to the model.")
    parser.add_argument("--resume-last", action="store_true", help="Include the latest run transcript as context.")
    parser.add_argument("--no-transcript", action="store_true", help="Do not write a run transcript under .mini_codex/runs.")
    parser.add_argument("--command-output-limit", type=int, default=4_000, help="Maximum stdout/stderr characters shown per stream.")
    parser.add_argument("--gui", action="store_true", help="Open the desktop app window.")
    parser.add_argument("--doctor", action="store_true", help="Check local setup and resolved non-secret configuration.")
    parser.add_argument("--show-config", action="store_true", help="Print resolved non-secret configuration and exit.")
    return parser


def run_task(task: str, args: argparse.Namespace, console: Console) -> int:
    workspace = Path(args.workspace)
    if not workspace.exists():
        raise RuntimeError(f"Workspace not found: {workspace}")
    if not workspace.is_dir():
        raise RuntimeError(f"Workspace is not a directory: {workspace}")
    config = load_config(workspace)
    model = args.model or config.model
    base_url = resolve_base_url_for_model(config.base_url, old_model=config.model, new_model=model)
    api_mode = validate_api_mode(args.api_mode or config.api_mode)
    request_timeout = args.request_timeout if args.request_timeout is not None else config.request_timeout
    if request_timeout <= 0:
        raise RuntimeError("--request-timeout must be greater than zero.")
    approval_mode = getattr(args, "approval_mode", "never")
    if approval_mode not in APPROVAL_MODES:
        raise RuntimeError("--approval-mode must be one of: always, never.")
    permission_profile = _load_selected_permission_profile(workspace, args.permission)

    if args.show_config:
        print_resolved_config(
            config,
            model=model,
            base_url=base_url,
            api_mode=api_mode,
            request_timeout=request_timeout,
            approval_mode=approval_mode,
            permission_profile=permission_profile,
            console=console,
        )
        return 0

    initial_observations = _resume_observations(workspace, console) if getattr(args, "resume_last", False) else ()

    settings = AgentSettings(
        workspace=workspace,
        model=model,
        api_key=config.api_key,
        base_url=base_url,
        api_key_header=config.api_key_header,
        api_mode=api_mode,
        azure_api_version=config.azure_api_version,
        request_timeout=request_timeout,
        max_iterations=args.max_iterations,
        max_files=args.max_files,
        max_file_bytes=args.max_file_bytes,
        max_context_bytes=args.max_context_bytes,
        max_output_tokens=args.max_output_tokens,
        dry_run=args.dry_run,
        run_commands=not args.no_commands,
        print_prompt=args.print_prompt,
        transcript=not args.no_transcript,
        command_output_limit=args.command_output_limit,
        permission_profile=permission_profile,
        approval_mode=approval_mode,
        initial_observations=initial_observations,
        redaction_values=load_redaction_values(workspace),
    )
    agent = CodingAgent(settings, console=console)
    result = agent.run(task)
    return 0 if result.ok else 1


def resolve_base_url_for_model(base_url: str | None, *, old_model: str, new_model: str) -> str | None:
    if not base_url or old_model == new_model:
        return base_url
    stripped = base_url.rstrip("/")
    suffix = "/" + old_model.strip("/")
    if not stripped.endswith(suffix):
        return base_url
    return stripped[: -len(suffix)] + "/" + new_model.strip("/") + "/"


def print_resolved_config(
    config,
    *,
    model: str,
    base_url: str | None,
    api_mode: str,
    request_timeout: float,
    permission_profile: PermissionProfile | None,
    console: Console,
    approval_mode: str = "never",
) -> None:
    console.info(f"Model: {model}")
    console.info(f"API mode: {api_mode}")
    console.info(f"Base URL configured: {'yes' if base_url else 'no'}")
    console.info(f"Auth header: {config.api_key_header or 'Authorization'}")
    console.info(f"Azure API version: {config.azure_api_version}")
    console.info(f"Request timeout: {request_timeout:g}s")
    console.info(f"Approval mode: {approval_mode}")
    permission = permission_profile.name if permission_profile is not None else "default dangerous-command blocklist"
    console.info(f"Permission profile: {permission}")


def run_doctor(args: argparse.Namespace, console: Console) -> int:
    workspace = Path(args.workspace).resolve()
    ok = True

    console.rule("Doctor")
    if not workspace.exists():
        console.error(f"Workspace not found: {workspace}")
        return 1
    console.info(f"[ok] Workspace: {workspace}")
    if not workspace.is_dir():
        console.error(f"Workspace is not a directory: {workspace}")
        return 1
    console.info("[ok] Workspace type: directory")

    env_path = workspace / ".env"
    if env_path.exists():
        console.info(f"[ok] .env: {env_path}")
    else:
        console.warn(".env: not found; shell environment variables may still work")
    if _workspace_is_git_repo(workspace):
        if _git_check_ignore(workspace, ".env"):
            console.info("[ok] .env git ignore: .env is ignored")
        else:
            console.error(".env git ignore: .env is not ignored")
            ok = False

    if os.access(workspace, os.W_OK):
        console.info("[ok] Workspace writable")
    else:
        console.error("Workspace is not writable")
        ok = False

    try:
        config = load_config(Path(args.workspace))
    except Exception as exc:
        console.error(f"Config: {exc}")
        return 1

    model = args.model or config.model
    base_url = resolve_base_url_for_model(config.base_url, old_model=config.model, new_model=model)
    api_mode = validate_api_mode(args.api_mode or config.api_mode)
    request_timeout = args.request_timeout if args.request_timeout is not None else config.request_timeout
    approval_mode = getattr(args, "approval_mode", "never")
    try:
        permission_profile = _load_selected_permission_profile(workspace, args.permission)
    except Exception as exc:
        console.error(f"Permission profile: {exc}")
        return 1
    console.info("[ok] Config loaded")
    print_resolved_config(
        config,
        model=model,
        base_url=base_url,
        api_mode=api_mode,
        request_timeout=request_timeout,
        permission_profile=permission_profile,
        console=console,
        approval_mode=approval_mode,
    )
    return 0 if ok else 1


def run_permission_command(workspace: Path, words: list[str], console: Console) -> int:
    parser = build_permission_parser()
    try:
        namespace = parser.parse_args(_normalize_permission_args(words))
    except SystemExit as exc:
        return int(exc.code)

    command = namespace.permission_command or "list"
    try:
        if command == "list":
            return _permission_list(workspace, console)
        if command == "show":
            return _permission_show(workspace, namespace.name, console)
        if command == "new":
            return _permission_new(workspace, namespace, console)
        if command == "delete":
            return _permission_delete(workspace, namespace.name, console)
    except Exception as exc:
        console.error(str(exc))
        return 1
    console.error(f"Unknown permission command: {command}")
    return 1


def run_logs_command(workspace: Path, console: Console, *, limit: int = 20) -> int:
    if not workspace.exists():
        console.error(f"Workspace not found: {workspace}")
        return 1
    if not workspace.is_dir():
        console.error(f"Workspace is not a directory: {workspace}")
        return 1
    paths = list_recent_transcripts(workspace, limit=limit)
    if not paths:
        console.info("No run transcripts yet.")
        return 0
    for path in paths:
        console.info(str(path))
    return 0


def run_last_command(workspace: Path, console: Console) -> int:
    if not workspace.exists():
        console.error(f"Workspace not found: {workspace}")
        return 1
    if not workspace.is_dir():
        console.error(f"Workspace is not a directory: {workspace}")
        return 1
    paths = list_recent_transcripts(workspace, limit=1)
    if not paths:
        console.info("No run transcripts yet.")
        return 1
    try:
        console.info(paths[0].read_text(encoding="utf-8").rstrip())
    except OSError as exc:
        console.error(str(exc))
        return 1
    return 0


def run_status_command(workspace: Path, console: Console) -> int:
    workspace = workspace.resolve()
    console.rule("Status")
    console.info(f"Workspace: {workspace}")
    if not workspace.exists():
        console.error(f"Workspace not found: {workspace}")
        return 1
    if not workspace.is_dir():
        console.error(f"Workspace is not a directory: {workspace}")
        return 1

    try:
        git_state = inspect_git_state(workspace)
    except (OSError, subprocess.SubprocessError) as exc:
        console.warn(f"Git status unavailable: {exc}")
    else:
        console.info(f"Git: {'repository' if git_state.is_repo else 'not a repository'}")
        for line in git_state.output.splitlines() or ["clean"]:
            console.plan_item(line)

    recent = list_recent_transcripts(workspace, limit=1)
    console.info(f"Latest transcript: {recent[0] if recent else 'none'}")
    profiles = ", ".join(profile.name for profile in list_permission_profiles(workspace))
    console.info(f"Permission profiles: {profiles or 'none'}")
    return 0


def run_diff_command(workspace: Path, console: Console) -> int:
    workspace = workspace.resolve()
    if not workspace.exists():
        console.error(f"Workspace not found: {workspace}")
        return 1
    if not workspace.is_dir():
        console.error(f"Workspace is not a directory: {workspace}")
        return 1

    console.rule("Diff")
    try:
        git_state = inspect_git_state(workspace)
    except (OSError, subprocess.SubprocessError) as exc:
        console.error(f"Git status unavailable: {exc}")
        return 1
    if not git_state.is_repo:
        console.error("Workspace is not a git repository.")
        return 1

    code = 0
    for title, command in (
        ("Staged changes", ["git", "diff", "--cached", "--stat"]),
        ("Unstaged changes", ["git", "diff", "--stat"]),
    ):
        completed = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        if completed.returncode != 0:
            console.error((completed.stderr or completed.stdout or "git diff failed").strip())
            code = completed.returncode
            continue
        console.info(f"{title}:")
        output = completed.stdout.strip()
        console.info(output if output else "none")
    return code


def run_test_command(workspace: Path, extra_args: list[str], console: Console) -> int:
    if not workspace.exists():
        console.error(f"Workspace not found: {workspace}")
        return 1
    if not workspace.is_dir():
        console.error(f"Workspace is not a directory: {workspace}")
        return 1
    command = build_test_command(sys.executable, extra_args)
    console.command(" ".join(shlex.quote(part) for part in command), why="run pytest", timeout=None)
    completed = subprocess.run(
        command,
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    console.command_result(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
    return completed.returncode


def build_test_command(python_executable: str, extra_args: list[str]) -> list[str]:
    return [python_executable, "-m", "pytest", "-q", *extra_args]


def list_recent_transcripts(workspace: Path, *, limit: int = 20) -> list[Path]:
    run_dir = workspace.resolve() / ".mini_codex" / "runs"
    if not run_dir.exists():
        return []
    paths = [path for path in run_dir.glob("*.md") if path.is_file()]
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _resume_observations(workspace: Path, console: Console) -> tuple[str, ...]:
    paths = list_recent_transcripts(workspace, limit=1)
    if not paths:
        console.warn("--resume-last requested, but no previous transcript was found.")
        return ()
    path = paths[0]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        console.warn(f"--resume-last requested, but the latest transcript could not be read: {exc}")
        return ()
    if len(text) > RESUME_TRANSCRIPT_LIMIT:
        text = text[-RESUME_TRANSCRIPT_LIMIT:]
        prefix = f"Previous run transcript ({path.name}, last {RESUME_TRANSCRIPT_LIMIT:,} characters):"
    else:
        prefix = f"Previous run transcript ({path.name}):"
    console.info(f"Resuming from transcript: {path}")
    return (f"{prefix}\n{text}",)


def _workspace_is_git_repo(workspace: Path) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    return completed.returncode == 0


def _git_check_ignore(workspace: Path, relative_path: str) -> bool:
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", relative_path],
        cwd=workspace,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )
    return completed.returncode == 0


def build_permission_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-codex permission",
        description="Manage mini-codex permission profiles.",
    )
    subparsers = parser.add_subparsers(dest="permission_command")

    subparsers.add_parser("list", help="List built-in and custom permission profiles.")

    show_parser = subparsers.add_parser("show", help="Print a permission profile as JSON.")
    show_parser.add_argument("name", help="Profile name.")

    new_parser = subparsers.add_parser("new", help="Create a custom permission profile.")
    new_parser.add_argument("name", help="Profile name.")
    new_parser.add_argument(
        "--template",
        choices=sorted(BUILTIN_TEMPLATE_NAMES),
        default="restricted",
        help="Built-in profile to copy. Defaults to restricted.",
    )
    new_parser.add_argument("--description", default="", help="Profile description.")
    new_parser.add_argument("--allow", action="append", default=None, help="Regex for commands to allow.")
    new_parser.add_argument("--deny", action="append", default=None, help="Regex for commands to deny.")
    new_parser.add_argument("--no-shell", action="store_true", help="Block all shell commands in this profile.")
    new_parser.add_argument("--force", action="store_true", help="Overwrite an existing custom profile.")

    delete_parser = subparsers.add_parser("delete", help="Delete a custom permission profile.")
    delete_parser.add_argument("name", help="Profile name.")
    return parser


def _permission_list(workspace: Path, console: Console) -> int:
    console.info("Permission profiles:")
    for profile in list_permission_profiles(workspace):
        source = "built-in" if profile.builtin else "custom"
        mode = _permission_mode_label(profile)
        detail = f" - {profile.description}" if profile.description else ""
        console.plan_item(f"{profile.name} ({source}, {mode}){detail}")
    return 0


def _permission_show(workspace: Path, name: str, console: Console) -> int:
    profile = load_permission_profile(workspace, name)
    console.info(permission_profile_to_json(profile).rstrip())
    return 0


def _permission_new(workspace: Path, namespace: argparse.Namespace, console: Console) -> int:
    profile = create_permission_profile(
        namespace.name,
        template=namespace.template,
        description=namespace.description,
        allow_patterns=namespace.allow,
        deny_patterns=namespace.deny,
        run_shell=False if namespace.no_shell else None,
    )
    path = save_permission_profile(workspace, profile, force=namespace.force)
    console.info(f"created: {path}")
    return 0


def _permission_delete(workspace: Path, name: str, console: Console) -> int:
    path = delete_permission_profile(workspace, name)
    console.info(f"deleted: {path}")
    return 0


def _permission_mode_label(profile: PermissionProfile) -> str:
    if not profile.run_shell:
        return "shell blocked"
    if profile.allow_patterns:
        return "allow list"
    return "shell allowed"


def _load_selected_permission_profile(workspace: Path, name: str | None) -> PermissionProfile | None:
    if not name:
        return None
    return load_permission_profile(workspace, name)


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    local_command = _extract_local_command(raw_argv)
    if local_command is not None:
        kind, workspace, words = local_command
        console = Console()
        if kind == "logs":
            return run_logs_command(Path(workspace), console)
        if kind == "last":
            return run_last_command(Path(workspace), console)
        if kind == "status":
            return run_status_command(Path(workspace), console)
        if kind == "diff":
            return run_diff_command(Path(workspace), console)
        if kind == "test":
            return run_test_command(Path(workspace), words, console)
        raw_argv = words

    permission_command = _extract_permission_command(raw_argv)
    if permission_command is not None:
        workspace, words = permission_command
        return run_permission_command(Path(workspace), words, Console())

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    console = Console()

    if args.max_iterations < 1:
        parser.error("--max-iterations must be at least 1")
    if args.request_timeout is not None and args.request_timeout <= 0:
        parser.error("--request-timeout must be greater than zero")
    if args.command_output_limit < 0:
        parser.error("--command-output-limit must be at least 0")

    if args.gui:
        task = " ".join(args.task).strip()
        try:
            from .gui import run_gui

            return run_gui(workspace=Path(args.workspace), initial_task=task)
        except Exception as exc:
            console.error(str(exc))
            return 1

    if args.doctor:
        try:
            return run_doctor(args, console)
        except Exception as exc:
            console.error(str(exc))
            return 1

    task = " ".join(args.task).strip()
    if args.show_config:
        try:
            return run_task(task, args, console)
        except Exception as exc:
            console.error(str(exc))
            return 1

    if task:
        try:
            return run_task(task, args, console)
        except KeyboardInterrupt:
            console.error("Interrupted.")
            return 130
        except Exception as exc:
            console.error(str(exc))
            return 1

    console.info("Interactive mode. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            task = input("mini-codex> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.info("")
            return 0

        if task.lower() in {"exit", "quit"}:
            return 0
        if not task:
            continue

        try:
            permission_words = _permission_words_from_text(task)
            if permission_words is not None:
                code = run_permission_command(Path(args.workspace), permission_words, console)
            else:
                local_words = _local_words_from_text(task, workspace=args.workspace)
                if local_words is not None:
                    kind, workspace, words = local_words
                    if kind == "logs":
                        code = run_logs_command(Path(workspace), console)
                    elif kind == "last":
                        code = run_last_command(Path(workspace), console)
                    elif kind == "status":
                        code = run_status_command(Path(workspace), console)
                    elif kind == "diff":
                        code = run_diff_command(Path(workspace), console)
                    elif kind == "test":
                        code = run_test_command(Path(workspace), words, console)
                    else:
                        alias_args = build_parser().parse_args(words)
                        code = run_task(" ".join(alias_args.task).strip(), alias_args, console)
                else:
                    code = run_task(task, args, console)
        except KeyboardInterrupt:
            console.info("")
            return 130
        except Exception as exc:
            console.error(str(exc))
            code = 1
        if code != 0:
            console.warn("Task ended with errors.")


def _extract_permission_command(argv: list[str]) -> tuple[str, list[str]] | None:
    workspace, remaining = _split_workspace_arg(argv)
    words = _permission_words_from_tokens(remaining)
    if words is None:
        return None
    return workspace, words


def _extract_local_command(argv: list[str]) -> tuple[str, str, list[str]] | None:
    workspace, remaining = _split_workspace_arg(argv)
    if not remaining:
        return None
    first_parts = _slash_parts(remaining[0])
    if not first_parts:
        return None
    kind = first_parts[0].lower()
    if kind not in LOCAL_COMMAND_NAMES:
        return None
    words = first_parts[1:] + remaining[1:]
    if kind == "config":
        return kind, workspace, ["--workspace", workspace, "--show-config", *words]
    if kind == "doctor":
        return kind, workspace, ["--workspace", workspace, "--doctor", *words]
    if kind == "dry":
        return kind, workspace, ["--workspace", workspace, "--dry-run", "--no-commands", *words]
    if kind == "gui":
        return kind, workspace, ["--workspace", workspace, "--gui", *words]
    return kind, workspace, words


def _local_words_from_text(text: str, *, workspace: str) -> tuple[str, str, list[str]] | None:
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    command = _extract_local_command(["--workspace", workspace, *words])
    if command is None:
        return None
    return command


def _split_workspace_arg(argv: list[str]) -> tuple[str, list[str]]:
    workspace = "."
    remaining: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--workspace":
            if index + 1 >= len(argv):
                remaining.append(arg)
                index += 1
                continue
            workspace = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--workspace="):
            workspace = arg.split("=", 1)[1]
            index += 1
            continue
        remaining.append(arg)
        index += 1
    return workspace, remaining


def _permission_words_from_text(text: str) -> list[str] | None:
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    return _permission_words_from_tokens(words)


def _permission_words_from_tokens(words: list[str]) -> list[str] | None:
    if not words:
        return None
    first_parts = _slash_parts(words[0])
    if not first_parts or first_parts[0].lower() not in PERMISSION_COMMAND_NAMES:
        return None
    return _normalize_permission_args(first_parts[1:] + words[1:])


def _normalize_permission_args(words: list[str]) -> list[str]:
    normalized = list(words)
    if normalized:
        normalized[0] = normalized[0].lstrip("/")
    return normalized


def _slash_parts(token: str) -> list[str]:
    stripped = token.strip().lstrip("/")
    return [part for part in stripped.split("/") if part]


if __name__ == "__main__":
    sys.exit(main())
