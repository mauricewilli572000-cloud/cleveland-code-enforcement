import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import normalize  # noqa: E402
import run_pipeline  # noqa: E402

ROW = {"PERMIT_ID": "BRB26-044521", "PERMIT_SUBTYPE": "Residential", "PRIMARY_ADDRESS": "13710 RUGBY RD, CLEVELAND, OH, 44110",
       "PRIMARY_PARCEL": "11122127", "DW_Parcel": "11122127", "ISSUED_DATE": 1791992532000, "CLOSED_DATE": 1793854800000,
       "Contrator_Business_Name": "T & T DEMOLITION"}


def test_demolition_signal():
    s = normalize.to_signal("demolitions", ROW)
    assert s["case_number"] == "BRB26-044521" and s["parcel"] == "11122127"
    assert s["status"].startswith("demolished 2026-") and not s["closed"]
    pending = normalize.to_signal("demolitions", {**ROW, "CLOSED_DATE": None})
    assert "still standing" in pending["status"]


def test_demolitions_keep_a_year_then_age_out():
    recent = dict(ROW, ISSUED_DATE=int((date.today() - timedelta(days=200)).strftime("%s")) * 1000)
    old = dict(ROW, PERMIT_ID="OLD", ISSUED_DATE=int((date.today() - timedelta(days=400)).strftime("%s")) * 1000)
    since = date.today() - timedelta(days=21)
    sigs, _, stats = run_pipeline.collect_signals({"demolitions": [recent, old]}, since, False, False)
    assert [s["case_number"] for s in sigs] == ["BRB26-044521"] and stats["demolitions"]["old"] == 1
