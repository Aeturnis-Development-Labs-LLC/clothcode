# Hard-won Blender 5.2 cloth facts

Each of these was a real bug the measurement gate caught, or a Blender behaviour that
cost real time to pin down. They'll save you re-discovering the sharp edges.

### Simulation

- **Always drape + measure the baseline BEFORE animating.** Motion hides an unstable
  or unsettled rest state. Every early animation here was built on an invalid rest.
- **Frame-stepping does NOT drive cloth in `--background`.** You must bake with
  `bpy.ops.ptcache.bake(bake=True)` inside a `temp_override(scene=..., active_object=
  ..., object=..., point_cache=cl.point_cache)`.
- **Cloth `mass` is per-vertex.** Keep ~0.05 on dense meshes or it's absurdly heavy
  and sags through the body.
- **Self-collision was a red herring.** The crumple that looked like self-collision
  came from heavy mass + too-few settle frames + too-tight collision margin. With a
  sane recipe, self-collision *and* subdivision are both stable.
- **`self_distance_min` must be below the mesh edge spacing**, or neighbours read as
  colliding and the solve explodes.
- **Measure the base sim mesh, not the subsurf'd mesh** — subsurf yields bogus ~2.7
  stretch artifacts.
- A skirt needs a **real body** (hip + legs) to drape over; a waistband pinned to
  empty air just collapses.

### Driving deformation

- **A pose bone drives the cloth; the armature object does not.** Rotate the pose
  bone about its local Y (vertical) for a twirl. Rotating the armature object leaves
  the cloth undeformed.
- Fast twirl wants a heavier fabric (`WOOL`); light `FABRIC` flings flat instantly.
- An axisymmetric skirt looks static while spinning — add flutes (symmetry-breaking
  radial pleats) so the motion reads.

### Force fields

- **WIND strength is not physical units.** Cloth at human scale needs values in the
  hundreds–thousands; `flow` leverages air-drag and carries much more force than the
  raw push. A wide skirt over thin central legs won't *conform* to them (the fabric
  is simply too far out) — you need a narrow skirt + fuller legs.

### Penetration metric

- The inside-test must be a **ray-cast parity test** (count crossings; odd = inside),
  not nearest-face-normal, which gives false positives near edges / end-caps (it once
  reported an impossible 0.185 m).
- A garment's "penetration" can be a **slow creep** that only appears after ~100+
  settle frames (an invisible inner-back contact). Renders can look clean while the
  number is non-zero — and stopping the settle early can incidentally avoid it.

### Dead ends (proven — don't re-chase)

- **`ClothSettings.rest_shape_key` is non-functional headless in 5.2.** An isolation
  test (gravity off, rest key shrunk to 0.4) showed zero effect. The sim always uses
  its *start* geometry as the rest, so you can't start a cloth already-draped while
  keeping flat-rest tension — a draped start goes slack and sinks into the body. This
  blocked a "drape once, branch many" cache; the working substitute is `auto_settle`.
- **`object.shape_key_add` creates a relative key with `value = 1.0`, not 0.** A new
  key therefore overrides the Basis in the evaluated mesh until you zero its `value`.

### 5.2 API changes that bit us

- Sky: `NISHITA` → `MULTIPLE_SCATTERING`.
- `Mesh.use_auto_smooth` removed — set `polygon.use_smooth` directly.
- `Action.fcurves` removed in favour of the channelbag API.
- Particle hair dynamics are effectively gone — use native Curves instead.
