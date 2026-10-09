"""
cloth - the reusable cloth pipeline.

Organised as the three layers we settled on:

  1. DRAPE SYNTHESIS   build garment geometry -> gravity relax -> canonical rest
  2. (analytic motion) cheap parameterised secondary motion  (see villager)
  3. FULL SOLVE        real simulation, baked for replay      (see twirl/dance)

and a measurement gate (clothdiag) that every garment must pass at rest before
any motion is attempted:  specify -> solve -> MEASURE -> accept/reject -> adjust.

------------------------------------------------------------------------------
FABRIC - the validated stable recipe
------------------------------------------------------------------------------
Found by working backwards from a known-good drape. The lessons, each the fix
for a real failure the diagnostics caught:

  * mass is PER-VERTEX: on a dense mesh it must stay small, or the cloth is
    absurdly heavy -> it sags through the body (penetration) and the solver
    struggles. 0.05 is sane for our resolutions.
  * self_distance (self-collision) must stay BELOW the mesh edge spacing, else
    neighbouring verts read as colliding and the solve explodes.
  * give the solver enough substeps (quality) and a sane body-collision margin
    (distance_min ~0.015), and self-collision + subdivision are both stable.
  * the garment needs a real BODY to drape over, and enough settle frames -
    trust settle_speed (from clothdiag.baseline) to say when it's actually done.
"""
import math
import bpy
import bmesh
import numpy as np
from mathutils import Vector
import utils as U
import clothdiag

TAU = 2 * math.pi
COL = "Cloth"

FABRIC = {
    "mass": 0.05,            # per-vertex (keep small on dense meshes)
    "tension": 20,
    "compression": 20,
    "shear": 5,
    "bending": 0.5,
    "air_damping": 1.0,
    "quality": 16,           # solver substeps
    "distance_min": 0.015,   # body-collision margin
    "self_collision": True,
    "self_distance": 0.006,  # MUST be < mesh edge spacing
    "collision_quality": 12,
}

# a heavier wool variant (needs a bigger margin so it doesn't sag through)
WOOL = dict(FABRIC, mass=0.09, bending=1.0, tension=30, compression=30, distance_min=0.02)

# LINEN - crisp and stiff: resists folding (high bending) so it holds a few
#   large sharp folds rather than many soft ones, and barely stretches (high
#   tension/shear). Light-to-medium weight. Stiffness can leave it slow to fully
#   settle, so give it more settle frames (see FABRICS table).
LINEN = dict(FABRIC, mass=0.06, tension=40, compression=40, shear=15,
             bending=3.0, distance_min=0.015)

# VELVET - heavy and soft: lots of mass with low bending, so it folds readily
#   into deep rounded drapes. Heavy fabric sags, so it needs a wider collision
#   margin or it pulls through the body (penetration).
VELVET = dict(FABRIC, mass=0.15, tension=15, compression=15, shear=3,
              bending=0.3, distance_min=0.025, quality=18)

# FITTED - a close-to-the-body margin for tailored garments. distance_min sets how
# far the fabric floats off the skin: 15mm -> ~9cm circumference slack, 8mm -> ~5cm.
# Smaller margin = more fitted but needs a smooth (not heavily decimated) collider.
FITTED = dict(FABRIC, distance_min=0.010, self_distance=0.004, quality=18,
              collision_quality=16)

# the study set, each with the settle budget its weight/stiffness needs
FABRICS = {
    "FABRIC": {"recipe": FABRIC, "settle": 90},
    "WOOL":   {"recipe": WOOL,   "settle": 110},
    "LINEN":  {"recipe": LINEN,  "settle": 120},   # stiff -> slower to relax
    "VELVET": {"recipe": VELVET, "settle": 150},   # heavy -> slower to settle
}


