"""Sandboxed execution of dynamically generated scoring code via Daytona.

Signal-strength aggregation is genuinely computed by running Python inside a
Daytona sandbox rather than executing pre-written business logic in-process.
If the sandbox is unreachable, callers fall back to an equivalent local
computation so a transient sandbox outage never blocks the pipeline; which
path ran is always returned so it can be recorded in the agent trace.
"""
from __future__ import annotations

import json
import logging

from app.core.config import get_settings

logger = logging.getLogger("signalis.sandbox")

_daytona_client = None


class SandboxError(RuntimeError):
    """Raised when the Daytona sandbox is unreachable or execution fails."""


def _get_sandbox():
    global _daytona_client
    settings = get_settings()
    if not settings.daytona_api_key or not settings.daytona_sandbox_id:
        raise SandboxError("Daytona is not configured")

    from daytona import Daytona, DaytonaConfig

    if _daytona_client is None:
        _daytona_client = Daytona(
            DaytonaConfig(api_key=settings.daytona_api_key, api_url=settings.daytona_api_url)
        )
    return _daytona_client.get(settings.daytona_sandbox_id)


def run_signal_scoring(signal_rows: list[dict]) -> tuple[dict, str]:
    """Compute a recency/strength-weighted signal score for a lead.

    Returns (result, execution_path) where execution_path is "daytona" or
    "local" depending on where the computation actually ran.
    """
    code = _build_scoring_script(signal_rows)
    try:
        sandbox = _get_sandbox()
        execution = sandbox.code_interpreter.run_code(code, timeout=20)
        stdout = getattr(execution, "stdout", None) or ""
        if getattr(execution, "exit_code", 0) not in (0, None):
            raise SandboxError(f"sandbox script exited non-zero: {stdout}")
        return json.loads(stdout.strip().splitlines()[-1]), "daytona"
    except Exception as exc:  # sandbox unreachable, timed out, or malformed output
        logger.warning("Daytona sandbox execution failed, falling back to local scoring: %s", exc)
        return _score_locally(signal_rows), "local"


def _build_scoring_script(signal_rows: list[dict]) -> str:
    payload = json.dumps(signal_rows)
    return f"""
import json

STRENGTH = {{"late": 3.0, "mid": 1.6, "early": 1.0}}

def score(rows):
    total = 0.0
    weight_sum = 0.0
    for row in rows:
        strength = STRENGTH.get(row.get("intent_stage_hint"), 1.0)
        days_ago = max(row.get("days_ago", 9999), 0)
        recency_weight = 1.0 / (1.0 + days_ago / 7.0)
        total += strength * recency_weight
        weight_sum += recency_weight
    if weight_sum == 0:
        return {{"weighted_score": 0.0, "signal_count": len(rows)}}
    return {{"weighted_score": round(total / weight_sum, 4), "signal_count": len(rows)}}

rows = json.loads('''{payload}''')
print(json.dumps(score(rows)))
"""


def _score_locally(signal_rows: list[dict]) -> dict:
    strength_map = {"late": 3.0, "mid": 1.6, "early": 1.0}
    total = 0.0
    weight_sum = 0.0
    for row in signal_rows:
        strength = strength_map.get(row.get("intent_stage_hint"), 1.0)
        days_ago = max(row.get("days_ago", 9999), 0)
        recency_weight = 1.0 / (1.0 + days_ago / 7.0)
        total += strength * recency_weight
        weight_sum += recency_weight
    if weight_sum == 0:
        return {"weighted_score": 0.0, "signal_count": len(signal_rows)}
    return {"weighted_score": round(total / weight_sum, 4), "signal_count": len(signal_rows)}
