"""Settings window for the Apple Music voice controller."""

import ctypes
import ctypes.wintypes
import json
import math
import os
import queue
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

import controller

# Bundled files sit next to this module, both from source and in the built exe.
# Settings must be writable, so the exe keeps them in %APPDATA%\Voice Control.
APP_DIR = Path(__file__).parent
FROZEN = getattr(sys, "frozen", False)  # running as the PyInstaller-built exe
DATA_DIR = Path(os.environ.get("APPDATA", APP_DIR)) / "Voice Control" if FROZEN else APP_DIR
SETTINGS_FILE = DATA_DIR / "settings.json"
FONTS_DIR = APP_DIR / "fonts"
ICON_FILE = APP_DIR / "assets" / "icon.ico"  # made by make_icon.py
DEFAULT_MIC = "System default"
# The console is the one flexible piece: at least CONSOLE_MIN_H tall, opening at
# CONSOLE_MIN_H + CONSOLE_EXTRA_H, and growing to fill a taller window.
CONSOLE_MIN_H, CONSOLE_EXTRA_H = 60, 60
NO_WAKE = "None"
DEFAULTS = {"mic": DEFAULT_MIC, "wake": None, "min_conf": 0.6, "autostart": False,
            "console": True, "night": False}

# Palette as (light, dark) pairs; CustomTkinter picks one by appearance mode.
BG = ("#FAF6F2", "#1E1E22")            # warm cream / dark grey window
CARD = ("#FFFFFF", "#28282D")
FIELD = ("#F4F0F7", "#323238")         # inputs, slider track
FIELD_HOVER = ("#ECE6F1", "#3B3B42")
INK = ("#3E3A4F", "#ECE8F2")
MUTED = ("#A39DB0", "#8D8898")
ON_PASTEL = ("#3E3A4F", "#2A2833")     # text on pastel fills
LAVENDER, LAVENDER_HOVER = ("#CDBDF2", "#C2B2EE"), ("#BFADEC", "#B3A1E8")
MINT, MINT_HOVER = ("#BDE7D3", "#A8DCC3"), ("#ABDEC5", "#97D3B6")
MINT_TINT, MINT_DEEP = ("#BDE7D3", "#2C4439"), ("#4F9C7B", "#8FD6B5")
PEACH, PEACH_HOVER, PEACH_DEEP = ("#F9D3C5", "#F2C2B0"), ("#F5C3B1", "#EDB29C"), ("#C9705A", "#F0A58F")
BLUSH, BLUSH_HOVER, BLUSH_ICON = ("#F7DCE5", "#3F343B"), ("#F1CAD7", "#4A3D45"), ("#3E3A4F", "#F4D3DF")
SWITCH_TRACK, SWITCH_ON = ("#DED6E6", "#46454D"), ("#4F9C7B", "#7CC9A6")
KNOB_ON, KNOB_OFF = ("#FFFFFF", "#F2EFF6"), ("#B3ACC0", "#B9B4C2")  # white knob is lost on a white card
POPUP, POPUP_BORDER = ("#FFFFFF", "#323238"), ("#E8E2EE", "#47464E")
POPUP_HOVER, POPUP_SELECTED = ("#F4F0F7", "#3E3D45"), ("#EFE9FB", "#3D3849")
SLIDER_KNOB, SLIDER_KNOB_HOVER = ("#B5A2E8", "#C2B2EE"), ("#A590E0", "#B3A1E8")

# Bundled Google Fonts (OFL), loaded privately in load_fonts()
SANS, SANS_BOLD, SERIF = "DM Sans", "DM Sans SemiBold", "Source Serif 4 SemiBold"
ICON_FONT = "Segoe Fluent Icons"
ICONS = {"previous": "", "play": "", "pause": "",
         "next": "", "refresh": "", "chevron": ""}


class Window(ctk.CTk):
    def _windows_set_titlebar_color(self, color_mode):
        # CustomTkinter hides and re-shows the whole window to repaint the title bar
        # on every theme change, which makes everything vanish for a moment.
        # tint_title_bar() recolours it in place instead.
        pass


class RoundIconButton(ctk.CTkButton):
    """CTkButton reserves corner_radius of padding on each side of its text,
    which stretches a circle into a pill. Drop that padding so it stays round."""

    def _create_grid(self):
        super()._create_grid()
        self.grid_columnconfigure(0, weight=1, minsize=0)
        self.grid_columnconfigure(4, weight=1, minsize=0)


