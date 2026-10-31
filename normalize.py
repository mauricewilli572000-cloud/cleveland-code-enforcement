"""Turn raw dataset rows into normalized lead signals."""
import re

import config
import fields
from arcgis_client import epoch_ms_to_iso

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")

UNIT_RE = re.compile(
    r"(#|\b(APT|APARTMENT|UNIT|STE|SUITE|FLR|FLOOR|BLDG|BUILDING|RM|ROOM)\b)", re.I
)
STREET_SUFFIX = (
    r"ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|BLVD|BOULEVARD|CT|COURT|LN|LANE|PL|PLACE|"
    r"WAY|PKWY|PARKWAY|TER|TERRACE|CIR|CIRCLE|HWY|AV"
)
TRAILING_UNIT_RE = re.compile(rf"\b(?:{STREET_SUFFIX})\.?\s+(?:\d{{1,4}}|[A-Z])\s*$", re.I)

ENTITY_WORDS = (
    "LLC", "L L C", "INC", "CORP", "CORPORATION", "LTD", "LP", "LLP", "TRUST", "TRUSTEE",
    "ESTATE", "HOLDINGS", "PROPERTIES", "INVESTMENTS", "BANK", "COMPANY", "CO ", "ASSOC",
    "ASSOCIATION", "HOUSING", "CITY OF", "COUNTY", "CHURCH", "MINISTRIES", "LAND BANK",
    "REALTY", "PARTNERS", "ENTERPRISES", "FUND", "MORTGAGE",
)


def redact(text) -> str:
    if not text:
        return ""
    t = EMAIL_RE.sub("[email]", str(text))
    t = PHONE_RE.sub("[phone]", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_address(addr) -> str:
    return re.sub(r"\s+", " ", str(addr or "")).strip().upper()


# The city datasets give "1130 E 113TH ST, CLEVELAND, OH, 44108"; we want street + zip separately.
CSZ_RE = re.compile(r"^(?P<street>.*?),\s*[A-Z .'\-]+,\s*(?:OH|OHIO)\b[,\s]*(?P<zip>\d{5})?(?:-\d{4})?\s*$")


def split_address(addr) -> tuple[str, str]:
    """(street, zip). Strips a trailing ', CITY, OH, ZIP' if present."""
    a = clean_address(addr)
    m = CSZ_RE.match(a)
    if m:
        return m.group("street").strip(" ,"), m.group("zip") or ""
    return a, ""


GOVERNMENT_WORDS = (
    "CITY OF ", "COUNTY OF ", "STATE OF ", "CUYAHOGA COUNTY", "LAND REUTILIZATION", "LAND BANK",
    "UNITED STATES", "METROPOLITAN HOUSING", "CMHA", "BOARD OF EDUCATION", "MUNICIPAL SCHOOL",
    "CMSD", "PORT AUTHORITY", "DEPARTMENT OF", "SECRETARY OF HOUSING", "HUD ", "NEORSD",
    "SEWER DISTRICT", "PUBLIC LIBRARY", "CLEVELAND METROPOLITAN",
)


def owner_class(name: str) -> str:
    """'government' (city/county/land bank/housing authority), 'entity', or 'individual'."""
    n = f" {str(name or '').upper()} "
    if not n.strip():
        return ""
    if any(w in n for w in GOVERNMENT_WORDS):
        return "government"
    return "entity" if is_entity(name) else "individual"


def has_unit(addr: str) -> bool:
    a = clean_address(addr)
    return bool(UNIT_RE.search(a) or TRAILING_UNIT_RE.search(a))


def norm_parcel(value) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


_DIR = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD",
    "COURT": "CT", "LANE": "LN", "PLACE": "PL", "PARKWAY": "PKWY", "TERRACE": "TER", "CIRCLE": "CIR",
}


def address_key(addr) -> str:
    """number + street name only (drops direction, suffix, ordinal letters) for fuzzy joins."""
    a = re.sub(r"[.,]", " ", clean_address(addr))
    toks = a.split()
    if not toks:
        return ""
    num = toks[0]
    rest = []
    for t in toks[1:]:
        t = _SUFFIX.get(t, t)
        if t in _DIR or t in _SUFFIX.values():
            continue
        rest.append(re.sub(r"(?<=\d)(ST|ND|RD|TH)$", "", t))
    return f"{num} {' '.join(rest)}".strip()


def is_entity(name: str) -> bool:
    n = f" {str(name or '').upper()} "
    return any(f" {w.strip()} " in n or w in n for w in ENTITY_WORDS)


def is_closed(status, all_active: bool) -> bool:
    s = str(status or "").strip().lower()
    if not s:
        return False if all_active else False  # unknown status: keep, scorer notes it
    return any(w in s for w in config.CLOSED_STATUS_WORDS)


def to_signal(source_key: str, attrs: dict) -> dict | None:
    """Normalize one raw row. Returns None if there is no usable address/parcel."""
    src = config.SOURCES[source_key]
    idx = fields.build_index(attrs)

    def get(role):
        return fields.pick(attrs, role, source_key, idx)

    address, addr_zip = split_address(get("address"))
    parcel = norm_parcel(get("parcel"))
    if not address and not parcel:
        return None
    raw_amount = get("amount")
    try:
        amount = float(str(raw_amount).replace("$", "").replace(",", "")) if raw_amount not in (None, "") else None
    except ValueError:
        amount = None
    status = get("status")
    return {
        "source": source_key,
        "label": src["label"],
        "case_number": str(get("case_number") or "").strip(),
        "address": address,
        "parcel": parcel,
        "zip": (str(get("zip") or "").strip()[:5] or addr_zip),
        "date": epoch_ms_to_iso(get("date")),
        "status": str(status or "").strip(),
        "closed": is_closed(status, src["all_active"]),
        "type": str(get("type") or "").strip(),
        "description": redact(get("description"))[:300],
        "amount": amount,
        "ward": str(get("ward") or "").strip(),
        "neighborhood": str(get("neighborhood") or "").strip(),
    }


def property_key(sig: dict) -> str:
    """Group key: parcel when known, else normalized address."""
    return f"P:{sig['parcel']}" if sig.get("parcel") else f"A:{address_key(sig['address'])}"
