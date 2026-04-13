import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import logging
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
    func: Callable[[pathlib.Path, MeasurementConfig], None | pathlib.Path],
    folder: pathlib.Path,
    measurement_config: MeasurementConfig,
):
    func(folder, measurement_config=measurement_config)
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


class AnalyzeApp(tk.Tk):
    is_busy: bool = False

    def __init__(self, data_dir: str, measurement_config: MeasurementConfig):
        super().__init__()

        self.title(TITLE)
        window_width = 600
        window_height = 400

        # Center the window
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x_coord = int((screen_width - window_width) / 2)
        y_coord = int((screen_height - window_height) / 2)
        self.geometry(f"{window_width}x{window_height}+{x_coord}+{y_coord}")

        self.checkbox_var = tk.BooleanVar()
        self.config_inputs_enabled = True

        self.data_dir = data_dir
        self.default_measurement_config = measurement_config

        self.create_widgets()
        self.setup_logging()

    def set_busy(self, busy: bool):
        """Set the cursor to a spinner if busy, otherwise reset it."""
        if busy:
            self.config(cursor="watch")
            self.analyze.config(state="disabled")
            self.delete.config(state="disabled")
            self.set_config_state(False)
        else:
            self.config(cursor="")
            self.analyze.config(state="normal")
            self.delete.config(state="normal")
            self.set_config_state(True)
        self.is_busy = busy

    def create_widgets(self):
        frame = ttk.Frame(self)
        frame.pack(pady=10)

        config_frame = ttk.LabelFrame(self, text="Measurement")
        config_frame.pack(padx=10, fill=tk.X)

        self.method_var = tk.StringVar(value=self.default_measurement_config.method)
        self.box_size_var = tk.StringVar(
            value=str(self.default_measurement_config.box_size)
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

        self.box_size_label = ttk.Label(config_frame, text="Box size [px]")
        self.box_size_label.grid(row=0, column=2, padx=5, pady=4, sticky=tk.W)
        self.box_size_entry = ttk.Entry(
            config_frame, textvariable=self.box_size_var, width=8
        )
        self.box_size_entry.grid(row=0, column=3, padx=5, pady=4, sticky=tk.W)

        self.perpendicular_width_label = ttk.Label(
            config_frame, text="Perpendicular width [px]"
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

        self.length_label = ttk.Label(config_frame, text="Length [px]")
        self.length_label.grid(row=1, column=2, padx=5, pady=4, sticky=tk.W)
        self.length_entry = ttk.Entry(
            config_frame, textvariable=self.length_var, width=8
        )
        self.length_entry.grid(row=1, column=3, padx=5, pady=4, sticky=tk.W)

        self.savgol_window_label = ttk.Label(
            config_frame, text="Savitzky-Golay window [px]"
        )
        self.savgol_window_label.grid(row=1, column=4, padx=5, pady=4, sticky=tk.W)
        self.savgol_window_entry = ttk.Entry(
            config_frame, textvariable=self.savgol_window_var, width=8
        )
        self.savgol_window_entry.grid(row=1, column=5, padx=5, pady=4, sticky=tk.W)
        self.sync_config_ui()

        self.delete = ttk.Button(
            frame,
            text="Delete previous output",
            command=lambda: self.open_file_dialog(action="delete"),
        )
        self.delete.grid(row=0, column=0, padx=5)

        self.analyze = ttk.Button(
            frame,
            text="Analyze folder",
            command=lambda: self.open_file_dialog(action="analyze"),
        )
        self.analyze.grid(row=0, column=1, padx=5)

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
        self.sync_config_ui()

    def sync_config_ui(self):
        method = self.method_var.get()
        is_box = method == "box"
        input_state = "normal" if self.config_inputs_enabled else "disabled"

        if is_box:
            self.box_size_label.grid()
            self.box_size_entry.grid()
            self.box_size_entry.configure(state=input_state)

            self.perpendicular_width_label.grid_remove()
            self.perpendicular_width_entry.grid_remove()
            self.length_label.grid_remove()
            self.length_entry.grid_remove()
            self.savgol_window_label.grid_remove()
            self.savgol_window_entry.grid_remove()
        else:
            self.box_size_label.grid_remove()
            self.box_size_entry.grid_remove()

            self.perpendicular_width_label.grid()
            self.perpendicular_width_entry.grid()
            self.perpendicular_width_entry.configure(state=input_state)
            self.length_label.grid()
            self.length_entry.grid()
            self.length_entry.configure(state=input_state)
            self.savgol_window_label.grid()
            self.savgol_window_entry.grid()
            self.savgol_window_entry.configure(state=input_state)

    def get_measurement_config(self) -> MeasurementConfig:
        return MeasurementConfig(
            method=self.method_var.get(),  # type: ignore[arg-type]
            box_size=int(self.box_size_var.get()),
            perpendicular_width=int(self.perpendicular_width_var.get()),
            length=int(self.length_var.get()),
            savgol_window=int(self.savgol_window_var.get()),
        )

    def open_file_dialog(self, action: Literal["analyze", "delete"]):
        folder = filedialog.askdirectory(
            title="Select an experiment or plate directory to process",
            initialdir=self.data_dir,
            mustexist=True,
        )

        path = pathlib.Path(folder)
        if not (path.stem.startswith("experiment") or path.stem.startswith("plate")):
            messagebox.showerror(
                message="Selected directory is neither an experiment nor a plate directory."
            )
            return

        if action == "delete":
            self.formatter.started = time.time()
            self.set_busy(True)

            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")

            threading.Thread(
                target=delete_in_background,
                args=(path,),
                daemon=True,
            ).start()
            threading.Thread(target=spinner_task, daemon=True).start()
            return

        if path.stem.startswith("experiment"):
            func = analyze.analyze_experiment_folder
        else:  # if path.stem.startswith("plate"):
            func = analyze.analyze_plate_folder

        try:
            measurement_config = self.get_measurement_config()
        except Exception as ex:
            messagebox.showerror(message=f"Invalid measurement configuration: {ex}")
            return

        self.formatter.started = time.time()
        self.set_busy(True)

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

        threading.Thread(
            target=analyze_in_background,
            args=(
                func,
                path,
                measurement_config,
            ),
            daemon=True,
        ).start()
        threading.Thread(target=spinner_task, daemon=True).start()


app = None
tapp = typer.Typer()


def build_measurement_config(
    method: str,
    box_size: int,
    perpendicular_width: int,
    length: int,
    savgol_window: int,
) -> MeasurementConfig:
    try:
        return MeasurementConfig(
            method=method,
            box_size=box_size,
            perpendicular_width=perpendicular_width,
            length=length,
            savgol_window=savgol_window,
        )
    except ValueError as ex:
        raise typer.BadParameter(str(ex)) from ex


@tapp.command()
def gui(
    data_dir: Annotated[str, typer.Argument(envvar="DATA_DIR")] = "",
    method: Annotated[str, typer.Option("--method")] = "box",
    box_size: Annotated[int, typer.Option("--box-size")] = 680,
    perpendicular_width: Annotated[int, typer.Option("--perpendicular-width")] = 3,
    length: Annotated[int, typer.Option("--length")] = 10,
    savgol_window: Annotated[int, typer.Option("--savgol-window")] = 100,
):
    global app

    import matplotlib

    matplotlib.use("Agg")
    measurement_config = build_measurement_config(
        method=method,
        box_size=box_size,
        perpendicular_width=perpendicular_width,
        length=length,
        savgol_window=savgol_window,
    )
    app = AnalyzeApp(data_dir, measurement_config=measurement_config)
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
    perpendicular_width: Annotated[int, typer.Option("--perpendicular-width")] = 3,
    length: Annotated[int, typer.Option("--length")] = 10,
    savgol_window: Annotated[int, typer.Option("--savgol-window")] = 100,
):
    measurement_config = build_measurement_config(
        method=method,
        box_size=box_size,
        perpendicular_width=perpendicular_width,
        length=length,
        savgol_window=savgol_window,
    )
    path = pathlib.Path(data_dir)
    print(path)
    pipio.delete_cache(path)
    if path.stem.startswith("experiment"):
        func = analyze.analyze_experiment_folder
    else:  # if path.stem.startswith("plate"):
        func = analyze.analyze_plate_folder
    func(path, measurement_config=measurement_config)


if __name__ == "__main__":
    tapp()
