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
    monkeypatch.setattr(config, "FEED_JSON", docs / "feed.json")
    monkeypatch.setattr(config, "DOCS_CSV", docs / "ghl_copy.csv")
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
    ("MORRIS, KATHERINE A & JURINA, DONOVAN", "Katherine A", "Morris"),
    ("SMITH JOHN & JANE", "John", "Smith"),
    ("JONES MARY AND JONES TOM", "Mary", "Jones"),
    ("LEWIS JEANETT ET AL", "Jeanett", "Lewis"),
    ("ROBERT MURRAY,JR. & MARY MURRAY", "Robert", "Murray Jr."),
])
def test_owner_name_split(owner, first, last):
    import store
    assert store._split_name(owner)[:2] == (first, last)


def test_owner_class():
    assert normalize.owner_class("CITY OF CLEVELAND LAND REUTILIZATION PROGRAM") == "government"
    assert normalize.owner_class("CUYAHOGA COUNTY LAND REUTILIZATION CORP") == "government"
    assert normalize.owner_class("TMDW PROPERTIES LLC") == "entity"
    assert normalize.owner_class("COOK, BRITTANY") == "individual"


def test_institutional_owner_class():
    assert normalize.owner_class("TRYON STREET AQQUISITION TRUST I REO HOLDINGS, LLC") == "institutional"
    assert normalize.owner_class("HERO HOMES JV 2 ENCORE BORROWER, LLC") == "institutional"
    assert normalize.owner_class("HERO HOMES LLC") == "entity"          # 'HERO' must not match REO
    assert normalize.owner_class("TMDW PROPERTIES LLC") == "entity"


@pytest.mark.parametrize("status,closed", [
    ("Payment Received", True), ("Appeal Granted", True), ("Closed Payment Received", True),
    ("Sent to DAL", False), ("Court Stat", False), ("Post&Photo (NC)", False), ("Awaiting Payment - Late", False),
])
def test_real_status_wording(status, closed):
    assert normalize.is_closed(status, False) is closed


def test_stage_and_owner_type_scoring(env):
    import scoring
    from datetime import date
    base = {"source": "violation_notices", "case_number": "X", "address": "1 TEST ST", "parcel": "5",
            "zip": "", "date": "2026-10-01", "type": "", "description": "", "amount": None,
            "ward": "", "neighborhood": "", "closed": False}

    class L:
        def __init__(self, name):
            self.n = name

        def lookup(self, p, a):
            return {"owner_name": self.n, "mailing_address": "1 TEST ST, CLEVELAND, OH 44101"}

    def score(status, owner):
        return scoring.build_leads([{**base, "status": status}], L(owner), date(2026, 10, 30))[0]["score"]

    plain = score("Open", "DOE JANE")
    assert score("VN Mailed by Clerk", "DOE JANE") == plain             # just a mailing, not a court stage
    assert score("Filed w/Clerk of Courts", "DOE JANE") == plain + 10
    assert score("Court Stat", "DOE JANE") == plain + 10
    assert score("Demo Packet to Legal", "DOE JANE") == plain + 15          # demolition outranks court
    assert score("Open", "DOE JANE") == score("Open", "TMDW PROPERTIES LLC") + 10
    assert score("Open", "TMDW PROPERTIES LLC") - score("Open", "X BORROWER LLC") == 15


REAL_FIELDS = {   # field names confirmed from the live services (discovery output, Oct 2026)
    "violation_notices": ["OBJECTID", "V_PERMIT_ID", "FILE_DATE", "RECORD_STATUS", "ISSUE_DATE", "ACTIVE_VIOLATION",
                          "ADDRESS", "PARCEL_NUMBER", "TYPE_OF_VIOLATION", "VIOLATION_CITATIONS",
                          "ADDITIONAL_CITATION_DETAILS", "DW_Parcel", "DW_Neighborhood"],
    "civil_tickets": ["OBJECTID", "TICKET_ID", "FILE_DATE", "TICKET_STATUS", "ISSUE_DATE", "ADDRESS",
                      "PARCEL_NUMBER", "TICKET_CITATIONS", "ADDITIONAL_CITATION_DETAILS", "DW_Parcel"],
    "board_ups": ["OBJECTID", "PERMIT_ID", "PERMIT_TYPE", "PRIMARY_ADDRESS", "PARCEL_NUMBER", "FILE_DATE",
                  "ISSUE_DATE", "DW_Parcel"],
    "condemnations": ["OBJECTID", "Parcel_Number", "Active_Condemnation", "Address", "Condemnation_Date", "DW_Parcel"],
}
EXPECTED = {
    "violation_notices": {"case_number": "V_PERMIT_ID", "address": "ADDRESS", "status": "RECORD_STATUS",
                          "date": "ISSUE_DATE", "type": "TYPE_OF_VIOLATION", "parcel": "DW_Parcel"},
    "civil_tickets": {"case_number": "TICKET_ID", "status": "TICKET_STATUS", "type": "TICKET_CITATIONS"},
    "board_ups": {"case_number": "PERMIT_ID", "address": "PRIMARY_ADDRESS", "date": "ISSUE_DATE"},
    "condemnations": {"address": "Address", "date": "Condemnation_Date", "parcel": "DW_Parcel"},
}


