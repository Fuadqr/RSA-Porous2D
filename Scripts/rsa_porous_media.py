"""
================================================================================
 RSA 2D POROUS-MEDIA GENERATOR  (configurable, GUI-ready)
================================================================================

WHAT THIS IS
------------
A generator for **2D disk packings** used as pore-scale porous media for CFD /
CFD-DEM. Grains are circles in a plane (the X-Z plane); this is NOT a 3D sphere
packing and the porosity / throat statistics differ from a 3D medium. A constant
out-of-plane Y coordinate is written so the output drops straight into a 3D
immersed-boundary solver.

The pore-network (throat) analysis is produced automatically as a BYPRODUCT of
generation: Delaunay neighbour graph + line-of-sight pruning -> throat widths,
fixed-micrometre bin table, CSV, and optional plots.

HONEST SCOPE NOTES (read these; they shape what the tool can promise)
---------------------------------------------------------------------
1. POROSITY IS A TARGET, NOT A GUARANTEE. Pure RSA jams. 2D monodisperse RSA
   saturates near a surface coverage of ~0.547 (porosity ~0.45); polydisperse
   mixtures pack denser, but the achievable floor depends entirely on the grain
   size distribution. The tool reports the ACHIEVED porosity and warns if it
   stalls above target. (The ~0.547 figure is the well-known approximate
   monodisperse value; do not treat it as exact for your PSD.)

2. THROAT CONTROL HAS TWO MODES, with a real trade-off:
     - 'soft' : best-of-K biased placement. Reduces the number of throats below
                the target but NEVER forbids them. Keeps RSA character/density.
     - 'hard' : a strict minimum surface gap. Guarantees no two near grains sit
                closer than `min_throat`, but lowers achievable porosity and can
                fail to converge for tight targets. Stronger than a pure Delaunay
                throat floor, since it constrains all close pairs.
   Requesting a low porosity AND a large hard min-throat can be mutually
   infeasible; the feasibility check flags this.

3. PARALLELISM. The RSA insertion loop is inherently SEQUENTIAL (each grain
   depends on all previously placed grains), so the core packing is single
   threaded. What is parallelised honestly here is the ENSEMBLE: independent
   realisations (different seeds) run across processes. That is the genuine,
   embarrassingly-parallel win. The per-realisation speed comes from the spatial
   hash grid, not threads.

All knobs live in the CONFIG block below. A future GUI just constructs a Config.
================================================================================
"""

from __future__ import annotations

import os
import csv
import math
import time
import warnings
from dataclasses import dataclass, field, asdict
from concurrent.futures import ProcessPoolExecutor


class CancelledError(Exception):
    """Raised when a cancel_cb requests an early stop (e.g. GUI 'Stop' button)."""


def _scaled_cb(cb, lo, hi):
    """Wrap a progress callback so a local 0..1 fraction maps into [lo, hi].

    Retained as a utility. The GUI no longer wraps the heterogeneity phases with
    this: each phase reports its OWN local 0..1 so the GUI can show two
    independent progress bars. The phase is identified by the desc string."""
    if cb is None:
        return None

    def inner(frac, msg):
        cb(lo + (hi - lo) * frac, msg)

    return inner

import numpy as np
from scipy import stats as sps
from scipy.spatial import Delaunay

# tqdm is optional; fall back to a no-op bar object if it is not installed.
try:
    from tqdm import tqdm
except Exception:  # pragma: no cover
    class _NoBar:
        def __init__(self, *a, **k):
            self.n = 0

        def set_postfix_str(self, *a, **k):
            pass

        def refresh(self):
            pass

        def close(self):
            pass

    def tqdm(*a, **k):
        return _NoBar()


# =============================================================================
# CONFIG  --  edit everything here (or build this object from a GUI)
# =============================================================================
@dataclass
class Config:
    # ---- 1. Domain (2D, X-Z plane). Sizes in metres. -----------------------
    width: float = 0.050           # domain extent in X [m]
    height: float = 0.060          # domain extent in Z [m]
    y_fixed: float = 0.0001        # constant out-of-plane Y written to outputs
    x_origin: float = 0.005        # world X offset added on output (0 = none)
    z_origin: float = 0.000        # world Z offset added on output
    # Boundary wall snapping: forces grains within snap_wall_threshold of a wall
    # to sit exactly tangent. ON gives a clean flush edge but a more regimented
    # boundary; turn OFF (or shrink the distance) to relax the edge ordering.
    snap_wall_enabled: bool = True
    snap_wall_threshold: float = 3.0e-4   # snap distance [m] (300 um default)

    # ---- 2. Grain-size distribution ----------------------------------------
    # dist_type: 'lognormal' | 'normal' | 'uniform' | 'custom'
    dist_type: str = "lognormal"
    r_min: float = 0.0008          # smallest grain radius [m]
    r_max: float = 0.0018          # largest grain radius [m]
    num_sizes: int = 24            # number of discrete radii bins
    # distribution parameters (only the ones for dist_type are used):
    ln_sigma: float = 0.35         # lognormal shape (sigma of underlying normal)
    ln_median: float = 0.0009      # lognormal median radius [m] (within range)
    nm_mean: float = 0.0012        # normal mean radius [m]
    nm_std: float = 0.0003         # normal std [m]
    # for dist_type == 'custom', supply explicit radii + weights (else None):
    custom_radii: tuple | None = None
    custom_weights: tuple | None = None

    # ---- 3. Porosity (TARGET, not guaranteed) ------------------------------
    target_porosity: float = 0.42

    # ---- 4. Heterogeneity: 'homogeneous' | 'layer' -------------------------
    medium_type: str = "homogeneous"
    # Heterogeneity settings. 'layer' keeps the original band/patch behavior.
    # 'polygon' can store additional completed polygons in heterogeneity_polygons.
    layer_shape: str = "rectangle"
    layer_polygon: tuple | None = (0.020, 0.000, 0.030, 0.000,
                                   0.030, 0.060, 0.020, 0.060)
    # Internal GUI storage for additional polygons. Groups are flattened x,z
    # vertex pairs separated by nan,nan.
    heterogeneity_polygons: tuple | None = None
    # The layer is a rectangle bounded in BOTH axes. Set a pair to the full
    # domain extent to recover an infinite band; keep both tight for a patch.
    # The wavy interface is applied to the edges of the NARROWER (band-normal)
    # axis only; the wider axis keeps clean edges.
    layer_x_start: float = 0.020   # rectangle X bounds [m]
    layer_x_end: float = 0.030
    layer_z_start: float = 0.000   # rectangle Z bounds [m] (full height = band)
    layer_z_end: float = 0.060
    layer_porosity: float = 0.42   # target porosity inside the heterogeneity
    layer_dist_type: str = "lognormal"
    layer_r_min: float = 0.0005
    layer_r_max: float = 0.0008
    layer_num_sizes: int = 10
    layer_ln_sigma: float = 0.21
    layer_ln_median: float = 0.0006
    layer_nm_mean: float = 0.00065
    layer_nm_std: float = 0.00005
    rough_interface: bool = True
    # Interface roughness ADAPTS to the surrounding grains by default:
    # amplitude <= 0 means "use the matrix grain scale" and blank freqs mean
    # "wavelength of a few matrix grain diameters per edge". Setting explicit
    # values still overrides the adaptive treatment.
    interface_amplitude: float = 0.0
    interface_freqs: tuple | None = None

    # ---- 5. Minimum pore throat -------------------------------------------
    # throat_mode: 'none' | 'soft' | 'hard'   (see HONEST SCOPE NOTE 2)
    throat_mode: str = "soft"
    min_throat: float = 100e-6     # target / floor [m] depending on mode
    k_candidates: int = 15         # best-of-K placement (1 == plain RSA)

    # RSA solver termination (engine/CLI only; deliberately NOT in the GUI).
    # max_attempts: random position trials per grain before that grain counts
    # as unplaceable. stall_limit: consecutive unplaceable grains before the
    # packing gives up and reports what it achieved ("jammed above target").
    max_attempts: int = 4000
    stall_limit: int = 400

    # ---- 6. Output ---------------------------------------------------------
    out_dir: str = "."
    out_basename: str = "porous_medium"
    # output_format: 'dat' (whitespace, default) | 'csv' | 'cin' (MultiFlow)
    output_format: str = "dat"
    use_diameter: bool = False     # False -> 4th column is radius, True -> diameter

    # ---- 7/8. Parallel ensemble + safety ----------------------------------
    seed: int = 42                 # base RNG seed
    n_realizations: int = 1        # >1 -> generate an ensemble
    n_workers: int = 1             # >1 -> run realisations across processes
    warn_particle_count: int = 50000   # warn above this estimated N
    strict_feasibility: bool = False   # True -> raise instead of warn

    # ---- diagnostics / plotting -------------------------------------------
    run_throat_analysis: bool = True
    throat_bin_width_um: float = 25.0
    make_plots: bool = False       # off by default (headless / ensemble safe)

    @property
    def domain_area(self) -> float:
        return self.width * self.height


# The instance the user edits (or the GUI builds):
CONFIG = Config()


