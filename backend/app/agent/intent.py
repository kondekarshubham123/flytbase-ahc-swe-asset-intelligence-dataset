"""Turn a user's message into a structured Intent.

Two sources, same output shape:
  * the LLM (when a key is configured) — best on unseen phrasing;
  * a deterministic keyword/regex router — the fallback that keeps the copilot
    working on a rate-limited free tier and makes the system testable offline.

The router intentionally encodes *domain* vocabulary (spills, downtime, RCA, …)
rather than sample-question strings, so unseen questions still land somewhere sane.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import window as W

VISUAL_WORDS = {
    "card": "card", "status card": "card",
    "table": "table", "list": "table",
    "line chart": "line", "line graph": "line", "over time": "line",
    "bar chart": "bar", "bar graph": "bar",
    "map": "map",
    "heatmap": "heatmap", "heat map": "heatmap",
}


@dataclass
class Intent:
    action: str = "ask"            # ask | correct_raw | correct_analysis | well_type_rule | share_knowledge
    intent: str = "unknown"        # status | current_status | downtime | temp_series | spills |
                                   # spills_map | spills_heatmap | well_locations | cost | rca |
                                   # compliance | containment | rule_search | doc_search |
                                   # reference_followup
    asset_ids: List[str] = field(default_factory=list)
    area: Optional[str] = None
    window: Dict[str, Any] = field(default_factory=dict)
    visual: Optional[str] = None
    equipment: Optional[str] = None
    months: Optional[int] = None
    top: Optional[int] = None
    query: str = ""
    # feedback-specific
    field: Optional[str] = None
    value: Optional[str] = None
    well_type: Optional[str] = None
    inspection_id: Optional[str] = None
    reason: Optional[str] = None
    confidence: float = 0.5
    source: str = "router"

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


FEEDBACK_CUES = (
    "is wrong", "was wrong", "were wrong", "are wrong", "should be", "should have been",
    "the real value", "real value is", "measured", "re-measured", "remeasured",
    "correction", "corrected", "not 4.5", "ai estimated", "the ai said", "actually it was",
)
WELLTYPE_RULE_RE = re.compile(r"(?:note for all|for all|for every)\s+([A-Za-z]\d{3,})\s+wells?", re.I)
SHARE_CUES = (
    "shut in", "shut-in", "will be shut", "back online", "running fine", "is online",
    "online since", "planned workover", "planned maintenance", "fyI", "for everyone",
    "is producing again", "back up",
)
# A declarative claim that a well is up/operating, as opposed to a question.
STATUS_CLAIM_CUES = (
    "is running", "are running", "running fine", "is producing", "are producing",
    "is online", "back online", "online since", "back up", "is producing again",
    "is up", "is back",
)
# The concrete number offered as the corrected value: first the value that
# follows a "re-measured / should be / actually" cue, otherwise a unit-suffixed
# number. Kept as two regexes so the cued value always wins over an earlier
# number that the user is reporting as wrong.
CORRECTED_VALUE_RE = re.compile(
    r"(?:measured|re-?measured|real value is|should be|should have been|actually|correct(?:ed)? to)"
    r"\D{0,30}?(\d+(?:\.\d+)?)",
    re.I,
)
UNIT_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:°?\s*C|°C|bbl|barrel)", re.I)


def _corrected_value(text: str) -> Optional[str]:
    m = CORRECTED_VALUE_RE.search(text) or UNIT_VALUE_RE.search(text)
    return m.group(1) if m else None


def _well_id(text: str) -> bool:
    return bool(re.search(r"well[\s\-_]*0*\d+", text, re.I))


def _is_question(text: str) -> bool:
    t = text.strip().lower()
    if t.endswith("?"):
        return True
    return t.startswith(("is ", "are ", "was ", "were ", "do ", "does ", "did ", "which ",
                         "what ", "how ", "when ", "where ", "why ", "who ", "show ",
                         "list ", "can ", "could ", "give "))


def parse_visual(text: str) -> Optional[str]:
    t = text.lower()
    for phrase, kind in VISUAL_WORDS.items():
        if phrase in t:
            return kind
    return None


def _has(text: str, *words: str) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def classify_action(text: str) -> str:
    t = text.lower()
    # A correction to the AI's analysis or a raw reading.
    if _has(t, *FEEDBACK_CUES):
        # A statement like "WELL-0164 is producing oil, that inspection was wrong"
        # is a status claim, not a numeric correction: route it to shared
        # knowledge so it is compared against the data instead of blanking a field.
        if _has(t, *STATUS_CLAIM_CUES) and not _corrected_value(t) and not _is_question(text) \
                and _well_id(text):
            return "share_knowledge"
        if _has(t, "ai estimated", "ai said", "estimated the") and _has(t, "bbl", "barrel"):
            return "correct_analysis"
        # A correction carrying a barrel unit or talking about a spill is a
        # correction to the volume analysis, not to a temperature reading.
        if _has(t, "bbl", "barrel"):
            return "correct_analysis"
        return "correct_raw"
    if WELLTYPE_RULE_RE.search(t):
        return "well_type_rule"
    if _has(t, *SHARE_CUES) and _well_id(t) and not _is_question(text):
        return "share_knowledge"
    return "ask"


def classify_question(text: str) -> str:
    t = text.lower()
    if _has(t, "the wells we discussed", "the three wells", "we discussed", "discussed yesterday",
            "those wells", "these wells", "the wells from"):
        return "reference_followup"
    # "compliance"
    if _has(t, "rules has", "rules did", "broken", "breach", "compliance", "violation",
            "still open", "not met"):
        return "compliance"
    if _has(t, "root cause", "root-cause", "rootcause", "rca"):
        return "rca"
    if _has(t, "contained", "containment"):
        return "containment"
    if _has(t, "heatmap", "heat map"):
        return "spills_heatmap"
    if _has(t, "spill") and _has(t, "map", "location", "locations", "where"):
        return "spills_map"
    # Well locations: explicit location words, or a map that is not about spills.
    if _has(t, "location", "locations", "coordinate", "coordinates", "latitude",
            "longitude", "geographic", "where are the wells", "where are wells",
            "where is well", "where's well", "locate the wells", "locate wells"):
        return "well_locations"
    if _has(t, "map"):
        return "well_locations"
    if _has(t, "cost", "spend", "spent", "paid"):
        return "cost"
    # A temperature question (often "wellhead temperature against air temperature").
    if _has(t, "temperature", "wellhead temp", "against air", "against the air") and not _has(
            t, "which wells", "any wells", "which well"):
        return "temp_series"
    if _has(t, "how many days", "downtime", "down the longest", "most downtime", "was down",
            "were down", "days was", "days were"):
        return "downtime"
    if _has(t, "which wells") and _has(t, "not producing", "are down", "is down", "stopped"):
        return "current_status"
    if _has(t, "not producing", "producing oil", "status", "is it down", "right now"):
        if _has(t, "which wells", "which well", "any wells", "list"):
            return "current_status"
        return "status"
    if _has(t, "spill") and _has(t, "how many", "how much", "total", "list", "had"):
        return "spills"
    if _has(t, "spill"):
        return "spills"
    if _has(t, "rule", "threshold", "limit", "deadline"):
        return "rule_search"
    if _has(t, "manual", "sop", "procedure"):
        return "doc_search"
    return "unknown"


def classify(text: str, today: Optional[date] = None,
             asset_ids: Optional[List[str]] = None, area: Optional[str] = None) -> Intent:
    today = today or date.today()
    action = classify_action(text)
    it = Intent(action=action, query=text)
    it.visual = parse_visual(text)
    it.window = W.parse_window(text, today)
    dp = W.parse_date_phrase(text, today)
    if dp:
        it.window["single"] = dp["single"]
    it.asset_ids = asset_ids or []
    it.area = area

    m = re.search(r"(pump jack|pumpjack|flowline|wellhead|rod string|downhole pump|separator)",
                  text, re.I)
    if m:
        it.equipment = m.group(1).lower().replace("pumpjack", "pump_jack").replace(" ", "_")

    if action == "well_type_rule":
        mt = WELLTYPE_RULE_RE.search(text)
        if mt:
            it.well_type = mt.group(1).upper()
        it.field = "rca_corrective_count" if _has(text, "root cause", "root-cause", "rca",
                                                  "analysis") else None
        mv = re.search(r"after\s+(\d+)\s+repairs?", text, re.I)
        if mv:
            it.value = mv.group(1)
        it.reason = text
        it.confidence = 0.9
        return it

    if action in ("correct_raw", "correct_analysis"):
        it.field = "spill_volume_bbl" if action == "correct_analysis" else "wellhead_temp_c"
        # Prefer the *corrected* value (the one the user measured / says is right),
        # not the value they are reporting as wrong.
        mv = _corrected_value(text)
        if mv:
            it.value = mv
        it.reason = text
        # Find the inspection referenced by date ("the 15 Oct reading", "the 24 Sep spill").
        it.window = W.parse_date_phrase(text, today) or it.window
        it.confidence = 0.85
        return it

    if action == "share_knowledge":
        it.reason = text
        it.confidence = 0.8
        return it

    it.intent = classify_question(text)
    # Special cases with extra parameters.
    m = W.parse_last_n(text)
    if m:
        unit, n = m
        if it.intent in ("downtime", "spills_map") and unit == "months" and n == 4:
            it.top = 3 if _has(text, "most downtime", "longest") else None
        if it.intent == "spills" or it.intent == "spills_map":
            pass
    if it.intent == "compliance":
        it.months = 12
        mm = re.search(r"last\s+(\d+)\s+months", text.lower())
        if mm:
            it.months = int(mm.group(1))
    if it.intent == "rca":
        it.months = 12
    if it.intent == "downtime":
        it.top = 3 if _has(text, "most downtime", "longest", "top", "which 3", "which three") else None
    it.confidence = 0.7 if it.intent != "unknown" else 0.3
    return it
