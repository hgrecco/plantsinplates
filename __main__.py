import logging
import math
import os
import pathlib
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, scrolledtext
import tkinter.font as tkfont
from typing import Annotated, Callable, Literal
import warnings

import ttkbootstrap as ttk
import typer

from plantsinplates import analyze, io as pipio, workflow
from plantsinplates.measurement_config import MeasurementConfig
from plantsinplates.workflow_chrome import (
    ApplicationFooter,
    StatusKind,
    StepDefinition,
    StepState,
    WorkflowSidebar,
    load_application_icons,
)

warnings.filterwarnings(
    "ignore", message=".*The color list has more values.*", category=UserWarning
)

TITLE = f"Plants in Plates ({pipio.__version__})"
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 16
SPACE_LG = 24
METHODS = {
    "box": {
        "name": "Tip box",
        "summary": "Fast local measurement around the root tip",
        "recommended": True,
    },
    "centerline": {
        "name": "Centerline profile",
        "summary": "Measures signal along the root shape",
        "recommended": False,
    },
    "centerline_gaussian": {
        "name": "Gaussian centerline profile",
        "summary": "Fits smooth intensity profiles for noisier data",
        "recommended": False,
    },
}
WORKFLOW_STEPS = (
    StepDefinition("folder", 1, "Data folder", "Choose input data", "folder"),
    StepDefinition("calibration", 2, "Calibration", "Choose a source", "calibration"),
    StepDefinition("method", 3, "Method", "Measurement settings", "method"),
    StepDefinition("analysis", 4, "Run analysis", "Configure and execute", "analysis"),
    StepDefinition("results", 5, "Results", "View outputs and reports", "results"),
)
WORKFLOW_INDEX = {step.key: index for index, step in enumerate(WORKFLOW_STEPS)}


FolderValidation = workflow.FolderValidation
folder_kind = workflow.folder_kind
validate_plate_folder = workflow.validate_plate_folder
validate_folder = workflow.validate_folder


def middle_ellipsis(value: str, max_length: int = 82) -> str:
    """Keep both useful ends of long paths visible in a compact field."""
    if len(value) <= max_length:
        return value
    half = (max_length - 1) // 2
    return value[:half] + "…" + value[-half:]


def list_output_artifacts(folder: pathlib.Path) -> list[pathlib.Path]:
    return sorted(folder.rglob(f"{pipio.PREFIX}*"), key=lambda path: str(path))


def output_paths(folder: pathlib.Path) -> dict[str, pathlib.Path]:
    return {
        "Excel summary": pipio.build_summary_excel_path(folder),
        "PDF summary": pipio.build_summary_pdf_path(folder),
        "Data table": pipio.build_dataframe_path(folder),
    }


def completion_feedback(
    success: bool, error_count: int, message: str | None = None
) -> tuple[str, Literal["success", "warning", "error"]]:
    """Build the concise completion state shown after a run."""
    if message:
        return message, "success" if success else "error"
    if success:
        return "Analysis completed successfully.", "success"
    return (
        f"Analysis completed with warnings — {error_count} image or processing error"
        f"{'s' if error_count != 1 else ''} were recorded. See Technical log for details.",
        "warning",
    )


def validation_tooltip_text(validation: FolderValidation) -> str:
    """Give the folder status a concise summary plus every detected cause."""
    if not validation.details:
        return validation.message
    return validation.message + "\n\nDetails:\n• " + "\n• ".join(validation.details)


def validation_display_text(validation: FolderValidation) -> str:
    """Show only the concise result and point to the detailed tooltip."""
    if not validation.details:
        return validation.message
    return validation.message + " (hover for more information)"


class RelativeTimeFormatter(logging.Formatter):
    started = 0.0

    def format(self, record: logging.LogRecord) -> str:
        total_seconds = record.created - self.started
        hours, remainder = divmod(max(total_seconds, 0), 3600)
        minutes, seconds = divmod(remainder, 60)
        record.relative_hms = f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"
        return super().format(record)


class TextHandler(logging.Handler):
    """Forward worker logs to the Tk event queue and update progress wording."""

    def __init__(self, app: "AnalyzeApp"):
        super().__init__()
        self.app = app
        self.error_count = 0

    def reset(self) -> None:
        self.error_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        if record.levelno >= logging.ERROR:
            self.error_count += 1
        self.app.after(0, self.app.append_log, message, record.getMessage())


class ToolTip:
    """Small hover/focus tooltip used for detailed validation feedback."""

    def __init__(self, widget: tk.Widget, text: str = ""):
        self.widget = widget
        self.text = text
        self.window: ttk.Toplevel | None = None
        for event in ("<Enter>", "<FocusIn>"):
            widget.bind(event, self.show, add=True)
        for event in ("<Leave>", "<FocusOut>", "<ButtonPress>"):
            widget.bind(event, self.hide, add=True)

    def set_text(self, text: str) -> None:
        self.text = text
        if self.window is not None:
            self.hide()

    def show(self, _event: tk.Event | None = None) -> None:
        if self.window is not None or not self.text:
            return
        self.window = ttk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.attributes("-topmost", True)
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window.wm_geometry(f"+{x}+{y}")
        ttk.Label(
            self.window,
            text=self.text,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=680,
            padding=SPACE_SM,
            bootstyle="inverse-dark",
        ).pack()

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


