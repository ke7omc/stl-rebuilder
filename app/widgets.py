"""Reusable read-only display widgets for the Details dock (G3 visual-review fix #1: never show
raw JSON to the user -- grouped, human-formatted property rows with the raw value in a tooltip)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem


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
        # truncated by the fixed row height with no wrap. Word-wrap the value column and let
        # rows grow to fit instead of eliding.
        self.setWordWrap(True)
        self.setUniformRowHeights(False)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)

    def set_groups(self, groups):
        """`groups`: list of (group_title, [(label, value_str, tooltip_str_or_None), ...])."""
        self.clear()
        for title, rows in groups:
            group_item = QTreeWidgetItem([title, ""])
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.addTopLevelItem(group_item)
            for label, value, tooltip in rows:
                row = QTreeWidgetItem([label, str(value)])
                row.setToolTip(1, str(tooltip) if tooltip else str(value))
                group_item.addChild(row)
            group_item.setExpanded(True)
        self.resizeColumnToContents(0)
        # Cap the label column so long group headers ("Suggested run settings") can't eat the
        # width the Value column needs to wrap readably (G3 review #2 item 2).
        self.setColumnWidth(0, min(self.columnWidth(0), 130))


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
        for idx, z, n_loops, r_outer, r_bore, cls, is_event in rows:
            r_outer_text = fmt_num(r_outer, 2) if r_outer is not None else "—"
            r_bore_text = fmt_num(r_bore, 2) if r_bore is not None else "—"
            cls_text = f"{cls} (event)" if is_event else cls
            item = QTreeWidgetItem(
                [str(idx), fmt_num(z, 2), str(n_loops), r_outer_text, r_bore_text, cls_text])
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

from app.theme import ACCENT as _ACCENT


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
        pen = _QPen(_QColor(_ACCENT), 2.4)
        pen.setCapStyle(_Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        rect = self.rect().adjusted(3, 3, -3, -3)
        p.drawArc(rect, -self._angle * 16, 100 * 16)
        p.end()