# ---------------------------------------------------------------------------
# Layer 1: garment geometry
# ---------------------------------------------------------------------------
def build_skirt(spec, collection=None):
    """A-line skirt from a GarmentSpec dict:
        r_waist, r_hem, ztop, zbot, segs, loops,
        flutes (int), amp_waist, amp_hem   (radial pleat depth, fraction of r)
    Returns (object, waist_vertex_indices); a 'Waist' pin group is created."""
    collection = collection or U.get_collection(COL)
    segs, loops = spec["segs"], spec["loops"]
    ztop, zbot = spec["ztop"], spec["zbot"]
    rw, rh = spec["r_waist"], spec["r_hem"]
    flutes = spec.get("flutes", 0)
    amp_w, amp_h = spec.get("amp_waist", 0.0), spec.get("amp_hem", 0.0)
    mesh = bpy.data.meshes.new("Skirt")
    obj = bpy.data.objects.new("Skirt", mesh)
    bm = bmesh.new()
    rings = []
    for li in range(loops + 1):
        t = li / loops
        z = zbot + (ztop - zbot) * t
        r = rh + (rw - rh) * t
        amp = amp_h + (amp_w - amp_h) * t
        ring = []
        for s in range(segs):
            a = TAU * s / segs
            rr = r * (1 + amp * math.cos(flutes * a)) if flutes else r
            ring.append(bm.verts.new((rr * math.cos(a), rr * math.sin(a), z)))
        rings.append(ring)
    for li in range(loops):
        for s in range(segs):
            s2 = (s + 1) % segs
            bm.faces.new((rings[li][s], rings[li][s2], rings[li + 1][s2], rings[li + 1][s]))
    bm.verts.index_update()
    waist = [v.index for v in rings[loops]]
    bm.to_mesh(mesh)
    bm.free()
    U.link(obj, collection)
    for p in mesh.polygons:
        p.use_smooth = True
    vg = obj.vertex_groups.new(name="Waist")
    vg.add(waist, 1.0, 'REPLACE')
    return obj, waist


def _ellipse_arc(ea, eb, a0, a1, n, samples=2000):
    """(x, y) for n+1 points from angle a0 to a1 spaced equally by ARC LENGTH.
    A flat panel top has uniform spacing; an ellipse sampled by uniform ANGLE bunches
    points at the high-curvature ends and spreads them at front/back, so sewing a
    uniform edge to it would compress the fabric at the sides and stretch it at the
    front. Equal-arc spacing makes the sewn edge map ~1:1 -> no spurious stretch."""
    dense = [(a0 + (a1 - a0) * k / samples) for k in range(samples + 1)]
    pts = [(ea * math.cos(t), eb * math.sin(t)) for t in dense]
    cum = [0.0]
    for k in range(1, len(pts)):
        cum.append(cum[-1] + math.dist(pts[k], pts[k - 1]))
    total = cum[-1]
    out, j = [], 0
    for i in range(n + 1):
        target = total * i / n
        while j < samples and cum[j + 1] < target:
            j += 1
        if j >= samples:
            out.append(pts[-1])
            continue
        seg = cum[j + 1] - cum[j]
        f = 0.0 if seg < 1e-12 else (target - cum[j]) / seg
        out.append((pts[j][0] + f * (pts[j + 1][0] - pts[j][0]),
                    pts[j][1] + f * (pts[j + 1][1] - pts[j][1])))
    return out


def _sew_waistband(bm, panels, edges_of, waist):
    """Build a pinned circular waistband ring and sew each panel's top edge to the
    matching arc (front panel y>=0 -> +Y arc, back panel y<0 -> -Y arc), sharing
    the two side points with the side seams. Returns the ring verts (to be pinned).
    Assumes one front + one back panel (the tube); generalises later for M3.
    waist may be a circle {radius, z} or an ellipse {a, b, z} - cut an elliptical
    waistband to the body's actual cross-section instead of a slack circle."""
    zr = waist["z"]
    ea = waist.get("a", waist.get("radius"))    # ellipse width semi-axis
    eb = waist.get("b", waist.get("radius"))    # ellipse depth semi-axis
    cx = waist.get("cx", 0.0)                   # torso cross-section centre (the body
    cy = waist.get("cy", 0.0)                   # isn't centred on the origin - e.g. the
    #   navel sits ~37mm behind it; a ring built at the origin leaves the back poking
    #   through the pinned loop, trapping the pin -> never settles -> explodes)
    fp = next(k for k, p in enumerate(panels) if p["center"][1] >= cy)
    bp = next(k for k, p in enumerate(panels) if p["center"][1] < cy)
    nu = panels[fp]["res"][0]
    # front arc: left (pi) -> right (0) via +Y;  back arc: left (pi) -> right (2pi)
    # via -Y.  Both sampled by equal arc length to match the uniform panel tops.
    front = _ellipse_arc(ea, eb, math.pi, 0.0, nu)
    back = _ellipse_arc(ea, eb, math.pi, 2 * math.pi, nu)
    Rf = [bm.verts.new((cx + x, cy + y, zr)) for x, y in front]
    Rb = [None] * (nu + 1)
    Rb[0], Rb[nu] = Rf[0], Rf[nu]               # side points shared with the seams
    for i in range(1, nu):
        Rb[i] = bm.verts.new((cx + back[i][0], cy + back[i][1], zr))
    # traverse the perimeter continuously: front arc left->right, then back arc
    # right->left. (range(1, nu) jumped across the ellipse -> long cross-edges.)
    loop = Rf + [Rb[i] for i in range(nu - 1, 0, -1)]
    for k in range(len(loop)):                  # closed ring edges (the waistband)
        try:
            bm.edges.new((loop[k], loop[(k + 1) % len(loop)]))
        except ValueError:
            pass
    for u, v in list(zip(edges_of[fp]["T"], Rf)) + list(zip(edges_of[bp]["T"], Rb)):
        if u is not v:                          # sew panel tops up to the ring
            try:
                bm.edges.new((u, v))
            except ValueError:
                pass
    return loop


