"""gmsh headless meshability check (MISSION.md sec.7 / docs/research/02-...md sec.6).

Runs gmsh in a *subprocess* (this file, invoked as a script) so a crash or hang inside
gmsh's OCC/mesher cannot take down the scorer process. Public API is `check_meshability`.
"""
import json
import subprocess
import sys


def _run(step_path: str, hmax: float) -> dict:
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.logger.start()
    try:
        gmsh.model.add("chk")
        dimtags = gmsh.model.occ.importShapes(step_path, highestDimOnly=True)
        gmsh.model.occ.synchronize()
        n_solids = sum(1 for d, _ in dimtags if d == 3)
        if n_solids == 0:
            return {"ok": False, "reason": "STEP contains no solid (surfaces only)"}
        gmsh.option.setNumber("Mesh.MeshSizeMax", hmax)
        gmsh.option.setNumber("Mesh.MeshSizeMin", hmax / 10)
        try:
            gmsh.model.mesh.generate(3)
        except Exception as e:
            return {"ok": False, "reason": f"mesh generate raised: {e}"}
        try:
            # Netgen's optimizer sweeps element quality up substantially on the initial
            # Delaunay mesh (measured: M10's worst tet went from minSICN 0.031 to 0.284 on the
            # same hmax/hmin) at a cost of a few seconds; small/thin features (e.g. M10's 1/40
            # scale slot fillets) otherwise leave slivers the raw Delaunay pass doesn't clean up.
            # Best-effort: if it raises on some geometry, keep the un-optimized mesh/quality.
            gmsh.model.mesh.optimize("Netgen")
        except Exception:
            pass
        types, tags, _ = gmsh.model.mesh.getElements(3)
        all_tags = [t for grp in tags for t in grp]
        if not all_tags:
            return {"ok": False, "reason": "0 tets (silent 3D failure)"}
        q = gmsh.model.mesh.getElementQualities(all_tags, "minSICN")
        errs = [l for l in gmsh.logger.get() if l.startswith("Error")]
        min_q = float(min(q))
        return {
            "ok": bool(min_q > 0 and not errs),
            "n_tet": len(all_tags),
            "min_quality": min_q,
            "errors": errs,
        }
    finally:
        gmsh.finalize()


def check_meshability(step_path: str, hmax: float, timeout_s: float = 120.0) -> dict:
    """Import `step_path` into gmsh, mesh it in 3D at `hmax`, report tet count + min SICN.

    Always returns a dict with an "ok" key; never raises. On subprocess crash, timeout,
    or bad output, "ok" is False and "reason" explains why.
    """
    try:
        proc = subprocess.run(
            [sys.executable, __file__, step_path, repr(hmax)],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"gmsh subprocess exceeded {timeout_s}s timeout"}

    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
        return {"ok": False, "reason": f"gmsh subprocess crashed (exit {proc.returncode}): {tail}"}

    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        return {"ok": False, "reason": f"gmsh subprocess produced no parseable JSON: {e}"}


if __name__ == "__main__":
    step_path, hmax = sys.argv[1], float(sys.argv[2])
    print(json.dumps(_run(step_path, hmax)))
