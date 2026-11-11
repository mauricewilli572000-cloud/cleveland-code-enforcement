"""Cleveland Housing Court criminal housing-code cases (owners charged with code violations).

Source: the Housing Court's "Criminal Cases Filed" report (Excel), linked from
https://www.clevelandhousingcourt.org/docket . One row per party; each case has a
"Property Address" row and one or more "Defendant" rows.
"""
import io
import logging
import re
from datetime import datetime

import requests

import config
from normalize import is_closed, redact, split_address

log = logging.getLogger(__name__)
SOURCE_KEY = "housing_criminal"


_SUFFIXES = {"AVENUE": "AVE", "AV": "AVE", "STREET": "ST", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD",
             "LANE": "LN", "COURT": "CT", "PLACE": "PL", "PARKWAY": "PKWY", "CIRCLE": "CIR", "TERRACE": "TER"}
_DIRS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}


def county_style(addr: str) -> str:
    """'7806 Lawn Avenue' -> '7806 LAWN AVE', '1437 W 85Th St' -> '1437 W 85TH ST' (how the parcel table writes it)."""
    toks = re.sub(r"[.,]", " ", str(addr or "")).upper().split()
    if len(toks) > 2:
        toks[-1] = _SUFFIXES.get(toks[-1], toks[-1])
    if len(toks) > 2:
        toks[1] = _DIRS.get(toks[1], toks[1])
    return " ".join(toks)


def _iso(v) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(v or ""))
    return m.group(1) if m else ""


def fetch_rows(url: str | None = None) -> list[dict]:
    import openpyxl
    r = requests.get(url or config.HOUSING_COURT_CRIMINAL_URL, timeout=config.REQUEST_TIMEOUT,
                     headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    ws = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True).worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = [str(c or "").strip() for c in next(it)]
    return [dict(zip(header, row)) for row in it if any(row)]


def to_signals(rows: list[dict]) -> list[dict]:
    """Group the per-party rows into one signal per case."""
    cases: dict[str, dict] = {}
    for r in rows:
        num = str(r.get("Case Number") or "").strip()
        if not num:
            continue
        c = cases.setdefault(num, {"address": "", "defendants": [], "row": r})
        role = str(r.get("Extended Connection") or "").strip().lower()
        party = str(r.get("Party") or "").strip()
        if role == "property address":
            c["address"] = party
        elif role == "defendant" and party:
            c["defendants"].append(party)
    out = []
    for num, c in cases.items():
        address, zipc = split_address(c["address"])
        address = county_style(address)
        if not address:
            continue
        r = c["row"]
        status = str(r.get("Current Case Status") or "").strip()
        offense = re.sub(r"\s*\(\d{1,2}/\d{1,2}/\d{4}\)", "", str(r.get("Offenses (Offense Date)") or "")).strip()
        last = str(r.get("Last Event Type Description") or "").strip()
        out.append({
            "source": SOURCE_KEY,
            "label": config.SOURCES[SOURCE_KEY]["label"],
            "case_number": num,
            "address": address,
            "parcel": "",
            "zip": zipc,
            "date": _iso(r.get("File Date")),
            "status": f"{status} - {last}".strip(" -"),
            "closed": is_closed(status, False),
            "type": offense[:120],
            "description": redact(f"Defendant: {', '.join(c['defendants'])}. Last event: {last}")[:300],
            "amount": None,
            "ward": "",
            "neighborhood": "",
        })
    return out
