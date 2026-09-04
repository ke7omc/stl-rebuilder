"""GUI test: the Output details page renders a "Verification" group at the top of the
property tree, one row per check, glyph-led and color-coded (green ✓ pass / amber ✗ fail /
grey – n-a) with the measured values, delta and tolerance in-line."""
from PySide6.QtGui import QColor

from app.main_window import _manifest_property_groups
from app.theme import SUCCESS, TEXT_DISABLED, WARNING
from app.widgets import PropertyTree

_VERIF = {
    "volume": {"input_mm3": 2.7523944e10, "solid_mm3": 2.7524139e10, "delta_pct": 0.0007,
               "tol_pct": 0.5, "pass": True},
    "bounds": {
        "x": {"input_mm": [-999.75, 1000.0], "solid_mm": [-999.74, 1000.0],
              "max_dev_mm": 0.01, "tol_mm": 1.0, "pass": True},
        "y": {"input_mm": [-999.75, 1000.0], "solid_mm": [-999.74, 1000.0],
              "max_dev_mm": 0.01, "tol_mm": 1.0, "pass": True},
        "z": {"input_mm": [0.0, 10000.0], "solid_mm": [0.0, 10012.0],
              "max_dev_mm": 12.0, "tol_mm": 10.0, "pass": False},
    },
    "bodies": {"expected": 1, "solid_bodies": 1, "pass": True},
    "deviation": {"pass": None, "error": "no tessellation"},
}


def test_verification_group_first_with_glyphs_and_colors(qtbot):
    man = {"output_path": "out.step", "stl_path": None, "warnings": [],
           "verification": _VERIF}
    groups = _manifest_property_groups(man)
    assert groups[0][0] == "Verification"

    tree = PropertyTree()
    qtbot.addWidget(tree)
    tree.set_groups(groups)
    top = tree.topLevelItem(0)
    assert top.text(0) == "Verification"
    rows = [(top.child(i).text(1), top.child(i).foreground(1).color().name())
            for i in range(top.childCount())]
    assert any(t.startswith("✓") and c == QColor(SUCCESS).name() for t, c in rows)
    assert any(t.startswith("✗") and c == QColor(WARNING).name() for t, c in rows)
    assert any(t.startswith("–") and c == QColor(TEXT_DISABLED).name() for t, c in rows)
    # measured values, delta and tolerance are all in-line in the value text
    vol_text = rows[0][0]
    assert "vs" in vol_text and "≤" in vol_text and "Δ" in vol_text


def test_no_verification_group_when_absent(qtbot):
    man = {"output_path": "out.step", "stl_path": None, "warnings": []}
    groups = _manifest_property_groups(man)
    assert groups[0][0] == "Output"
