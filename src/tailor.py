"""
tailor - measure a body and cut garments to its measurements.

Real-tailoring workflow: measure the model (circumferences as ellipses + heights),
draft flat panels sized to those measurements plus an ease allowance, sew them on
the body with cloth.build_garment, and fit-check through the gate.

  blender --background --python src/tailor.py -- [--measure] [--model mpfb|ansur]
"""
import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy
import bmesh
import utils as U
import cloth
import clothdiag
import body

# vertical landmarks as fractions of stature, and the torso cut for each slice
LEVELS = {"neck": (0.86, 0.12), "shoulder": (0.82, 0.26), "bust": (0.75, 0.28),
          "waist": (0.63, 0.28), "hip": (0.52, 0.30), "knee": (0.28, 0.20)}


def _perimeter(a, b):
    """Ramanujan ellipse circumference."""
    return math.pi * (3 * (a + b) - math.sqrt(max(0.0, (3 * a + b) * (a + 3 * b))))


def measure_body(b):
    """Read tailoring measurements off a prepped body (dict from body.*). Returns
    per-level {z, a, b, circ} (ellipse semi-axes + circumference) plus stature."""
    obj = b["obj"]
    verts = [tuple(v.co) for v in obj.data.vertices]
    z0 = min(v[2] for v in verts)
    H = max(v[2] for v in verts) - z0
    M = {"stature": H, "z0": z0}
    for name, (frac, cut) in LEVELS.items():
        z = z0 + frac * H
        a, bb = body._girth(verts, z, torso_cut=cut)
        M[name] = {"z": z, "a": a, "b": bb, "circ": _perimeter(a, bb)}
    return M


def flat(name, rgb, rough=0.85):
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    return mat


def print_measurements(M):
    print("\nTAILOR MEASUREMENTS (metres)")
    print(f"{'level':<9} {'z':>6} {'width(2a)':>10} {'depth(2b)':>10} {'circ':>7}")
    for name in ("neck", "shoulder", "bust", "waist", "hip", "knee"):
        m = M[name]
        print(f"{name:<9} {m['z']:>6.3f} {2*m['a']:>10.3f} {2*m['b']:>10.3f} {m['circ']:>7.3f}", flush=True)
    print(f"stature = {M['stature']:.3f} m", flush=True)


def tailor_skirt(M, col, ease=0.012, flare=1.55, length=0.50):
    """Draft a skirt cut to the waist measurement: an elliptical waistband sized to
    the body's waist + ease, two flared front/back panels whose top width equals
    the waistband's front arc (so it sews on without gathering)."""
    wz = M["waist"]["z"]
    wa, wb = M["waist"]["a"] + ease, M["waist"]["b"] + ease   # waistband ellipse
    w = _perimeter(wa, wb) / 2.0                              # panel top = front arc
    hz = wz - length
    d = wb + 0.02
    nu, nv = 40, 30
    panels = [
        dict(name="SkirtF", w=w, h=length, res=(nu, nv),
             center=(0, +d, (wz + hz) / 2), normal="Y", taper=flare),
        dict(name="SkirtB", w=w, h=length, res=(nu, nv),
             center=(0, -d, (wz + hz) / 2), normal="Y", taper=flare),
    ]
    seams = [((0, "L"), (1, "L")), ((0, "R"), (1, "R"))]
    skirt, pin = cloth.build_garment(panels, seams, pins=None,
                                     waist=dict(a=wa, b=wb, z=wz),
                                     name="Skirt", collection=col)
    return skirt, pin


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    def arg(f, d, c=str):
        return c(argv[argv.index(f) + 1]) if f in argv else d

    model = arg("--model", "mpfb")
    settle = arg("--settle", 150, int)
    samples = arg("--samples", 18, int)

    U.clear_scene()
    col = U.get_collection(cloth.COL)
    # a smooth collider (little decimation) so the fitted margin doesn't poke through
    b = (body.mpfb_body(decimate=0.7, collection=col) if model == "mpfb"
         else body.ansur_body("F", 50, collection=col))
    for p in b["parts"]:
        p.data.materials.append(flat("Skin", (0.80, 0.56, 0.44), 0.6))
    M = measure_body(b)
    print_measurements(M)

    if "--measure" in argv:
        print("MEASURE_DONE")
        return

    # strip the out-stretched arms from the collider: the A-pose hands hang at hip
    # height and otherwise collide with (and snag) the skirt. A skirt only needs
    # the torso + legs.
    obj = b["obj"]
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    z_cut = M["hip"]["z"] + 0.03
    doomed = [v for v in bm.verts
              if (v.co.x ** 2 + v.co.y ** 2) ** 0.5 > 0.23 and v.co.z > z_cut]
    bmesh.ops.delete(bm, geom=doomed, context='VERTS')
    bm.to_mesh(obj.data)
    bm.free()
    print(f"stripped arms: removed {len(doomed)} verts above z={z_cut:.2f}", flush=True)

    skirt, pin = tailor_skirt(M, col)
    skirt.data.materials.append(flat("Skirt", (0.33, 0.12, 0.40)))
    cl = cloth.add_cloth(skirt, pin, cloth.FITTED, sew=True)
    cloth.bake(skirt, cl, settle)

    rep = clothdiag.baseline(skirt, settle, fps=25)
    ok, text = clothdiag.baseline_verdict(rep)
    print(text, flush=True)
    print("TAILOR_SKIRT_GATE", "PASS" if ok else "FAIL")

    bpy.context.scene.frame_set(settle)
    U.add_ground(size=20, material=flat("Floor", (0.16, 0.16, 0.19), 0.9))
    U.setup_solid_world(color=(0.52, 0.55, 0.62), strength=1.0)
    U.add_sun(rotation_deg=(54, 0, 32), strength=2.8, angle_deg=3)
    U.add_sun(rotation_deg=(60, 0, -118), strength=0.8, angle_deg=5)
    U.add_camera(location=(1.9, -2.3, 0.95), look_at=(0, 0, 0.85), lens=50)
    out = os.path.join(os.path.dirname(HERE), "renders", "body", "tailor_skirt.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    U.setup_render(out, res=(620, 820), samples=samples, exposure=-0.3, look='AgX - Punchy')
    U.render()
    print("TAILOR_DONE", out)


main()
