# Security Policy

## Supported versions

Signalis is under active development on `main`. Security fixes are made
against `main` only; there are no maintained release branches yet.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security vulnerability.

Instead, report it privately via GitHub's
[private vulnerability reporting](../../security/advisories/new) for this
repository, or by emailing the maintainer listed in the repository's GitHub
profile. Include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce, or a proof of concept if you have one.
- The affected file(s) or endpoint(s), if known.

You should expect an initial response within 5 business days. We'll work
with you to understand and fix the issue, and will credit you in the fix
unless you prefer otherwise.

## Scope and known sensitive surfaces

A few areas of this codebase are worth extra scrutiny in any report or
review:

- **Secrets and credentials.** All API keys (`GEMINI_API_KEY`, `HF_TOKEN`,
  `DAYTONA_API_KEY`) are read from environment variables via
  `backend/app/core/config.py` and must never be committed. `.env` is
  gitignored; `.env.example` documents the required shape without real
  values.
- **Sandboxed code execution.** `backend/app/core/sandbox.py` executes
  agent-generated Python inside a Daytona sandbox for buying-stage signal
  scoring. The script string passed to the sandbox is built from
  structured, escaped values rather than raw string interpolation — see the
  history of `fix: prevent code injection in generated sandbox script` in
  `git log` for the specific vulnerability class this guards against. Any
  change to how that script is assembled should be treated as
  security-sensitive.
- **LLM-generated content.** Outreach plan copy, classification
  justifications, and explainability narratives are all real model output.
  They are rendered as plain text in the frontend, not raw HTML — a change
  that starts rendering model output as HTML/markdown-with-HTML would
  reintroduce an XSS surface and should be reviewed accordingly.
- **CORS.** `backend/app/core/config.py`'s `cors_allow_origins` is currently
  scoped to local development origins. Widening it (e.g. to `*`) for a
  deployed environment should be a deliberate, reviewed decision, not an
  incidental change.

## Dependencies

Backend dependencies are pinned in `backend/requirements.txt` and declared
at the top level in `backend/pyproject.toml`. Frontend dependencies are
locked in `frontend/package-lock.json`. Dependabot or an equivalent
automated dependency-update tool is recommended once the repository is
public.
