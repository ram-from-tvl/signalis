# Contributing to Signalis

Thanks for taking the time to contribute. This document covers everything
you need to get a change from idea to merged.

## Before you start

- For a bug fix or small change, feel free to open a pull request directly.
- For a new feature or a larger refactor, please open an issue first
  describing what you want to do and why, so we can align on approach before
  you invest the time.
- Check open issues and pull requests to avoid duplicate work.

## Development setup

Follow [README.md](README.md#setup-from-a-clean-checkout) for the full
first-time setup (environment variables, TrueForge harness, MCP server,
backend, frontend). The short version once your `.env` is in place:

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings \
  python-dotenv python-multipart langgraph google-genai daytona mcp pytest httpx ruff
uvicorn app.main:app --reload

# Frontend, in a second terminal
cd frontend
npm install
npm run dev
```

## Making a change

1. Create a branch off `main`: `git checkout -b feat/short-description` (or
   `fix/`, `refactor/`, `docs/` — see [commit and branch conventions](#commit-and-branch-conventions)
   below).
2. Make your change. Keep it scoped — a pull request that does one thing is
   easier to review and much easier to revert if something goes wrong.
3. Add or update tests for any behavior change. See [Testing](#testing).
4. Update relevant documentation in the same pull request — a code change
   that makes `docs/API.md`, `docs/DATA_SCHEMA.md`, or a README inaccurate
   should update it, not leave it stale for someone else to notice later.
5. Run the full local check before opening a pull request:

   ```bash
   (cd backend && ruff check app tests && pytest -m "not integration" -q)
   (cd frontend && npx eslint . && npx tsc --noEmit)
   ```

6. Push your branch and open a pull request against `main`.

## Testing

```bash
cd backend
source venv/bin/activate
pytest -m "not integration" -q      # fast unit + API tests, LLM calls mocked
pytest -m integration -q            # real end-to-end Gemini call, needs GEMINI_API_KEY
```

A pull request that changes backend behavior should include a test that
would have failed before the change. A pull request that only refactors
structure (no behavior change) should show the existing suite passing
unmodified in assertions.

## Linting

```bash
(cd backend && ruff check app tests)
(cd frontend && npx eslint . && npx tsc --noEmit)
```

Both are enforced in CI (`.github/workflows/code-review.yml`) and will block
merge if they fail.

## Commit and branch conventions

Branch names are prefixed by intent: `feat/`, `fix/`, `refactor/`, `docs/`,
`chore/`, `test/`. Commit messages follow a short imperative summary line —
`fix: prevent code injection in generated sandbox script`,
`feat: add prioritization agent` — with an optional body explaining *why*
when the reasoning isn't obvious from the diff alone.

## Code review

Every pull request runs through automated review before merge:

- **CI** (`.github/workflows/code-review.yml`) runs backend lint (`ruff`)
  and the backend test suite, and runs frontend lint (`eslint`, up to 20
  warnings tolerated before failing) and a production build. There is no
  frontend test suite yet — a frontend behavior change is verified by the
  tests described in [Testing](#testing) plus manual verification, not CI.
  Any of these steps failing blocks merge.
- **Qodo Merge** reviews every pull request automatically on open and on
  each new commit, posting both a structured description and inline
  findings. Address real findings with an actual code fix (not just a
  reply), reply to the thread referencing the fixing commit, and resolve
  the thread once addressed. A finding that doesn't apply (false positive,
  out of scope) should still get a reply explaining why, rather than being
  silently resolved.
- `main` is a protected branch: direct pushes are blocked, and a pull
  request needs its checks green and its review threads resolved before it
  can merge.

## Project structure

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for a system-level overview, or
jump straight to [backend/README.md](backend/README.md) and
[frontend/README.md](frontend/README.md) for component-level detail.

## Questions

Open an issue with the `question` label, or start a discussion if the repo
has GitHub Discussions enabled.