def build_gored_skirt(waist, length, n_gores=8, hem_scale=1.6, cols=5, rows=26,
                      name="Skirt", collection=None):
    """A sewn GORED skirt: `n_gores` wedge panels joined edge-to-edge around an
    elliptical waistband, each flaring from its arc of the waist ellipse to
    `hem_scale` x that arc at the hem.

    Why gores beat two flat panels: two panels each have to bend ~180 degrees around
    the body, which buckles the fabric at the side seams (reads as heavy compression)
    and leaves no room between 'tight over the hips' (stretch) and 'too much hem'
    (folds). N gores each bend only 360/N degrees and spread the flare evenly. Each
    gore is PRE-CURVED to the body loft (it follows the waist ellipse at the top and a
    scaled ellipse at the hem) so it starts in-shape and barely deforms while settling.

    The top row is pinned (the waistband); adjacent gores are joined by loose sewing
    edges that start coincident and stay shut. Returns (obj, "Pin")."""
    collection = collection or U.get_collection(COL)
    ea = waist.get("a", waist.get("radius"))
    eb = waist.get("b", waist.get("radius"))
    cx, cy = waist.get("cx", 0.0), waist.get("cy", 0.0)
    zr = waist["z"]
    hz = zr - length
    N, C, R = n_gores, cols, rows
    total = N * C
    # equal-arc points around the full waist ellipse, relative to its centre; scaling
    # them by s about the centre keeps the same arc division at every row down.
    rel = _ellipse_arc(ea, eb, -math.pi, math.pi, total)[:total]

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bm = bmesh.new()
    gores = []                                  # per gore: grid[col][row] of BMVert
    pin_verts = []
    for g in range(N):
        grid = [[None] * (R + 1) for _ in range(C + 1)]
        for c in range(C + 1):
            bx, by = rel[(g * C + c) % total]   # point on the waist ellipse
            for r in range(R + 1):
                t = r / R                       # 0 = waist (top), 1 = hem (bottom)
                z = zr + (hz - zr) * t
                s = 1.0 + (hem_scale - 1.0) * t
                grid[c][r] = bm.verts.new((cx + bx * s, cy + by * s, z))
            pin_verts.append(grid[c][0])        # pin the whole top row (the waistband)
        for c in range(C):
            for r in range(R):
                bm.faces.new((grid[c][r], grid[c + 1][r],
                              grid[c + 1][r + 1], grid[c][r + 1]))
        gores.append(grid)
    # vertical seams: gore g's right column sews to gore g+1's left column (they start
    # coincident, so the seam is already closed - the gores are genuinely separate)
    for g in range(N):
        right, left = gores[g], gores[(g + 1) % N]
        for r in range(R + 1):
            u, v = right[C][r], left[0][r]
            if u is not v:
                try:
                    bm.edges.new((u, v))
                except ValueError:
                    pass
    bm.normal_update()                          # orient every face outward (radially)
    for f in bm.faces:
        ctr = f.calc_center_median()
        if f.normal.x * (ctr.x - cx) + f.normal.y * (ctr.y - cy) < 0:
            f.normal_flip()
    bm.verts.index_update()
    pin_idx = sorted({v.index for v in pin_verts})
    bm.to_mesh(mesh)
    bm.free()
    U.link(obj, collection)
    for p in mesh.polygons:
        p.use_smooth = True
    vg = obj.vertex_groups.new(name="Pin")
    vg.add(pin_idx, 1.0, 'REPLACE')
    return obj, "Pin"