# =============================================================================
# DISTRIBUTIONS
# =============================================================================
def build_psd(dist_type, r_min, r_max, num_sizes,
              ln_sigma=0.35, ln_median=None,
              nm_mean=None, nm_std=None,
              custom_radii=None, custom_weights=None):
    """Return (radii[np], weights[np]) discretising the requested distribution.

    Weights come from CDF mass in each bin, so the realised PSD tracks the
    requested one (subject to RSA jamming bias, which is reported at run time).
    """
    if dist_type == "custom":
        if custom_radii is None or custom_weights is None:
            raise ValueError("dist_type='custom' requires custom_radii and custom_weights.")
        r = np.asarray(custom_radii, float)
        w = np.asarray(custom_weights, float)
        w = w / w.sum()
        return r, w

    # bin centres: log-spaced for lognormal, linear otherwise
    if dist_type == "lognormal":
        radii = np.logspace(np.log10(r_min), np.log10(r_max), num_sizes)
    else:
        radii = np.linspace(r_min, r_max, num_sizes)

    # bin edges around centres
    edges = np.empty(num_sizes + 1)
    edges[1:-1] = 0.5 * (radii[:-1] + radii[1:])
    edges[0] = radii[0] - (radii[1] - radii[0]) / 2
    edges[-1] = radii[-1] + (radii[-1] - radii[-2]) / 2

    if dist_type == "lognormal":
        scale = ln_median if ln_median is not None else math.sqrt(r_min * r_max)
        dist = sps.lognorm(s=ln_sigma, scale=scale)
    elif dist_type == "normal":
        mean = nm_mean if nm_mean is not None else 0.5 * (r_min + r_max)
        std = nm_std if nm_std is not None else (r_max - r_min) / 6
        dist = sps.norm(loc=mean, scale=std)
    elif dist_type == "uniform":
        dist = sps.uniform(loc=r_min, scale=(r_max - r_min))
    else:
        raise ValueError(f"Unknown dist_type={dist_type!r}.")

    w = np.array([dist.cdf(edges[i + 1]) - dist.cdf(edges[i]) for i in range(num_sizes)])
    if w.sum() <= 0:
        w = np.ones(num_sizes)
    w = w / w.sum()
    return radii, w


def psd_diagnostics(dist_type, r_min, r_max, num_sizes,
                    ln_sigma=0.35, ln_median=None, nm_mean=None, nm_std=None,
                    custom_radii=None, custom_weights=None, n_curve=200):
    """Describe what RSA will SAMPLE from (NOT the final placed PSD, which only
    emerges after packing). Returns discretised bin weights, the continuous
    target curve for overlay, and two honesty flags:
      coverage   : fraction of the continuous distribution's mass that falls
                   inside [r_min, r_max] (low => your range clips the tails).
      max_weight : largest single-bin weight (high => effectively monodisperse,
                   e.g. a lognormal whose spread collapses onto one size)."""
    radii, weights = build_psd(dist_type, r_min, r_max, num_sizes,
                               ln_sigma, ln_median, nm_mean, nm_std,
                               custom_radii, custom_weights)
    curve_r = curve_pdf = None
    coverage = 1.0
    if dist_type in ("lognormal", "normal", "uniform"):
        if dist_type == "lognormal":
            scale = ln_median if ln_median is not None else math.sqrt(r_min * r_max)
            dist = sps.lognorm(s=ln_sigma, scale=scale)
        elif dist_type == "normal":
            mean = nm_mean if nm_mean is not None else 0.5 * (r_min + r_max)
            std = nm_std if nm_std is not None else (r_max - r_min) / 6
            dist = sps.norm(loc=mean, scale=std)
        else:
            dist = sps.uniform(loc=r_min, scale=(r_max - r_min))
        curve_r = np.linspace(r_min, r_max, n_curve)
        curve_pdf = dist.pdf(curve_r)
        coverage = float(dist.cdf(r_max) - dist.cdf(r_min))
    return {
        "radii": np.asarray(radii), "weights": np.asarray(weights),
        "curve_r": curve_r, "curve_pdf": curve_pdf,
        "coverage": coverage, "max_weight": float(np.max(weights)) if len(weights) else 1.0,
    }


# =============================================================================
# SPATIAL HASH GRID
# =============================================================================
class SpatialGrid:
    def __init__(self, cell):
        self.inv = 1.0 / max(cell, 1e-12)
        self.grid = {}
        self.max_r = cell

    def _key(self, x, y):
        return int(x * self.inv), int(y * self.inv)

    def insert(self, idx, x, y):
        self.grid.setdefault(self._key(x, y), []).append(idx)

    def neighbors(self, x, y, reach):
        ix, iy = self._key(x, y)
        sr = int(np.ceil((reach + self.max_r) * self.inv))
        for dx in range(-sr, sr + 1):
            for dy in range(-sr, sr + 1):
                cell = self.grid.get((ix + dx, iy + dy))
                if cell:
                    yield from cell


# =============================================================================
# GEOMETRY HELPERS
# =============================================================================
def porosity(radii, area):
    if len(radii) == 0:
        return 1.0
    return 1.0 - float(np.sum(np.pi * np.asarray(radii) ** 2)) / area


def estimate_n(radii, weights, phi, area):
    r = np.asarray(radii); w = np.asarray(weights, float); w = w / w.sum()
    return int(np.ceil((1.0 - phi) * area / np.sum(w * np.pi * r ** 2)))


def overlaps(x, z, r, centers, radii, grid, min_gap):
    for j in grid.neighbors(x, z, r):
        cx, cz = centers[j]
        sep = r + radii[j] + min_gap
        if (x - cx) ** 2 + (z - cz) ** 2 <= sep ** 2 + 1e-20:
            return True
    return False


def placement_score(x, z, r, centers, radii, grid, gap_target):
    """Lower is better. Primary: count of neighbours closer than gap_target.
    Tie-break: larger minimum gap, capped at gap_target (no over-dispersion)."""
    n_tight = 0
    min_gap = np.inf
    for j in grid.neighbors(x, z, r + gap_target):
        cx, cz = centers[j]
        gap = math.hypot(x - cx, z - cz) - r - radii[j]
        if gap < min_gap:
            min_gap = gap
        if gap < gap_target:
            n_tight += 1
    return (n_tight, -min(min_gap, gap_target))


def snap_wall(x, z, r, width, height, thr=3.0e-4):
    if 0 < x - r < thr:
        x = r
    elif 0 < width - (x + r) < thr:
        x = width - r
    if 0 < z - r < thr:
        z = r
    elif 0 < height - (z + r) < thr:
        z = height - r
    return x, z


def hybrid_sample(radii, weights, n_total, rng, min_per_size=2):
    out = []
    for r in radii:
        out.extend([r] * min_per_size)
    rem = n_total - len(out)
    if rem > 0:
        out.extend(list(rng.choice(radii, size=rem, p=weights)))
    return out[:max(n_total, len(radii) * min_per_size)]


def _mode_params(throat_mode, min_throat, k_candidates):
    """Map a throat mode to (min_gap, gap_target, k) used by the packer."""
    if throat_mode == "none":
        return 0.0, 0.0, 1
    if throat_mode == "soft":
        return 0.0, min_throat, max(1, k_candidates)
    if throat_mode == "hard":
        return min_throat, min_throat, max(1, k_candidates)
    raise ValueError(f"Unknown throat_mode={throat_mode!r}.")


