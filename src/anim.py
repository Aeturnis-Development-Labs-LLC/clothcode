"""
Rebuilt cloth animations on validated baselines.

Every clip: cloth.build_skirt (spec) + a fitted cloth.lower_body + cloth.FABRIC,
then a SETTLE pre-roll (gravity only) so the motion begins from a properly
draped rest state - never from the raw built mesh. Then the mode's motion is
driven, the whole range is baked, and only the post-settle frames are rendered.

  blender --background --python cottage/anim.py -- --mode twirl|dance|march [--samples N]
"""
import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy
import utils as U
import cloth

TAU = 2 * math.pi
SETTLE = 40

SPECS = {
    "twirl": dict(r_waist=0.14, r_hem=0.22, ztop=0.95, zbot=0.30,
                  segs=56, loops=34, flutes=14, amp_waist=0.02, amp_hem=0.13,
                  color=(0.12, 0.05, 0.28), motion=60, revs=3.0),
    "dance": dict(r_waist=0.15, r_hem=0.33, ztop=0.95, zbot=0.16,
                  segs=64, loops=42, flutes=18, amp_waist=0.015, amp_hem=0.075,
                  color=(0.45, 0.08, 0.12), motion=80),
    "march": dict(r_waist=0.15, r_hem=0.27, ztop=0.95, zbot=0.35,
                  segs=56, loops=36, flutes=14, amp_waist=0.015, amp_hem=0.055,
                  color=(0.10, 0.20, 0.30), motion=60),
    "wind": dict(r_waist=0.15, r_hem=0.30, ztop=0.95, zbot=0.42,
                 segs=60, loops=38, flutes=12, amp_waist=0.015, amp_hem=0.05,
                 color=(0.22, 0.30, 0.38), motion=100),
}


def colour(rgb):
    mat = bpy.data.materials.new("Garment")
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = 0.82
    if "Sheen Weight" in b.inputs:
        b.inputs["Sheen Weight"].default_value = 0.25
    return mat


def flat(name, rgb):
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = 0.9
    return mat


def march_body(spec, col, total, marches=2, knee_deg=55):
    """march needs an animated leg: static hip + left thigh, and a right thigh
    that pivots at the hip (flat during settle, then knees up toward -Y)."""
    rw, ztop, zbot = spec["r_waist"], spec["ztop"], spec["zbot"]
    hip_z = ztop - 0.12
    cloth._collide(U.make_sphere("Hip", rw * 0.90, center=(0, 0, hip_z),
                                 scale=(1.04, 0.74, 1.0), collection=col))
    leg_r = max(0.042, rw * 0.33)
    top, bot = hip_z, zbot - 0.05
    cloth._collide(U.make_cylinder("ThighL", radius=leg_r, depth=(top - bot),
                                   center=(-rw * 0.38, 0, (top + bot) / 2), collection=col))
    pivot = bpy.data.objects.new("Knee", None)
    U.link(pivot, col)
    pivot.location = (rw * 0.38, 0, hip_z)
    bpy.context.view_layer.update()
    thigh = U.make_cylinder("ThighR", radius=leg_r, depth=(top - bot),
                            center=(rw * 0.38, 0, (top + bot) / 2), collection=col)
    thigh.parent = pivot
    thigh.matrix_parent_inverse = pivot.matrix_world.inverted()
    cloth._collide(thigh)
    a = math.radians(knee_deg)
    for f in range(1, total + 1):
        ang = 0.0 if f <= SETTLE else a * (0.5 - 0.5 * math.cos(TAU * marches * (f - SETTLE) / (total - SETTLE)))
        pivot.rotation_euler = (-ang, 0, 0)
        pivot.keyframe_insert('rotation_euler', frame=f)


def wind_body(spec, col):
    """Hip + two clearly-separated legs so the skirt pressed back by the wind
    visibly wraps and conforms to them."""
    rw, ztop, zbot = spec["r_waist"], spec["ztop"], spec["zbot"]
    hip_z = ztop - 0.12
    cloth._collide(U.make_sphere("Hip", rw * 0.88, center=(0, 0, hip_z),
                                 scale=(1.04, 0.74, 1.0), collection=col))
    for s in (-1, 1):
        cloth._collide(U.make_cylinder(f"Leg{s}", radius=0.058, depth=0.75,
                                       center=(s * 0.075, 0, hip_z - 0.40), collection=col))


