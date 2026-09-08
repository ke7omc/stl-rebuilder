"""In-app manual: the markdown pages in `app/help_content/` plus the dialog that renders them.

Markdown-in-a-QTextBrowser rather than a web view: the pages are data files that travel with the
repo, they stay readable and diffable outside the app, and rendering them adds no dependency to a
tool that already has to install cleanly on a locked-down work machine (WORK_SETUP.md)."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLineEdit, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.theme import BG_DARKEST, TEXT_PRIMARY

HELP_DIR = os.path.join(os.path.dirname(__file__), "help_content")


def load_pages() -> list:
    """[(page_id, title, markdown_body)] for every `.md` in `help_content/`, ordered by filename
    (the numeric prefixes are the manual's reading order). `page_id` is the filename stem, so a
    caller can deep-link a page by name -- `_show_help("00_quick_start")`.

    A file whose first line isn't an `# H1` still contributes a page, titled by its stem: a
    malformed page is worth showing badly rather than dropping silently out of the manual."""
    pages = []
    if not os.path.isdir(HELP_DIR):
        return pages
    for filename in sorted(os.listdir(HELP_DIR)):
        if not filename.endswith(".md"):
            continue
        page_id = os.path.splitext(filename)[0]
        try:
            with open(os.path.join(HELP_DIR, filename), encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            continue
        lines = raw.splitlines()
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
            body = "\n".join(lines[1:]).lstrip("\n")
        else:
            title, body = page_id, raw
        pages.append((page_id, title, body))
    return pages


class HelpDialog(QDialog):
    """The manual window: a filterable table of contents on the left, the selected page on the
    right. Non-modal and parented to the main window, so it can stay open beside the app while
    the reader works through a run rather than blocking it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STL Rebuilder — Manual")
        self.resize(900, 640)
        self._pages = load_pages()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter pages...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        self.toc = QTreeWidget()
        self.toc.setHeaderHidden(True)
        self.toc.setMinimumWidth(200)
        self.toc.setRootIsDecorated(False)
        for page_id, title, _body in self._pages:
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, page_id)
            self.toc.addTopLevelItem(item)
        self.toc.currentItemChanged.connect(self._on_toc_selection)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.toc)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._on_anchor_clicked)
        # The app QSS already reaches this widget (its QTextEdit rule matches the QTextBrowser
        # subclass), and its colors were confirmed legible in the screenshot pass -- what it
        # gives is a 4px pad, which is a cramped left margin for a page of prose. The colors are
        # restated alongside the wider padding so the manual's own legibility doesn't rest on the
        # theme's catch-all `*` selector continuing to win over a QTextDocument's defaults.
        self.browser.setStyleSheet(
            f"QTextBrowser {{ background-color: {BG_DARKEST}; color: {TEXT_PRIMARY}; "
            "padding: 8px 14px; }")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 670])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

        if self._pages:
            self.toc.setCurrentItem(self.toc.topLevelItem(0))

    def show_page(self, page_id: str) -> bool:
        """Select `page_id` in the TOC (which renders it). False if there is no such page, so a
        deep-link typo leaves the dialog on whatever it was showing rather than blanking it."""
        for i in range(self.toc.topLevelItemCount()):
            item = self.toc.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == page_id:
                item.setHidden(False)
                self.toc.setCurrentItem(item)
                return True
        return False

    def _render(self, page_id: str):
        for pid, title, body in self._pages:
            if pid == page_id:
                self.browser.setMarkdown(f"# {title}\n\n{body}")
                self._space_out_blocks()
                self.browser.verticalScrollBar().setValue(0)
                return

    def _space_out_blocks(self):
        """Widen the vertical rhythm of the rendered page.

        `setMarkdown` builds a QTextDocument directly, so the document's default stylesheet
        (an HTML-only mechanism) never gets a say and every paragraph arrives with zero margins
        -- consecutive paragraphs run together into one wall of text, and a heading sits flush on
        the paragraph above it, which is the opposite of the skimmable reference page this is
        supposed to be. The margins have to be set on the blocks themselves after the fact.

        Blocks inside a table are left alone (row height is set by the cell text, so a bottom
        margin there just inflates every row), and so are code blocks, whose lines are separate
        blocks that would otherwise be pushed apart into unrelated-looking lines."""
        doc = self.browser.document()
        probe = QTextCursor(doc)
        block = doc.begin()
        while block.isValid():
            probe.setPosition(block.position())
            if probe.currentTable() is None and not block.blockFormat().nonBreakableLines():
                level = block.blockFormat().headingLevel()
                spacing = QTextBlockFormat()
                if level == 1:
                    spacing.setBottomMargin(8.0)
                elif level:
                    spacing.setTopMargin(16.0)
                    spacing.setBottomMargin(3.0)
                elif block.textList() is not None:
                    spacing.setTopMargin(2.0)
                    spacing.setBottomMargin(2.0)
                else:
                    spacing.setTopMargin(6.0)
                    spacing.setBottomMargin(6.0)
                QTextCursor(block).mergeBlockFormat(spacing)
            block = block.next()

    def _on_toc_selection(self, current, _previous):
        if current is None:
            return
        self._render(current.data(0, Qt.ItemDataRole.UserRole))

    def _on_anchor_clicked(self, url):
        """Cross-page links only. `setOpenLinks(False)` means nothing navigates on its own, so an
        external URL in a page is inert rather than launching a browser out of a manual that is
        deliberately offline."""
        target = os.path.splitext(os.path.basename(url.path() or url.toString()))[0]
        self.show_page(target)

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for i in range(self.toc.topLevelItemCount()):
            item = self.toc.topLevelItem(i)
            page_id = item.data(0, Qt.ItemDataRole.UserRole)
            haystack = ""
            for pid, title, body in self._pages:
                if pid == page_id:
                    haystack = f"{pid} {title} {body}".lower()
                    break
            item.setHidden(bool(needle) and needle not in haystack)
