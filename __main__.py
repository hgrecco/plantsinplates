import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import logging
import math
import time
import threading
import pathlib
from typing import Annotated, Callable, Literal
import warnings

import typer

from plantsinplates import analyze, io as pipio
from plantsinplates.measurement_config import MeasurementConfig

warnings.filterwarnings(
    "ignore", message=".*The color list has more values.*", category=UserWarning
)

SPINNER_CHARS = ["◐", "◓", "◑", "◒"]
TITLE = f"Plants in Plates - Analyze Experiment or Plate Folder ({pipio.__version__})"


def spinner_task():
    idx = 0
    pipio.logger.info("The application is busy ...")
    while app.is_busy:
        state = SPINNER_CHARS[idx % len(SPINNER_CHARS)]
        app.after(0, lambda: app.title(f"{state} {TITLE}"))
        app.update_idletasks()
        idx += 1
        time.sleep(0.1)
    pipio.logger.info("The application is no longer busy.")
    app.title(TITLE)


def delete_in_background(folder: pathlib.Path):
    pipio.delete_cache(folder)
    app.after(0, lambda: app.set_busy(False))


def analyze_done(folder: pathlib.Path):
    content = app.log_text.get("1.0", tk.END)
    with open(folder / (pipio.PREFIX + "log.txt"), "w", encoding="utf-8") as f:
        f.write(content)
    app.set_busy(False)


def analyze_in_background(
    func: Callable[[pathlib.Path, MeasurementConfig, bool], None | pathlib.Path],
    folder: pathlib.Path,
    measurement_config: MeasurementConfig,
    reuse_artifacts: bool,
):
    func(folder, measurement_config=measurement_config, reuse_artifacts=reuse_artifacts)
    app.after(0, lambda: analyze_done(folder))


class RelativeTimeFormatter(logging.Formatter):
    started = 0

    def format(self, record) -> str:
        total_seconds = record.created - self.started

        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)

        millis = int((total_seconds - int(total_seconds)) * 1000)

        time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

        record.relative_hms = time_str  # Add custom attribute for formatting

        return super().format(record)


class TextHandler(logging.Handler):
    """Custom logging handler to write logs to a Tkinter Text widget."""

    def __init__(self, text_widget: scrolledtext.ScrolledText):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record: logging.LogRecord):
        # if record.name != pipio.logger.name:
        #     print(record.name)
        #     return
        msg = self.format(record) + "\n"
        self.text_widget.configure(state="normal")
        self.text_widget.insert(tk.END, msg)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")