@pytest.mark.parametrize("source", list(EXPECTED))
def test_real_schema_roles_detected(source):
    import fields
    for role, field in EXPECTED[source].items():
        assert fields.detect_field(REAL_FIELDS[source], role, source) == field, (source, role)


def test_real_county_mailing_fields_build_a_full_street_address():
    import enrich
    row = {"parcel_id": "12345678", "parcel_owner": "SMITH JOHN A", "mail_addr_street": "10 MAIN ST",
           "mail_unit_no": "APT 2", "mail_city": "PHOENIX", "mail_state": "AZ", "mail_zip": "85001",
           "par_addr_all": "1234 E 105TH ST, CLEVELAND, OH, 44108"}
    lk = enrich.OwnerLookup()
    lk.add_rows([row])
    got = lk.lookup("12345678", "")
    assert got["mailing_address"] == "10 MAIN ST APT 2, PHOENIX, AZ 85001"
    assert lk.lookup("", "1234 E 105 ST")["owner_name"] == "SMITH JOHN A"     # address fallback via par_addr_all


def test_payment_received_ticket_is_closed():
    assert normalize.is_closed("Payment Received", False)
    assert normalize.is_closed("Closed Payment Received", False)
    assert not normalize.is_closed("Awaiting Payment - Late", False)
    assert not normalize.is_closed("Sent to DAL", False)


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
    assert rows[0]["Owner Full Name"] == "SMITH JOHN A"
    assert list(rows[0].keys())[-1] == "Owner Full Name"      # appended last: existing GHL mappings unchanged
    dash = json.loads(config.DASHBOARD_JSON.read_text())
    assert dash["meta"]["lead_count"] == 2


def test_dashboard_feed_and_published_csv(env):
    run(env)
    feed = json.loads(config.FEED_JSON.read_text())
    assert feed["meta"]["feed_count"] == len(feed["leads"]) and feed["meta"]["lead_count"] == 2
    assert all(l["score"] >= config.FEED_MIN_SCORE for l in feed["leads"])
    assert feed["leads"][0]["score"] >= feed["leads"][-1]["score"]                 # sorted best first
    assert {"lead_id", "property_address", "owner_name", "mailing_address", "score_reasons"} <= set(feed["leads"][0])
    assert "description" not in feed["leads"][0]                                    # slim: no long text fields
    assert config.DOCS_CSV.read_bytes() == config.GHL_CSV.read_bytes()             # CSV published next to the page


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


def test_foreclosure_json_feed_matches_by_hyphenated_parcel_and_by_address(env, tmp_path):
    feed = tmp_path / "cuyahoga.json"
    feed.write_text(json.dumps({"leads": [
        {"parcel_number": "123-45-678", "property_address": "9999  OTHER  ST"},      # parcel match only
        {"parcel_number": "", "property_address": "3300  W 25TH ST"},                  # address match only
        {"parcel_number": "555-55-555", "property_address": "1 NOWHERE RD"},           # no match
    ]}))
    run(env, "--foreclosure-source", str(feed))
    L = leads()
    assert L["P:12345678"]["in_foreclosure"] and L["P:33333333"]["in_foreclosure"]
    assert any("foreclosure" in r for r in L["P:12345678"]["score_reasons"])
    base = json.loads(json.dumps(L["P:33333333"]))
    run(env)                                                                          # without the feed: no bonus
    assert not leads()["P:33333333"]["in_foreclosure"]
    assert leads()["P:33333333"]["score"] == base["score"] - 25


def test_unreadable_foreclosure_source_is_skipped_not_fatal(env, tmp_path):
    assert run(env, "--foreclosure-source", str(tmp_path / "missing.json"),
               "--foreclosure-source", "https://invalid.invalid/x.json") == 0
    assert not leads()["P:12345678"]["in_foreclosure"]


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


def test_housing_court_criminal_cases():
    import housing_court
    rows = [
        {"Case Number": "2026-CRB-000316", "Party": "14809 Rochelle Ave", "Extended Connection": "Property Address",
         "Offenses (Offense Date)": "10/1/3101(e) Maintenance Exterior Property Area (12/26/2025)",
         "File Date": "2026-01-13 00:00:00", "Current Case Status": "Active",
         "Last Event Type Description": "Housing Criminal Arraignment"},
        {"Case Number": "2026-CRB-000316", "Party": "Zada, Ahmad", "Extended Connection": "Defendant",
         "Offenses (Offense Date)": "10/1/3101(e) Maintenance Exterior Property Area (12/26/2025)",
         "File Date": "2026-01-13 00:00:00", "Current Case Status": "Active",
         "Last Event Type Description": "Housing Criminal Arraignment"},
        {"Case Number": "2026-CRB-000400", "Party": "1 Closed St", "Extended Connection": "Property Address",
         "File Date": "2026-01-20 00:00:00", "Current Case Status": "Closed"},
    ]
    sigs = housing_court.to_signals(rows)
    assert len(sigs) == 2
    s = next(x for x in sigs if x["case_number"] == "2026-CRB-000316")
    assert s["address"].upper().startswith("14809 ROCHELLE") and s["date"] == "2026-01-13" and not s["closed"]
    assert "Zada, Ahmad" in s["description"] and "(12/26/2025)" not in s["type"]
    assert next(x for x in sigs if x["case_number"] == "2026-CRB-000400")["closed"]