def build_bodice(levels, z_top, z_hem, z_underarm, z_neck_front, z_neck_back,
                 ease=0.02, hn=0.13, hs=0.33, cols=24, rows=22,
                 name="Bodice", collection=None):
    """A fitted sleeveless bodice lofted from the measured torso cross-sections.

    Like the gored skirt it is pre-curved to the body loft (so it starts in-shape),
    but as a torso tube split into a front (+Y) and back (-Y) panel that follow the
    hip->waist->bust->shoulder ellipses. The top edge is SHAPED per column by its
    position u=c/C across the front arc (0 = left side, 0.5 = centre-front, 1 = right
    side), measured as d=|u-0.5|:
        d < hn           neckline  - scooped down to z_neck (front lower, back higher)
        hn <= d < hs     shoulder  - full height to z_top; F sews to B (shoulder seam)
        d >= hs          armhole   - top ramps from z_top down to z_underarm; the side
                                     seam closes only BELOW the underarm, leaving the
                                     armhole open above for a sleeve.
    The garment hangs from the pinned shoulder straps (they rest on the shoulders,
    offset out by `ease` so they clear the collider). Front/back meet coincident at the
    two side points, so the side seams start closed. Returns (obj, "Pin").

    levels: ascending-z list of (z, a, b, cx, cy) torso ellipses (semi-axes + centre).
    """
    collection = collection or U.get_collection(COL)
    C, R = cols, rows
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bm = bmesh.new()

    def loft(z):
        ls = levels
        if z <= ls[0][0]:
            return ls[0][1:]
        if z >= ls[-1][0]:
            return ls[-1][1:]
        for i in range(1, len(ls)):
            if z <= ls[i][0]:
                z0, z1 = ls[i - 1][0], ls[i][0]
                t = (z - z0) / (z1 - z0)
                return tuple(p + (q - p) * t for p, q in zip(ls[i - 1][1:], ls[i][1:]))

    def top_z(u, z_neck):
        d = abs(u - 0.5)
        if d < hn:                                      # neckline: rounded scoop rising
            tt = d / hn                                 # from z_neck (centre) to z_top
            s = tt * tt * (3 - 2 * tt)                  # smoothstep -> no square corner
            return z_neck + (z_top - z_neck) * s
        if d < hs:                                      # shoulder strap: flat plateau
            return z_top
        tt = (d - hs) / (0.5 - hs + 1e-9)               # armhole ramp to the underarm
        return z_top + (z_underarm - z_top) * tt

    panels = {}
    for side, a0, a1, z_neck in (("F", math.pi, 0.0, z_neck_front),
                                 ("B", math.pi, 2 * math.pi, z_neck_back)):
        grid = [[None] * (R + 1) for _ in range(C + 1)]
        for r in range(R + 1):
            z = z_hem + (z_top - z_hem) * (r / R)
            a, b, cx, cy = loft(z)
            arc = _ellipse_arc(a + ease, b + ease, a0, a1, C)
            for c in range(C + 1):
                if z <= top_z(c / C, z_neck) + 1e-6:
                    bx, by = arc[c]
                    grid[c][r] = bm.verts.new((cx + bx, cy + by, z))
        for c in range(C):
            for r in range(R):
                q = [grid[c][r], grid[c + 1][r], grid[c + 1][r + 1], grid[c][r + 1]]
                if all(q):
                    bm.faces.new(q)
        panels[side] = grid
    F, B = panels["F"], panels["B"]

    def top_vert(grid, c):
        for r in range(R, -1, -1):
            if grid[c][r] is not None:
                return grid[c][r]
        return None

    pin_verts = []
    for c in range(C + 1):                              # shoulder straps -> pins
        d = abs(c / C - 0.5)
        if hn <= d < hs:
            # Pin the front and back strap tops at the shoulder (they rest on the
            # shoulder, offset out by ease). Do NOT sew front-to-back here: at a
            # torso-level slice the front (+Y) and back (-Y) strap verts are ~80mm
            # apart with the shoulder/neck between them, so a sewing spring would drag
            # them together straight THROUGH the body and the solver never settles.
            # The pins hold each side up independently (a tank-strap shoulder).
            pin_verts += [v for v in (top_vert(F, c), top_vert(B, c)) if v is not None]
    for c in (0, C):                                    # side seams below the underarm
        for r in range(R + 1):
            z = z_hem + (z_top - z_hem) * (r / R)
            if z <= z_underarm + 1e-6 and F[c][r] and B[c][r] and F[c][r] is not B[c][r]:
                try:
                    bm.edges.new((F[c][r], B[c][r]))
                except ValueError:
                    pass

    bm.normal_update()
    for f in bm.faces:
        ctr = f.calc_center_median()
        _a, _b, cx, cy = loft(ctr.z)
        if f.normal.x * (ctr.x - cx) + f.normal.y * (ctr.y - cy) < 0:
            f.normal_flip()
    bm.verts.index_update()
    pin_idx = sorted({v.index for v in pin_verts})
    bm.to_mesh(mesh)
    bm.free()
    U.link(obj, collection)
    for p in mesh.polygons:
        p.use_smooth = True
    vg = obj.vertex_groups.new(name="Pin")
    vg.add(pin_idx, 1.0, 'REPLACE')
    return obj, "Pin"