class ToolTip:
    """Simple tooltip attached to a widget."""

    def __init__(self, widget: tk.Widget, text: str = ""):
        self.widget = widget
        self.text = text
        self.tip_window: tk.Toplevel | None = None
        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)
        self.widget.bind("<ButtonPress>", self.hide)

    def set_text(self, text: str):
        self.text = text

    def show(self, _event=None):
        if self.tip_window is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True)
        self.tip_window.wm_geometry(f"+{x}+{y}")
        ttk.Label(
            self.tip_window,
            text=self.text,
            relief="solid",
            borderwidth=1,
            padding=4,
        ).pack()

    def hide(self, _event=None):
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


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
        window_width = 860
        window_height = 400

        # Center the window
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x_coord = int((screen_width - window_width) / 2)
        y_coord = int((screen_height - window_height) / 2)
        self.geometry(f"{window_width}x{window_height}+{x_coord}+{y_coord}")

        self.config_inputs_enabled = True
        self.calibration_editable = True
        self.calibration_default_message = (
            "Calibration not found; using default/editable value."
        )
        self.selected_folder: pathlib.Path | None = None
        self.selected_folder_kind: Literal["experiment", "plate"] | None = None
        self.selected_has_artifacts = False
        self.selected_artifact_count = 0

        self.data_dir = data_dir
        self.default_measurement_config = measurement_config
        self.default_reuse_artifacts = reuse_artifacts

        self.create_widgets()
        self.setup_logging()
        self.refresh_folder_state()

    def set_busy(self, busy: bool):
        """Set the cursor to a spinner if busy, otherwise reset it."""
        self.is_busy = busy
        if busy:
            self.config(cursor="watch")
            self.browse_button.config(state="disabled")
            self.set_config_state(False)
        else:
            self.config(cursor="")
            self.browse_button.config(state="normal")
            self.set_config_state(True)
            self.refresh_folder_state()
        self.apply_calibration_entry_state()
        self.update_action_buttons_state()

    def create_widgets(self):
        folder_frame = ttk.LabelFrame(self, text="Folder")
        folder_frame.pack(padx=10, pady=6, fill=tk.X)
        folder_frame.columnconfigure(1, weight=1)

        self.selected_folder_var = tk.StringVar(value="")
        self.folder_status_var = tk.StringVar(
            value="Select an experiment_* or plate_* folder."
        )

        self.browse_button = ttk.Button(
            folder_frame,
            text="Browse...",
            command=self.pick_folder,
        )
        self.browse_button.grid(row=0, column=0, padx=5, pady=4, sticky=tk.W)

        self.folder_path_entry = ttk.Entry(
            folder_frame,
            textvariable=self.selected_folder_var,
            state="readonly",
            width=95,
        )
        self.folder_path_entry.grid(row=0, column=1, padx=5, pady=4, sticky=tk.EW)

        ttk.Label(folder_frame, textvariable=self.folder_status_var).grid(
            row=1, column=0, columnspan=2, padx=5, pady=2, sticky=tk.W
        )

        action_frame = ttk.Frame(self)
        action_frame.pack(pady=6, padx=10, fill=tk.X)

        config_frame = ttk.LabelFrame(self, text="Measurement")
        config_frame.pack(padx=10, fill=tk.X)

        self.method_var = tk.StringVar(value=self.default_measurement_config.method)
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

        ttk.Label(config_frame, text="Method").grid(
            row=0, column=0, padx=5, pady=4, sticky=tk.W
        )
        self.method_combo = ttk.Combobox(
            config_frame,
            textvariable=self.method_var,
            values=["box", "centerline"],
            state="readonly",
            width=12,
        )
        self.method_combo.grid(row=0, column=1, padx=5, pady=4, sticky=tk.W)
        self.method_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.sync_config_ui()
        )

        self.box_size_label = ttk.Label(config_frame, text="Box size [um]")
        self.box_size_label.grid(row=0, column=2, padx=5, pady=4, sticky=tk.W)
        self.box_size_entry = ttk.Entry(
            config_frame, textvariable=self.box_size_var, width=8
        )
        self.box_size_entry.grid(row=0, column=3, padx=5, pady=4, sticky=tk.W)

        self.box_offset_label = ttk.Label(config_frame, text="Box offset [box]")
        self.box_offset_label.grid(row=0, column=4, padx=5, pady=4, sticky=tk.W)
        self.box_offset_entry = ttk.Entry(
            config_frame, textvariable=self.box_offset_var, width=8
        )
        self.box_offset_entry.grid(row=0, column=5, padx=5, pady=4, sticky=tk.W)

        self.perpendicular_width_label = ttk.Label(
            config_frame, text="Perpendicular width [um]"
        )
        self.perpendicular_width_label.grid(
            row=1, column=0, padx=5, pady=4, sticky=tk.W
        )
        self.perpendicular_width_entry = ttk.Entry(
            config_frame, textvariable=self.perpendicular_width_var, width=8
        )
        self.perpendicular_width_entry.grid(
            row=1, column=1, padx=5, pady=4, sticky=tk.W
        )

        self.length_label = ttk.Label(config_frame, text="Length [um]")
        self.length_label.grid(row=1, column=2, padx=5, pady=4, sticky=tk.W)
        self.length_entry = ttk.Entry(
            config_frame, textvariable=self.length_var, width=8
        )
        self.length_entry.grid(row=1, column=3, padx=5, pady=4, sticky=tk.W)

        self.savgol_window_label = ttk.Label(
            config_frame, text="Shape smooth window [um]"
        )
        self.savgol_window_label.grid(row=1, column=4, padx=5, pady=4, sticky=tk.W)
        self.savgol_window_entry = ttk.Entry(
            config_frame, textvariable=self.savgol_window_var, width=8
        )
        self.savgol_window_entry.grid(row=1, column=5, padx=5, pady=4, sticky=tk.W)
        self.intensity_savgol_window_label = ttk.Label(
            config_frame, text="Profile smooth window [um]"
        )
        self.intensity_savgol_window_label.grid(
            row=2, column=0, padx=5, pady=4, sticky=tk.W
        )
        self.intensity_savgol_window_entry = ttk.Entry(
            config_frame, textvariable=self.intensity_savgol_window_var, width=8
        )
        self.intensity_savgol_window_entry.grid(
            row=2, column=1, padx=5, pady=4, sticky=tk.W
        )
        self.sync_config_ui()

        self.um_per_pixel_var = tk.StringVar(value="1")
        self.calibration_label = ttk.Label(action_frame, text="Calibration [um/pixel]")
        self.calibration_label.grid(row=0, column=2, padx=(18, 5), pady=4, sticky=tk.W)
        self.um_per_pixel_entry = ttk.Entry(
            action_frame, textvariable=self.um_per_pixel_var, width=10
        )
        self.um_per_pixel_entry.grid(row=0, column=3, padx=5, pady=4, sticky=tk.W)
        self.calibration_tooltip = ToolTip(
            self.um_per_pixel_entry, self.calibration_default_message
        )

        self.delete = ttk.Button(
            action_frame,
            text="Delete previous output",
            command=self.delete_selected_folder,
        )
        self.delete.grid(row=0, column=0, padx=5)

        self.analyze = ttk.Button(
            action_frame,
            text="Analyze folder",
            command=self.analyze_selected_folder,
        )
        self.analyze.grid(row=0, column=1, padx=5)

        self.reuse_artifacts_var = tk.BooleanVar(value=self.default_reuse_artifacts)
        self.reuse_artifacts_check = ttk.Checkbutton(
            action_frame,
            text="Reuse output artifacts",
            variable=self.reuse_artifacts_var,
        )
        self.reuse_artifacts_check.grid(
            row=1, column=0, columnspan=4, padx=5, pady=(0, 4), sticky=tk.W
        )

        self.log_text = scrolledtext.ScrolledText(
            self, width=70, height=20, state="disabled"
        )
        self.log_text.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

    def setup_logging(self):
        text_handler = TextHandler(self.log_text)
        self.formatter = RelativeTimeFormatter(
            "%(relative_hms)s - %(levelname)s - %(message)s"
        )
        text_handler.setFormatter(self.formatter)
        pipio.logger.addHandler(text_handler)
        pipio.logger.setLevel(logging.INFO)

    def set_config_state(self, enabled: bool):
        self.config_inputs_enabled = enabled
        self.method_combo.configure(state="readonly" if enabled else "disabled")
        self.reuse_artifacts_check.configure(state="normal" if enabled else "disabled")
        self.sync_config_ui()
        self.apply_calibration_entry_state()

    def sync_config_ui(self):
        method = self.method_var.get()
        is_box = method == "box"
        input_state = "normal" if self.config_inputs_enabled else "disabled"

        if is_box:
            self.box_size_label.grid()
            self.box_size_entry.grid()
            self.box_size_entry.configure(state=input_state)
            self.box_offset_label.grid()
            self.box_offset_entry.grid()
            self.box_offset_entry.configure(state=input_state)

            self.perpendicular_width_label.grid_remove()
            self.perpendicular_width_entry.grid_remove()
            self.length_label.grid_remove()
            self.length_entry.grid_remove()
            self.savgol_window_label.grid_remove()
            self.savgol_window_entry.grid_remove()
            self.intensity_savgol_window_label.grid_remove()
            self.intensity_savgol_window_entry.grid_remove()
        else:
            self.box_size_label.grid_remove()
            self.box_size_entry.grid_remove()
            self.box_offset_label.grid_remove()
            self.box_offset_entry.grid_remove()

            self.perpendicular_width_label.grid()
            self.perpendicular_width_entry.grid()
            self.perpendicular_width_entry.configure(state=input_state)
            self.length_label.grid()
            self.length_entry.grid()
            self.length_entry.configure(state=input_state)
            self.savgol_window_label.grid()
            self.savgol_window_entry.grid()
            self.savgol_window_entry.configure(state=input_state)
            self.intensity_savgol_window_label.grid()
            self.intensity_savgol_window_entry.grid()
            self.intensity_savgol_window_entry.configure(state=input_state)

    def apply_calibration_entry_state(self):
        if not self.config_inputs_enabled:
            state = "disabled"
        elif self.calibration_editable:
            state = "normal"
        else:
            state = "readonly"
        self.um_per_pixel_entry.configure(state=state)

    @staticmethod
    def folder_kind(path: pathlib.Path) -> Literal["experiment", "plate"] | None:
        if path.stem.startswith("experiment"):
            return "experiment"
        if path.stem.startswith("plate"):
            return "plate"
        return None

    def update_action_buttons_state(self):
        folder_is_valid = (
            self.selected_folder is not None and self.selected_folder_kind is not None
        )
        self.analyze.config(
            state="normal" if (folder_is_valid and not self.is_busy) else "disabled"
        )
        self.delete.config(
            state=(
                "normal"
                if (
                    folder_is_valid and self.selected_has_artifacts and not self.is_busy
                )
                else "disabled"
            )
        )

    def pick_folder(self):
        if self.selected_folder is not None:
            initial_dir = str(self.selected_folder.parent)
        else:
            initial_dir = self.data_dir

        folder = filedialog.askdirectory(
            title="Select an experiment or plate directory to process",
            initialdir=initial_dir,
            mustexist=True,
        )
        if not folder:
            return
        self.selected_folder = pathlib.Path(folder)
        self.refresh_folder_state()

    def refresh_folder_state(self):
        if self.selected_folder is None:
            self.selected_folder_var.set("")
            self.folder_status_var.set("Select an experiment_* or plate_* folder.")
            self.selected_folder_kind = None
            self.selected_has_artifacts = False
            self.selected_artifact_count = 0
            self.set_calibration(
                1.0, editable=True, tooltip=self.calibration_default_message
            )
            self.update_action_buttons_state()
            return

        selected_path = self.selected_folder.resolve()
        self.selected_folder_var.set(str(selected_path))
        self.selected_folder_kind = self.folder_kind(self.selected_folder)

        if self.selected_folder_kind is None:
            self.selected_has_artifacts = False
            self.selected_artifact_count = 0
            self.folder_status_var.set(
                "Selected directory is neither an experiment_* nor a plate_* folder."
            )
            self.set_calibration(
                1.0, editable=True, tooltip=self.calibration_default_message
            )
            self.update_action_buttons_state()
            return

        self.selected_artifact_count = pipio.count_output_artifacts(
            self.selected_folder
        )
        self.selected_has_artifacts = self.selected_artifact_count > 0
        if self.selected_has_artifacts:
            self.folder_status_var.set(
                f"Selected {self.selected_folder_kind} folder. "
                f"Found {self.selected_artifact_count} output artifact(s)."
            )
        else:
            self.folder_status_var.set(
                f"Selected {self.selected_folder_kind} folder. No output artifacts found."
            )

        cal_value, editable, tooltip = self.resolve_calibration(self.selected_folder)
        self.set_calibration(cal_value, editable=editable, tooltip=tooltip)
        self.update_action_buttons_state()

    def parse_calibration_file(self, path: pathlib.Path) -> float:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as ex:
            raise ValueError(str(ex)) from ex

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) != 1:
            raise ValueError("cal.txt must contain exactly one non-empty line")

        value = float(lines[0])
        if not math.isfinite(value) or value <= 0:
            raise ValueError("cal.txt value must be a finite float > 0")
        return value

    def resolve_calibration(self, folder: pathlib.Path) -> tuple[float, bool, str]:
        local_cal = folder / "cal.txt"
        if local_cal.exists():
            try:
                value = self.parse_calibration_file(local_cal)
                return value, False, f"Loaded from {local_cal.resolve()}"
            except ValueError:
                return (
                    1.0,
                    True,
                    "Calibration not loaded (invalid cal.txt); using default/editable value.",
                )

        if self.selected_folder_kind == "plate":
            parent = folder.parent
            if self.folder_kind(parent) == "experiment":
                parent_cal = parent / "cal.txt"
                if parent_cal.exists():
                    try:
                        value = self.parse_calibration_file(parent_cal)
                        return value, False, f"Loaded from {parent_cal.resolve()}"
                    except ValueError:
                        return (
                            1.0,
                            True,
                            "Calibration not loaded (invalid cal.txt); using default/editable value.",
                        )

        return 1.0, True, self.calibration_default_message

    def set_calibration(self, value: float, *, editable: bool, tooltip: str):
        self.um_per_pixel_var.set(f"{value:g}")
        self.calibration_editable = editable
        self.calibration_tooltip.set_text(tooltip)
        self.apply_calibration_entry_state()

    @staticmethod
    def parse_positive_float(name: str, value: str) -> float:
        parsed = float(value)
        if (not math.isfinite(parsed)) or parsed <= 0:
            raise ValueError(f"{name} must be a finite number > 0")
        return parsed

    @staticmethod
    def parse_finite_float(name: str, value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{name} must be finite")
        return parsed

    @staticmethod
    def parse_nonnegative_float(name: str, value: str) -> float:
        parsed = float(value)
        if (not math.isfinite(parsed)) or parsed < 0:
            raise ValueError(f"{name} must be a finite number >= 0")
        return parsed

    @staticmethod
    def um_to_px(value_um: float, um_per_pixel: float) -> int:
        pixels = int(math.floor((value_um / um_per_pixel) + 0.5))
        return max(1, pixels)

    def get_measurement_config(self) -> MeasurementConfig:
        um_per_pixel = self.parse_positive_float(
            "um/pixel", self.um_per_pixel_var.get()
        )

        box_size_um = self.parse_positive_float(
            "box_size [um]", self.box_size_var.get()
        )
        perpendicular_width_um = self.parse_positive_float(
            "perpendicular_width [um]", self.perpendicular_width_var.get()
        )
        length_um = self.parse_positive_float("length [um]", self.length_var.get())
        savgol_window_um = self.parse_positive_float(
            "savgol_window [um]", self.savgol_window_var.get()
        )
        intensity_savgol_window_um = self.parse_nonnegative_float(
            "intensity_savgol_window [um]", self.intensity_savgol_window_var.get()
        )
        box_offset = self.parse_finite_float(
            "box_offset [box]", self.box_offset_var.get()
        )

        return MeasurementConfig(
            method=self.method_var.get(),  # type: ignore[arg-type]
            box_size=self.um_to_px(box_size_um, um_per_pixel),
            box_offset=box_offset,
            perpendicular_width=self.um_to_px(perpendicular_width_um, um_per_pixel),
            length=self.um_to_px(length_um, um_per_pixel),
            savgol_window=self.um_to_px(savgol_window_um, um_per_pixel),
            intensity_savgol_window=(
                0
                if intensity_savgol_window_um == 0
                else self.um_to_px(intensity_savgol_window_um, um_per_pixel)
            ),
        )

    def clear_logs(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def delete_selected_folder(self):
        if self.selected_folder is None or self.selected_folder_kind is None:
            return
        if not self.selected_has_artifacts:
            return

        self.formatter.started = time.time()
        self.set_busy(True)
        self.clear_logs()

        threading.Thread(
            target=delete_in_background,
            args=(self.selected_folder,),
            daemon=True,
        ).start()
        threading.Thread(target=spinner_task, daemon=True).start()

    def analyze_selected_folder(self):
        if self.selected_folder is None or self.selected_folder_kind is None:
            return

        if self.selected_folder_kind == "experiment":
            func = analyze.analyze_experiment_folder
        else:
            func = analyze.analyze_plate_folder

        try:
            measurement_config = self.get_measurement_config()
        except Exception as ex:
            messagebox.showerror(message=f"Invalid measurement configuration: {ex}")
            return
        reuse_artifacts = bool(self.reuse_artifacts_var.get())

        self.formatter.started = time.time()
        self.set_busy(True)
        self.clear_logs()

        threading.Thread(
            target=analyze_in_background,
            args=(
                func,
                self.selected_folder,
                measurement_config,
                reuse_artifacts,
            ),
            daemon=True,
        ).start()
        threading.Thread(target=spinner_task, daemon=True).start()


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
    measurement_config = build_measurement_config(
        method=method,
        box_size=box_size,
        box_offset=box_offset,
        perpendicular_width=perpendicular_width,
        length=length,
        savgol_window=savgol_window,
        intensity_savgol_window=intensity_savgol_window,
    )
    app = AnalyzeApp(
        data_dir,
        measurement_config=measurement_config,
        reuse_artifacts=reuse_artifacts,
    )
    icon = tk.PhotoImage(width=16, height=16)
    icon.put("black", to=(0, 0, 16, 16))
    icon.put("white", to=(6, 0, 10, 10))
    app.iconphoto(True, icon)

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
    measurement_config = build_measurement_config(
        method=method,
        box_size=box_size,
        box_offset=box_offset,
        perpendicular_width=perpendicular_width,
        length=length,
        savgol_window=savgol_window,
        intensity_savgol_window=intensity_savgol_window,
    )
    path = pathlib.Path(data_dir)
    print(path)
    if not reuse_artifacts:
        pipio.delete_cache(path)
    if path.stem.startswith("experiment"):
        func = analyze.analyze_experiment_folder
    else:  # if path.stem.startswith("plate"):
        func = analyze.analyze_plate_folder
    func(path, measurement_config=measurement_config, reuse_artifacts=reuse_artifacts)


if __name__ == "__main__":
    tapp()
