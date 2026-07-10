"""Pydantic models for the ANALYSIS-phase JSON skeleton.

The skeleton is the structured output of ANALYSIS: all reasoning conclusions
(narratives, forensic audit, valuation, Kelly, red team, decision tree,
self-critique, stress test) as typed fields - no prose.

COMPILE consumes this skeleton to expand into a full report; SELF_CHECK
validates it programmatically (0 LLM).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MissingDataItem(BaseModel):
    field: str
    importance: Literal["high", "medium", "low"] = "medium"
    estimable: bool = False


class DataInference(BaseModel):
    field: str
    formula: str = ""
    base: float | None = None
    optimistic: float | None = None
    pessimistic: float | None = None
    price_impact: str = ""


class Narrative(BaseModel):
    probability: float = Field(ge=0.0, le=1.0)
    consensus: str = ""
    our_view: str = ""
    falsify: str = ""


class Narratives(BaseModel):
    macro: Narrative
    industry: Narrative
    alpha: Narrative


class ForensicItem(BaseModel):
    item: str
    status: Literal["正常", "关注", "危险"] = "正常"
    detail: str = ""


class Dupont(BaseModel):
    net_margin: float | None = None
    turnover: float | None = None
    leverage: float | None = None


class ForensicAudit(BaseModel):
    items: list[ForensicItem] = Field(default_factory=list)
    dupont: Dupont = Field(default_factory=Dupont)
    quality_score: int = Field(ge=1, le=10)
    red_flags: list[str] = Field(default_factory=list)


class ValuationTargets(BaseModel):
    conservative: float | None = None
    neutral: float | None = None
    optimistic: float | None = None


class SensitivityItem(BaseModel):
    param: str = ""
    impact: str = ""


class Valuation(BaseModel):
    targets: ValuationTargets = Field(default_factory=ValuationTargets)
    implied_g: float | None = None
    peer_pe: dict[str, float | int] | None = None
    expectation_gap: str = ""
    sensitivity: list[SensitivityItem] = Field(default_factory=list)


class Kelly(BaseModel):
    b: float
    p: float = Field(ge=0.0, le=1.0)
    q: float = Field(ge=0.0, le=1.0)
    f_star: float
    position: float = 0.0
    rating: str = ""


class RedTeamItem(BaseModel):
    bull: str = ""
    bear: str = ""


class DecisionBranch(BaseModel):
    event: str = ""
    trigger: str = ""
    prob: float | None = None
    data_node: str = ""
    action_triggered: str = ""
    action_else: str = ""


class BearAttack(BaseModel):
    assumption: str = ""
    attack: str = ""


class SelfCritique(BaseModel):
    bear_attacks: list[BearAttack] = Field(default_factory=list)
    judge: str = ""


class StressTest(BaseModel):
    scenario: str = ""
    stress_fcf: float | None = None
    dividend_coverage: float | None = None
    floor_price: float | None = None
    floor_downside_pct: float | None = None
    verdict: str = ""


class AnalysisSkeleton(BaseModel):
    """Top-level skeleton: all reasoning conclusions as typed fields."""

    data_audit: dict | None = None
    data_inference: list[DataInference] = Field(default_factory=list)
    narratives: Narratives
    composite_prob: float = Field(ge=0.0, le=1.0)
    forensic_audit: ForensicAudit
    valuation: Valuation
    kelly: Kelly
    red_team: list[RedTeamItem] = Field(default_factory=list)
    decision_tree: list[DecisionBranch] = Field(default_factory=list)
    self_critique: SelfCritique = Field(default_factory=SelfCritique)
    stress_test: StressTest = Field(default_factory=StressTest)
