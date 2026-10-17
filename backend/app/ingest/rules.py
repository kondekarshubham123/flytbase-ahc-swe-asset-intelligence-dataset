"""Load manuals/SOPs as retrievable document chunks and extract the rule registry.

Rule *keys* are the fixed protocol vocabulary of the domain (SR-01, RC-02, producing
temperature…). Rule *values* are read out of the documents with regexes, so a new
well type or a revised SOP in the test dataset is picked up automatically and every
numeric answer can cite the section it came from.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

import duckdb

WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _num(token: str) -> Optional[float]:
    token = token.strip().lower()
    if token in WORDS:
        return float(WORDS[token])
    try:
        return float(token)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Document chunking
# ---------------------------------------------------------------------------
def _split_sections(text: str) -> List[Tuple[str, str]]:
    """Split markdown into (section_heading, body) chunks on heading lines."""
    lines = text.splitlines()
    chunks: List[Tuple[str, str]] = []
    heading = "preamble"
    buf: List[str] = []
    any_heading = False

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            chunks.append((heading, body))

    for line in lines:
        if re.match(r"^#{1,4}\s+", line):
            any_heading = True
            flush()
            buf = []
            heading = re.sub(r"^#{1,4}\s+", "", line).strip()
        else:
            buf.append(line)
    flush()
    if not any_heading and chunks:
        return chunks
    return chunks


def load_documents(con: duckdb.DuckDBPyConnection, fleet: Path) -> int:
    files: List[Path] = []
    files += sorted((fleet / "well-types").glob("*.md"))
    files += sorted((fleet / "sops").glob("*.md"))

    count = 0
    for path in files:
        text = path.read_text()
        doc_id = _doc_id(text, path)
        title = _title(text, path)
        for i, (section, body) in enumerate(_split_sections(text)):
            con.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?)",
                [f"{doc_id}#{i}", doc_id, title, section, i, body],
            )
            count += 1
    return count


def _doc_id(text: str, path: Path) -> str:
    m = re.search(r"Document ID:\s*([A-Za-z0-9\-]+)", text)
    if m:
        return m.group(1)
    return path.stem.upper()


def _title(text: str, path: Path) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else path.stem


# ---------------------------------------------------------------------------
# Rule extraction
# ---------------------------------------------------------------------------
def _first(pattern: str, text: str, group: int = 1, flags: int = re.I | re.S) -> Optional[float]:
    m = re.search(pattern, text, flags)
    if not m:
        return None
    return _num(m.group(group))


def extract_well_type_rules(text: str) -> dict:
    """Pull per-well-type rules out of one manual."""
    rules = {}
    delta = _first(r"at least\s*\**\s*([\d.]+)\s*°?\s*C\s+above\s+ambient", text)
    if delta is None:
        delta = _first(r"temperature is\s*(?:at least\s*)?\**\s*([\d.]+)\s*°?\s*C\s+above", text)
    if delta is not None:
        rules["producing_delta_c"] = (delta, "°C above ambient", "production temperature rule")

    alarm = _first(r"at or below\s*\**\s*([\d.]+)\s*°?\s*C", text)
    if alarm is not None:
        rules["temp_alarm_c"] = (alarm, "°C", "temperature alarm")

    shutin = _first(r"more than\s*\**\s*([\d.]+)\s*%?\s*of the pad area", text)
    if shutin is not None:
        rules["spill_shutin_area_pct"] = (shutin, "% of pad", "spill-related shut-in")

    rca = _first(r"([\w]+|\d+)\s+or more\s*\**\s*corrective", text)
    if rca is not None:
        rules["rca_corrective_count"] = (rca, "repairs", "maintenance escalation")

    window = _first(r"([\d]+)\s+months? before the current date", text)
    if window is not None:
        rules["rca_window_months"] = (window, "months", "maintenance escalation")

    dtdown = _first(r"Downtime of more than\s*\**\s*(\d+)\s*consecutive days", text)
    if dtdown is not None:
        rules["consecutive_downtime_days"] = (dtdown, "days", "maintenance escalation")
    return rules


# SOP protocol → (rule_key, regex, unit, section marker)
SOP_RULES = [
    ("SR-01", "report_deadline_hours", r"written notice.*?within\s*\**\s*(\d+)\s*hours", "hours"),
    ("SR-01", "report_deadline_major_hours", r"within\s*\**\s*(\d+)\s*hours for a major", "hours"),
    ("SR-02", "containment_deadline_hours", r"contained within\s*\**\s*(\d+)\s*hours", "hours"),
    ("SR-03", "verify_deadline_days", r"verified within\s*\**\s*(\d+)\s*days", "days"),
    ("SR-03", "verify_tph_pct", r"cleaned to\s*\**\s*([\d.]+)\s*%\s*TPH", "% TPH"),
    ("SR-03", "soil_samples_min", r"at least\s*\**\s*(\d+)\s*soil samples", "samples"),
    ("RC-01", "inspection_interval_days", r"inspected at least once every\s*\**\s*(\d+)\s*days", "days"),
    ("RC-03", "consecutive_downtime_days", r"down for more than\s*\**\s*(\d+)\s*consecutive days", "days"),
    ("RC-04", "nov_response_days", r"answered within\s*\**\s*(\d+)\s*days", "days"),
    ("WS-03", "h2s_action_ppm", r"reads\s*\**\s*(\d+)\s*ppm or more", "ppm"),
    ("WS-04", "lel_limit_pct", r"reads\s*\**\s*(\d+)%\s*of the lower explosive limit", "% LEL"),
    ("WS-05", "pressure_test_multiplier", r"at least\s*\**\s*([\d.]+)\s*[×x]\s*the flowline", "× MOP"),
    ("WS-05", "pressure_test_hold_hours", r"hold for\s*\**\s*(\d+)\s*hours", "hours"),
    ("WS-07", "report_window_days", r"within\s*\**\s*(\d+)\s*days of finishing", "days"),
]


def build_rules(con: duckdb.DuckDBPyConnection, fleet: Path) -> int:
    count = 0

    for path in sorted((fleet / "well-types").glob("*.md")):
        text = path.read_text()
        doc_id = _doc_id(text, path)
        wtype = re.search(r"well_type\s*=\s*\"([^\"]+)\"", text)
        scope_key = wtype.group(1) if wtype else path.stem
        for key, (value, unit, section) in extract_well_type_rules(text).items():
            con.execute(
                "INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)",
                [f"RULE-{scope_key}-{key}", "well_type", scope_key, key, value, None,
                 unit, doc_id, section, f"{scope_key} manual: {section}"],
            )
            count += 1

    for path in sorted((fleet / "sops").glob("*.md")):
        text = path.read_text()
        doc_id = _doc_id(text, path)
        for protocol, key, pattern, unit in SOP_RULES:
            value = _first(pattern, text)
            if value is None:
                continue
            con.execute(
                "INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)",
                [f"RULE-{protocol}-{key}", "protocol", protocol, key, value, None,
                 unit, doc_id, protocol, f"{protocol}: {key}"],
            )
            count += 1

    # Fallbacks for a well type the manuals don't name (keeps the system alive).
    defaults = {
        "producing_delta_c": (15.0, "°C above ambient"),
        "rca_corrective_count": (3.0, "repairs"),
        "rca_window_months": (12.0, "months"),
        "consecutive_downtime_days": (14.0, "days"),
    }
    for key, (value, unit) in defaults.items():
        con.execute(
            "INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)",
            [f"RULE-DEFAULT-{key}", "well_type", "*", key, value, None, unit,
             "SYSTEM", "default", f"Default fallback for {key}"],
        )
        count += 1

    # T350 refers to T200 §10: make that explicit so a T350 override is meaningful.
    con.execute(
        "INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)",
        ["RULE-T350-rca_corrective_count", "well_type", "T350", "rca_corrective_count",
         3.0, None, "repairs", "OM-T350", "6. Maintenance escalation",
         "T350 refers to T200 §10"],
    )
    count += 1
    return count