# =============================================================================
# CORE RSA FILL  (shared by homogeneous + layer)
# =============================================================================
def rsa_fill(rng, radii_pool, *, bbox, region_area, target_porosity,
             min_gap, gap_target, k_candidates,
             max_attempts=4000, stall_limit=400,
             allow_fn=None, obstacles=None, progress=False, desc="RSA",
             progress_cb=None, cancel_cb=None,
             snap_enabled=True, snap_thr=3.0e-4):
    """Place grains by best-of-K RSA into bbox region.

    radii_pool : sorted (large->small) list of radii to try.
    obstacles  : optional (centers, radii, grid) the new grains must avoid.
    allow_fn   : optional predicate(x,z,r)->bool to restrict placement region.
    progress_cb: optional callable(fraction in 0..1, message) for GUI bars.
    cancel_cb  : optional callable()->bool; if it returns True, raise CancelledError.
    Returns (centers, radii, grid) for the newly placed grains.
    """
    bx0, bx1, bz0, bz1 = bbox
    max_r = float(max(radii_pool))
    cell = max_r + min_gap
    grid = SpatialGrid(cell)
    centers, radii = [], []
    ob_c = ob_r = ob_g = None
    if obstacles is not None:
        ob_c, ob_r, ob_g = obstacles

    denom = max(1.0 - target_porosity, 1e-6)
    stall = 0
    last_emit = time.time()

    # Progress is measured by porosity gap closed, NOT by candidate-pool index.
    # That gives an honest bar that fills 0->100% and completes, instead of one
    # tied to the pool that stalls when the porosity target is hit early. For a
    # heterogeneity this yields two clean sequential bars.
    bar = tqdm(total=100, desc=desc, disable=not progress,
               bar_format="{l_bar}{bar:25}| {percentage:3.0f}% [{elapsed}] {postfix}") if progress else None

    for r in radii_pool:
        if cancel_cb is not None and cancel_cb():
            if bar is not None:
                bar.close()
            raise CancelledError(desc)
        phi = porosity(radii, region_area)
        if phi <= target_porosity:
            break
        frac = min(max((1.0 - phi) / denom, 0.0), 1.0)
        if bar is not None:
            bar.n = frac * 100.0
            bar.set_postfix_str(f"N={len(centers)} phi={phi:.3f}", refresh=False)
            bar.refresh()
        if progress_cb is not None:
            now = time.time()
            if now - last_emit > 0.1:
                # include the live stats so GUI bars can show the same
                # N / phi readout as the CLI tqdm bar
                progress_cb(frac, f"{desc} N={len(centers)} phi={phi:.3f}")
                last_emit = now
        lo_x, hi_x = max(r, bx0), min(bx1 - r, bx1)
        lo_z, hi_z = max(r, bz0), min(bz1 - r, bz1)
        if hi_x <= lo_x or hi_z <= lo_z:
            continue

        best_xy, best_score, n_valid = None, None, 0
        for _ in range(max_attempts):
            x = rng.uniform(lo_x, hi_x)
            z = rng.uniform(lo_z, hi_z)
            if snap_enabled:
                x, z = snap_wall(x, z, r, bx1, bz1, snap_thr)
            if allow_fn is not None and not allow_fn(x, z, r):
                continue
            if ob_g is not None and overlaps(x, z, r, ob_c, ob_r, ob_g, min_gap):
                continue
            if overlaps(x, z, r, centers, radii, grid, min_gap):
                continue
            score = placement_score(x, z, r, centers, radii, grid, gap_target)
            if best_score is None or score < best_score:
                best_score, best_xy = score, (x, z)
            n_valid += 1
            if n_valid >= k_candidates:
                break

        if best_xy is not None:
            x, z = best_xy
            centers.append((x, z)); radii.append(r)
            grid.insert(len(centers) - 1, x, z)
            stall = 0
        else:
            stall += 1
            if stall >= stall_limit:
                break

    if bar is not None:
        bar.n = 100.0
        bar.set_postfix_str(f"N={len(centers)} phi={porosity(radii, region_area):.3f}", refresh=False)
        bar.refresh()
        bar.close()
    if progress_cb is not None:
        # final tick: phases that hit the target between emit intervals would
        # otherwise leave the GUI bar resting below 100%
        progress_cb(1.0, f"{desc} N={len(centers)} phi={porosity(radii, region_area):.3f}")
    return centers, radii, grid


def _radii_pool(rng, psd_radii, psd_weights, target_porosity, area, oversample=1.6):
    n = int(oversample * estimate_n(psd_radii, psd_weights, target_porosity, area))
    pool = hybrid_sample(psd_radii, psd_weights, n, rng, min_per_size=2)
    pool.sort(reverse=True)
    return pool


# =============================================================================
# HOMOGENEOUS + LAYER BUILDERS
# =============================================================================
def build_homogeneous(cfg: Config, seed: int, progress_cb=None, cancel_cb=None):
    rng = np.random.RandomState(seed)
    psd_r, psd_w = build_psd(cfg.dist_type, cfg.r_min, cfg.r_max, cfg.num_sizes,
                             cfg.ln_sigma, cfg.ln_median, cfg.nm_mean, cfg.nm_std,
                             cfg.custom_radii, cfg.custom_weights)
    min_gap, gap_target, k = _mode_params(cfg.throat_mode, cfg.min_throat, cfg.k_candidates)

    pool = _radii_pool(rng, psd_r, psd_w, cfg.target_porosity, cfg.domain_area)
    centers, radii, _ = rsa_fill(
        rng, pool, bbox=(0, cfg.width, 0, cfg.height),
        region_area=cfg.domain_area, target_porosity=cfg.target_porosity,
        min_gap=min_gap, gap_target=gap_target, k_candidates=k,
        max_attempts=cfg.max_attempts, stall_limit=cfg.stall_limit,
        progress=cfg.n_realizations == 1, desc="[homogeneous] RSA",
        progress_cb=progress_cb, cancel_cb=cancel_cb,
        snap_enabled=cfg.snap_wall_enabled, snap_thr=cfg.snap_wall_threshold)

    centers = np.asarray(centers, float)
    radii = np.asarray(radii, float)
    materials = np.array(["matrix"] * len(radii), dtype=object)
    meta = {"phi": porosity(radii, cfg.domain_area), "n": len(radii)}
    return centers, radii, materials, meta


def _clean_layer_polygon(points, width, height):
    """Return clipped (x, z) vertices from a flat x,z tuple/list."""
    if points is None:
        return []
    try:
        vals = [float(v) for v in points]
    except (TypeError, ValueError):
        return []

    pts = []
    for i in range(0, len(vals) - 1, 2):
        x = min(max(vals[i], 0.0), width)
        z = min(max(vals[i + 1], 0.0), height)
        if not pts or abs(x - pts[-1][0]) > 1e-12 or abs(z - pts[-1][1]) > 1e-12:
            pts.append((x, z))
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) <= 1e-12 and abs(pts[0][1] - pts[-1][1]) <= 1e-12:
        pts.pop()
    return pts


def _clean_point_pairs(points, width, height):
    """Return clipped x,z pairs without polygon-specific closing cleanup."""
    if points is None:
        return []
    try:
        vals = [float(v) for v in points]
    except (TypeError, ValueError):
        return []
    pts = []
    for i in range(0, len(vals) - 1, 2):
        x = min(max(vals[i], 0.0), width)
        z = min(max(vals[i + 1], 0.0), height)
        pts.append((x, z))
    return pts


def _clean_polygon_groups(points, width, height):
    """Return a list of clipped polygons from flat x,z groups.

    Groups are separated by non-finite pairs such as nan,nan. This format keeps
    the config simple while allowing the GUI to store multiple independent
    polygons without changing old single-polygon behavior.
    """
    if points is None:
        return []
    try:
        vals = [float(v) for v in points]
    except (TypeError, ValueError):
        return []

    groups = []
    current = []
    for i in range(0, len(vals) - 1, 2):
        x_raw, z_raw = vals[i], vals[i + 1]
        if not (math.isfinite(x_raw) and math.isfinite(z_raw)):
            if current:
                groups.append(current)
                current = []
            continue
        x = min(max(x_raw, 0.0), width)
        z = min(max(z_raw, 0.0), height)
        if not current or abs(x - current[-1][0]) > 1e-12 or abs(z - current[-1][1]) > 1e-12:
            current.append((x, z))
    if current:
        groups.append(current)

    cleaned = []
    for group in groups:
        if len(group) > 1 and abs(group[0][0] - group[-1][0]) <= 1e-12 and abs(group[0][1] - group[-1][1]) <= 1e-12:
            group = group[:-1]
        cleaned.append(group)
    return cleaned


def _polygon_area(points):
    if len(points) < 3:
        return 0.0
    s = 0.0
    for i, (x0, z0) in enumerate(points):
        x1, z1 = points[(i + 1) % len(points)]
        s += x0 * z1 - x1 * z0
    return 0.5 * abs(s)


def _polygon_centroid(points):
    if not points:
        return 0.0, 0.0
    if len(points) < 3:
        return (sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points))
    signed_area2 = 0.0
    cx = 0.0
    cz = 0.0
    for i, (x0, z0) in enumerate(points):
        x1, z1 = points[(i + 1) % len(points)]
        cross = x0 * z1 - x1 * z0
        signed_area2 += cross
        cx += (x0 + x1) * cross
        cz += (z0 + z1) * cross
    if abs(signed_area2) < 1e-18:
        return (sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points))
    return cx / (3.0 * signed_area2), cz / (3.0 * signed_area2)


def _translate_polygon_to_center(polygon, center, width, height):
    base_cx, base_cz = _polygon_centroid(polygon)
    dx = float(center[0]) - base_cx
    dz = float(center[1]) - base_cz
    moved = [(x + dx, z + dz) for x, z in polygon]
    min_x = min(p[0] for p in moved)
    max_x = max(p[0] for p in moved)
    min_z = min(p[1] for p in moved)
    max_z = max(p[1] for p in moved)
    if min_x < 0.0:
        dx -= min_x
    if max_x > width:
        dx -= max_x - width
    if min_z < 0.0:
        dz -= min_z
    if max_z > height:
        dz -= max_z - height
    return [(min(max(x + dx, 0.0), width),
             min(max(z + dz, 0.0), height)) for x, z in polygon]


def _point_on_segment(x, z, x0, z0, x1, z1, tol=1e-12):
    dx = x1 - x0
    dz = z1 - z0
    cross = (x - x0) * dz - (z - z0) * dx
    if abs(cross) > tol:
        return False
    dot = (x - x0) * dx + (z - z0) * dz
    if dot < -tol:
        return False
    seg_len2 = dx * dx + dz * dz
    return dot <= seg_len2 + tol


