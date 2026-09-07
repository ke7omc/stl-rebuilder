"""Reusable read-only display widgets for the Details dock (G3 visual-review fix #1: never show
raw JSON to the user -- grouped, human-formatted property rows with the raw value in a tooltip)."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from app.theme import MONO_FAMILY


def _mono_font(base: QFont) -> QFont:
    """Technical/numeric readout face (2026-09-07 design review: the log console was the only
    monospace surface in the app and consistently read as the most "mission control" thing in
    every screenshot reviewed -- applying it to every OTHER numeric readout is the cheapest
    authenticity upgrade available)."""
    font = QFont(base)
    font.setFamilies([f.strip(' "') for f in MONO_FAMILY.split(",")])
    return font


class PropertyTree(QTreeWidget):
    """Two-column (Property, Value) tree, rows grouped under bold section headers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderLabels(["Property", "Value"])
        self.setRootIsDecorated(True)
        self.setIndentation(14)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        # G3 review #2 item 2: long values ("Bounds [[-999.75...", "Paths used: ...") were
        # truncated by the fixed row height with no wrap. Every multi-line value in this class
        # is pre-wrapped into explicit "\n"-separated lines by its caller (`fmt_bounds`, and
        # `main_window._with_hint`) rather than relying on Qt's own per-pixel reflow -- so
        # `setWordWrap` stays OFF: turning it on let Qt ALSO re-wrap an already-wrapped long
        # line a second time whenever the real column came out narrower than the caller's wrap
        # width guessed, silently invalidating the line count `set_groups` uses to size the row
        # (found while adding the verification "hint" line, 2026-09-05: correct in isolation,
        # clipped once real column widths made Qt's second wrap pass add extra lines). Explicit
        # "\n"s still render as separate lines with word-wrap off -- only reflow is disabled.
        self.setUniformRowHeights(False)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)

    def set_groups(self, groups):
        """`groups`: list of (group_title, [(label, value_str, tooltip_str_or_None), ...]).
        A row tuple may carry an optional 4th element: a color string (e.g. "#4caf6f") applied
        to the value column's foreground (Qt.ForegroundRole) — used by the Output page's
        Verification group for its pass/fail/n-a glyph rows."""
        from PySide6.QtGui import QColor
        self.clear()
        for title, rows in groups:
            group_item = QTreeWidgetItem([title, ""])
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.addTopLevelItem(group_item)
            for row_tuple in rows:
                label, value, tooltip = row_tuple[:3]
                color = row_tuple[3] if len(row_tuple) > 3 else None
                value_str = str(value)
                row = QTreeWidgetItem([label, value_str])
                row.setFont(1, _mono_font(self.font()))
                row.setToolTip(1, str(tooltip) if tooltip else value_str)
                if color:
                    row.setForeground(1, QColor(color))
                group_item.addChild(row)
                # Word-wrap's automatic row-height growth is unreliable once a stylesheet is
                # applied to this widget (Qt/QSS switches to the CSS item delegate, whose
                # wrapped-text sizeHint calculation silently caps out after ~2-3 lines and
                # elides the rest even with ElideNone set) -- found while adding the
                # verification "hint" line, which was being cut off no matter how short it was
                # made (Brady, 2026-09-05). Compute the height ourselves from the line count
                # instead of trusting the delegate's own wrap sizeHint. The 1.5x isn't slack --
                # our status/hint text mixes in glyphs (✗ ↳ Δ ≤) the base font doesn't cover, so
                # Qt renders those runs from a taller fallback font than `lineSpacing()`
                # reports; measured empirically (a 6-line value clipped its last line at 1.0x
                # and rendered complete at 1.5x).
                n_lines = value_str.count("\n") + 1
                if n_lines > 1:
                    line_h = self.fontMetrics().lineSpacing()
                    row.setSizeHint(1, QSize(0, int(n_lines * line_h * 1.5) + 10))
            group_item.setExpanded(True)
        self.resizeColumnToContents(0)
        # Cap the label column so long group headers ("Suggested run settings") can't eat the
        # width the Value column needs to wrap readably (G3 review #2 item 2).
        self.setColumnWidth(0, min(self.columnWidth(0), 130))
        # Force a full relayout after every row's sizeHint is set: setting several rows'
        # non-uniform sizeHints back-to-back in this loop left later rows' positions computed
        # from a stale layout (a row could report the CORRECT sizeHint on its own yet still get
        # clipped in-place -- verified by rendering it as the tree's only row, where it was
        # fine) -- found the same day as the sizeHint fix above.
        self.doItemsLayout()


