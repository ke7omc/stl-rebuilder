"""Instrument-cluster dashboard: analog progress gauges + log + verification readout, replacing
the plain progress bar/log dock (MISSION §12/G3, Brady's 2026-09-05 live-testing feedback: an
M13-scale run sits silent for long stretches with nothing to show it's still working, and asked
for a NASA/aeronautical-themed dashboard -- one dial per phase, a "space-shuttle screen" for the
verification checks, with the log moved onto the same panel)."""
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from app.theme import (
    BG_DARKEST, BG_PANEL, BORDER, ERROR, MONO_FAMILY, SUCCESS, TELEMETRY, TEXT_DISABLED,
    TEXT_SECONDARY,
)
from pipeline import engine

# 270-degree sweep like a car speedometer: needle rests down-left at 0%, sweeps clockwise through
# straight-up, to down-right at 100%. Qt angles are counterclockwise from 3 o'clock.
_START_ANGLE_DEG = 225.0
_SWEEP_DEG = -270.0

# "active" is TELEMETRY (instrument cyan), not ACCENT (interaction blue) -- 2026-09-07 design
# review: reusing the button/selection color for "the machine is working" made the dashboard read
# as generic-IDE rather than aeronautical-instrument, since a busy dial looked identical to a
# clicked button. Aeronautical displays code live data in a color reserved for exactly that.
_STATE_COLORS = {"idle": TEXT_DISABLED, "active": TELEMETRY, "done": SUCCESS, "error": ERROR}
_MONO_FAMILIES = [f.strip(' "') for f in MONO_FAMILY.split(",")]

# Engine stage name -> dial index. "analyze" is `engine.analyze()`'s own progress (a separate
# operation from a rebuild, fed by AnalyzeWorker rather than RebuildWorker -- Brady, 2026-09-06:
# "add an Analyze dial on the left side of Load"). The rest are `engine.rebuild()`'s on_progress
# stages (MISSION §12/G3); "refine" is the verify-and-refine retry pass's renamed "stations"
# stage (pipeline/engine.py's _RefineProgressRelay) -- sharing the Sectioning dial is the
# correct read, not a separate phase.
STAGE_DIAL = {"analyze": 0, "load": 1, "scan": 2, "stations": 3, "refine": 3, "build": 4}
DIAL_TITLES = ("ANALYZE", "LOAD", "SCAN", "SECTIONING", "BUILD")


def _scale_font(font: QFont, factor: float):
    """Scale a font's size in place, in whichever unit it's actually set with -- a font that
    only has a pixel size set (common under `QT_QPA_PLATFORM=offscreen`, and in the smoke test)
    reports `pointSizeF() == -1`, and multiplying that by `factor` produces an invalid negative
    size Qt rejects with a runtime warning."""
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * factor)
    elif font.pixelSize() > 0:
        font.setPixelSize(max(1, round(font.pixelSize() * factor)))


# Seven-segment digit readout (2026-09-08, Brady's request: "old LED alarm clock" style for the
# percentage below each dial, keeping the existing state colors rather than clock-red). No
# segment-style font is installed on this machine and none can be fetched (this repo's Bash
# guard blocks network tools), so the digits are hand-drawn -- segments a(top)/b(top-right)/
# c(bottom-right)/d(bottom)/e(bottom-left)/f(top-left)/g(middle), the standard 7-segment layout.
# "-" (used for the idle "--" readout) lights only the middle segment, matching how a real LED
# clock shows a dash.
_SEGMENTS_ON = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgecd", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "-": "g", " ": "",
}


