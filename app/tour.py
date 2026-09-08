"""Coachmark engine: speech bubbles anchored to real controls, and the controller that walks a
list of `TourStep`s through them.

Deliberately content-free -- it knows about `MainWindow`'s widgets and signals, nothing about any
particular demo. `app/demos.py` and `app/demo_scripts.py` depend on this module; this module never
imports them.

Two frameless top-level tool windows (bubble + halo) rather than one child-widget overlay with a
spotlight cutout: the central widget is a native OpenGL `QtInteractor`, and Qt child widgets
stacked over a native GL surface are unreliable across platforms (`app/viewport.py`'s
offscreen-composite dance is the same class of problem, already worked around once). A top-level
window always stacks above the GL surface."""
import math
from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDockWidget, QDoubleSpinBox, QHBoxLayout, QLabel,
    QMenu, QMenuBar, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.theme import BG_PANEL, MONO_FAMILY, TELEMETRY, TEXT_PRIMARY, TEXT_SECONDARY, WARNING

# Pointer height, and therefore the transparent inset the rounded body sits inside on EVERY side
# (uniform, so the bubble's geometry doesn't change shape when the pointer moves sides).
POINTER = 14
POINTER_BASE = 22
CONTENT_WIDTH = 340
PAD = 12
BUBBLE_WIDTH = CONTENT_WIDTH + 2 * (POINTER + PAD)
# Gap between the pointer tip and the target's edge.
GAP = 8
# Keep the pointer's base off the rounded corners, where it would meet a curve instead of the
# straight edge and read as a torn-off flap.
POINTER_CORNER_INSET = 20


@dataclass(frozen=True)
class TourStep:
    """One coachmark. `target` is a dotted getattr path on `MainWindow` (`"run_btn"`,
    `"page_stations.table"`); None centers the bubble over the viewport with no pointer or halo.

    `advance` is either "next" (the user clicks Next) or a key in `TourController.SIGNAL_KEYS`
    (the step waits for the app to actually do the thing). `expect` may only appear on a
    "next" step -- see `TourController.__init__` for why -- and `on_enter` is one of the four
    names in `TourController.ON_ENTER`, not a scripting language."""

    target: str | None
    title: str
    body: str
    advance: str = "next"
    constrain: bool = False
    expect: dict | None = None
    on_enter: str | None = None


def _resolve_target(window, path: str):
    """Dotted getattr chain against the main window. Raises `AttributeError` on a typo rather
    than returning None: every demo script's every target is resolved against a real offscreen
    window in the test suite, so a bad path fails CI instead of a user's tour."""
    obj = window
    for name in path.split("."):
        obj = getattr(obj, name)
    return obj


def _pretty_label(path: str) -> str:
    """Human name for an option widget from its attribute path, for the correction bullets --
    "sections_spin" -> "Sections", "chord_tol_auto" -> "Chord tol auto". Derived rather than
    tabled so this module stays free of any particular demo's vocabulary."""
    name = path.split(".")[-1]
    for suffix in ("_spin", "_check", "_combo", "_edit", "_btn"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.replace("_", " ").strip().capitalize()


def _widget_value(widget):
    """Current value of an option widget, in the same type the `expect` dict states it in.
    Returns `NotImplemented` for a widget kind `expect` cannot speak about, so a mis-typed
    expectation surfaces as a loud mismatch rather than silently passing."""
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QComboBox):
        return widget.currentText()
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    return NotImplemented


def _values_match(actual, expected) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return bool(actual) == bool(expected)
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) < 1e-9
    return actual == expected


