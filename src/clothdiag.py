"""
Cloth diagnostics - the measurement layer.

Turns a baked cloth simulation into OBJECTIVE numbers and an accept/reject
verdict against acceptance envelopes, so quality is judged by metrics rather
than by eyeballing a render. Runs headless, no rendering required.

Pipeline role:  specify -> solve -> MEASURE -> accept/reject -> adjust -> solve

Metrics (all derived from the evaluated geometry over the cached frames):
  max_stretch / min_compression  - edge length vs rest (fabric should barely stretch)
  max_penetration_m              - how far cloth verts sit inside a collider
  max_vertex_speed / accel       - instability / jitter / explosion guard
  max_hem_speed                  - liveliness of the hem
  loop_pos/vel_discont           - seam continuity for looping clips
"""
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


DEFAULT_ENVELOPE = {
    "max_stretch":          ("<=", 1.6),    # low-stretch woven fabric
    "min_compression":      (">=", 0.40),
    "max_penetration_m":    ("<=", 0.012),  # 12 mm
    "max_vertex_speed_m_s": ("<=", 30.0),   # explosion guard
    "exploded":             ("==", False),
}
LOOP_ENVELOPE = {
    "loop_pos_discont_m":   ("<=", 0.02),
    "loop_vel_discont_m_s": ("<=", 0.60),
}


BASELINE_ENVELOPE = {
    "settled_penetration_m": ("<=", 0.012),   # cloth must rest OUTSIDE the body
    "rest_max_stretch":      ("<=", 1.30),    # relaxed drape barely stretches
    "rest_min_compression":  (">=", 0.55),
    "settle_speed_m_s":      ("<=", 0.05),     # actually reached equilibrium
}


def colliders():
    """All collision meshes in the scene."""
    return [o for o in bpy.context.scene.objects
            if o.type == 'MESH' and any(m.type == 'COLLISION' for m in o.modifiers)]


def _eval_positions(obj):
    """Returns (world_positions, edges, face_edge_mask).

    face_edge_mask marks edges used by at least one face. Loose edges - e.g. the
    sewing springs in a sewn garment - are False, so stretch/compression can skip
    them (a seam spring's rest->final length ratio is nonsense for the fabric)."""
    dg = bpy.context.evaluated_depsgraph_get()
    eo = obj.evaluated_get(dg)
    me = eo.to_mesh()
    try:
        n = len(me.vertices)
        xyz = np.empty(n * 3)
        me.vertices.foreach_get('co', xyz)
        xyz = xyz.reshape(n, 3)
        mw = np.array(eo.matrix_world)
        world = xyz @ mw[:3, :3].T + mw[:3, 3]
        edges = np.array([(e.vertices[0], e.vertices[1]) for e in me.edges])
        face_keys = set()
        for p in me.polygons:
            face_keys.update(p.edge_keys)     # (min,max) vertex-index tuples
        if len(edges):
            mask = np.array([(min(a, b), max(a, b)) in face_keys for a, b in edges], dtype=bool)
        else:
            mask = np.zeros(0, dtype=bool)
        return world, edges, mask
    finally:
        eo.to_mesh_clear()


_RAY_DIRS = [Vector(v).normalized() for v in (
    (0.577, 0.577, 0.577), (-0.577, 0.577, -0.577), (0.577, -0.577, -0.577),
    (-0.577, -0.577, 0.577), (0.0, 0.0, -1.0))]


def _inside(bvh, point, directions=_RAY_DIRS):
    """Inside test by MAJORITY VOTE over several ray directions: a ray crosses a
    closed mesh an odd number of times iff the point is inside. A single ray can be
    fooled when it grazes a sharp feature (a T-pose arm, a mesh cavity), so we vote
    over several directions - robust on imperfect anatomical colliders."""
    votes = 0
    for d in directions:
        origin = point.copy()
        crossings = 0
        for _ in range(16):
            loc, nrm, idx, dist = bvh.ray_cast(origin, d)
            if loc is None:
                break
            crossings += 1
            origin = loc + d * 1e-4
        votes += (crossings % 2 == 1)
    return votes * 2 > len(directions)


