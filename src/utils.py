"""
Low-level Blender helpers for the headless cloth pipeline.

Scene/collection plumbing, a few procedural primitives (box / cylinder / sphere),
and camera / light / world / render setup - everything the cloth modules need to
build a scene and render it entirely from code, with no GUI.
"""
import math
import datetime
import bpy
import bmesh
from mathutils import Vector, Euler


def timestamp():
    """Filesystem-safe local timestamp for unambiguous output filenames."""
    return datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")


# ---------------------------------------------------------------------------
# Scene / collection management
# ---------------------------------------------------------------------------
def clear_scene():
    """Wipe all objects, meshes, materials so re-runs start clean."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                  bpy.data.cameras, bpy.data.images):
        for b in list(block):
            if b.users == 0:
                block.remove(b)


def get_collection(name):
    """Fetch or create a named collection linked to the scene."""
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def link(obj, collection):
    """Link obj into the given collection (and nowhere else)."""
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    if isinstance(collection, str):
        collection = get_collection(collection)
    collection.objects.link(obj)
    return obj


# ---------------------------------------------------------------------------
# Primitive geometry
# ---------------------------------------------------------------------------
def make_box(name, size, center=(0, 0, 0), rot=(0, 0, 0),
             collection=None, material=None, bevel=0.0, bevel_seg=2):
    """A box of exact `size` (x,y,z) centred at `center`, optionally rotated."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    obj.location = center
    obj.rotation_euler = Euler(rot, 'XYZ')
    if collection is not None:
        link(obj, collection)
    else:
        bpy.context.scene.collection.objects.link(obj)
    if bevel and bevel > 0:
        add_bevel(obj, bevel, bevel_seg)
    if material is not None:
        set_material(obj, material)
    return obj


def make_frustum(name, r1, r2, depth, center=(0, 0, 0), rot=(0, 0, 0),
                 verts=24, collection=None, material=None, scale=(1, 1, 1),
                 cap_ends=True):
    """A cone/tube with radius r1 at -Z end and r2 at +Z end.
    cap_ends=False leaves both ends open (a shell - e.g. a garment)."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=cap_ends, segments=verts,
                          radius1=r1, radius2=r2, depth=depth)
    if scale != (1, 1, 1):
        bmesh.ops.scale(bm, vec=Vector(scale), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    obj.location = center
    obj.rotation_euler = Euler(rot, 'XYZ')
    if collection is not None:
        link(obj, collection)
    else:
        bpy.context.scene.collection.objects.link(obj)
    if material is not None:
        set_material(obj, material)
    return obj


def make_cylinder(name, radius, depth, center=(0, 0, 0), rot=(0, 0, 0),
                  verts=16, collection=None, material=None):
    return make_frustum(name, radius, radius, depth, center, rot, verts,
                        collection, material)


def make_sphere(name, radius, center=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1),
                segs=28, rings=18, collection=None, material=None, smooth=True):
    """A UV sphere, optionally squashed by `scale`."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=radius)
    except TypeError:
        bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, diameter=radius)
    if scale != (1, 1, 1):
        bmesh.ops.scale(bm, vec=Vector(scale), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    obj.location = center
    obj.rotation_euler = Euler(rot, 'XYZ')
    if collection is not None:
        link(obj, collection)
    else:
        bpy.context.scene.collection.objects.link(obj)
    if material is not None:
        set_material(obj, material)
    if smooth:
        shade_smooth(obj)
    return obj


# ---------------------------------------------------------------------------
# Modifiers / finishing
# ---------------------------------------------------------------------------
def add_bevel(obj, width=0.01, segments=2):
    m = obj.modifiers.new("Bevel", 'BEVEL')
    m.width = width
    m.segments = segments
    m.limit_method = 'ANGLE'
    m.angle_limit = math.radians(40)
    m.harden_normals = True
    return m


def set_material(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)


def shade_smooth(obj, angle=30):
    for p in obj.data.polygons:
        p.use_smooth = True


# ---------------------------------------------------------------------------
# Camera / light / world / render
# ---------------------------------------------------------------------------
def add_camera(location, look_at, lens=38, name="Camera"):
    cam_data = bpy.data.cameras.new(name)
    cam_data.lens = lens
    cam = bpy.data.objects.new(name, cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = location
    direction = Vector(look_at) - Vector(location)
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = cam
    return cam


def add_sun(rotation_deg=(55, 0, 35), strength=3.5, angle_deg=2.0):
    light = bpy.data.lights.new("Sun", 'SUN')
    light.energy = strength
    light.angle = math.radians(angle_deg)   # soft shadows
    obj = bpy.data.objects.new("Sun", light)
    bpy.context.scene.collection.objects.link(obj)
    obj.rotation_euler = Euler([math.radians(a) for a in rotation_deg], 'XYZ')
    return obj


def setup_solid_world(color=(0.62, 0.63, 0.66), strength=1.0):
    """A flat, neutral environment light - a 'studio' backdrop."""
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg.inputs["Color"].default_value = (*color, 1.0)
    bg.inputs["Strength"].default_value = strength
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return world


def add_text(body, location, size=0.22, rot=(math.radians(90), 0, 0),
             material=None, collection=None, align='CENTER'):
    cur = bpy.data.curves.new(body, 'FONT')
    cur.body = body
    cur.size = size
    cur.align_x = align
    cur.align_y = 'CENTER'
    cur.extrude = 0.004
    obj = bpy.data.objects.new("lbl_" + body, cur)
    obj.location = location
    obj.rotation_euler = Euler(rot, 'XYZ')
    if collection is not None:
        link(obj, collection)
    else:
        bpy.context.scene.collection.objects.link(obj)
    if material is not None:
        obj.data.materials.append(material)
    return obj


def add_ground(size=60, material=None):
    make_box("Ground", (size, size, 0.1), center=(0, 0, -0.05),
             collection="Environment", material=material, bevel=0)


def set_view(transform='AgX', look='None', exposure=0.0):
    vs = bpy.context.scene.view_settings
    try:
        vs.view_transform = transform
    except Exception:
        pass
    try:
        vs.look = look
    except Exception:
        pass
    vs.exposure = exposure


def setup_render(filepath, res=(1280, 720), samples=48, engine='CYCLES',
                 exposure=-0.4, look='None'):
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.filepath = filepath
    scene.render.image_settings.file_format = 'PNG'
    set_view('AgX', look, exposure)
    if engine == 'CYCLES':
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
        try:
            scene.cycles.device = 'CPU'
        except Exception:
            pass
    return scene


def render(filepath=None):
    if filepath:
        bpy.context.scene.render.filepath = filepath
    bpy.ops.render.render(write_still=True)