class AnalyzeApp(ttk.Window):
    """Five-stage desktop workflow for reproducible analysis runs."""

    is_busy: bool = False

    def __init__(
        self,
        data_dir: str,
        measurement_config: MeasurementConfig,
        reuse_artifacts: bool = True,
    ):
        super().__init__(title=TITLE, themename="litera", iconphoto=None)
        self.app_icons = load_application_icons(self)
        self.iconphoto(True, *self.app_icons)
        self.geometry("1200x820")
        self.minsize(900, 620)
        self.data_dir = data_dir
        self.default_measurement_config = measurement_config
        self.config_inputs_enabled = True
        self.current_step_index = 0
        self.furthest_step_index = 0
        self.last_run_status: str | None = None
        self.analysis_ready = False
        self.selected_folder: pathlib.Path | None = None
        self.folder_validation = workflow.validate_folder(None)
        self.validation_generation = 0
        self.path_validation_after: str | None = None
        self.previous_runs: list[workflow.RunRecord] = []
        self.selected_run: workflow.RunRecord | None = None
        self.audit_readings: list[workflow.CalibrationReading] = []
        self.audit_results: dict[str, workflow.CalibrationReading] = {}
        self.audit_in_progress = False
        self.audit_generation = 0
        self.audit_cancel_event = threading.Event()
        self.run_cancel_event = threading.Event()
        self.run_started_at: float | None = None
        self.current_run_dir: pathlib.Path | None = None
        self.completion_files: dict[str, pathlib.Path] = {}
        self.calibration_file_path: pathlib.Path | None = None
        self.calibration_file_value: float | None = None

        self._configure_styles()
        self._create_workflow_layout()
        self.create_widgets("compatible" if reuse_artifacts else "none")
        self.setup_logging()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.sync_config_ui()
        if data_dir:
            self.selected_folder_var.set(str(workflow.normalize_path_text(data_dir)))
            self.after(0, self.recheck_folder)
        else:
            self._apply_folder_validation(None, workflow.validate_folder(None))

    def _configure_styles(self) -> None:
        colors = self.style.colors
        default_font = tkfont.nametofont("TkDefaultFont")
        default_size = int(default_font.cget("size"))
        self.page_title_font = default_font.copy()
        self.page_title_font.configure(size=max(default_size + 6, 18), weight="bold")
        self.section_title_font = default_font.copy()
        self.section_title_font.configure(size=max(default_size + 2, 11), weight="bold")
        self.control_title_font = default_font.copy()
        self.control_title_font.configure(weight="bold")
        self.helper_font = default_font.copy()
        self.helper_font.configure(size=max(default_size - 1, 9))
        self.fixed_font = tkfont.nametofont("TkFixedFont").copy()

        def blend(background_color: str, accent_color: str, strength: float) -> str:
            background_rgb = self.winfo_rgb(background_color)
            accent_rgb = self.winfo_rgb(accent_color)
            channels = (
                round((background * (1 - strength) + accent * strength) / 257)
                for background, accent in zip(background_rgb, accent_rgb)
            )
            return "#" + "".join(f"{channel:02x}" for channel in channels)

        helper_color = blend(colors.dark, colors.secondary, 0.28)
        surface_border = blend(colors.bg, colors.dark, 0.14)
        self.style.configure("App.TFrame", background=colors.bg)
        self.style.configure("Header.TFrame", background=colors.bg)
        self.style.configure(
            "AppTitle.TLabel",
            background=colors.bg,
            foreground=colors.dark,
            font=self.page_title_font,
        )
        self.style.configure(
            "AppSubtitle.TLabel",
            background=colors.bg,
            foreground=helper_color,
        )
        self.style.configure(
            "DialogTitle.TLabel",
            background=colors.bg,
            foreground=colors.dark,
            font=self.section_title_font,
        )
        self.style.configure(
            "StepTitle.TLabel",
            background=colors.bg,
            foreground=colors.dark,
            font=self.section_title_font,
        )
        self.style.configure(
            "StepSubtitle.TLabel",
            background=colors.bg,
            foreground=helper_color,
            font=self.helper_font,
        )
        self.style.configure(
            "Section.TFrame",
            background=colors.bg,
            bordercolor=surface_border,
            borderwidth=1,
            relief="solid",
        )
        self.style.configure(
            "Section.TLabelframe",
            background=colors.bg,
            bordercolor=surface_border,
            borderwidth=1,
            relief="solid",
        )
        self.style.configure(
            "Section.TLabelframe.Label",
            background=colors.bg,
            foreground=colors.dark,
            font=self.section_title_font,
        )
        self.style.configure(
            "SectionTitle.TLabel",
            foreground=colors.dark,
            font=self.control_title_font,
        )
        self.style.configure(
            "Helper.TLabel",
            foreground=helper_color,
            font=self.helper_font,
        )
        self.style.configure(
            "MethodTitle.TLabel",
            foreground=colors.dark,
            font=self.control_title_font,
        )
        self.style.configure(
            "Speed.TLabel",
            foreground=helper_color,
            font=self.helper_font,
        )
        self.style.configure(
            "Runs.Treeview",
            background=colors.bg,
            fieldbackground=colors.bg,
            foreground=colors.dark,
            bordercolor=surface_border,
            borderwidth=1,
            relief="solid",
            rowheight=28,
        )
        self.style.map(
            "Runs.Treeview",
            background=[("selected", blend(colors.bg, colors.primary, 0.16))],
            foreground=[("selected", colors.dark)],
        )
        self.style.configure(
            "Runs.Treeview.Heading",
            background=colors.dark,
            foreground=colors.light,
            font=self.control_title_font,
            padding=(SPACE_SM, SPACE_SM),
            relief="flat",
        )

        for name, color in (
            ("Primary", colors.primary),
            ("Info", colors.info),
            ("Success", colors.success),
            ("Warning", colors.warning),
            ("Danger", colors.danger),
        ):
            self.style.configure(
                f"Status.{name}.TLabel",
                background=blend(colors.bg, color, 0.16),
                foreground=colors.dark,
            )
        self.style.configure(
            "InlineError.TLabel",
            background=colors.bg,
            foreground=blend(colors.dark, colors.danger, 0.65),
            font=self.helper_font,
        )

    def _create_workflow_layout(self) -> None:
        self.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.sidebar = WorkflowSidebar(
            self,
            WORKFLOW_STEPS,
            on_select=self.show_step,
            width=286,
            on_preferences=self.show_preferences,
            on_help=self.show_help,
        )
        self.sidebar.grid(row=0, column=0, sticky=tk.NS)
        ttk.Separator(self, orient=tk.VERTICAL).grid(
            row=0, column=0, sticky=tk.NS + tk.E
        )

        shell = ttk.Frame(self, style="App.TFrame")
        shell.grid(row=0, column=1, sticky=tk.NSEW)
        self.canvas = tk.Canvas(
            shell, highlightthickness=0, background=self.style.colors.bg
        )
        scrollbar = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, style="App.TFrame")
        self.content.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(
                self.canvas_window, width=event.width
            ),
        )
        self.canvas.bind_all("<MouseWheel>", self._mousewheel)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.footer = ApplicationFooter(
            self,
            on_back=self.go_back,
            on_continue=self.go_forward,
        )
        self.footer.grid(row=1, column=0, columnspan=2, sticky=tk.EW)
        self.analyze = self.footer.continue_button
        self.progress = self.footer.progress

    def _mousewheel(self, event: tk.Event) -> None:
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def show_preferences(self) -> None:
        messagebox.showinfo(
            "Preferences",
            "Analysis preferences are configured explicitly within each workflow step.",
            parent=self,
        )

    def show_help(self) -> None:
        webbrowser.open_new_tab("https://github.com/hgrecco/plantsinplates")

    def show_step(self, key: str) -> None:
        index = WORKFLOW_INDEX[key]
        if self.is_busy or index > self.furthest_step_index:
            return
        self.current_step_index = index
        for step_key, page in self.step_pages.items():
            if step_key == key:
                page.pack(fill=tk.X)
            else:
                page.pack_forget()
        self.canvas.yview_moveto(0)
        self.refresh_workflow_chrome()

    def go_back(self) -> None:
        if not self.is_busy and self.current_step_index > 0:
            self.show_step(WORKFLOW_STEPS[self.current_step_index - 1].key)

    def go_forward(self) -> None:
        if self.is_busy:
            return
        if self.current_step_index == WORKFLOW_INDEX["analysis"]:
            self.analyze_selected_folder()
            return
        if self.current_step_index == WORKFLOW_INDEX["results"]:
            self.on_close()
            return
        if not self._current_step_is_valid():
            self.refresh_workflow_chrome()
            return
        next_index = self.current_step_index + 1
        self.furthest_step_index = max(self.furthest_step_index, next_index)
        self.show_step(WORKFLOW_STEPS[next_index].key)

    def _calibration_is_valid(self) -> bool:
        if not hasattr(self, "calibration_mode_var"):
            return False
        try:
            self.get_calibration_spec()
        except (TypeError, ValueError):
            return False
        return True

    def _measurement_is_valid(self) -> bool:
        if not self._calibration_is_valid() or not hasattr(self, "setting_errors"):
            return False
        try:
            self.get_measurement_settings()
        except (TypeError, ValueError):
            return False
        return True

    def _current_step_is_valid(self) -> bool:
        key = WORKFLOW_STEPS[self.current_step_index].key
        if key == "folder":
            return self.folder_validation.valid
        if key == "calibration":
            return self.folder_validation.valid and self._calibration_is_valid()
        if key in {"method", "analysis"}:
            return self.folder_validation.valid and self._measurement_is_valid()
        return self.current_run_dir is not None

    def _current_step_has_error(self) -> bool:
        key = WORKFLOW_STEPS[self.current_step_index].key
        if key == "folder":
            return (
                bool(self.selected_folder_var.get().strip())
                and not self.folder_validation.valid
            )
        if key == "calibration":
            return self.folder_validation.valid and not self._calibration_is_valid()
        if key == "method":
            return self.folder_validation.valid and not self._measurement_is_valid()
        if key == "analysis":
            return self.last_run_status in {"failed", "cancelled"}
        return False

    def refresh_workflow_chrome(self) -> None:
        if not hasattr(self, "step_pages") or not hasattr(self, "cancel_button"):
            return

        folder_subtitle = (
            self.selected_folder.name
            if self.selected_folder is not None and self.folder_validation.valid
            else "Choose input data"
        )
        calibration_subtitle = {
            "metadata": "Image metadata",
            "cal_file": "cal.txt shared value",
            "manual": "Manual shared value",
        }.get(self.calibration_mode_var.get(), "Choose a source")
        method_subtitle = METHODS.get(self.method_var.get(), {}).get(
            "name", "Measurement settings"
        )
        if self.is_busy:
            analysis_subtitle = "Analysis in progress"
        elif self.last_run_status:
            analysis_subtitle = self.last_run_status.replace("_", " ").title()
        else:
            analysis_subtitle = "Configure and execute"
        results_subtitle = (
            self.current_run_dir.name
            if self.current_run_dir is not None
            else "View outputs and reports"
        )
        for key, subtitle in {
            "folder": folder_subtitle,
            "calibration": calibration_subtitle,
            "method": str(method_subtitle),
            "analysis": analysis_subtitle,
            "results": results_subtitle,
        }.items():
            self.sidebar.update_subtitle(key, subtitle)

        for index, step in enumerate(WORKFLOW_STEPS):
            if index == self.current_step_index:
                state = (
                    StepState.ERROR
                    if self._current_step_has_error()
                    else StepState.CURRENT
                )
            elif index <= self.furthest_step_index:
                state = (
                    StepState.ERROR
                    if step.key == "analysis"
                    and self.last_run_status in {"failed", "cancelled"}
                    else StepState.COMPLETED
                )
            else:
                state = StepState.UPCOMING
            self.sidebar.set_state(step.key, state)

        self.footer.configure_back(
            enabled=self.current_step_index > 0 and not self.is_busy
        )
        if self.is_busy:
            self.footer.configure_continue(
                enabled=False,
                text="Running…",
                command=self.analyze_selected_folder,
            )
            self.footer.set_status(self.operation_var.get(), StatusKind.BUSY)
            return

        key = WORKFLOW_STEPS[self.current_step_index].key
        if key == "folder":
            enabled = self.folder_validation.valid
            if enabled:
                message = "Ready — data folder validated."
                kind = StatusKind.READY
            elif self.selected_folder_var.get().strip():
                message = self.folder_validation.message
                kind = (
                    StatusKind.WARNING
                    if self.folder_validation.state == "inconsistent"
                    else StatusKind.ERROR
                )
            else:
                message = "Choose or paste a data folder to begin."
                kind = StatusKind.INFO
            self.footer.configure_continue(enabled=enabled)
        elif key == "calibration":
            enabled = self.folder_validation.valid and self._calibration_is_valid()
            message = (
                "Ready — calibration source configured."
                if enabled
                else "Choose a valid calibration source."
            )
            kind = StatusKind.READY if enabled else StatusKind.ERROR
            self.footer.configure_continue(enabled=enabled)
        elif key == "method":
            enabled = self.folder_validation.valid and self._measurement_is_valid()
            message = (
                "Ready — method settings configured."
                if enabled
                else "Correct the method settings before continuing."
            )
            kind = StatusKind.READY if enabled else StatusKind.ERROR
            self.footer.configure_continue(enabled=enabled)
        elif key == "analysis":
            enabled = self.analysis_ready
            label = {
                "experiment": "Run experiment  →",
                "plate": "Run plate  →",
            }.get(self.folder_validation.kind, "Run analysis  →")
            if self.current_run_dir is not None:
                label = "Run again  →"
            if self.last_run_status == "failed":
                message = (
                    "The previous run failed. The configuration is ready to try again."
                )
                kind = StatusKind.ERROR
            elif self.last_run_status == "cancelled":
                message = "The previous run was cancelled. Completed cache work can be reused."
                kind = StatusKind.WARNING
            else:
                message = (
                    "Ready — analysis configured."
                    if enabled
                    else "Complete the previous steps before running analysis."
                )
                kind = StatusKind.READY if enabled else StatusKind.ERROR
            self.footer.configure_continue(
                enabled=enabled,
                text=label,
                command=self.analyze_selected_folder,
            )
        else:
            enabled = self.current_run_dir is not None
            message = (
                self.operation_var.get()
                if enabled
                else "No completed run is available yet."
            )
            kind = (
                StatusKind.WARNING
                if self.last_run_status in {"completed_with_warnings", "cancelled"}
                else StatusKind.ERROR
                if self.last_run_status == "failed"
                else StatusKind.READY
                if enabled
                else StatusKind.INFO
            )
            self.footer.configure_continue(
                enabled=True,
                text="Close",
                bootstyle="dark-outline",
                command=self.on_close,
            )
        self.footer.set_status(message, kind)

    @staticmethod
    def card(parent: tk.Widget, title: str = "") -> ttk.Frame:
        if title:
            frame = ttk.Labelframe(
                parent,
                text=title,
                padding=SPACE_MD,
                style="Section.TLabelframe",
            )
        else:
            frame = ttk.Frame(
                parent,
                padding=SPACE_MD,
                style="Section.TFrame",
            )
        frame.pack(fill=tk.X, padx=SPACE_LG, pady=(0, SPACE_MD))
        return frame

    @staticmethod
    def card_label(
        parent: tk.Widget, text: str = "", *, muted: bool = False, **kwargs
    ) -> ttk.Label:
        if muted:
            kwargs.setdefault("style", "Helper.TLabel")
        return ttk.Label(parent, text=text, **kwargs)

    def create_widgets(self, default_reuse_policy: workflow.ReusePolicy) -> None:
        intro = ttk.Frame(
            self.content,
            padding=(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_MD),
            style="Header.TFrame",
        )
        intro.pack(fill=tk.X)
        ttk.Label(
            intro,
            text="Fluorescence microscopy analysis",
            style="AppTitle.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            intro,
            text="Choose data, make calibration explicit, and keep every run reproducible.",
            style="AppSubtitle.TLabel",
        ).pack(anchor=tk.W, pady=(SPACE_XS, 0))
        ttk.Separator(self.content).pack(
            fill=tk.X,
            padx=SPACE_LG,
            pady=(0, SPACE_MD),
        )
        self.step_pages = {
            step.key: self._create_step_page(step) for step in WORKFLOW_STEPS
        }
        self._create_input_card()
        self._create_calibration_card()
        self._create_method_card()
        self._create_run_card(default_reuse_policy)
        self._create_completion_card()
        self._create_log_card()
        self.step_pages["folder"].pack(fill=tk.X)

    def _create_step_page(self, step: StepDefinition) -> ttk.Frame:
        page = ttk.Frame(self.content, style="App.TFrame")
        ttk.Label(
            page,
            text={
                "folder": "Data folder and previous runs",
                "calibration": "Image calibration",
                "method": "Measurement method and settings",
                "analysis": "Run analysis",
                "results": "Results",
            }[step.key],
            style="StepTitle.TLabel",
        ).pack(anchor=tk.W, padx=SPACE_LG)
        ttk.Label(
            page,
            text={
                "folder": "Choose the input data and review earlier managed runs.",
                "calibration": "Select one explicit calibration source for this run.",
                "method": "Choose a measurement approach and configure its physical dimensions.",
                "analysis": "Review reuse behavior, then execute the configured analysis.",
                "results": "Open the immutable run folder and generated reports.",
            }[step.key],
            style="StepSubtitle.TLabel",
        ).pack(anchor=tk.W, padx=SPACE_LG, pady=(SPACE_XS, SPACE_MD))
        return page

    def _create_input_card(self) -> None:
        frame = self.card(self.step_pages["folder"])
        frame.columnconfigure(0, weight=1)
        self.card_label(
            frame,
            "Paste a path or browse to an experiment_* or plate_* folder. Re-check after repairing input files.",
            muted=True,
            wraplength=900,
        ).grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, SPACE_SM))
        self.selected_folder_var = tk.StringVar()
        self.folder_path_entry = ttk.Entry(frame, textvariable=self.selected_folder_var)
        self.folder_path_entry.grid(row=1, column=0, sticky=tk.EW, padx=(0, SPACE_SM))
        self.folder_path_entry.bind("<Return>", lambda _event: self.recheck_folder())
        self.folder_path_entry.bind(
            "<FocusOut>", lambda _event: self.schedule_path_validation(0)
        )
        self.folder_path_entry.bind(
            "<KeyRelease>", lambda _event: self.schedule_path_validation(600)
        )
        self.browse_button = ttk.Button(
            frame,
            text="Browse…",
            command=self.pick_folder,
            bootstyle="primary-outline",
        )
        self.browse_button.grid(row=1, column=1, padx=(0, SPACE_SM))
        self.open_input_button = ttk.Button(
            frame,
            text="Open folder",
            command=self.open_input_folder,
            bootstyle="primary-outline",
        )
        self.open_input_button.grid(row=1, column=2, padx=(0, SPACE_SM))
        self.recheck_button = ttk.Button(
            frame,
            text="Re-check",
            command=self.recheck_folder,
            bootstyle="primary-outline",
        )
        self.recheck_button.grid(row=1, column=3)
        self.folder_status = ttk.Label(
            frame,
            anchor="w",
            justify=tk.LEFT,
            padding=SPACE_SM,
            style="Status.Primary.TLabel",
            text="ⓘ Choose or paste an experiment_* or plate_* folder to begin.",
        )
        self.folder_status.grid(
            row=2, column=0, columnspan=4, sticky=tk.EW, pady=(SPACE_MD, 0)
        )
        self.folder_details_visible = False
        self.folder_details_var = tk.StringVar()
        self.folder_details_button = ttk.Button(
            frame,
            text="Show validation details",
            command=self.toggle_folder_details,
            bootstyle="link",
        )
        self.folder_details_button.grid(
            row=3, column=0, sticky=tk.W, pady=(SPACE_XS, 0)
        )
        self.folder_details_label = self.card_label(
            frame,
            textvariable=self.folder_details_var,
            muted=True,
            wraplength=900,
            justify=tk.LEFT,
        )

        ttk.Separator(frame).grid(
            row=5,
            column=0,
            columnspan=4,
            sticky=tk.EW,
            pady=SPACE_MD,
        )
        self.card_label(frame, "Previous runs", style="SectionTitle.TLabel").grid(
            row=6, column=0, sticky=tk.W
        )
        self.runs_status_var = tk.StringVar(value="No managed runs found.")
        self.card_label(frame, textvariable=self.runs_status_var, muted=True).grid(
            row=6, column=1, columnspan=3, sticky=tk.E
        )
        columns = ("time", "status", "method", "calibration", "reuse", "outputs")
        self.runs_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=4,
            style="Runs.Treeview",
        )
        headings = {
            "time": "Started",
            "status": "Status",
            "method": "Method",
            "calibration": "Calibration",
            "reuse": "Reuse",
            "outputs": "Outputs",
        }
        widths = {
            "time": 155,
            "status": 145,
            "method": 130,
            "calibration": 110,
            "reuse": 120,
            "outputs": 75,
        }
        for column in columns:
            self.runs_tree.heading(column, text=headings[column])
            self.runs_tree.column(
                column, width=widths[column], minwidth=60, stretch=column == "time"
            )
        self.runs_tree.grid(
            row=7,
            column=0,
            columnspan=4,
            sticky=tk.EW,
            pady=(SPACE_SM, SPACE_XS),
        )
        self.runs_tree.bind("<<TreeviewSelect>>", self.select_previous_run)
        self.run_details_var = tk.StringVar(
            value="Select a run to inspect its saved settings."
        )
        self.card_label(
            frame,
            textvariable=self.run_details_var,
            muted=True,
            wraplength=900,
            justify=tk.LEFT,
        ).grid(
            row=8,
            column=0,
            columnspan=4,
            sticky=tk.W,
            pady=(SPACE_XS, SPACE_SM),
        )
        run_actions = ttk.Frame(frame)
        run_actions.grid(row=9, column=0, columnspan=4, sticky=tk.W)
        self.open_previous_run_button = ttk.Button(
            run_actions,
            text="Open run folder",
            command=self.open_previous_run,
            bootstyle="primary-outline",
        )
        self.load_previous_settings_button = ttk.Button(
            run_actions,
            text="Use these settings",
            command=self.load_previous_settings,
            bootstyle="primary-outline",
        )
        self.delete_run_button = ttk.Button(
            run_actions,
            text="Delete run…",
            command=self.delete_previous_run,
            bootstyle="danger-outline",
        )
        self.clear_cache_button = ttk.Button(
            run_actions,
            text="Clear reusable cache…",
            command=self.clear_reusable_cache,
            bootstyle="danger-outline",
        )
        for index, button in enumerate(
            (
                self.open_previous_run_button,
                self.load_previous_settings_button,
                self.delete_run_button,
                self.clear_cache_button,
            )
        ):
            button.grid(row=0, column=index, padx=(0, SPACE_SM))

    def _create_calibration_card(self) -> None:
        frame = self.card(self.step_pages["calibration"])
        frame.columnconfigure(1, weight=1)
        self.calibration_mode_var = tk.StringVar(value="metadata")
        self.calibration_radio_buttons: list[ttk.Radiobutton] = []
        choices = [
            (
                "metadata",
                "Image metadata (per image)",
                "Use each image's embedded X/Y physical pixel size.",
            ),
            (
                "cal_file",
                "cal.txt (shared)",
                "Use one value from the selected folder or parent experiment.",
            ),
            (
                "manual",
                "Manual value (shared)",
                "Enter one micrometers-per-pixel value for every image.",
            ),
        ]
        for row, (value, title, description) in enumerate(choices):
            radio = ttk.Radiobutton(
                frame,
                variable=self.calibration_mode_var,
                value=value,
                command=self.on_calibration_mode_changed,
            )
            radio.grid(
                row=row * 2,
                column=0,
                rowspan=2,
                sticky=tk.NW,
                padx=(0, SPACE_SM),
                pady=(SPACE_XS, 0),
            )
            self.calibration_radio_buttons.append(radio)
            self.card_label(frame, title, style="MethodTitle.TLabel").grid(
                row=row * 2, column=1, sticky=tk.W, pady=(SPACE_XS, 0)
            )
            self.card_label(frame, description, muted=True, wraplength=760).grid(
                row=row * 2 + 1, column=1, sticky=tk.W, pady=(0, SPACE_SM)
            )
        self.cal_file_status_var = tk.StringVar(value="No cal.txt found.")
        self.card_label(
            frame, textvariable=self.cal_file_status_var, muted=True, wraplength=800
        ).grid(row=6, column=1, sticky=tk.W, pady=(SPACE_XS, SPACE_XS))
        manual = ttk.Frame(frame)
        manual.grid(row=7, column=1, sticky=tk.W, pady=(SPACE_XS, SPACE_XS))
        self.card_label(manual, "Manual µm/pixel").grid(
            row=0, column=0, padx=(0, SPACE_SM)
        )
        self.manual_calibration_var = tk.StringVar(value="1")
        self.um_per_pixel_var = self.manual_calibration_var
        self.manual_calibration_entry = ttk.Entry(
            manual, textvariable=self.manual_calibration_var, width=14
        )
        self.um_per_pixel_entry = self.manual_calibration_entry
        self.manual_calibration_entry.grid(row=0, column=1)
        self.calibration_error_var = tk.StringVar()
        ttk.Label(
            frame,
            textvariable=self.calibration_error_var,
            style="InlineError.TLabel",
        ).grid(row=8, column=1, sticky=tk.W)
        audit_actions = ttk.Frame(frame)
        audit_actions.grid(row=9, column=1, sticky=tk.W, pady=(SPACE_SM, 0))
        self.audit_button = ttk.Button(
            audit_actions,
            text="Check image calibrations",
            command=self.start_calibration_audit,
            bootstyle="primary-outline",
        )
        self.audit_details_button = ttk.Button(
            audit_actions,
            text="Show audit details",
            command=self.show_audit_details,
            bootstyle="primary-outline",
        )
        self.audit_button.grid(row=0, column=0, padx=(0, SPACE_SM))
        self.audit_details_button.grid(row=0, column=1)
        self.audit_status_var = tk.StringVar(
            value="Optional audit not run. Metadata failures will be skipped and reported."
        )
        self.card_label(
            frame, textvariable=self.audit_status_var, muted=True, wraplength=850
        ).grid(row=10, column=1, sticky=tk.W, pady=(SPACE_XS, 0))
        self.manual_calibration_var.trace_add(
            "write", lambda *_args: self.validate_configuration()
        )

    def _create_method_card(self) -> None:
        frame = self.card(self.step_pages["method"])
        frame.columnconfigure(0, weight=1)
        self.method_var = tk.StringVar(value=self.default_measurement_config.method)
        self.method_radio_buttons: list[ttk.Radiobutton] = []
        for index, (key, data) in enumerate(METHODS.items()):
            row = ttk.Frame(frame)
            row.grid(row=index, column=0, sticky=tk.EW, pady=SPACE_SM)
            row.columnconfigure(1, weight=1)
            radio = ttk.Radiobutton(
                row, variable=self.method_var, value=key, command=self.sync_config_ui
            )
            radio.grid(
                row=0,
                column=0,
                rowspan=2,
                sticky=tk.NW,
                padx=(0, SPACE_SM),
            )
            self.method_radio_buttons.append(radio)
            self.card_label(row, data["name"], style="MethodTitle.TLabel").grid(
                row=0, column=1, sticky=tk.W
            )
            speed = {
                "box": "Fast",
                "centerline": "Moderate",
                "centerline_gaussian": "Slow",
            }[key]
            self.card_label(row, speed, style="Speed.TLabel").grid(
                row=0, column=2, sticky=tk.E, padx=(SPACE_SM, 0)
            )
            self.card_label(row, data["summary"], muted=True).grid(
                row=1, column=1, columnspan=2, sticky=tk.W
            )

        ttk.Separator(frame).grid(row=3, column=0, sticky=tk.EW, pady=SPACE_MD)
        self.settings_body = ttk.Frame(frame)
        self.settings_body.grid(row=4, column=0, sticky=tk.EW)
        self.box_size_var = tk.StringVar(
            value=str(self.default_measurement_config.box_size)
        )
        self.box_offset_var = tk.StringVar(
            value=str(self.default_measurement_config.box_offset)
        )
        self.perpendicular_width_var = tk.StringVar(
            value=str(self.default_measurement_config.perpendicular_width)
        )
        self.length_var = tk.StringVar(
            value=str(self.default_measurement_config.length)
        )
        self.savgol_window_var = tk.StringVar(
            value=str(self.default_measurement_config.savgol_window)
        )
        self.intensity_savgol_window_var = tk.StringVar(
            value=str(self.default_measurement_config.intensity_savgol_window)
        )
        self.setting_errors: dict[str, ttk.Label] = {}
        for variable in self.setting_variables.values():
            variable.trace_add("write", lambda *_args: self.validate_configuration())
        self.conversion_preview_var = tk.StringVar()
        self.card_label(
            frame, textvariable=self.conversion_preview_var, muted=True, wraplength=880
        ).grid(row=5, column=0, sticky=tk.W, pady=(SPACE_SM, 0))

    @property
    def setting_variables(self) -> dict[str, tk.StringVar]:
        return {
            "box_size": self.box_size_var,
            "box_offset": self.box_offset_var,
            "perpendicular_width": self.perpendicular_width_var,
            "length": self.length_var,
            "savgol_window": self.savgol_window_var,
            "intensity_savgol_window": self.intensity_savgol_window_var,
        }

    def _create_run_card(self, default_reuse_policy: workflow.ReusePolicy) -> None:
        frame = self.card(self.step_pages["analysis"])
        frame.columnconfigure(1, weight=1)
        self.card_label(frame, "Reuse policy", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky=tk.W
        )
        self.reuse_policy_var = tk.StringVar(value=default_reuse_policy)
        self.reuse_radio_buttons: list[ttk.Radiobutton] = []
        reuse_choices = [
            (
                "compatible",
                "Reuse compatible work",
                "Reuse matching measurements and preprocessing.",
            ),
            (
                "preprocessing",
                "Recompute measurements",
                "Reuse masks and centerlines only.",
            ),
            ("none", "Recompute everything", "Ignore caches without deleting them."),
        ]
        reuse_frame = ttk.Frame(frame)
        reuse_frame.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=(SPACE_XS, SPACE_SM),
        )
        for row, (value, title, description) in enumerate(reuse_choices):
            radio = ttk.Radiobutton(
                reuse_frame,
                variable=self.reuse_policy_var,
                value=value,
                command=self.update_ready_summary,
            )
            radio.grid(row=row, column=0, sticky=tk.NW, pady=SPACE_XS)
            self.reuse_radio_buttons.append(radio)
            self.card_label(reuse_frame, title).grid(
                row=row, column=1, sticky=tk.W, padx=(SPACE_XS, SPACE_SM)
            )
            self.card_label(reuse_frame, description, muted=True).grid(
                row=row, column=2, sticky=tk.W
            )
        self.ready_summary_var = tk.StringVar(
            value="Waiting for a valid folder and settings."
        )
        self.card_label(
            frame,
            textvariable=self.ready_summary_var,
            muted=True,
            wraplength=880,
            justify=tk.LEFT,
        ).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=(SPACE_XS, SPACE_SM),
        )
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, columnspan=2, sticky=tk.W)
        self.cancel_button = ttk.Button(
            actions,
            text="Cancel after current image",
            command=self.cancel_run,
            bootstyle="danger-outline",
        )
        self.cancel_button.grid(row=0, column=0)
        self.operation_var = tk.StringVar(
            value="Waiting for a valid experiment or plate."
        )
        self.card_label(
            frame, textvariable=self.operation_var, muted=True, wraplength=880
        ).grid(
            row=4,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=(SPACE_SM, SPACE_XS),
        )
        self.elapsed_var = tk.StringVar(value="Elapsed time: —")
        self.card_label(frame, textvariable=self.elapsed_var, muted=True).grid(
            row=5, column=0, columnspan=2, sticky=tk.W
        )

    def _create_completion_card(self) -> None:
        frame = self.card(self.step_pages["results"])
        self.completion_status = ttk.Label(
            frame,
            anchor="w",
            justify=tk.LEFT,
            padding=SPACE_SM,
            style="Status.Primary.TLabel",
            text="ⓘ Results will appear here after analysis.",
        )
        self.completion_status.pack(fill=tk.X)
        self.output_path_var = tk.StringVar()
        self.output_path_entry = ttk.Entry(
            frame, textvariable=self.output_path_var, state="readonly"
        )
        self.output_path_entry.pack(fill=tk.X, pady=(SPACE_SM, SPACE_XS))
        self.generated_files_var = tk.StringVar()
        self.card_label(
            frame, textvariable=self.generated_files_var, muted=True, wraplength=900
        ).pack(anchor=tk.W, pady=(0, SPACE_SM))
        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X)
        self.open_output_button = ttk.Button(
            actions,
            text="Open run folder",
            command=self.open_output_folder,
            bootstyle="primary-outline",
        )
        self.open_excel_button = ttk.Button(
            actions,
            text="Open Excel summary",
            command=lambda: self.open_output_file("Excel summary"),
            bootstyle="primary-outline",
        )
        self.open_pdf_button = ttk.Button(
            actions,
            text="Open PDF summary",
            command=lambda: self.open_output_file("PDF summary"),
            bootstyle="primary-outline",
        )
        self.open_calibration_report_button = ttk.Button(
            actions,
            text="Open calibration report",
            command=lambda: self.open_output_file("Calibration report"),
            bootstyle="primary-outline",
        )
        for index, button in enumerate(
            (
                self.open_output_button,
                self.open_excel_button,
                self.open_pdf_button,
                self.open_calibration_report_button,
            )
        ):
            button.grid(row=0, column=index, padx=(0, SPACE_SM))

    def _create_log_card(self) -> None:
        frame = self.card(self.step_pages["analysis"], "Technical details")
        self.log_visible = False
        self.log_toggle = ttk.Button(
            frame,
            text="Show technical log",
            command=self.toggle_log,
            bootstyle="primary-outline",
        )
        self.log_toggle.pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(
            frame,
            height=11,
            state="disabled",
            wrap=tk.WORD,
            font=self.fixed_font,
            background=self.style.colors.inputbg,
            foreground=self.style.colors.inputfg,
            insertbackground=self.style.colors.inputfg,
            selectbackground=self.style.colors.selectbg,
            selectforeground=self.style.colors.selectfg,
            highlightbackground=self.style.colors.border,
            highlightcolor=self.style.colors.primary,
            highlightthickness=1,
            relief=tk.FLAT,
        )

    def setup_logging(self) -> None:
        self.formatter = RelativeTimeFormatter(
            "%(relative_hms)s  %(levelname)s  %(message)s"
        )
        self.text_handler = TextHandler(self)
        self.text_handler.setFormatter(self.formatter)
        pipio.logger.addHandler(self.text_handler)
        pipio.logger.setLevel(logging.INFO)

    def on_close(self) -> None:
        if self.is_busy:
            self.run_cancel_event.set()
        self.audit_cancel_event.set()
        self.canvas.unbind_all("<MouseWheel>")
        pipio.logger.removeHandler(self.text_handler)
        self.destroy()

    def toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_text.pack(fill=tk.BOTH, expand=True, pady=(SPACE_SM, 0))
            self.log_toggle.configure(text="Hide technical log")
        else:
            self.log_text.pack_forget()
            self.log_toggle.configure(text="Show technical log")

    def append_log(self, message: str, _operation: str) -> None:
        if not self.winfo_exists():
            return
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def schedule_path_validation(self, delay_ms: int = 600) -> None:
        if self.is_busy:
            return
        if self.path_validation_after is not None:
            self.after_cancel(self.path_validation_after)
        self.path_validation_after = self.after(delay_ms, self.recheck_folder)

    def pick_folder(self) -> None:
        initial = (
            str(self.selected_folder.parent)
            if self.selected_folder
            else self.data_dir or str(pathlib.Path.cwd())
        )
        selected = filedialog.askdirectory(
            title="Choose experiment or plate folder",
            initialdir=initial,
            mustexist=True,
        )
        if selected:
            self.selected_folder_var.set(selected)
            self.recheck_folder()

    def recheck_folder(self) -> None:
        if self.is_busy:
            return
        if self.audit_in_progress:
            self.audit_cancel_event.set()
            self.audit_generation += 1
            self.audit_in_progress = False
        if self.path_validation_after is not None:
            try:
                self.after_cancel(self.path_validation_after)
            except tk.TclError:
                pass
        self.path_validation_after = None
        raw = self.selected_folder_var.get()
        if not raw.strip():
            self._apply_folder_validation(None, workflow.validate_folder(None))
            return
        path = workflow.normalize_path_text(raw, base=pathlib.Path.cwd())
        self.selected_folder = path
        self.selected_folder_var.set(str(path))
        self.validation_generation += 1
        generation = self.validation_generation
        self.folder_status.configure(
            text="ⓘ Checking folder structure and referenced images…",
            style="Status.Info.TLabel",
        )
        self.recheck_button.configure(state="disabled")
        self.analyze.configure(state="disabled")
        self.footer.set_status("Checking the selected folder…", StatusKind.INFO)

        def worker() -> None:
            result = workflow.validate_folder(path)
            self.after(0, self._finish_folder_validation, generation, path, result)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_folder_validation(
        self,
        generation: int,
        path: pathlib.Path,
        result: workflow.FolderValidation,
    ) -> None:
        if generation != self.validation_generation or not self.winfo_exists():
            return
        self._apply_folder_validation(path, result)

    def _apply_folder_validation(
        self, path: pathlib.Path | None, result: workflow.FolderValidation
    ) -> None:
        self.audit_cancel_event.set()
        self.audit_generation += 1
        self.audit_in_progress = False
        self.selected_folder = path
        self.folder_validation = result
        self.audit_readings = []
        self.audit_results = {}
        self.audit_status_var.set(
            "Optional audit not run. Metadata failures will be skipped and reported."
        )
        status_styles = {
            "valid": "Status.Success.TLabel",
            "invalid": "Status.Danger.TLabel",
            "inconsistent": "Status.Warning.TLabel",
            "empty": "Status.Primary.TLabel",
        }
        status_symbols = {
            "valid": "✓",
            "invalid": "⚠",
            "inconsistent": "⚠",
            "empty": "ⓘ",
        }
        self.folder_status.configure(
            text=f"{status_symbols[result.state]} {result.message}",
            style=status_styles[result.state],
        )
        self.folder_details_var.set(
            "\n".join(f"• {detail}" for detail in result.details)
        )
        self.folder_details_button.configure(
            state="normal" if result.details else "disabled",
            text="Hide validation details"
            if self.folder_details_visible
            else "Show validation details",
        )
        if self.folder_details_visible and result.details:
            self.folder_details_label.grid(
                row=4,
                column=0,
                columnspan=4,
                sticky=tk.W,
                pady=(SPACE_XS, 0),
            )
        else:
            self.folder_details_label.grid_remove()
        self.refresh_previous_runs()
        self.refresh_calibration_options()
        if not result.valid:
            self.furthest_step_index = 0
            if self.current_step_index != 0:
                self.show_step("folder")
        self.update_action_buttons_state()
        self.update_ready_summary()

    def toggle_folder_details(self) -> None:
        self.folder_details_visible = not self.folder_details_visible
        if self.folder_details_visible and self.folder_validation.details:
            self.folder_details_label.grid(
                row=4,
                column=0,
                columnspan=4,
                sticky=tk.W,
                pady=(SPACE_XS, 0),
            )
            self.folder_details_button.configure(text="Hide validation details")
        else:
            self.folder_details_label.grid_remove()
            self.folder_details_button.configure(text="Show validation details")

    def open_input_folder(self) -> None:
        if self.selected_folder and self.selected_folder.is_dir():
            self.open_path(self.selected_folder)

    def refresh_previous_runs(self) -> None:
        self.previous_runs = workflow.discover_runs(self.selected_folder)
        self.selected_run = None
        for item in self.runs_tree.get_children():
            self.runs_tree.delete(item)
        for record in self.previous_runs:
            started = record.started_at.replace("T", " ")[:19]
            self.runs_tree.insert(
                "",
                tk.END,
                iid=record.run_id,
                values=(
                    started,
                    record.status.replace("_", " "),
                    record.method,
                    record.calibration_mode,
                    record.reuse_policy,
                    "Yes" if record.outputs else "No",
                ),
            )
        count = len(self.previous_runs)
        self.runs_status_var.set(
            f"{count} managed run{'s' if count != 1 else ''} found."
        )
        self.run_details_var.set("Select a run to inspect its saved settings.")

    def select_previous_run(self, _event: tk.Event | None = None) -> None:
        selected = self.runs_tree.selection()
        run_id = selected[0] if selected else None
        self.selected_run = next(
            (record for record in self.previous_runs if record.run_id == run_id), None
        )
        self.run_details_var.set(
            workflow.run_record_summary(self.selected_run)
            if self.selected_run
            else "Select a run to inspect its saved settings."
        )
        self.update_action_buttons_state()

    def open_previous_run(self) -> None:
        if self.selected_run:
            self.open_path(self.selected_run.run_dir)

    def load_previous_settings(self) -> None:
        record = self.selected_run
        if record is None:
            return
        if record.settings_unit != "um":
            messagebox.showinfo(
                "Pixel-based settings",
                "This batch run stored pixel settings, which cannot be loaded into the GUI's micrometer fields.",
                parent=self,
            )
            return
        method = record.settings.get("method")
        if method in METHODS:
            self.method_var.set(method)
        for key, variable in self.setting_variables.items():
            if key in record.settings:
                variable.set(str(record.settings[key]))
        request = record.manifest.get("request", {})
        calibration = request.get("calibration", {})
        mode = calibration.get("mode")
        if mode in ("metadata", "manual") or (
            mode == "cal_file" and self.calibration_file_value is not None
        ):
            self.calibration_mode_var.set(mode)
        if mode == "manual" and calibration.get("shared_um_per_pixel") is not None:
            self.manual_calibration_var.set(str(calibration["shared_um_per_pixel"]))
        reuse = request.get("reuse_policy")
        if reuse in ("compatible", "preprocessing", "none"):
            self.reuse_policy_var.set(reuse)
        self.sync_config_ui()
        self.on_calibration_mode_changed()
        note = (
            " The stored cal.txt source is unavailable; choose a current calibration source."
            if mode == "cal_file" and self.calibration_file_value is None
            else ""
        )
        self.operation_var.set(f"Loaded settings from run {record.run_id}.{note}")

    def delete_previous_run(self) -> None:
        record = self.selected_run
        if record is None:
            return
        if not messagebox.askyesno(
            "Delete analysis run?",
            f"Delete {record.run_dir.name}? This removes only that managed run and cannot be undone.",
            parent=self,
        ):
            return
        try:
            workflow.delete_run(record)
        except Exception as ex:
            messagebox.showerror("Could not delete run", str(ex), parent=self)
        else:
            if self.current_run_dir == record.run_dir:
                self.current_run_dir = None
                self.completion_files = {}
                self.output_path_var.set("")
                self.generated_files_var.set("")
                self.set_completion("The selected run was deleted.", "neutral")
        self.refresh_previous_runs()
        self.update_action_buttons_state()

    def clear_reusable_cache(self) -> None:
        if self.selected_folder is None:
            return
        caches = workflow.cache_directories(self.selected_folder)
        if not caches:
            messagebox.showinfo(
                "Reusable cache", "No reusable cache was found.", parent=self
            )
            return
        if not messagebox.askyesno(
            "Clear reusable cache?",
            f"Delete {len(caches)} reusable cache director{'y' if len(caches) == 1 else 'ies'}? Previous runs and input images are not affected.",
            parent=self,
        ):
            return
        try:
            workflow.clear_reusable_cache(self.selected_folder)
        except Exception as ex:
            messagebox.showerror("Could not clear cache", str(ex), parent=self)
        self.operation_var.set("Reusable cache cleared.")

    def refresh_calibration_options(self) -> None:
        self.calibration_file_path = None
        self.calibration_file_value = None
        if self.selected_folder is not None:
            path = workflow.find_calibration_file(self.selected_folder)
            if path is not None:
                self.calibration_file_path = path
                try:
                    self.calibration_file_value = workflow.parse_calibration_file(path)
                except ValueError as ex:
                    self.cal_file_status_var.set(f"Invalid {path}: {ex}")
                    self.calibration_mode_var.set("metadata")
                else:
                    self.cal_file_status_var.set(
                        f"{path} — {self.calibration_file_value:g} µm/pixel"
                    )
                    self.calibration_mode_var.set("cal_file")
            else:
                self.cal_file_status_var.set("No cal.txt found for this selection.")
                self.calibration_mode_var.set("metadata")
        else:
            self.cal_file_status_var.set("No folder selected.")
        self.calibration_radio_buttons[1].configure(
            state="normal"
            if self.config_inputs_enabled and self.calibration_file_value is not None
            else "disabled"
        )
        self.on_calibration_mode_changed()

    def on_calibration_mode_changed(self) -> None:
        editable = (
            self.config_inputs_enabled and self.calibration_mode_var.get() == "manual"
        )
        self.manual_calibration_entry.configure(
            state="normal" if editable else "disabled"
        )
        self.validate_configuration()

    def parse_calibration_file(self, path: pathlib.Path) -> float:
        return workflow.parse_calibration_file(path)

    def resolve_calibration(self, folder: pathlib.Path) -> tuple[float, bool, str, str]:
        path = workflow.find_calibration_file(folder)
        if path is None:
            return 1.0, True, "Manual value", ""
        try:
            value = workflow.parse_calibration_file(path)
        except ValueError as ex:
            return (
                1.0,
                True,
                "Manual value",
                f"Invalid cal.txt: {ex}. Enter a value manually.",
            )
        source = (
            "Loaded from plate"
            if path.parent == folder and workflow.folder_kind(folder) == "plate"
            else "Loaded from experiment"
        )
        return value, False, source, ""

    def get_calibration_spec(self) -> workflow.CalibrationSpec:
        mode = self.calibration_mode_var.get()
        if mode == "metadata":
            return workflow.CalibrationSpec("metadata")
        if mode == "cal_file":
            if self.calibration_file_path is None:
                raise ValueError("cal.txt is not available")
            value = workflow.parse_calibration_file(self.calibration_file_path)
            return workflow.CalibrationSpec(
                "cal_file", value, self.calibration_file_path
            )
        value = self.parse_positive_float(
            "Manual calibration", self.manual_calibration_var.get()
        )
        return workflow.CalibrationSpec("manual", value)

    def start_calibration_audit(self) -> None:
        if self.audit_in_progress or not self.folder_validation.referenced_images:
            return
        self.audit_in_progress = True
        self.audit_generation += 1
        generation = self.audit_generation
        self.audit_cancel_event = threading.Event()
        self.audit_readings = []
        self.audit_results = {}
        self.audit_status_var.set("Starting calibration audit…")
        self.update_action_buttons_state()

        def progress(event: workflow.ProgressEvent) -> None:
            self.after(0, self.audit_status_var.set, event.message)

        def worker() -> None:
            try:
                readings = workflow.audit_calibrations(
                    self.folder_validation.referenced_images,
                    progress=progress,
                    cancel_event=self.audit_cancel_event,
                )
            except Exception as ex:
                self.after(0, self._finish_calibration_audit, generation, [], str(ex))
            else:
                self.after(0, self._finish_calibration_audit, generation, readings, "")

        threading.Thread(target=worker, daemon=True).start()

    def _finish_calibration_audit(
        self,
        generation: int,
        readings: list[workflow.CalibrationReading],
        error: str,
    ) -> None:
        if generation != self.audit_generation:
            return
        self.audit_in_progress = False
        self.audit_readings = readings
        self.audit_results = {
            str(reading.path.resolve()): reading for reading in readings
        }
        self.audit_status_var.set(
            f"Calibration audit failed: {error}"
            if error
            else workflow.summarize_calibrations(readings)
        )
        self.update_action_buttons_state()
        self.update_ready_summary()

    def show_audit_details(self) -> None:
        if not self.audit_readings:
            return
        dialog = ttk.Toplevel(self)
        dialog.title("Image calibration audit")
        dialog.geometry("900x520")
        dialog.minsize(640, 360)
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=SPACE_MD)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text="Image calibration audit",
            style="DialogTitle.TLabel",
        ).pack(anchor=tk.W, pady=(0, SPACE_SM))
        text = scrolledtext.ScrolledText(
            body,
            wrap=tk.NONE,
            font=self.fixed_font,
            background=self.style.colors.inputbg,
            foreground=self.style.colors.inputfg,
            insertbackground=self.style.colors.inputfg,
            selectbackground=self.style.colors.selectbg,
            selectforeground=self.style.colors.selectfg,
            highlightbackground=self.style.colors.border,
            highlightcolor=self.style.colors.primary,
            highlightthickness=1,
            relief=tk.FLAT,
        )
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(
            tk.END, workflow.summarize_calibrations(self.audit_readings) + "\n\n"
        )
        for reading in self.audit_readings:
            value = (
                f"{reading.um_per_pixel:g} µm/pixel"
                if reading.um_per_pixel is not None
                else "—"
            )
            text.insert(tk.END, f"{reading.status:12}  {value:18}  {reading.path}\n")
            if reading.message:
                text.insert(tk.END, f"              {reading.message}\n")
        text.configure(state="disabled")
        ttk.Button(
            body,
            text="Close",
            command=dialog.destroy,
            bootstyle="primary-outline",
        ).pack(anchor=tk.E, pady=(SPACE_SM, 0))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    @staticmethod
    def parse_positive_float(name: str, value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError(f"{name} must be a finite number greater than 0")
        return parsed

    @staticmethod
    def parse_nonnegative_float(name: str, value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0:
            raise ValueError(f"{name} must be a finite number of 0 or greater")
        return parsed

    @staticmethod
    def parse_finite_float(name: str, value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{name} must be finite")
        return parsed

    @staticmethod
    def um_to_px(value_um: float, um_per_pixel: float) -> int:
        return max(1, int(math.floor((value_um / um_per_pixel) + 0.5)))

    def _settings_for_method(self) -> list[tuple[str, str, str]]:
        if self.method_var.get() == "box":
            return [
                (
                    "box_size",
                    "Measurement box size (µm)",
                    "Larger boxes average more tissue; smaller boxes focus tightly on the tip.",
                ),
                (
                    "box_offset",
                    "Tip position offset (box units)",
                    "0 centers on the tip; positive moves toward it and negative moves away.",
                ),
            ]
        return [
            (
                "perpendicular_width",
                "Profile width (µm)",
                "Width sampled across the root.",
            ),
            (
                "length",
                "Profile length (µm)",
                "Distance measured along the root from the tip.",
            ),
            (
                "savgol_window",
                "Root-shape smoothing (µm)",
                "Spatial window used to smooth the traced centerline.",
            ),
            (
                "intensity_savgol_window",
                "Signal smoothing (µm)",
                "Set to 0 to keep the longitudinal signal unsmoothed.",
            ),
        ]

    def sync_config_ui(self) -> None:
        if not hasattr(self, "settings_body"):
            return
        for child in self.settings_body.winfo_children():
            child.destroy()
        self.setting_errors = {}
        for row, (key, label, helper) in enumerate(self._settings_for_method()):
            self.settings_body.columnconfigure(1, weight=1)
            self.card_label(self.settings_body, label).grid(
                row=row * 2,
                column=0,
                sticky=tk.W,
                padx=(0, SPACE_SM),
                pady=(SPACE_SM, 0),
            )
            entry = ttk.Entry(
                self.settings_body, textvariable=self.setting_variables[key], width=14
            )
            entry.grid(row=row * 2, column=1, sticky=tk.W, pady=(SPACE_SM, 0))
            entry.configure(
                state="normal" if self.config_inputs_enabled else "disabled"
            )
            self.card_label(
                self.settings_body, helper, muted=True, wraplength=640
            ).grid(
                row=row * 2 + 1,
                column=0,
                columnspan=2,
                sticky=tk.W,
                pady=(0, SPACE_SM),
            )
            error = ttk.Label(self.settings_body, style="InlineError.TLabel")
            error.grid(row=row * 2, column=2, sticky=tk.W, padx=(SPACE_SM, 0))
            self.setting_errors[key] = error
        self.validate_configuration()

    def get_measurement_settings(self) -> MeasurementConfig:
        return MeasurementConfig(
            method=self.method_var.get(),  # type: ignore[arg-type]
            box_size=self.parse_positive_float(
                "Measurement box size", self.box_size_var.get()
            ),  # type: ignore[arg-type]
            box_offset=self.parse_finite_float(
                "Tip position offset", self.box_offset_var.get()
            ),
            perpendicular_width=self.parse_positive_float(
                "Profile width", self.perpendicular_width_var.get()
            ),  # type: ignore[arg-type]
            length=self.parse_positive_float("Profile length", self.length_var.get()),  # type: ignore[arg-type]
            savgol_window=self.parse_positive_float(
                "Root-shape smoothing", self.savgol_window_var.get()
            ),  # type: ignore[arg-type]
            intensity_savgol_window=self.parse_nonnegative_float(
                "Signal smoothing", self.intensity_savgol_window_var.get()
            ),  # type: ignore[arg-type]
        )

    def get_measurement_config(self) -> MeasurementConfig:
        """Compatibility name for the user-facing GUI settings."""
        return self.get_measurement_settings()

    def validate_configuration(self) -> bool:
        if not hasattr(self, "setting_errors"):
            return False
        active = self._settings_for_method()
        if any(key not in self.setting_errors for key, _label, _helper in active):
            return False
        errors: dict[str, str] = {}
        try:
            self.get_calibration_spec()
        except (TypeError, ValueError) as ex:
            self.calibration_error_var.set(str(ex))
            errors["calibration"] = str(ex)
        else:
            self.calibration_error_var.set("")
        rules: dict[str, Callable[[str, str], float]] = {
            "box_size": self.parse_positive_float,
            "box_offset": self.parse_finite_float,
            "perpendicular_width": self.parse_positive_float,
            "length": self.parse_positive_float,
            "savgol_window": self.parse_positive_float,
            "intensity_savgol_window": self.parse_nonnegative_float,
        }
        for key, _label, _helper in active:
            try:
                rules[key](key.replace("_", " "), self.setting_variables[key].get())
                text = ""
            except (TypeError, ValueError) as ex:
                text = str(ex)
                errors[key] = text
            self.setting_errors[key].configure(text=text)
        valid = not errors
        self.update_conversion_preview(valid)
        self.update_action_buttons_state(config_valid=valid)
        self.update_ready_summary(config_valid=valid)
        return valid

    def update_conversion_preview(self, config_valid: bool) -> None:
        if not config_valid:
            self.conversion_preview_var.set(
                "Correct the highlighted values to preview pixel conversion."
            )
            return
        if self.calibration_mode_var.get() == "metadata":
            self.conversion_preview_var.set(
                "Micrometer settings will be converted separately for each image's calibration."
            )
            return
        try:
            spec = self.get_calibration_spec()
            settings = self.get_measurement_settings()
            request = workflow.AnalysisRequest(
                self.selected_folder or pathlib.Path.cwd(),
                self.folder_validation.kind or "plate",
                settings,
                "um",
                spec,
            )
            pixels = analyze.resolve_pixel_config(request, spec.shared_um_per_pixel)
            active = {key for key, _label, _helper in self._settings_for_method()}
            values = [
                f"{key.replace('_', ' ')}={value} px"
                for key, value in pixels.to_dict().items()
                if key in active and key != "box_offset"
            ]
            self.conversion_preview_var.set(
                "Shared-calibration preview: " + ", ".join(values)
            )
        except Exception:
            self.conversion_preview_var.set("")

    def build_analysis_request(self) -> workflow.AnalysisRequest:
        if self.selected_folder is None or self.folder_validation.kind is None:
            raise ValueError("Select a valid folder")
        return workflow.AnalysisRequest(
            input_path=self.selected_folder,
            input_kind=self.folder_validation.kind,
            settings=self.get_measurement_settings(),
            settings_unit="um",
            calibration=self.get_calibration_spec(),
            reuse_policy=self.reuse_policy_var.get(),  # type: ignore[arg-type]
            audit_results=dict(self.audit_results),
        )

    def update_ready_summary(self, config_valid: bool | None = None) -> None:
        if not hasattr(self, "ready_summary_var"):
            return
        if config_valid is None:
            try:
                self.get_measurement_settings()
                self.get_calibration_spec()
                config_valid = True
            except Exception:
                config_valid = False
        if (
            not self.folder_validation.valid
            or not config_valid
            or self.selected_folder is None
        ):
            self.ready_summary_var.set(
                "Waiting for a valid folder, calibration source, and method settings."
            )
            return
        audit = "audited" if self.audit_readings else "not audited"
        calibration = self.calibration_mode_var.get().replace("_", " ")
        proposed = self.selected_folder / "_output_<timestamp>-<unique-id>"
        self.ready_summary_var.set(
            f"Ready: {self.folder_validation.image_count} images • {METHODS[self.method_var.get()]['name']} • "
            f"calibration: {calibration} ({audit}) • reuse: {self.reuse_policy_var.get()}\n"
            f"New output: {proposed}"
        )

    def update_action_buttons_state(self, config_valid: bool | None = None) -> None:
        if not hasattr(self, "analyze"):
            return
        if config_valid is None:
            try:
                self.get_measurement_settings()
                self.get_calibration_spec()
                config_valid = True
            except Exception:
                config_valid = False
        valid = self.folder_validation.valid and config_valid and not self.is_busy
        self.analysis_ready = valid
        self.cancel_button.configure(state="normal" if self.is_busy else "disabled")
        existing = self.selected_folder is not None and self.selected_folder.is_dir()
        self.open_input_button.configure(
            state="normal" if existing and not self.is_busy else "disabled"
        )
        self.recheck_button.configure(
            state="normal" if not self.is_busy else "disabled"
        )
        selected_run = self.selected_run is not None and not self.is_busy
        self.open_previous_run_button.configure(
            state="normal" if selected_run else "disabled"
        )
        can_load = (
            selected_run
            and self.selected_run is not None
            and self.selected_run.settings_unit == "um"
        )
        self.load_previous_settings_button.configure(
            state="normal" if can_load else "disabled"
        )
        self.delete_run_button.configure(state="normal" if selected_run else "disabled")
        has_cache = bool(
            self.selected_folder and workflow.cache_directories(self.selected_folder)
        )
        self.clear_cache_button.configure(
            state="normal" if has_cache and not self.is_busy else "disabled"
        )
        self.audit_button.configure(
            state="normal"
            if self.folder_validation.referenced_images
            and not self.audit_in_progress
            and not self.is_busy
            else "disabled"
        )
        self.audit_details_button.configure(
            state="normal" if self.audit_readings else "disabled"
        )
        has_run = self.current_run_dir is not None and self.current_run_dir.exists()
        self.open_output_button.configure(state="normal" if has_run else "disabled")
        for button, name in (
            (self.open_excel_button, "Excel summary"),
            (self.open_pdf_button, "PDF summary"),
            (self.open_calibration_report_button, "Calibration report"),
        ):
            path = self.completion_files.get(name)
            button.configure(state="normal" if path and path.exists() else "disabled")
        self.refresh_workflow_chrome()

    def set_config_state(self, enabled: bool) -> None:
        self.config_inputs_enabled = enabled
        state = "normal" if enabled else "disabled"
        self.folder_path_entry.configure(state=state)
        self.browse_button.configure(state=state)
        for radio in (
            self.calibration_radio_buttons
            + self.method_radio_buttons
            + self.reuse_radio_buttons
        ):
            radio.configure(state=state)
        self.calibration_radio_buttons[1].configure(
            state="normal"
            if enabled and self.calibration_file_value is not None
            else "disabled"
        )
        self.manual_calibration_entry.configure(
            state="normal"
            if enabled and self.calibration_mode_var.get() == "manual"
            else "disabled"
        )
        self.sync_config_ui()

    def set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        self.config(cursor="watch" if busy else "")
        self.set_config_state(not busy)
        if busy:
            self.run_started_at = time.time()
            self.footer.show_indeterminate_progress()
            self._update_elapsed()
        else:
            self.footer.hide_progress()
            self.run_started_at = None
        self.update_action_buttons_state()

    def _update_elapsed(self) -> None:
        if not self.is_busy or self.run_started_at is None:
            return
        elapsed = int(time.time() - self.run_started_at)
        self.elapsed_var.set(f"Elapsed time: {elapsed // 60:02d}:{elapsed % 60:02d}")
        self.after(500, self._update_elapsed)

    def analyze_selected_folder(self) -> None:
        if not self.folder_validation.valid or not self.validate_configuration():
            return
        try:
            request = self.build_analysis_request()
        except ValueError as ex:
            messagebox.showerror("Invalid analysis setup", str(ex), parent=self)
            return
        self.clear_logs()
        self.text_handler.reset()
        self.formatter.started = time.time()
        self.run_cancel_event = threading.Event()
        self.operation_var.set("Preparing analysis…")
        self.elapsed_var.set("Elapsed time: 00:00")
        self.current_run_dir = None
        self.last_run_status = None
        self.completion_files = {}
        self.output_path_var.set("")
        self.generated_files_var.set("")
        self.set_completion("Running analysis…", "neutral")
        self.set_busy(True)

        def progress(event: workflow.ProgressEvent) -> None:
            self.after(0, self.apply_progress_event, event)

        threading.Thread(
            target=self._analysis_worker,
            args=(request, progress),
            daemon=True,
        ).start()

    def _analysis_worker(
        self, request: workflow.AnalysisRequest, progress: workflow.ProgressCallback
    ) -> None:
        try:
            result = analyze.run_analysis(
                request, progress=progress, cancel_event=self.run_cancel_event
            )
        except Exception as ex:
            self.after(0, self.finish_unexpected_run_error, str(ex))
        else:
            self.after(0, self.finish_run, result)

    def apply_progress_event(self, event: workflow.ProgressEvent) -> None:
        if not self.is_busy:
            return
        if event.total > 0:
            self.progress.stop()
            self.footer.show_progress(
                maximum=event.total,
                value=event.completed,
            )
        else:
            if str(self.progress.cget("mode")) != "indeterminate":
                self.footer.show_indeterminate_progress()
        message = event.message
        if event.stage == "measurement":
            message += f" • {event.completed}/{event.total} • reused {event.reused} • skipped {event.skipped} • errors {event.errors}"
        self.operation_var.set(message)
        self.refresh_workflow_chrome()

    def cancel_run(self) -> None:
        if self.is_busy:
            self.run_cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.operation_var.set(
                "Cancellation requested; waiting for the current image to finish…"
            )
            self.refresh_workflow_chrome()

    def finish_unexpected_run_error(self, message: str) -> None:
        self.set_busy(False)
        self.last_run_status = "failed"
        self.set_completion(f"Analysis stopped unexpectedly: {message}", "error")
        self.operation_var.set("Analysis failed before a run result was returned.")
        self.refresh_workflow_chrome()

    def finish_run(self, result: workflow.RunResult) -> None:
        self.set_busy(False)
        self.last_run_status = result.status
        self.current_run_dir = result.run_dir
        self.completion_files = dict(result.outputs)
        self.output_path_var.set(str(result.run_dir.resolve()))
        generated = [
            f"{name}: {path.name}" for name, path in self.completion_files.items()
        ]
        self.generated_files_var.set(
            "Generated: "
            + (" • ".join(generated) if generated else "No completed output files.")
        )
        kind: Literal["success", "warning", "error", "neutral"]
        if result.status == "completed":
            kind = "success"
        elif result.status in ("completed_with_warnings", "cancelled"):
            kind = "warning"
        else:
            kind = "error"
        summary = (
            f"{result.message} Processed {result.completed}/{result.total}; "
            f"reused {result.reused}, recomputed {result.recomputed}, "
            f"skipped {result.skipped}, errors {result.errors}."
        )
        self.set_completion(summary, kind)
        self.operation_var.set(result.message)
        self.furthest_step_index = WORKFLOW_INDEX["results"]
        self.refresh_previous_runs()
        self.update_action_buttons_state()
        self.show_step("results")

    def set_completion(
        self, text: str, kind: Literal["success", "warning", "error", "neutral"]
    ) -> None:
        status_styles = {
            "success": "Status.Success.TLabel",
            "warning": "Status.Warning.TLabel",
            "error": "Status.Danger.TLabel",
            "neutral": "Status.Primary.TLabel",
        }
        status_symbols = {
            "success": "✓",
            "warning": "⚠",
            "error": "⚠",
            "neutral": "ⓘ",
        }
        self.completion_status.configure(
            text=f"{status_symbols[kind]} {text}", style=status_styles[kind]
        )

    def open_output_folder(self) -> None:
        if self.current_run_dir and self.current_run_dir.exists():
            self.open_path(self.current_run_dir)

    def open_output_file(self, name: str) -> None:
        path = self.completion_files.get(name)
        if path and path.exists():
            self.open_path(path)

    @staticmethod
    def open_path(path: pathlib.Path) -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as ex:
            messagebox.showerror("Could not open path", str(ex))


app = None
tapp = typer.Typer()


def build_measurement_config(
    method: str,
    box_size: int,
    box_offset: float,
    perpendicular_width: int,
    length: int,
    savgol_window: int,
    intensity_savgol_window: int,
) -> MeasurementConfig:
    try:
        return MeasurementConfig(
            method=method,
            box_size=box_size,
            box_offset=box_offset,
            perpendicular_width=perpendicular_width,
            length=length,
            savgol_window=savgol_window,
            intensity_savgol_window=intensity_savgol_window,
        )
    except ValueError as ex:
        raise typer.BadParameter(str(ex)) from ex


@tapp.command()
def gui(
    data_dir: Annotated[str, typer.Argument(envvar="DATA_DIR")] = "",
    method: Annotated[str, typer.Option("--method")] = "box",
    box_size: Annotated[int, typer.Option("--box-size")] = 680,
    box_offset: Annotated[
        float,
        typer.Option(
            "--box-offset",
            help="Signed shift in box-size units (0=centered, +1=towards, -1=away)",
        ),
    ] = 0.0,
    perpendicular_width: Annotated[int, typer.Option("--perpendicular-width")] = 50,
    length: Annotated[int, typer.Option("--length")] = 50,
    savgol_window: Annotated[int, typer.Option("--savgol-window")] = 25,
    intensity_savgol_window: Annotated[
        int,
        typer.Option(
            "--intensity-savgol-window",
            help="Savitzky-Golay window for smoothing skel_intensities (0 disables).",
        ),
    ] = 25,
    reuse_artifacts: Annotated[
        bool,
        typer.Option(
            "--reuse-artifacts/--force-recompute",
            help="Choose the initial GUI reuse policy (compatible or none).",
        ),
    ] = True,
):
    global app
    import matplotlib

    matplotlib.use("Agg")
    app = AnalyzeApp(
        data_dir,
        measurement_config=build_measurement_config(
            method,
            box_size,
            box_offset,
            perpendicular_width,
            length,
            savgol_window,
            intensity_savgol_window,
        ),
        reuse_artifacts=reuse_artifacts,
    )
    app.mainloop()


@tapp.command()
def version():
    print(pipio.__version__)


@tapp.command()
def update():
    import requests
    import zipfile

    print("Downloading app from server ...")
    new_version = requests.get(
        "http://users.df.uba.ar/hgrecco/pkg/plantsinplates-app"
    ).content
    with open(".tmp.new_version", mode="wb") as fo:
        fo.write(new_version)
    print("Updating environment ...")
    with zipfile.ZipFile(".tmp.new_version") as z:
        z.extract("pixi.toml")
        z.extract("pixi.lock")


@tapp.command()
def test(
    data_dir: Annotated[str, typer.Argument(envvar="DATA_DIR")] = "",
    method: Annotated[str, typer.Option("--method")] = "box",
    box_size: Annotated[int, typer.Option("--box-size")] = 680,
    box_offset: Annotated[float, typer.Option("--box-offset")] = 0.0,
    perpendicular_width: Annotated[int, typer.Option("--perpendicular-width")] = 50,
    length: Annotated[int, typer.Option("--length")] = 50,
    savgol_window: Annotated[int, typer.Option("--savgol-window")] = 25,
    intensity_savgol_window: Annotated[
        int, typer.Option("--intensity-savgol-window")
    ] = 25,
    reuse_policy: Annotated[
        str,
        typer.Option(
            "--reuse-policy",
            help="compatible, preprocessing, or none",
        ),
    ] = "compatible",
):
    measurement_config = build_measurement_config(
        method,
        box_size,
        box_offset,
        perpendicular_width,
        length,
        savgol_window,
        intensity_savgol_window,
    )
    if reuse_policy not in ("compatible", "preprocessing", "none"):
        raise typer.BadParameter(
            "reuse policy must be compatible, preprocessing, or none"
        )
    path = workflow.normalize_path_text(data_dir, base=pathlib.Path.cwd())
    kind = workflow.folder_kind(path)
    if kind is None:
        raise typer.BadParameter("data directory must begin with experiment_ or plate_")
    request = workflow.AnalysisRequest(
        input_path=path,
        input_kind=kind,
        settings=measurement_config,
        settings_unit="px",
        calibration=workflow.CalibrationSpec("pixels"),
        reuse_policy=reuse_policy,  # type: ignore[arg-type]
    )
    result = analyze.run_analysis(request)
    typer.echo(f"{result.status}: {result.run_dir}")
    if result.status == "failed":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    tapp()
