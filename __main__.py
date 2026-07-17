import logging
import math
import os
import pathlib
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Annotated, Callable, Literal
import warnings

import typer

from plantsinplates import analyze, io as pipio
from plantsinplates.measurement_config import MeasurementConfig

warnings.filterwarnings(
    "ignore", message=".*The color list has more values.*", category=UserWarning
)

TITLE = f"Plants in Plates ({pipio.__version__})"
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


@dataclass(frozen=True)
class FolderValidation:
    valid: bool
    kind: Literal["experiment", "plate"] | None
    state: Literal["empty", "valid", "invalid", "inconsistent"]
    message: str
    details: tuple[str, ...] = ()


def folder_kind(path: pathlib.Path) -> Literal["experiment", "plate"] | None:
    """Return the supported analysis type encoded in a folder name."""
    if path.stem.startswith("experiment"):
        return "experiment"
    if path.stem.startswith("plate"):
        return "plate"
    return None


def validate_plate_folder(folder: pathlib.Path) -> FolderValidation:
    """Do a lightweight, non-mutating preflight for the selection UI."""
    missing: list[str] = []
    info = folder / "info.xlsx"
    # Keep this discovery and parsing identical to analyze.preflight_plate:
    # date folders may be nested and an optional description may follow the
    # date token (for example, date_20250613.0930_control).
    date_dirs = [path for path in folder.rglob("date_*") if path.is_dir()]
    if not info.is_file():
        missing.append("info.xlsx")
    if not date_dirs:
        missing.append("at least one date_* folder")
    if missing:
        return FolderValidation(
            False,
            "plate",
            "inconsistent",
            "Missing required input: " + ", ".join(missing) + ".",
            tuple(missing),
        )

    # Reading the workbook here catches a common configuration mistake before a
    # long run, without changing the analysis implementation.
    try:
        info_df = analyze.pd.read_excel(info)
    except Exception as ex:
        return FolderValidation(
            False,
            "plate",
            "inconsistent",
            "Could not read info.xlsx.",
            (str(ex),),
        )

    expected = {"row", "col", "genotype"}
    missing_columns = sorted(expected - set(info_df.columns))
    length_dates = {
        str(column).removesuffix("_length")
        for column in info_df.columns
        if str(column).endswith("_length")
    }
    fluo_dates = {
        str(column).removesuffix("_fluo")
        for column in info_df.columns
        if str(column).endswith("_fluo")
    }
    folder_dates = {
        path.name.split("_")[1] for path in date_dirs if len(path.name.split("_")) > 1
    }
    missing_date_columns = sorted(length_dates - fluo_dates)
    missing_length_columns = sorted(fluo_dates - length_dates)
    missing_date_folders = sorted(length_dates - folder_dates)
    unexpected_date_folders = sorted(folder_dates - length_dates)
    date_problem = not length_dates or not fluo_dates or length_dates != fluo_dates
    folder_problem = bool(length_dates and folder_dates != length_dates)
    if missing_columns or date_problem or folder_problem:
        details = tuple(
            (
                ["missing columns: " + ", ".join(missing_columns)]
                if missing_columns
                else []
            )
            + (
                ["missing _fluo columns for dates: " + ", ".join(missing_date_columns)]
                if missing_date_columns
                else []
            )
            + (
                [
                    "missing _length columns for dates: "
                    + ", ".join(missing_length_columns)
                ]
                if missing_length_columns
                else []
            )
            + (
                ["no _length/_fluo date columns were found"]
                if not length_dates or not fluo_dates
                else []
            )
            + (
                ["missing date folders: " + ", ".join(missing_date_folders)]
                if missing_date_folders
                else []
            )
            + (
                ["unexpected date folders: " + ", ".join(unexpected_date_folders)]
                if unexpected_date_folders
                else []
            )
        )
        return FolderValidation(
            False,
            "plate",
            "inconsistent",
            "info.xlsx was found, but its contents or date folders are inconsistent.",
            details,
        )

    return FolderValidation(
        True,
        "plate",
        "valid",
        "Valid plate — info.xlsx and date folders are ready for analysis.",
    )