def _fmt_value(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


class CoachmarkBubble(QWidget):
    """The speech bubble: a hand-painted rounded rect with a triangular pointer on whichever
    side faces the target, with ordinary child widgets laid out on top of it."""

    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self, window=None):
        super().__init__(window, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("coachmarkBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # The app-wide QSS paints `QWidget { background-color: BG_DARK }`; state it away
        # explicitly here rather than trusting that a custom QWidget subclass keeps escaping
        # that rule, which would fill the transparent pointer margin with a grey square and
        # destroy the bubble shape.
        self.setStyleSheet("#coachmarkBubble { background: transparent; }")
        self.setFixedWidth(BUBBLE_WIDTH)

        self._pointer_side = None
        self._pointer_along = 0
        self._plain_body = ""
        self._in_correction = False

        self.title_label = QLabel()
        self.title_label.setWordWrap(True)
        title_font = self.title_label.font()
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        self.counter_label = QLabel()
        counter_font = self.counter_label.font()
        counter_font.setFamilies([f.strip(' "') for f in MONO_FAMILY.split(",")])
        self.counter_label.setFont(counter_font)
        self.counter_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.counter_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        title_row.addWidget(self.title_label)
        title_row.addStretch(1)
        title_row.addWidget(self.counter_label)

        self.body_label = QLabel()
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self.body_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.back_btn = QPushButton("Back")
        self.back_btn.setToolTip(
            "Re-reads the previous step. It does not undo anything you already ran.")
        self.back_btn.clicked.connect(self.back_clicked)
        self.skip_btn = QPushButton("End tour")
        self.skip_btn.clicked.connect(self.skip_clicked)
        self.next_btn = QPushButton("Next ▸")
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self.next_clicked)
        # The "waiting" hint gets a full-width row of its own between the body and the buttons,
        # rather than sitting inline where Next was: at the bubble's 340px content width it wraps
        # to two lines, and inline that left it colliding with the button row and vertically
        # misaligned against it (screenshot pass, out/tour_review/waiting.png). On its own row it
        # also reads as what it is -- a live status line attached to the instruction above it.
        self.waiting_label = QLabel()
        waiting_font = self.waiting_label.font()
        waiting_font.setItalic(True)
        self.waiting_label.setFont(waiting_font)
        self.waiting_label.setStyleSheet(f"color: {TELEMETRY};")
        self.waiting_label.setWordWrap(True)
        self.waiting_label.hide()

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        button_row.addWidget(self.back_btn)
        button_row.addStretch(1)
        button_row.addWidget(self.skip_btn)
        button_row.addWidget(self.next_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(POINTER + PAD, POINTER + PAD, POINTER + PAD, POINTER + PAD)
        layout.setSpacing(10)
        layout.addLayout(title_row)
        layout.addWidget(self.body_label)
        layout.addWidget(self.waiting_label)
        layout.addLayout(button_row)

    # ---- content --------------------------------------------------------
    def set_step(self, title: str, body: str, index: int, total: int, waiting: str | None = None):
        """Fill the bubble for one step. `waiting` non-None replaces the Next button with the
        passive "the app is waiting for you to do something" hint."""
        self._in_correction = False
        self._plain_body = body
        self.title_label.setText(title)
        self.counter_label.setText(f"{index + 1} / {total}")
        self.body_label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_label.setText(body)
        self.back_btn.setVisible(index > 0)
        self.next_btn.setText("Next ▸")
        self.set_waiting(waiting)

    def set_waiting(self, text: str | None):
        self.waiting_label.setVisible(bool(text))
        self.waiting_label.setText(text or "")
        self.next_btn.setVisible(not text)
        self._refit()

    def show_correction(self, lines):
        """Replace the body with the "fix these first" list and turn Next into Re-check."""
        self._in_correction = True
        bullets = "".join(
            f'<div style="color:{WARNING}; margin-top:3px;">&#8226;&nbsp;{escape(line)}</div>'
            for line in lines)
        self.body_label.setTextFormat(Qt.TextFormat.RichText)
        self.body_label.setText(
            f'<div style="color:{TEXT_PRIMARY};">Before continuing, fix these settings:</div>'
            f'<div style="margin-top:6px;">{bullets}</div>')
        self.next_btn.setText("Re-check")
        self.next_btn.setVisible(True)
        self.waiting_label.setVisible(False)
        self._refit()

    def clear_correction(self):
        if not self._in_correction:
            return
        self._in_correction = False
        self.body_label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_label.setText(self._plain_body)
        self.next_btn.setText("Next ▸")
        self._refit()

    @property
    def in_correction(self) -> bool:
        return self._in_correction

    def _refit(self):
        """Size the bubble to its wrapped text.

        A word-wrapped QLabel's own `sizeHint()` is its UNWRAPPED width, so `adjustSize()` on the
        parent would size the bubble to one very long line -- the label's height has to be pinned
        from `heightForWidth` at the width it will actually get before the layout is asked
        anything."""
        counter_w = self.counter_label.sizeHint().width()
        title_w = max(80, CONTENT_WIDTH - counter_w - 10)
        self.title_label.setFixedWidth(title_w)
        self.title_label.setFixedHeight(max(1, self.title_label.heightForWidth(title_w)))
        self.body_label.setFixedWidth(CONTENT_WIDTH)
        self.body_label.setFixedHeight(max(1, self.body_label.heightForWidth(CONTENT_WIDTH)))
        if self.waiting_label.isVisibleTo(self):
            self.waiting_label.setFixedWidth(CONTENT_WIDTH)
            self.waiting_label.setFixedHeight(
                max(1, self.waiting_label.heightForWidth(CONTENT_WIDTH)))
        self.adjustSize()
        self.setFixedWidth(BUBBLE_WIDTH)

    # ---- geometry -------------------------------------------------------
    def set_pointer(self, side: str | None, along: int):
        """`side` is the bubble edge the triangle sticks out of (so a bubble sitting to the RIGHT
        of its target has a "left" pointer). `along` is measured in widget coordinates: y for
        left/right, x for top/bottom."""
        self._pointer_side = side
        if side in ("left", "right"):
            lo, hi = POINTER + POINTER_CORNER_INSET, self.height() - POINTER - POINTER_CORNER_INSET
        else:
            lo, hi = POINTER + POINTER_CORNER_INSET, self.width() - POINTER - POINTER_CORNER_INSET
        self._pointer_along = int(min(max(along, lo), max(lo, hi)))
        self.update()

    def place_near(self, target_global_rect: QRect, window_screen):
        """Position the bubble beside `target_global_rect`, preferring right, then left, then
        below, then above -- the first side where it fits inside the screen's available geometry
        with a `GAP` px gap. Returns the pointer side used, or None for the centered fallback.

        Pure geometry: `window_screen` only has to answer `availableGeometry()`, so this is unit
        tested directly with synthetic rects and never needs a shown widget."""
        self._refit()
        avail = window_screen.availableGeometry()
        w, h = self.width(), self.height()
        t = target_global_rect
        candidates = (
            ("left", t.right() + 1 + GAP, t.center().y() - h // 2),
            ("right", t.left() - GAP - w, t.center().y() - h // 2),
            ("top", t.center().x() - w // 2, t.bottom() + 1 + GAP),
            ("bottom", t.center().x() - w // 2, t.top() - GAP - h),
        )
        for side, x, y in candidates:
            if side == "left" and x + w - 1 > avail.right():
                continue
            if side == "right" and x < avail.left():
                continue
            if side == "top" and y + h - 1 > avail.bottom():
                continue
            if side == "bottom" and y < avail.top():
                continue
            if side in ("left", "right"):
                y = min(max(y, avail.top()), max(avail.top(), avail.bottom() - h + 1))
                along = t.center().y() - y
            else:
                x = min(max(x, avail.left()), max(avail.left(), avail.right() - w + 1))
                along = t.center().x() - x
            self.move(x, y)
            self.set_pointer(side, along)
            return side
        self.place_centered(t, window_screen)
        return None

    def place_centered(self, rect: QRect, window_screen):
        """Centered-over-`rect` fallback: no pointer, because there is nothing it could honestly
        point at (target off-screen, hidden in a closed dock, or a step with no target at all)."""
        self._refit()
        avail = window_screen.availableGeometry()
        w, h = self.width(), self.height()
        x = min(max(rect.center().x() - w // 2, avail.left()),
                max(avail.left(), avail.right() - w + 1))
        y = min(max(rect.center().y() - h // 2, avail.top()),
                max(avail.top(), avail.bottom() - h + 1))
        self.move(x, y)
        self.set_pointer(None, 0)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect().adjusted(POINTER, POINTER, -POINTER, -POINTER))
        shape = QPainterPath()
        shape.addRoundedRect(body, 6, 6)
        polygon = self._pointer_polygon(body)
        if polygon is not None:
            triangle = QPainterPath()
            triangle.addPolygon(polygon)
            triangle.closeSubpath()
            # United, not two separate draws: the border pen would otherwise stroke the
            # triangle's base straight across the bubble edge it is supposed to grow out of.
            shape = shape.united(triangle)
        painter.setBrush(QColor(BG_PANEL))
        painter.setPen(QPen(QColor(TELEMETRY), 1))
        painter.drawPath(shape)
        painter.end()

    def _pointer_polygon(self, body: QRectF):
        side, along = self._pointer_side, float(self._pointer_along)
        if side is None:
            return None
        half = POINTER_BASE / 2.0
        # The base sits 1px INSIDE the rounded rect so `united()` sees a real overlap and merges
        # the two shapes instead of leaving a hairline seam.
        if side == "left":
            return QPolygonF([QPointF(0.0, along),
                              QPointF(body.left() + 1, along - half),
                              QPointF(body.left() + 1, along + half)])
        if side == "right":
            return QPolygonF([QPointF(float(self.width()), along),
                              QPointF(body.right() - 1, along - half),
                              QPointF(body.right() - 1, along + half)])
        if side == "top":
            return QPolygonF([QPointF(along, 0.0),
                              QPointF(along - half, body.top() + 1),
                              QPointF(along + half, body.top() + 1)])
        return QPolygonF([QPointF(along, float(self.height())),
                          QPointF(along - half, body.bottom() - 1),
                          QPointF(along + half, body.bottom() - 1)])


class TargetHalo(QWidget):
    """The ring drawn around the control the bubble points at. Transparent to mouse events, so
    it never eats the very click it is asking for."""

    GROW = 6

    def __init__(self, window=None):
        super().__init__(window, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("targetHalo")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("#targetHalo { background: transparent; }")
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    def set_target_rect(self, rect: QRect):
        self.setGeometry(rect.adjusted(-self.GROW, -self.GROW, self.GROW, self.GROW))

    def start(self):
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def flash(self):
        """Jump the pulse back to full brightness -- the feedback for a click the tour swallowed,
        answering "why did nothing happen?" with "because it wants THIS control"."""
        self._phase = math.pi / 2
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.16) % (2 * math.pi)
        self.update()

    def alpha(self) -> int:
        return int(120 + 135 * (0.5 + 0.5 * math.sin(self._phase)))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(TELEMETRY)
        color.setAlpha(self.alpha())
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)), 4, 4)
        painter.end()


class TourController(QObject):
    """Walks one list of `TourStep`s. Owns the bubble, the halo, and an application-level event
    filter used for re-anchoring and for the soft input constraint."""

    finished = Signal()

    SIGNAL_KEYS = {
        "analyze_done": lambda w: w.analyze_finished,
        "rebuild_done": lambda w: w.rebuild_finished,
        "run_failed": lambda w: w.run_failed,
        # A verification check FAILING is in the report, not a Qt signal -- a script that cares
        # branches on `rebuild_finished`'s bool. The alias exists so a "watch it fail" step reads
        # as what it is.
        "verify_failed": lambda w: w.rebuild_finished,
        "file_loaded": lambda w: w.input_loaded,
        "swap_toggled": lambda w: w.swap_action.toggled,
        "section_toggled": lambda w: w.section_view_action.toggled,
        "deviation_toggled": lambda w: w.deviation_action.toggled,
    }

    WAITING_TEXT = {
        "analyze_done": "waiting — click Analyze to continue",
        "rebuild_done": "waiting — click Run to continue",
        "run_failed": "waiting — click Run to continue",
        "verify_failed": "waiting — click Run to continue",
        "file_loaded": "waiting — choose an input STL to continue",
        "swap_toggled": "waiting — toggle Swap input ↔ rebuilt (B) to continue",
        "section_toggled": "waiting — toggle Section view (S) to continue",
        "deviation_toggled": "waiting — toggle Deviation heatmap to continue",
    }

    ON_ENTER = {
        "select_outline_input": lambda w: w.outline.setCurrentItem(w.node_input),
        "select_outline_detected": lambda w: w.outline.setCurrentItem(w.node_detected),
        "select_outline_stations": lambda w: w.outline.setCurrentItem(w.node_stations),
        "select_outline_output": lambda w: w.outline.setCurrentItem(w.node_output),
    }

    def __init__(self, window, steps, title: str):
        super().__init__(window)
        for step in steps:
            if step.expect and step.advance != "next":
                # By the time a run's completion signal fires, the engine has already run with
                # whatever was set -- validating there would be reporting history, not
                # preventing a wrong run. Scripts put `expect` on the review step BEFORE the run.
                raise ValueError(
                    f"TourStep {step.title!r}: `expect` is only valid on advance='next' steps")
            if step.advance != "next" and step.advance not in self.SIGNAL_KEYS:
                raise ValueError(f"TourStep {step.title!r}: unknown advance {step.advance!r}")
            if step.on_enter and step.on_enter not in self.ON_ENTER:
                raise ValueError(f"TourStep {step.title!r}: unknown on_enter {step.on_enter!r}")
        self._window = window
        self._steps = list(steps)
        self._title = title
        self._i = -1
        self._bubble = None
        self._halo = None
        self._active = False
        self._constrained = False
        self._connected = None  # (bound signal, slot) for the current step's advance, if any
        self._filter_installed = False
        self._finished_emitted = False
        self.last_signal_args = ()

    # ---- lifecycle ------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    @property
    def index(self) -> int:
        return self._i

    @property
    def title(self) -> str:
        """The tour's own name, for whatever starts it to log or display."""
        return self._title

    @property
    def bubble(self):
        return self._bubble

    @property
    def halo(self):
        return self._halo

    @property
    def filter_installed(self) -> bool:
        """Whether this controller is currently filtering application events. QApplication does
        not expose its installed filters, so a tour that fails to remove its own would silently
        keep swallowing clicks after it ended -- this is the only thing a test can assert on."""
        return self._filter_installed

    def start(self):
        if self._active or not self._steps:
            return
        self._active = True
        self._bubble = CoachmarkBubble(self._window)
        self._bubble.next_clicked.connect(self._on_next_clicked)
        self._bubble.back_clicked.connect(self._on_back_clicked)
        self._bubble.skip_clicked.connect(self.cancel)
        self._halo = TargetHalo(self._window)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._filter_installed = True
        self._show_step(0)

    def cancel(self):
        self._finish()

    def _finish(self):
        if not self._active:
            return
        self._active = False
        self._disconnect_advance()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._filter_installed = False
        if self._halo is not None:
            self._halo.stop()  # the pulse timer outlives close() otherwise
        for widget in (self._bubble, self._halo):
            if widget is not None:
                widget.close()
                widget.deleteLater()
        self._bubble = None
        self._halo = None
        if not self._finished_emitted:
            self._finished_emitted = True
            self.finished.emit()

    # ---- steps ----------------------------------------------------------
    def _show_step(self, i: int):
        if not self._active:
            return
        if i < 0 or i >= len(self._steps):
            self._finish()
            return
        self._disconnect_advance()
        self._i = i
        step = self._steps[i]
        if step.on_enter:
            self.ON_ENTER[step.on_enter](self._window)
        self._constrained = bool(step.constrain)
        waiting = None if step.advance == "next" else self.WAITING_TEXT.get(
            step.advance, "waiting — perform the action above")
        target = self._current_target()
        self._bubble.set_step(step.title, self._body_for(step, target), i, len(self._steps),
                              waiting)
        if step.advance != "next":
            slot = self._on_step_signal
            signal = self.SIGNAL_KEYS[step.advance](self._window)
            signal.connect(slot)
            self._connected = (signal, slot)
        self._reanchor()
        self._bubble.show()

    def _body_for(self, step, target) -> str:
        """The step's text, with an honest one-line prefix when its control is currently out of
        sight -- a closed dock mid-tour would otherwise leave the bubble centered over the
        viewport talking about a widget the reader cannot find."""
        if step.target and (target is None or not target.isVisible()):
            dock = self._dock_of(target) if target is not None else None
            where = f"the {dock.windowTitle()} dock" if dock is not None else "a closed panel"
            return (f"({_pretty_label(step.target)} is in {where} — reopen it via the View menu)"
                    f"\n\n{step.body}")
        return step.body

    @staticmethod
    def _dock_of(widget):
        parent = widget
        while parent is not None:
            if isinstance(parent, QDockWidget):
                return parent
            parent = parent.parentWidget()
        return None

    def _current_target(self):
        step = self._steps[self._i] if 0 <= self._i < len(self._steps) else None
        if step is None or step.target is None:
            return None
        return _resolve_target(self._window, step.target)

    def _screen(self):
        screen = self._window.screen() if self._window is not None else None
        return screen or QApplication.primaryScreen()

    def _reanchor(self):
        if not self._active or self._bubble is None:
            return
        target = self._current_target()
        screen = self._screen()
        if screen is None:
            return
        if target is None or not target.isVisible():
            self._halo.stop()
            anchor = self._window.viewport if self._window is not None else None
            rect = (QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
                    if anchor is not None else screen.availableGeometry())
            self._bubble.place_centered(rect, screen)
            return
        rect = QRect(target.mapToGlobal(QPoint(0, 0)), target.size())
        self._halo.set_target_rect(rect)
        self._halo.start()
        self._bubble.place_near(rect, screen)

    # ---- advancing ------------------------------------------------------
    def _on_next_clicked(self):
        self._advance()

    def _on_back_clicked(self):
        # Back re-reads earlier text only. It deliberately does NOT undo anything the user
        # already did -- a completed rebuild stays completed. Guarded rather than relying on the
        # button being hidden on step 0: `_show_step(-1)` would END the tour, which is the last
        # thing "Back" should ever do.
        if self._i <= 0:
            return
        self._bubble.clear_correction()
        self._show_step(self._i - 1)

    def _on_step_signal(self, *args):
        """The app did the thing this step was waiting for. `last_signal_args` is kept so a
        script can branch on it (e.g. `rebuild_finished`'s all-checks-passed bool)."""
        self.last_signal_args = args
        if self._active:
            self._show_step(self._i + 1)

    def _advance(self):
        step = self._steps[self._i]
        mismatches = self.check_expectations(step)
        if mismatches:
            self._bubble.show_correction(mismatches)
            self._reanchor()
            return
        self._bubble.clear_correction()
        self._show_step(self._i + 1)

    def check_expectations(self, step) -> list:
        """Bullets for every option widget whose value differs from `step.expect`; empty when
        the settings are right (or the step has no expectations)."""
        lines = []
        for path, expected in (step.expect or {}).items():
            try:
                widget = _resolve_target(self._window, path)
            except AttributeError:
                lines.append(f"{_pretty_label(path)}: control not found")
                continue
            actual = _widget_value(widget)
            if actual is NotImplemented:
                lines.append(f"{_pretty_label(path)}: cannot be checked automatically")
            elif not _values_match(actual, expected):
                lines.append(
                    f"{_pretty_label(path)}: {_fmt_value(actual)} → set to {_fmt_value(expected)}")
        return lines

    def _disconnect_advance(self):
        if self._connected is None:
            return
        signal, slot = self._connected
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass
        self._connected = None

    # ---- app-level event filter ----------------------------------------
    def eventFilter(self, obj, event):
        if not self._active:
            return False
        event_type = event.type()
        if event_type == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            # Not while a modal dialog is up: Esc there belongs to the dialog's own Cancel, and
            # stealing it would end the tour instead of closing what the user is looking at.
            if QApplication.activeModalWidget() is None:
                self.cancel()
                return True
            return False
        if event_type in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.LayoutRequest,
                          QEvent.Type.Show, QEvent.Type.Hide):
            if self._affects_anchor(obj):
                # singleShot(0) coalesces the burst of Move/Resize/LayoutRequest a single window
                # drag or dock resize produces into one reposition.
                QTimer.singleShot(0, self._reanchor)
            return False
        if self._constrained and event_type in (QEvent.Type.MouseButtonPress,
                                                QEvent.Type.MouseButtonDblClick):
            if not self._click_allowed(obj):
                if self._halo is not None:
                    self._halo.flash()
                return True
        return False

    def _affects_anchor(self, obj) -> bool:
        if obj is self._window:
            return True
        target = None
        try:
            target = self._current_target()
        except AttributeError:
            return False
        if target is None or not isinstance(obj, QWidget):
            return False
        parent = target
        while parent is not None:
            if parent is obj:
                return True
            parent = parent.parentWidget()
        return False

    def _click_allowed(self, obj) -> bool:
        """Soft constraint: while an action step is asking for a specific click, clicks elsewhere
        are swallowed -- but menus, dialogs and the menubar always stay live so Cancel, Quit and
        native behavior are never trapped. Keyboard is never constrained."""
        if not isinstance(obj, QWidget):
            return True
        try:
            target = self._current_target()
        except AttributeError:
            target = None
        allowed_roots = [w for w in (target, self._bubble, self._halo,
                                     self._window.menuBar() if self._window else None)
                         if w is not None]
        widget = obj
        while widget is not None:
            if widget in allowed_roots:
                return True
            if isinstance(widget, (QMenu, QMenuBar, QMessageBox, QDialog)):
                return True
            widget = widget.parentWidget()
        return False
