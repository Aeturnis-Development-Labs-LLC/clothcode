# headless-cloth

A **fully procedural, headless cloth pipeline** for Blender 5.2 — garments, fabrics,
and motion are produced entirely by Python with no GUI, no hand-modeling, and no
keyframing. Quality isn't eyeballed: every garment has to pass an **objective
measurement gate** before it's allowed to move, and the system **tunes itself** to
meet that bar.

Built with [Claude Code](https://claude.com/claude-code) as the engineer.

Two principles the whole thing rests on:

- **Procedural + headless** — every vertex, pose, material, and frame comes from
  code (`blender --background --python …`). Reproducible, parameterized, no artist
  in the loop.
- **Measured, not eyeballed** — a garment is dropped under gravity, baked to rest,
  and *measured* (body penetration, stretch, did it actually settle). It passes or
  fails against fixed limits before any motion is authored.

---

## The pipeline

```
parameters → geometry → drape → MEASURE → (auto-tune) → drive motion → bake → render
```

1. **Geometry from parameters.** A garment is a dict of numbers (waist/hem radius,
   height, pleat depth). `cloth.build_skirt(spec)` turns it into a mesh; the body it
   drapes on (hip + legs) is generated from the *same* spec so it always fits.
2. **A fabric is physics params.** `FABRIC / WOOL / LINEN / VELVET` are four recipes
   (mass, tension, bending, collision margin). Every difference in how they move is
   those numbers.
3. **Drape → measure.** `cloth.drape_baseline(spec, fabric)` settles the garment and
   returns objective metrics; `clothdiag` grades them against an envelope.
4. **Self-calibration.** `clothtune.auto_settle` finds the real settle time by
   scanning for convergence; `clothtune.auto_tune` reads the failing metric and
   nudges the fixing param until the gate passes.
5. **Drive the motion.** A pose bone (twirl/dance), an animated collider (kick), or a
   force field (wind) drives the *validated* drape. Bake the cloth sim to a cache.
6. **Render.** Cycles renders the baked frames (CPU by default).

---

## Modules

| file | role |
|------|------|
| `src/cloth.py` | the pipeline: garment + body geometry, fabric recipes, cloth setup, bake, drape-baseline |
| `src/clothdiag.py` | the measurement layer: objective metrics + accept/reject envelopes |
| `src/clothtune.py` | self-calibration: `auto_settle`, `auto_tune`, `motion_gate` |
| `src/utils.py` | low-level Blender helpers (primitives, scene, lights, render) |
| `src/showcase.py` | 4 fabrics side-by-side, one shared motion (twirl/dance/kick/wind) |
| `src/fabric_study.py` | run every recipe through the baseline gate, tabulated |
| `src/anim.py` | single-garment twirl/dance/march/wind clips |
| `src/validate_garments.py` | regression-check garment baselines |
| `src/tune_demo.py` | demo of auto-settle + auto-tune + motion-gate |

See [`docs/pipeline.md`](docs/pipeline.md) for the measurement-gate design and the
fabric recipes, and [`docs/findings.md`](docs/findings.md) for hard-won Blender-5.2
cloth facts (each was a real bug the metrics caught).

---

## Running it

Requires **Blender 5.2** (uses `numpy`, which ships with Blender). Everything runs
headless:

```bash
BLENDER="/path/to/blender"      # e.g. "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"

# four fabrics, one motion, side by side
"$BLENDER" --background --python src/showcase.py -- --motion twirl   # or dance | kick | wind

# run every recipe through the baseline gate
"$BLENDER" --background --python src/fabric_study.py

# watch the pipeline calibrate and repair itself
"$BLENDER" --background --python src/tune_demo.py -- --part all
```

Rendered frames / caches are written under `renders/` (gitignored).

---

## Fabric recipes

| recipe | character | mass | bending |
|--------|-----------|-----:|--------:|
| `FABRIC` | light cotton | 0.05 | 0.5 |
| `WOOL` | heavier, fuller | 0.09 | 1.0 |
| `LINEN` | crisp, holds sharp folds | 0.06 | 3.0 |
| `VELVET` | heavy, deep soft folds | 0.15 | 0.3 |

All four pass the same gate; the recipe is stable across a 3× weight and 10×
stiffness range as long as `distance_min` scales with mass and the settle budget
scales with mass + stiffness (`auto_settle` handles the latter). `rest_min_compression`
turns out to read fabric stiffness as a single number.

---

## Limitations

- **CPU-rendered** — bakes and renders are slow.
- **Colliders are primitives** (hip sphere + leg cylinders), not a real body yet.
- **Wind is under-tuned** — the forcing is gentle, and a wide skirt won't *conform*
  to thin legs (garment geometry, not the solver).
- Blender's `rest_shape_key` is **non-functional headless in 5.2**, which blocked a
  drape-cache optimization — see `docs/findings.md`.

## Contributing

Issues and PRs welcome — the obvious next steps are sewing-seam garments (flat
panels → fitted 3D), real body colliders, GPU rendering, and extending the gate to
full motion clips. `docs/findings.md` will save you re-discovering the sharp edges.

## License

MIT — see [`LICENSE`](LICENSE).