def _draw_seven_segment_digit(p: QPainter, rect: QRectF, char: str, on_color: QColor,
                               off_color: QColor, thickness: float):
    """One digit position. Unlit segments are drawn faintly (`off_color`, expected to already
    carry a low alpha) rather than omitted -- a real LED display shows the whole segment
    template, not just the lit strokes, which is a real part of why it reads as "LED" rather
    than an arbitrary shape."""
    on = set(_SEGMENTS_ON.get(char, ""))
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    t = thickness
    mid_y = y + h / 2
    segments = {
        "a": (x + t, y, x + w - t, y),
        "b": (x + w, y + t, x + w, mid_y - t * 0.6),
        "c": (x + w, mid_y + t * 0.6, x + w, y + h - t),
        "d": (x + t, y + h, x + w - t, y + h),
        "e": (x, mid_y + t * 0.6, x, y + h - t),
        "f": (x, y + t, x, mid_y - t * 0.6),
        "g": (x + t * 0.6, mid_y, x + w - t * 0.6, mid_y),
    }
    for name, (x1, y1, x2, y2) in segments.items():
        pen = QPen(on_color if name in on else off_color, t)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _draw_led_readout(p: QPainter, box: QRectF, text: str, color: QColor):
    """The full bezeled LED display: a dark rounded housing (flat, no gradient/bevel -- matches
    the dial face's own restrained styling) containing one 7-segment digit per character of
    `text` (non-digit/dash characters, e.g. "%", fall back to plain small text alongside the
    segment digits rather than being forced into a segment shape they don't suit)."""
    p.setPen(QPen(QColor(BORDER), 1.0))
    p.setBrush(QColor("#0a0b0d"))
    p.drawRoundedRect(box, 3, 3)

    digits = [c for c in text if c in _SEGMENTS_ON]
    suffix = text[len(digits):]  # e.g. "%" -- rendered as plain text, not a segment shape
    pad = box.height() * 0.16
    digit_w = box.width() * 0.62 / max(len(digits), 1) if digits else 0
    digit_h = box.height() - 2 * pad
    gap = digit_w * 0.18
    total_digits_w = len(digits) * digit_w + max(len(digits) - 1, 0) * gap
    x = box.x() + (box.width() - total_digits_w - (box.width() * 0.22 if suffix else 0)) / 2
    off_color = QColor(color)
    off_color.setAlpha(38)
    for ch in digits:
        digit_rect = QRectF(x, box.y() + pad, digit_w * 0.78, digit_h)
        _draw_seven_segment_digit(p, digit_rect, ch, color, off_color, max(1.4, digit_h * 0.11))
        x += digit_w + gap
    if suffix:
        suffix_font = QFont(p.font())
        _scale_font(suffix_font, 0.6)
        p.setFont(suffix_font)
        p.setPen(color)
        p.drawText(QRectF(x, box.y(), box.width() * 0.22, box.height()),
                  Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, suffix)


def _draw_led_clock(p: QPainter, box: QRectF, prefix: str, h: int, m: int, s: int, color: QColor):
    """The mission clock's own bezeled LED plaque (2026-09-08, Brady's follow-up request: give
    T+/NOMINAL the same "old LED alarm clock" treatment as the per-dial percentage readouts).
    Six 7-segment digits (HH:MM:SS) with two lit colon dots between the groups -- unlike the
    dial readout's "%", a colon IS kept here: this number genuinely tells the time, whereas the
    dial's percentage explicitly did not (Brady's own distinction from the earlier request).
    `prefix` ("T+") sits as plain text ahead of the digits rather than forced into a segment
    shape neither T nor + honestly has -- the same real-hardware idiom as a chronometer's
    engraved label next to its segmented numeral field."""
    p.setPen(QPen(QColor(BORDER), 1.0))
    p.setBrush(QColor("#0a0b0d"))
    p.drawRoundedRect(box, 3, 3)

    digits = f"{h:02d}{m:02d}{s:02d}"
    pad = box.height() * 0.2
    digit_h = box.height() - 2 * pad
    off_color = QColor(color)
    off_color.setAlpha(38)

    prefix_font = QFont(p.font())
    _scale_font(prefix_font, 0.65)
    prefix_w = QFontMetricsF(prefix_font).horizontalAdvance(prefix + " ") if prefix else 0.0

    digit_w = digit_h * 0.55
    colon_w = digit_w * 0.28
    gap = digit_w * 0.22
    total_digits_w = 6 * digit_w + 2 * colon_w + 7 * gap
    start_x = box.x() + (box.width() - prefix_w - total_digits_w) / 2
    if prefix:
        p.setFont(prefix_font)
        p.setPen(color)
        p.drawText(QRectF(start_x, box.y(), prefix_w, box.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, prefix)

    x = start_x + prefix_w
    thickness = max(1.4, digit_h * 0.11)
    for i, ch in enumerate(digits):
        _draw_seven_segment_digit(p, QRectF(x, box.y() + pad, digit_w, digit_h), ch,
                                   color, off_color, thickness)
        x += digit_w + gap
        if i in (1, 3):  # a lit colon after HH and after MM, none after SS
            dot_r = thickness * 0.65
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(x + colon_w / 2, box.y() + pad + digit_h * 0.30), dot_r, dot_r)
            p.drawEllipse(QPointF(x + colon_w / 2, box.y() + pad + digit_h * 0.70), dot_r, dot_r)
            x += colon_w + gap


