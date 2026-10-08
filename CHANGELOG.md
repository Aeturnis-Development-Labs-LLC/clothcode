# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.y.z`, the public API (fabric recipes, function
signatures, metric envelopes) may change in a minor release.

## [Unreleased]

## [0.1.0] - 2026-10-08

### Added
- Procedural, headless cloth pipeline for Blender 5.2 (`cloth`, `clothdiag`,
  `clothtune`, `utils`).
- Four validated fabric recipes: `FABRIC`, `WOOL`, `LINEN`, `VELVET`.
- Measurement gate (`clothdiag`) with objective baseline + dynamic envelopes and a
  ray-cast parity penetration test.
- Self-calibration: `auto_settle`, `auto_tune`, and `motion_gate` (`clothtune`).
- Driver scripts: `showcase` (four fabrics / one motion), `fabric_study`,
  `anim`, `validate_garments`, `tune_demo`.
- Docs: `docs/pipeline.md` (design) and `docs/findings.md` (hard-won Blender 5.2
  facts, including the `rest_shape_key` dead end).

[Unreleased]: https://github.com/Aeturnis-Development-Labs/headless-cloth/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Aeturnis-Development-Labs/headless-cloth/releases/tag/v0.1.0