def fmt_bounds(bounds_mm) -> str:
    """Render a [[xmin,ymin,zmin],[xmax,ymax,zmax]] bounds pair as three readable ranges
    instead of a raw 16-decimal-float nested list (G3 review #2 item 2)."""
    try:
        (xmin, ymin, zmin), (xmax, ymax, zmax) = bounds_mm
        return (f"X: {fmt_num(xmin)} … {fmt_num(xmax)} mm\n"
                f"Y: {fmt_num(ymin)} … {fmt_num(ymax)} mm\n"
                f"Z: {fmt_num(zmin)} … {fmt_num(zmax)} mm")
    except (TypeError, ValueError):
        return str(bounds_mm)


def fmt_num(x, decimals=2) -> str:
    try:
        return f"{float(x):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(x)


def axis_label(vec) -> str:
    idx = max(range(3), key=lambda i: abs(vec[i]))
    sign = "+" if vec[idx] >= 0 else "-"
    return f"{sign}{'XYZ'[idx]}"


class StationTable(QTreeWidget):
    """Per-station table for the Stations details page: station index, Z, loop count, outer and
    bore radius (from an exact planar slice of the rebuilt solid preview mesh at that station --
    see `app.main_window._station_rows`), a dome/barrel classification, and whether a topology
    event lands at that station."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(6)
        self.setHeaderLabels(["#", "Z (mm)", "Loops", "R_outer (mm)", "R_bore (mm)", "Class"])
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        for col in range(5):
            self.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

    def set_rows(self, rows):
        """`rows`: list of (index, z_mm, n_loops, r_outer_mm_or_None, r_bore_mm_or_None,
        classification_str, is_event_bool)."""
        self.clear()
        mono = _mono_font(self.font())
        for idx, z, n_loops, r_outer, r_bore, cls, is_event in rows:
            r_outer_text = fmt_num(r_outer, 2) if r_outer is not None else "—"
            r_bore_text = fmt_num(r_bore, 2) if r_bore is not None else "—"
            cls_text = f"{'⚠ ' if is_event else ''}{cls}{' (event)' if is_event else ''}"
            item = QTreeWidgetItem(
                [str(idx), fmt_num(z, 2), str(n_loops), r_outer_text, r_bore_text, cls_text])
            item.setData(0, Qt.ItemDataRole.UserRole, (float(z), r_outer))
            for col in (1, 3, 4):
                item.setFont(col, mono)
                item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if is_event:
                from PySide6.QtGui import QColor
                from app.theme import WARNING
                for col in range(6):
                    item.setForeground(col, QColor(WARNING))
                item.setToolTip(5, "Cross-section topology changes at this station")
            self.addTopLevelItem(item)


# ---- BusySpinner (added 2026-09-04, Brady's request) --------------------------------------
from PySide6.QtCore import QTimer as _QTimer, Qt as _Qt
from PySide6.QtGui import QColor as _QColor, QPainter as _QPainter, QPen as _QPen
from PySide6.QtWidgets import QWidget as _QWidget

from app.theme import TELEMETRY as _TELEMETRY


class BusySpinner(_QWidget):
    """Small continuously rotating arc shown while a worker thread is active. Deliberately
    independent of the determinate progress bar: it animates on its own QTimer on the UI
    thread, so it keeps moving (proving the app is alive and working) even when engine
    progress events are sparse -- e.g. the long solid-build stage on M13-sized meshes."""

    def __init__(self, parent=None, diameter: int = 16):
        super().__init__(parent)
        self._angle = 0
        self._diameter = diameter
        self.setFixedSize(diameter + 6, diameter + 6)
        self._timer = _QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def start(self):
        self.show()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def paintEvent(self, _event):
        p = _QPainter(self)
        p.setRenderHint(_QPainter.RenderHint.Antialiasing)
        pen = _QPen(_QColor(_TELEMETRY), 2.4)
        pen.setCapStyle(_Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        rect = self.rect().adjusted(3, 3, -3, -3)
        p.drawArc(rect, -self._angle * 16, 100 * 16)
        p.end()
