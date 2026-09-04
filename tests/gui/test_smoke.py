"""Integration test for the `--smoke` contract MISSION §12/G2 spells out exactly (mirrors what
the driver's gate checks in `loop.py::_gui_smoke_ok`)."""
import json

from app.smoke import run_smoke


def test_smoke_contract(tmp_path):
    outdir = str(tmp_path / "gui")
    code = run_smoke(outdir)
    assert code == 0

    smoke_json = tmp_path / "gui" / "smoke.json"
    assert smoke_json.exists()
    data = json.loads(smoke_json.read_text())
    assert data["ok"] is True
    assert len(data["screenshots"]) >= 3

    pngs = [p for p in (tmp_path / "gui").glob("*.png") if p.stat().st_size >= 20_000]
    assert len(pngs) >= 3