def _point_in_polygon(x, z, points):
    """Ray-casting inclusion test with boundary treated as inside."""
    if len(points) < 3:
        return False

    inside = False
    j = len(points) - 1
    for i, (xi, zi) in enumerate(points):
        xj, zj = points[j]
        if _point_on_segment(x, z, xi, zi, xj, zj):
            return True
        if (zi > z) != (zj > z):
            x_cross = (xj - xi) * (z - zi) / (zj - zi) + xi
            if x <= x_cross:
                inside = not inside
        j = i
    return inside


def make_layer_geometry(cfg: Config, seed=None):
    """Resolve the layer shape. Returns helpers shared by build_layer and the
    live overview so they always agree.

    The layer is bounded in both axes: x in [xs, xe], z in [zs, ze]. The rough
    interface roughens every edge that is INTERNAL (faces the matrix); edges
    flush against a domain wall stay straight (roughening them would push grains
    out of the domain). Roughness is TAPERED to zero near each corner, so corners
    stay near the nominal rectangle and only mid-edges wobble. Everything is
    hard-clipped to the nominal rectangle, so wobble never leaves the box -- the
    nominal rectangle is the hard boundary and the wavy edges push inward only."""
    if getattr(cfg, "layer_shape", "rectangle") == "polygon":
        raw_groups = _clean_polygon_groups(getattr(cfg, "heterogeneity_polygons", None),
                                           cfg.width, cfg.height)
        current = _clean_layer_polygon(getattr(cfg, "layer_polygon", None),
                                       cfg.width, cfg.height)
        polygons = []
        for poly in raw_groups + [current]:
            if len(poly) >= 3 and _polygon_area(poly) > 0.0:
                if not polygons or poly != polygons[-1]:
                    polygons.append(poly)
        if polygons:
            all_pts = [p for poly in polygons for p in poly]
            xs = min(p[0] for p in all_pts)
            xe = max(p[0] for p in all_pts)
            zs = min(p[1] for p in all_pts)
            ze = max(p[1] for p in all_pts)
            wx, wz = xe - xs, ze - zs
            total_area = sum(_polygon_area(poly) for poly in polygons)
            edge_internal = {"x_lo": False, "x_hi": False,
                             "z_lo": False, "z_hi": False}

            def edge_offset(edge, coord, lo, hi):
                return 0.0

            def in_layer(x, z):
                if not (xs <= x <= xe and zs <= z <= ze):
                    return False
                return any(_point_in_polygon(x, z, poly) for poly in polygons)

            shape = "polygon" if len(polygons) == 1 else "multipolygon"
            return {
                "shape": shape,
                "polygon": polygons[0] if polygons else [],
                "polygons": polygons,
                "centers": [_polygon_centroid(poly) for poly in polygons],
                "xs": xs, "xe": xe, "zs": zs, "ze": ze, "wx": wx, "wz": wz,
                "edge_internal": edge_internal, "edge_offset": edge_offset,
                "in_layer": in_layer, "bbox": (xs, xe, zs, ze),
                "area": total_area,
            }

    xs, xe = sorted((cfg.layer_x_start, cfg.layer_x_end))
    zs, ze = sorted((cfg.layer_z_start, cfg.layer_z_end))
    xs, xe = max(0.0, xs), min(cfg.width, xe)
    zs, ze = max(0.0, zs), min(cfg.height, ze)
    wx, wz = xe - xs, ze - zs

    # which edges are internal (not flush against a domain wall)?
    tol = 1e-9
    edge_internal = {
        "x_lo": xs > tol,                 # left
        "x_hi": xe < cfg.width - tol,     # right
        "z_lo": zs > tol,                 # bottom
        "z_hi": ze < cfg.height - tol,    # top
    }

    if seed is None:
        seed = cfg.seed + 999
    rng = np.random.RandomState(seed)

    # ---- adaptive interface treatment --------------------------------------
    # The raggedness of a real interface is set by the grains that frame it,
    # so fixed user values turn cosmetic as soon as the background gets
    # coarser than the chosen amplitude. amplitude <= 0 -> use the mean
    # matrix grain radius (capped at a quarter of the band's narrow side);
    # blank freqs -> per-edge wave counts giving a base wavelength of ~8
    # matrix radii plus two higher harmonics for texture. Explicit values
    # still override.
    amp = float(cfg.interface_amplitude or 0.0)
    freqs_cfg = list(cfg.interface_freqs or ()) if cfg.rough_interface else []
    r_mean = None
    if cfg.rough_interface and (amp <= 0.0 or not freqs_cfg):
        psd_r, psd_w = build_psd(cfg.dist_type, cfg.r_min, cfg.r_max, cfg.num_sizes,
                                 cfg.ln_sigma, cfg.ln_median, cfg.nm_mean, cfg.nm_std,
                                 cfg.custom_radii, cfg.custom_weights)
        r_mean = float(np.average(np.asarray(psd_r, float),
                                  weights=np.asarray(psd_w, float)))
    if cfg.rough_interface and amp <= 0.0 and r_mean:
        narrow = min(wx, wz) if min(wx, wz) > 0 else max(wx, wz)
        amp = min(r_mean, 0.25 * narrow) if narrow > 0 else r_mean

    def _edge_freqs(length):
        if freqs_cfg:
            return freqs_cfg
        if not cfg.rough_interface or not r_mean or length <= 0:
            return []
        n1 = min(max(length / (8.0 * r_mean), 1.0), 60.0)
        return [n1, 2.6 * n1, 5.3 * n1]

    def _components(freq_list):
        comps = []
        nf = max(len(freq_list), 1)
        for f in freq_list:
            comps.append((amp * rng.uniform(0.3, 1.0) / nf, f, rng.uniform(0, 2 * np.pi)))
        return comps

    edge_len = {"x_lo": wz, "x_hi": wz, "z_lo": wx, "z_hi": wx}
    edge_comps = {e: _components(_edge_freqs(edge_len.get(e, max(wx, wz))))
                  for e in edge_internal}

    def _taper(t):
        # t in [0,1] along the edge; sin^2 window -> 0 at corners, 1 at centre
        return math.sin(math.pi * t) ** 2

    def edge_offset(edge, coord, lo, hi):
        """Inward displacement (>=0) of an internal edge at parametric position
        'coord' along [lo, hi]. Tapered to 0 at the corners."""
        if not cfg.rough_interface or not edge_internal[edge] or hi <= lo:
            return 0.0
        t = (coord - lo) / (hi - lo)
        raw = sum(a * math.sin(2 * np.pi * f * t + p) for a, f, p in edge_comps[edge])
        return abs(raw) * _taper(t)

    def in_layer(x, z):
        if not (xs <= x <= xe and zs <= z <= ze):
            return False
        if x < xs + edge_offset("x_lo", z, zs, ze):
            return False
        if x > xe - edge_offset("x_hi", z, zs, ze):
            return False
        if z < zs + edge_offset("z_lo", x, xs, xe):
            return False
        if z > ze - edge_offset("z_hi", x, xs, xe):
            return False
        return True

    bbox = (xs, xe, zs, ze)  # nominal rectangle is the hard clip

    return {
        "shape": "rectangle", "polygon": [],
        "xs": xs, "xe": xe, "zs": zs, "ze": ze, "wx": wx, "wz": wz,
        "edge_internal": edge_internal, "edge_offset": edge_offset,
        "in_layer": in_layer, "bbox": bbox, "area": wx * wz,
    }


