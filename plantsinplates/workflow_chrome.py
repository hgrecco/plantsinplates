"""
Reusable PlantinPlates workflow chrome built with ttk + ttkbootstrap.

This module contains:

- WorkflowSidebar
- ApplicationFooter
- StepDefinition
- StepState
- StatusKind

Typical integration:

    self.rowconfigure(0, weight=1)
    self.columnconfigure(1, weight=1)

    self.sidebar = WorkflowSidebar(
        self,
        steps=STEPS,
        on_select=self.show_step,
    )
    self.sidebar.grid(row=0, column=0, sticky="ns")

    self.content = ttk.Frame(self, padding=24)
    self.content.grid(row=0, column=1, sticky="nsew")

    self.footer = ApplicationFooter(
        self,
        on_back=self.go_back,
        on_continue=self.go_forward,
    )
    self.footer.grid(
        row=1,
        column=0,
        columnspan=2,
        sticky="ew",
    )
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
import pathlib
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from typing import Any

import ttkbootstrap as ttk


class StepState(str, Enum):
    """Visual state of a workflow step."""

    COMPLETED = "completed"
    CURRENT = "current"
    UPCOMING = "upcoming"
    ERROR = "error"


class StatusKind(str, Enum):
    """Semantic presentation used by the footer status message."""

    READY = "ready"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class StepDefinition:
    """Static information displayed for one workflow step."""

    key: str
    number: int
    title: str
    subtitle: str = ""
    icon: str = ""


def _theme_color(colors: Any, name: str, fallback: str) -> str:
    """Read a ttkbootstrap theme color with a safe fallback."""

    value = getattr(colors, name, None)
    return value if isinstance(value, str) and value else fallback


def _blend_color(
    widget: tk.Misc,
    foreground: str,
    background: str,
    amount: float,
) -> str:
    """Blend foreground into background by an amount from 0.0 to 1.0."""

    amount = max(0.0, min(1.0, amount))
    fg_r, fg_g, fg_b = widget.winfo_rgb(foreground)
    bg_r, bg_g, bg_b = widget.winfo_rgb(background)

    def blend_channel(foreground_channel: int, background_channel: int) -> int:
        value = background_channel + (foreground_channel - background_channel) * amount
        return round(value / 257)

    return (
        f"#{blend_channel(fg_r, bg_r):02x}"
        f"{blend_channel(fg_g, bg_g):02x}"
        f"{blend_channel(fg_b, bg_b):02x}"
    )


def create_leaf_icon(master: tk.Misc, size: int = 32) -> tk.PhotoImage:
    """Create a small transparent leaf icon without platform font glyphs."""

    image = tk.PhotoImage(master=master, width=size, height=size)
    scale = size / 32

    def distance_to_segment(
        x: float, y: float, start: tuple[float, float], end: tuple[float, float]
    ) -> float:
        start_x, start_y = start
        end_x, end_y = end
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        length_squared = delta_x * delta_x + delta_y * delta_y
        projection = (
            (x - start_x) * delta_x + (y - start_y) * delta_y
        ) / length_squared
        projection = max(0.0, min(1.0, projection))
        nearest_x = start_x + projection * delta_x
        nearest_y = start_y + projection * delta_y
        return ((x - nearest_x) ** 2 + (y - nearest_y) ** 2) ** 0.5

    pixels: dict[tuple[int, int], str] = {}
    for pixel_y in range(size):
        for pixel_x in range(size):
            x = (pixel_x + 0.5) / scale
            y = (pixel_y + 0.5) / scale
            if distance_to_segment(x, y, (8, 27), (18, 10)) <= 1.2:
                pixels[(pixel_x, pixel_y)] = "#3f8f35"

            leaf_x = x - 19
            leaf_y = y - 10
            major = (leaf_x - leaf_y) / 1.41421356237
            minor = (leaf_x + leaf_y) / 1.41421356237
            if (major / 10.5) ** 2 + (minor / 5.2) ** 2 <= 1:
                pixels[(pixel_x, pixel_y)] = "#61b83b"

            small_x = x - 9.5
            small_y = y - 19
            small_major = (-0.82 * small_x) + (-0.57 * small_y)
            small_minor = (0.57 * small_x) + (-0.82 * small_y)
            if (small_major / 5.5) ** 2 + (small_minor / 3.0) ** 2 <= 1:
                pixels[(pixel_x, pixel_y)] = "#76c442"

    for (pixel_x, pixel_y), color in pixels.items():
        image.put(color, (pixel_x, pixel_y))
    return image


def load_application_icons(master: tk.Misc) -> tuple[tk.PhotoImage, ...]:
    """Load the packaged multi-resolution application icon set for Tk."""

    icon_names = (
        "plantinplates_icon_tk.png",
        "plantinplates_icon_16.png",
        "plantinplates_icon_20.png",
        "plantinplates_icon_24.png",
        "plantinplates_icon_32.png",
        "plantinplates_icon_48.png",
        "plantinplates_icon_128.png",
        "plantinplates_icon_256.png",
    )
    asset_directory = pathlib.Path(__file__).parent / "assets"
    resource_loader = globals().get("__loader__")
    read_resource = getattr(resource_loader, "get_data", None)
    icons: list[tk.PhotoImage] = []
    for name in icon_names:
        try:
            asset_path = asset_directory / name
            data = (
                read_resource(str(asset_path))
                if callable(read_resource)
                else asset_path.read_bytes()
            )
        except (FileNotFoundError, OSError):
            continue
        encoded = base64.b64encode(data).decode("ascii")
        icons.append(tk.PhotoImage(master=master, data=encoded, format="png"))
    return tuple(icons) or (create_leaf_icon(master),)


class _StepIndicator(tk.Canvas):
    """Circular indicator and vertical connector used by the sidebar."""

    WIDTH = 42
    HEIGHT = 82
    CENTER_X = WIDTH // 2
    CENTER_Y = HEIGHT // 2
    RADIUS = 15

    def __init__(
        self,
        parent: tk.Misc,
        *,
        colors: Any,
        background: str,
        has_previous: bool,
        has_next: bool,
    ) -> None:
        super().__init__(
            parent,
            width=self.WIDTH,
            height=self.HEIGHT,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            takefocus=0,
        )

        self._colors = colors
        self._has_previous = has_previous
        self._has_next = has_next

        self._font = tkfont.nametofont("TkDefaultFont").copy()
        self._font.configure(weight="bold")

    def render(
        self,
        *,
        number: int,
        state: StepState,
        background: str,
    ) -> None:
        """Redraw the indicator for the requested state."""

        self.configure(background=background)
        self.delete("all")

        primary = _theme_color(self._colors, "primary", "#0d6efd")
        success = _theme_color(self._colors, "success", "#198754")
        warning = _theme_color(self._colors, "warning", "#ffc107")
        secondary = _theme_color(self._colors, "secondary", "#6c757d")
        selectfg = _theme_color(self._colors, "selectfg", "#ffffff")
        dark = _theme_color(self._colors, "dark", "#212529")
        border = _theme_color(self._colors, "border", "#ced4da")

        top = self.CENTER_Y - self.RADIUS
        bottom = self.CENTER_Y + self.RADIUS

        if self._has_previous:
            self.create_line(
                self.CENTER_X,
                0,
                self.CENTER_X,
                top,
                fill=border,
                width=1,
            )

        if self._has_next:
            self.create_line(
                self.CENTER_X,
                bottom,
                self.CENTER_X,
                self.HEIGHT,
                fill=border,
                width=1,
            )

        if state is StepState.COMPLETED:
            fill = success
            outline = success
            text = None
            text_color = selectfg
        elif state is StepState.CURRENT:
            fill = primary
            outline = primary
            text = str(number)
            text_color = selectfg
        elif state is StepState.ERROR:
            fill = warning
            outline = warning
            text = None
            text_color = dark
        else:
            fill = secondary
            outline = secondary
            text = str(number)
            text_color = selectfg

        self.create_oval(
            self.CENTER_X - self.RADIUS,
            self.CENTER_Y - self.RADIUS,
            self.CENTER_X + self.RADIUS,
            self.CENTER_Y + self.RADIUS,
            fill=fill,
            outline=outline,
            width=1,
        )
        if state is StepState.COMPLETED:
            self.create_line(
                self.CENTER_X - 7,
                self.CENTER_Y,
                self.CENTER_X - 2,
                self.CENTER_Y + 5,
                self.CENTER_X + 8,
                self.CENTER_Y - 6,
                fill=selectfg,
                width=2,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
            )
        elif state is StepState.ERROR:
            self.create_line(
                self.CENTER_X,
                self.CENTER_Y - 7,
                self.CENTER_X,
                self.CENTER_Y + 2,
                fill=dark,
                width=2,
                capstyle=tk.ROUND,
            )
            self.create_oval(
                self.CENTER_X - 1,
                self.CENTER_Y + 6,
                self.CENTER_X + 1,
                self.CENTER_Y + 8,
                fill=dark,
                outline=dark,
            )
        else:
            self.create_text(
                self.CENTER_X,
                self.CENTER_Y,
                text=text,
                fill=text_color,
                font=self._font,
            )


class _StepIcon(tk.Canvas):
    """Consistent 18 px line icon for one workflow step."""

    SIZE = 18

    def __init__(
        self,
        parent: tk.Misc,
        *,
        kind: str,
        colors: Any,
        background: str,
    ) -> None:
        super().__init__(
            parent,
            width=self.SIZE,
            height=self.SIZE,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            takefocus=0,
        )
        self._kind = kind
        self._colors = colors

    def render(self, state: StepState, background: str) -> None:
        self.configure(background=background)
        self.delete("all")
        color = _theme_color(
            self._colors,
            "primary" if state is StepState.CURRENT else "secondary",
            "#0d6efd" if state is StepState.CURRENT else "#6c757d",
        )
        line = {"fill": color, "width": 2, "capstyle": tk.ROUND}

        if self._kind == "folder":
            self.create_line(1, 6, 6, 6, 8, 8, 17, 8, **line)
            self.create_line(1, 6, 1, 16, 17, 16, 17, 8, **line)
        elif self._kind == "calibration":
            for points in (
                (1, 6, 1, 1, 6, 1),
                (12, 1, 17, 1, 17, 6),
                (1, 12, 1, 17, 6, 17),
                (12, 17, 17, 17, 17, 12),
            ):
                self.create_line(*points, **line)
            self.create_oval(6, 6, 12, 12, outline=color, width=2)
        elif self._kind == "method":
            self.create_rectangle(1, 4, 17, 14, outline=color, width=2)
            for x, height in ((5, 4), (9, 6), (13, 4)):
                self.create_line(x, 4, x, 4 + height, **line)
        elif self._kind == "analysis":
            self.create_oval(1, 1, 17, 17, outline=color, width=2)
            self.create_polygon(7, 5, 14, 9, 7, 13, outline=color, fill="", width=2)
        elif self._kind == "results":
            self.create_line(1, 17, 17, 17, **line)
            for left, top, right in ((2, 10, 5), (7, 5, 10), (12, 1, 15)):
                self.create_rectangle(left, top, right, 17, outline=color, width=2)


class _WorkflowStepRow(ttk.Frame):
    """One composite workflow row used by WorkflowSidebar."""

    def __init__(
        self,
        parent: ttk.Frame,
        definition: StepDefinition,
        *,
        colors: Any,
        sidebar_background: str,
        active_background: str,
        has_previous: bool,
        has_next: bool,
        command: Callable[[str], None],
    ) -> None:
        super().__init__(parent, style="Workflow.Step.TFrame")

        self.definition = definition
        self._sidebar_background = sidebar_background
        self._active_background = active_background
        self._command = command
        self._state = StepState.UPCOMING

        self.columnconfigure(2, weight=1)

        self._indicator = _StepIndicator(
            self,
            colors=colors,
            background=sidebar_background,
            has_previous=has_previous,
            has_next=has_next,
        )
        self._indicator.grid(
            row=0,
            column=0,
            sticky="ns",
            padx=(8, 5),
        )

        self._icon = _StepIcon(
            self,
            kind=definition.icon,
            colors=colors,
            background=sidebar_background,
        )
        self._icon.grid(row=0, column=1, padx=(2, 10))

        self._text_container = ttk.Frame(
            self,
            style="Workflow.StepContent.TFrame",
        )
        self._text_container.grid(
            row=0,
            column=2,
            sticky="w",
            padx=(0, 12),
        )

        self._title_label = ttk.Label(
            self._text_container,
            text=definition.title,
            style="Workflow.Title.TLabel",
        )
        self._title_label.grid(
            row=0,
            column=0,
            sticky="w",
        )

        self._subtitle_label = ttk.Label(
            self._text_container,
            text=definition.subtitle,
            style="Workflow.Subtitle.TLabel",
        )
        self._subtitle_label.grid(
            row=1,
            column=0,
            sticky="w",
            pady=(4, 0),
        )

        self._bind_clicks()
        self.set_state(StepState.UPCOMING)

    @property
    def state(self) -> StepState:
        return self._state

    def _bind_clicks(self) -> None:
        widgets: tuple[tk.Misc, ...] = (
            self,
            self._indicator,
            self._icon,
            self._text_container,
            self._title_label,
            self._subtitle_label,
        )

        for widget in widgets:
            widget.bind("<Button-1>", self._on_click, add="+")
            widget.bind("<Return>", self._on_click, add="+")
            widget.bind("<space>", self._on_click, add="+")
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")

    def _is_clickable(self) -> bool:
        return self._state in {
            StepState.COMPLETED,
            StepState.CURRENT,
            StepState.ERROR,
        }

    def _on_click(self, _event: tk.Event[Any] | None = None) -> None:
        if self._is_clickable():
            self._command(self.definition.key)

    def _on_enter(self, _event: tk.Event[Any] | None = None) -> None:
        if self._is_clickable():
            for widget in (
                self,
                self._indicator,
                self._icon,
                self._text_container,
                self._title_label,
                self._subtitle_label,
            ):
                widget.configure(cursor="hand2")

    def _on_leave(self, _event: tk.Event[Any] | None = None) -> None:
        for widget in (
            self,
            self._indicator,
            self._icon,
            self._text_container,
            self._title_label,
            self._subtitle_label,
        ):
            widget.configure(cursor="")

    def set_state(self, state: StepState) -> None:
        self._state = state

        if state is StepState.CURRENT:
            row_style = "Workflow.ActiveStep.TFrame"
            content_style = "Workflow.ActiveStepContent.TFrame"
            title_style = "Workflow.ActiveTitle.TLabel"
            subtitle_style = "Workflow.ActiveSubtitle.TLabel"
            background = self._active_background
        elif state is StepState.UPCOMING:
            row_style = "Workflow.Step.TFrame"
            content_style = "Workflow.StepContent.TFrame"
            title_style = "Workflow.MutedTitle.TLabel"
            subtitle_style = "Workflow.MutedSubtitle.TLabel"
            background = self._sidebar_background
        elif state is StepState.ERROR:
            row_style = "Workflow.Step.TFrame"
            content_style = "Workflow.StepContent.TFrame"
            title_style = "Workflow.Title.TLabel"
            subtitle_style = "Workflow.Subtitle.TLabel"
            background = self._sidebar_background
        else:
            row_style = "Workflow.Step.TFrame"
            content_style = "Workflow.StepContent.TFrame"
            title_style = "Workflow.Title.TLabel"
            subtitle_style = "Workflow.Subtitle.TLabel"
            background = self._sidebar_background

        self.configure(style=row_style)
        self._text_container.configure(style=content_style)
        self._title_label.configure(style=title_style)
        self._subtitle_label.configure(style=subtitle_style)

        self._indicator.render(
            number=self.definition.number,
            state=state,
            background=background,
        )
        self._icon.render(state, background)

    def update_subtitle(self, subtitle: str) -> None:
        self._subtitle_label.configure(text=subtitle)

    def update_title(self, title: str) -> None:
        self._title_label.configure(text=title)

    def set_compact(self, compact: bool) -> None:
        """Show only the step indicator when the sidebar is collapsed."""

        if compact:
            self._icon.grid_remove()
            self._text_container.grid_remove()
        else:
            self._icon.grid()
            self._text_container.grid()


class WorkflowSidebar(ttk.Frame):
    """
    Persistent workflow sidebar for PlantinPlates.

    The callback receives the selected step key. Upcoming steps are not
    clickable; completed, current, and error steps are clickable.
    """

    def __init__(
        self,
        parent: tk.Misc,
        steps: Iterable[StepDefinition],
        *,
        on_select: Callable[[str], None],
        width: int = 300,
        show_menu_button: bool = True,
        show_footer_actions: bool = True,
        on_preferences: Callable[[], None] | None = None,
        on_help: Callable[[], None] | None = None,
    ) -> None:
        style = ttk.Style()
        colors = style.colors

        content_background = _theme_color(colors, "bg", "#ffffff")
        sidebar_background = _blend_color(
            parent,
            _theme_color(colors, "dark", "#212529"),
            content_background,
            0.045,
        )
        active_background = _blend_color(
            parent,
            _theme_color(colors, "primary", "#0d6efd"),
            sidebar_background,
            0.08,
        )
        active_border = _blend_color(
            parent,
            _theme_color(colors, "primary", "#0d6efd"),
            sidebar_background,
            0.22,
        )

        self._configure_styles(
            sidebar_background=sidebar_background,
            active_background=active_background,
            active_border=active_border,
        )

        super().__init__(
            parent,
            width=width,
            style="Workflow.Sidebar.TFrame",
            padding=(10, 16),
        )

        self.grid_propagate(False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._on_select = on_select
        self._rows: dict[str, _WorkflowStepRow] = {}
        self._expanded_width = width
        self._compact = False
        step_list = list(steps)

        if show_menu_button:
            self.menu_button = ttk.Button(
                self,
                text="☰",
                width=3,
                style="Workflow.SidebarLink.TButton",
                command=self.toggle_compact,
            )
            self.menu_button.grid(
                row=0,
                column=0,
                sticky="w",
                pady=(0, 18),
            )
        else:
            self.menu_button = None

        self._heading = ttk.Label(
            self,
            text="WORKFLOW",
            style="Workflow.Heading.TLabel",
        )
        self._heading.grid(
            row=1,
            column=0,
            sticky="w",
            padx=10,
            pady=(0, 6),
        )

        steps_container = ttk.Frame(
            self,
            style="Workflow.Sidebar.TFrame",
        )
        steps_container.grid(
            row=2,
            column=0,
            sticky="new",
        )
        steps_container.columnconfigure(0, weight=1)

        for index, definition in enumerate(step_list):
            row = _WorkflowStepRow(
                steps_container,
                definition,
                colors=colors,
                sidebar_background=sidebar_background,
                active_background=active_background,
                has_previous=index > 0,
                has_next=index < len(step_list) - 1,
                command=self._select,
            )
            row.grid(
                row=index,
                column=0,
                sticky="ew",
                pady=1,
            )
            self._rows[definition.key] = row

        if show_footer_actions:
            self._action_footer = ttk.Frame(
                self,
                style="Workflow.Sidebar.TFrame",
            )
            self._action_footer.grid(
                row=3,
                column=0,
                sticky="sew",
                pady=(16, 0),
            )
            self._action_footer.columnconfigure(0, weight=1)

            ttk.Separator(self._action_footer).grid(
                row=0,
                column=0,
                sticky="ew",
                pady=(0, 10),
            )

            # Preferences button hidden for now.
            # preferences_button = ttk.Button(
            #     self._action_footer,
            #     text="⚙  Preferences",
            #     style="Workflow.SidebarLink.TButton",
            #     command=on_preferences,
            # )
            # preferences_button.grid(
            #     row=1,
            #     column=0,
            #     sticky="w",
            # )
            # if on_preferences is None:
            #     preferences_button.configure(state="disabled")

            help_button = ttk.Button(
                self._action_footer,
                text="?  Help",
                style="Workflow.SidebarLink.TButton",
                command=on_help,
            )
            help_button.grid(
                row=2,
                column=0,
                sticky="w",
            )
            if on_help is None:
                help_button.configure(state="disabled")
        else:
            self._action_footer = None

    @staticmethod
    def _configure_styles(
        *,
        sidebar_background: str,
        active_background: str,
        active_border: str,
    ) -> None:
        style = ttk.Style()
        colors = style.colors

        default_font = tkfont.nametofont("TkDefaultFont")
        title_font = default_font.copy()
        title_font.configure(weight="bold")

        small_font = default_font.copy()
        size = int(default_font.cget("size"))
        if size > 0:
            small_font.configure(size=max(8, size - 1))

        foreground = _theme_color(colors, "fg", "#212529")
        secondary = _theme_color(colors, "secondary", "#6c757d")
        primary = _theme_color(colors, "primary", "#0d6efd")

        style.configure(
            "Workflow.Sidebar.TFrame",
            background=sidebar_background,
        )
        style.configure(
            "Workflow.Step.TFrame",
            background=sidebar_background,
            bordercolor=sidebar_background,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Workflow.ActiveStep.TFrame",
            background=active_background,
            bordercolor=active_border,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Workflow.StepContent.TFrame",
            background=sidebar_background,
        )
        style.configure(
            "Workflow.ActiveStepContent.TFrame",
            background=active_background,
        )
        style.configure(
            "Workflow.SidebarLink.TButton",
            background=sidebar_background,
            foreground=secondary,
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Workflow.SidebarLink.TButton",
            background=[("active", active_background)],
            foreground=[("active", primary)],
        )

        style.configure(
            "Workflow.Heading.TLabel",
            background=sidebar_background,
            foreground=secondary,
            font=small_font,
        )

        style.configure(
            "Workflow.Title.TLabel",
            background=sidebar_background,
            foreground=foreground,
            font=title_font,
        )
        style.configure(
            "Workflow.Subtitle.TLabel",
            background=sidebar_background,
            foreground=secondary,
            font=small_font,
        )

        style.configure(
            "Workflow.ActiveTitle.TLabel",
            background=active_background,
            foreground=primary,
            font=title_font,
        )
        style.configure(
            "Workflow.ActiveSubtitle.TLabel",
            background=active_background,
            foreground=primary,
            font=small_font,
        )

        style.configure(
            "Workflow.MutedTitle.TLabel",
            background=sidebar_background,
            foreground=secondary,
        )
        style.configure(
            "Workflow.MutedSubtitle.TLabel",
            background=sidebar_background,
            foreground=secondary,
            font=small_font,
        )

    def _select(self, key: str) -> None:
        self._on_select(key)

    def set_state(self, key: str, state: StepState) -> None:
        self._get_row(key).set_state(state)

    def update_subtitle(self, key: str, subtitle: str) -> None:
        self._get_row(key).update_subtitle(subtitle)

    def update_title(self, key: str, title: str) -> None:
        self._get_row(key).update_title(title)

    def state_of(self, key: str) -> StepState:
        return self._get_row(key).state

    def toggle_compact(self) -> None:
        """Collapse or expand the sidebar while keeping navigation available."""

        self._compact = not self._compact
        self.configure(width=74 if self._compact else self._expanded_width)
        for row in self._rows.values():
            row.set_compact(self._compact)
        if self._compact:
            self._heading.grid_remove()
            if self._action_footer is not None:
                self._action_footer.grid_remove()
        else:
            self._heading.grid()
            if self._action_footer is not None:
                self._action_footer.grid()

    def _get_row(self, key: str) -> _WorkflowStepRow:
        try:
            return self._rows[key]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow step: {key!r}") from exc


class ApplicationFooter(ttk.Frame):
    """
    Persistent application footer with status, optional progress, Back, and
    Continue/primary-action buttons.
    """

    _STATUS_PRESENTATION: dict[StatusKind, tuple[str, str]] = {
        StatusKind.READY: ("✓", "success"),
        StatusKind.INFO: ("ⓘ", "primary"),
        StatusKind.WARNING: ("!", "warning"),
        StatusKind.ERROR: ("×", "danger"),
        StatusKind.BUSY: ("…", "primary"),
    }

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_back: Callable[[], None],
        on_continue: Callable[[], None],
        padding: tuple[int, int] = (24, 14),
    ) -> None:
        style = ttk.Style()
        footer_background = _blend_color(
            parent,
            _theme_color(style.colors, "dark", "#212529"),
            _theme_color(style.colors, "bg", "#ffffff"),
            0.035,
        )
        style.configure("Workflow.Footer.TFrame", background=footer_background)
        style.configure(
            "Workflow.FooterStatus.TLabel",
            background=footer_background,
            foreground=_theme_color(style.colors, "dark", "#212529"),
        )
        style.configure(
            "Workflow.FooterIcon.TLabel",
            background=footer_background,
            foreground=_theme_color(style.colors, "success", "#198754"),
        )
        super().__init__(parent, style="Workflow.Footer.TFrame")

        self._default_continue_command = on_continue
        self._style = style

        self.columnconfigure(0, weight=1)

        ttk.Separator(
            self,
            orient="horizontal",
        ).grid(
            row=0,
            column=0,
            sticky="ew",
        )

        body = ttk.Frame(
            self,
            padding=padding,
            style="Workflow.Footer.TFrame",
        )
        body.grid(
            row=1,
            column=0,
            sticky="ew",
        )
        body.columnconfigure(1, weight=1)

        status_area = ttk.Frame(body, style="Workflow.Footer.TFrame")
        status_area.grid(
            row=0,
            column=0,
            sticky="w",
        )

        status_icon_font = tkfont.nametofont("TkDefaultFont").copy()
        status_icon_font.configure(weight="bold")

        self.status_icon = ttk.Label(
            status_area,
            text="✓",
            width=2,
            anchor="center",
            style="Workflow.FooterIcon.TLabel",
            font=status_icon_font,
        )
        self.status_icon.grid(
            row=0,
            column=0,
            sticky="w",
        )

        self.status_label = ttk.Label(
            status_area,
            text="Ready",
            style="Workflow.FooterStatus.TLabel",
            justify="left",
            wraplength=420,
        )
        self.status_label.grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 0),
        )

        self.progress = ttk.Progressbar(
            body,
            mode="determinate",
            bootstyle="primary-striped",
            length=180,
        )
        self.progress.grid(
            row=0,
            column=1,
            sticky="e",
            padx=(16, 20),
        )
        self.progress.grid_remove()

        self.back_button = ttk.Button(
            body,
            text="←  Back",
            bootstyle="dark-outline",
            command=on_back,
            width=14,
        )
        self.back_button.grid(
            row=0,
            column=2,
            padx=(8, 0),
        )

        self.continue_button = ttk.Button(
            body,
            text="Continue  →",
            bootstyle="primary",
            command=on_continue,
            width=16,
        )
        self.continue_button.grid(
            row=0,
            column=3,
            padx=(12, 0),
        )

    def set_status(
        self,
        message: str,
        kind: StatusKind = StatusKind.INFO,
    ) -> None:
        symbol, bootstyle = self._STATUS_PRESENTATION[kind]
        self._style.configure(
            "Workflow.FooterIcon.TLabel",
            foreground=_theme_color(
                self._style.colors,
                bootstyle,
                "#212529",
            ),
        )
        self.status_icon.configure(text=symbol)
        self.status_label.configure(text=message)

    def configure_back(
        self,
        *,
        enabled: bool,
        text: str = "←  Back",
        command: Callable[[], None] | None = None,
    ) -> None:
        options: dict[str, object] = {
            "text": text,
            "state": "normal" if enabled else "disabled",
        }
        if command is not None:
            options["command"] = command
        self.back_button.configure(**options)

    def configure_continue(
        self,
        *,
        enabled: bool,
        text: str = "Continue  →",
        bootstyle: str = "primary",
        command: Callable[[], None] | None = None,
    ) -> None:
        self.continue_button.configure(
            text=text,
            state="normal" if enabled else "disabled",
            bootstyle=bootstyle,
            command=command or self._default_continue_command,
        )

    def show_progress(
        self,
        *,
        value: float = 0,
        maximum: float = 100,
    ) -> None:
        self.progress.stop()
        self.progress.configure(
            mode="determinate",
            maximum=maximum,
            value=value,
        )
        self.progress.grid()

    def update_progress(self, value: float) -> None:
        self.progress.configure(value=value)

    def show_indeterminate_progress(self) -> None:
        self.progress.configure(mode="indeterminate")
        self.progress.grid()
        self.progress.start(12)

    def hide_progress(self) -> None:
        self.progress.stop()
        self.progress.grid_remove()


# Optional standalone demonstration.
if __name__ == "__main__":
    STEPS = [
        StepDefinition(
            key="folder",
            number=1,
            title="Data folder",
            subtitle="experiment_42",
            icon="folder",
        ),
        StepDefinition(
            key="calibration",
            number=2,
            title="Calibration",
            subtitle="Image metadata",
            icon="calibration",
        ),
        StepDefinition(
            key="method",
            number=3,
            title="Method",
            subtitle="Measurement settings",
            icon="method",
        ),
        StepDefinition(
            key="analysis",
            number=4,
            title="Run analysis",
            subtitle="Configure and execute",
            icon="analysis",
        ),
        StepDefinition(
            key="results",
            number=5,
            title="Results",
            subtitle="View outputs and reports",
            icon="results",
        ),
    ]

    class DemoApplication(ttk.Window):
        def __init__(self) -> None:
            super().__init__(
                title="PlantinPlates",
                themename="litera",
                size=(1200, 760),
                minsize=(900, 620),
            )
            self.app_icons = load_application_icons(self)
            self.iconphoto(True, *self.app_icons)

            self._current_index = 2
            self._furthest_index = 2

            self.rowconfigure(0, weight=1)
            self.columnconfigure(1, weight=1)

            self.sidebar = WorkflowSidebar(
                self,
                STEPS,
                on_select=self.show_step,
                on_help=lambda: webbrowser.open_new_tab(
                    "https://github.com/hgrecco/plantsinplates"
                ),
            )
            self.sidebar.grid(
                row=0,
                column=0,
                sticky="ns",
            )

            content = ttk.Frame(self, padding=32)
            content.grid(
                row=0,
                column=1,
                sticky="nsew",
            )
            content.columnconfigure(0, weight=1)

            page_title_font = tkfont.nametofont("TkDefaultFont").copy()
            page_title_font.configure(size=20, weight="bold")
            section_title_font = tkfont.nametofont("TkDefaultFont").copy()
            section_title_font.configure(size=15, weight="bold")

            ttk.Label(
                content,
                text="Fluorescence microscopy analysis",
                font=page_title_font,
            ).grid(
                row=0,
                column=0,
                sticky="w",
            )
            ttk.Label(
                content,
                text=(
                    "Choose data, make calibration explicit, "
                    "and keep every run reproducible."
                ),
                bootstyle="secondary",
            ).grid(
                row=1,
                column=0,
                sticky="w",
                pady=(6, 28),
            )
            ttk.Label(
                content,
                text="Measurement method and settings",
                font=section_title_font,
            ).grid(
                row=2,
                column=0,
                sticky="w",
            )

            self.footer = ApplicationFooter(
                self,
                on_back=self.go_back,
                on_continue=self.go_forward,
            )
            self.footer.grid(
                row=1,
                column=0,
                columnspan=2,
                sticky="ew",
            )

            self._refresh()

        def show_step(self, key: str) -> None:
            index = next(index for index, step in enumerate(STEPS) if step.key == key)
            if index <= self._furthest_index:
                self._current_index = index
                self._refresh()

        def go_back(self) -> None:
            if self._current_index > 0:
                self._current_index -= 1
                self._refresh()

        def go_forward(self) -> None:
            if self._current_index < len(STEPS) - 1:
                self._current_index += 1
                self._furthest_index = max(
                    self._furthest_index,
                    self._current_index,
                )
                self._refresh()

        def _refresh(self) -> None:
            for index, step in enumerate(STEPS):
                if index == self._current_index:
                    state = StepState.CURRENT
                elif index <= self._furthest_index:
                    state = StepState.COMPLETED
                else:
                    state = StepState.UPCOMING
                self.sidebar.set_state(step.key, state)

            current = STEPS[self._current_index]
            self.footer.configure_back(
                enabled=self._current_index > 0,
            )
            self.footer.configure_continue(
                enabled=True,
                text=(
                    "Close" if self._current_index == len(STEPS) - 1 else "Continue  →"
                ),
                bootstyle=(
                    "secondary" if self._current_index == len(STEPS) - 1 else "primary"
                ),
                command=(
                    self.destroy
                    if self._current_index == len(STEPS) - 1
                    else self.go_forward
                ),
            )
            self.footer.set_status(
                f"Ready — {current.title} configured.",
                StatusKind.READY,
            )

    DemoApplication().mainloop()
