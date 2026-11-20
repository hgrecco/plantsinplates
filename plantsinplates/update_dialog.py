"""The desktop application's explicit GitHub update action."""

import json
from pathlib import Path
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable
import webbrowser

import ttkbootstrap as ttk

from . import updates
from .version import __version__


class UpdateDialog(ttk.Toplevel):
    """Keep network and installation preparation off the Tk thread."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        can_install: Callable[[], bool],
        on_restart: Callable[[], None],
        data_folder: str,
    ):
        super().__init__(parent)
        self.title("Preferences")
        self.transient(parent)
        self.resizable(True, False)
        self.minsize(480, 0)
        self.root_path = updates.application_root()
        self.can_install = can_install
        self.on_restart = on_restart
        self.data_folder = data_folder
        self.release: updates.Release | None = None
        self.stage: Path | None = None
        self.working = False
        self.restarting = False
        self.cancel_event = threading.Event()
        self.events: Queue[tuple[str, object]] = Queue()
        body = ttk.Frame(self, padding=24)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Application updates", style="DialogTitle.TLabel").pack(
            anchor=tk.W
        )
        ttk.Label(body, text=f"Installed version: {__version__}", padding=(0, 8)).pack(
            anchor=tk.W
        )
        helper_label = ttk.Label(
            body,
            text="Analysis preferences are configured within each workflow step.",
            wraplength=480,
            style="Helper.TLabel",
        )
        helper_label.pack(anchor=tk.W)
        self.status = tk.StringVar(value="Check GitHub for a newer stable release.")
        status_label = ttk.Label(
            body,
            textvariable=self.status,
            wraplength=480,
            padding=(0, 16),
            justify=tk.LEFT,
        )
        status_label.pack(fill=tk.X)
        body.bind(
            "<Configure>",
            lambda event: self._wrap_text(event.width, (helper_label, status_label)),
        )
        self.progress = ttk.Progressbar(body, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(0, 16))
        self.progress.pack_forget()
        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X)
        self.check_button = ttk.Button(
            buttons, text="Check for updates", command=self.check
        )
        self.check_button.pack(side=tk.LEFT)
        self.notes_button = ttk.Button(
            buttons, text="Release notes", bootstyle="link", command=self.open_notes
        )
        self.notes_button.pack(side=tk.LEFT, padx=8)
        self.notes_button.configure(state="disabled")
        self.install_button = ttk.Button(
            body, text="Update and restart", command=self.install, state="disabled"
        )
        self.install_button.pack(anchor=tk.W, pady=(16, 0))
        self.close_button = ttk.Button(
            body, text="Close", bootstyle="secondary", command=self.close
        )
        self.close_button.pack(anchor=tk.E, pady=(16, 0))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.grab_set()
        self.poll_job = self.after(100, self.poll)

    @staticmethod
    def _wrap_text(width: int, labels: tuple[ttk.Label, ...]) -> None:
        for label in labels:
            label.configure(wraplength=max(240, width - 48))

    def _busy(self, value: bool) -> None:
        self.working = value
        self.check_button.configure(state="disabled" if value else "normal")
        self.close_button.configure(text="Cancel" if value else "Close")
        if value:
            self.progress.pack(fill=tk.X, pady=(0, 16), before=self.close_button)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _worker(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as error:
            self.events.put(("error", str(error)))

    def check(self) -> None:
        if self.working:
            return
        self.release = None
        self.cancel_event.clear()
        self._busy(True)
        self.status.set("Checking GitHub…")

        def action() -> None:
            self.events.put(("release", updates.check_for_update(__version__)))

        threading.Thread(target=self._worker, args=(action,), daemon=True).start()

    def install(self) -> None:
        if self.working or self.release is None or not self.can_install():
            return
        if updates.is_development_checkout(self.root_path):
            return
        self.cancel_event.clear()
        self.stage = None
        self._busy(True)
        release = self.release

        def report(message: str) -> None:
            self.events.put(("progress", message))

        def action() -> None:
            self.stage = updates.stage_update(
                self.root_path, release, cancel=self.cancel_event, report=report
            )
            report("Preparing the updater. The application will close and reopen…")
            updates.prepare_updater(self.root_path, cancel=self.cancel_event)
            updates.launch_installer(
                self.root_path,
                self.stage,
                restart_folder=self.data_folder,
                cancel=self.cancel_event,
            )
            self.events.put(("ready", None))

        threading.Thread(target=self._worker, args=(action,), daemon=True).start()

    def open_notes(self) -> None:
        if self.release:
            webbrowser.open_new_tab(self.release.notes_url)

    def poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(str(value))
                elif kind == "release":
                    self._busy(False)
                    self.release = value
                    self.notes_button.configure(state="normal" if value else "disabled")
                    self.status.set(
                        f"Version {value.version} is available. Review its release notes before updating."
                        if value
                        else "No newer stable release is available."
                    )
                elif kind == "error":
                    self._busy(False)
                    self.status.set(str(value))
                elif kind == "ready":
                    if not self.can_install():
                        self.cancel_before_close()
                        self._busy(False)
                        self.status.set(
                            "A folder operation started. Wait for it to finish, then retry the update."
                        )
                        continue
                    self.restarting = True
                    self.destroy()
                    self.on_restart()
                    return
        except Empty:
            pass
        available = self.release is not None and not self.working
        if available and updates.is_development_checkout(self.root_path):
            self.status.set(
                f"Version {self.release.version} is available. This is a development checkout; update it with Git or install the release ZIP separately."
            )
            available = False
        elif available and not self.can_install():
            self.status.set(
                "An update is available. Wait for analysis and other folder operations to finish before installing."
            )
            available = False
        self.install_button.configure(state="normal" if available else "disabled")
        self.poll_job = self.after(100, self.poll)

    def cancel_before_close(self) -> None:
        if not self.restarting:
            self.cancel_event.set()
            if self.stage is not None:
                (self.stage / "cancel").touch()

    def close(self) -> None:
        self.cancel_before_close()
        self.destroy()

    def destroy(self) -> None:
        self.after_cancel(self.poll_job)
        super().destroy()


def show_update_result(parent: tk.Misc) -> None:
    """Display the detached helper's result once after a restart."""
    path = updates.application_root() / updates.UPDATE_DIRECTORY / "result.json"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
    except (OSError, ValueError):
        return
    show = messagebox.showinfo if result.get("success") else messagebox.showerror
    show(
        "Application update",
        result.get("message", "See the application update log."),
        parent=parent,
    )