def _collider_bvh(objs):
    verts, faces, off = [], [], 0
    dg = bpy.context.evaluated_depsgraph_get()
    for o in objs:
        eo = o.evaluated_get(dg)
        me = eo.to_mesh()
        try:
            mw = eo.matrix_world
            verts += [mw @ v.co for v in me.vertices]
            faces += [tuple(i + off for i in p.vertices) for p in me.polygons]
            off += len(me.vertices)
        finally:
            eo.to_mesh_clear()
    return BVHTree.FromPolygons(verts, faces) if faces else None


def measure(cloth, frames, fps=25, collider_objs=None, looping=False,
            pen_check_every=2):
    """Step the cache, capture geometry, return a metrics dict."""
    scene = bpy.context.scene
    if collider_objs is None:
        collider_objs = colliders()

    scene.frame_set(1)
    rest, edges, emask = _eval_positions(cloth)
    nv = len(rest)
    if emask.any():                    # ignore loose sewing edges in stretch
        edges = edges[emask]
    rest_len = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
    hem_mask = rest[:, 2] < (rest[:, 2].min() + 0.03)

    P = np.empty((frames, nv, 3))
    max_stretch, min_comp, max_pen, max_pen_frame = 1.0, 1.0, 0.0, 0
    for fi in range(frames):
        scene.frame_set(fi + 1)
        pos, _e, _m = _eval_positions(cloth)
        if len(pos) != nv:
            raise RuntimeError("cloth vertex count changed between frames")
        P[fi] = pos
        L = np.linalg.norm(pos[edges[:, 0]] - pos[edges[:, 1]], axis=1)
        ratio = L / np.maximum(rest_len, 1e-9)
        max_stretch = max(max_stretch, float(ratio.max()))
        min_comp = min(min_comp, float(ratio.min()))
        if collider_objs and fi % pen_check_every == 0:
            bvh = _collider_bvh(collider_objs)
            if bvh is not None:
                for v in pos:
                    vv = Vector(v)
                    loc, nrm, _idx, dist = bvh.find_nearest(vv)
                    # only ray-test the cheap subset that's near a collider
                    if loc is not None and dist < 0.06 and _inside(bvh, vv):
                        if dist > max_pen:
                            max_pen, max_pen_frame = float(dist), fi + 1

    V = (P[1:] - P[:-1]) * fps
    A = (P[2:] - 2 * P[1:-1] + P[:-2]) * (fps * fps)
    report = {
        "vertices": int(nv),
        "frames": int(frames),
        "max_stretch": round(max_stretch, 4),
        "min_compression": round(min_comp, 4),
        "max_penetration_m": round(max_pen, 5),
        "max_penetration_frame": int(max_pen_frame),
        "max_vertex_speed_m_s": round(float(np.linalg.norm(V, axis=2).max()) if len(V) else 0.0, 3),
        "max_vertex_accel_m_s2": round(float(np.linalg.norm(A, axis=2).max()) if len(A) else 0.0, 2),
        "max_hem_speed_m_s": round(float(np.linalg.norm(V[:, hem_mask], axis=2).max()) if len(V) else 0.0, 3),
        "exploded": bool((not np.isfinite(P).all()) or float(np.abs(P).max()) > 100),
    }
    if looping:
        report["loop_pos_discont_m"] = round(float(np.abs(P[-1] - P[0]).max()), 5)
        report["loop_vel_discont_m_s"] = round(float(np.abs(V[-1] - V[0]).max()) if len(V) > 1 else 0.0, 4)
    scene.frame_set(1)
    return report


def evaluate(report, looping=False):
    env = dict(DEFAULT_ENVELOPE)
    if looping:
        env.update(LOOP_ENVELOPE)
    ops = {"<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
           "<": lambda a, b: a < b, ">": lambda a, b: a > b,
           "==": lambda a, b: a == b}
    checks, ok = [], True
    for key, (op, bound) in env.items():
        val = report.get(key)
        passed = ops[op](val, bound)
        ok = ok and passed
        checks.append((key, val, op, bound, passed))
    return ok, checks


