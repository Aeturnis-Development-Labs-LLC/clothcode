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
          "waist": (0.63, 0.28), "navel": (0.59, 0.27), "hip": (0.52, 0.30),
          "knee": (0.28, 0.20)}


def _perimeter(a, b):
    """Ramanujan ellipse circumference."""
    return math.pi * (3 * (a + b) - math.sqrt(max(0.0, (3 * a + b) * (a + 3 * b))))


def _plane_segments(verts, faces, zc):
    """Line segments where the mesh crosses the horizontal plane z=zc, as (x,y)
    pairs (exact triangle/polygon-plane intersection)."""
    segs = []
    for f in faces:
        pts = []
        n = len(f)
        for k in range(n):
            a = verts[f[k]]
            b = verts[f[(k + 1) % n]]
            if (a[2] < zc) != (b[2] < zc):
                t = (zc - a[2]) / (b[2] - a[2])
                pts.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        if len(pts) == 2:
            segs.append((pts[0], pts[1]))
    return segs


def _components(segs, q=0.004):
    """Group segments into connected contours by endpoint proximity (union-find)."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def key(p):
        return (round(p[0] / q), round(p[1] / q))

    for p0, p1 in segs:
        parent[find(key(p0))] = find(key(p1))
    comps = {}
    for seg in segs:
        comps.setdefault(find(key(seg[0])), []).append(seg)
    return list(comps.values())


def _torso_section(verts, faces, zc):
    """Measure the TORSO cross-section at z=zc: split the plane-slice into contours,
    pick the central closed loop (ignoring separate arm/hand loops), and return its
    TRUE perimeter + half-width + half-depth + centre."""
    comps = _components(_plane_segments(verts, faces, zc))
    if not comps:
        return None

    def stats(c):
        pts = [p for s in c for p in s]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return {"per": sum(math.dist(p0, p1) for p0, p1 in c),
                "a": (max(xs) - min(xs)) / 2, "b": (max(ys) - min(ys)) / 2,
                "center": (sum(xs) / len(xs), sum(ys) / len(ys))}

    scored = [stats(c) for c in comps]
    central = [s for s in scored
               if (s["center"][0] ** 2 + s["center"][1] ** 2) ** 0.5 < 0.15]
    return max(central or scored, key=lambda s: s["per"])


def measure_body(b):
    """Tailoring measurements by true CONTOUR slicing of the CLEAN anatomy (not the
    remesh proxy, which inflates dimensions): at each landmark, intersect a
    horizontal plane with the torso and take the real perimeter + width + depth.
    Separate arm/hand loops are ignored, so an A-pose can't corrupt the waist."""
    verts = b.get("anatomy_verts")
    faces = b.get("anatomy_faces")
    if verts is None:                      # procedural body: slice its mesh directly
        obj = b["obj"]
        verts = [tuple(obj.matrix_world @ v.co) for v in obj.data.vertices]
        faces = [tuple(p.vertices) for p in obj.data.polygons]
    z0 = min(v[2] for v in verts)
    H = max(v[2] for v in verts) - z0
    M = {"stature": H, "z0": z0}
    for name, (frac, _cut) in LEVELS.items():
        zc = z0 + frac * H
        s = _torso_section(verts, faces, zc)
        M[name] = ({"z": zc, "a": 0.0, "b": 0.0, "circ": 0.0, "valid": False}
                   if s is None else
                   {"z": zc, "a": s["a"], "b": s["b"], "circ": s["per"],
                    "center": s["center"], "valid": True})
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


def tailor_skirt(M, col, level="navel", ease=0.025, hem_scale=1.6, length=0.48,
                 gores=8):
    """Draft a gored skirt cut to the body: an elliptical waistband at `level` (navel,
    where a skirt actually sits - lower + rounder than the anatomical waist) + ease,
    and `gores` pre-curved wedge panels flaring to `hem_scale` x the waist ellipse.

    `ease` is a RADIAL offset (metres): the waistband ellipse is (half-width+ease,
    half-depth+ease). A radial offset strictly ENCLOSES the true cross-section with a
    uniform clearance - the key property a perimeter-matched ellipse lacks (equal
    perimeter + different shape means the curves cross, so the body pokes through the
    pinned waist and traps it). The band is built at the measured torso CENTRE, not the
    origin (the navel sits ~37mm behind the model origin; ignoring that let the back
    poke through - the real cause of the earlier explosions). `hem_scale` 1.6 clears
    the hips (wider than the navel) and spreads the flare evenly over the gores so the
    fabric drapes smoothly instead of buckling into two folds."""
    L = M[level]
    cx, cy = L.get("center", (0.0, 0.0))
    wa, wb = L["a"] + ease, L["b"] + ease                    # enclosing waistband
    waist = dict(a=wa, b=wb, z=L["z"], cx=cx, cy=cy)
    skirt, pin = cloth.build_gored_skirt(waist, length, n_gores=gores,
                                         hem_scale=hem_scale, name="Skirt",
                                         collection=col)
    return skirt, pin


