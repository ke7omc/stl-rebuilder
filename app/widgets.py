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
                if tooltip:
                    row.setToolTip(1, str(tooltip))
                group_item.addChild(row)
            group_item.setExpanded(True)


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
    """Per-station table for the Stations details page (station index, Z, outer radius sampled
    from the rebuilt solid preview mesh, and whether a topology event lands at that station)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["#", "Z (mm)", "R_outer (mm)", ""])
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        for col in range(3):
            self.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

    def set_rows(self, rows):
        """`rows`: list of (index, z_mm, r_outer_mm_or_None, is_event_bool)."""
        self.clear()
        for idx, z, r_outer, is_event in rows:
            r_text = fmt_num(r_outer, 2) if r_outer is not None else "-"
            note = "topology event" if is_event else ""
            item = QTreeWidgetItem([str(idx), fmt_num(z, 2), r_text, note])
            if is_event:
                from PySide6.QtGui import QColor
                from app.theme import WARNING
                for col in range(4):
                    item.setForeground(col, QColor(WARNING))
                item.setToolTip(3, "Cross-section topology changes at this station")
            self.addTopLevelItem(item)
