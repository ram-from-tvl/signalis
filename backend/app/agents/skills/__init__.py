"""TrueForge skill content bundled with this backend.

Each subpackage here corresponds to one git-backed TrueForge skill: a
`SKILL.md` file with the full instructional content TrueForge clones into an
agent's sandbox on demand, plus (where a fallback path needs it) a condensed
Python constant for use when TrueForge/the skill is unavailable. See
`app.core.trueforge.ensure_skill` and `app.agents.outreach_planner`.
"""