class GaugeDial(QWidget):
    """One analog instrument: a needle over a 270° arc, tick marks, a digital percentage readout,
    and a one-line caption. Modeled on `app/widgets.py::BusySpinner` (a QTimer-driven paintEvent,
    no external graphics dependency) but persistent rather than one-shot.

    The needle's displayed value eases toward the externally-set target on its own ~30fps timer,
    and while `active` it also gets a small continuous idle wobble on top of the target -- the
    dashboard's actual fix for engine phases that go a long time between progress events (the
    coarse `--adaptive` pre-scan, the post-sectioning build tail): the needle visibly keeps
    living even when no new data has arrived, instead of looking frozen.

    That wobble alone isn't enough for a phase with NO fine-grained instrumentation at all and a
    duration measured in tens of seconds to minutes (Brady, 2026-09-06: Analyze on an M13-scale
    STL, one black-box `trimesh.load()` call with no internal progress hooks, sat still enough
    to look stuck even with the wobble) -- `_tick` also CREEPS the displayed value slowly toward
    a soft ceiling above the last real checkpoint, asymptotically, capped well short of 100% and
    reset the instant a real `set_value()` arrives. This is a deliberate half-truth (the needle
    moves without a real progress signal backing every increment) in service of the same goal
    the wobble already serves: proving the app is alive, not claiming precise measured progress."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._target = 0.0
        self._display = 0.0
        self._state = "idle"
        self._caption = ""
        self._phase = 0.0
        self._creep_base = 0.0
        self._creep_started_at = time.monotonic()
        # 148, not 118: at 118 the caption line (drawn at rect.bottom()+12..+26) fell partly or
        # fully past the widget's own bottom edge -- the stage message the dashboard exists to
        # show was invisible during real runs (2026-09-07 design review, confirmed on a live
        # screenshot: "scanning cross-sections 84/200" cut off mid-line). 148 gives the caption
        # band its full ~28px instead of the ~16px it was squeezed into.
        # Brady, 2026-09-07 (live testing): "should scale with the pane... give the dials a lot
        # of real estate" -- this dashboard is the app's main "it's still working" feedback
        # surface, not a decoration, so it should grow to fill whatever room the dock is given.
        # Policy.Fixed vertically used to leave the dial rendering at its small minimum size no
        # matter how tall the dock's splitter pane actually was (confirmed on a live screenshot:
        # a generously tall dock, tiny dials, a large blank gap around them) -- Expanding in
        # both directions lets `paintEvent`'s own `min(w, h)`-based sizing (already correct)
        # actually receive the extra space. `setMaximumWidth` raised well past the old 190 (a
        # hard cap that fought the same goal) but still bounded, so 5 dials can't sprawl into
        # comically oversized circles on an extreme window width either.
        self.setMinimumSize(100, 148)
        self.setMaximumWidth(340)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_value(self, frac: float):
        self._target = max(0.0, min(1.0, frac))
        self._creep_base = self._target
        self._creep_started_at = time.monotonic()

    def set_state(self, state: str):
        self._state = state
        if state == "done":
            # Snap instantly rather than animating the catch-up: this dial's phase has ALREADY
            # finished by the time it's marked done (the engine has moved on to the next stage),
            # so there is nothing left to visibly count up to. Animating it anyway (2026-09-06,
            # Brady's live testing) meant a fast stage's catch-up animation could still be
            # in-flight when the NEXT dial's own animation started, making sequential phases
            # look like they were "moving at the same time" instead of one after another.
            self._target = 1.0
            self._display = 1.0
        elif state == "idle":
            self._target = 0.0
            self._display = 0.0
        self.update()

    def set_caption(self, text: str):
        self._caption = text
        self.update()

    # Per-tick cap on how far the needle may move, in addition to proportional easing --
    # without it, a stage that completes in well under a second (a small mesh's SECTIONING
    # phase, say) reports so few real progress events, spaced so closely together, that pure
    # proportional easing (`diff * _EASE_RATE`) converges within just a couple of 33ms ticks and
    # reads as an instant snap to 100% rather than a visible sweep -- exactly Brady's report
    # after M8 ("basically instantly go from 0% to 100%"). This floors a full 0->1 sweep at
    # roughly 1/_MAX_STEP_PER_TICK ticks (~0.7s at the 33ms timer interval) even when the engine
    # itself finishes that fast, while leaving small, already-gradual updates (the common case
    # on a slower, e.g. M13-scale, run) to the proportional term untouched.
    _EASE_RATE = 0.18
    _MAX_STEP_PER_TICK = 0.05
    # An ABSOLUTE ceiling, not "last checkpoint + a margin": measured directly on a real M13
    # Analyze (2026-09-06), the dominant cost is one un-subdividable call (`_weld_by_radius`'s
    # KD-tree union-find over ~5M vertices) that alone ran 160 of 182 total seconds while
    # advancing the checkpoint fraction by only 0.18 -- a margin-based ceiling anchored to that
    # checkpoint would have saturated within the first ~40s of that 160s wait and then sat flat
    # again for the rest, reproducing the exact "looks stuck" bug this exists to fix. A fixed
    # ceiling well short of 100%, approached on a long time constant, keeps SOME visible forward
    # motion across a wait of arbitrary length instead of assuming any particular duration.
    _CREEP_CEILING = 0.93
    _CREEP_TIME_CONSTANT_S = 80.0

    def _creep_target(self) -> float:
        if self._state != "active" or self._CREEP_CEILING <= self._creep_base:
            return self._target
        elapsed = time.monotonic() - self._creep_started_at
        creep = self._creep_base + (self._CREEP_CEILING - self._creep_base) * (
            1 - math.exp(-elapsed / self._CREEP_TIME_CONSTANT_S))
        return max(self._target, creep)

    def _tick(self):
        self._phase = (self._phase + 0.06) % (2 * math.pi)
        # Never move backward: a real checkpoint can legitimately report a frac LOWER than
        # where creep had already visually advanced to (the M13 case above: creep could reach
        # partway toward its ceiling during the 160s weld, then the next real checkpoint at a
        # smaller-than-hoped 0.56 arrives) -- displaying that as the needle visibly reversing
        # would look more broken than just holding position until real progress catches back up.
        target = max(self._creep_target(), self._display)
        diff = target - self._display
        if abs(diff) > 0.001:
            step = diff * self._EASE_RATE
            step = max(-self._MAX_STEP_PER_TICK, min(self._MAX_STEP_PER_TICK, step))
            self._display += step
        else:
            self._display = target
        self.update()

    # Vertical layout budget (2026-09-07, Brady's live-testing feedback: the "100%" readout was
    # overlapping the dial's own bottom rim, and the "0"/"100" end-tick labels were getting
    # clipped at the widget's edge with too little room between neighboring dials). Named
    # constants instead of magic numbers, so the geometry stays provably consistent: the dial's
    # own size is DERIVED from what's left after every other band claims its own fixed space,
    # both vertically (title/readout/caption) and horizontally (the end-tick labels, which
    # protrude diagonally past the dial's own radius -- see `_HORZ_LABEL_MARGIN`'s comment).
    _TOP_MARGIN = 18       # title band
    _GAP_TO_PCT = 8        # clears the dial's own bottom rim -- was 4px INTO the rim before
    _PCT_ROW_H = 20
    _GAP_TO_CAPTION = 2
    _CAPTION_H = 14
    _BOTTOM_MARGIN = 4
    # Horizontal margin reserved on each side for the "0"/"100" end-tick labels, which sit at
    # radius+5 outward from the dial center at a 45-degree angle (cos(45 deg) ~= 0.707) plus
    # their own ~14px text-box half-width. Provably safe for ANY widget width: with dial_size
    # capped at `w - 2*margin`, the label's own box edge can be shown algebraically to never go
    # negative or exceed `w` regardless of how wide or narrow the dial ends up.
    _HORZ_LABEL_MARGIN = 26

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        vert_budget = (self._TOP_MARGIN + self._GAP_TO_PCT + self._PCT_ROW_H
                      + self._GAP_TO_CAPTION + self._CAPTION_H + self._BOTTOM_MARGIN)
        dial_size = max(min(w - 2 * self._HORZ_LABEL_MARGIN, self.height() - vert_budget), 20)
        # Center the whole title/dial/readout/caption stack vertically when WIDTH (not height)
        # is the binding constraint on dial_size -- otherwise a tall, narrow pane collects all
        # its extra room as dead space below the caption instead of growing the dial to fill it
        # (2026-09-07 implementation review, confirmed on a very tall dock/narrow-window shot).
        used_height = vert_budget + dial_size
        top_offset = max(0.0, (self.height() - used_height) / 2)
        rect = QRectF((w - dial_size) / 2, self._TOP_MARGIN + top_offset, dial_size, dial_size)
        color = QColor(_STATE_COLORS.get(self._state, TEXT_DISABLED))
        center = rect.center()
        radius = dial_size / 2

        # Title, instrument-label style (matches the dock-title/section-label idiom elsewhere).
        p.setPen(QColor(TEXT_SECONDARY))
        title_font = QFont(self.font())
        _scale_font(title_font, 0.85)
        title_font.setBold(True)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        p.setFont(title_font)
        p.drawText(QRectF(0, top_offset, w, 14), Qt.AlignmentFlag.AlignHCenter, self._title)

        # Matte face, single hairline ring -- no bezel, no gradient highlight, no fake bevel
        # shading. 2026-09-07 (premium-CAD/instrument research pass): DaVinci Resolve's color-
        # page dials -- the closest real analog to a QPainter-drawn instrument -- read as
        # authentic BECAUSE they're flat: "thin outer tick ring, hairline needle/arc, matte face
        # the same color as the panel... rather than gradients, glass highlights, or drop
        # shadows." The previous version's darker bezel ring + highlight arc + shaded lower
        # chord (2026-09-06) was a step in the opposite direction; removed in favor of the
        # restraint every reference instrument actually uses.
        p.setPen(QPen(QColor(BORDER), 1.0))
        p.setBrush(QColor(BG_PANEL))
        p.drawEllipse(rect)
        for i in range(21):
            frac = i / 20
            angle = math.radians(_START_ANGLE_DEG + _SWEEP_DEG * frac)
            major = i % 5 == 0
            inner = radius - (9 if major else 5)
            x1 = center.x() + math.cos(angle) * inner
            y1 = center.y() - math.sin(angle) * inner
            x2 = center.x() + math.cos(angle) * (radius - 2.5)
            y2 = center.y() - math.sin(angle) * (radius - 2.5)
            p.setPen(QPen(QColor(TEXT_DISABLED), 1.4 if major else 0.9))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # 0/100 tick labels at the two ends of the sweep -- skipping a "50" label at top-dead-
        # center, which sits right where the title text already is.
        label_font = QFont(self.font())
        _scale_font(label_font, 0.75)
        p.setFont(label_font)
        p.setPen(QColor(TEXT_DISABLED))
        for frac, text, align in (
            (0.0, "0", Qt.AlignmentFlag.AlignLeft),
            (1.0, "100", Qt.AlignmentFlag.AlignRight),
        ):
            angle = math.radians(_START_ANGLE_DEG + _SWEEP_DEG * frac)
            lx = center.x() + math.cos(angle) * (radius + 5)
            ly = center.y() - math.sin(angle) * (radius + 5)
            p.drawText(QRectF(lx - 14, ly - 6, 28, 12), align | Qt.AlignmentFlag.AlignVCenter, text)

        # Progress ring: a filled arc tracking `display` on top of a dim full-sweep track,
        # giving an at-a-glance "how much" read that doesn't require judging the needle's exact
        # angle -- the needle stays for a precise pointer.
        arc_rect = rect.adjusted(6, 6, -6, -6)
        track_pen = QPen(QColor(BORDER), 3.0)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track_pen)
        p.drawArc(arc_rect, int(_START_ANGLE_DEG * 16), int(_SWEEP_DEG * 16))
        if self._display > 0.001:
            progress_pen = QPen(color, 3.0)
            progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(progress_pen)
            p.drawArc(arc_rect, int(_START_ANGLE_DEG * 16),
                      int(_SWEEP_DEG * self._display * 16))

        # Needle. A small idle wobble while active keeps it visibly "alive" between sparse
        # engine progress events -- see class docstring.
        display = self._display
        if self._state == "active":
            display = max(0.0, min(1.0, display + 0.015 * math.sin(self._phase)))
        angle = math.radians(_START_ANGLE_DEG + _SWEEP_DEG * display)
        needle_len = radius - 16
        nx = center.x() + math.cos(angle) * needle_len
        ny = center.y() - math.sin(angle) * needle_len
        needle_pen = QPen(color, 2.2)
        needle_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(needle_pen)
        p.drawLine(center, QPointF(nx, ny))
        p.setPen(QPen(QColor(BG_DARKEST), 1.2))
        p.setBrush(color)
        p.drawEllipse(center, 4.0, 4.0)

        # Digital readout (LED-clock style, Brady's request 2026-09-08) + caption, below the
        # dial. The readout number tracks the (possibly creeping) DISPLAY value, not the raw
        # target -- otherwise the digits would sit frozen at the last real checkpoint while the
        # needle visibly moves past it, which reads as MORE broken than not creeping at all would.
        pct_text = "--" if self._state == "idle" else f"{int(round(self._display * 100))}%"
        pct_top = rect.bottom() + self._GAP_TO_PCT
        box_w = min(w - 8, dial_size * 0.85)
        led_box = QRectF((w - box_w) / 2, pct_top, box_w, self._PCT_ROW_H)
        _draw_led_readout(p, led_box, pct_text, color)
        if self._caption:
            p.setPen(QColor(TEXT_SECONDARY))
            cap_font = QFont(self.font())
            _scale_font(cap_font, 0.8)
            p.setFont(cap_font)
            metrics = p.fontMetrics()
            elided = metrics.elidedText(self._caption, Qt.TextElideMode.ElideRight, w - 6)
            cap_top = pct_top + self._PCT_ROW_H + self._GAP_TO_CAPTION
            p.drawText(QRectF(0, cap_top, w, self._CAPTION_H),
                      Qt.AlignmentFlag.AlignHCenter, elided)
        p.end()


class MissionClock(QWidget):
    """`T+ 00:04:13` monospace elapsed-time readout, the "mission control" voice for the gauge
    cluster (2026-09-07 design review). Starts on a run, FREEZES (not clears) on done/fail --
    an engineer wants to know how long the run actually took, not have the number vanish."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._elapsed_s = 0.0
        self._started_at = None
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.update)
        self.setMinimumSize(190, 28)  # room for a "T+" prefix + 6 LED digits + 2 colons

    def start(self):
        if self._started_at is not None:
            return  # already running -- an Analyze that chains into a Rebuild is ONE mission
        self._started_at = time.monotonic()
        self._elapsed_s = 0.0
        self._timer.start()
        self.update()

    def freeze(self):
        if self._started_at is not None:
            self._elapsed_s = time.monotonic() - self._started_at
        self._started_at = None
        self._timer.stop()
        self.update()

    def reset(self):
        self._started_at = None
        self._elapsed_s = 0.0
        self._timer.stop()
        self.update()

    def _current_elapsed(self) -> float:
        if self._started_at is not None:
            return time.monotonic() - self._started_at
        return self._elapsed_s

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        secs = int(self._current_elapsed())
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        font = QFont(self.font())
        font.setFamilies(_MONO_FAMILIES)
        font.setBold(True)
        p.setFont(font)
        color = QColor(TELEMETRY if self._started_at is not None else TEXT_SECONDARY)
        _draw_led_clock(p, QRectF(self.rect()), "T+", h, m, s, color)
        p.end()


