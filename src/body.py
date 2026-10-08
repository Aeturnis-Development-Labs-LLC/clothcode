"""
Measurement-driven body colliders, sized to real anthropometric data.

A procedural body (NOT a scanned/sculpted mesh): a torso lofted from elliptical
cross-sections whose circumferences come from ANSUR II (US Army Anthropometric
Survey, public), plus tapered legs. The point is that garments drape on a body
with REAL dimensions, and a garment pattern can be sized from the same numbers -
so "does it fit" becomes a measured question, not a guess.

`ansur_body()` returns the collider object(s) plus a dict of measurement anchors
(waist/hip height and radius, stature, ...) for the garment builder to size to.

  blender --background --python src/body.py -- [--render]

NOTE: the values below are approximate 50th-percentile figures (metres) meant as
a starting point; replace with exact ANSUR II percentiles (incl. 5th/95th for a
fit range) from the public CSV. See issue #2.
"""
import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy
import bmesh
import utils as U

# (sex, percentile) -> circumferences / breadths in METRES
ANSUR = {
    ("F", 50): dict(stature=1.628, neck=0.315, chest=0.930, waist=0.800,
                    hip=1.010, thigh=0.580, shoulder=0.355),
    ("M", 50): dict(stature=1.755, neck=0.385, chest=1.045, waist=0.913,
                    hip=1.028, thigh=0.600, shoulder=0.400),
}

# vertical landmarks as a fraction of stature (rough but serviceable)
LEVELS = dict(neck=0.86, chest=0.72, waist=0.63, hip=0.52, crotch=0.49, ankle=0.06)


def _ring(bm, z, circ, k=1.35, segs=28, ox=0.0):
    """A ring of `segs` verts on an ellipse (width:depth = k) whose perimeter
    matches circumference `circ`. Bodies are wider side-to-side than front-back."""
    b = circ / (2 * math.pi * math.sqrt((k * k + 1) / 2))
    a = k * b
    return [bm.verts.new((ox + a * math.cos(2 * math.pi * s / segs),
                          b * math.sin(2 * math.pi * s / segs), z))
            for s in range(segs)]


def _bridge(bm, r0, r1):
    n = len(r0)
    for s in range(n):
        bm.faces.new((r0[s], r0[(s + 1) % n], r1[(s + 1) % n], r1[s]))


def _cap(bm, ring, z):
    c = bm.verts.new((sum(v.co.x for v in ring) / len(ring), 0.0, z))
    n = len(ring)
    for s in range(n):
        bm.faces.new((ring[s], ring[(s + 1) % n], c))
    return c


def ansur_body(sex="F", pct=50, ox=0.0, collection=None, make_collider=True):
    """Build a torso + legs sized to ANSUR (sex, pct), offset in x by `ox`.
    Returns a dict of the objects and measurement anchors."""
    collection = collection or U.get_collection("Body")
    m = ANSUR[(sex, pct)]
    H = m["stature"]
    zL = {k: v * H for k, v in LEVELS.items()}

    mesh = bpy.data.meshes.new(f"Body_{sex}{pct}")
    obj = bpy.data.objects.new(f"Body_{sex}{pct}", mesh)
    bm = bmesh.new()
    # torso: neck -> chest -> waist -> hip (the neck->chest slope reads as shoulders)
    r_neck = _ring(bm, zL["neck"], m["neck"], k=1.2, ox=ox)
    r_chest = _ring(bm, zL["chest"], m["chest"], k=1.35, ox=ox)
    r_waist = _ring(bm, zL["waist"], m["waist"], k=1.3, ox=ox)
    r_hip = _ring(bm, zL["hip"], m["hip"], k=1.45, ox=ox)
    _bridge(bm, r_neck, r_chest)
    _bridge(bm, r_chest, r_waist)
    _bridge(bm, r_waist, r_hip)
    _cap(bm, r_neck, zL["neck"] + 0.04)
    _cap(bm, r_hip, zL["hip"] - 0.02)
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    U.link(obj, collection)
    U.shade_smooth(obj)

    # legs: tapered cylinders from the hip down, offset left/right
    hip_b = m["hip"] / (2 * math.pi * math.sqrt((1.45 ** 2 + 1) / 2))  # hip semi-depth
    hip_a = 1.45 * hip_b
    thigh_r = m["thigh"] / (2 * math.pi)
    legs = []
    for s in (-1, 1):
        leg = U.make_frustum(f"Leg_{sex}{pct}_{s}", r1=thigh_r * 0.7, r2=thigh_r,
                             depth=(zL["hip"] - zL["ankle"]),
                             center=(ox + s * hip_a * 0.5,
                                     0, (zL["hip"] + zL["ankle"]) / 2),
                             verts=18, collection=collection)
        legs.append(leg)

    parts = [obj] + legs
    if make_collider:
        for p in parts:
            p.modifiers.new("Collision", 'COLLISION')
            p.collision.thickness_outer = 0.006
    return {
        "obj": obj, "legs": legs, "parts": parts, "sex": sex, "pct": pct,
        "stature": H, "waist_z": zL["waist"], "hip_z": zL["hip"],
        "waist_r": max(hip_a, _ring_radius(m["waist"], 1.3)),
        "waist_circ": m["waist"], "hip_circ": m["hip"], "ox": ox,
    }


def _ring_radius(circ, k):
    b = circ / (2 * math.pi * math.sqrt((k * k + 1) / 2))
    return k * b


def _mat(name, rgb, rough=0.6):
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    return mat


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    U.clear_scene()
    col = U.get_collection("Body")
    skin = _mat("Skin", (0.80, 0.56, 0.44))
    f = ansur_body("F", 50, ox=-0.45, collection=col, make_collider=False)
    mm = ansur_body("M", 50, ox=+0.45, collection=col, make_collider=False)
    for b in (f, mm):
        for p in b["parts"]:
            p.data.materials.append(skin)
        print(f"BODY {b['sex']}{b['pct']}  stature={b['stature']:.3f} "
              f"waist_z={b['waist_z']:.3f} waist_circ={b['waist_circ']:.3f} "
              f"hip_z={b['hip_z']:.3f} hip_circ={b['hip_circ']:.3f}", flush=True)

    if "--render" in argv:
        U.add_ground(size=20, material=_mat("Floor", (0.16, 0.16, 0.19), 0.9))
        U.setup_solid_world(color=(0.5, 0.53, 0.6), strength=1.0)
        U.add_sun(rotation_deg=(55, 0, 30), strength=2.8, angle_deg=3)
        U.add_sun(rotation_deg=(60, 0, -120), strength=0.8, angle_deg=5)
        U.add_camera(location=(0, -3.4, 1.0), look_at=(0, 0, 0.95), lens=50)
        out = os.path.join(os.path.dirname(HERE), "renders", "body", "ansur.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        U.setup_render(out, res=(720, 720), samples=24, exposure=-0.3, look='AgX - Punchy')
        U.render()
        print("BODY_DONE", out)


if __name__ == "__main__" or True:
    main()