def build_layer(cfg: Config, seed: int, progress_cb=None, cancel_cb=None):
    rng = np.random.RandomState(seed)

    # 1) coarse background over whole domain
    psd_r, psd_w = build_psd(cfg.dist_type, cfg.r_min, cfg.r_max, cfg.num_sizes,
                             cfg.ln_sigma, cfg.ln_median, cfg.nm_mean, cfg.nm_std,
                             cfg.custom_radii, cfg.custom_weights)
    min_gap, gap_target, k = _mode_params(cfg.throat_mode, cfg.min_throat, cfg.k_candidates)
    pool = _radii_pool(rng, psd_r, psd_w, cfg.target_porosity, cfg.domain_area)
    bg_c, bg_r, _ = rsa_fill(
        rng, pool, bbox=(0, cfg.width, 0, cfg.height),
        region_area=cfg.domain_area, target_porosity=cfg.target_porosity,
        min_gap=min_gap, gap_target=gap_target, k_candidates=k,
        max_attempts=cfg.max_attempts, stall_limit=cfg.stall_limit,
        progress=cfg.n_realizations == 1, desc="[heterogeneity:bg] RSA",
        progress_cb=progress_cb, cancel_cb=cancel_cb,
        snap_enabled=cfg.snap_wall_enabled, snap_thr=cfg.snap_wall_threshold)
    bg_c = np.asarray(bg_c, float); bg_r = np.asarray(bg_r, float)

    # 2) heterogeneity geometry (orientation + start/end + rough interface)
    geo = make_layer_geometry(cfg, seed + 999)
    if (cfg.medium_type == "layer"
            and getattr(cfg, "layer_shape", "rectangle") == "polygon"
            and not geo.get("polygons")):
        raise ValueError("layer_shape='polygon' requires at least three valid x,z vertices.")
    in_layer = geo["in_layer"]

    # 3) carve background grains whose CENTRE falls in the heterogeneity (no halo)
    keep = np.array([not in_layer(cx, cz) for (cx, cz) in bg_c], dtype=bool)
    mat_c, mat_r = bg_c[keep], bg_r[keep]

    # obstacle grid from kept matrix grains
    fine_r, fine_w = build_psd(cfg.layer_dist_type, cfg.layer_r_min, cfg.layer_r_max,
                               cfg.layer_num_sizes, cfg.layer_ln_sigma, cfg.layer_ln_median,
                               cfg.layer_nm_mean, cfg.layer_nm_std)
    max_r_all = float(max(bg_r.max(), fine_r.max()))
    ob_grid = SpatialGrid(max_r_all + min_gap)
    for i, (cx, cz) in enumerate(mat_c):
        ob_grid.insert(i, cx, cz)

    # 4) fill the heterogeneity with its grain population (pure/biased RSA,
    # avoids matrix). The nominal area is used for porosity bookkeeping.
    layer_area = geo["area"]
    fine_pool = _radii_pool(rng, fine_r, fine_w, cfg.layer_porosity, layer_area, oversample=1.8)
    fine_c, fine_rr, _ = rsa_fill(
        rng, fine_pool, bbox=geo["bbox"],
        region_area=layer_area, target_porosity=cfg.layer_porosity,
        min_gap=min_gap, gap_target=gap_target, k_candidates=k,
        max_attempts=cfg.max_attempts, stall_limit=cfg.stall_limit,
        allow_fn=lambda x, z, r: in_layer(x, z),
        obstacles=(list(map(tuple, mat_c)), list(mat_r), ob_grid),
        progress=cfg.n_realizations == 1, desc="[heterogeneity:grains] RSA",
        progress_cb=progress_cb, cancel_cb=cancel_cb,
        snap_enabled=cfg.snap_wall_enabled, snap_thr=cfg.snap_wall_threshold)
    fine_c = np.asarray(fine_c, float); fine_rr = np.asarray(fine_rr, float)

    if len(fine_c):
        centers = np.vstack([mat_c, fine_c])
        radii = np.concatenate([mat_r, fine_rr])
    else:
        centers, radii = mat_c, mat_r
    materials = np.array(["matrix"] * len(mat_r) + ["fine"] * len(fine_rr), dtype=object)
    meta = {
        "phi": porosity(radii, cfg.domain_area),
        "phi_fine": porosity(fine_rr, layer_area) if len(fine_rr) else 1.0,
        "n": len(radii), "n_matrix": len(mat_r), "n_fine": len(fine_rr),
        "n_carved": int((~keep).sum()),
        "layer_x": (geo["xs"], geo["xe"]), "layer_z": (geo["zs"], geo["ze"]),
    }
    return centers, radii, materials, meta


def generate_single(cfg: Config, seed: int, progress_cb=None, cancel_cb=None):
    """Top-level (picklable) single realisation -> dict of arrays + meta.

    progress_cb / cancel_cb are used by the GUI; they are left at None when the
    function is dispatched to a ProcessPoolExecutor (callbacks are not picklable
    and cannot cross the process boundary anyway)."""
    if cfg.medium_type == "homogeneous":
        c, r, m, meta = build_homogeneous(cfg, seed, progress_cb, cancel_cb)
    elif cfg.medium_type == "layer":
        c, r, m, meta = build_layer(cfg, seed, progress_cb, cancel_cb)
    else:
        raise ValueError(f"Unknown medium_type={cfg.medium_type!r}.")
    return {"seed": seed, "centers": c, "radii": r, "materials": m, "meta": meta}


def generate_ensemble(cfg: Config):
    """Run cfg.n_realizations realisations. Parallel across processes if asked.
    NOTE: this parallelises independent realisations, not the (sequential) RSA loop."""
    seeds = [cfg.seed + i for i in range(cfg.n_realizations)]
    if cfg.n_workers > 1 and cfg.n_realizations > 1:
        with ProcessPoolExecutor(max_workers=cfg.n_workers) as ex:
            results = list(ex.map(generate_single, [cfg] * len(seeds), seeds))
    else:
        results = [generate_single(cfg, s) for s in seeds]
    return results


def run_realisations_to_queue(cfg: Config, seeds, q, cancel_event):
    """Run generation + throat analysis for `seeds`, streaming progress and the
    final result over a multiprocessing.Queue `q`. Designed to run in a CHILD
    PROCESS so the heavy, GIL-holding RSA / Delaunay loops do not block a GUI.

    This function is Qt-free and top-level (picklable for spawn). It never
    imports the GUI. Messages put on the queue:
        ('progress', msg, frac, k, n)   live progress (frac<0 == indeterminate)
        ('result', results)             list of realisation dicts (with 'net')
        ('cancelled', None)             cancel_event was set mid-run
        ('error', traceback_str)        unexpected failure
    `cancel_event` is a multiprocessing.Event the parent sets to request a stop.
    """
    try:
        n = len(seeds)
        results = []
        for k, s in enumerate(seeds):
            def pcb(frac, msg, k=k):
                q.put(("progress", msg, frac, k, n))

            def cancel_cb():
                return cancel_event.is_set()

            res = generate_single(cfg, s, progress_cb=pcb, cancel_cb=cancel_cb)

            if cfg.run_throat_analysis:
                q.put(("progress", "[analysis] throat network", -1.0, k, n))
                floor_um = (cfg.min_throat * 1e6) if cfg.throat_mode in ("soft", "hard") else None
                res["net"] = analyze_pore_network(res["centers"], res["radii"],
                                                  cfg.throat_bin_width_um, floor_um=floor_um,
                                                  cancel_cb=cancel_cb)
            else:
                res["net"] = None

            results.append(res)
            if cancel_event.is_set():
                break
        q.put(("result", results))
    except CancelledError:
        q.put(("cancelled", None))
    except Exception:
        import traceback as _tb
        q.put(("error", _tb.format_exc()))


# =============================================================================
# FEASIBILITY / COST WARNINGS
# =============================================================================
def check_feasibility(cfg: Config):
    """Return (estimated_N, [warning strings]); cheap pre-run sanity check."""
    msgs = []
    psd_r, psd_w = build_psd(cfg.dist_type, cfg.r_min, cfg.r_max, cfg.num_sizes,
                             cfg.ln_sigma, cfg.ln_median, cfg.nm_mean, cfg.nm_std,
                             cfg.custom_radii, cfg.custom_weights)
    n_est = estimate_n(psd_r, psd_w, cfg.target_porosity, cfg.domain_area)

    if n_est > cfg.warn_particle_count:
        msgs.append(f"Estimated ~{n_est:,} grains (> {cfg.warn_particle_count:,}). "
                    f"Small grains in a large domain: generation may be slow / memory heavy.")

    work = n_est * max(1, cfg.k_candidates)
    if work > 2_000_000:
        msgs.append(f"Estimated candidate evaluations ~{work:,}. Consider lowering "
                    f"k_candidates or coarsening grains for a quick preview.")

    # crude 2D-RSA jamming heuristic (monodisperse ~0.45 porosity floor; PSD-dependent)
    if cfg.target_porosity < 0.30:
        msgs.append(f"target_porosity={cfg.target_porosity:.2f} is low for 2D RSA. "
                    f"The packing may jam ABOVE target. Achieved value is reported after the run.")

    if cfg.throat_mode == "hard":
        min_d = 2 * cfg.r_min
        if cfg.min_throat > 0.5 * min_d:
            msgs.append(f"Hard min_throat={cfg.min_throat*1e6:.0f} um is large vs smallest "
                        f"grain diameter ({min_d*1e6:.0f} um). Packing may not converge; "
                        f"expect lower achieved porosity.")
        if cfg.target_porosity < 0.40:
            msgs.append("Hard throat floor + low porosity target may be mutually infeasible.")

    if cfg.medium_type == "layer":
        geo = make_layer_geometry(cfg)
        if (cfg.medium_type == "layer"
                and getattr(cfg, "layer_shape", "rectangle") == "polygon"
                and not geo.get("polygons")):
            msgs.append("Polygon heterogeneity has fewer than three valid vertices; draw a closed shape first.")
            return n_est, msgs
        wx, wz = geo["wx"], geo["wz"]
        thickness = min(wx, wz)
        if thickness < 4 * cfg.layer_r_max:
            msgs.append(f"Heterogeneity is only {thickness*1e3:.2f} mm across its narrow axis "
                        f"(a few grain diameters); statistics will be noisy.")
        if geo.get("shape") == "rectangle":
            xs, xe = sorted((cfg.layer_x_start, cfg.layer_x_end))
            zs, ze = sorted((cfg.layer_z_start, cfg.layer_z_end))
            if xs < 0 or xe > cfg.width or zs < 0 or ze > cfg.height:
                msgs.append(f"Heterogeneity rectangle (x {xs*1e3:.1f}-{xe*1e3:.1f}, z {zs*1e3:.1f}-{ze*1e3:.1f} mm) "
                            f"extends outside the domain ({cfg.width*1e3:.1f} x {cfg.height*1e3:.1f} mm); "
                            f"it will be clipped.")

    return n_est, msgs


