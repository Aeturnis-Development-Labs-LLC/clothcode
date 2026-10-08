"""
Fabric showcase - the four validated recipes, side by side, one shared motion.

All four skirts (FABRIC / WOOL / LINEN / VELVET) are built from the SAME garment
and driven by the SAME motion, so the only variable is the cloth recipe. A
checker grid with fabric-locked UVs rides the cloth, exposing stretch/shear; the
physics gradient is the star - light FABRIC reacts most, heavy VELVET least.

Motions (--motion):
  twirl  each waist bone eases through 3 whole turns (loops: rest -> rest)
  dance  each waist bone oscillates +/-55 deg, 2 cycles (loops)
  kick   a per-skirt knee pivots up into the front hem, twice (loops)
  wind   ONE shared wind field (breeze -> gust -> gale) hits all four equally

  blender --background --python cottage/showcase.py -- --motion twirl|dance|kick|wind
          [--samples N] [--res WxH] [--settle N] [--motion-frames N] [--every N]

Each motion writes renders/showcase/<motion>/ and, if ffmpeg is on PATH, encodes
showcase_<motion>.mp4 plus a looping .gif.
"""
import os
import sys
import math
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy
from mathutils import Matrix
import utils as U
import cloth

TAU = 2 * math.pi
SPACING = 1.1
ORDER = ["FABRIC", "WOOL", "LINEN", "VELVET"]

# identical garment for every fabric; only the recipe (and motion) differ
SPEC = dict(r_waist=0.15, r_hem=0.27, ztop=0.95, zbot=0.32,
            segs=64, loops=40, flutes=12, amp_waist=0.015, amp_hem=0.06)

# per-motion defaults (settle long enough to fully drape the heavy fabrics first)
MOTIONS = {
    "twirl": dict(settle=120, motion=96,  driver="waist", revs=3),
    "dance": dict(settle=120, motion=96,  driver="waist", sway_deg=55, cycles=2),
    "kick":  dict(settle=120, motion=96,  driver="knee",  kick_deg=58, kicks=2),
    "wind":  dict(settle=120, motion=120, driver="wind"),
}

