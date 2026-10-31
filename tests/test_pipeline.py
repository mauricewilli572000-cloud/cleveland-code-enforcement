"""Offline tests. All fixture data below is SYNTHETIC (made up) - field names are
the scraper's best guess at the Cleveland schema, not confirmed live names."""
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import normalize  # noqa: E402
import run_pipeline  # noqa: E402

TODAY = "2026-10-30"


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


def make_fixtures(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    vn = [
        # kept: open, recent, has parcel
        {"DW_RecordID": "VN-1", "DW_Address": "1234 E 105TH ST, CLEVELAND, OH, 44108", "DW_Parcel": "12345678",
         "DW_Opened_Date": ms("2026-09-20"), "DW_Status": "Open", "DW_Record_Type": "Exterior Violation",
         "DW_Description": "Call owner at 216-555-0101 or bob@example.com about the roof", "DW_Zip": "44108"},
        # dropped: closed -> and closes lead P:99999999 if present
        {"DW_RecordID": "VN-2", "DW_Address": "55 CLOSED AVE", "DW_Parcel": "99999999",
         "DW_Opened_Date": ms("2026-10-01"), "DW_Status": "Closed - Compliance"},
        # dropped: unit address
        {"DW_RecordID": "VN-3", "DW_Address": "700 SUPERIOR AVE APT 4", "DW_Parcel": "11111111",
         "DW_Opened_Date": ms("2026-10-10"), "DW_Status": "Open"},
        # dropped: older than look-back
        {"DW_RecordID": "VN-4", "DW_Address": "9 OLD ST", "DW_Parcel": "22222222",
         "DW_Opened_Date": ms("2025-01-10"), "DW_Status": "Open"},
        # dropped: no address and no parcel
        {"DW_RecordID": "VN-5", "DW_Opened_Date": ms("2026-10-10"), "DW_Status": "Open"},
    ]
    cond = [
        # same property as VN-1 -> grouped, multi-type bonus
        {"DW_RecordID": "CD-1", "DW_Address": "1234 E 105 ST", "DW_Parcel": "123-45-678",
         "DW_Date": ms("2026-03-01"), "DW_Record_Type": "Condemnation"},
        {"DW_RecordID": "CD-2", "DW_Address": "3300 W 25 ST", "DW_Parcel": "33333333",
         "DW_Date": ms("2026-08-01")},
        # city/land-bank owned -> dropped unless --include-public
        {"DW_RecordID": "CD-3", "DW_Address": "13710 EARLWOOD RD, CLEVELAND, OH, 44110",
         "DW_Parcel": "44444444", "DW_Date": ms("2026-02-01")},
    ]
    tickets = [
        {"DW_RecordID": "CT-1", "DW_Address": "3300 W 25 ST", "DW_Parcel": "33333333",
         "DW_Opened_Date": ms("2026-10-05"), "DW_Status": "Issued", "Fine_Amount": "$500.00"},
    ]
    boards = []
    parcels = [
        {"parcel_id": "12345678", "deeded_owner": "SMITH JOHN A", "tax_address": "10 MAIN ST",
         "tax_city": "PHOENIX", "tax_state": "AZ", "tax_zip": "85001", "par_addr": "1234 E 105 ST"},
        {"parcel_id": "33333333", "deeded_owner": "WESTSIDE HOLDINGS LLC", "tax_address": "1 PUBLIC SQ",
         "tax_city": "CLEVELAND", "tax_state": "OH", "tax_zip": "44113", "par_addr": "3300 W 25 ST"},
        {"parcel_id": "44444444", "deeded_owner": "CITY OF CLEVELAND LAND REUTILIZATION PROGRAM",
         "tax_address": "601 LAKESIDE AVE", "tax_city": "CLEVELAND", "tax_state": "OH", "tax_zip": "44114",
         "par_addr": "13710 EARLWOOD RD, CLEVELAND, OH, 44110"},
    ]
    for name, rows in [("violation_notices", vn), ("condemnations", cond), ("civil_tickets", tickets),
                       ("board_ups", boards), ("parcels", parcels)]:
        (d / f"{name}.json").write_text(json.dumps(rows))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    docs = tmp_path / "docs"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DOCS_DIR", docs)
    monkeypatch.setattr(config, "RECORDS_JSON", data / "records.json")
    monkeypatch.setattr(config, "GHL_CSV", data / "ghl.csv")
    monkeypatch.setattr(config, "DASHBOARD_JSON", docs / "dash.json")
    monkeypatch.setattr(config, "FIELD_OVERRIDES", data / "field_overrides.json")
    monkeypatch.setattr(config, "SERVICE_URL_CACHE", data / "service_urls.json")
    fx = tmp_path / "fx"
    make_fixtures(fx)
    return fx


def run(fx, *extra):
    return run_pipeline.main(["--offline", str(fx), "--today", TODAY, *extra])


def leads():
    return json.loads(config.RECORDS_JSON.read_text())["leads"]


# ---- unit tests ----------------------------------------------------------------
@pytest.mark.parametrize("addr,expected", [
    ("700 SUPERIOR AVE APT 4", True), ("12 W 3 ST #5", True), ("4400 LORAIN AVE 2", True),
    ("1234 E 105 ST", False), ("3300 W 25 ST", False), ("8800 SUPERIOR AVE", False),
])
def test_has_unit(addr, expected):
    assert normalize.has_unit(addr) is expected


def test_address_key_matches_variants():
    assert normalize.address_key("1234 E. 105th Street") == normalize.address_key("1234 E 105 ST")


def test_split_address_strips_city_state_zip():
    assert normalize.split_address("1130 E 113TH ST, CLEVELAND, OH, 44108") == ("1130 E 113TH ST", "44108")
    assert normalize.split_address("123 main st, apt 4, cleveland, oh, 44102") == ("123 MAIN ST, APT 4", "44102")
    assert normalize.split_address("3300 W 25 ST") == ("3300 W 25 ST", "")


def test_unit_detected_after_city_is_stripped():
    street, _ = normalize.split_address("4400 LORAIN AVE 2, CLEVELAND, OH, 44102")
    assert normalize.has_unit(street)


@pytest.mark.parametrize("owner,first,last", [
    ("COOK, BRITTANY", "Brittany", "Cook"),
    ("MILEV, MILEN K.", "Milen K.", "Milev"),
    ("RUSSELL ALFONSO T", "Alfonso T", "Russell"),
    ("ROBERT MURRAY,JR.", "Robert", "Murray Jr."),
])
def test_owner_name_split(owner, first, last):
    import store
    assert store._split_name(owner)[:2] == (first, last)


def test_owner_class():
    assert normalize.owner_class("CITY OF CLEVELAND LAND REUTILIZATION PROGRAM") == "government"
    assert normalize.owner_class("CUYAHOGA COUNTY LAND REUTILIZATION CORP") == "government"
    assert normalize.owner_class("TMDW PROPERTIES LLC") == "entity"
    assert normalize.owner_class("COOK, BRITTANY") == "individual"


def test_redact():
    out = normalize.redact("call 216-555-0101 or bob@example.com")
    assert "555" not in out and "@" not in out


# ---- pipeline ----------------------------------------------------------------
def test_pipeline_filters_groups_and_enriches(env):
    assert run(env) == 0
    L = leads()
    assert set(L) == {"P:12345678", "P:33333333"}      # closed, unit, old, blank all dropped

    a = L["P:12345678"]
    assert a["open_records"] == 2 and len(a["lead_types"]) == 2          # VN + condemnation grouped
    assert a["owner_name"] == "SMITH JOHN A"
    assert a["property_address"] == "1234 E 105TH ST" and a["zip"] == "44108"    # city/state/zip stripped
    assert a["out_of_state_owner"] is True
    assert "216" not in a["description"] and "@" not in a["description"]
    assert a["score"] >= 80

    b = L["P:33333333"]
    assert b["owner_is_entity"] is True and b["total_fines"] == 500.0
    assert b["out_of_state_owner"] is False


def test_exports(env):
    run(env)
    raw = config.GHL_CSV.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(open(config.GHL_CSV, newline="", encoding="utf-8-sig")))
    assert len(rows) == 2 and rows[0]["Lead Source"] == "Cleveland Code Enforcement"
    assert rows[0]["First Name"] == "John A" and rows[0]["Last Name"] == "Smith"
    assert rows[1]["Company Name"] == "WESTSIDE HOLDINGS LLC"
    dash = json.loads(config.DASHBOARD_JSON.read_text())
    assert dash["meta"]["lead_count"] == 2


