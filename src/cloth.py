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
    fp = next(k for k, p in enumerate(panels) if p["center"][1] >= 0)
    bp = next(k for k, p in enumerate(panels) if p["center"][1] < 0)
    nu = panels[fp]["res"][0]
    Rf = []
    for i in range(nu + 1):
        a = math.pi * (1 - i / nu)              # left (pi) -> right (0) via +Y
        Rf.append(bm.verts.new((ea * math.cos(a), eb * math.sin(a), zr)))
    Rb = [None] * (nu + 1)
    Rb[0], Rb[nu] = Rf[0], Rf[nu]               # side points shared with the seams
    for i in range(1, nu):
        a = math.pi * (1 + i / nu)              # left (pi) -> right (2pi) via -Y
        Rb[i] = bm.verts.new((ea * math.cos(a), eb * math.sin(a), zr))
    loop = Rf + [Rb[i] for i in range(1, nu)]
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
        for i in range(nu):
            for j in range(nv):
                bm.faces.new((g[i][j], g[i + 1][j], g[i + 1][j + 1], g[i][j + 1]))
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
