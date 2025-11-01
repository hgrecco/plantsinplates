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
    func: Callable[[pathlib.Path], None],
    folder: pathlib.Path,
):
    func(folder)
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

    def __init__(self, data_dir: str):
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

        self.data_dir = data_dir

        self.create_widgets()
        self.setup_logging()

    def set_busy(self, busy: bool):
        """Set the cursor to a spinner if busy, otherwise reset it."""
        if busy:
            self.config(cursor="watch")
            self.analyze.config(state="disabled")
            self.delete.config(state="disabled")
        else:
            self.config(cursor="")
            self.analyze.config(state="normal")
            self.delete.config(state="normal")
        self.is_busy = busy

    def create_widgets(self):
        frame = ttk.Frame(self)
        frame.pack(pady=10)

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
            ),
            daemon=True,
        ).start()
        threading.Thread(target=spinner_task, daemon=True).start()


app = None
tapp = typer.Typer()


@tapp.command()
def gui(data_dir: Annotated[str, typer.Argument(envvar="DATA_DIR")] = ""):
    global app

    import matplotlib

    matplotlib.use("Agg")
    app = AnalyzeApp(data_dir)
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
def test(data_dir: Annotated[str, typer.Argument(envvar="DATA_DIR")] = ""):
    path = pathlib.Path(data_dir)
    print(path)
    pipio.delete_cache(path)
    if path.stem.startswith("experiment"):
        func = analyze.analyze_experiment_folder
    else:  # if path.stem.startswith("plate"):
        func = analyze.analyze_plate_folder
    func(path)


if __name__ == "__main__":
    tapp()