def build_garment(panels, seams, pins=None, waist=None, name="Garment", collection=None):
    """Sew a garment from flat 2D panels (the general, pattern-based builder).

    panels: list of flat sheets, each a dict:
        w, h           - panel width and height
        res=(nu, nv)   - grid resolution (columns x rows)
        center=(x,y,z) - centre of the sheet in world space
        normal="Y"|"X" - axis the flat sheet faces; it spans the OTHER horizontal
                         axis (width) and Z (height)
        taper=1.0      - bottom-width / top-width (1.0 = rectangle)
    seams: list of (a, b) or (a, b, flip) where a, b = (panel_index, edge) and
        edge in {"L","R","T","B"}. The two edges' vertices are bridged one-for-one
        with LOOSE edges - the cloth sewing springs that pull the seam shut.
        `flip` reverses one side's vertex order.
    pins: list of (panel_index, edge) whose vertices form the 'Pin' group.

    Returns (obj, "Pin"). The caller adds a body collider + cloth with
    add_cloth(..., sew=True) and bakes; the seams close during the sim.
    """
    collection = collection or U.get_collection(COL)
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bm = bmesh.new()
    edges_of = []                       # per panel: {"L"/"R"/"T"/"B": [BMVert,...]}
    for p in panels:
        nu, nv = p["res"]
        w, h = p["w"], p["h"]
        cx, cy, cz = p["center"]
        normal = p.get("normal", "Y")
        taper = p.get("taper", 1.0)
        g = [[None] * (nv + 1) for _ in range(nu + 1)]
        for j in range(nv + 1):
            tv = j / nv                 # 0 = bottom, 1 = top
            z = cz - h / 2 + h * tv
            wj = w * (taper + (1 - taper) * tv)
            for i in range(nu + 1):
                off = wj * (i / nu - 0.5)
                co = (cx + off, cy, z) if normal == "Y" else (cx, cy + off, z)
                g[i][j] = bm.verts.new(co)
        # the base winding faces -Y (for normal 'Y') / +X (for 'X'); flip it so each
        # panel's normal points OUTWARD (away from the body centre) - a consistent
        # garment surface after sewing
        flip_winding = (cy >= 0) if normal == "Y" else (cx < 0)
        for i in range(nu):
            for j in range(nv):
                quad = (g[i][j], g[i + 1][j], g[i + 1][j + 1], g[i][j + 1])
                bm.faces.new(quad[::-1] if flip_winding else quad)
        edges_of.append({
            "L": [g[0][j] for j in range(nv + 1)],
            "R": [g[nu][j] for j in range(nv + 1)],
            "B": [g[i][0] for i in range(nu + 1)],
            "T": [g[i][nv] for i in range(nu + 1)],
        })
    # sewing: loose edges bridge matched seam vertices (no face uses them)
    for seam in seams:
        a, b = seam[0], seam[1]
        flip = seam[2] if len(seam) > 2 else False
        va = edges_of[a[0]][a[1]]
        vb = edges_of[b[0]][b[1]]
        if flip:
            vb = list(reversed(vb))
        for u, v in zip(va, vb):
            if u is not v:
                try:
                    bm.edges.new((u, v))
                except ValueError:
                    pass                # edge already exists
    # optional pinned waistband ring the panel tops sew up to
    ring = _sew_waistband(bm, panels, edges_of, waist) if waist else []

    bm.verts.index_update()
    pin_idx = [v.index for v in ring]
    for entry in (pins or []):
        pi, edge = entry[0], entry[1]
        drop = entry[2] if len(entry) > 2 else 0   # free this many verts at each end
        verts = edges_of[pi][edge]
        if drop:
            verts = verts[drop:len(verts) - drop]
        pin_idx += [v.index for v in verts]
    bm.to_mesh(mesh)
    bm.free()
    U.link(obj, collection)
    for poly in mesh.polygons:
        poly.use_smooth = True
    vg = obj.vertex_groups.new(name="Pin")
    if pin_idx:
        vg.add(sorted(set(pin_idx)), 1.0, 'REPLACE')
    return obj, "Pin"