def validate_folder(folder: pathlib.Path | None) -> FolderValidation:
    """Describe whether a selected experiment or plate is safe to analyze."""
    if folder is None:
        return FolderValidation(
            False,
            None,
            "empty",
            "Choose an experiment_* or plate_* folder to begin.",
        )
    if not folder.is_dir():
        return FolderValidation(False, None, "invalid", "Invalid folder.")

    kind = folder_kind(folder)
    if kind is None:
        return FolderValidation(
            False,
            None,
            "invalid",
            "Invalid folder — choose an experiment_* or plate_* folder.",
        )
    if kind == "plate":
        return validate_plate_folder(folder)

    plates = sorted(path for path in folder.glob("plate_*") if path.is_dir())
    if not plates:
        return FolderValidation(
            False,
            "experiment",
            "inconsistent",
            "Missing required input: no plate_* folders were found.",
        )
    invalid_plates = [(plate, validate_plate_folder(plate)) for plate in plates]
    invalid_plates = [
        (plate, result) for plate, result in invalid_plates if not result.valid
    ]
    if invalid_plates:
        names = ", ".join(plate.name for plate, _result in invalid_plates[:3])
        more = "" if len(invalid_plates) <= 3 else f" (+{len(invalid_plates) - 3} more)"
        details = tuple(
            f"{plate.name}: {result.message}"
            + (f" ({'; '.join(result.details)})" if result.details else "")
            for plate, result in invalid_plates
        )
        return FolderValidation(
            False,
            "experiment",
            "inconsistent",
            f"Missing or inconsistent files in {names}{more}.",
            details,
        )
    return FolderValidation(
        True,
        "experiment",
        "valid",
        f"Valid experiment — {len(plates)} plate{'s' if len(plates) != 1 else ''} ready for analysis.",
    )


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
        self.window: tk.Toplevel | None = None
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
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.attributes("-topmost", True)
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.window,
            text=self.text,
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=680,
            background="#1f2937",
            foreground="#ffffff",
            padx=10,
            pady=8,
        ).pack()

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


