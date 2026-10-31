"""Accumulate leads across runs and export the GHL CSV + dashboard feed."""
import csv
import json
import re
from datetime import date, datetime, timezone

import config
from normalize import is_entity

GHL_COLUMNS = [
    "First Name", "Last Name", "Company Name", "Phone", "Email",
    "Mailing Address", "Property Address", "City", "State", "Postal Code",
    "Parcel", "Lead Source", "Lead Type", "Score", "Open Records", "Total Fines",
    "Oldest Open Days", "In Foreclosure", "Out Of State Owner", "Case Numbers",
    "Violations", "Score Reasons", "Date Added", "Tags",
    "Owner Full Name",            # appended last so existing GHL column mappings don't shift
]


def load() -> dict:
    try:
        return json.loads(config.RECORDS_JSON.read_text())
    except (OSError, ValueError):
        return {"meta": {}, "leads": {}}


def merge(existing: dict, new_leads: list[dict], closed_keys: set[str], today: date) -> dict:
    leads = existing.get("leads", {})
    iso = today.isoformat()
    for k in closed_keys:
        leads.pop(k, None)                         # case closed -> no longer a lead
    for lead in new_leads:
        old = leads.get(lead["lead_id"])
        lead["added_date"] = old["added_date"] if old else iso
        lead["last_seen"] = iso
        # never lose an owner we already found
        if old and not lead["owner_name"] and old.get("owner_name"):
            for f in ("owner_name", "owner_is_entity", "mailing_address", "out_of_state_owner"):
                lead[f] = old[f]
        leads[lead["lead_id"]] = lead
    cutoff = config.PRUNE_AFTER_DAYS
    for k in [k for k, v in leads.items()
              if (today - date.fromisoformat(v.get("last_seen", iso))).days > cutoff]:
        del leads[k]
    existing["leads"] = leads
    existing["meta"] = {
        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lead_count": len(leads),
    }
    return existing


def save(state: dict):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.RECORDS_JSON.write_text(json.dumps(state, indent=1))


SUFFIXES = {"JR", "JR.", "SR", "SR.", "II", "III", "IV"}


_CO_OWNER_SPLIT = re.compile(r"\s+(?:&|AND)\s+", re.I)
_ET_AL = re.compile(r"\s+ET\s*(?:AL|UX|VIR)\b\.?", re.I)


def _split_name(owner: str) -> tuple[str, str, str]:
    """(first, last, company). County names are 'LAST, FIRST M', 'LAST FIRST M',
    or 'FIRST LAST,JR' (comma before a suffix). With co-owners ('A & B', 'ET AL')
    only the first named owner is split out; the full string goes in Owner Full Name."""
    if not owner:
        return "", "", ""
    if is_entity(owner):
        return "", "", owner
    owner = _ET_AL.sub("", owner).strip()
    owner = _CO_OWNER_SPLIT.split(owner, maxsplit=1)[0].strip(" ,")
    if "," in owner:
        head, tail = [x.strip() for x in owner.split(",", 1)]
        if tail.upper() in SUFFIXES:                      # "ROBERT MURRAY,JR."
            parts = head.split()
            if len(parts) >= 2:
                return parts[0].title(), f"{' '.join(parts[1:])} {tail}".title(), ""
            return "", f"{head} {tail}".title(), ""
        return tail.title(), head.title(), ""             # "COOK, BRITTANY"
    parts = owner.split()
    if len(parts) > 1:
        return " ".join(parts[1:]).title(), parts[0].title(), ""
    return "", owner.title(), ""


def tags(lead: dict) -> str:
    t = ["cleveland-code-enforcement"]
    if lead["score"] >= 60:
        t.append("hot")
    if lead["in_foreclosure"]:
        t.append("foreclosure-overlap")
    if lead["out_of_state_owner"]:
        t.append("out-of-state-owner")
    if any("Condemnation" in x for x in lead["lead_types"]):
        t.append("condemned")
    if any("Board-Up" in x for x in lead["lead_types"]):
        t.append("boarded")
    return ",".join(t)


def export_ghl(state: dict, min_score: int = 0):
    rows = sorted(state["leads"].values(), key=lambda l: -l["score"])
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.GHL_CSV, "w", newline="", encoding="utf-8-sig") as fh:   # BOM so Excel/GHL read UTF-8
        w = csv.writer(fh)
        w.writerow(GHL_COLUMNS)
        for l in rows:
            if l["score"] < min_score:
                continue
            first, last, company = _split_name(l["owner_name"])
            w.writerow([
                first, last, company, "", "",
                l["mailing_address"], l["property_address"], "Cleveland", l["state"], l["zip"],
                l["parcel"], "Cleveland Code Enforcement", "; ".join(l["lead_types"]), l["score"],
                l["open_records"], l["total_fines"], l["oldest_open_days"],
                "Yes" if l["in_foreclosure"] else "", "Yes" if l["out_of_state_owner"] else "",
                "; ".join(l["case_numbers"]), "; ".join(l["violation_types"]),
                "; ".join(l["score_reasons"]), l["added_date"], tags(l),
                l["owner_name"],
            ])


def export_dashboard(state: dict):
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    rows = sorted(state["leads"].values(), key=lambda l: -l["score"])
    config.DASHBOARD_JSON.write_text(json.dumps({"meta": state["meta"], "leads": rows}, indent=1))
