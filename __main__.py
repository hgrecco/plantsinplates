import os
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import logging
import time
import threading
import pathlib
from typing import Callable, Literal
import warnings
import matplotlib

matplotlib.use("Agg")

from plantsinplates import analyze, io

SPINNER_CHARS = ["◐", "◓", "◑", "◒"]
TITLE = f"Plants in Plates - Analyze Experiment or Plate Folder ({io.__version__})"


def spinner_task():
    idx = 0
    io.logger.info("The application is busy ...")
    while app.is_busy:
        state = SPINNER_CHARS[idx % len(SPINNER_CHARS)]
        app.after(0, lambda: app.title(f"{state} {TITLE}"))
        app.update_idletasks()
        idx += 1
        time.sleep(0.1)
    io.logger.info("The application is no longer busy.")
    app.title(TITLE)


def delete_in_background(folder: pathlib.Path):
    io.delete_cache(folder)
    app.after(0, lambda: app.set_busy(False))


def analyze_done(folder: pathlib.Path):
    content = app.log_text.get("1.0", tk.END)
    with open(folder / (io.PREFIX + "log.txt"), "w", encoding="utf-8") as f:
        f.write(content)
    app.set_busy(False)


def analyze_in_background(
    func: Callable[[pathlib.Path], None],
    folder: pathlib.Path,
):
    with warnings.filterwarnings(
        "ignore", message=".*The color list has more values.*", category=UserWarning
    ):
        func(folder)
    app.after(0, lambda: analyze_done(folder))


class TextHandler(logging.Handler):
    """Custom logging handler to write logs to a Tkinter Text widget."""

    def __init__(self, text_widget: scrolledtext.ScrolledText):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record: logging.LogRecord):
        # if record.name != io.logger.name:
        #     print(record.name)
        #     return
        msg = self.format(record) + "\n"
        self.text_widget.configure(state="normal")
        self.text_widget.insert(tk.END, msg)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")


class AnalyzeApp(tk.Tk):
    is_busy: bool = False

    def __init__(self):
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
        formatter = logging.Formatter(
            "%(relativeCreated).2f - %(levelname)s - %(message)s"
        )
        text_handler.setFormatter(formatter)
        io.logger.addHandler(text_handler)
        io.logger.setLevel(logging.INFO)

    def open_file_dialog(self, action: Literal["analyze", "delete"]):
        folder = filedialog.askdirectory(
            title="Select an experiment or plate directory to process",
            initialdir=os.environ.get("DATA_DIR", ""),
            mustexist=True,
        )

        path = pathlib.Path(folder)
        if not (path.stem.startswith("experiment") or path.stem.startswith("plate")):
            messagebox.showerror(
                message="Selected directory is neither an experiment nor a plate directory."
            )
            return

        if action == "delete":
            self.set_busy(True)
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

        self.set_busy(True)
        self.log_text.delete("1.0", tk.END)
        threading.Thread(
            target=analyze_in_background,
            args=(
                func,
                path,
            ),
            daemon=True,
        ).start()
        threading.Thread(target=spinner_task, daemon=True).start()


if __name__ == "__main__":
    app = AnalyzeApp()
    icon = tk.PhotoImage(width=16, height=16)
    icon.put("black", to=(0, 0, 16, 16))
    icon.put("white", to=(6, 0, 10, 10))
    app.iconphoto(True, icon)

    io.logger.info("Application started.")
    app.mainloop()