_STATUS_STRIP_STYLE = {
    "idle": ("STANDBY", TEXT_DISABLED),
    "running": ("RUNNING", TELEMETRY),
    "nominal": ("NOMINAL", SUCCESS),
    "fault": ("FAULT", ERROR),
}


class StatusStrip(QLabel):
    """Uppercase, letter-spaced status word (STANDBY/RUNNING — <stage>/NOMINAL/FAULT — <kind>)
    giving the gauge cluster a single at-a-glance verdict, the same idiom as an aircraft
    caution-and-warning annunciator panel. Housed in the same dark bezel as the LED digit
    readouts (2026-09-08, Brady's follow-up: match NOMINAL to the "old LED clock" look) -- but
    the WORD itself stays plain letter-spaced text rather than forced into 7-segment shapes,
    since M/W/N and friends have no honest segment rendering; the shared bezel/glow-color
    language is what ties it to the digit displays, the same way a real console pairs a
    segmented numeral field with a plain backlit annunciator placard, not two of the same."""

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QFont(self.font())
        font.setFamilies(_MONO_FAMILIES)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        self.setFont(font)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.setContentsMargins(0, 0, 0, 0)
        self.set_status("idle")

    def set_status(self, key: str, detail: str = ""):
        text, color = _STATUS_STRIP_STYLE.get(key, _STATUS_STRIP_STYLE["idle"])
        if detail:
            text = f"{text} — {detail}"
        self.setText(text)
        self.setStyleSheet(
            f"color: {color}; background-color: #0a0b0d; border: 1px solid {BORDER}; "
            "border-radius: 3px; padding: 4px 8px;")


