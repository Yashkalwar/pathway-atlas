"""Allowlisted analytics tools exposed to the local LLM. The LLM never writes SQL; it only picks a tool + args."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..metrics import core as m

GroupLiteral = Literal["age", "sex", "comorbidity", "copay"]


class Window(BaseModel):
    window_days: Optional[int] = Field(None, description="Follow-up window in days, e.g. 180, 365, 730. Default 365.")


class Pathways(BaseModel):
    max_lines: int = Field(3, ge=1, le=4, description="Number of lines of therapy to show")


class Survival(BaseModel):
    event: Literal["persistence", "switch", "discontinuation"] = "persistence"
    group: Optional[GroupLiteral] = None


class Subgroups(BaseModel):
    by: GroupLiteral = "age"
    window_days: Optional[int] = None


class NoArgs(BaseModel):
    pass


TOOLS = {
    "get_funnel": (
        Window, lambda a: m.funnel(a.window_days),
        "What happens to patients after initiating Therapy A: how many remain on therapy, discontinue, switch, get hospitalized, and median time to switch.",
    ),
    "get_leave_drivers": (
        Window, lambda a: m.leave_drivers(a.window_days),
        "Why patients discontinue or switch from Therapy A (60-day preceding OMOP clinical/claims proxy signals: flare/CRP, safety/AE, OOP copay/adherence barrier, remission step-down) and what happens after discontinuation.",
    ),
    "get_second_line_outcomes": (
        NoArgs, lambda a: m.second_line(),
        "Compare intra-class TNFi cycling (adalimumab -> etanercept) vs Mechanism-of-Action (MoA) swapping (IL-6i, JAKi, T-cell, anti-CD20) on 2nd-line persistence.",
    ),
    "get_risk_factors": (
        NoArgs, lambda a: m.risk_drivers(),
        "Multivariable Cox proportional-hazards risk drivers (hazard ratios for OOP copay, comorbidities, age, sex, baseline CRP) and hospitalization rate ratios.",
    ),
    "get_pathways": (
        Pathways, lambda a: m.pathways(a.max_lines),
        "Treatment sequencing: most common sequences of therapy lines (A -> B -> C) and switch destinations.",
    ),
    "get_survival": (
        Survival, lambda a: m.survival(a.event, a.group),
        "Persistence on Therapy A over time (Kaplan-Meier) or cumulative incidence of switching/discontinuation at 6/12/24 months.",
    ),
    "get_utilization": (
        NoArgs, lambda a: m.utilization(),
        "Healthcare utilization: hospitalizations, ER visits, outpatient visits, and cost per patient-year, overall and by status.",
    ),
    "get_diagnosis_and_treatment_timing": (
        NoArgs, lambda a: m.pre_index(),
        "Time to diagnosis, time to specialist, time to treatment, and treatment initiation rate for RA patients.",
    ),
    "get_escalation": (
        Window, lambda a: m.escalation(a.window_days),
        "Treatment escalation: share of patients with add-on therapy while on Therapy A, and which agents are added.",
    ),
    "get_subgroups": (
        Subgroups, lambda a: m.subgroups(a.by, a.window_days),
        "Compare persistence, discontinuation, switching and hospitalization across age, sex, comorbidity, or OOP copay groups.",
    ),
    "get_unmet_need": (
        NoArgs, lambda a: m.unmet_need(),
        "Unmet-need flags: early failure, copay/refill barriers, untreated discontinuation, multi-line cycling, hospitalization after leaving therapy.",
    ),
    "get_overview": (NoArgs, lambda a: m.kpis(), "Headline KPIs for the cohort."),
}


def ollama_tools():
    return [
        {"type": "function", "function": {"name": n, "description": d, "parameters": model.model_json_schema()}}
        for n, (model, _fn, d) in TOOLS.items()
    ]


def run_tool(name: str, args: dict):
    if name not in TOOLS:
        raise ValueError(f"unknown tool {name}")
    model, fn, _ = TOOLS[name]
    return fn(model(**(args or {})))
