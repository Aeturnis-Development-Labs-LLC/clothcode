"""
Fabric range study - map the recipe's safe envelope.

Runs every recipe in cloth.FABRICS (FABRIC / WOOL / LINEN / VELVET) through the
SAME garment, the SAME body, and the SAME baseline gate (clothdiag), so the only
variable is the cloth recipe. Prints a comparison table of the baseline metrics
with each fabric's PASS/FAIL, and renders one settled frame of each into a
labelled contact strip so the fold CHARACTER (crisp linen vs deep velvet) is
visible beside the numbers.

  blender --background --python cottage/fabric_study.py [-- --samples N]

Afterwards an ImageMagick montage (if `magick` is on PATH) stitches the four
settled renders into renders/fabric_study/strip.png.
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy
import utils as U
import cloth
import clothdiag

# one neutral A-line skirt used for every fabric
STUDY_SPEC = dict(r_waist=0.15, r_hem=0.30, ztop=0.95, zbot=0.30,
                  segs=60, loops=40, flutes=10, amp_waist=0.015, amp_hem=0.06)

OUT = os.path.join(os.path.dirname(HERE), "renders", "fabric_study")


def flat(name, rgb, rough=0.88):
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    if "Sheen Weight" in b.inputs:
        b.inputs["Sheen Weight"].default_value = 0.3
    return mat


# a tint per fabric just so the strip reads at a glance
TINT = {
    "FABRIC": (0.18, 0.22, 0.42),
    "WOOL":   (0.35, 0.20, 0.12),
    "LINEN":  (0.70, 0.66, 0.54),
    "VELVET": (0.30, 0.06, 0.10),
}


def render_settled(name, settle, samples):
    """Camera/lights/ground are rebuilt each pass (drape clears the scene)."""
    skirt = bpy.data.objects["Skirt"]
    skirt.data.materials.clear()
    skirt.data.materials.append(flat(name, TINT[name], rough=0.6 if name == "VELVET" else 0.9))
    U.add_ground(size=20, material=flat("Floor", (0.16, 0.16, 0.18)))
    U.setup_solid_world(color=(0.55, 0.57, 0.62), strength=1.0)
    U.add_sun(rotation_deg=(52, 0, 35), strength=2.6, angle_deg=3.0)
    U.add_sun(rotation_deg=(58, 0, -120), strength=0.8, angle_deg=5.0)
    U.add_camera(location=(1.5, -1.95, 0.95), look_at=(0, 0.03, 0.58), lens=44)
    bpy.context.scene.frame_set(settle)     # the settled (baked) rest state
    p = os.path.join(OUT, f"{name}.png")
    U.setup_render(p, res=(430, 620), samples=samples, exposure=-0.3, look='AgX - Punchy')
    U.render()
    return p


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    samples = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 24
    os.makedirs(OUT, exist_ok=True)

    rows = []
    pngs = []
    for name, cfg in cloth.FABRICS.items():
        recipe, settle = cfg["recipe"], cfg["settle"]
        print(f"\n===== {name}  (settle={settle}) =====", flush=True)
        report = cloth.drape_baseline(STUDY_SPEC, settle=settle, fabric=recipe)
        ok, text = clothdiag.baseline_verdict(report)
        print(text, flush=True)
        rows.append((name, recipe, settle, report, ok))
        pngs.append(render_settled(name, settle, samples))

    # ---- comparison table ----
    print("\n\n################  FABRIC RANGE STUDY  ################")
    print(f"garment: A-line skirt  r_waist={STUDY_SPEC['r_waist']} r_hem={STUDY_SPEC['r_hem']} "
          f"z[{STUDY_SPEC['zbot']},{STUDY_SPEC['ztop']}]  "
          f"{STUDY_SPEC['segs']}x{STUDY_SPEC['loops']} flutes={STUDY_SPEC['flutes']}")
    print(f"gate: {clothdiag.BASELINE_ENVELOPE}\n")
    hdr = (f"{'fabric':<8} {'mass':>5} {'bend':>5} {'tens':>5} {'dmin':>6} {'settle':>6} | "
           f"{'pen_mm':>7} {'stretch':>8} {'compr':>7} {'v_mm/s':>7} | verdict")
    print(hdr)
    print("-" * len(hdr))
    for name, r, settle, rep, ok in rows:
        print(f"{name:<8} {r['mass']:>5.2f} {r['bending']:>5.1f} {r['tension']:>5} "
              f"{r['distance_min']:>6.3f} {settle:>6} | "
              f"{rep['settled_penetration_m']*1000:>7.1f} "
              f"{rep['rest_max_stretch']:>8.3f} "
              f"{rep['rest_min_compression']:>7.3f} "
              f"{rep['settle_speed_m_s']*1000:>7.1f} | "
              f"{'PASS' if ok else 'FAIL'}")
    print("\n(pen_mm = how far the settled cloth sits inside the body; "
          "v_mm/s = settle speed, is it actually at rest)")

    # ---- stitch a labelled strip (ImageMagick, if present) ----
    strip = os.path.join(OUT, "strip.png")
    try:
        labelled = []
        for name, png in zip(cloth.FABRICS, pngs):
            lp = os.path.join(OUT, f"_{name}_lbl.png")
            subprocess.run(["magick", png, "-gravity", "South", "-background", "#111",
                            "-splice", "0x34", "-gravity", "South", "-fill", "white",
                            "-pointsize", "26", "-annotate", "+0+4", name, lp], check=True)
            labelled.append(lp)
        subprocess.run(["magick"] + labelled + ["+append", "-bordercolor", "#111",
                        "-border", "6", strip], check=True)
        print("STRIP:", strip, flush=True)
    except Exception as e:
        print("montage skipped:", e, flush=True)

    print("\nFABRIC_STUDY_DONE", OUT)


main()