# =============================================================================
# PORE-NETWORK (THROAT) ANALYSIS  --  produced as a byproduct
# =============================================================================
def _line_hits_circle(p1, p2, c, r):
    d = p2 - p1; f = p1 - c
    a = float(d @ d); b = float(2 * f @ d); cc = float(f @ f - r * r)
    disc = b * b - 4 * a * cc
    if disc < 0:
        return False
    sd = math.sqrt(disc)
    t1 = (-b - sd) / (2 * a); t2 = (-b + sd) / (2 * a)
    return (0 <= t1 <= 1) or (0 <= t2 <= 1) or (t1 < 0 and t2 > 1)


def analyze_pore_network(centers, radii, bin_width_um=25.0, floor_um=None, cancel_cb=None):
    """Delaunay + line-of-sight throat extraction (grid-accelerated blocking).
    Returns dict with throat widths (m), summary stats, and a fixed-um bin table.
    If floor_um is given, also reports how many throats fall below that floor.
    cancel_cb : optional callable()->bool; checked periodically so a GUI 'Stop'
    can interrupt a long analysis (raises CancelledError)."""
    pts = np.asarray(centers, float)
    rad = np.asarray(radii, float)
    n = len(pts)
    if n < 4:
        return None

    tri = Delaunay(pts)
    raw_edges = set()
    for s in tri.simplices:
        for ii in range(3):
            raw_edges.add(tuple(sorted((int(s[ii]), int(s[(ii + 1) % 3])))))

    max_r = float(rad.max())
    grid_cell = 3.0 * 2.0 * float(rad.mean())
    grid = SpatialGrid(grid_cell)
    for i, (x, z) in enumerate(pts):
        grid.insert(i, x, z)

    widths, edges_kept = [], []
    n_far = n_blocked = 0
    for _ei, (i, j) in enumerate(raw_edges):
        if cancel_cb is not None and (_ei & 0x7FF) == 0 and cancel_cb():
            raise CancelledError("analysis")
        p1, p2 = pts[i], pts[j]
        d = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
        w = d - rad[i] - rad[j]
        if w <= 0:
            continue
        # Neighbour cutoff: Delaunay also connects grains across large voids
        # (and along the convex hull); skip those. The gap is judged against
        # the PAIR's own size -- a global mean-radius cutoff collapses when
        # many fine grains dominate (bimodal PSDs) and silently dropped every
        # coarse-coarse throat from the network and the statistics.
        if w > 2.0 * (rad[i] + rad[j]):
            n_far += 1
            continue
        # blocking check only against grains near the segment midpoint
        mid = 0.5 * (p1 + p2)
        reach = 0.5 * d + max_r
        blocked = False
        for k in grid.neighbors(mid[0], mid[1], reach):
            if k == i or k == j:
                continue
            if _line_hits_circle(p1, p2, pts[k], rad[k]):
                blocked = True
                break
        if blocked:
            n_blocked += 1
            continue
        widths.append(w)
        edges_kept.append((i, j))

    w = np.asarray(widths, float)
    if len(w) == 0:
        return None
    w_um = w * 1e6

    # fixed-width bins in micrometres
    bin_end = math.ceil(w_um.max() / bin_width_um) * bin_width_um
    bin_edges = np.arange(0.0, bin_end + bin_width_um, bin_width_um)
    counts, _ = np.histogram(w_um, bins=bin_edges)
    cum = np.cumsum(counts)
    table = [{
        "bin_um": f"{bin_edges[i]:.0f}-{bin_edges[i+1]:.0f}",
        "count": int(counts[i]),
        "percent": 100.0 * counts[i] / len(w),
        "cum_count": int(cum[i]),
        "cum_percent": 100.0 * cum[i] / len(w),
    } for i in range(len(counts))]

    n_below_floor = None
    if floor_um is not None:
        n_below_floor = int(np.sum(w_um < floor_um))

    return {
        "widths_m": w, "edges": edges_kept,
        "n_throats": len(w), "n_far": n_far, "n_blocked": n_blocked, "n_raw": len(raw_edges),
        "min_um": float(w_um.min()), "max_um": float(w_um.max()),
        "mean_um": float(w_um.mean()), "median_um": float(np.median(w_um)),
        "std_um": float(w_um.std()), "p05_um": float(np.percentile(w_um, 5)),
        "bin_edges": bin_edges, "bin_counts": counts, "bin_table": table,
        "bin_width_um": bin_width_um,
        "floor_um": floor_um, "n_below_floor": n_below_floor,
    }


def throat_floor_status(cfg, net):
    """Return a one-line, mode-aware statement about the requested minimum throat
    vs what was achieved (Delaunay throats). None when not applicable.

    - hard mode: a real pass/fail against the floor (a breach is usually export
      rounding or grains packing right down to the limit).
    - soft mode: reports throats below the target while making clear it was never
      a guaranteed floor.
    - none mode: nothing to report."""
    if net is None or cfg.throat_mode == "none":
        return None
    target = cfg.min_throat * 1e6
    minw = net["min_um"]
    nbelow = net.get("n_below_floor")
    total = int(net.get("n_throats") or 0)

    def _share(n):
        return f"{n} of {total} throat(s), {100.0 * n / total:.1f}%" if total else f"{n} throat(s)"

    if cfg.throat_mode == "hard":
        if minw >= target - 1e-6:
            return f"Hard throat floor met: min throat {minw:.1f} µm ≥ requested {target:.0f} µm."
        return (f"Hard throat floor NOT strictly met: min throat {minw:.1f} µm < requested "
                f"{target:.0f} µm - {_share(nbelow)} below floor "
                f"(export rounding / grains packed to the limit).")
    # soft
    if nbelow:
        return (f"Soft throat target: {_share(nbelow)} below {target:.0f} µm "
                f"(soft target, not a guaranteed floor; min {minw:.1f} µm).")
    return f"Soft throat target: all {total} throats ≥ {target:.0f} µm (min {minw:.1f} µm)."


# =============================================================================
# OUTPUT WRITERS
# =============================================================================
def write_geometry(cfg: Config, centers, radii, path_stem):
    """Write x y z value (value = radius or diameter). Plane is X-Z, Y fixed."""
    val = (2 * radii) if cfg.use_diameter else radii
    label = "diameter" if cfg.use_diameter else "radius"
    x = centers[:, 0] + cfg.x_origin
    z = centers[:, 1] + cfg.z_origin
    y = np.full(len(radii), cfg.y_fixed)

    if cfg.output_format in ("dat", "csv"):
        path = f"{path_stem}.{cfg.output_format}"
        sep = "," if cfg.output_format == "csv" else " "
        with open(path, "w") as f:
            f.write(sep.join(["x", "y", "z", label]) + "\n")
            for xi, yi, zi, vi in zip(x, y, z, val):
                f.write(sep.join(f"{q:.7f}" for q in (xi, yi, zi, vi)) + "\n")
        return path

    if cfg.output_format == "cin":
        return _write_cin(f"{path_stem}.cin", x, y, z, radii)

    raise ValueError(f"Unknown output_format={cfg.output_format!r}.")


def _write_cin(path, x, y, z, radii, decimals=7):
    fxyz = f"{{:.{decimals}f}} {{:.{decimals}f}} {{:.{decimals}f}}"
    fr = f"{{:.{decimals}f}}"
    with open(path, "w") as f:
        f.write("========== IB Characteristics ===========================\n")
        f.write("5 Delta f.(1-phi1*,2-phi2*,3-phi3*,4-phi4*,5-phi3,6-phi4\n")
        f.write("0 Multi-Direct Forcing steps\n")
        f.write(f"{len(radii)} nIBMs(# of Immersed Bs)\n")
        for idx, (xi, yi, zi, ri) in enumerate(zip(x, y, z, radii), 1):
            f.write(f"========= IB #{idx} ============\n")
            f.write("4 Shape: 1=Square,2=Cylinder,3=Cube,4=Sphere,5-File,6-PipeFlow\n")
            f.write("0 0.0 1.907 Length:1-Infinite; 0-finite,zini,zend\n")
            f.write(f"{fxyz.format(xi, yi, zi)} Centre:Cx,Cy,Cz\n")
            f.write(f"{fr.format(ri)} Radius\n")
            f.write("1 cmax: Number of marker layers\n")
            f.write("-1 Axis of extrusion (1-x, 2-y, 3-z, -1-none)\n")
            f.write("'chamber_ductfinal.txt' File from where load the points.\n")
            f.write("F Movement. F=steady; T= Rotating\n")
            f.write("F Flow type (F:ext,T:int(pipe flow))\n")
            f.write("1.00 1 Reduction factor,refinement\n")
    return path


