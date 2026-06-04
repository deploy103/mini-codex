from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import dumps, loads
from pathlib import Path
from typing import Sequence
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .permissions import list_permission_profiles

try:
    import tkinter as tk
    from tkinter import filedialog, ttk
except ImportError:  # pragma: no cover
    tk = None
    ttk = None
    filedialog = None


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def build_agent_command(
    python_executable: str,
    *,
    workspace: Path,
    task: str = "",
    dry_run: bool = False,
    no_commands: bool = False,
    permission: str = "",
    extra_args: Sequence[str] = (),
) -> list[str]:
    command = [
        python_executable,
        "-m",
        "mini_codex",
        "--workspace",
        str(workspace),
    ]
    command.extend(extra_args)
    if permission.strip():
        command.extend(["--permission", permission.strip()])
    if dry_run:
        command.extend(["--dry-run", "--no-commands"])
    elif no_commands:
        command.append("--no-commands")
    if task.strip():
        command.append(task.strip())
    return command


def agent_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("NO_COLOR", "1")
    return env


def list_recent_transcripts(workspace: Path, *, limit: int = 20) -> list[Path]:
    run_dir = workspace / ".mini_codex" / "runs"
    if not run_dir.exists():
        return []
    paths = [path for path in run_dir.glob("*.md") if path.is_file()]
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class BrowserGuiState:
    def __init__(self, *, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.lock = threading.Lock()
        self.events: list[dict[str, object]] = []
        self.next_event_id = 1
        self.process: subprocess.Popen[str] | None = None
        self.started_at: float | None = None
        self.status = "Idle"
        self.config_summary = self._config_summary()

    def snapshot(self, *, after: int = 0) -> dict[str, object]:
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            elapsed = int(time.monotonic() - self.started_at) if self.started_at is not None else 0
            events = [event for event in self.events if int(event["id"]) > after]
            return {
                "workspace": str(self.workspace),
                "status": self.status,
                "running": running,
                "elapsed": elapsed,
                "config": self.config_summary,
                "permission_profiles": self._permission_profile_names(),
                "events": events,
            }

    def set_workspace(self, workspace: Path) -> dict[str, object]:
        with self.lock:
            if self._is_running_locked():
                return {"ok": False, "error": "A run is already active."}
            self.workspace = workspace.expanduser().resolve()
            self.config_summary = self._config_summary()
        self.add_event("system", f"Workspace changed: {self.workspace}\n")
        return {
            "ok": True,
            "workspace": str(self.workspace),
            "config": self.config_summary,
            "permission_profiles": self._permission_profile_names(),
        }

    def transcripts(self) -> list[dict[str, object]]:
        rows = []
        for path in list_recent_transcripts(self.workspace):
            stat = path.stat()
            rows.append({"name": path.name, "path": str(path), "mtime": stat.st_mtime})
        return rows

    def open_transcript(self, name: str) -> dict[str, object]:
        for path in list_recent_transcripts(self.workspace, limit=100):
            if path.name == name:
                try:
                    open_path(path)
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}
                return {"ok": True}
        return {"ok": False, "error": "Transcript not found."}

    def start_task(self, *, task: str, dry_run: bool, no_commands: bool, permission: str = "") -> dict[str, object]:
        task = task.strip()
        if not task:
            return {"ok": False, "error": "Task is empty."}
        command = build_agent_command(
            sys.executable,
            workspace=self.workspace,
            task=task,
            dry_run=dry_run,
            no_commands=no_commands,
            permission=permission,
        )
        self.add_event("user", task + "\n")
        return self._start_process(command, title="Run")

    def start_utility(self, name: str) -> dict[str, object]:
        if name == "config":
            args = ["--show-config"]
            title = "Config"
        elif name == "doctor":
            args = ["--doctor"]
            title = "Doctor"
        else:
            return {"ok": False, "error": "Unknown utility."}
        command = build_agent_command(sys.executable, workspace=self.workspace, extra_args=args)
        return self._start_process(command, title=title)

    def stop(self) -> dict[str, object]:
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                return {"ok": True}
        self.add_event("system", "Stopping current run...\n")
        process.terminate()
        threading.Timer(2.5, self._kill_if_still_running, args=(process,)).start()
        return {"ok": True}

    def shutdown(self) -> None:
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.terminate()

    def add_event(self, kind: str, text: str) -> None:
        with self.lock:
            self.events.append({"id": self.next_event_id, "kind": kind, "text": strip_ansi(text)})
            self.next_event_id += 1
            if len(self.events) > 5000:
                self.events = self.events[-2500:]

    def _start_process(self, command: list[str], *, title: str) -> dict[str, object]:
        with self.lock:
            if self._is_running_locked():
                return {"ok": False, "error": "A run is already active."}
            self.status = f"{title} running"
            self.started_at = time.monotonic()
            self.config_summary = self._config_summary()

        self.add_event("command", "$ " + _shell_join(command) + "\n")
        try:
            process = subprocess.Popen(
                command,
                cwd=self.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=agent_environment(),
            )
        except Exception as exc:
            with self.lock:
                self.status = "Failed"
                self.started_at = None
            self.add_event("error", f"Error: failed to start process: {exc}\n")
            return {"ok": False, "error": str(exc)}

        with self.lock:
            self.process = process
        threading.Thread(target=self._read_process_output, args=(process,), daemon=True).start()
        return {"ok": True}

    def _read_process_output(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self.add_event(_event_kind_for_line(line), line)
            returncode = process.wait()
        except Exception as exc:
            self.add_event("error", f"Error: output reader failed: {exc}\n")
            returncode = 1

        with self.lock:
            if self.process is process:
                self.process = None
                self.started_at = None
                self.status = "Idle" if returncode == 0 else "Failed"
                self.config_summary = self._config_summary()
        kind = "success" if returncode == 0 else "error"
        self.add_event(kind, f"Process finished with exit code {returncode}.\n")

    def _kill_if_still_running(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.kill()

    def _is_running_locked(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _config_summary(self) -> str:
        try:
            config = load_config(self.workspace)
        except Exception as exc:
            return f"Config issue: {exc}"
        base = "custom base URL" if config.base_url else "OpenAI default"
        return f"Model: {config.model}\nAPI mode: {config.api_mode}\n{base}"

    def _permission_profile_names(self) -> list[str]:
        try:
            return [""] + [profile.name for profile in list_permission_profiles(self.workspace)]
        except Exception:
            return [""]


class MiniCodexApp:
    def __init__(self, root, *, workspace: Path, initial_task: str = "") -> None:
        if tk is None or ttk is None:
            raise RuntimeError("tkinter is not available in this Python installation.")

        self.root = root
        self.workspace = workspace.resolve()
        self.events: queue.Queue[tuple[str, str | int]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.reader: threading.Thread | None = None
        self.started_at: float | None = None
        self.transcript_paths: list[Path] = []

        self.workspace_var = tk.StringVar(value=str(self.workspace))
        self.status_var = tk.StringVar(value="Idle")
        self.config_var = tk.StringVar(value="")
        self.elapsed_var = tk.StringVar(value="00:00")
        self.dry_run_var = tk.BooleanVar(value=False)
        self.no_commands_var = tk.BooleanVar(value=False)
        self.permission_var = tk.StringVar(value="")

        self._configure_root()
        self._build_layout()
        self._refresh_config_status()
        self._refresh_transcripts()
        self._set_running(False)

        if initial_task.strip():
            self.task_text.insert("1.0", initial_task.strip())
            self.root.after(250, self._start_task)

        self.root.after(50, self._pump_events)
        self.root.after(500, self._update_elapsed)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_root(self) -> None:
        self.root.title("mini-codex")
        self.root.geometry("1120x740")
        self.root.minsize(840, 560)
        self.root.configure(bg="#0e1116")

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background="#0e1116")
        style.configure("Sidebar.TFrame", background="#151923")
        style.configure("Panel.TFrame", background="#1a202c")
        style.configure("App.TLabel", background="#0e1116", foreground="#e6edf3")
        style.configure("Muted.TLabel", background="#151923", foreground="#9ca3af")
        style.configure("Sidebar.TLabel", background="#151923", foreground="#e6edf3")
        style.configure("Panel.TLabel", background="#1a202c", foreground="#e6edf3")
        style.configure("Status.TLabel", background="#1a202c", foreground="#7dd3fc")
        style.configure("App.TButton", padding=(12, 7), background="#2f3747", foreground="#e6edf3")
        style.map(
            "App.TButton",
            background=[("active", "#3b4558"), ("disabled", "#242b38")],
            foreground=[("disabled", "#747b87")],
        )
        style.configure("Primary.TButton", padding=(16, 8), background="#2563eb", foreground="#ffffff")
        style.map(
            "Primary.TButton",
            background=[("active", "#1d4ed8"), ("disabled", "#223454")],
            foreground=[("disabled", "#8fa6ce")],
        )
        style.configure("Danger.TButton", padding=(12, 7), background="#7f1d1d", foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", "#991b1b"), ("disabled", "#3a2427")])
        style.configure("App.TCheckbutton", background="#0e1116", foreground="#d1d5db")
        style.map(
            "App.TCheckbutton",
            background=[("active", "#0e1116")],
            foreground=[("disabled", "#747b87")],
        )
        style.configure(
            "App.Vertical.TScrollbar",
            background="#2f3747",
            troughcolor="#111827",
            bordercolor="#111827",
            arrowcolor="#cbd5e1",
        )

    def _build_layout(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame")
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(shell, style="Sidebar.TFrame", padding=(16, 16))
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(5, weight=1)

        title = ttk.Label(sidebar, text="mini-codex", style="Sidebar.TLabel", font=("Segoe UI", 17, "bold"))
        title.grid(row=0, column=0, sticky="w")
        subtitle = ttk.Label(sidebar, text="Local coding agent", style="Muted.TLabel")
        subtitle.grid(row=1, column=0, sticky="w", pady=(2, 16))

        status_panel = ttk.Frame(sidebar, style="Panel.TFrame", padding=12)
        status_panel.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        status_panel.columnconfigure(0, weight=1)
        ttk.Label(status_panel, textvariable=self.status_var, style="Status.TLabel", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(status_panel, textvariable=self.elapsed_var, style="Panel.TLabel").grid(row=0, column=1, sticky="e")
        ttk.Label(status_panel, textvariable=self.config_var, style="Panel.TLabel", wraplength=210).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        ttk.Label(sidebar, text="Workspace", style="Sidebar.TLabel", font=("Segoe UI", 10, "bold")).grid(
            row=3, column=0, sticky="w", pady=(0, 6)
        )
        workspace_box = ttk.Frame(sidebar, style="Sidebar.TFrame")
        workspace_box.grid(row=4, column=0, sticky="ew", pady=(0, 14))
        workspace_box.columnconfigure(0, weight=1)
        self.workspace_entry = ttk.Entry(workspace_box, textvariable=self.workspace_var)
        self.workspace_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(workspace_box, text="Browse", style="App.TButton", command=self._browse_workspace).grid(
            row=1, column=0, sticky="ew", pady=(8, 0)
        )

        transcript_panel = ttk.Frame(sidebar, style="Sidebar.TFrame")
        transcript_panel.grid(row=5, column=0, sticky="nsew")
        transcript_panel.columnconfigure(0, weight=1)
        transcript_panel.rowconfigure(1, weight=1)
        ttk.Label(transcript_panel, text="Recent Runs", style="Sidebar.TLabel", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.transcript_list = tk.Listbox(
            transcript_panel,
            bg="#10151f",
            fg="#d1d5db",
            selectbackground="#2563eb",
            selectforeground="#ffffff",
            highlightthickness=0,
            relief="flat",
            activestyle="none",
            height=8,
        )
        self.transcript_list.grid(row=1, column=0, sticky="nsew")
        self.transcript_list.bind("<Double-Button-1>", lambda _event: self._open_selected_transcript())
        transcript_buttons = ttk.Frame(transcript_panel, style="Sidebar.TFrame")
        transcript_buttons.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        transcript_buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(transcript_buttons, text="Open", style="App.TButton", command=self._open_selected_transcript).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(transcript_buttons, text="Refresh", style="App.TButton", command=self._refresh_transcripts).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        utility = ttk.Frame(sidebar, style="Sidebar.TFrame")
        utility.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        utility.columnconfigure((0, 1), weight=1)
        self.config_button = ttk.Button(
            utility,
            text="Config",
            style="App.TButton",
            command=lambda: self._start_utility(["--show-config"], "Config"),
        )
        self.config_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.doctor_button = ttk.Button(
            utility,
            text="Doctor",
            style="App.TButton",
            command=lambda: self._start_utility(["--doctor"], "Doctor"),
        )
        self.doctor_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        main = ttk.Frame(shell, style="App.TFrame", padding=(18, 16))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        log_frame = ttk.Frame(main, style="App.TFrame")
        log_frame.grid(row=0, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(
            log_frame,
            bg="#0b0f14",
            fg="#dbe4ee",
            insertbackground="#e6edf3",
            selectbackground="#243b60",
            relief="flat",
            wrap="word",
            padx=16,
            pady=14,
            font=("Consolas", 10),
            state="disabled",
            undo=False,
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, style="App.Vertical.TScrollbar", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.tag_configure("system", foreground="#8ea0b8")
        self.log.tag_configure("activity", foreground="#67e8f9")
        self.log.tag_configure("error", foreground="#fca5a5")
        self.log.tag_configure("warning", foreground="#fde68a")
        self.log.tag_configure("success", foreground="#86efac")
        self.log.tag_configure("command", foreground="#c4b5fd")
        self.log.tag_configure("user_label", foreground="#93c5fd", spacing1=8)
        self.log.tag_configure("user", foreground="#e6edf3", lmargin1=18, lmargin2=18, spacing3=10)

        composer = ttk.Frame(main, style="App.TFrame")
        composer.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        composer.columnconfigure(0, weight=1)
        composer.rowconfigure(0, weight=1)
        self.task_text = tk.Text(
            composer,
            height=4,
            bg="#151923",
            fg="#f8fafc",
            insertbackground="#ffffff",
            selectbackground="#1d4ed8",
            relief="flat",
            wrap="word",
            padx=12,
            pady=10,
            font=("Segoe UI", 10),
        )
        self.task_text.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.task_text.bind("<Control-Return>", lambda _event: self._start_task())

        options = ttk.Frame(composer, style="App.TFrame")
        options.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.dry_check = ttk.Checkbutton(
            options,
            text="Dry run",
            variable=self.dry_run_var,
            style="App.TCheckbutton",
        )
        self.dry_check.grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.no_commands_check = ttk.Checkbutton(
            options,
            text="No commands",
            variable=self.no_commands_var,
            style="App.TCheckbutton",
        )
        self.no_commands_check.grid(row=0, column=1, sticky="w")
        ttk.Label(options, text="Permission", style="App.TLabel").grid(row=0, column=2, sticky="w", padx=(14, 6))
        self.permission_combo = ttk.Combobox(
            options,
            textvariable=self.permission_var,
            values=self._permission_values(),
            width=18,
            state="readonly",
        )
        self.permission_combo.grid(row=0, column=3, sticky="w")

        actions = ttk.Frame(composer, style="App.TFrame")
        actions.grid(row=1, column=3, sticky="e", pady=(10, 0))
        self.clear_button = ttk.Button(actions, text="Clear", style="App.TButton", command=self._clear_log)
        self.clear_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="Stop", style="Danger.TButton", command=self._stop_process)
        self.stop_button.grid(row=0, column=1, padx=(0, 8))
        self.run_button = ttk.Button(actions, text="Run", style="Primary.TButton", command=self._start_task)
        self.run_button.grid(row=0, column=2)

        self._append_system("mini-codex GUI ready.\n")

    def _browse_workspace(self) -> None:
        if filedialog is None:
            return
        selected = filedialog.askdirectory(initialdir=str(self.workspace))
        if not selected:
            return
        self.workspace = Path(selected).resolve()
        self.workspace_var.set(str(self.workspace))
        self._refresh_config_status()
        self._refresh_transcripts()
        self._append_system(f"Workspace changed: {self.workspace}\n")

    def _refresh_config_status(self) -> None:
        self.workspace = Path(self.workspace_var.get()).expanduser().resolve()
        try:
            config = load_config(self.workspace)
        except Exception as exc:
            self.config_var.set(f"Config issue: {exc}")
            return
        base = "custom base URL" if config.base_url else "OpenAI default"
        self.config_var.set(f"Model: {config.model}\nAPI mode: {config.api_mode}\n{base}")
        if hasattr(self, "permission_combo"):
            values = self._permission_values()
            self.permission_combo.configure(values=values)
            if self.permission_var.get() not in values:
                self.permission_var.set("")

    def _refresh_transcripts(self) -> None:
        self.workspace = Path(self.workspace_var.get()).expanduser().resolve()
        self.transcript_paths = list_recent_transcripts(self.workspace)
        self.transcript_list.delete(0, "end")
        for path in self.transcript_paths:
            self.transcript_list.insert("end", path.name)

    def _open_selected_transcript(self) -> None:
        selection = self.transcript_list.curselection()
        if not selection:
            return
        path = self.transcript_paths[selection[0]]
        self._open_path(path)

    def _open_path(self, path: Path) -> None:
        try:
            open_path(path)
        except Exception as exc:
            self._append_line(f"Error: could not open {path}: {exc}\n", "error")

    def _start_task(self) -> None:
        task = self.task_text.get("1.0", "end").strip()
        if not task:
            return
        command = build_agent_command(
            sys.executable,
            workspace=self._current_workspace(),
            task=task,
            dry_run=self.dry_run_var.get(),
            no_commands=self.no_commands_var.get(),
            permission=self.permission_var.get(),
        )
        self._append_user_task(task)
        self._start_process(command, title="Run")

    def _start_utility(self, args: Sequence[str], title: str) -> None:
        command = build_agent_command(sys.executable, workspace=self._current_workspace(), extra_args=args)
        self._start_process(command, title=title)

    def _start_process(self, command: list[str], *, title: str) -> None:
        if self.process is not None and self.process.poll() is None:
            self._append_system("A run is already active.\n")
            return

        workspace = self._current_workspace()
        self._refresh_config_status()
        self._set_running(True)
        self.started_at = time.monotonic()
        self.status_var.set(f"{title} running")
        self._append_line("$ " + _shell_join(command) + "\n", "command")

        try:
            self.process = subprocess.Popen(
                command,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=agent_environment(),
            )
        except Exception as exc:
            self._append_line(f"Error: failed to start process: {exc}\n", "error")
            self._finish_run(1)
            return

        self.reader = threading.Thread(target=self._read_process_output, args=(self.process,), daemon=True)
        self.reader.start()

    def _read_process_output(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self.events.put(("line", line))
            returncode = process.wait()
            self.events.put(("exit", returncode))
        except Exception as exc:
            self.events.put(("line", f"Error: output reader failed: {exc}\n"))
            self.events.put(("exit", 1))

    def _stop_process(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self._append_system("Stopping current run...\n")
        self.process.terminate()
        self.root.after(2500, self._kill_if_still_running)

    def _kill_if_still_running(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()

    def _pump_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                line = strip_ansi(str(payload))
                self._append_line(line, self._tag_for_line(line))
            elif kind == "exit":
                self._finish_run(int(payload))
        self.root.after(50, self._pump_events)

    def _finish_run(self, returncode: int) -> None:
        if returncode == 0:
            self.status_var.set("Idle")
            self._append_line(f"Process finished with exit code {returncode}.\n", "success")
        else:
            self.status_var.set("Failed")
            self._append_line(f"Process finished with exit code {returncode}.\n", "error")
        self.process = None
        self.started_at = None
        self._set_running(False)
        self._refresh_transcripts()

    def _update_elapsed(self) -> None:
        if self.started_at is not None:
            elapsed = int(time.monotonic() - self.started_at)
            minutes, seconds = divmod(elapsed, 60)
            self.elapsed_var.set(f"{minutes:02d}:{seconds:02d}")
        self.root.after(500, self._update_elapsed)

    def _set_running(self, running: bool) -> None:
        normal = "normal"
        disabled = "disabled"
        self.run_button.configure(state=disabled if running else normal)
        self.config_button.configure(state=disabled if running else normal)
        self.doctor_button.configure(state=disabled if running else normal)
        self.stop_button.configure(state=normal if running else disabled)
        self.clear_button.configure(state=disabled if running else normal)
        self.dry_check.configure(state=disabled if running else normal)
        self.no_commands_check.configure(state=disabled if running else normal)
        self.permission_combo.configure(state=disabled if running else "readonly")

    def _append_user_task(self, task: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", "\nYou\n", "user_label")
        self.log.insert("end", task + "\n", "user")
        self.log.configure(state="disabled")
        self.log.see("end")

    def _append_system(self, text: str) -> None:
        self._append_line(text, "system")

    def _append_line(self, text: str, tag: str = "system") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        if not text.endswith("\n"):
            self.log.insert("end", "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _tag_for_line(self, line: str) -> str:
        return _event_kind_for_line(line)

    def _current_workspace(self) -> Path:
        self.workspace = Path(self.workspace_var.get()).expanduser().resolve()
        return self.workspace

    def _permission_values(self) -> list[str]:
        try:
            names = [profile.name for profile in list_permission_profiles(self._current_workspace())]
        except Exception:
            names = []
        return [""] + names

    def _on_close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self.root.destroy()


def _shell_join(command: Sequence[str]) -> str:
    if hasattr(subprocess, "list2cmdline") and sys.platform.startswith("win"):
        return subprocess.list2cmdline(list(command))
    import shlex

    return " ".join(shlex.quote(part) for part in command)


def _event_kind_for_line(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("Error:"):
        return "error"
    if stripped.startswith("Warning:"):
        return "warning"
    if stripped.startswith("$ "):
        return "command"
    if "[activity]" in stripped:
        return "activity"
    if "result: passed" in stripped or "Finished: Task completed" in stripped:
        return "success"
    return "system"


def open_path(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def run_gui(*, workspace: Path | None = None, initial_task: str = "") -> int:
    if tk is None:
        return run_browser_gui(workspace=workspace, initial_task=initial_task)
    root = tk.Tk()
    MiniCodexApp(root, workspace=workspace or Path.cwd(), initial_task=initial_task)
    root.mainloop()
    return 0


class BrowserGuiHandler(BaseHTTPRequestHandler):
    server_version = "MiniCodexGUI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(BROWSER_HTML)
            return
        if parsed.path == "/state":
            after = _int_query(parsed.query, "after", default=0)
            self._send_json(self.state.snapshot(after=after))
            return
        if parsed.path == "/transcripts":
            self._send_json({"items": self.state.transcripts()})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json()
        if parsed.path == "/run":
            self._send_json(
                self.state.start_task(
                    task=str(payload.get("task", "")),
                    dry_run=bool(payload.get("dry_run")),
                    no_commands=bool(payload.get("no_commands")),
                    permission=str(payload.get("permission", "")),
                )
            )
            return
        if parsed.path == "/utility":
            self._send_json(self.state.start_utility(str(payload.get("name", ""))))
            return
        if parsed.path == "/workspace":
            self._send_json(self.state.set_workspace(Path(str(payload.get("path", "")))))
            return
        if parsed.path == "/open-transcript":
            self._send_json(self.state.open_transcript(str(payload.get("name", ""))))
            return
        if parsed.path == "/stop":
            self._send_json(self.state.stop())
            return
        if parsed.path == "/shutdown":
            self._send_json({"ok": True})
            self.state.shutdown()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.send_error(404)

    @property
    def state(self) -> BrowserGuiState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args) -> None:
        return

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            value = loads(raw)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def _send_json(self, payload: dict[str, object]) -> None:
        data = dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _int_query(query: str, key: str, *, default: int) -> int:
    values = parse_qs(query).get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        return default


def run_browser_gui(*, workspace: Path | None = None, initial_task: str = "") -> int:
    state = BrowserGuiState(workspace=workspace or Path.cwd())
    server = ThreadingHTTPServer(("127.0.0.1", 0), BrowserGuiHandler)
    server.state = state  # type: ignore[attr-defined]
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"mini-codex GUI: {url}", flush=True)
    state.add_event("system", "mini-codex browser GUI ready.\n")
    if initial_task.strip():
        state.start_task(task=initial_task, dry_run=False, no_commands=False)
    if os.getenv("MINI_CODEX_NO_BROWSER") != "1":
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown()
        server.server_close()
    return 0


BROWSER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mini-codex</title>
<style>
:root {
  color-scheme: dark;
  --bg: #0e1116;
  --panel: #151923;
  --panel-2: #1a202c;
  --line: #2b3444;
  --text: #e6edf3;
  --muted: #9ca3af;
  --blue: #2563eb;
  --cyan: #67e8f9;
  --green: #86efac;
  --red: #fca5a5;
  --yellow: #fde68a;
  --violet: #c4b5fd;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font-family: Segoe UI, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  letter-spacing: 0;
}
button, input, select, textarea { font: inherit; }
.app {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  min-height: 100vh;
}
.sidebar {
  background: var(--panel);
  border-right: 1px solid #242c3a;
  padding: 18px;
  display: grid;
  grid-template-rows: auto auto auto auto 1fr auto;
  gap: 14px;
}
.brand h1 {
  font-size: 23px;
  line-height: 1.1;
  margin: 0;
}
.brand p, .label, .muted {
  color: var(--muted);
}
.brand p {
  margin: 5px 0 0;
  font-size: 13px;
}
.status-card {
  background: var(--panel-2);
  border: 1px solid #263143;
  border-radius: 8px;
  padding: 12px;
}
.status-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--cyan);
  font-weight: 650;
}
.config {
  margin-top: 10px;
  white-space: pre-wrap;
  color: #cbd5e1;
  font-size: 13px;
}
.field {
  display: grid;
  gap: 7px;
}
.field input, select {
  width: 100%;
  border: 1px solid var(--line);
  background: #10151f;
  color: var(--text);
  border-radius: 7px;
  padding: 9px 10px;
}
.transcripts {
  min-height: 0;
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 8px;
}
.transcript-list {
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #10151f;
}
.transcript-item {
  width: 100%;
  padding: 9px 10px;
  border: 0;
  border-bottom: 1px solid #202838;
  background: transparent;
  color: #d1d5db;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border-radius: 0;
}
.transcript-item:hover {
  background: #1d2736;
}
.main {
  min-width: 0;
  padding: 18px;
  display: grid;
  grid-template-rows: 1fr auto;
  gap: 14px;
}
.log {
  overflow: auto;
  background: #0b0f14;
  border: 1px solid #202838;
  border-radius: 8px;
  padding: 16px;
  font-family: Consolas, ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
}
.event { margin: 0; }
.event.user {
  margin: 12px 0;
  padding: 11px 12px;
  border-left: 3px solid #93c5fd;
  background: #101827;
  border-radius: 0 7px 7px 0;
  color: #f8fafc;
}
.event.activity { color: var(--cyan); }
.event.error { color: var(--red); }
.event.warning { color: var(--yellow); }
.event.success { color: var(--green); }
.event.command { color: var(--violet); }
.event.system { color: #a9b6c7; }
.composer {
  display: grid;
  gap: 10px;
}
textarea {
  width: 100%;
  min-height: 96px;
  resize: vertical;
  border: 1px solid var(--line);
  background: var(--panel);
  color: #f8fafc;
  border-radius: 8px;
  padding: 12px;
}
.controls, .left-controls, .right-controls, .side-buttons {
  display: flex;
  gap: 8px;
  align-items: center;
}
.controls {
  justify-content: space-between;
  flex-wrap: wrap;
}
label.check {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  color: #d1d5db;
}
.permission-control {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  color: #d1d5db;
}
.permission-control select {
  width: 170px;
}
button {
  border: 1px solid #354052;
  background: #2f3747;
  color: #e6edf3;
  border-radius: 7px;
  padding: 8px 12px;
  cursor: pointer;
}
button:hover { background: #3b4558; }
button:disabled {
  cursor: not-allowed;
  color: #747b87;
  background: #242b38;
}
button.primary {
  border-color: #1d4ed8;
  background: var(--blue);
  color: white;
  min-width: 84px;
}
button.primary:hover { background: #1d4ed8; }
button.danger {
  border-color: #7f1d1d;
  background: #7f1d1d;
  color: white;
}
@media (max-width: 820px) {
  .app { grid-template-columns: 1fr; }
  .sidebar {
    border-right: 0;
    border-bottom: 1px solid #242c3a;
    grid-template-rows: auto;
  }
  .transcripts { min-height: 180px; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <h1>mini-codex</h1>
      <p>Local coding agent</p>
    </div>
    <section class="status-card">
      <div class="status-row"><span id="status">Idle</span><span id="elapsed">00:00</span></div>
      <div class="config" id="config"></div>
    </section>
    <div class="field">
      <div class="label">Workspace</div>
      <input id="workspace" spellcheck="false">
      <div class="side-buttons">
        <button id="workspaceApply">Apply</button>
        <button id="refresh">Refresh</button>
      </div>
    </div>
    <div class="transcripts">
      <div class="label">Recent Runs</div>
      <div class="transcript-list" id="transcripts"></div>
      <div class="side-buttons">
        <button id="configButton">Config</button>
        <button id="doctorButton">Doctor</button>
      </div>
    </div>
    <button id="quitButton">Quit</button>
  </aside>
  <main class="main">
    <section id="log" class="log" aria-live="polite"></section>
    <section class="composer">
      <textarea id="task" spellcheck="false"></textarea>
      <div class="controls">
        <div class="left-controls">
          <label class="check"><input type="checkbox" id="dryRun"> Dry run</label>
          <label class="check"><input type="checkbox" id="noCommands"> No commands</label>
          <label class="permission-control">Permission <select id="permission"></select></label>
        </div>
        <div class="right-controls">
          <button id="clearButton">Clear</button>
          <button id="stopButton" class="danger">Stop</button>
          <button id="runButton" class="primary">Run</button>
        </div>
      </div>
    </section>
  </main>
</div>
<script>
let lastEvent = 0;
let running = false;
const log = document.getElementById("log");
const task = document.getElementById("task");
const workspace = document.getElementById("workspace");
const statusText = document.getElementById("status");
const elapsedText = document.getElementById("elapsed");
const configText = document.getElementById("config");
const transcripts = document.getElementById("transcripts");
const runButton = document.getElementById("runButton");
const stopButton = document.getElementById("stopButton");
const clearButton = document.getElementById("clearButton");
const configButton = document.getElementById("configButton");
const doctorButton = document.getElementById("doctorButton");
const dryRun = document.getElementById("dryRun");
const noCommands = document.getElementById("noCommands");
const permission = document.getElementById("permission");
let permissionOptionsKey = "";

function fmtElapsed(total) {
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
}

function appendEvent(event) {
  const item = document.createElement("pre");
  item.className = "event " + event.kind;
  item.textContent = event.text;
  log.appendChild(item);
  log.scrollTop = log.scrollHeight;
}

function setRunning(value) {
  running = value;
  runButton.disabled = value;
  configButton.disabled = value;
  doctorButton.disabled = value;
  stopButton.disabled = !value;
  clearButton.disabled = value;
  dryRun.disabled = value;
  noCommands.disabled = value;
  permission.disabled = value;
}

function updatePermissionProfiles(names) {
  const values = Array.isArray(names) && names.length ? names : [""];
  const key = values.join("\u0000");
  if (key === permissionOptionsKey) return;
  const current = permission.value;
  permission.textContent = "";
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value || "default";
    permission.appendChild(option);
  }
  permission.value = values.includes(current) ? current : "";
  permissionOptionsKey = key;
}

async function post(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  return await response.json();
}

async function pollState() {
  try {
    const state = await fetch("/state?after=" + lastEvent).then(r => r.json());
    workspace.value = state.workspace;
    statusText.textContent = state.status;
    elapsedText.textContent = fmtElapsed(state.elapsed || 0);
    configText.textContent = state.config || "";
    updatePermissionProfiles(state.permission_profiles);
    setRunning(Boolean(state.running));
    for (const event of state.events) {
      appendEvent(event);
      lastEvent = Math.max(lastEvent, event.id);
    }
  } catch (error) {
    statusText.textContent = "Disconnected";
  }
}

async function loadTranscripts() {
  const data = await fetch("/transcripts").then(r => r.json());
  transcripts.textContent = "";
  for (const item of data.items) {
    const button = document.createElement("button");
    button.className = "transcript-item";
    button.textContent = item.name;
    button.title = item.path;
    button.addEventListener("click", () => post("/open-transcript", {name: item.name}));
    transcripts.appendChild(button);
  }
}

runButton.addEventListener("click", async () => {
  const result = await post("/run", {
    task: task.value,
    dry_run: dryRun.checked,
    no_commands: noCommands.checked,
    permission: permission.value
  });
  if (!result.ok && result.error) appendEvent({kind: "error", text: "Error: " + result.error + "\n"});
});
stopButton.addEventListener("click", () => post("/stop"));
clearButton.addEventListener("click", () => { log.textContent = ""; });
configButton.addEventListener("click", () => post("/utility", {name: "config"}));
doctorButton.addEventListener("click", () => post("/utility", {name: "doctor"}));
document.getElementById("workspaceApply").addEventListener("click", async () => {
  const result = await post("/workspace", {path: workspace.value});
  if (!result.ok && result.error) appendEvent({kind: "error", text: "Error: " + result.error + "\n"});
  await pollState();
  await loadTranscripts();
});
document.getElementById("refresh").addEventListener("click", loadTranscripts);
document.getElementById("quitButton").addEventListener("click", async () => {
  await post("/shutdown");
  document.body.innerHTML = "<main class='main'><section class='log'>mini-codex GUI closed.</section></main>";
});
task.addEventListener("keydown", event => {
  if (event.ctrlKey && event.key === "Enter") {
    event.preventDefault();
    runButton.click();
  }
});

setRunning(false);
pollState();
loadTranscripts();
setInterval(pollState, 250);
setInterval(loadTranscripts, 2000);
</script>
</body>
</html>
"""


def main() -> int:
    return run_gui(workspace=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main())