def tailor_tunic(M, col, ease=0.030):
    """Draft a fitted sleeveless bodice (Stage 1 of the tunic): loft the measured
    hip->waist->bust->shoulder torso ellipses into front/back panels, scoop a round
    neckline, close the side seams below the underarm, and hang it from pinned shoulder
    straps. Cap sleeves are added in Stage 2 onto the open armholes.

    The hem sits at the hip (just below the skirt's navel waistband, so it covers it);
    the underarm is taken at the bust level; the front neckline scoops ~55% of the way
    from the bust up to the shoulder, the back neckline sits just under the shoulder."""
    levels = []
    for name in ("hip", "waist", "bust", "shoulder"):
        m = M[name]
        cx, cy = m.get("center", (0.0, 0.0))
        levels.append((m["z"], m["a"], m["b"], cx, cy))
    z_top = M["shoulder"]["z"]
    z_hem = M["hip"]["z"]
    z_underarm = M["bust"]["z"]
    z_neck_front = z_underarm + 0.55 * (z_top - z_underarm)
    z_neck_back = z_top - 0.02
    tunic, pin = cloth.build_bodice(levels, z_top, z_hem, z_underarm,
                                    z_neck_front, z_neck_back, ease=ease,
                                    name="Tunic", collection=col)
    return tunic, pin


def _drape_and_gate(garment, pin, settle, label):
    """Shared drape recipe + baseline gate for any tailored garment."""
    drape = dict(cloth.FITTED, self_collision=False, quality=12, collision_quality=8)
    cl = cloth.add_cloth(garment, pin, drape, sew=True)
    cloth.bake(garment, cl, settle)
    rep = clothdiag.baseline(garment, settle, fps=25)
    ok, text = clothdiag.baseline_verdict(rep)
    print(text, flush=True)
    print(f"TAILOR_{label}_GATE", "PASS" if ok else "FAIL")
    return ok


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    def arg(f, d, c=str):
        return c(argv[argv.index(f) + 1]) if f in argv else d

    model = arg("--model", "mpfb")
    glb = arg("--glb", None)             # path to an imported body (.glb/.obj/.fbx)
    settle = arg("--settle", 150, int)
    samples = arg("--samples", 18, int)
    garment = arg("--garment", "skirt")  # skirt | tunic | both

    U.clear_scene()
    col = U.get_collection(cloth.COL)
    if glb:
        # a pre-made, already-posed MakeHuman model (T-pose, legs splayed) - just
        # import + voxel-remesh to the watertight collider; no posing needed
        b = body.load_body(glb, height=1.755, remesh=0.02, up='Z', collection=col)
    elif model == "mpfb":
        # T-pose the figure (arms out - the garment-fitting pose; keeps the hands
        # clear of the waist without touching the geometry), then voxel-remesh the
        # full closed human into a watertight smooth shell.
        b = body.mpfb_body(remesh=0.02, pose='tpose', collection=col)
    else:
        b = body.ansur_body("F", 50, collection=col)
    for p in b["parts"]:
        p.data.materials.append(flat("Skin", (0.80, 0.56, 0.44), 0.6))
    M = measure_body(b)
    print_measurements(M)

    if "--measure" in argv:
        print("MEASURE_DONE")
        return

    print(f"collider verts (remeshed): {len(b['obj'].data.vertices)}", flush=True)
    # drape recipe: fitted margin but NO self-collision and lower quality - a garment
    # draping on a body barely self-intersects, and self-collision is what made the
    # bake pathologically slow against a dense collider.
    if garment in ("skirt", "both"):
        skirt, spin = tailor_skirt(M, col)
        skirt.data.materials.append(flat("Skirt", (0.33, 0.12, 0.40)))
        _drape_and_gate(skirt, spin, settle, "SKIRT")
    if garment in ("tunic", "both"):
        tunic, tpin = tailor_tunic(M, col)
        tunic.data.materials.append(flat("Tunic", (0.18, 0.34, 0.52)))
        _drape_and_gate(tunic, tpin, settle, "TUNIC")

    look_z = 0.85 if garment == "skirt" else 1.05
    bpy.context.scene.frame_set(settle)
    U.add_ground(size=20, material=flat("Floor", (0.16, 0.16, 0.19), 0.9))
    U.setup_solid_world(color=(0.52, 0.55, 0.62), strength=1.0)
    U.add_sun(rotation_deg=(54, 0, 32), strength=2.8, angle_deg=3)
    U.add_sun(rotation_deg=(60, 0, -118), strength=0.8, angle_deg=5)
    U.add_camera(location=(1.9, -2.3, 1.15), look_at=(0, 0, look_z), lens=50)
    out = os.path.join(os.path.dirname(HERE), "renders", "body", f"tailor_{garment}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    U.setup_render(out, res=(620, 820), samples=samples, exposure=-0.3, look='AgX - Punchy')
    U.render()
    print("TAILOR_DONE", out)


main()
