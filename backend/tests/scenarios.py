"""End-to-end scenario runner for the 18 example questions.

Rebuilds the database, then drives the copilot through the multi-turn scenarios
from 03-evaluation-queries.md and checks the key facts of each answer.

Run: python -m tests.scenarios
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agent import orchestrator as O
from app.ingest.loader import build, connect

U_MICHAEL = "U-201"
U_JESSICA = "U-202"
U_TYLER = "U-204"
U_JAKE = "U-102"
U_LAURA = "U-401"

RESULTS: List[bool] = []


class Copilot:
    def __init__(self, con):
        self.con = con

    def say(self, user: str, session: str, message: str) -> Dict[str, Any]:
        return O.handle(self.con, user, session, message).as_dict()


def check(label: str, ok: bool, extra: str = "") -> None:
    RESULTS.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  → ' + extra) if extra and not ok else ''}")


def has(text: str, *needles: str) -> bool:
    t = (text or "").lower()
    return all(n.lower() in t for n in needles)


def run(reset: bool = True) -> tuple:
    print("Rebuilding database...")
    con = connect()
    if reset:
        RESULTS.clear()
    build(con=con, verbose=False)
    cp = Copilot(con)

    # ================= PART A1 =================
    print("\n--- A1: asking about the data ---")

    r = cp.say(U_MICHAEL, "a1q1", "Is WELL-0001 producing oil right now? When was it last inspected, and by whom? Show me its status card.")
    check("Q1 not producing", has(r["text"], "not producing"))
    check("Q1 cites INS-0001-0203", "INS-0001-0203" in r["text"])
    check("Q1 Ryan Brooks", has(r["text"], "ryan brooks"))
    check("Q1 names planned shut-in", has(r["text"], "planned") or "CTX-0001-003" in r["text"])
    check("Q1 card visual", r["visual"] and r["visual"]["type"] == "card")

    r = cp.say(U_MICHAEL, "a1q2", "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table.")
    check("Q2 table", r["visual"] and r["visual"]["type"] == "table")
    check("Q2 lists 4 wells", all(w in r["text"] for w in ("WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175")))
    check("Q2 WF-0170 repair reason", "MNT-0170-031" in r["text"])
    check("Q2 no-reason honesty", "no recorded reason" in r["text"].lower())

    r = cp.say(U_MICHAEL, "a1q3", "Show a line chart of WELL-0001's wellhead temperature against air temperature for the last 13 weeks, and mark when it was not producing oil.")
    check("Q3 line chart", r["visual"] and r["visual"]["type"] == "line")
    check("Q3 13 readings", "13 readings" in r["text"])
    check("Q3 marks 8 Oct and 15 Oct", "2026-10-08" in r["text"] and "2026-10-15" in r["text"])

    r = cp.say(U_MICHAEL, "a1q4", "How many oil spills has WELL-0001 had in the last 4 years, and how much oil in total? List them in a table.")
    check("Q4 6 spills", has(r["text"], "6 spill"))
    check("Q4 15.1 bbl", "15.1" in r["text"])
    check("Q4 mentions correction", "correct" in r["text"].lower())
    check("Q4 table", r["visual"] and r["visual"]["type"] == "table")

    r = cp.say(U_MICHAEL, "a1q5", "How many days was WELL-0001 down in 2025?")
    check("Q5 21 days", "21" in r["text"] and "day" in r["text"])
    check("Q5 explains 51.6/43.1", "51.6" in r["text"] and "43.1" in r["text"])
    check("Q5 cites MNT-0001-021", "MNT-0001-021" in r["text"])

    r = cp.say(U_MICHAEL, "a1q6", "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart.")
    check("Q6 bar chart", r["visual"] and r["visual"]["type"] == "bar")
    check("Q6 top3 correct", all(w in r["text"] for w in ("WELL-0121", "WELL-0138", "WELL-0107")))
    check("Q6 49 days", "49" in r["text"])

    # Q11 — follows Q6 in a NEW chat (Michael's most recent prior conversation).
    r = cp.say(U_MICHAEL, "a2q11", "For the three wells we discussed yesterday, what have repairs cost this year?")
    check("Q11 WELL-0121 cost", "25,794" in r["text"])
    check("Q11 WELL-0138 cost", "46,942" in r["text"])
    check("Q11 WELL-0107 cost", "5,176" in r["text"])

    r = cp.say(U_MICHAEL, "a1q7", "Show on a map every well in Northeast Block 8 with a real spill in the last 90 days.")
    check("Q7 map", r["visual"] and r["visual"]["type"] == "map")
    check("Q7 4 wells", all(w in r["text"] for w in ("WELL-0151", "WELL-0165", "WELL-0175", "WELL-0186")))
    check("Q7 map points", r["visual"] and len(r["visual"]["spec"]["points"]) == 4)

    r = cp.say(U_MICHAEL, "a1q8", "Show a heatmap of 2026 spills by geographic area and month.")
    check("Q8 heatmap", r["visual"] and r["visual"]["type"] == "heatmap")
    check("Q8 mentions SW B24", "Southwest Block 24" in r["text"])

    r = cp.say(U_MICHAEL, "a1q9", "Which rules has WELL-0001 broken in the last 12 months, and what is still open?")
    check("Q9 SR-01", "SR-01" in r["text"])
    check("Q9 SR-02", "SR-02" in r["text"])
    check("Q9 SR-03", "SR-03" in r["text"])
    check("Q9 RC-04", "RC-04" in r["text"])
    check("Q9 5,000 fine", "5,000" in r["text"])

    r = cp.say(U_MICHAEL, "a1q10", "In Southwest Block 24, which spills in the last 90 days were not contained within the SOP time limit?")
    check("Q10 WELL-0082", "WELL-0082" in r["text"])
    check("Q10 WELL-0100", "WELL-0100" in r["text"])

    # ================= PART A2 =================
    print("\n--- A2: memory and feedback from one user ---")

    # Q11 is already checked above (it follows Q6).

    # Q12 — correct wrong raw data
    r = cp.say(U_MICHAEL, "a2q12", "The 15 Oct reading for WELL-0164 is wrong. The handheld thermal gun used in that inspection was faulty; the field crew re-measured the wellhead at 41.0 °C.")
    check("Q12 keeps original 24.0", "24.0" in r["text"])
    check("Q12 records correction", "INS-0164-0205" in r["text"] or "correct" in r["text"].lower())
    check("Q12 now producing", "producing" in r["text"].lower())
    check("Q12 flags disagreeing note", bool(r["conflicts"]) or "pump jack not moving" in r["text"].lower())
    r = cp.say(U_MICHAEL, "a2q12b", "Which wells in Northeast Block 8 are not producing oil right now? Show them in a table.")
    check("Q12 re-ask now 3 wells", "WELL-0164" not in r["text"])
    check("Q12 re-ask keeps 0170/0172/0175", all(w in r["text"] for w in ("WELL-0170", "WELL-0172", "WELL-0175")))

    # Q13 — correct the AI's analysis
    r = cp.say(U_MICHAEL, "a2q13", "The AI estimated the 24 Sep spill at WELL-0151 as 4.5 bbl. The field crew measured 5.4 bbl.")
    check("Q13 corrects to 5.4", "5.4" in r["text"])
    check("Q13 reportable", "reportable" in r["text"].lower())
    check("Q13 SR-01 flagged", "SR-01" in r["text"])
    check("Q13 SR-03 due 23 Nov", "23 Nov" in r["text"] or "2026-11-23" in r["text"])

    # Q14 — a rule for a whole well type
    r = cp.say(U_MICHAEL, "a2q14a", "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?")
    first = set(w for w in ("WELL-0108", "WELL-0121", "WELL-0138", "WELL-0120", "WELL-0102") if w in r["text"])
    check("Q14a 3 wells", first == {"WELL-0108", "WELL-0121", "WELL-0138"}, extra=str(first))
    r = cp.say(U_MICHAEL, "a2q14b", "Note for all T350 wells: our team now wants a pump jack root-cause analysis after 2 repairs, not 3.")
    r = cp.say(U_MICHAEL, "a2q14c", "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?")
    added = "WELL-0120" in r["text"]
    not_t200 = "WELL-0102" not in r["text"]
    check("Q14c adds T350 WELL-0120", added)
    check("Q14c excludes T200 WELL-0102", not_t200)

    # ================= PART B =================
    print("\n--- B: several users ---")

    # Q16 is evaluated first so Jessica genuinely has no prior conversation,
    # which is the privacy case the example describes.
    r = cp.say(U_JAKE, "bq16a", "FYI for everyone: WELL-0007 will be shut in from 17 Oct for a 10-day planned workover.")
    check("Q16a stored", "WELL-0007" in r["text"] and ("noted" in r["text"].lower() or "shared" in r["text"].lower()))
    r = cp.say(U_MICHAEL, "bq16b", "Is any downtime planned for WELL-0007?")
    check("Q16b Michael sees Jake's note", "yes" in r["text"].lower() and "jake" in r["text"].lower())
    r = cp.say(U_JESSICA, "bq16c", "For the three wells we discussed yesterday, what have repairs cost this year?")
    check("Q16c Jessica has no such history", "don't have" in r["text"].lower() or "no earlier" in r["text"].lower())

    r = cp.say(U_JESSICA, "bq15a", "Is WELL-0164 producing oil right now?")
    check("Q15a Jessica sees correction → yes", "producing oil" in r["text"].lower() and "not producing" not in r["text"].lower())
    check("Q15a mentions Michael's correction", "michael turner" in r["text"].lower() or "U-201" in r["text"])
    r = cp.say(U_LAURA, "bq15b", "Which spills in Northeast Block 8 in the last 90 days needed a report to the regulator?")
    check("Q15b only WELL-0151", "WELL-0151" in r["text"] and "WELL-0165" not in r["text"])
    check("Q15b flags overdue report", "overdue" in r["text"].lower() or "not" in r["text"].lower())

    r = cp.say(U_MICHAEL, "bq17a", "WELL-0175 has been shut in for planned work since 14 Oct.")
    check("Q17a stored as shared", "WELL-0175" in r["text"])
    r = cp.say(U_TYLER, "bq17b", "WELL-0175 is running fine.")
    check("Q17b conflict raised", bool(r["conflicts"]) or "not producing" in r["text"].lower())
    check("Q17b marked unconfirmed", "unconfirmed" in r["text"].lower())
    r = cp.say(U_JAKE, "bq17c", "WELL-0001 is back online since 14 Oct.")
    check("Q17c conflict with data", bool(r["conflicts"]) or "not producing" in r["text"].lower())

    r = cp.say(U_LAURA, "bq18", "Was the 20 Aug spill at WELL-0101 real oil?")
    check("Q18 yes but disagreement shown", "yes" in r["text"].lower() and ("CTX-0101-002" in r["text"] or "CTX-0101-003" in r["text"]))
    check("Q18 detection stands", "stands" in r["text"].lower() or "neither note is a formal correction" in r["text"].lower())

    print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks passed")
    con.close()
    return sum(RESULTS), len(RESULTS)


if __name__ == "__main__":
    run()
