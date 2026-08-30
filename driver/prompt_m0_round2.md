## MILESTONE M0 (ROUND 2) — extend the frozen harness with M6–M13 and MR

Round 1 is complete: `harness/` is frozen at `harness-frozen`, and `rebuild.py` passes M1–M5.
Your job across the Round 2 M0 iterations is to extend `harness/` per MISSION.md §6.2 and §7.2
so the driver's M0 gate passes again:

1. `.venv/bin/python harness/selftest.py` exits 0 covering **M1–M13 and MR** (MR prints `[SKIP]`
   when `real_inputs/` is empty), and
2. `.venv/bin/python harness/score.py --milestone M6 --out out/score.json` exits 0 or 1 with
   contract-valid JSON — with the current pipeline that is a *failing* score (M6 needs a loft the
   pipeline does not have yet), and
3. `score.py --milestone M1 … M5` on the current pipeline still report `pass: true` — **the
   Round 1 truths, gates and numbers must not change.** Run this after every harness edit.

While the milestone is M0 the driver does NOT restore `harness/` from its tag, so your edits
stick; after the review pass it re-freezes. `pipeline/` is off-limits in M0 — do not touch it.

Suggested build order (one focused step per iteration; several iterations are expected):
1. `harness/milestones.py`: the new `MilestoneSpec` fields (§7.2) with defaults that leave M1–M5
   byte-for-byte equivalent; specs `_m6`.. `_m13`, `_mr`; registry order M1..M13, MR.
2. `harness/generators.py`: truth cache (`Mk.json` hash) and `--warm`; analytic builders in
   order M7 (primitives only), M11 (segments), M6 (ruled `ThruSections` star loft), M8 (dilated
   cavity: obround slots + `BRepFilletAPI_MakeFillet`; verify the offset property), M12, M10
   (M8 scaled ×1/40 + frame + inch STL), then `harness/voxelize.py` for M9 and M13. Check each
   truth's closed-form volume as you go; write `harness/truth/Mk.json`.
3. `harness/score.py`: per-spec `chord_tol`, frame mapping, spec-controlled `input_watertight`,
   the new key-gated checks (`per_solid_volume_err_pct`, `frame_axis_err_deg`,
   `axial_extent_err_mm`, `station_bands`, `n_stations_max`, `topo_events`, `min_edge_mm`),
   `n_solids` from the spec, MR skip semantics. Keep `progress` deterministic and monotone.
4. `harness/selftest.py`: `_BORE_FILLERS` for every new milestone, `_ideal_report` v2, one "2b"
   mutation per new gate proving it bites, the truth-correctness checks, the report-on-failure
   check, `[SKIP]` for MR. Keep the full run under 8 minutes with warm caches (`--milestone` for
   targeted runs while iterating).
5. `tests/test_score_round2.py`, `tests/test_voxelize.py`; keep `tests/test_score.py` green.

Read `docs/research/02-brep-loft-step-gmsh.md` (ThruSections, fillets, booleans) before the
generators and `docs/research/01-trimesh-slicing-fitting-metrics.md` before `voxelize.py`.
Truth files are gitignored; generation must be deterministic and cached. Record each truth's
volume in PROGRESS.md and in its `Mk.json`.