OUTROOT = os.path.join(os.path.dirname(HERE), "renders", "showcase")


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------
def checker_material():
    """Debug checker; fabric-locked UVs make the squares stretch WITH the cloth."""
    mat = bpy.data.materials.new("Checker")
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.78
    coord = nt.nodes.new("ShaderNodeTexCoord")
    chk = nt.nodes.new("ShaderNodeTexChecker")
    chk.inputs["Scale"].default_value = 24
    chk.inputs["Color1"].default_value = (0.42, 0.05, 0.07, 1)
    chk.inputs["Color2"].default_value = (0.90, 0.87, 0.80, 1)
    nt.links.new(coord.outputs["UV"], chk.inputs["Vector"])
    nt.links.new(chk.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def flat(name, rgb, rough=0.9):
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    return mat


# ---------------------------------------------------------------------------
# per-skirt geometry
# ---------------------------------------------------------------------------
def cyl_uv(skirt, ztop, zbot):
    """Cylindrical UVs from the UNDEFORMED, origin-centred positions, so the
    checker is painted into the fabric and then deforms along with it."""
    me = skirt.data
    me.uv_layers.new(name="UVMap")
    uvd = me.uv_layers.active.data
    for poly in me.polygons:
        for li in poly.loop_indices:
            co = me.vertices[me.loops[li].vertex_index].co
            u = (math.atan2(co.y, co.x) % TAU) / TAU
            v = (co.z - zbot) / (ztop - zbot)
            uvd[li].uv = (u, v)


def body_at(spec, ox, col):
    """Hip + two thighs (the thing a twirling/dancing skirt hangs on) at x = ox."""
    rw, ztop, zbot = spec["r_waist"], spec["ztop"], spec["zbot"]
    hip_z = ztop - 0.12
    cloth._collide(U.make_sphere(f"Hip_{ox:+.2f}", rw * 0.90, center=(ox, 0, hip_z),
                                 scale=(1.04, 0.74, 1.0), collection=col))
    leg_r = max(0.042, rw * 0.33)
    top, bot = hip_z, zbot - 0.05
    for s in (-1, 1):
        cloth._collide(U.make_cylinder(f"Thigh_{ox:+.2f}_{s}", radius=leg_r,
                                       depth=(top - bot),
                                       center=(ox + s * rw * 0.38, 0, (top + bot) / 2),
                                       verts=18, collection=col))


def kick_body_at(spec, ox, col):
    """Static hip + planted left thigh, plus a right thigh on a knee pivot that
    swings UP into the front of the skirt. Returns the pivot empty to keyframe."""
    rw, ztop, zbot = spec["r_waist"], spec["ztop"], spec["zbot"]
    hip_z = ztop - 0.12
    cloth._collide(U.make_sphere(f"Hip_{ox:+.2f}", rw * 0.90, center=(ox, 0, hip_z),
                                 scale=(1.04, 0.74, 1.0), collection=col))
    leg_r = max(0.042, rw * 0.33)
    top, bot = hip_z, zbot - 0.05
    cloth._collide(U.make_cylinder(f"ThighL_{ox:+.2f}", radius=leg_r, depth=(top - bot),
                                   center=(ox - rw * 0.38, 0, (top + bot) / 2),
                                   verts=18, collection=col))
    pivot = bpy.data.objects.new(f"Knee_{ox:+.2f}", None)
    U.link(pivot, col)
    pivot.location = (ox + rw * 0.38, 0, hip_z)
    bpy.context.view_layer.update()
    thigh = U.make_cylinder(f"ThighR_{ox:+.2f}", radius=leg_r, depth=(top - bot),
                            center=(ox + rw * 0.38, 0, (top + bot) / 2),
                            verts=18, collection=col)
    thigh.parent = pivot
    thigh.matrix_parent_inverse = pivot.matrix_world.inverted()
    cloth._collide(thigh)
    return pivot


def wind_body_at(spec, ox, col):
    """Hip + two clearly-separated legs so the skirt pressed back by the wind
    visibly wraps and conforms to them."""
    rw, ztop, zbot = spec["r_waist"], spec["ztop"], spec["zbot"]
    hip_z = ztop - 0.12
    cloth._collide(U.make_sphere(f"Hip_{ox:+.2f}", rw * 0.88, center=(ox, 0, hip_z),
                                 scale=(1.04, 0.74, 1.0), collection=col))
    for s in (-1, 1):
        cloth._collide(U.make_cylinder(f"Leg_{ox:+.2f}_{s}", radius=0.058, depth=0.75,
                                       center=(ox + s * 0.075, 0, hip_z - 0.40),
                                       verts=16, collection=col))


def waist_bone_at(skirt, ox, z_top, name, col):
    """Armature whose single 'Waist' bone sits at x = ox and drives the skirt's
    waistband. Add BEFORE the cloth so the stack is Armature -> Cloth -> Subsurf."""
    ad = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, ad)
    U.link(arm, col)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    b = ad.edit_bones.new("Waist")
    b.head = (ox, 0, z_top)
    b.tail = (ox, 0, z_top + 0.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    am = skirt.modifiers.new("Drive", 'ARMATURE')
    am.object = arm
    pb = arm.pose.bones["Waist"]
    pb.rotation_mode = 'XYZ'
    return pb


def label(text, ox, mat, col):
    U.add_text(text, location=(ox, -0.42, 0.10), size=0.11,
               rot=(math.radians(90), 0, 0), material=mat, collection=col)


# ---------------------------------------------------------------------------
# wind field (shared by all four skirts in the wind motion)
# ---------------------------------------------------------------------------
def add_wind_row(col, settle, total):
    """One WIND field blowing +Y (away from camera) so every skirt billows back
    in parallel. Strength escalates breeze -> gust -> gale (not physical units;
    cloth at this scale needs hundreds, and flow carries much more force)."""
    bpy.ops.object.effector_add(type='WIND', location=(0, -1.6, 0.6))
    w = bpy.context.active_object
    w.name = "Wind"
    w.rotation_euler = (math.radians(-90), 0, 0)   # local Z -> +Y
    f = w.field
    f.flow = 0.75                 # more air-drag transfer -> fuller billow
    f.noise = 0.0
    try:
        f.falloff_power = 0.0
    except Exception:
        pass
    keys = [(1, 0), (settle, 0),
            (settle + 20, 300.0), (settle + 40, 250.0),                 # soft breeze
            (settle + 55, 1100.0), (settle + 66, 620.0), (settle + 78, 980.0),  # gusts
            (settle + 100, 2050.0), (total, 2000.0)]                    # gale
    bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
    for fr, s in keys:
        f.strength = s
        f.keyframe_insert('strength', frame=fr)
    return w


def smoothstep(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


# ---------------------------------------------------------------------------
def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    def arg(flag, default=None, cast=str):
        return cast(argv[argv.index(flag) + 1]) if flag in argv else default

    mode = arg("--motion", "twirl")
    if mode not in MOTIONS:
        raise SystemExit(f"unknown --motion {mode}; choose from {list(MOTIONS)}")
    cfg = MOTIONS[mode]
    driver = cfg["driver"]
    samples = arg("--samples", 40, int)
    settle = arg("--settle", cfg["settle"], int)
    motion = arg("--motion-frames", cfg["motion"], int)
    every = arg("--every", 1, int)
    res = (1280, 720)
    if "--res" in argv:
        w, h = arg("--res").lower().split("x")
        res = (int(w), int(h))
    total = settle + motion
    out = os.path.join(OUTROOT, mode)
    os.makedirs(out, exist_ok=True)

    U.clear_scene()
    col = U.get_collection("Showcase")
    checker = checker_material()
    lbl_mat = bpy.data.materials.new("Label")
    lb = lbl_mat.node_tree.nodes["Principled BSDF"]
    lb.inputs["Base Color"].default_value = (0.92, 0.92, 0.95, 1)
    if "Emission Color" in lb.inputs:
        lb.inputs["Emission Color"].default_value = (0.92, 0.92, 0.95, 1)
        lb.inputs["Emission Strength"].default_value = 1.1

    x0 = -SPACING * (len(ORDER) - 1) / 2.0
    drivers, cloths = [], []
    for i, name in enumerate(ORDER):
        ox = x0 + i * SPACING
        skirt, _waist = cloth.build_skirt(SPEC, col)
        skirt.name = f"Skirt_{name}"
        cyl_uv(skirt, SPEC["ztop"], SPEC["zbot"])
        skirt.data.materials.clear()
        skirt.data.materials.append(checker)
        skirt.data.transform(Matrix.Translation((ox, 0, 0)))   # offset MESH; object stays identity

        if driver == "waist":
            drivers.append(waist_bone_at(skirt, ox, SPEC["ztop"], f"Drv_{name}", col))
            body_at(SPEC, ox, col)
        elif driver == "knee":
            drivers.append(kick_body_at(SPEC, ox, col))
        elif driver == "wind":
            wind_body_at(SPEC, ox, col)
            drivers.append(None)

        cl = cloth.add_cloth(skirt, "Waist", cloth.FABRICS[name]["recipe"])
        label(name, ox, lbl_mat, col)
        cloths.append((skirt, cl))

    bpy.context.view_layer.update()

    # --- drive the shared motion (flat through the settle pre-roll) ---
    if driver == "wind":
        for _s, cl in cloths:
            cl.settings.effector_weights.wind = 1.0
            cl.settings.effector_weights.all = 1.0
        add_wind_row(col, settle, total)
    else:
        for f in range(1, total + 1):
            u = (f - settle) / motion
            on = f > settle
            if mode == "twirl":
                ang = cfg["revs"] * TAU * smoothstep(u) if on else 0.0
                euler = (0, ang, 0)
            elif mode == "dance":
                ang = math.radians(cfg["sway_deg"]) * math.sin(TAU * cfg["cycles"] * u) if on else 0.0
                euler = (0, ang, 0)
            elif mode == "kick":
                ang = math.radians(cfg["kick_deg"]) * (0.5 - 0.5 * math.cos(TAU * cfg["kicks"] * u)) if on else 0.0
                euler = (-ang, 0, 0)
            for d in drivers:
                d.rotation_euler = euler
                d.keyframe_insert('rotation_euler', frame=f)

    # --- bake each cloth cache (spatially separate -> independent) ---
    for i, (skirt, cl) in enumerate(cloths):
        print(f"baking {mode}/{ORDER[i]} ...", flush=True)
        cloth.bake(skirt, cl, total)

    # --- studio ---
    U.add_ground(size=30, material=flat("Floor", (0.14, 0.14, 0.17)))
    U.setup_solid_world(color=(0.30, 0.33, 0.40), strength=1.0)
    U.add_sun(rotation_deg=(54, 0, 28), strength=3.0, angle_deg=3.0)
    U.add_sun(rotation_deg=(60, 0, -115), strength=0.9, angle_deg=6.0)
    U.add_camera(location=(0, -5.2, 0.92), look_at=(0, 0.0, 0.56), lens=42)

    # --- render ---
    n = 0
    for f in range(settle + 1, total + 1):
        if every > 1 and (f - settle - 1) % every != 0:
            continue
        bpy.context.scene.frame_set(f)
        n += 1
        p = os.path.join(out, f"f_{n:04d}.png")
        U.setup_render(p, res=res, samples=samples, exposure=-0.35, look='AgX - Punchy')
        U.render()
    print(f"SHOWCASE_FRAMES {mode} {n} -> {out}", flush=True)
    encode(out, mode)


def encode(outdir, mode):
    """mp4 (for Reddit) + a looping gif, via ffmpeg if available."""
    mp4 = os.path.join(outdir, f"showcase_{mode}.mp4")
    gif = os.path.join(outdir, f"showcase_{mode}.gif")
    pat = os.path.join(outdir, "f_%04d.png")
    try:
        subprocess.run(["ffmpeg", "-y", "-framerate", "25", "-i", pat,
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", mp4],
                       check=True, capture_output=True)
        print("MP4:", mp4, flush=True)
        pal = os.path.join(outdir, "_pal.png")
        subprocess.run(["ffmpeg", "-y", "-i", pat, "-vf",
                        "fps=25,scale=640:-1:flags=lanczos,palettegen", pal],
                       check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-framerate", "25", "-i", pat, "-i", pal,
                        "-lavfi", "fps=25,scale=640:-1:flags=lanczos[x];[x][1:v]paletteuse",
                        "-loop", "0", gif], check=True, capture_output=True)
        print("GIF:", gif, flush=True)
    except Exception as e:
        print("encode skipped:", e, flush=True)


main()