def test_include_flags(env):
    run(env, "--include-units", "--include-closed")
    assert {"P:11111111", "P:99999999"} <= set(leads())


def test_public_owned_dropped_by_default_and_kept_with_flag(env):
    run(env)
    assert "P:44444444" not in leads()
    run(env, "--include-public")
    assert leads()["P:44444444"]["owner_class"] == "government"
    run(env)                                           # default run removes it again
    assert "P:44444444" not in leads()


def test_closed_case_removes_existing_lead(env):
    run(env)
    assert "P:33333333" in leads()
    rows = json.loads((env / "civil_tickets.json").read_text())
    rows[0]["DW_Status"] = "Closed"
    (env / "civil_tickets.json").write_text(json.dumps(rows))
    cond = json.loads((env / "condemnations.json").read_text())
    (env / "condemnations.json").write_text(json.dumps([c for c in cond if c["DW_RecordID"] != "CD-2"]))
    run(env)
    assert "P:33333333" not in leads()
    assert "P:12345678" in leads()


def test_added_date_is_preserved(env):
    run(env)
    first = leads()["P:12345678"]["added_date"]
    run_pipeline.main(["--offline", str(env), "--today", "2026-11-02"])
    assert leads()["P:12345678"]["added_date"] == first


def test_field_override(env):
    rows = json.loads((env / "violation_notices.json").read_text())
    rows[0]["Weird_Addr_Col"] = "1234 E 105 ST"
    rows[0].pop("DW_Address")
    (env / "violation_notices.json").write_text(json.dumps(rows))
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.FIELD_OVERRIDES.write_text(json.dumps({"violation_notices": {"address": "Weird_Addr_Col"}}))
    run(env)
    assert leads()["P:12345678"]["property_address"] == "1234 E 105 ST"