def write_throat_csv(net, path):
    with open(path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["bin_um", "count", "percent", "cum_count", "cum_percent"])
        wr.writeheader()
        wr.writerows(net["bin_table"])
    return path


# =============================================================================
# OPTIONAL PLOTTING
# =============================================================================
def _draw_layer_overview(ax, cfg):
    """Draw the layer patch onto a given axis."""
    from matplotlib.patches import Polygon, Rectangle

    geo = make_layer_geometry(cfg)
    xs, xe, zs, ze = geo["xs"], geo["xe"], geo["zs"], geo["ze"]
    ei, eo = geo["edge_internal"], geo["edge_offset"]

    ax.add_patch(Rectangle((0, 0), cfg.width, cfg.height, fill=False, edgecolor="red", lw=2))

    if geo.get("shape") in ("polygon", "multipolygon"):
        polygons = geo.get("polygons", [])
        if geo.get("shape") == "polygon":
            polygons = [geo.get("polygon", [])]
        for idx, poly in enumerate(polygons):
            if not poly:
                continue
            ax.add_patch(Polygon(poly, closed=True, fill=True, facecolor="#2dd4bf",
                                 alpha=0.18, edgecolor="#0f766e", lw=1.6,
                                 label=("heterogeneity polygon" if idx == 0 else None)))
            px = [p[0] for p in poly] + [poly[0][0]]
            pz = [p[1] for p in poly] + [poly[0][1]]
            ax.plot(px, pz, color="#0f766e", lw=1.1)
            ax.scatter([p[0] for p in poly], [p[1] for p in poly],
                       s=16, color="#0f766e", zorder=3)
        for cx, cz in geo.get("centers", []):
            ax.scatter([cx], [cz], s=34, marker="P", color="#f97316",
                       edgecolor="white", linewidth=0.6, zorder=4)
        frac = 100.0 * geo["area"] / cfg.domain_area if cfg.domain_area else 0.0
        if geo.get("shape") == "multipolygon":
            ax.set_title(f"Heterogeneity polygons | {len(polygons)} polygons | "
                         f"{geo['area']*1e6:.1f} mm^2 | {frac:.0f}% of domain",
                         fontsize=9)
        else:
            poly = geo.get("polygon", [])
            ax.set_title(f"Heterogeneity polygon | {len(poly)} vertices | "
                         f"{geo['area']*1e6:.1f} mm^2 | {frac:.0f}% of domain",
                         fontsize=9)
    else:
        ax.add_patch(Rectangle((xs, zs), xe - xs, ze - zs, fill=True, facecolor="#4682B4",
                               alpha=0.15, edgecolor="gray", ls=":", lw=1.0,
                               label="nominal rectangle"))

        labeled = False

        def _edge(curve_x, curve_z):
            nonlocal labeled
            ax.plot(curve_x, curve_z, color="navy", lw=1.3,
                    label=("rough interface" if not labeled else None))
            labeled = True

        if ei["x_lo"]:
            z = np.linspace(zs, ze, 300); _edge([xs + eo("x_lo", zi, zs, ze) for zi in z], z)
        if ei["x_hi"]:
            z = np.linspace(zs, ze, 300); _edge([xe - eo("x_hi", zi, zs, ze) for zi in z], z)
        if ei["z_lo"]:
            x = np.linspace(xs, xe, 300); _edge(x, [zs + eo("z_lo", xi, xs, xe) for xi in x])
        if ei["z_hi"]:
            x = np.linspace(xs, xe, 300); _edge(x, [ze - eo("z_hi", xi, xs, xe) for xi in x])

        frac = 100.0 * geo["area"] / cfg.domain_area if cfg.domain_area else 0.0
        n_int = sum(ei.values())
        ax.set_title(f"Heterogeneity patch | {geo['wx']*1e3:.1f} x {geo['wz']*1e3:.1f} mm | "
                     f"{frac:.0f}% of domain | {n_int} rough edge(s)", fontsize=9)

    ax.set_xlim(-0.002, cfg.width + 0.002)
    ax.set_ylim(cfg.height + 0.002, -0.002)
    ax.set_aspect("equal")
    ax.set_xlabel("X [m]", fontsize=8); ax.set_ylabel("Z [m]", fontsize=8)
    ax.tick_params(labelsize=7)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(fontsize=6.5, loc="upper right")


def make_layer_overview_figure(cfg):
    """Standalone layer overview figure (CLI / single use)."""
    from matplotlib.figure import Figure
    aspect = cfg.width / cfg.height
    fig = Figure(figsize=(5.0, max(2.2, 5.0 / max(aspect, 0.4))))
    _draw_layer_overview(fig.subplots(1, 1), cfg)
    fig.tight_layout(pad=0.6)
    return fig


def make_preview_figure(cfg, fig=None):
    """Combined live preview for the GUI's Preview tab.
      - homogeneous: just the matrix sampled PSD.
      - heterogeneity: matrix PSD and heterogeneity PSD side by side on top,
        heterogeneity overview below.
    If `fig` is given it is cleared and reused (persistent-canvas pattern);
    otherwise a new Figure is created. No packing is performed (cheap, live-safe)."""
    matrix_diag = psd_diagnostics(cfg.dist_type, cfg.r_min, cfg.r_max, cfg.num_sizes,
                                  cfg.ln_sigma, cfg.ln_median, cfg.nm_mean, cfg.nm_std,
                                  cfg.custom_radii, cfg.custom_weights)
    if fig is None:
        from matplotlib.figure import Figure
        fig = Figure()
    else:
        fig.clear()

    if cfg.medium_type != "layer":
        _draw_psd(fig.add_subplot(1, 1, 1), matrix_diag, "Matrix PSD (sampled)", "#D2B48C")
        fig.subplots_adjust(left=0.10, right=0.97, top=0.90, bottom=0.16)
        return fig

    fine_diag = psd_diagnostics(cfg.layer_dist_type, cfg.layer_r_min, cfg.layer_r_max,
                                cfg.layer_num_sizes, cfg.layer_ln_sigma, cfg.layer_ln_median,
                                cfg.layer_nm_mean, cfg.layer_nm_std)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.7], hspace=0.5, wspace=0.28,
                          left=0.09, right=0.97, top=0.94, bottom=0.07)
    _draw_psd(fig.add_subplot(gs[0, 0]), matrix_diag, "Matrix PSD (sampled)", "#D2B48C")
    _draw_psd(fig.add_subplot(gs[0, 1]), fine_diag, "Heterogeneity grains PSD (sampled)", "#4682B4")
    _draw_layer_overview(fig.add_subplot(gs[1, :]), cfg)
    return fig




def _draw_psd(ax, diag, title, color):
    """Draw one sampled-PSD panel: bars = bin weights RSA will sample, line =
    continuous target shape (scaled to the bars, shape comparison only)."""
    r = np.asarray(diag["radii"], float)
    w = np.asarray(diag["weights"], float)
    d_mm = 2.0 * r * 1e3
    if len(d_mm) > 1:
        bw = 0.8 * float(np.min(np.diff(np.sort(d_mm))))
    else:
        bw = 0.1
    ax.bar(d_mm, w, width=bw, color=color, edgecolor="black", lw=0.3, alpha=0.85,
           label="sampled")
    if diag["curve_r"] is not None and w.max() > 0:
        cd = 2.0 * np.asarray(diag["curve_r"]) * 1e3
        cp = np.asarray(diag["curve_pdf"], float)
        cp = cp / cp.max() * w.max() if cp.max() > 0 else cp
        ax.plot(cd, cp, color="darkred", lw=1.4, label="target (scaled)")
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("grain diameter [mm]", fontsize=8)
    ax.set_ylabel("weight", fontsize=8)
    ax.tick_params(labelsize=7)

    cov, mx = diag["coverage"], diag["max_weight"]
    flags = []
    if cov < 0.50:
        flags.append(f"coverage {cov * 100:.0f}% (range clips the distribution)")
    if mx > 0.80:
        flags.append(f"~monodisperse ({mx * 100:.0f}% one size)")
    warn = bool(flags)
    txt = "  ".join(flags) if flags else f"coverage {cov * 100:.0f}%"
    ax.text(0.98, 0.96, txt, transform=ax.transAxes, ha="right", va="top",
            fontsize=7.5, color=("darkred" if warn else "gray"))
    ax.legend(fontsize=6.5, loc="upper left")


def make_psd_preview_figure(cfg):
    """Compact live preview of the SAMPLED grain-size distribution(s) for the
    current config. One panel for homogeneous; a second heterogeneity panel when
    applicable. No packing is performed (cheap, safe to call live)."""
    from matplotlib.figure import Figure
    is_heterogeneous = cfg.medium_type == "layer"
    fig = Figure(figsize=(5.2, 3.4 if is_heterogeneous else 1.9))
    axes = fig.subplots(2, 1) if is_heterogeneous else [fig.subplots(1, 1)]

    _draw_psd(axes[0],
              psd_diagnostics(cfg.dist_type, cfg.r_min, cfg.r_max, cfg.num_sizes,
                              cfg.ln_sigma, cfg.ln_median, cfg.nm_mean, cfg.nm_std,
                              cfg.custom_radii, cfg.custom_weights),
              "Matrix PSD (sampled)", "#D2B48C")
    if is_heterogeneous:
        _draw_psd(axes[1],
                  psd_diagnostics(cfg.layer_dist_type, cfg.layer_r_min, cfg.layer_r_max,
                                  cfg.layer_num_sizes, cfg.layer_ln_sigma, cfg.layer_ln_median,
                                  cfg.layer_nm_mean, cfg.layer_nm_std),
                  "Heterogeneity grains PSD (sampled)", "#4682B4")
    fig.tight_layout(pad=0.6)
    return fig


