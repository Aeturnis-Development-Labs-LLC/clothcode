"""
M1 for #1 (sewing-seam garments): sew a TUBE from two flat panels.

A flat front panel and a flat back panel, bridged by loose sewing edges down both
side seams, pinned along the top, with a torso collider between them. The cloth
sewing springs pull the side seams shut around the body; the result goes straight
through the baseline measurement gate (which now ignores the loose seam edges).

  blender --background --python src/garment_demo.py -- [--settle N] [--samples N]
"""
import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy
import utils as U
import cloth
import clothdiag


def flat(name, rgb, rough=0.85):
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    return mat


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    settle = int(argv[argv.index("--settle") + 1]) if "--settle" in argv else 120
    samples = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 24

    U.clear_scene()
    col = U.get_collection(cloth.COL)

    ztop, zbot = 0.95, 0.30
    zmid, h = (ztop + zbot) / 2, ztop - zbot
    rb = 0.14              # torso radius
    d = rb + 0.015         # front/back panel offset
    w = 0.50               # panel width (2*w ≈ wrap circumference with ease)
    res = (24, 30)

    panels = [
        dict(name="Front", w=w, h=h, res=res, center=(0, +d, zmid), normal="Y"),
        dict(name="Back",  w=w, h=h, res=res, center=(0, -d, zmid), normal="Y"),
    ]
    seams = [((0, "L"), (1, "L")), ((0, "R"), (1, "R"))]   # both side seams
    # hang the garment from a pinned circular waistband; the flat panel tops sew
    # UP to the ring (radius = panel width / pi, so the wrap length matches)
    waist = dict(radius=w / math.pi, z=ztop)
    garment, pin = cloth.build_garment(panels, seams, pins=None, waist=waist,
                                       name="Tube", collection=col)
    garment.data.materials.append(flat("Cloth", (0.22, 0.28, 0.45)))

    cloth._collide(U.make_cylinder("Torso", radius=rb, depth=h,
                                   center=(0, 0, zmid), verts=24, collection=col))

    cl = cloth.add_cloth(garment, pin, cloth.FABRIC, sew=True)
    cloth.bake(garment, cl, settle)

    rep = clothdiag.baseline(garment, settle, fps=25)
    ok, text = clothdiag.baseline_verdict(rep)
    print(text, flush=True)
    print("GARMENT_GATE", "PASS" if ok else "FAIL")

    bpy.context.scene.frame_set(settle)
    U.add_ground(size=20, material=flat("Floor", (0.15, 0.15, 0.18)))
    U.setup_solid_world(color=(0.5, 0.53, 0.6), strength=1.0)
    U.add_sun(rotation_deg=(52, 0, 35), strength=2.8, angle_deg=3)
    U.add_sun(rotation_deg=(58, 0, -120), strength=0.8, angle_deg=5)
    U.add_camera(location=(1.7, -2.0, 0.95), look_at=(0, 0, 0.6), lens=46)
    out = os.path.join(os.path.dirname(HERE), "renders", "garment", "tube.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    U.setup_render(out, res=(560, 720), samples=samples, exposure=-0.3, look='AgX - Punchy')
    U.render()
    print("GARMENT_DONE", out)


main()
