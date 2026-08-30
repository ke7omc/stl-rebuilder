"""Unit tests for harness/voxelize.py (MISSION §7.2's voxel InputSpec synthesis)."""
from dataclasses import dataclass

import numpy as np
import trimesh

from harness import voxelize


def _sphere(radius=100.0, subdivisions=4):
    # Watertight, well-tessellated -- stands in for the harness's own ct/2 truth mesh.
    return trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)


def test_marching_cubes_surface_matches_sphere_radius():
    r = 100.0
    h = 5.0
    mesh = _sphere(radius=r)
    verts, faces = voxelize.marching_cubes_surface(mesh, spacing_mm=(h, h, h), seed=0)
    assert len(faces) > 0
    radii = np.linalg.norm(verts, axis=1)
    # MC on an exact SDF deviates ~h^2*kappa/8 on smooth faces (MISSION §6.2); a sphere's
    # curvature kappa=1/r is tiny here, so the achieved error is well inside the suggested
    # 0.1*h envelope.
    assert np.abs(radii - r).max() < 0.1 * h


def test_marching_cubes_surface_is_watertight_and_outward_facing():
    r = 50.0
    h = 4.0
    mesh = _sphere(radius=r)
    verts, faces = voxelize.marching_cubes_surface(mesh, spacing_mm=(h, h, h), seed=0)
    out = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    assert out.is_watertight
    # gradient_direction="descent" with inside-positive phi should yield outward normals: mean
    # vertex-to-centroid vector should agree in sign with the face normal at each face centroid.
    assert out.volume > 0


def test_grid_too_coarse_raises():
    mesh = _sphere(radius=5.0)
    try:
        voxelize.marching_cubes_surface(mesh, spacing_mm=(1000.0, 1000.0, 1000.0), seed=0)
        assert False, "expected RuntimeError for a grid that misses the surface entirely"
    except RuntimeError:
        pass


@dataclass
class _FakeSpec:
    spacing_mm: tuple
    noise_sigma_mm: float = 0.0
    unweld_jitter_mm: float = 0.0
    flip_frac: float = 0.0
    islands: int = 0


def test_synthesize_voxel_input_noise_only_is_deterministic_and_bounded():
    mesh = _sphere(radius=80.0)
    spec = _FakeSpec(spacing_mm=(4.0, 4.0, 4.0), noise_sigma_mm=0.5)
    a = voxelize.synthesize_voxel_input(mesh, spec, seed=7)
    b = voxelize.synthesize_voxel_input(mesh, spec, seed=7)
    assert np.array_equal(a.vertices, b.vertices)
    radii = np.linalg.norm(a.vertices, axis=1)
    # 0.5mm sigma noise on an r=80 sphere: a handful of sigma out to ~4 is still << the radius.
    assert np.abs(radii - 80.0).max() < 5.0


def test_synthesize_voxel_input_islands_add_disconnected_components():
    mesh = _sphere(radius=80.0)
    base_spec = _FakeSpec(spacing_mm=(4.0, 4.0, 4.0))
    base = voxelize.synthesize_voxel_input(mesh, base_spec, seed=7)
    n_base_components = len(base.split(only_watertight=False))

    spec = _FakeSpec(spacing_mm=(4.0, 4.0, 4.0), islands=3)
    out = voxelize.synthesize_voxel_input(mesh, spec, seed=7)
    n_components = len(out.split(only_watertight=False))
    assert n_components == n_base_components + 3
    assert len(out.vertices) == len(base.vertices) + 3 * 4
    assert len(out.faces) == len(base.faces) + 3 * 4


def test_synthesize_voxel_input_unweld_jitter_breaks_vertex_sharing():
    mesh = _sphere(radius=80.0)
    spec = _FakeSpec(spacing_mm=(4.0, 4.0, 4.0), unweld_jitter_mm=1e-3)
    out = voxelize.synthesize_voxel_input(mesh, spec, seed=7)
    assert len(out.vertices) == 3 * len(out.faces)


def test_synthesize_voxel_input_flip_frac_reverses_some_facets():
    mesh = _sphere(radius=80.0)
    base_spec = _FakeSpec(spacing_mm=(4.0, 4.0, 4.0))
    base = voxelize.synthesize_voxel_input(mesh, base_spec, seed=7)
    spec = _FakeSpec(spacing_mm=(4.0, 4.0, 4.0), flip_frac=0.5)
    out = voxelize.synthesize_voxel_input(mesh, spec, seed=7)
    n_diff = np.sum(np.any(out.faces != base.faces, axis=1))
    assert n_diff > 0


def test_synthesize_voxel_input_unit_scale():
    mesh = _sphere(radius=80.0)
    spec = _FakeSpec(spacing_mm=(4.0, 4.0, 4.0))
    mm = voxelize.synthesize_voxel_input(mesh, spec, seed=7, scale=1.0)
    inches = voxelize.synthesize_voxel_input(mesh, spec, seed=7, scale=1.0 / 25.4)
    np.testing.assert_allclose(inches.vertices, mm.vertices / 25.4)
