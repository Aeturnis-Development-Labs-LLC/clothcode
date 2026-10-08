"""
Validate the ANSUR body as a real drape target: size an A-line skirt to the
body's own waist/hip measurements, drape it, and run the baseline gate. Reusable
across the fit range.

  blender --background --python src/fit_test.py -- [--sex F|M] [--pct 5|50|95]
          [--settle N] [--samples N]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy
from mathutils import Matrix
import utils as U
import cloth
import clothdiag
import body


def flat(name, rgb, rough=0.85):
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    return mat


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    def arg(f, d, c=str):
        return c(argv[argv.index(f) + 1]) if f in argv else d

    sex = arg("--sex", "F")
    pct = arg("--pct", 50, int)
    model = arg("--model", "ansur")      # ansur | mpfb
    settle = arg("--settle", 140, int)
    samples = arg("--samples", 24, int)

    U.clear_scene()
    col = U.get_collection(cloth.COL)

    if model == "mpfb":
        b = body.mpfb_body(collection=col, make_collider=True)
        tag = "mpfb"
    else:
        b = body.ansur_body(sex, pct, collection=col, make_collider=True)
        tag = f"{sex}{pct}"
    for p in b["parts"]:
        p.data.materials.append(flat("Skin", (0.80, 0.56, 0.44), 0.6))
    print(f"BODY[{model}] waist(z={b['waist_z']:.3f} a={b['waist_a']:.3f} "
          f"b={b['waist_b']:.3f}) hip(z={b['hip_z']:.3f} a={b['hip_a']:.3f})", flush=True)

    # size the skirt to the body: waistband just outside the waist ellipse; hem
    # flared well past the hips; length to just above the knee
    # waistband must clear the body by MORE than the fabric's collision margin
    # (distance_min ~0.015) or the pinned waist sits inside the margin and the
    # solver shoves it forever (never settles).
    spec = dict(
        r_waist=b["waist_a"] + 0.030,
        r_hem=b["hip_a"] + 0.09,          # flare that clears the hips + legs
        ztop=b["waist_z"],
        zbot=b["hip_z"] - 0.30,           # mid-thigh (hem stays clear of the legs)
        segs=48, loops=30, flutes=8, amp_waist=0.008, amp_hem=0.035,
    )
    skirt, _w = cloth.build_skirt(spec, col)
    skirt.data.materials.append(flat("Skirt", (0.30, 0.10, 0.36)))
    cl = cloth.add_cloth(skirt, "Waist", cloth.FABRIC)
    cloth.bake(skirt, cl, settle)

    rep = clothdiag.baseline(skirt, settle, fps=25)
    ok, text = clothdiag.baseline_verdict(rep)
    print(f"--- skirt on {tag}  (r_waist={spec['r_waist']:.3f} "
          f"r_hem={spec['r_hem']:.3f} z[{spec['zbot']:.2f},{spec['ztop']:.2f}]) ---")
    print(text, flush=True)
    print("FIT_GATE", tag, "PASS" if ok else "FAIL")

    bpy.context.scene.frame_set(settle)
    U.add_ground(size=20, material=flat("Floor", (0.16, 0.16, 0.19), 0.9))
    U.setup_solid_world(color=(0.52, 0.55, 0.62), strength=1.0)
    U.add_sun(rotation_deg=(54, 0, 32), strength=2.8, angle_deg=3)
    U.add_sun(rotation_deg=(60, 0, -118), strength=0.8, angle_deg=5)
    U.add_camera(location=(1.9, -2.3, 0.95), look_at=(0, 0, 0.8), lens=50)
    out = os.path.join(os.path.dirname(HERE), "renders", "body", f"fit_{tag}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    U.setup_render(out, res=(620, 760), samples=samples, exposure=-0.3, look='AgX - Punchy')
    U.render()
    print("FIT_DONE", out)


main()