class Dropdown(ctk.CTkOptionMenu):
    """CTkOptionMenu, but the whole field highlights on hover (not just the arrow),
    the arrow is a crisp icon-font chevron, and it opens a Popup in the app's style
    instead of the native Windows menu."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._popup = None

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        # CustomTkinter's arrow is a glyph from its shapes font, which renders
        # with dotty artifacts at some scalings. Hide it and draw a chevron instead.
        self._canvas.itemconfigure("dropdown_arrow", state="hidden")
        if not self._canvas.find_withtag("chevron"):
            self._canvas.create_text(0, 0, text=ICONS["chevron"], anchor="center", tags="chevron")
        self._canvas.itemconfigure("chevron", fill=self._apply_appearance_mode(self._text_color),
                                   font=(ICON_FONT, -round(self._apply_widget_scaling(12))))
        self._canvas.coords("chevron",
                            round(self._apply_widget_scaling(self._current_width - self._current_height / 2)),
                            round(self._apply_widget_scaling(self._current_height / 2)))
        self._canvas.tag_raise("chevron")

    def _paint(self, color):
        c = self._apply_appearance_mode(color)
        for part in ("inner_parts_left", "inner_parts_right"):
            self._canvas.itemconfig(part, outline=c, fill=c)
        self._text_label.configure(bg=c)

    def _on_enter(self, event=0):
        if self._state == tk.NORMAL and self._values:
            self._paint(self._button_hover_color)

    def _on_leave(self, event=0):
        self._paint(self._fg_color)

    def _clicked(self, event=0):
        if self._popup:
            self._popup.close()
        elif self._values:
            self._popup = Popup(self, self._values, self.get(), self._picked, self._popup_gone)

    def _picked(self, value):
        self._dropdown_callback(value)

    def _popup_gone(self):
        self._popup = None


class Popup:
    """A rounded list of choices under a field. Closes on pick, or via
    Popup.close_open() when the app sees a click elsewhere, Escape, a scroll,
    the window moving, or the app losing focus."""

    current = None  # the open popup, if any

    def __init__(self, field, values, current, on_pick, on_close):
        Popup.close_open()
        Popup.current = self
        self.field, self.on_pick, self.on_close = field, on_pick, on_close
        scale = ctk.ScalingTracker.get_widget_scaling(field)
        # Corners outside the rounded panel are painted in a key colour that
        # Windows renders transparent; it's a near-copy of the card colour so the
        # panel's anti-aliased edges blend in.
        key = near_color(shade(CARD))
        self.top = top = tk.Toplevel(field)
        top.overrideredirect(True)
        top.configure(bg=key)
        top.wm_attributes("-transparentcolor", key)

        # The rounded, 1px-bordered background is one image drawn here, so its edges
        # are exact. (CustomTkinter shapes are drawn a pixel or two smaller than their
        # widgets at fractional scaling, and nested ones paint square corners over
        # each other.) The rows sit on a plain panel inset clear of the corners.
        canvas = tk.Canvas(top, bg=key, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        rows = ctk.CTkFrame(canvas, fg_color=POPUP, bg_color=POPUP, corner_radius=0)
        for i, value in enumerate(values):
            chosen = value == current
            ctk.CTkButton(rows, text=value, anchor="w", height=32, corner_radius=8,
                          font=font(13, bold=chosen), text_color=INK,
                          fg_color=POPUP_SELECTED if chosen else POPUP, hover_color=POPUP_HOVER,
                          command=lambda v=value: self.pick(v)).pack(fill="x", pady=(0 if i == 0 else 1, 0))

        # Match the width the field is actually drawn at (see drawn_size).
        w = drawn_size(field.winfo_width(), scale)
        inset = round(7 * scale)
        top.update_idletasks()
        h = rows.winfo_reqheight() + 2 * inset
        self.background = popup_background(w, h, round(12 * scale), shade(POPUP),
                                           shade(POPUP_BORDER), key)
        canvas.configure(width=w, height=h)
        canvas.create_image(0, 0, anchor="nw", image=self.background)
        canvas.create_window(inset, inset, anchor="nw", window=rows,
                             width=w - 2 * inset, height=h - 2 * inset)

        x, y = field.winfo_rootx(), field.winfo_rooty() + field.winfo_height() + round(4 * scale)
        if y + h > work_area_height(field):  # no room below: open upwards
            y = field.winfo_rooty() - h - round(4 * scale)
        top.geometry(f"{w}x{h}+{x}+{y}")
        make_menu_window(top, field.winfo_toplevel())

    def owns(self, widget):
        """True if a click on `widget` belongs to this popup or the field that opened it."""
        path = str(widget)
        return any(path.startswith(str(w)) for w in (self.top, self.field) if w is not None)

    def pick(self, value):
        self.close()
        self.on_pick(value)

    def close(self):
        if self.top is not None:
            self.top.destroy()
            self.top = None
            if Popup.current is self:
                Popup.current = None
            self.on_close()

    @staticmethod
    def close_open(event=None):
        if Popup.current and not (event is not None and Popup.current.owns(event.widget)):
            Popup.current.close()


def make_menu_window(top, owner):
    """Make a borderless popup behave like a native menu: owned by the main window
    (so it stays in front of it, but not in front of other apps), and clicks on it
    don't take focus from the main window. Tk's own `transient` doesn't set the
    owner for borderless windows, which left the popup behind the main window."""
    try:
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        top.update_idletasks()
        hwnd = user32.GetParent(top.winfo_id())
        owner_hwnd = user32.GetParent(owner.winfo_id())
        GWLP_HWNDPARENT, GWL_EXSTYLE, WS_EX_NOACTIVATE = -8, -20, 0x08000000
        user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, owner_hwnd)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE,
                                 user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
        HWND_TOP, SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0, 0x2, 0x1, 0x10
        user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def drawn_size(n, scale):
    """The width CustomTkinter actually draws a shape at, for an n-px widget: it
    floors to unscaled units, scales back up, then floors to an even number
    (at 125%, a 375px field is drawn 374px wide)."""
    return math.floor(math.floor(n / scale) * scale / 2) * 2


def popup_background(w, h, radius, fill, border, key):
    """A w x h rounded rectangle with a 1px border, anti-aliased against the
    transparency key so only the shape shows."""
    k = 4  # draw large, scale down for smooth corners
    rgb = lambda c: tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    shape = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(shape)
    d.rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=radius * k, fill=rgb(border))
    d.rounded_rectangle((k, k, w * k - 1 - k, h * k - 1 - k), radius=(radius - 1) * k, fill=rgb(fill))
    image = Image.new("RGBA", (w, h), rgb(key) + (255,))
    image.alpha_composite(shape.resize((w, h), Image.LANCZOS))
    return ImageTk.PhotoImage(image.convert("RGB"))


def near_color(hex_color):
    """A colour one step away from hex_color, for use as a transparency key."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{r:02x}{g:02x}{b + 1 if b < 255 else b - 1:02x}"


