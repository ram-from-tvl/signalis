"""B2B outreach copywriting style guide.

The full guidance lives in `SKILL.md` in this directory and is registered
with TrueForge as a git-backed skill (see `app.core.trueforge.ensure_skill`)
so the Outreach Planner agent can load it on demand instead of it being
baked into the agent's system instruction on every call.

`CONDENSED_STYLE_GUIDANCE` is a short, inline summary of the same guidance
for the direct Gemini/Hugging Face fallback path
(`app.agents.common.run_agent_reasoning`'s non-TrueForge branch), which has
no access to TrueForge skills and would otherwise regress to whatever craft
guidance is left in the system instruction. Keep this in sync by hand if
`SKILL.md` changes materially — it is intentionally a condensed summary, not
a duplicate, since the full document is too long to inject into every
fallback call.
"""
from __future__ import annotations

CONDENSED_STYLE_GUIDANCE = """B2B outreach copywriting guidance:
- Tone by channel: email is short (1-3 sentence paragraphs), one ask, peer-to-peer voice.
  LinkedIn is casual and opens with something specific to this person, not a pitch. A call-script
  touchpoint gives talking points and a real opening question, not a paragraph to read verbatim.
- Reference buying signals as a business inference ("you're likely comparing options"), never as
  raw telemetry (exact visit counts, time-on-page, timestamps) — the latter reads as surveillance.
- Cadence: touchpoint 1 is low-commitment and signal-motivated; touchpoint 2 adds new value rather
  than repeating the same ask; touchpoint 3 often switches channel and gets more direct for
  late-stage leads; a final touchpoint (if 4-5 total) can be a genuine "I'll stop reaching out
  unless..." close-the-loop message. Space later touches further apart, not evenly.
- Avoid AI-sounding copy: no "I hope this finds you well," no "fast-paced landscape" filler, no
  stacking 3+ generic value props, no "seamlessly/leverage/unlock/empower/revolutionize," no vague
  closers like "let me know if you have questions" — end with one specific, easy next step instead.
- Strong openings make a specific, plausible claim about the reader's situation and invite a real
  answer; weak openings are generic, name-stuffed, and announce an intent to pitch.
- Tailor to seniority: ICs get product detail; Directors/VPs get outcome/team-impact framing;
  C-level gets business-outcome/risk framing only, with the shortest messages."""
