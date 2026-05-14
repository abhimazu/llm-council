"""Base runner — common pipeline-execution machinery shared by all conditions.

A runner takes a question + a pipeline configuration and produces a
``RunResult`` with the response, telemetry (cost, latency, tokens), and
the routing decision (where applicable). The result is JSON-serializable
and gets appended to ``runs/<sweep_id>.jsonl``.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class RunResult:
    """One row in a sweep's JSONL output."""
    sweep_id: str
    condition: str               # "C0" | "C1" | "C2" | "C3" | "R"
    question_id: str             # e.g. "fact-001"
    question: str
    category: str                # "factual" | "open_ended" | "trap"
    response: Optional[str]
    routing_used_council: Optional[bool] = None
    routing_reason: Optional[str] = None
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    finish_reason: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    raw_envelope: Optional[Dict[str, Any]] = None  # full council pipeline envelope, for debugging

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
