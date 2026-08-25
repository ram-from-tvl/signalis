## Summary

<!-- What does this change do, and why? One to three sentences or bullets. -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor (no behavior change)
- [ ] Documentation
- [ ] Chore / tooling

## Test plan

<!-- How did you verify this? Be specific — commands run, endpoints hit,
     screens checked. "Tests pass" alone is not enough for a UI or
     behavior change; describe what was actually exercised. -->

- [ ] `pytest -m "not integration" -q` passes (backend changes)
- [ ] `ruff check app tests` clean (backend changes)
- [ ] `npx eslint .` and `npx tsc --noEmit` clean (frontend changes)
- [ ] Manually verified in a running app (describe below), for any
      user-visible change

## Related documentation

<!-- Does this change make any existing doc (README, docs/API.md,
     docs/DATA_SCHEMA.md, ARCHITECTURE.md, backend/README.md,
     frontend/README.md) inaccurate? Link the update, or note N/A. -->

## Checklist

- [ ] This PR does one focused thing (not a bundle of unrelated changes)
- [ ] I've added/updated tests for any behavior change
- [ ] I've updated documentation affected by this change
- [ ] I've run the local lint + test checks listed above
