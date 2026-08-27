"""Real end-to-end direct-LLM integration test.

Unlike the rest of the suite, this makes a genuine network call to the
configured model to prove the agent reasoning is real, not mocked. Skipped
automatically when no API key is configured (e.g. in CI without secrets).
TrueForge is disabled for this test specifically so it exercises the direct
generate_json call path deterministically, independent of whether a
TrueForge sidecar happens to be running — this hits Hugging Face first
(the primary provider) and falls back to Gemini only if HF fails, same
priority order as generate_json everywhere else.
"""
from __future__ import annotations

import pytest

from app.agents.persona_fit import run_persona_fit
from app.core.config import get_settings
from app.models import Persona, Solution

settings = get_settings()


@pytest.mark.integration
@pytest.mark.skipif(
    not (settings.hf_tokens or settings.gemini_api_keys),
    reason="Neither HF_TOKEN nor GEMINI_API_KEY is configured",
)
def test_persona_fit_agent_real_llm_call(db_session, sample_lead):
    original_trueforge_enabled = settings.trueforge_enabled
    settings.trueforge_enabled = False
    try:
        _run_test(db_session, sample_lead)
    finally:
        settings.trueforge_enabled = original_trueforge_enabled


def _run_test(db_session, sample_lead):
    persona = Persona(
        role="VP of Sales",
        seniority="VP",
        industry="SaaS",
        company_size_band="51-200",
        geography="US",
    )
    solution = Solution(
        name="Signalis Copilot",
        problem_solved="Unifying buying signals across systems",
        icp_filters={"company_size_band": "51-200", "industries": ["SaaS"]},
    )

    result, agent_run_id = run_persona_fit(db_session, sample_lead, persona, solution)

    assert agent_run_id is not None
    assert result["fit"] in {"full_fit", "partial_fit", "mismatch"}
    assert isinstance(result["reasoning"], str)
    assert len(result["reasoning"]) > 10
    assert isinstance(result["missing_data"], list)
