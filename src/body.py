"""
Measurement-driven body colliders, sized to real anthropometric data.

A procedural body (NOT a scanned/sculpted mesh): a torso lofted from elliptical
cross-sections whose circumferences come from ANSUR II (2012 Anthropometric Survey
of US Army Personnel - public), plus tapered legs. Garments drape on a body with
REAL dimensions, and a garment pattern can be sized from the same numbers, so
"does it fit" becomes a measured question.

`ansur_body(sex, pct)` builds the collider(s) at a given percentile and returns a
dict of measurement anchors (waist/hip height + ellipse radii, stature) for the
garment builder to size to. pct in {5, 50, 95} gives a fit range.

  blender --background --python src/body.py -- [--render]

Data note: values are ANSUR II 2012 summary figures in METRES. Waist is the
omphalion circumference (runs large). The 5th/95th waist figures are confirmed
from the survey; others are summary approximations - drop in the exact ANSUR II
percentile CSV (Penn State Open Design Lab / US Army DEVCOM) to make them precise.
See issue #2.
"""
import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy
import bmesh
import utils as U

# sex -> measurement -> {percentile: metres}
ANSUR = {
    "F": {
        "stature":  {5: 1.521, 50: 1.628, 95: 1.737},
        "neck":     {5: 0.300, 50: 0.335, 95: 0.379},
        "shoulder": {5: 0.330, 50: 0.355, 95: 0.382},   # biacromial breadth
        "chest":    {5: 0.821, 50: 0.931, 95: 1.079},
        "waist":    {5: 0.726, 50: 0.861, 95: 1.264},   # omphalion
        "hip":      {5: 0.904, 50: 1.019, 95: 1.159},   # buttock circumference
        "thigh":    {5: 0.496, 50: 0.582, 95: 0.685},
    },
    "M": {
        "stature":  {5: 1.655, 50: 1.756, 95: 1.868},
        "neck":     {5: 0.352, 50: 0.397, 95: 0.448},
        "shoulder": {5: 0.367, 50: 0.400, 95: 0.433},
        "chest":    {5: 0.904, 50: 1.041, 95: 1.200},
        "waist":    {5: 0.775, 50: 0.912, 95: 1.284},   # omphalion
        "hip":      {5: 0.920, 50: 1.033, 95: 1.159},
        "thigh":    {5: 0.509, 50: 0.600, 95: 0.702},
    },
}

# vertical landmarks as a fraction of stature
LEVELS = dict(neck=0.86, shoulder=0.82, chest=0.72, waist=0.63, hip=0.52, ankle=0.06)
SEGS = 28


def _ab(circ, k):
    """Ellipse semi-axes (a=width, b=depth) whose perimeter matches circumference
    `circ`, at width:depth ratio k."""
    b = circ / (2 * math.pi * math.sqrt((k * k + 1) / 2))
    return k * b, b


def _ring(bm, z, a, b, ox=0.0):
    return [bm.verts.new((ox + a * math.cos(2 * math.pi * s / SEGS),
                          b * math.sin(2 * math.pi * s / SEGS), z))
            for s in range(SEGS)]


def _bridge(bm, r0, r1):
    n = len(r0)
    for s in range(n):
        bm.faces.new((r0[s], r0[(s + 1) % n], r1[(s + 1) % n], r1[s]))


def _flat_cap(bm, ring, z, ox=0.0):
    c = bm.verts.new((ox, 0.0, z))
    n = len(ring)
    for s in range(n):
        bm.faces.new((ring[s], ring[(s + 1) % n], c))


