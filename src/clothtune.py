"""
clothtune - close the measurement loop: self-calibrating settle and recipe.

The pipeline philosophy is specify -> solve -> MEASURE -> accept/reject -> adjust.
clothdiag does the measuring; this module does the ADJUSTING automatically, so the
hand-tuned magic numbers (per-fabric settle budgets, distance_min nudges) stop
being hand-tuned.

  auto_settle  - bake the drape once to a generous max, then SCAN the cached
                 frames for the convergence frame F (earliest frame whose max
                 vertex speed stays under a threshold). Replaces fixed settle
                 counts; never over- or under-bakes.
  auto_tune    - a bounded search: read the gate's failing metric and nudge the
                 ONE recipe param that fixes it, re-solve, repeat until the
                 baseline gate PASSes (or give up with the history).
  motion_gate  - run clothdiag.measure + evaluate on a baked CLIP, so an
                 animation (not just the rest state) gets an objective verdict.
"""
import copy
import bpy
import numpy as np
import utils as U
import cloth
import clothdiag


# ---------------------------------------------------------------------------
# auto-settle
# ---------------------------------------------------------------------------
def _base_at(obj, f):
    bpy.context.scene.frame_set(f)
    return cloth._cloth_base_positions(obj)


def scan_convergence(obj, frames, fps=25, thresh=0.03, hold=5):
    """Earliest frame whose max per-vertex speed stays < thresh for `hold`
    consecutive frames (i.e. the drape has actually stopped moving)."""
    prev = _base_at(obj, 1)
    run = 0
    for f in range(2, frames + 1):
        cur = _base_at(obj, f)
        speed = float(np.abs(cur - prev).max()) * fps
        prev = cur
        if speed < thresh:
            run += 1
            if run >= hold:
                return f
        else:
            run = 0
    return frames        # never settled within the budget


def auto_settle(spec, fabric=cloth.FABRIC, fps=25, max_settle=170,
                thresh=0.02, hold=6):
    """Bake the drape once to max_settle, find the convergence frame F, and
    return (F, baseline_report_at_F). F is the settle this garment+fabric
    actually needs - use it instead of a guessed constant."""
    U.clear_scene()
    col = U.get_collection(cloth.COL)
    skirt, _w = cloth.build_skirt(spec, col)
    cloth.lower_body(spec, col)
    cl = cloth.add_cloth(skirt, "Waist", fabric)
    cloth.bake(skirt, cl, max_settle)
    F = scan_convergence(skirt, max_settle, fps=fps, thresh=thresh, hold=hold)
    rep = clothdiag.baseline(skirt, F, fps=fps)
    return F, rep


# ---------------------------------------------------------------------------
# auto-tune
# ---------------------------------------------------------------------------
def _adjust(fabric, rep):
    """Apply the metric->param map from clothdiag.baseline_verdict's advice to the
    SINGLE dominant failing metric. Returns (new_fabric, what_changed) or
    (None, reason) if nothing actionable."""
    env = clothdiag.BASELINE_ENVELOPE
    f = copy.deepcopy(fabric)
    pen_b = env["settled_penetration_m"][1]
    str_b = env["rest_max_stretch"][1]
    cmp_b = env["rest_min_compression"][1]
    # penetration is the most common / most actionable failure: the fabric is
    # resting inside the body -> widen the collision margin (and shed a little
    # mass, since heavy cloth sags through).
    if rep["settled_penetration_m"] > pen_b:
        f["distance_min"] = round(min(0.05, f["distance_min"] * 1.5), 4)
        f["mass"] = round(max(0.03, f["mass"] * 0.9), 4)
        return f, f"distance_min->{f['distance_min']}, mass->{f['mass']}"
    if rep["rest_max_stretch"] > str_b:
        f["tension"] = int(round(f["tension"] * 1.35))
        f["compression"] = int(round(f["compression"] * 1.35))
        return f, f"tension/compression->{f['tension']}"
    if rep["rest_min_compression"] < cmp_b:
        f["bending"] = round(f["bending"] * 1.4, 3)
        return f, f"bending->{f['bending']}"
    return None, "no actionable param for the failing metric"


def auto_tune(spec, fabric0=cloth.FABRIC, max_iters=5, fps=25, max_settle=150,
              settle=None):
    """Search for a recipe that passes the baseline gate on `spec`. Each iter
    measures the candidate, and if it fails nudges the param that fixes the
    dominant failure. By default each candidate is auto-settled; pass `settle` to
    force a fixed settle instead (useful when a failure only manifests after a
    known number of frames). Returns (fabric, F, report, ok, history)."""
    fabric = copy.deepcopy(fabric0)
    history = []
    F, rep, ok = None, None, False
    change = "(start)"              # what was applied going INTO the current iter
    for it in range(max_iters):
        if settle is None:
            F, rep = auto_settle(spec, fabric, fps=fps, max_settle=max_settle)
        else:
            F = settle
            rep = cloth.drape_baseline(spec, settle=settle, fabric=fabric)
        ok, _ = clothdiag.baseline_verdict(rep)
        history.append(dict(iter=it, fabric=copy.deepcopy(fabric), settle=F,
                            pen_mm=round(rep["settled_penetration_m"] * 1000, 1),
                            stretch=rep["rest_max_stretch"],
                            compr=rep["rest_min_compression"],
                            settle_speed=rep["settle_speed_m_s"], passed=ok,
                            change=change))
        if ok:
            break
        nf, change = _adjust(fabric, rep)
        if nf is None:
            break
        fabric = nf
    return fabric, F, rep, ok, history


# ---------------------------------------------------------------------------
# motion gate
# ---------------------------------------------------------------------------
def motion_gate(cloth_obj, frames, fps=25, colliders=None, looping=True):
    """Objective PASS/FAIL for a baked CLIP (not just the rest state): runs the
    full dynamic metrics and checks them against clothdiag's dynamic + loop
    envelopes. Returns (ok, report_text, report)."""
    report = clothdiag.measure(cloth_obj, frames, fps=fps,
                               collider_objs=colliders, looping=looping)
    ok, text = clothdiag.report_text(report, looping=looping)
    return ok, text, report
