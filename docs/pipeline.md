# Pipeline design

The pipeline is organized as three layers plus a measurement gate that sits in
front of all of them.

```
specify → solve → MEASURE → accept/reject → adjust
```

## Layer 1 — drape synthesis

Build the garment geometry flat (`cloth.build_skirt`), give it a body to hang on
(`cloth.lower_body` — a hip sphere + two leg cylinders sized to the garment), add a
cloth modifier with a fabric recipe, and relax it under gravity to a canonical rest.
A garment pinned to empty air just collapses — it needs a real body to drape over.

## Layer 2 — analytic / secondary motion

Cheap parameterized motion (shape-key surrogates) for cases that don't need a full
solve. Present for completeness; the showcase uses Layer 3.

## Layer 3 — full solve

A real cloth simulation, driven by a pose bone / animated collider / force field and
baked to a point cache for replay. **Frame-stepping does not advance a cloth sim in
`--background`** — you must bake with `bpy.ops.ptcache.bake(bake=True)` under a
`temp_override(point_cache=...)`.

## The measurement gate (`clothdiag`)

Before any motion, the settled drape is measured against an envelope:

| metric | bound | meaning |
|--------|-------|---------|
| `settled_penetration_m` | ≤ 0.012 | cloth rests *outside* the body |
| `rest_max_stretch` | ≤ 1.30 | relaxed drape barely stretches |
| `rest_min_compression` | ≥ 0.55 | not bunching pathologically |
| `settle_speed_m_s` | ≤ 0.05 | actually reached equilibrium |

Penetration uses a **ray-cast parity inside-test** (count surface crossings along a
ray; odd = inside). Nearest-face-normal gives false positives near sharp edges and
end-caps. Always measure the **base sim mesh**, not the subsurf'd mesh — subsurf
yields bogus ~2.7 stretch artifacts.

`clothdiag.measure()` adds dynamic metrics (vertex speed/accel, hem speed, loop
discontinuity) for grading a baked *clip*, not just the rest state.

## Fabric recipes (`cloth.FABRIC`, `WOOL`, `LINEN`, `VELVET`)

A recipe is the physics: `mass, tension, compression, shear, bending, air_damping,
quality, distance_min, self_collision, self_distance, collision_quality`.

Lessons baked into the recipe, each the fix for a real failure the metrics caught:

- **`mass` is per-vertex.** On a dense mesh keep it small (~0.05) or the cloth is
  absurdly heavy and sags through the body.
- **`self_distance` must be below the mesh edge spacing**, or neighbouring verts read
  as colliding and the solve explodes.
- **`distance_min` scales with mass** (0.015 light → 0.025 heavy), or heavy fabric
  sags through the body.
- The recipe is stable across **mass 0.05→0.15 and bending 0.3→3.0** given the above.
- `rest_min_compression` reads stiffness: high (~0.95, linen) = crisp/barely bunches;
  low (~0.77, velvet) = soft/bunches most.

## Self-calibration (`clothtune`)

- **`auto_settle(spec, fabric)`** — bake the drape once to a generous max, then scan
  the cached frames for the convergence frame F (earliest frame whose max vertex
  speed stays below a threshold). Replaces hand-tuned settle budgets, which were
  2–4× too long.
- **`auto_tune(spec, fabric)`** — read the gate's dominant failing metric and nudge
  the param that fixes it (penetration → `distance_min` up + `mass` down; stretch →
  `tension` up; bunching → `bending` up); re-solve until it passes.
- **`motion_gate(cloth_obj, frames)`** — run the dynamic metrics on a baked clip and
  return an ACCEPT/REJECT, so a *moving* garment gets an objective verdict too.

## Driving motion

The driver is always math, authored over the post-settle frames (flat during the
settle pre-roll):

- **twirl / dance** — an armature **pose bone** rotating about its local Y (vertical).
  Rotating the armature *object* does **not** deform the cloth; the pose bone does.
- **kick** — an animated collider (a knee pivot) pushing into the fabric.
- **wind** — a `WIND` force field. Strength is **not** in physical units; cloth at
  this scale needs values in the hundreds–thousands, and `flow` carries much more
  force via air-drag.