class AnalyzeApp(tk.Tk):
    is_busy: bool = False

    def __init__(
        self,
        data_dir: str,
        measurement_config: MeasurementConfig,
        reuse_artifacts: bool = True,
    ):
        super().__init__()
        self.title(TITLE)
        self.geometry("980x760")
        self.minsize(720, 520)
        self.configure(background="#f5f7fa")

        self.data_dir = data_dir
        self.default_measurement_config = measurement_config
        self.config_inputs_enabled = True
        self.calibration_editable = True
        self.selected_folder: pathlib.Path | None = None
        self.folder_validation = validate_folder(None)
        self.selected_artifact_count = 0
        self.run_started_at: float | None = None
        self.completion_files: dict[str, pathlib.Path] = {}

        self._configure_styles()
        self._create_scroll_area()
        self.create_widgets(reuse_artifacts)
        self.setup_logging()
        self.refresh_folder_state()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#f5f7fa")
        style.configure("Card.TLabelframe", background="#ffffff", bordercolor="#d7dee8")
        style.configure(
            "Card.TLabelframe.Label",
            background="#ffffff",
            foreground="#162033",
            font=("TkDefaultFont", 11, "bold"),
        )
        style.configure("TLabel", background="#f5f7fa", foreground="#263449")
        style.configure("Card.TLabel", background="#ffffff", foreground="#263449")
        style.configure("Muted.Card.TLabel", background="#ffffff", foreground="#5d6b7f")
        style.configure(
            "Primary.TButton", padding=(14, 8), font=("TkDefaultFont", 10, "bold")
        )
        style.configure("Destructive.TButton", foreground="#a61b1b", padding=(10, 6))
        style.configure("TButton", padding=(9, 6))
        style.configure("TEntry", padding=5)
        style.map("TButton", focuscolor=[("focus", "#1769aa")])

    def _create_scroll_area(self) -> None:
        shell = ttk.Frame(self)
        shell.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(shell, highlightthickness=0, background="#f5f7fa")
        scrollbar = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas)
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

    def _mousewheel(self, event: tk.Event) -> None:
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    @staticmethod
    def card(parent: tk.Widget, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=14)
        frame.pack(fill=tk.X, padx=18, pady=(0, 12))
        return frame

    @staticmethod
    def card_label(
        parent: tk.Widget, text: str = "", *, muted: bool = False, **kwargs
    ) -> ttk.Label:
        style = "Muted.Card.TLabel" if muted else "Card.TLabel"
        return ttk.Label(parent, text=text, style=style, **kwargs)

    def create_widgets(self, reuse_artifacts: bool) -> None:
        intro = ttk.Frame(self.content, padding=(18, 18, 18, 12))
        intro.pack(fill=tk.X)
        ttk.Label(
            intro,
            text="Fluorescence microscopy analysis",
            font=("TkDefaultFont", 16, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            intro,
            text="Set up a reproducible measurement, then review the generated scientific outputs.",
            style="Muted.Card.TLabel",
        ).pack(anchor=tk.W, pady=(3, 0))

        self._create_input_card()
        self._create_calibration_card()
        self._create_method_card()
        self._create_settings_card()
        self._create_existing_results_card(reuse_artifacts)
        self._create_run_card()
        self._create_completion_card()
        self._create_log_card()

    def _create_input_card(self) -> None:
        frame = self.card(self.content, "1. Choose experiment or plate")
        frame.columnconfigure(1, weight=1)
        self.card_label(
            frame,
            "Choose an experiment_* folder containing plate_* folders, or a single plate_* folder. Each plate needs info.xlsx and date_* image folders.",
            muted=True,
            wraplength=820,
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))
        self.browse_button = ttk.Button(
            frame,
            text="Choose folder…",
            command=self.pick_folder,
            style="Primary.TButton",
        )
        self.browse_button.grid(row=1, column=0, sticky=tk.W)
        self.selected_folder_var = tk.StringVar()
        self.folder_path_entry = ttk.Entry(
            frame, textvariable=self.selected_folder_var, state="readonly"
        )
        self.folder_path_entry.grid(row=1, column=1, sticky=tk.EW, padx=8)
        self.copy_path_button = ttk.Button(
            frame, text="Copy path", command=self.copy_selected_path
        )
        self.copy_path_button.grid(row=1, column=2, sticky=tk.E)
        self.folder_status = tk.Label(
            frame,
            anchor="w",
            justify=tk.LEFT,
            padx=9,
            pady=7,
            background="#eef2f6",
            foreground="#344256",
        )
        self.folder_status.grid(
            row=2, column=0, columnspan=3, sticky=tk.EW, pady=(10, 0)
        )
        self.folder_status_tooltip = ToolTip(self.folder_status)
        self.folder_details_var = tk.StringVar()
        self.card_label(
            frame, textvariable=self.folder_details_var, muted=True, wraplength=820
        ).grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))

    def _create_calibration_card(self) -> None:
        frame = self.card(self.content, "2. Confirm image calibration")
        frame.columnconfigure(2, weight=1)
        self.card_label(frame, "Micrometers per pixel").grid(
            row=0, column=0, sticky=tk.W
        )
        self.um_per_pixel_var = tk.StringVar(value="1")
        self.um_per_pixel_entry = ttk.Entry(
            frame, textvariable=self.um_per_pixel_var, width=13
        )
        self.um_per_pixel_entry.grid(row=0, column=1, sticky=tk.W, padx=(10, 16))
        self.calibration_source_var = tk.StringVar(value="Manual value")
        self.card_label(
            frame, textvariable=self.calibration_source_var, muted=True
        ).grid(row=0, column=2, sticky=tk.W)
        self.calibration_error_var = tk.StringVar()
        self.calibration_error = ttk.Label(
            frame,
            textvariable=self.calibration_error_var,
            style="Card.TLabel",
            foreground="#a61b1b",
        )
        self.calibration_error.grid(
            row=1, column=0, columnspan=3, sticky=tk.W, pady=(6, 0)
        )
        self.card_label(
            frame,
            "A valid value in cal.txt is loaded automatically and remains read-only. Without cal.txt, enter the calibration used for this image set.",
            muted=True,
            wraplength=820,
        ).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(4, 0))
        self.um_per_pixel_var.trace_add(
            "write", lambda *_args: self.validate_configuration()
        )

    def _create_method_card(self) -> None:
        frame = self.card(self.content, "3. Choose measurement method")
        self.method_var = tk.StringVar(value=self.default_measurement_config.method)
        for index, (key, data) in enumerate(METHODS.items()):
            row = ttk.Frame(frame, style="Card.TFrame")
            row.grid(row=index, column=0, sticky=tk.EW, pady=3)
            row.columnconfigure(1, weight=1)
            ttk.Radiobutton(
                row, variable=self.method_var, value=key, command=self.sync_config_ui
            ).grid(row=0, column=0, rowspan=2, sticky=tk.NW, padx=(0, 6))
            self.card_label(row, data["name"], font=("TkDefaultFont", 10, "bold")).grid(
                row=0, column=1, sticky=tk.W
            )
            if data["recommended"]:
                self.card_label(row, "Recommended default", muted=True).grid(
                    row=0, column=2, sticky=tk.W, padx=(8, 0)
                )
            self.card_label(row, data["summary"], muted=True).grid(
                row=1, column=1, columnspan=2, sticky=tk.W
            )
        self.method_help_visible = False
        self.method_help = self.card_label(
            frame,
            "Tip box reports a local signal near the tip. Centerline methods follow the traced root; Gaussian fitting can make noisy profiles more stable but is slower.",
            muted=True,
            wraplength=800,
        )
        ttk.Button(frame, text="Learn more", command=self.toggle_method_help).grid(
            row=3, column=0, sticky=tk.W, pady=(7, 0)
        )

    def _create_settings_card(self) -> None:
        self.settings_card = self.card(self.content, "4. Method settings")
        self.card_label(
            self.settings_card,
            "Only settings used by the selected measurement method are shown. Values are converted to pixels using the calibration above.",
            muted=True,
            wraplength=820,
        ).pack(anchor=tk.W, pady=(0, 8))
        self.settings_body = ttk.Frame(self.settings_card, style="Card.TFrame")
        self.settings_body.pack(fill=tk.X)
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
        for variable in self.setting_variables.values():
            variable.trace_add("write", lambda *_args: self.validate_configuration())
        self.setting_errors: dict[str, ttk.Label] = {}

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

    def _create_existing_results_card(self, reuse_artifacts: bool) -> None:
        frame = self.card(self.content, "5. Existing results")
        self.reuse_artifacts_var = tk.BooleanVar(value=reuse_artifacts)
        self.reuse_artifacts_check = ttk.Checkbutton(
            frame,
            text="Reuse valid previous results",
            variable=self.reuse_artifacts_var,
        )
        self.reuse_artifacts_check.grid(row=0, column=0, sticky=tk.W)
        self.card_label(
            frame,
            "When disabled, all measurements are recalculated even when compatible output files already exist.",
            muted=True,
            wraplength=820,
        ).grid(row=1, column=0, sticky=tk.W, pady=(2, 7))
        self.artifact_status_var = tk.StringVar(value="No folder selected.")
        self.card_label(frame, textvariable=self.artifact_status_var, muted=True).grid(
            row=2, column=0, sticky=tk.W
        )
        self.delete = ttk.Button(
            frame,
            text="Delete previous output…",
            command=self.delete_selected_folder,
            style="Destructive.TButton",
        )
        self.delete.grid(row=3, column=0, sticky=tk.W, pady=(8, 0))

    def _create_run_card(self) -> None:
        frame = self.card(self.content, "6. Run analysis")
        frame.columnconfigure(1, weight=1)
        self.analyze = ttk.Button(
            frame,
            text="Analyze",
            command=self.analyze_selected_folder,
            style="Primary.TButton",
        )
        self.analyze.grid(row=0, column=0, sticky=tk.W)
        self.operation_var = tk.StringVar(
            value="Waiting for a valid experiment or plate."
        )
        self.card_label(
            frame, textvariable=self.operation_var, muted=True, wraplength=600
        ).grid(row=0, column=1, sticky=tk.W, padx=12)
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=260)
        self.progress.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(10, 3))
        self.elapsed_var = tk.StringVar(value="Elapsed time: —")
        self.card_label(frame, textvariable=self.elapsed_var, muted=True).grid(
            row=2, column=0, columnspan=2, sticky=tk.W
        )
        self.card_label(
            frame,
            "Cancellation is unavailable after a run starts, so result files are never left half-written.",
            muted=True,
            wraplength=720,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(3, 0))

    def _create_completion_card(self) -> None:
        frame = self.card(self.content, "7. Results")
        self.completion_status = tk.Label(
            frame,
            anchor="w",
            justify=tk.LEFT,
            padx=9,
            pady=7,
            background="#eef2f6",
            foreground="#344256",
            text="Results will appear here after analysis.",
        )
        self.completion_status.pack(fill=tk.X)
        self.output_path_var = tk.StringVar()
        self.card_label(
            frame, textvariable=self.output_path_var, muted=True, wraplength=820
        ).pack(anchor=tk.W, pady=(8, 0))
        self.generated_files_var = tk.StringVar()
        self.card_label(
            frame, textvariable=self.generated_files_var, muted=True, wraplength=820
        ).pack(anchor=tk.W, pady=(3, 8))
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(fill=tk.X)
        self.open_output_button = ttk.Button(
            actions, text="Open output folder", command=self.open_output_folder
        )
        self.open_excel_button = ttk.Button(
            actions,
            text="Open Excel summary",
            command=lambda: self.open_output_file("Excel summary"),
        )
        self.open_pdf_button = ttk.Button(
            actions,
            text="Open PDF summary",
            command=lambda: self.open_output_file("PDF summary"),
        )
        self.copy_output_button = ttk.Button(
            actions, text="Copy output path", command=self.copy_output_path
        )
        for index, button in enumerate(
            (
                self.open_output_button,
                self.open_excel_button,
                self.open_pdf_button,
                self.copy_output_button,
            )
        ):
            button.grid(row=0, column=index, padx=(0, 7), sticky=tk.W)

    def _create_log_card(self) -> None:
        frame = self.card(self.content, "Technical details")
        self.log_visible = False
        self.log_toggle = ttk.Button(
            frame, text="Show technical log", command=self.toggle_log
        )
        self.log_toggle.pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(
            frame, height=11, state="disabled", wrap=tk.WORD, font=("TkFixedFont", 9)
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
        self.canvas.unbind_all("<MouseWheel>")
        pipio.logger.removeHandler(self.text_handler)
        self.destroy()

    def toggle_method_help(self) -> None:
        self.method_help_visible = not self.method_help_visible
        if self.method_help_visible:
            self.method_help.grid(row=4, column=0, sticky=tk.W, pady=(7, 0))
        else:
            self.method_help.grid_remove()

    def toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_text.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
            self.log_toggle.configure(text="Hide technical log")
        else:
            self.log_text.pack_forget()
            self.log_toggle.configure(text="Show technical log")

    def append_log(self, message: str, operation: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
        if self.is_busy:
            self.operation_var.set(operation)

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        self.config(cursor="watch" if busy else "")
        self.set_config_state(not busy)
        if busy:
            self.progress.start(12)
            self.run_started_at = time.time()
            self._update_elapsed()
        else:
            self.progress.stop()
            self.run_started_at = None
            self.refresh_folder_state()
        self.update_action_buttons_state()

    def _update_elapsed(self) -> None:
        if not self.is_busy or self.run_started_at is None:
            return
        elapsed = int(time.time() - self.run_started_at)
        self.elapsed_var.set(f"Elapsed time: {elapsed // 60:02d}:{elapsed % 60:02d}")
        self.after(500, self._update_elapsed)

    def set_config_state(self, enabled: bool) -> None:
        self.config_inputs_enabled = enabled
        self.browse_button.configure(state="normal" if enabled else "disabled")
        self.reuse_artifacts_check.configure(state="normal" if enabled else "disabled")
        self.apply_calibration_entry_state()
        self.sync_config_ui()

    def apply_calibration_entry_state(self) -> None:
        if not self.config_inputs_enabled:
            state = "disabled"
        elif self.calibration_editable:
            state = "normal"
        else:
            state = "readonly"
        self.um_per_pixel_entry.configure(state=state)

    def pick_folder(self) -> None:
        initial_dir = (
            str(self.selected_folder.parent) if self.selected_folder else self.data_dir
        )
        selected = filedialog.askdirectory(
            title="Choose experiment or plate folder",
            initialdir=initial_dir,
            mustexist=True,
        )
        if selected:
            self.selected_folder = pathlib.Path(selected)
            self.completion_files = {}
            self.output_path_var.set("")
            self.generated_files_var.set("")
            self.set_completion("Results will appear here after analysis.", "neutral")
            self.refresh_folder_state()

    def copy_selected_path(self) -> None:
        if self.selected_folder is None:
            return
        self.clipboard_clear()
        self.clipboard_append(str(self.selected_folder.resolve()))
        self.operation_var.set("Experiment path copied to clipboard.")

    def refresh_folder_state(self) -> None:
        self.folder_validation = validate_folder(self.selected_folder)
        if self.selected_folder is None:
            self.selected_folder_var.set("")
            self.selected_artifact_count = 0
            self.artifact_status_var.set("No folder selected.")
            self.set_calibration(1.0, editable=True, source="Manual value")
        else:
            path = self.selected_folder.resolve()
            self.selected_folder_var.set(middle_ellipsis(str(path)))
            self.selected_artifact_count = pipio.count_output_artifacts(
                self.selected_folder
            )
            self.artifact_status_var.set(
                f"Found {self.selected_artifact_count} previous output artifact{'s' if self.selected_artifact_count != 1 else ''}."
            )
            value, editable, source, error = self.resolve_calibration(
                self.selected_folder
            )
            self.set_calibration(value, editable=editable, source=source, error=error)
        self._show_folder_validation()
        self.validate_configuration()
        self.update_action_buttons_state()

    def _show_folder_validation(self) -> None:
        validation = self.folder_validation
        colors = {
            "valid": ("#e6f4ea", "#176b3a"),
            "invalid": ("#fdecec", "#a61b1b"),
            "inconsistent": ("#fff4d6", "#875b00"),
            "empty": ("#eef2f6", "#344256"),
        }
        background, foreground = colors[validation.state]
        self.folder_status.configure(
            text=validation_display_text(validation),
            background=background,
            foreground=foreground,
        )
        # Detailed hover text is available from the status result; do not
        # duplicate it in a second label below the result.
        self.folder_details_var.set("")
        self.folder_status_tooltip.set_text(validation_tooltip_text(validation))

    def parse_calibration_file(self, path: pathlib.Path) -> float:
        try:
            lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError as ex:
            raise ValueError(str(ex)) from ex
        if len(lines) != 1:
            raise ValueError("cal.txt must contain exactly one non-empty line")
        return self.parse_positive_float("cal.txt", lines[0])

    def resolve_calibration(self, folder: pathlib.Path) -> tuple[float, bool, str, str]:
        candidates: list[tuple[pathlib.Path, str]] = [
            (
                folder / "cal.txt",
                "Loaded from plate"
                if folder_kind(folder) == "plate"
                else "Loaded from experiment",
            )
        ]
        if (
            folder_kind(folder) == "plate"
            and folder_kind(folder.parent) == "experiment"
        ):
            candidates.append((folder.parent / "cal.txt", "Loaded from experiment"))
        for path, source in candidates:
            if not path.exists():
                continue
            try:
                return self.parse_calibration_file(path), False, source, ""
            except ValueError as ex:
                return (
                    1.0,
                    True,
                    "Manual value",
                    f"Invalid {path.name}: {ex}. Enter a value manually.",
                )
        return 1.0, True, "Manual value", ""

    def set_calibration(
        self, value: float, *, editable: bool, source: str, error: str = ""
    ) -> None:
        self.um_per_pixel_var.set(f"{value:g}")
        self.calibration_editable = editable
        self.calibration_source_var.set(source)
        self.calibration_error_var.set(error)
        self.apply_calibration_entry_state()

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
                    "Larger boxes average more tissue; smaller boxes focus more tightly on the tip.",
                ),
                (
                    "box_offset",
                    "Tip position offset",
                    "0 centers the box on the tip. Positive moves it toward the tip direction; negative moves it away.",
                ),
            ]
        return [
            (
                "perpendicular_width",
                "Profile width (µm)",
                "A wider profile averages more pixels across the root; a narrower one preserves local detail.",
            ),
            (
                "length",
                "Profile length (µm)",
                "A longer profile measures farther from the tip; a shorter one concentrates on the tip region.",
            ),
            (
                "savgol_window",
                "Root-shape smoothing (µm)",
                "Increase to smooth a noisier root trace; decrease to retain tighter bends.",
            ),
            (
                "intensity_savgol_window",
                "Signal smoothing (µm)",
                "Increase to smooth intensity noise; set to 0 to leave the signal unsmoothed.",
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
                row=row * 2, column=0, sticky=tk.W, padx=(0, 10), pady=(4, 0)
            )
            entry = ttk.Entry(
                self.settings_body, textvariable=self.setting_variables[key], width=14
            )
            entry.grid(row=row * 2, column=1, sticky=tk.W, pady=(4, 0))
            entry.configure(
                state="normal" if self.config_inputs_enabled else "disabled"
            )
            self.card_label(
                self.settings_body, helper, muted=True, wraplength=600
            ).grid(row=row * 2 + 1, column=0, columnspan=2, sticky=tk.W, pady=(1, 4))
            error = ttk.Label(
                self.settings_body, style="Card.TLabel", foreground="#a61b1b"
            )
            error.grid(row=row * 2, column=2, sticky=tk.W, padx=(10, 0))
            self.setting_errors[key] = error
        self.validate_configuration()

    def validate_configuration(self) -> bool:
        if not hasattr(self, "setting_errors"):
            return False
        active_settings = self._settings_for_method()
        # A variable trace may fire while sync_config_ui is rebuilding this
        # dictionary. Keep the Analyze button disabled until that short UI
        # transition has completed instead of addressing a missing label.
        if any(
            key not in self.setting_errors for key, _label, _helper in active_settings
        ):
            self.update_action_buttons_state(config_valid=False)
            return False
        errors: dict[str, str] = {}
        try:
            self.parse_positive_float(
                "Micrometers per pixel", self.um_per_pixel_var.get()
            )
        except (TypeError, ValueError) as ex:
            self.calibration_error_var.set(str(ex))
            errors["calibration"] = str(ex)
        else:
            if not self.calibration_error_var.get().startswith("Invalid cal.txt"):
                self.calibration_error_var.set("")
        rules: dict[str, Callable[[str, str], float]] = {
            "box_size": self.parse_positive_float,
            "box_offset": self.parse_finite_float,
            "perpendicular_width": self.parse_positive_float,
            "length": self.parse_positive_float,
            "savgol_window": self.parse_positive_float,
            "intensity_savgol_window": self.parse_nonnegative_float,
        }
        for key, _label, _helper in active_settings:
            try:
                rules[key](key.replace("_", " "), self.setting_variables[key].get())
                error_text = ""
            except (TypeError, ValueError) as ex:
                error_text = str(ex)
                errors[key] = error_text
            self.setting_errors[key].configure(text=error_text)
        self.update_action_buttons_state(config_valid=not errors)
        return not errors

    def update_action_buttons_state(self, config_valid: bool | None = None) -> None:
        if config_valid is None:
            config_valid = (
                self.validate_configuration()
                if hasattr(self, "setting_errors")
                else False
            )
        valid_selection = self.folder_validation.valid and config_valid
        if hasattr(self, "analyze"):
            if self.folder_validation.kind == "experiment":
                button_text = "Analyze experiment"
            elif self.folder_validation.kind == "plate":
                button_text = "Analyze plate"
            else:
                button_text = "Analyze"
            self.analyze.configure(
                text=button_text,
                state="normal" if valid_selection and not self.is_busy else "disabled",
            )
        if hasattr(self, "delete"):
            can_delete = (
                self.selected_folder is not None
                and self.selected_artifact_count > 0
                and not self.is_busy
            )
            self.delete.configure(state="normal" if can_delete else "disabled")
        if hasattr(self, "open_output_button"):
            has_folder = self.selected_folder is not None and bool(
                self.completion_files
            )
            self.open_output_button.configure(
                state="normal" if has_folder else "disabled"
            )
            self.copy_output_button.configure(
                state="normal" if has_folder else "disabled"
            )
            excel = self.completion_files.get("Excel summary")
            pdf = self.completion_files.get("PDF summary")
            self.open_excel_button.configure(
                state="normal" if excel and excel.exists() else "disabled"
            )
            self.open_pdf_button.configure(
                state="normal" if pdf and pdf.exists() else "disabled"
            )

    def get_measurement_config(self) -> MeasurementConfig:
        um_per_pixel = self.parse_positive_float(
            "Micrometers per pixel", self.um_per_pixel_var.get()
        )
        return MeasurementConfig(
            method=self.method_var.get(),  # type: ignore[arg-type]
            box_size=self.um_to_px(
                self.parse_positive_float(
                    "Measurement box size", self.box_size_var.get()
                ),
                um_per_pixel,
            ),
            box_offset=self.parse_finite_float(
                "Tip position offset", self.box_offset_var.get()
            ),
            perpendicular_width=self.um_to_px(
                self.parse_positive_float(
                    "Profile width", self.perpendicular_width_var.get()
                ),
                um_per_pixel,
            ),
            length=self.um_to_px(
                self.parse_positive_float("Profile length", self.length_var.get()),
                um_per_pixel,
            ),
            savgol_window=self.um_to_px(
                self.parse_positive_float(
                    "Root-shape smoothing", self.savgol_window_var.get()
                ),
                um_per_pixel,
            ),
            intensity_savgol_window=(
                0
                if self.parse_nonnegative_float(
                    "Signal smoothing", self.intensity_savgol_window_var.get()
                )
                == 0
                else self.um_to_px(
                    self.parse_nonnegative_float(
                        "Signal smoothing", self.intensity_savgol_window_var.get()
                    ),
                    um_per_pixel,
                )
            ),
        )

    def deletion_message(self) -> str:
        if self.selected_folder is None:
            return ""
        artifacts = list_output_artifacts(self.selected_folder)
        relative = [str(path.relative_to(self.selected_folder)) for path in artifacts]
        return (
            "The following generated outputs will be permanently deleted:\n\n"
            + "\n".join(relative)
        )

    def delete_selected_folder(self) -> None:
        if self.selected_folder is None or not self.selected_artifact_count:
            return
        if not self.confirm_delete():
            return
        self._begin_run("Deleting previous output…")
        folder = self.selected_folder
        threading.Thread(
            target=self._delete_worker, args=(folder,), daemon=True
        ).start()

    def confirm_delete(self) -> bool:
        """Use a scrollable confirmation dialog so every affected artifact is visible."""
        dialog = tk.Toplevel(self)
        dialog.title("Delete previous output?")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("640x420")
        result = tk.BooleanVar(value=False)
        ttk.Label(
            dialog, text="Delete generated output", font=("TkDefaultFont", 12, "bold")
        ).pack(anchor=tk.W, padx=16, pady=(16, 5))
        ttk.Label(
            dialog,
            text="This cannot be undone. Original images and input files will not be deleted.",
        ).pack(anchor=tk.W, padx=16)
        listing = scrolledtext.ScrolledText(dialog, height=14, wrap=tk.NONE)
        listing.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        listing.insert("1.0", self.deletion_message())
        listing.configure(state="disabled")
        buttons = ttk.Frame(dialog, padding=(16, 0, 16, 16))
        buttons.pack(fill=tk.X)

        def accept() -> None:
            result.set(True)
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(
            buttons, text="Delete output", command=accept, style="Destructive.TButton"
        ).pack(side=tk.RIGHT, padx=(0, 8))
        self.wait_window(dialog)
        return bool(result.get())

    def _delete_worker(self, folder: pathlib.Path) -> None:
        try:
            pipio.delete_cache(folder)
        except Exception as ex:
            self.after(
                0, self.finish_run, False, f"Could not delete previous output: {ex}"
            )
        else:
            self.after(0, self.finish_run, True, "Previous output was deleted.")

    def analyze_selected_folder(self) -> None:
        if (
            self.selected_folder is None
            or not self.folder_validation.valid
            or not self.validate_configuration()
        ):
            return
        try:
            config = self.get_measurement_config()
        except ValueError as ex:
            self.validate_configuration()
            messagebox.showerror("Invalid measurement settings", str(ex), parent=self)
            return
        self._begin_run("Preparing analysis…")
        folder = self.selected_folder
        func = (
            analyze.analyze_experiment_folder
            if self.folder_validation.kind == "experiment"
            else analyze.analyze_plate_folder
        )
        threading.Thread(
            target=self._analysis_worker,
            args=(func, folder, config, bool(self.reuse_artifacts_var.get())),
            daemon=True,
        ).start()

    def _begin_run(self, operation: str) -> None:
        self.clear_logs()
        self.text_handler.reset()
        self.formatter.started = time.time()
        self.operation_var.set(operation)
        self.elapsed_var.set("Elapsed time: 00:00")
        self.completion_files = {}
        self.output_path_var.set("")
        self.generated_files_var.set("")
        self.set_completion("Running analysis…", "neutral")
        self.set_busy(True)

    def _analysis_worker(
        self,
        func: Callable[[pathlib.Path, MeasurementConfig, bool], None | pathlib.Path],
        folder: pathlib.Path,
        config: MeasurementConfig,
        reuse: bool,
    ) -> None:
        try:
            func(folder, measurement_config=config, reuse_artifacts=reuse)
        except Exception as ex:
            self.after(
                0, self.finish_run, False, f"Analysis stopped unexpectedly: {ex}"
            )
            return
        self.after(0, self.finish_run, self.text_handler.error_count == 0, None)

    def finish_run(self, success: bool, message: str | None) -> None:
        folder = self.selected_folder
        self.set_busy(False)
        if folder is None:
            return
        self.completion_files = {
            name: path for name, path in output_paths(folder).items() if path.exists()
        }
        self.output_path_var.set(f"Output directory: {folder.resolve()}")
        generated = [
            f"{name}: {path.name}" for name, path in self.completion_files.items()
        ]
        self.generated_files_var.set(
            "Generated files: "
            + (
                " • ".join(generated)
                if generated
                else "No summary files were generated."
            )
        )
        summary, kind = completion_feedback(
            success, self.text_handler.error_count, message
        )
        self.set_completion(summary, kind)
        self.operation_var.set(
            "Analysis complete."
            if success
            else "Analysis finished with warnings or errors."
        )
        self.update_action_buttons_state()

    def set_completion(
        self, text: str, kind: Literal["success", "warning", "error", "neutral"]
    ) -> None:
        colors = {
            "success": ("#e6f4ea", "#176b3a"),
            "warning": ("#fff4d6", "#875b00"),
            "error": ("#fdecec", "#a61b1b"),
            "neutral": ("#eef2f6", "#344256"),
        }
        background, foreground = colors[kind]
        self.completion_status.configure(
            text=text, background=background, foreground=foreground
        )

    def open_output_folder(self) -> None:
        if self.selected_folder:
            self.open_path(self.selected_folder)

    def open_output_file(self, name: str) -> None:
        path = self.completion_files.get(name)
        if path and path.exists():
            self.open_path(path)

    @staticmethod
    def open_path(path: pathlib.Path) -> None:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def copy_output_path(self) -> None:
        if self.selected_folder is None:
            return
        self.clipboard_clear()
        self.clipboard_append(str(self.selected_folder.resolve()))
        self.operation_var.set("Output path copied to clipboard.")


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
            help="Reuse previously generated _output_ artifacts when valid.",
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
    reuse_artifacts: Annotated[
        bool, typer.Option("--reuse-artifacts/--force-recompute")
    ] = True,
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
    path = pathlib.Path(data_dir)
    print(path)
    if not reuse_artifacts:
        pipio.delete_cache(path)
    func = (
        analyze.analyze_experiment_folder
        if path.stem.startswith("experiment")
        else analyze.analyze_plate_folder
    )
    func(path, measurement_config=measurement_config, reuse_artifacts=reuse_artifacts)


if __name__ == "__main__":
    tapp()