def make_overview_figure(cfg, res, net, fig=None):
    """Return a matplotlib Figure (OO API, no pyplot, embeddable):
      - geometry with throat-network overlay,
      - throat-width histogram,
      - realised grain-size distribution (split by material for layer media).
    If `fig` is given it is cleared and reused; otherwise a new Figure is made."""
    from matplotlib.patches import Circle
    from matplotlib.collections import LineCollection

    c, r, m = res["centers"], res["radii"], res["materials"]
    if fig is None:
        from matplotlib.figure import Figure
        fig = Figure(figsize=(15, 8))
    else:
        fig.clear()
    # Constrained layout sizes the panels around titles, axis labels and
    # legends, so the stacked right-hand plots never collide (the throat
    # x-label used to run into the grain-size title on small canvases).
    try:
        fig.set_layout_engine("constrained")
    except Exception:
        pass  # very old matplotlib: keep default layout
    gs = fig.add_gridspec(2, 2, width_ratios=[1.5, 1.0])
    axg = fig.add_subplot(gs[:, 0])   # geometry spans both rows
    axt = fig.add_subplot(gs[0, 1])   # throat-width histogram
    axd = fig.add_subplot(gs[1, 1])   # grain-size distribution

    # --- geometry ---
    cols = np.where(m == "fine", "#4682B4", "#D2B48C")
    # Edge width scales with grain radius: a fixed hairline (0.2 pt) is
    # sub-pixel at typical render DPI, so large grains looked borderless and
    # touching ones merged visually, while dense fine packs already read as
    # outlined. Scaling keeps fine regions light and big grains crisp.
    r_max_draw = float(np.max(r)) if len(r) else 1.0
    for (x, z), rr, col in zip(c, r, cols):
        lw = 0.25 + 0.55 * (float(rr) / r_max_draw)
        axg.add_patch(Circle((x, z), rr, facecolor=col, edgecolor="black", lw=lw))
    if net:
        lines = [[(c[i, 0], c[i, 1]), (c[j, 0], c[j, 1])] for (i, j) in net["edges"]]
        axg.add_collection(LineCollection(lines, colors="#D2691E", lw=0.5, alpha=0.5))
    axg.set_xlim(0, cfg.width); axg.set_ylim(cfg.height, 0); axg.set_aspect("equal")
    meta = res["meta"]
    if cfg.medium_type == "layer" and "phi_fine" in meta:
        title = (f"heterogeneity\n"
                 f"N={meta['n']} | total phi={meta['phi']:.3f}\n"
                 f"heterogeneity phi={meta['phi_fine']:.3f}")
    else:
        title = f"{cfg.medium_type}\nN={meta['n']} | phi={meta['phi']:.3f}"
    axg.set_title(title, pad=10)
    axg.set_xlabel("X [m]"); axg.set_ylabel("Z [m]")

    # --- throat-width histogram ---
    if net:
        axt.bar(0.5 * (net["bin_edges"][:-1] + net["bin_edges"][1:]), net["bin_counts"],
                width=net["bin_width_um"] * 0.85, color="#D2691E", edgecolor="black", alpha=0.75)
        axt.axvline(net["mean_um"], color="darkred", lw=2, label=f"mean {net['mean_um']:.0f} µm")
        axt.axvline(net["median_um"], color="darkblue", ls="--", lw=2,
                    label=f"median {net['median_um']:.0f} µm")
        # bin width lives in the x-label: a long centred title overflows the
        # figure edge on narrow canvases
        axt.set_xlabel(f"Throat width [µm]  ({net['bin_width_um']:.0f} µm bins)")
        axt.set_ylabel("Count")
        axt.set_title("Throat-width distribution")
        # reserve headroom so the legend never sits on the bars
        top = float(np.max(net["bin_counts"])) if len(net["bin_counts"]) else 1.0
        axt.set_ylim(0, top * 1.32)
        axt.legend(fontsize=8, loc="upper right", framealpha=0.9)
    else:
        axt.text(0.5, 0.5, "throat analysis off", ha="center", va="center", transform=axt.transAxes)
        axt.set_xticks([]); axt.set_yticks([])

    # --- realised grain-size distribution (diameter) ---
    d_mm = 2.0 * np.asarray(r) * 1e3
    matrix_d = d_mm[m == "matrix"]
    fine_d = d_mm[m == "fine"]
    peak = 1.0
    if len(matrix_d):
        counts_m, _, _ = axd.hist(matrix_d, bins=24, color="#D2B48C", edgecolor="black",
                                  lw=0.3, alpha=0.85, label=f"matrix (n={len(matrix_d)})")
        peak = max(peak, float(np.max(counts_m)))
    if len(fine_d):
        counts_f, _, _ = axd.hist(fine_d, bins=20, color="#4682B4", edgecolor="black",
                                  lw=0.3, alpha=0.85,
                                  label=f"heterogeneity grains (n={len(fine_d)})")
        peak = max(peak, float(np.max(counts_f)))
    axd.set_xlabel("Grain diameter [mm]"); axd.set_ylabel("Count")
    axd.set_title("Grain-size distribution")
    # reserve headroom so the legend never overlaps the tallest bars
    axd.set_ylim(0, peak * (1.18 + 0.09 * (bool(len(matrix_d)) + bool(len(fine_d)))))
    axd.legend(fontsize=8, loc="upper right", framealpha=0.9)
    return fig


def plot_results(cfg, res, net, path_stem):
    """CLI path: render the overview figure to a PNG file."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = make_overview_figure(cfg, res, net)
    FigureCanvasAgg(fig)  # attach a backend so savefig works without pyplot
    out = f"{path_stem}_overview.png"
    fig.savefig(out, dpi=200, bbox_inches="tight")
    return out


# =============================================================================
# MAIN
# =============================================================================
def main(cfg: Config = CONFIG):
    os.makedirs(cfg.out_dir, exist_ok=True)

    print("=" * 72)
    print(f"RSA 2D POROUS MEDIA  |  {cfg.medium_type}  |  {cfg.width*1e3:.0f} x {cfg.height*1e3:.0f} mm")
    print(f"distribution: {cfg.dist_type}  r=[{cfg.r_min*1e6:.0f}, {cfg.r_max*1e6:.0f}] um")
    print(f"target porosity: {cfg.target_porosity}  |  throat mode: {cfg.throat_mode} "
          f"({cfg.min_throat*1e6:.0f} um)  |  K={cfg.k_candidates}")
    print("=" * 72)

    n_est, warns = check_feasibility(cfg)
    print(f"Estimated grains: ~{n_est:,}")
    if warns:
        print("\nWARNINGS:")
        for w in warns:
            print(f"  - {w}")
        if cfg.strict_feasibility:
            raise RuntimeError("strict_feasibility=True and warnings present; aborting.")
    print()

    results = generate_ensemble(cfg)

    for k, res in enumerate(results):
        suffix = "" if cfg.n_realizations == 1 else f"_r{k:02d}"
        stem = os.path.join(cfg.out_dir, cfg.out_basename + suffix)
        meta = res["meta"]
        print(f"[realisation {k}] seed={res['seed']}  N={meta['n']}  "
              f"phi_achieved={meta['phi']:.4f}  (target {cfg.target_porosity})")
        if meta["phi"] > cfg.target_porosity + 0.01:
            print(f"           note: packing jammed above target by "
                  f"{(meta['phi']-cfg.target_porosity):.3f} porosity.")

        gpath = write_geometry(cfg, res["centers"], res["radii"], stem)
        print(f"           geometry -> {gpath}")

        net = None
        if cfg.run_throat_analysis:
            floor_um = (cfg.min_throat * 1e6) if cfg.throat_mode in ("soft", "hard") else None
            net = analyze_pore_network(res["centers"], res["radii"],
                                       cfg.throat_bin_width_um, floor_um=floor_um)
            if net:
                cpath = write_throat_csv(net, stem + "_throats.csv")
                print(f"           throats  -> {cpath}  "
                      f"(n={net['n_throats']}, min={net['min_um']:.1f}, "
                      f"median={net['median_um']:.1f}, mean={net['mean_um']:.1f} um)")
                status = throat_floor_status(cfg, net)
                if status:
                    print(f"           {status}")

        if cfg.make_plots:
            ppath = plot_results(cfg, res, net, stem)
            print(f"           plot     -> {ppath}")

    print("\nDONE.")
    return results


if __name__ == "__main__":
    main()