def _collide(o, thickness=0.008):
    o.modifiers.new("Collision", 'COLLISION')
    o.collision.thickness_outer = thickness
    o.hide_render = True
    return o


def lower_body(spec, collection=None):
    """A pelvis/hip form + thighs sized to the skirt - the thing it hangs on.
    Every skirt needs this; a waistband pinned to empty air just collapses."""
    collection = collection or U.get_collection(COL)
    rw, ztop, zbot = spec["r_waist"], spec["ztop"], spec["zbot"]
    hip_z = ztop - 0.12
    # Hip snug under the waistband (so the fabric rests on it instead of sagging
    # in) but legs thin and close (so even a narrow skirt clears them).
    _collide(U.make_sphere("Hip", rw * 0.90, center=(0, 0, hip_z),
                           scale=(1.04, 0.74, 1.0), collection=collection))
    leg_r = max(0.042, rw * 0.33)
    top, bot = hip_z, zbot - 0.05
    for s in (-1, 1):
        _collide(U.make_cylinder(f"Thigh{s}", radius=leg_r, depth=(top - bot),
                                 center=(s * rw * 0.38, 0, (top + bot) / 2),
                                 verts=18, collection=collection))


def waist_bone(skirt, z_top, name="Driver", collection=None):
    """An armature whose single 'Waist' bone drives the skirt's waistband.
    Add this BEFORE add_cloth so the stack is Armature -> Cloth -> Subsurf: the
    cloth pin ('Waist') then follows the (spun / swayed) bone. Returns the pose
    bone; keyframe its rotation (local Y = vertical axis)."""
    collection = collection or U.get_collection(COL)
    ad = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, ad)
    U.link(arm, collection)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    b = ad.edit_bones.new("Waist")
    b.head = (0, 0, z_top)
    b.tail = (0, 0, z_top + 0.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    am = skirt.modifiers.new("Drive", 'ARMATURE')
    am.object = arm
    pb = arm.pose.bones["Waist"]
    pb.rotation_mode = 'XYZ'
    return arm, pb


# ---------------------------------------------------------------------------
# Layer 3: simulation
# ---------------------------------------------------------------------------
def add_cloth(obj, pin_group="Waist", fabric=FABRIC, rest_key=None, sew=False):
    cl = obj.modifiers.new("Cloth", 'CLOTH')
    s = cl.settings
    s.vertex_group_mass = pin_group
    s.pin_stiffness = 1.0
    if sew:
        # pull loose seam edges shut (sewing springs); 0 = uncapped force. Verified
        # functional headless in 5.2 (unlike rest_shape_key) - see docs/findings.md.
        s.use_sewing_springs = True
        s.sewing_force_max = 0.0
    if rest_key is not None:
        # use a separate shape key as the UNSTRETCHED reference, so the sim can
        # START from an already-draped Basis while keeping correct spring tension.
        # rest_key may be the ShapeKey itself or its name on this object.
        if isinstance(rest_key, str):
            rest_key = obj.data.shape_keys.key_blocks[rest_key]
        s.rest_shape_key = rest_key
    s.quality = fabric["quality"]
    s.mass = fabric["mass"]
    s.tension_stiffness = fabric["tension"]
    s.compression_stiffness = fabric["compression"]
    s.shear_stiffness = fabric["shear"]
    s.bending_stiffness = fabric["bending"]
    s.air_damping = fabric["air_damping"]
    cs = cl.collision_settings
    cs.distance_min = fabric["distance_min"]
    cs.collision_quality = fabric["collision_quality"]
    cs.use_self_collision = fabric["self_collision"]
    if fabric["self_collision"]:
        cs.self_distance_min = fabric["self_distance"]
        try:
            cs.self_friction = 0.0
        except Exception:
            pass
    sub = obj.modifiers.new("Subsurf", 'SUBSURF')
    sub.levels = sub.render_levels = 1
    return cl


def bake(obj, cl, frames):
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, frames
    cl.point_cache.frame_start = 1
    cl.point_cache.frame_end = frames
    scene.frame_set(1)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    with bpy.context.temp_override(scene=scene, active_object=obj, object=obj,
                                   point_cache=cl.point_cache):
        bpy.ops.ptcache.bake(bake=True)
    if not cl.point_cache.is_baked:
        raise RuntimeError("cloth bake failed")


# ---------------------------------------------------------------------------
# Step 0: the baseline gate
# ---------------------------------------------------------------------------
def drape_baseline(spec, settle=85, fabric=FABRIC):
    """Build garment on its body, settle under gravity, and return the baseline
    measurement. A garment must PASS this before any motion is authored."""
    U.clear_scene()
    collection = U.get_collection(COL)
    skirt, _waist = build_skirt(spec, collection)
    lower_body(spec, collection)
    cl = add_cloth(skirt, "Waist", fabric)
    bake(skirt, cl, settle)
    return clothdiag.baseline(skirt, settle, fps=25)


# ---------------------------------------------------------------------------
# Drape-once / branch-many: bake the gravity settle ONCE per (garment, fabric),
# save the settled rest, and start every motion from it (no settle pre-roll).
# ---------------------------------------------------------------------------
def _cloth_base_positions(obj):
    """World positions of the cloth-deformed BASE mesh (Subsurf disabled so the
    vertex count matches the pattern the sim actually solves)."""
    sub = obj.modifiers.get("Subsurf")
    old = sub.show_viewport if sub else None
    if sub:
        sub.show_viewport = False
    pos, _edges, _mask = clothdiag._eval_positions(obj)
    if sub:
        sub.show_viewport = old
    return pos


def settle_and_cache(spec, path, fabric=FABRIC, settle=140):
    """Drape the garment at the ORIGIN under gravity, bake, and save the settled
    base positions to `path` (.npy). Returns the baseline report so the caller
    can confirm the cached drape actually passed the gate."""
    U.clear_scene()
    collection = U.get_collection(COL)
    skirt, _waist = build_skirt(spec, collection)
    lower_body(spec, collection)
    cl = add_cloth(skirt, "Waist", fabric)
    bake(skirt, cl, settle)
    bpy.context.scene.frame_set(settle)
    settled = _cloth_base_positions(skirt)          # origin -> world == local
    np.save(path, settled)
    bpy.context.scene.frame_set(1)
    return clothdiag.baseline(skirt, settle, fps=25)


def apply_draped(skirt, settled, ox=0.0):
    """Make a freshly-built (flat) `skirt` START already draped.

    The subtlety: cloth's rest_shape_key is evaluated THROUGH the shape-key value
    mix, so we can't keep the displayed geometry and the rest reference on the
    same key. Instead:
      * Basis   = the FLAT pattern  -> used as rest_shape_key (correct tension)
      * 'Draped' key (value 1.0) = the cached settled shape -> what's displayed,
                                    i.e. where the sim STARTS.
    So the sim begins already draped yet still pulls toward the flat rest - no
    settle pre-roll, no sag-through. Call BEFORE add_cloth and pass the returned
    (Basis) key as rest_key. `ox` shifts both keys in x (edge lengths unchanged).
    """
    me = skirt.data
    basis = skirt.shape_key_add(name="Basis")       # Basis = FLAT = rest reference
    draped_key = skirt.shape_key_add(name="Draped")  # displayed = cached drape
    draped_key.value = 1.0
    off = Vector((ox, 0.0, 0.0))
    for i in range(len(me.vertices)):
        flat = me.vertices[i].co.copy() + off
        basis.data[i].co = flat
        draped_key.data[i].co = Vector((settled[i][0], settled[i][1], settled[i][2])) + off
        me.vertices[i].co = flat
    me.update()
    bpy.context.view_layer.update()      # flush shape-key edits before the sim reads them
    return basis