class Dashboard(QWidget):
    """The dock's full contents: the instrument cluster (mission clock + status strip + one dial
    per phase) and the log console, stacked top to bottom. The verification readout that used to
    live here too was dropped 2026-09-06 -- Brady's call: it duplicated the Details dock's Output
    page, which already shows the same checks, and having it in two places was noise, not signal."""

    def __init__(self, log_widget: QWidget, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

        cluster = QFrame()
        cluster.setObjectName("gaugeCluster")
        cluster.setStyleSheet(
            f"#gaugeCluster {{ background-color: {BG_DARKEST}; border: 1px solid {BORDER}; "
            "border-radius: 4px; }")
        cluster_layout = QHBoxLayout(cluster)
        cluster_layout.setContentsMargins(10, 6, 10, 6)
        # Brady, 2026-09-07: "not enough spacing between the dials" -- was 4px, tight enough
        # that neighboring dials' own end-tick labels (each already fully inside its own widget
        # bounds, see GaugeDial._HORZ_LABEL_MARGIN) still read as crowded together.
        cluster_layout.setSpacing(20)

        telemetry_col = QVBoxLayout()
        telemetry_col.setSpacing(4)
        telemetry_col.addStretch(1)
        self.mission_clock = MissionClock()
        self.status_strip = StatusStrip()
        telemetry_col.addWidget(self.mission_clock)
        telemetry_col.addWidget(self.status_strip)
        telemetry_col.addStretch(1)
        cluster_layout.addLayout(telemetry_col)

        # No flanking stretches here (2026-09-07): with the dials now Expanding in both
        # directions, they should CLAIM the row's real estate themselves rather than stay
        # small and centered inside padding -- see GaugeDial's own size-policy comment.
        self.dials = [GaugeDial(title) for title in DIAL_TITLES]
        for dial in self.dials:
            cluster_layout.addWidget(dial)

        # A QSplitter, not a plain stacked layout, so the dial cluster and the log can be
        # resized independently of each other (Brady, 2026-09-06: "make the log box smaller and
        # the geometry box bigger" -- the geometry/viewport side of that is the dock-vs-central-
        # widget split Qt already gives for free, but reallocating space WITHIN the dashboard
        # needed this).
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(cluster)
        splitter.addWidget(log_widget)
        for i in range(splitter.count()):
            splitter.setCollapsible(i, False)  # explicit per-pane override, not just the
                                                # splitter-wide default `setChildrenCollapsible`
        # Equal stretch (2026-09-07, was cluster=0/log=1): the dials are a main feedback
        # feature, not secondary chrome -- when Brady drags the dock bigger, the gauge cluster
        # should keep growing right along with the log, not sit fixed while the log eats
        # every new pixel of height.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

    # ---- stage -> dial wiring (driven by main_window's on_progress/rebuilt/failed) -----------
    def reset(self):
        for dial in self.dials:
            dial.set_state("idle")
            dial.set_caption("")
        self.mission_clock.reset()
        self.status_strip.set_status("idle")

    def mission_start(self):
        """Call at the top of any run (Analyze or Run). Idempotent -- a Run that silently
        chains Analyze->Rebuild calls this twice, and the second call must NOT reset the clock;
        it's one mission, not two (`MissionClock.start()` itself no-ops while already running)."""
        self.mission_clock.start()
        self.status_strip.set_status("running")

    def mission_done(self):
        """End-of-mission with no further dial transition of its own -- used for an Analyze that
        does NOT chain into a Rebuild (`on_done()` below covers a completed Rebuild, dial state
        included; this is the narrower Analyze-only case, called by MainWindow directly)."""
        self.mission_clock.freeze()
        self.status_strip.set_status("nominal")

    def mark_analyze_done(self):
        """A standalone Analyze click has no LATER stage to trigger ANALYZE's done-transition
        the way an earlier dial gets marked done once a rebuild moves past it -- call this once
        `engine.analyze()` itself has actually finished, so the dial doesn't just sit at
        "active" forever after its last real progress event."""
        self.dials[STAGE_DIAL["analyze"]].set_state("done")

    def reset_rebuild_dials(self):
        """Like `reset()`, but leaves the ANALYZE dial alone -- used when a rebuild starts, so a
        just-completed (or earlier-session) Analyze's "done" status stays visible through the
        rebuild that follows it instead of blinking back to idle the instant Load activates."""
        analyze_idx = STAGE_DIAL["analyze"]
        for i, dial in enumerate(self.dials):
            if i == analyze_idx:
                continue
            dial.set_state("idle")
            dial.set_caption("")

    def on_stage(self, stage: str, frac: float, message: str):
        idx = STAGE_DIAL.get(stage)
        if idx is None:
            return
        # `frac` here is whatever `on_progress` actually delivers -- for every GUI run that's
        # `_RefineProgressRelay`'s combined, cross-stage value (refine_passes defaults to 1), not
        # a raw stage-local 0..1. Recover the local value so the needle sweeps its own dial's
        # full arc instead of just the sliver `_STAGE_RANGE` allotted that stage.
        local = engine.stage_local_progress(stage, frac)
        self.status_strip.set_status("running", stage.upper())
        for i, dial in enumerate(self.dials):
            if i < idx:
                dial.set_state("done")
            elif i == idx:
                dial.set_state("active")
                dial.set_value(local)
                dial.set_caption(message)
            # Dials AFTER idx are deliberately left untouched, not reset to idle: a
            # verify-and-refine retry pass (M8 is a milestone this actually triggers) re-emits
            # "load" then "refine"/"build" again, and briefly wiping SECTIONING/BUILD back to 0%
            # right as the retry starts erases the very feedback this dashboard exists to give
            # (Brady, 2026-09-05: "it shouldn't reset and do it again... if you erase them you
            # erase usable feedback"). Leaving them alone means a dial a retry revisits simply
            # flips from done (green) back to active (blue) and climbs again when ITS OWN stage
            # actually fires -- informative, not a data-losing reset.

    def on_done(self):
        for dial in self.dials:
            dial.set_state("done")
            dial.set_caption("")
        self.mission_done()

    def on_failed(self, last_stage: str | None, kind: str = ""):
        idx = STAGE_DIAL.get(last_stage)
        for i, dial in enumerate(self.dials):
            if idx is None or i < idx:
                continue
            if i == idx:
                dial.set_state("error")
            else:
                dial.set_state("idle")
        self.mission_clock.freeze()
        # A user-requested Cancel isn't a fault -- distinct wording for that one kind.
        self.status_strip.set_status("fault", "ABORTED" if kind == "Cancelled" else (kind or "ERROR"))