def ansur_body(sex="F", pct=50, ox=0.0, collection=None, make_collider=True):
    """Build a torso + legs sized to ANSUR (sex, pct), offset in x by `ox`.
    Returns a dict of the objects and measurement anchors (SI metres)."""
    collection = collection or U.get_collection("Body")
    g = {m: ANSUR[sex][m][pct] for m in ANSUR[sex]}
    H = g["stature"]
    z = {k: v * H for k, v in LEVELS.items()}

    # elliptical rings (shoulder uses the biacromial breadth as its width)
    neck_a, neck_b = _ab(g["neck"], 1.15)
    chest_a, chest_b = _ab(g["chest"], 1.35)
    waist_a, waist_b = _ab(g["waist"], 1.30)
    hip_a, hip_b = _ab(g["hip"], 1.45)
    sh_a, sh_b = g["shoulder"] / 2.0, chest_b * 0.80
    thigh_r = g["thigh"] / (2 * math.pi)

    mesh = bpy.data.meshes.new(f"Body_{sex}{pct}")
    obj = bpy.data.objects.new(f"Body_{sex}{pct}", mesh)
    bm = bmesh.new()
    rn = _ring(bm, z["neck"], neck_a, neck_b, ox)
    rs = _ring(bm, z["shoulder"], sh_a, sh_b, ox)
    rc = _ring(bm, z["chest"], chest_a, chest_b, ox)
    rw = _ring(bm, z["waist"], waist_a, waist_b, ox)
    rh = _ring(bm, z["hip"], hip_a, hip_b, ox)
    _bridge(bm, rn, rs)
    _bridge(bm, rs, rc)
    _bridge(bm, rc, rw)
    _bridge(bm, rw, rh)
    _flat_cap(bm, rn, z["neck"] + 0.02, ox)     # flat neck stub (no spike)
    _flat_cap(bm, rh, z["hip"] - 0.01, ox)
    # normals MUST point outward or cloth collision sucks the garment inward
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    U.link(obj, collection)
    U.shade_smooth(obj)

    # legs: tapered, stopping just below the hip cap. They must NOT overlap the
    # torso - overlapping colliders create a conflicting zone that makes draped
    # cloth jitter forever (never settles). The small crotch gap hides under
    # any garment.
    leg_top = z["hip"] - 0.03
    legs = []
    for s in (-1, 1):
        leg = U.make_frustum(f"Leg_{sex}{pct}_{s}", r1=thigh_r * 0.70, r2=thigh_r,
                             depth=(leg_top - z["ankle"]),
                             center=(ox + s * hip_a * 0.48, 0,
                                     (leg_top + z["ankle"]) / 2),
                             verts=18, collection=collection)
        legs.append(leg)

    parts = [obj] + legs
    if make_collider:
        for p in parts:
            p.modifiers.new("Collision", 'COLLISION')
            p.collision.thickness_outer = 0.006
    return {
        "obj": obj, "legs": legs, "parts": parts, "sex": sex, "pct": pct,
        "stature": H, "waist_z": z["waist"], "hip_z": z["hip"],
        "waist_a": waist_a, "waist_b": waist_b, "hip_a": hip_a, "hip_b": hip_b,
        "waist_circ": g["waist"], "hip_circ": g["hip"], "ox": ox,
    }


def _girth(verts, zc, band=0.025, torso_cut=0.28):
    """Torso ellipse semi-axes of the mesh slice near height zc. Only verts within
    `torso_cut` of the vertical axis count, so out-stretched arms/hands (which sit
    at waist height in an A-pose) don't get read as the waist."""
    sl = [v for v in verts if abs(v[2] - zc) < band
          and (v[0] * v[0] + v[1] * v[1]) ** 0.5 < torso_cut]
    if not sl:
        return 0.0, 0.0
    return max(abs(v[0]) for v in sl), max(abs(v[1]) for v in sl)


def prep_collider(obj, decimate=0.25, collection=None, make_collider=True):
    """Turn an existing body mesh (real-world scale, Z-up, feet ~z=0) into a light
    cloth collider: decimate, force OUTWARD normals (else cloth sucks inward),
    add COLLISION. Returns the ansur_body()-shaped anchor dict with waist/hip read
    off the geometry."""
    collection = collection or U.get_collection("Body")
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    # MakeHuman meshes carry shape keys (morphs) + maybe subsurf; bake them into a
    # static mesh so Decimate can be applied (can't apply over shape keys)
    for md in list(obj.modifiers):
        if md.type in ('SUBSURF', 'MULTIRES'):
            obj.modifiers.remove(md)
    if obj.data.shape_keys:
        bpy.ops.object.convert(target='MESH')
        obj = bpy.context.view_layer.objects.active
    if decimate and decimate < 1.0:
        dm = obj.modifiers.new("Decimate", 'DECIMATE')
        dm.ratio = decimate
        bpy.ops.object.modifier_apply(modifier=dm.name)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    U.shade_smooth(obj)
    if make_collider:
        obj.modifiers.new("Collision", 'COLLISION')
        obj.collision.thickness_outer = 0.006
    verts = [tuple(v.co) for v in obj.data.vertices]
    z0 = min(v[2] for v in verts)
    H = max(v[2] for v in verts) - z0
    waist_z, hip_z = z0 + 0.62 * H, z0 + 0.52 * H
    wa, wb = _girth(verts, waist_z)
    ha, hb = _girth(verts, hip_z)
    return {
        "obj": obj, "parts": [obj], "sex": "model", "pct": None,
        "stature": H, "waist_z": waist_z, "hip_z": hip_z,
        "waist_a": wa, "waist_b": wb, "hip_a": ha, "hip_b": hb, "ox": 0.0,
    }


def mpfb_body(decimate=0.25, collection=None, make_collider=True):
    """Create an anatomical human with MPFB2 (must be installed) and prep it as a
    collider. The cloth is still 100% simulated - this is the (asset) mannequin."""
    import addon_utils
    for mod in ('bl_ext.user_default.mpfb', 'mpfb'):
        try:
            addon_utils.enable(mod, default_set=False, persistent=True)
        except Exception:
            pass
    before = set(bpy.data.objects)
    bpy.ops.mpfb.create_human()
    new = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
    if not new:
        raise RuntimeError("mpfb.create_human produced no mesh (is MPFB2 installed?)")
    human = max(new, key=lambda o: len(o.data.vertices))
    for o in new:                       # drop eyes/teeth extras; keep the body
        if o is not human:
            bpy.data.objects.remove(o, do_unlink=True)
    human.name = "Body_mpfb"
    return prep_collider(human, decimate=decimate, collection=collection,
                         make_collider=make_collider)


