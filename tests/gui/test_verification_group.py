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


def test_watertight_row_shown_first_when_present(qtbot):
    """Brady, 2026-09-06: "the end verification in the details pane should also state if it
    passed watertight verification" -- a Watertight row, leading the Verification group."""
    verif = dict(_VERIF, watertight={"pass": True})
    man = {"output_path": "out.step", "stl_path": None, "warnings": [], "verification": verif}
    groups = _manifest_property_groups(man)
    tree = PropertyTree()
    qtbot.addWidget(tree)
    tree.set_groups(groups)
    top = tree.topLevelItem(0)
    assert top.child(0).text(0) == "Watertight"
    assert top.child(0).text(1).startswith("✓")
    assert top.child(0).foreground(1).color().name() == QColor(SUCCESS).name()


def test_not_watertight_row_shown_as_a_failure(qtbot):
    verif = dict(_VERIF, watertight={"pass": False})
    man = {"output_path": "out.step", "stl_path": None, "warnings": [], "verification": verif}
    groups = _manifest_property_groups(man)
    tree = PropertyTree()
    qtbot.addWidget(tree)
    tree.set_groups(groups)
    top = tree.topLevelItem(0)
    assert top.child(0).text(0) == "Watertight"
    assert "NOT watertight" in top.child(0).text(1)
    assert top.child(0).foreground(1).color().name() == QColor(WARNING).name()


def test_surface_area_row_shown_after_volume(qtbot):
    """tapered_bore_dome_pinch_and_surface_area.md §5.4: a "Surface area" row, right after
    Volume, same m^3->m^2 idiom (input vs. solid, delta, tolerance in-line)."""
    verif = dict(_VERIF, surface_area={
        "input_mm2": 8.3088e7, "solid_mm2": 8.3838e7, "delta_pct": 0.903, "tol_pct": 1.0,
        "pass": True,
    })
    man = {"output_path": "out.step", "stl_path": None, "warnings": [], "verification": verif}
    groups = _manifest_property_groups(man)
    tree = PropertyTree()
    qtbot.addWidget(tree)
    tree.set_groups(groups)
    top = tree.topLevelItem(0)
    labels = [top.child(i).text(0) for i in range(top.childCount())]
    assert "Surface area" in labels
    assert labels.index("Surface area") == labels.index("Volume") + 1
    row = top.child(labels.index("Surface area"))
    assert row.text(1).startswith("✓")
    assert "vs" in row.text(1) and "≤" in row.text(1) and "Δ" in row.text(1)
    assert row.foreground(1).color().name() == QColor(SUCCESS).name()


def test_surface_area_row_shows_hint_on_failure(qtbot):
    verif = dict(_VERIF, surface_area={
        "input_mm2": 8.3088e7, "solid_mm2": 8.0e7, "delta_pct": 3.71, "tol_pct": 1.0,
        "pass": False,
        "hint": "missing/under-resolved surface features (thin fins/slots are the usual "
                "cause) — try more --sections, --adaptive, or a finer --chord-tol",
    })
    man = {"output_path": "out.step", "stl_path": None, "warnings": [], "verification": verif}
    groups = _manifest_property_groups(man)
    tree = PropertyTree()
    qtbot.addWidget(tree)
    tree.set_groups(groups)
    top = tree.topLevelItem(0)
    labels = [top.child(i).text(0) for i in range(top.childCount())]
    row = top.child(labels.index("Surface area"))
    assert row.text(1).startswith("✗")
    assert "WHAT TO DO" in row.text(1)
    assert row.foreground(1).color().name() == QColor(WARNING).name()
