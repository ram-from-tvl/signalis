"""LangGraph orchestration of the five Signalis agents.

Graph shape:

    signal_extraction -> persona_fit -> buying_stage -> [conditional] -> outreach_planner -> explainability
                                                              |
                                                              v
                                                     human_approval_gate -> explainability

The buying_stage node's confidence score decides the conditional edge: when
confidence is below app.core.config.confidence_approval_threshold, the graph
routes to a human_approval_gate node instead of proceeding straight to plan
generation. The gate still produces an outreach plan (so the marketer has
something concrete to review) but persists it — and the stage classification
— with a pending_approval status rather than auto-approving. This matches
the requirement that *every* generated plan requires approval, while
low-confidence classifications additionally require the classification
itself to be approved before it is treated as final.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agents.buying_stage import run_buying_stage
from app.agents.explainability import run_explainability
from app.agents.outreach_planner import run_outreach_planner
from app.agents.persona_fit import run_persona_fit
from app.agents.signal_extraction import run_signal_extraction
from app.core.config import get_settings
from app.models.entities import Lead, Persona, Signal, Solution


class PipelineState(TypedDict, total=False):
    db: Session
    lead: Lead
    persona: Persona | None
    solution: Solution | None
    raw_signals: list[Signal]
    signal_extraction_result: dict[str, Any]
    persona_fit_result: dict[str, Any]
    stage_result: dict[str, Any]
    requires_approval: bool
    plan_result: dict[str, Any] | None
    explainability_result: dict[str, Any]


def _node_signal_extraction(state: PipelineState) -> PipelineState:
    result = run_signal_extraction(state["db"], state["lead"], state["raw_signals"])
    return {"signal_extraction_result": result}


def _node_persona_fit(state: PipelineState) -> PipelineState:
    result = run_persona_fit(state["db"], state["lead"], state.get("persona"), state.get("solution"))
    return {"persona_fit_result": result}


def _node_buying_stage(state: PipelineState) -> PipelineState:
    all_signals = state["lead"].signals
    result = run_buying_stage(state["db"], state["lead"], all_signals, state["persona_fit_result"])
    settings = get_settings()
    requires_approval = result.get("confidence", 0.0) < settings.confidence_approval_threshold
    return {"stage_result": result, "requires_approval": requires_approval}


def _route_after_stage(state: PipelineState) -> str:
    # Every generated plan requires approval regardless of confidence (see
    # module docstring), so both branches proceed to plan generation; the
    # branch only changes how the resulting classification is flagged.
    return "low_confidence" if state.get("requires_approval") else "normal"


def _node_outreach_planner(state: PipelineState) -> PipelineState:
    stage_result = state["stage_result"]
    result = run_outreach_planner(
        state["db"],
        state["lead"],
        stage_result["stage"],
        stage_result["confidence"],
        state["persona_fit_result"],
        state.get("persona"),
        state.get("solution"),
    )
    return {"plan_result": result}


def _node_explainability(state: PipelineState) -> PipelineState:
    result = run_explainability(
        state["db"],
        state["lead"],
        signal_summary=state["signal_extraction_result"],
        persona_fit=state["persona_fit_result"],
        stage_result=state["stage_result"],
        plan_result=state.get("plan_result"),
        requires_approval=state.get("requires_approval", False),
    )
    return {"explainability_result": result}


def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("signal_extraction", _node_signal_extraction)
    graph.add_node("persona_fit", _node_persona_fit)
    graph.add_node("buying_stage", _node_buying_stage)
    graph.add_node("outreach_planner", _node_outreach_planner)
    graph.add_node("explainability", _node_explainability)

    graph.set_entry_point("signal_extraction")
    graph.add_edge("signal_extraction", "persona_fit")
    graph.add_edge("persona_fit", "buying_stage")
    graph.add_conditional_edges(
        "buying_stage",
        _route_after_stage,
        {"low_confidence": "outreach_planner", "normal": "outreach_planner"},
    )
    graph.add_edge("outreach_planner", "explainability")
    graph.add_edge("explainability", END)

    return graph.compile()


_compiled_graph = None


def get_pipeline_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