def load_body(filepath, height=1.70, decimate=0.25, up='Y',
              collection=None, make_collider=True):
    """Import an external body mesh (an asset-library model / MakeHuman export)
    and prep it as a collider: orient, scale to real-world `height` (m) with feet
    at z=0, centre on the vertical axis, decimate to a light collision proxy,
    force outward normals, and add COLLISION. The cloth is still 100% simulated;
    this is just the (asset) mannequin it drapes on.

    `up` is the model's up-axis ('Y' for most exports -> rotated to Blender Z).
    Returns the same measurement-anchor dict shape as ansur_body(), with waist/hip
    radii read off the actual geometry. Supports .obj / .fbx / .glb / .gltf.
    """
    collection = collection or U.get_collection("Body")
    before = set(bpy.data.objects)
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.obj':
        bpy.ops.wm.obj_import(filepath=filepath)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=filepath)
    elif ext in ('.glb', '.gltf'):
        bpy.ops.import_scene.gltf(filepath=filepath)
    else:
        raise ValueError(f"unsupported body format: {ext}")
    new = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
    if not new:
        raise RuntimeError("import produced no mesh")

    bpy.ops.object.select_all(action='DESELECT')
    for o in new:
        o.select_set(True)
    bpy.context.view_layer.objects.active = new[0]
    if len(new) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = "Body_imported"

    if up == 'Y':                          # orient to Blender Z-up
        obj.rotation_euler = (math.radians(90), 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # scale to real height, feet to z=0, centre on the vertical axis
    co = [obj.matrix_world @ v.co for v in obj.data.vertices]
    zs, xs, ys = [c.z for c in co], [c.x for c in co], [c.y for c in co]
    cur_h = max(zs) - min(zs)
    s = height / cur_h if cur_h > 1e-6 else 1.0
    obj.scale = (s, s, s)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.location = (-(min(xs) + max(xs)) / 2 * s,
                    -(min(ys) + max(ys)) / 2 * s, -min(zs) * s)
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)

    if decimate and decimate < 1.0:        # light collision proxy
        dm = obj.modifiers.new("Decimate", 'DECIMATE')
        dm.ratio = decimate
        bpy.ops.object.modifier_apply(modifier=dm.name)
    bm = bmesh.new()                        # outward normals (else cloth sucks in)
    bm.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    U.shade_smooth(obj)
    if make_collider:
        obj.modifiers.new("Collision", 'COLLISION')
        obj.collision.thickness_outer = 0.006

    verts = [tuple(v.co) for v in obj.data.vertices]
    waist_z, hip_z = 0.62 * height, 0.52 * height
    wa, wb = _girth(verts, waist_z)
    ha, hb = _girth(verts, hip_z)
    return {
        "obj": obj, "parts": [obj], "sex": "imported", "pct": None,
        "stature": height, "waist_z": waist_z, "hip_z": hip_z,
        "waist_a": wa, "waist_b": wb, "hip_a": ha, "hip_b": hb, "ox": 0.0,
    }


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

    # a fit range: female 5/50/95 and male 50 (left -> right)
    layout = [("F", 5, -1.2), ("F", 50, -0.4), ("F", 95, 0.4), ("M", 50, 1.3)]
    for sex, pct, ox in layout:
        b = ansur_body(sex, pct, ox=ox, collection=col, make_collider=False)
        for p in b["parts"]:
            p.data.materials.append(skin)
        print(f"BODY {sex}{pct:>2}  stature={b['stature']:.3f} "
              f"waist(z={b['waist_z']:.3f} a={b['waist_a']:.3f} b={b['waist_b']:.3f}) "
              f"hip(z={b['hip_z']:.3f} a={b['hip_a']:.3f})", flush=True)

    if "--render" in argv:
        U.add_ground(size=20, material=_mat("Floor", (0.16, 0.16, 0.19), 0.9))
        U.setup_solid_world(color=(0.5, 0.53, 0.6), strength=1.0)
        U.add_sun(rotation_deg=(55, 0, 30), strength=2.8, angle_deg=3)
        U.add_sun(rotation_deg=(60, 0, -120), strength=0.8, angle_deg=5)
        U.add_camera(location=(0, -4.2, 1.0), look_at=(0.05, 0, 0.95), lens=52)
        out = os.path.join(os.path.dirname(HERE), "renders", "body", "ansur.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        U.setup_render(out, res=(960, 720), samples=24, exposure=-0.3, look='AgX - Punchy')
        U.render()
        print("BODY_DONE", out)


if __name__ == "__main__":      # only run the demo when invoked directly, not on import
    main()
