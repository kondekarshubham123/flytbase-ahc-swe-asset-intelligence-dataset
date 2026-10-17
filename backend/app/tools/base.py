"""Common result contract shared by every tool.

A tool returns structured data, a suggested visual, and the evidence records /
documents it used. The orchestrator composes those into the chat answer, so
citations and generative UI fall out of the tool layer rather than being bolted on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Evidence:
    kind: str                 # "record" | "document" | "note"
    id: str
    label: str
    detail: str = ""
    path: Optional[str] = None
    snippet: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class ToolResult:
    tool: str
    data: Dict[str, Any] = field(default_factory=dict)
    visual: Optional[Dict[str, Any]] = None
    evidence: List[Evidence] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    answer: Optional[str] = None

    def add_evidence(self, *items: Evidence) -> "ToolResult":
        self.evidence.extend(items)
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "data": self.data,
            "visual": self.visual,
            "evidence": [e.as_dict() for e in self.evidence],
            "notes": self.notes,
            "conflicts": self.conflicts,
            "answer": self.answer,
        }


def iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.date().isoformat()
    return str(dt)


def fmt_num(x: Any) -> Any:
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x
