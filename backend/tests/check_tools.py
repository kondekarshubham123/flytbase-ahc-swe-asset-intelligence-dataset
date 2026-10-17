"""Quick deterministic checks of the analytical tools against the expected
answers in 03-evaluation-queries.md. Run: python -m tests.check_tools
"""
from __future__ import annotations

from datetime import date

from app.ingest.loader import build, connect
from app import config
from app.tools import analytics as A
from app.tools.resolve import resolve_area as RA

TODAY = config.TODAY.date()


def line(t, ok, got, exp):
    print(f"[{'PASS' if ok else 'FAIL'}] {t}: got={got} exp={exp}")
    return ok


def main():
    con = connect()
    try:
        con.execute("SELECT 1 FROM v_spills LIMIT 1")
    except Exception:
        build(con=con, verbose=False)
    results = []

    # Q1 — WELL-0001 not producing, last inspection INS-0001-0203 by Ryan Brooks
    r = A.well_status(con, "WELL-0001", as_of=TODAY)
    d = r.data
    results.append(line("Q1 producing False", d["producing"] is False, d["producing"], False))
    results.append(line("Q1 delta 3.2", d["delta_c"] == 3.2, d["delta_c"], 3.2))
    results.append(line("Q1 last insp", d["latest"]["inspection_id"] == "INS-0001-0203",
                        d["latest"]["inspection_id"], "INS-0001-0203"))
    results.append(line("Q1 has planned-shutin note", any("planned" in (x["text"] or "").lower()
                        for x in d["reasons"]), d["reasons"], "CTX-0001-003 planned"))

    # Q2 — NE Block 8 not producing now → 4 T200 wells
    area = RA(con, "Northeast Block 8")
    cs = A.current_status(con, area_id=area, as_of=TODAY)
    down = [x for x in cs.data["rows"] if not x["producing"]]
    results.append(line("Q2 NE-B8 area", area == "NE-08", area, "NE-08"))
    results.append(line("Q2 count 4", len(down) == 4, [x["asset_id"] for x in down],
                        ["WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175"]))
    results.append(line("Q2 WF-0170 reason repair", any(
        x["id"] == "MNT-0170-031" for r_ in down if r_["asset_id"] == "WELL-0170" for x in r_["reasons"]),
        [x["reasons"] for x in down if x["asset_id"] == "WELL-0170"], "MNT-0170-031"))

    # Q3 — 13 weeks series for WELL-0001
    ts = A.temp_series(con, "WELL-0001", start=date(2026, 7, 23), end=TODAY)
    results.append(line("Q3 13 readings", len(ts.data["rows"]) == 13, len(ts.data["rows"]), 13))
    nonprod = [x["inspected_at"].date() for x in ts.data["rows"] if not x["producing"]]
    results.append(line("Q3 nonproducing 8&15 Oct", nonprod == [date(2026, 10, 8), date(2026, 10, 15)],
                        nonprod, ["2026-10-08", "2026-10-15"]))

    # Q4 — 6 spills 15.1 bbl
    sp = A.spills(con, asset_ids=["WELL-0001"], start=date(2022, 10, 20), end=TODAY)
    results.append(line("Q4 count 6", sp.data["count"] == 6, sp.data["count"], 6))
    results.append(line("Q4 total 15.1", abs(sp.data["total_bbl"] - 15.1) < 0.01, sp.data["total_bbl"], 15.1))

    # Q5 — 21 days in 2025
    dt = A.downtime(con, asset_ids=["WELL-0001"], start=date(2025, 1, 1), end=date(2025, 12, 31))
    results.append(line("Q5 21 days", dt.data["rows"][0]["downtime_days"] == 21,
                        dt.data["rows"][0]["downtime_days"], 21))

    # Q6 — NE Block 7 top3 last 4 months
    area7 = RA(con, "Northeast Block 7")
    d6 = A.downtime(con, area_id=area7, start=date(2026, 6, 16), end=TODAY, top=3)
    got6 = sorted(((r_["asset_id"], int(r_["downtime_days"])) for r_ in d6.data["rows"]))
    results.append(line("Q6 top3 NE-B7", got6 == sorted([("WELL-0121", 49), ("WELL-0138", 42), ("WELL-0107", 42)]),
                        got6, [("WELL-0107", 42), ("WELL-0121", 49), ("WELL-0138", 42)]))

    # Q7 — map spills NE B8 last 90 days → 4
    area8 = RA(con, "Northeast Block 8")
    sp7 = A.spills_map(con, area_id=area8, start=date(2026, 7, 18), end=TODAY)
    got7 = sorted((r_["asset_id"], r_["volume_bbl"]) for r_ in sp7.data["rows"])
    results.append(line("Q7 4 pins", len(got7) == 4, got7,
                        [("WELL-0151", 4.5), ("WELL-0165", 1.2), ("WELL-0175", 3.9), ("WELL-0186", 0.6)]))

    # Q8 — 2026 heatmap totals per area
    hm = A.spills_heatmap(con, 2026)
    totals = {r_["area_name"]: r_["total"] for r_ in hm.data["area_totals"]}
    exp8 = {"Southwest Block 23": 35, "Southwest Block 24": 43, "Northeast Block 7": 37, "Northeast Block 8": 26}
    results.append(line("Q8 area totals", totals == exp8, totals, exp8))

    # Q9 — WELL-0001 breaches last 12 months: SR-01, SR-02, SR-03, RC-04
    comp = A.compliance_audit(con, "WELL-0001", months=12, as_of=TODAY)
    protos = sorted({b["protocol"] for b in comp.data["breaches"]})
    results.append(line("Q9 breaches SR-01/02/03/RC-04",
                        protos == ["RC-04", "SR-01", "SR-02", "SR-03"], protos,
                        ["RC-04", "SR-01", "SR-02", "SR-03"]))

    # Q10 — SW B24 late containment last 90 days → 2
    area24 = RA(con, "Southwest Block 24")
    cc = A.containment_check(con, area_id=area24, start=date(2026, 7, 18), end=TODAY)
    got10 = sorted(r_["asset_id"] for r_ in cc.data["rows"])
    results.append(line("Q10 2 late", got10 == ["WELL-0082", "WELL-0100"], got10,
                        ["WELL-0082", "WELL-0100"]))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    con.close()
    return sum(results), len(results)


if __name__ == "__main__":
    main()