def baseline(cloth, settle_frames, fps=25, collider_objs=None):
    """Measure the SETTLED rest drape - the foundation every animation builds on.

    Assumes the garment has been draped (gravity-only settle) and baked over
    `settle_frames`. Reads penetration, rest stretch/compression, and whether it
    actually reached equilibrium."""
    scene = bpy.context.scene
    if collider_objs is None:
        collider_objs = colliders()
    scene.frame_set(1)
    pattern, edges, emask = _eval_positions(cloth)
    if emask.any():                    # ignore loose sewing edges in stretch
        edges = edges[emask]
    rest_len = np.linalg.norm(pattern[edges[:, 0]] - pattern[edges[:, 1]], axis=1)
    scene.frame_set(settle_frames)
    settled, _e, _m = _eval_positions(cloth)
    scene.frame_set(max(1, settle_frames - 1))
    prev, _e2, _m2 = _eval_positions(cloth)
    settle_speed = float(np.linalg.norm((settled - prev) * fps, axis=1).max())
    L = np.linalg.norm(settled[edges[:, 0]] - settled[edges[:, 1]], axis=1)
    ratio = L / np.maximum(rest_len, 1e-9)
    max_pen = 0.0
    bvh = _collider_bvh(collider_objs)
    if bvh is not None:
        for v in settled:
            vv = Vector(v)
            loc, nrm, _idx, dist = bvh.find_nearest(vv)
            if loc is not None and dist < 0.06 and _inside(bvh, vv):
                max_pen = max(max_pen, float(dist))
    scene.frame_set(1)
    return {
        "settled_penetration_m": round(max_pen, 5),
        "rest_max_stretch": round(float(ratio.max()), 4),
        "rest_min_compression": round(float(ratio.min()), 4),
        "settle_speed_m_s": round(settle_speed, 4),
    }


def baseline_verdict(report):
    ops = {"<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b}
    checks, ok = [], True
    for key, (op, bound) in BASELINE_ENVELOPE.items():
        passed = ops[op](report[key], bound)
        ok = ok and passed
        checks.append((key, report[key], op, bound, passed))
    recs = []
    if report["settled_penetration_m"] > 0.012:
        recs.append("Rest-state collider overlap -> increase clearance (thinner "
                    "colliders / wider or repositioned garment) BEFORE animating.")
    if report["settle_speed_m_s"] > 0.05:
        recs.append("Not at equilibrium -> increase settle frames.")
    if report["rest_max_stretch"] > 1.30:
        recs.append("Over-stretched at rest -> pattern too small or over-pinned.")
    if report["rest_min_compression"] < 0.55:
        recs.append("Heavily compressed at rest -> too much fabric / bunching.")
    if not recs:
        recs.append("Baseline clean -> safe to proceed to secondary motion / full solve.")
    lines = ["BASELINE DRAPE  (" + ("PASS" if ok else "FAIL") + ")"]
    for key, val, op, bound, passed in checks:
        lines.append(f"  [{'ok ' if passed else 'XX '}] {key:<22} = {val!s:<10} (need {op} {bound})")
    lines.append("  recommendation:")
    for r in recs:
        lines.append(f"    - {r}")
    return ok, "\n".join(lines)


def report_text(report, looping=False):
    ok, checks = evaluate(report, looping)
    lines = ["CLOTH DIAGNOSTICS  (" + ("ACCEPT" if ok else "REJECT") + ")"]
    for key, val, op, bound, passed in checks:
        flag = "ok " if passed else "XX "
        lines.append(f"  [{flag}] {key:<22} = {val!s:<10} (need {op} {bound})")
    extra = [k for k in report if k not in dict(DEFAULT_ENVELOPE)
             and k not in LOOP_ENVELOPE]
    if extra:
        lines.append("  -- informational --")
        for k in extra:
            lines.append(f"        {k:<22} = {report[k]}")
    return ok, "\n".join(lines)
