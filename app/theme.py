"""Dark theme for the desktop app (MISSION §12/G3 design bar): a complete QSS stylesheet plus a
palette used by the 3D viewport, so the app never falls back to the default grey Qt look."""

BG_DARKEST = "#1a1d21"
BG_DARK = "#22262b"
BG_PANEL = "#282c33"
BG_RAISED = "#2f343c"
BORDER = "#3a3f47"
ACCENT = "#4a9eff"
ACCENT_HOVER = "#6cb0ff"
ACCENT_PRESSED = "#3a7fd0"
TEXT_PRIMARY = "#e6e8eb"
TEXT_SECONDARY = "#9aa1ab"
TEXT_DISABLED = "#5a5f68"
ERROR = "#e5534b"
SUCCESS = "#4caf6f"
WARNING = "#e0a336"

DARK_QSS = f"""
* {{
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: {TEXT_PRIMARY};
    outline: none;
}}

QMainWindow, QWidget {{
    background-color: {BG_DARK};
}}

QMenuBar, QStatusBar {{
    background-color: {BG_DARKEST};
    border: none;
}}

QStatusBar QLabel {{
    color: {TEXT_SECONDARY};
    padding: 0 4px;
}}

QDockWidget {{
    color: {TEXT_SECONDARY};
    titlebar-close-icon: none;
    font-weight: 600;
    border: 1px solid {BORDER};
}}

QDockWidget::title {{
    background-color: {BG_DARKEST};
    padding: 6px 8px;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-size: 11px;
    border-bottom: 1px solid {BORDER};
}}

QTreeWidget, QTextEdit, QPlainTextEdit, QListWidget {{
    background-color: {BG_DARKEST};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}

QTreeWidget::item {{
    padding: 4px 6px;
    border-radius: 3px;
}}

QTreeWidget::item:selected {{
    background-color: {ACCENT};
    color: #ffffff;
}}

QTreeWidget::item:hover:!selected {{
    background-color: {BG_RAISED};
}}

QLabel {{
    background: transparent;
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 6px;
    min-height: 20px;
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {TEXT_DISABLED};
    background-color: {BG_DARK};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
    border-left: 1px solid {BORDER};
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 16px;
    border-left: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 16px;
    border-left: 1px solid {BORDER};
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 0;
    height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {TEXT_SECONDARY};
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {TEXT_SECONDARY};
}}

QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
}}

QCheckBox {{
    spacing: 6px;
}}

QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background-color: {BG_PANEL};
}}

QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
}}

QPushButton {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 14px;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {BORDER};
}}

QPushButton:pressed {{
    background-color: {BG_DARKEST};
}}

QPushButton:disabled {{
    color: {TEXT_DISABLED};
    background-color: {BG_DARK};
    border: 1px solid {BG_DARK};
}}

QPushButton#primary {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}

QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}

QPushButton#primary:pressed {{
    background-color: {ACCENT_PRESSED};
}}

QPushButton#danger {{
    background-color: {BG_RAISED};
    border: 1px solid {ERROR};
    color: {ERROR};
}}

QPushButton#danger:hover {{
    background-color: {ERROR};
    color: #ffffff;
}}

QPushButton#danger:disabled {{
    background-color: {BG_DARK};
    border: 1px solid {BG_DARK};
    color: {TEXT_DISABLED};
}}

QMenuBar {{
    padding: 2px;
}}

QMenuBar::item {{
    background: transparent;
    padding: 4px 10px;
    border-radius: 3px;
}}

QMenuBar::item:selected {{
    background-color: {BG_RAISED};
}}

QMenu {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
}}

QMenu::item {{
    padding: 5px 20px;
}}

QMenu::item:selected {{
    background-color: {ACCENT};
    color: #ffffff;
}}

QToolBar {{
    background-color: {BG_DARKEST};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 3px;
    spacing: 4px;
}}

QToolButton {{
    background: transparent;
    border-radius: 4px;
    padding: 5px;
}}

QToolButton:hover {{
    background-color: {BG_RAISED};
}}

#emptyHint {{
    color: {TEXT_SECONDARY};
    font-size: 14px;
}}

#logConsole {{
    font-family: "SF Mono", "Consolas", "Menlo", monospace;
    font-size: 12px;
}}

QProgressBar {{
    background-color: {BG_DARKEST};
    border: 1px solid {BORDER};
    border-radius: 4px;
    text-align: center;
    min-height: 16px;
}}

QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}

QScrollBar:vertical {{
    background: {BG_DARK};
    width: 12px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {BG_RAISED};
    border-radius: 5px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: {BORDER};
}}

QScrollBar:horizontal {{
    background: {BG_DARK};
    height: 12px;
}}

QScrollBar::handle:horizontal {{
    background: {BG_RAISED};
    border-radius: 5px;
    min-width: 24px;
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}

QSplitter::handle {{
    background-color: {BORDER};
}}

QMessageBox {{
    background-color: {BG_PANEL};
}}

#errorBanner {{
    background-color: #3a2224;
    border: 1px solid {ERROR};
    border-radius: 4px;
    padding: 8px;
    color: #ff9d97;
}}
"""


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)
