# Contributing

Thanks for your interest! This is a procedural, **headless** Blender project — every
change should keep that property (no step should require the GUI).

## Dev setup

- Install **Blender 5.2** (the pipeline uses its bundled Python + numpy).
- Everything runs headless:

  ```bash
  BLENDER="/path/to/blender"
  "$BLENDER" --background --python src/showcase.py -- --motion twirl
  "$BLENDER" --background --python src/fabric_study.py
  "$BLENDER" --background --python src/tune_demo.py -- --part all
  ```

- Rendered frames and caches go to `renders/` (gitignored).

## Before you open a PR

- **The measurement gate is the contract.** If you change geometry, a recipe, or the
  solver setup, run `fabric_study.py` (or `drape_baseline` / `tune_demo`) and make sure
  garments still **PASS** the baseline gate. Don't tune by eye — tune to the numbers.
- Keep new helpers in `src/utils.py` general (no project-specific builders).
- Read [`docs/findings.md`](docs/findings.md) first — it will save you re-discovering
  Blender's sharp edges (e.g. `rest_shape_key` is a dead end).
- CI must be green (`ruff` error-check + byte-compile). Style is not enforced; real
  errors are.

## Branching & versioning

- Work on a feature branch; open a PR against `main`. `main` is protected (see below).
- **Semantic Versioning.** While we're `0.y.z`, breaking changes (recipe values,
  function signatures, metric envelopes) bump the **minor**; fixes bump the **patch**.
- Add an entry under `## [Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md) with your PR.
- Releases are git tags `vX.Y.Z` cut from `main`.

## Maintainer notes — enabling branch protection

Run once after the repo exists on GitHub (requires `gh` and admin). Requires PRs,
a passing CI check, and blocks force-pushes to `main`:

```bash
gh api -X PUT repos/Aeturnis-Development-Labs-LLC/clothcode/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=lint + compile' \
  -f 'enforce_admins=true' \
  -F 'required_pull_request_reviews[required_approving_review_count]=1' \
  -f 'restrictions=' \
  -f 'allow_force_pushes=false' \
  -f 'allow_deletions=false'
```

(Or set the same via **Settings → Branches → Add rule** in the GitHub UI: require a
pull request, require the `lint + compile` status check, and disallow force pushes.)