def load_settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def font(size, bold=False):
    return ctk.CTkFont(family=SANS_BOLD if bold else SANS, size=size)


def load_fonts():
    """Make the bundled fonts available to this process only (no install)."""
    FR_PRIVATE = 0x10
    for f in FONTS_DIR.glob("*.ttf"):
        ctypes.windll.gdi32.AddFontResourceExW(str(f), FR_PRIVATE, 0)


def shade(pair):
    """The half of a (light, dark) pair for the current mode, for plain tk widgets."""
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]


class App:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.events = queue.Queue()
        self.listener = None
        self.music = None
        self.mics = []
        self.error = False
        self.am_state = None  # "closed" / "playing" / "paused"

        root.title("Voice Control")
        if ICON_FILE.exists():
            root.iconbitmap(str(ICON_FILE))
        root.configure(fg_color=BG)
        root.minsize(420, 240)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self.refresh_mics()
        self.update_hint()
        self.show_idle()
        self.fit_window()
        tint_title_bar(root)
        set_window_icon(root, ICON_FILE)

        root.after(50, self.poll)
        root.after(300, self.check_apple_music)
        if self.settings["autostart"]:
            root.after(200, self.start)

    # ---------- layout ----------

    def _build(self):
        # Content lives in a canvas so a window shorter than it can scroll. The
        # scrollbar only shows up when it's needed (see update_scroll).
        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, bg=shade(BG),
                                yscrollincrement=20)
        self.scrollbar = ctk.CTkScrollbar(self.root, command=self.canvas.yview, fg_color=BG,
                                          button_color=FIELD_HOVER, button_hover_color=MUTED)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = ctk.CTkFrame(self.canvas, fg_color=BG, corner_radius=0)
        self.content_id = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda e: self.update_scroll())
        self.canvas.bind("<Configure>", lambda e: self.update_scroll())
        self.root.bind_all("<MouseWheel>", self.on_wheel)
        # Close an open dropdown popup when anything else happens in the window.
        self.root.bind_all("<Button-1>", Popup.close_open, add="+")
        self.root.bind_all("<Escape>", lambda e: Popup.close_open(), add="+")
        self.root.bind("<Configure>", lambda e: e.widget is self.root and Popup.close_open(), add="+")
        self.root.bind("<FocusOut>", lambda e: self.root.after(50, self.close_popup_if_inactive), add="+")

        outer = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        outer.pack(fill="both", expand=True, padx=22, pady=(18, 20))

        # Header
        header = ctk.CTkFrame(outer, fg_color=BG)
        header.pack(fill="x")
        titles = ctk.CTkFrame(header, fg_color=BG)
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="Voice Control", text_color=INK,
                     font=ctk.CTkFont(family=SERIF, size=26)).pack(anchor="w")
        ctk.CTkLabel(titles, text="for Apple Music", text_color=MUTED,
                     font=font(13)).pack(anchor="w", pady=(0, 0))
        # Status pill: one rounded label, with the dot as an image (a text "●" sits on
        # the baseline, off-centre; separate widgets showed seams).
        self.dots = {"open": dot_image(MINT_DEEP), "closed": dot_image(MUTED)}
        self.am_pill = ctk.CTkLabel(header, text="Apple Music", image=self.dots["closed"],
                                    compound="left", text_color=MUTED, fg_color=FIELD,
                                    corner_radius=13, height=26, font=font(12))
        self.am_pill.pack(side="right", anchor="n", pady=(6, 0), ipadx=10)

        # Listen card
        card = self.card(outer, pady=(14, 0))
        self.listen_btn = ctk.CTkButton(card, height=54, corner_radius=27, font=font(15, bold=True),
                                        command=self.toggle)
        self.listen_btn.pack(fill="x", padx=18, pady=(18, 12))
        self.level = ctk.CTkProgressBar(card, height=6, corner_radius=3, fg_color=FIELD,
                                        progress_color=MINT)
        self.level.set(0)
        self.level.pack(fill="x", padx=30)
        self.hint = ctk.CTkLabel(card, text="", text_color=MUTED, font=font(12),
                                 wraplength=320, justify="center")
        self.hint.pack(padx=18, pady=(10, 16))

        # Transport buttons; play/pause is one button showing what a press will do
        row = ctk.CTkFrame(outer, fg_color=BG)
        row.pack(pady=(14, 0))
        self.play_btn = None
        for action, size in (("previous", 56), ("toggle", 68), ("next", 56)):
            btn = RoundIconButton(row, text=ICONS.get(action, ICONS["play"]), width=size,
                                  height=size, corner_radius=size // 2,
                                  fg_color=BLUSH, hover_color=BLUSH_HOVER, text_color=BLUSH_ICON,
                                  font=ctk.CTkFont(family=ICON_FONT, size=18 if size == 56 else 22),
                                  command=lambda a=action: self.manual(a))
            btn.pack(side="left", padx=9)
            if action == "toggle":
                self.play_btn = btn

        # Settings card
        card = self.card(outer, pady=(14, 0))
        inner = ctk.CTkFrame(card, fg_color=CARD)
        inner.pack(fill="x", padx=18, pady=16)
        inner.columnconfigure(0, weight=1)

        self.section(inner, "Microphone", 0)
        self.mic_var = tk.StringVar()
        self.mic_menu = self.menu(inner, self.mic_var, [], lambda v: self.on_setting_changed())
        self.mic_menu.grid(row=1, column=0, sticky="ew")
        RoundIconButton(inner, text=ICONS["refresh"], width=36, height=36, corner_radius=18,
                        fg_color=CARD, hover_color=FIELD, text_color=MUTED,
                        font=ctk.CTkFont(family=ICON_FONT, size=13),
                        command=self.refresh_mics).grid(row=1, column=1, padx=(6, 0))

        self.section(inner, "Wake word", 2)
        self.wake_var = tk.StringVar(value=self.settings["wake"] or NO_WAKE)
        self.menu(inner, self.wake_var, [NO_WAKE] + controller.WAKE_WORDS,
                  lambda v: self.on_setting_changed()).grid(row=3, column=0, columnspan=2, sticky="ew")

        self.section(inner, "Strictness", 4)
        self.conf_value = ctk.CTkLabel(inner, text="", text_color=MUTED, font=font(12))
        self.conf_value.grid(row=4, column=1, sticky="e", pady=(10, 4))
        self.conf_var = tk.DoubleVar(value=self.settings["min_conf"])
        ctk.CTkSlider(inner, from_=0.3, to=0.95, variable=self.conf_var, height=16,
                      fg_color=FIELD, progress_color=LAVENDER, button_color=SLIDER_KNOB,
                      button_hover_color=SLIDER_KNOB_HOVER,
                      command=lambda v: self.on_conf_changed()).grid(
            row=5, column=0, columnspan=2, sticky="ew")
        ctk.CTkLabel(inner, text="Higher ignores more background noise; lower hears you more easily.",
                     text_color=MUTED, font=font(11), wraplength=330, justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.on_conf_changed(save=False)

        self.auto_var = tk.BooleanVar(value=self.settings["autostart"])
        self.switch(inner, "Start listening when opened", self.auto_var, self.save).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.night_var = tk.BooleanVar(value=self.settings["night"])
        self.switch(inner, "Night mode", self.night_var, self.toggle_night).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))

        # Console
        con = ctk.CTkFrame(outer, fg_color=BG)
        con.pack(fill="x", pady=(14, 0))
        ctk.CTkLabel(con, text="CONSOLE", text_color=MUTED, font=font(11, bold=True)).pack(
            side="left", padx=4)
        self.console_var = tk.BooleanVar(value=self.settings["console"])
        self.switch(con, "", self.console_var, self.toggle_console, width=40).pack(side="right")
        self.log = ctk.CTkTextbox(outer, fg_color=CARD, text_color=INK, corner_radius=16,
                                  border_width=0, font=font(12), height=CONSOLE_MIN_H,
                                  activate_scrollbars=False)
        if self.console_var.get():
            self.log.pack(fill="both", expand=True, pady=(4, 0))
        self.color_log()
        self.log.configure(state="disabled")

    def toggle_console(self):
        if self.console_var.get():
            self.log.pack(fill="both", expand=True, pady=(4, 0))
        else:
            self.log.pack_forget()
        self.save()
        self.fit_window()

    def toggle_night(self):
        ctk.set_appearance_mode("dark" if self.night_var.get() else "light")
        self.canvas.configure(bg=shade(BG))
        self.color_log()
        tint_title_bar(self.root)
        self.save()

    def color_log(self):
        # Text tags are plain tk, so they don't follow the appearance mode by themselves.
        self.log.tag_config("ok", foreground=shade(MINT_DEEP))
        self.log.tag_config("dim", foreground=shade(MUTED))
        self.log.tag_config("err", foreground=shade(PEACH_DEEP))

    def fit_window(self):
        """Size the window to its contents (e.g. after showing/hiding the console),
        but no taller than the screen; anything beyond that scrolls."""
        self.root.update_idletasks()
        max_h = work_area_height(self.root) - 80  # room for the title bar
        want_h = self.content.winfo_reqheight()
        if self.console_var.get():
            want_h += round(CONSOLE_EXTRA_H * ctk.ScalingTracker.get_widget_scaling(self.root))
        self.canvas.configure(width=self.content.winfo_reqwidth(), height=min(want_h, max_h))
        tk.Tk.wm_geometry(self.root, "")

    def update_scroll(self):
        """Stretch the content to fill the window, and show the scrollbar only when
        the window is shorter than the content."""
        view_w, view_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        need_h = self.content.winfo_reqheight()
        self.canvas.itemconfigure(self.content_id, width=view_w, height=max(view_h, need_h))
        self.canvas.configure(scrollregion=(0, 0, view_w, max(view_h, need_h)))
        overflow = need_h > view_h + 1
        if overflow and not self.scrollbar.winfo_ismapped():
            self.scrollbar.pack(side="right", fill="y", before=self.canvas)
        elif not overflow and self.scrollbar.winfo_ismapped():
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)

    def close_popup_if_inactive(self):
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        if focused is None:  # another app took focus
            Popup.close_open()

    def on_wheel(self, event):
        Popup.close_open()
        if not self.scrollbar.winfo_ismapped():
            return
        # The console scrolls itself; leave its wheel events alone.
        if str(event.widget).startswith(str(self.log)):
            return
        self.canvas.yview_scroll(int(-event.delta / 120) * 2, "units")

    def card(self, parent, pady):
        c = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=20)
        c.pack(fill="x", pady=pady)
        return c

    def section(self, parent, text, row):
        ctk.CTkLabel(parent, text=text.upper(), text_color=MUTED, font=font(11, bold=True)).grid(
            row=row, column=0, sticky="w", pady=(0 if row == 0 else 10, 4))

    def switch(self, parent, text, var, command, **kw):
        sw = ctk.CTkSwitch(parent, text=text, variable=var, onvalue=True, offvalue=False,
                           command=command, text_color=INK, font=font(13),
                           fg_color=SWITCH_TRACK, progress_color=SWITCH_ON, **kw)

        def knob(*_):
            color = KNOB_ON if var.get() else KNOB_OFF
            sw.configure(button_color=color, button_hover_color=color)

        var.trace_add("write", knob)  # follows the value however it changes
        knob()
        return sw

    def menu(self, parent, var, values, command):
        return Dropdown(parent, variable=var, values=values, command=command,
                        height=36, corner_radius=12, fg_color=FIELD,
                        button_color=FIELD, button_hover_color=FIELD_HOVER,
                        text_color=INK, font=font(13), dynamic_resizing=False)

    # ---------- settings ----------

    def refresh_mics(self):
        self.mics = controller.list_microphones()
        names = [DEFAULT_MIC] + [name for _, name in self.mics]
        self.mic_menu.configure(values=names)
        self.mic_var.set(self.settings["mic"] if self.settings["mic"] in names else DEFAULT_MIC)

    def selected_device(self):
        name = self.mic_var.get()
        return next((i for i, n in self.mics if n == name), None)

    def selected_wake(self):
        w = self.wake_var.get()
        return None if w == NO_WAKE else w

    def update_hint(self):
        w = self.selected_wake()
        if w:
            self.hint.configure(text=f"Say “{w} play”, “{w} pause”, “{w} next” or “{w} previous”,\n"
                                     f"or “{w} play <song> by <artist>”.")
        else:
            self.hint.configure(text="Say “play”, “pause”, “next”, “previous”\n"
                                     "or “play <song> by <artist>”. Over songs with vocals,\n"
                                     "a wake word is far more reliable.")

    def on_setting_changed(self):
        self.update_hint()
        self.save()
        if self.listener:  # mic and wake word need a fresh listener
            self.stop()
            self.start()

    def on_conf_changed(self, save=True):
        value = round(self.conf_var.get(), 2)
        self.conf_value.configure(text=f"{value:.2f}")
        if self.listener:
            self.listener.min_conf = value
        if save:
            self.save()

    def save(self):
        self.settings = {"mic": self.mic_var.get(), "wake": self.selected_wake(),
                         "min_conf": round(self.conf_var.get(), 2),
                         "autostart": self.auto_var.get(),
                         "console": self.console_var.get(),
                         "night": self.night_var.get()}
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2))
        except OSError as e:
            self.write_log(f"Couldn't save settings: {e}", "err")

    # ---------- listening ----------

    def toggle(self):
        self.stop() if self.listener else self.start()

    def start(self):
        self.error = False
        self.set_button("Starting…", FIELD, FIELD_HOVER, text_color=INK)
        self.listener = controller.Listener(
            lambda kind, data: self.events.put((kind, data)),
            device=self.selected_device(), wake=self.selected_wake(),
            min_conf=round(self.conf_var.get(), 2))
        self.listener.start()

    def stop(self):
        if self.listener:
            self.listener.stop()
            self.listener.join(2)
            self.listener = None
        self.show_idle()

    def show_idle(self):
        self.level.set(0)
        self.level.configure(progress_color=FIELD)
        if self.error:
            self.set_button("Something went wrong · try again", PEACH, PEACH_HOVER)
        else:
            self.set_button("Start listening", LAVENDER, LAVENDER_HOVER)

    def set_button(self, text, color, hover, text_color=ON_PASTEL):
        self.listen_btn.configure(text=text, fg_color=color, hover_color=hover,
                                  text_color=text_color)

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                self.handle(kind, data)
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def handle(self, kind, data):
        if kind == "level":
            if self.listener:
                self.level.set(data)
        elif kind == "status" and data == "listening":
            self.set_button("Listening · tap to stop", MINT, MINT_HOVER)
            self.level.configure(progress_color=MINT)
        elif kind == "status" and data == "stopped" and self.listener and not self.listener.is_alive():
            # The listener ended on its own (e.g. mic unplugged).
            self.listener = None
            self.show_idle()
        elif kind == "error":
            self.error = True
            self.write_log(f"Error: {data}", "err")
        elif kind == "heard":
            if data["action"] == "search":
                self.write_log("Song request heard, working out which song…", "dim")
            elif data["action"]:
                note = f"  ({data['note']})" if data["note"] else ""
                self.write_log(f"“{data['text']}” → {data['action']}{note}", "ok")
                if data["action"] in ("play", "pause") and self.am_state != "closed":
                    self.show_state("playing" if data["action"] == "play" else "paused")
            else:
                why = data["note"] or (f"{data['conf']:.2f}" if data["conf"] is not None else "")
                self.write_log(f"“{data['text']}” ignored" + (f" ({why})" if why else ""), "dim")
        elif kind == "search":
            self.handle_search(data)

    def handle_search(self, data):
        stage = data["stage"]
        if stage == "heard":
            self.write_log(f"Looking up “{data['query']}” in Apple Music…", "dim")
        elif stage == "playing":
            self.write_log(f"♪ {data['title']} · {data['artist']}", "ok")
            self.show_state("playing")
        elif stage == "resumed":
            self.write_log("“play” → play", "ok")
            self.show_state("playing")
        elif stage == "failed":
            what = f"“{data['query']}”" if data.get("query") else "that song"
            self.write_log(f"Couldn't play {what}: {data['error']}", "err")

    # ---------- misc ----------

    def manual(self, action):
        if action == "toggle":
            action = "pause" if self.am_state == "playing" else "play"
        try:
            self.music = self.music or controller.AppleMusic()
            note = self.music.send(action)
            self.write_log(f"Button → {action}" + (f"  ({note})" if note else ""), "ok")
        except Exception as e:
            self.write_log(f"Error: {e}", "err")
            return
        if action in ("play", "pause"):
            # Flip the icon right away; the next state check confirms it.
            self.show_state("playing" if action == "play" else "paused")

    def check_apple_music(self):
        try:
            self.music = self.music or controller.AppleMusic()
            state = self.music.state()
        except Exception:
            state = "closed"
        self.show_state(state)
        self.root.after(1000, self.check_apple_music)

    def show_state(self, state):
        self.am_state = state
        if state == "closed":
            self.am_pill.configure(fg_color=FIELD, text_color=MUTED, image=self.dots["closed"])
        else:
            self.am_pill.configure(fg_color=MINT_TINT, text_color=MINT_DEEP, image=self.dots["open"])
        self.play_btn.configure(text=ICONS["pause"] if state == "playing" else ICONS["play"])

    def write_log(self, line, tag):
        self.log.configure(state="normal")
        # Newest first, so the latest command is always visible.
        self.log.insert("1.0", f"{time.strftime('%H:%M')}   {line}\n", tag)
        self.log.delete("101.0", "end")
        self.log.configure(state="disabled")

    def on_close(self):
        self.save()
        self.stop()
        self.root.destroy()


