"""Modeles Pydantic pour DevAgent autonome."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class InterviewQuestion(BaseModel):
    question: str
    type: Literal["qcm", "text", "qcm_or_text"]
    options: Optional[list[str]] = None


class InterviewAnswer(BaseModel):
    project_id: int
    question: str
    answer: str


class LoopBudget(BaseModel):
    max_iterations: int = 25
    max_tokens: int = 500_000
    max_consecutive_failures: int = 3


class DevSpec(BaseModel):
    project_name: str
    slug: str
    project_type: str
    stack: list[str]
    isolation_path: str
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    loop_budget: LoopBudget = Field(default_factory=LoopBudget)


class DevAgentStartResponse(BaseModel):
    project_id: int
    first_question: dict


class DevAgentRunResponse(BaseModel):
    status: str


class DevAgentPauseResponse(BaseModel):
    status: str