def add_wind(col, settle, total):
    """A WIND force field blowing +Y (away from the camera), so the near face of
    the skirt is pressed back against the legs. Strength escalates:
    settle (calm) -> soft breeze -> gusts -> gale."""
    bpy.ops.object.effector_add(type='WIND', location=(0, -1.5, 0.6))
    w = bpy.context.active_object
    w.name = "Wind"
    w.rotation_euler = (0, math.radians(90), 0)     # local Z -> +X (blow sideways)
    f = w.field
    # Blender wind isn't in physical units; cloth at this scale needs values in
    # the hundreds, and flow (air-drag transfer) carries much more force.
    f.flow = 0.6                 # part direct push (presses obstacles), part drag
    f.noise = 0.0
    try:
        f.falloff_power = 0.0    # constant force regardless of distance
    except Exception:
        pass
    keys = [(1, 0), (settle, 0),
            (settle + 18, 250.0), (settle + 35, 210.0),            # soft breeze
            (settle + 48, 950.0), (settle + 56, 500.0), (settle + 64, 760.0),  # gusts
            (settle + 82, 1700.0), (total, 1650.0)]                # gale
    bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
    for fr, s in keys:
        f.strength = s
        f.keyframe_insert('strength', frame=fr)
    print("WIND_FIELD", w.name, f.type, "end_strength", f.strength, flush=True)
    return w


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    mode = argv[argv.index("--mode") + 1] if "--mode" in argv else "dance"
    samples = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 18
    spec = SPECS[mode]
    total = SETTLE + spec["motion"]

    U.clear_scene()
    col = U.get_collection(cloth.COL)
    skirt, _ = cloth.build_skirt(spec, col)
    skirt.data.materials.append(colour(spec["color"]))

    if mode in ("twirl", "dance"):
        arm, pb = cloth.waist_bone(skirt, spec["ztop"], collection=col)
        cloth.lower_body(spec, col)
    elif mode == "march":
        march_body(spec, col, total)
    elif mode == "wind":
        wind_body(spec, col)

    # a fast twirl needs a heavier fabric (light cloth flings flat instantly)
    fabric = cloth.WOOL if mode == "twirl" else cloth.FABRIC
    cl = cloth.add_cloth(skirt, "Waist", fabric)

    # --- drive the motion (flat through the settle pre-roll) ---
    if mode == "twirl":
        bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
        pb.rotation_euler = (0, 0, 0); pb.keyframe_insert('rotation_euler', frame=1)
        pb.rotation_euler = (0, 0, 0); pb.keyframe_insert('rotation_euler', frame=SETTLE)
        pb.rotation_euler = (0, spec["revs"] * TAU, 0); pb.keyframe_insert('rotation_euler', frame=total)
    elif mode == "dance":
        A = math.radians(55)
        for f in range(1, total + 1):
            ang = 0.0 if f <= SETTLE else A * math.sin(TAU * 2 * (f - SETTLE) / spec["motion"])
            pb.rotation_euler = (0, ang, 0)
            pb.keyframe_insert('rotation_euler', frame=f)
    elif mode == "wind":
        cl.settings.effector_weights.wind = 1.0
        cl.settings.effector_weights.all = 1.0
        add_wind(col, SETTLE, total)

    cloth.bake(skirt, cl, total)

    # --- studio + render the post-settle frames ---
    U.add_ground(size=20, material=flat("Floor", (0.17, 0.17, 0.19)))
    U.setup_solid_world(color=(0.55, 0.57, 0.62), strength=1.0)
    U.add_sun(rotation_deg=(52, 0, 35), strength=2.6, angle_deg=3.0)
    U.add_sun(rotation_deg=(58, 0, -120), strength=0.8, angle_deg=5.0)
    if mode == "wind":
        U.add_camera(location=(1.1, -2.2, 0.82), look_at=(0.05, 0, 0.5), lens=46)
    else:
        U.add_camera(location=(1.5, -1.95, 0.95), look_at=(0, 0.03, 0.55), lens=44)

    seqdir = os.path.join(os.path.dirname(HERE), "renders", f"anim_{mode}")
    os.makedirs(seqdir, exist_ok=True)
    n = 0
    for f in range(SETTLE + 1, total + 1):
        bpy.context.scene.frame_set(f)
        n += 1
        p = os.path.join(seqdir, f"a_{n:04d}.png")
        U.setup_render(p, res=(560, 720), samples=samples, exposure=-0.3, look='AgX - Punchy')
        U.render()
    print("ANIM_DONE:", mode, seqdir, n, "frames")


main()