def dot_image(colors, size=8, gap=6, lift=1):
    """A smooth status dot followed by `gap` px of transparent space before the text,
    in both appearance modes. `lift` px of empty space below the dot raises it to
    line up with the text."""
    k = 8  # draw large, let CTkImage scale down for anti-aliasing

    def draw(color):
        img = Image.new("RGBA", ((size + gap) * k, (size + lift) * k), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((0, 0, size * k - 1, size * k - 1), fill=color)
        return img

    return ctk.CTkImage(light_image=draw(colors[0]), dark_image=draw(colors[1]),
                        size=(size + gap, size + lift))


def work_area_height(root):
    """Usable height (minus taskbar) of the window's monitor, in real pixels.
    winfo_screenheight() reports unscaled pixels, which don't match widget sizes."""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.wintypes.RECT),
                    ("rcWork", ctypes.wintypes.RECT), ("dwFlags", ctypes.c_ulong)]
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        MONITOR_DEFAULTTONEAREST = 2
        monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        return info.rcWork.bottom - info.rcWork.top
    except Exception:
        return root.winfo_screenheight()


def set_window_icon(root, path):
    """Give Windows the icon at exactly the sizes it draws for this screen's scaling
    (title bar and taskbar). Tk picks sizes itself and Windows then stretches them,
    which looks blurry."""
    if not path.exists():
        return
    try:
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                        ctypes.c_void_p]
        hwnd = user32.GetParent(root.winfo_id())
        dpi = user32.GetDpiForWindow(ctypes.c_void_p(hwnd))
        SM_CXICON, SM_CXSMICON = 11, 49
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        for which, metric in ((0, SM_CXSMICON), (1, SM_CXICON)):  # ICON_SMALL, ICON_BIG
            size = user32.GetSystemMetricsForDpi(metric, dpi)
            icon = user32.LoadImageW(None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if icon:
                user32.SendMessageW(hwnd, WM_SETICON, which, icon)
    except Exception:
        pass


def tint_title_bar(root):
    """Match the Windows 11 title bar to the window background and text colours."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi

        def set_attr(attr, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        def colorref(hex_color):
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            return r | g << 8 | b << 16

        DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 20, 35, 36
        set_attr(DWMWA_USE_IMMERSIVE_DARK_MODE, int(ctk.get_appearance_mode() == "Dark"))
        set_attr(DWMWA_CAPTION_COLOR, colorref(shade(BG)))
        set_attr(DWMWA_TEXT_COLOR, colorref(shade(INK)))
    except Exception:
        pass


def selftest(root, out_path):
    """Check that everything the app needs is present and working, write the results
    to out_path as JSON, and quit. Used to verify a fresh exe build (build.bat)."""
    import tkinter.font as tkfont

    def fonts():
        missing = [f for f in (SANS, SANS_BOLD, SERIF, ICON_FONT) if f not in tkfont.families()]
        if missing:
            raise RuntimeError(f"missing fonts: {missing}")
        return "all present"

    def icon():
        if not ICON_FILE.exists():
            raise FileNotFoundError(ICON_FILE)
        return str(ICON_FILE)

    def speech():
        controller.CommandDetector().feed(b"\0" * 3200)  # loads the model, runs the recognizer
        return "model loaded, recognizer ran"

    def song_search_model():
        import numpy as np
        import song_search
        searcher = song_search.SongSearcher(lambda *a: None)
        searcher._load()
        searcher.transcribe((np.zeros(16000, np.int16)).tobytes())
        return "Whisper model loaded, transcribed a test clip"

    def apple_music_ui():
        # UI Automation generates COM wrappers on first use; make sure that works here.
        import uiautomation as auto
        import apple_music_ui
        with auto.UIAutomationInitializerInThread():
            window = auto.WindowControl(searchDepth=1, ClassName="WinUIDesktopWin32WindowClass",
                                        Name="Apple Music")
            if not window.Exists(1):
                return "Apple Music not open; skipped"
            box = apple_music_ui.find(window, auto.ControlType.EditControl, automation_id="TextBox")
            if not box:
                raise RuntimeError("couldn't find Apple Music's search box")
            return "found Apple Music's search box"

    def mics():
        names = [n for _, n in controller.list_microphones()]
        if not names:
            raise RuntimeError("no microphones found")
        return names

    results = {}
    for name, check in (("fonts", fonts), ("icon", icon), ("speech model", speech),
                        ("song search model", song_search_model), ("apple music search", apple_music_ui),
                        ("microphones", mics), ("apple music link", lambda: controller.AppleMusic().state()),
                        ("settings file", lambda: str(SETTINGS_FILE))):
        try:
            results[name] = {"ok": True, "detail": check()}
        except Exception as e:
            results[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    Path(out_path).write_text(json.dumps(results, indent=2))
    root.destroy()


def main():
    # Own taskbar identity, so Windows shows our icon instead of Python's.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VoiceControl.AppleMusic")
    except Exception:
        pass
    load_fonts()
    ctk.set_appearance_mode("dark" if load_settings()["night"] else "light")
    root = Window()
    App(root)
    if "--selftest" in sys.argv:
        out = sys.argv[sys.argv.index("--selftest") + 1]
        root.after(2000, lambda: selftest(root, out))
    root.mainloop()


def report_crash():
    """The exe has no console, so a crash would vanish silently. Log it and say so."""
    log = DATA_DIR / "error.log"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc())
    except OSError:
        pass
    message = f"Voice Control hit an error and has to close.\n\nDetails were saved to:\n{log}"
    ctypes.windll.user32.MessageBoxW(None, message, "Voice Control", 0x10)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if not FROZEN:
            raise
        report_crash()
