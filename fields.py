"""Schema-tolerant field picking.

Field names in the Cleveland datasets (several use "DW_" data-warehouse
prefixed, cleaned versions next to the raw ones) could not be confirmed from the
build sandbox, so every role has an ordered list of candidate names. The first
candidate present with a non-empty value wins. data/field_overrides.json can
force a field for a role once discover_cleveland.py has shown the real names:

    {"violation_notices": {"address": "DW_Address"}, "*": {"parcel": "DW_Parcel"}}
"""
import json
import re

import config


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


CANDIDATES = {
    "case_number": [
        "v_permit_id", "ticket_id", "permit_id",          # confirmed real names (Oct 2026)
        "dw_recordid", "recordid", "record_id", "recordnumber", "record_number", "case_number",
        "casenumber", "vn_number", "violation_notice_number", "complaint_number", "cmp_number",
        "ticket_number", "permit_number", "permit_id", "boardup_number", "altid", "alt_id",
    ],
    "address": [
        "dw_address", "dw_full_address", "dw_site_address", "full_address", "fulladdress",
        "address", "primary_address", "primary_address", "site_address", "property_address", "street_address",
        "situs_address", "location_address", "addr",
    ],
    "parcel": [
        "dw_parcel", "dw_parcel_number", "dw_parcel_id", "parcel_number", "parcelnumber",
        "parcel_id", "parcelid", "parcel", "ppn", "pin", "parcelpin", "parcel_pin",
    ],
    "zip": ["dw_zip", "zip", "zipcode", "zip_code", "postal_code"],
    "date": [
        "dw_opened_date", "opened_date", "open_date", "date_opened", "dw_created_date",
        "created_date", "dw_filed_date", "filed_date", "issue_date", "issued_date",
        "dw_issue_date", "violation_date", "condemn_date", "condemnation_date",
        "dw_date", "date", "creation_date", "createdate",
    ],
    "status": [
        "dw_status", "status", "record_status", "current_status", "workflow_status",
        "violation_status", "case_status", "ticket_status",
    ],
    "type": [
        "type_of_violation", "violation_citations", "ticket_citations",
        "dw_record_type", "record_type", "dw_type", "type", "violation_type", "category",
        "dw_category", "complaint_type", "dw_subtype", "subtype", "record_subtype",
        "dw_violation", "violation", "ticket_type",
    ],
    "description": [
        "additional_citation_details",
        "dw_description", "description", "violation_description", "short_notes",
        "notes", "comments", "dw_comments", "detail", "details", "ordinance_description",
    ],
    "amount": [
        "fine_amount", "dw_fine_amount", "amount", "total_fine", "amount_due", "balance",
        "balance_due", "penalty_amount", "fee_total", "total_fees", "fines",
    ],
    "ward": ["dw_ward", "ward"],
    "neighborhood": ["dw_neighborhood", "neighborhood", "sna", "statistical_planning_area"],
    # parcel-layer roles ----------------------------------------------------
    "owner": [
        "deeded_owner", "deededowner", "parcel_owner", "owner", "owner_name", "own_name",
        "owner1", "par_owner", "ownername", "taxpayer_name", "tax_name", "owner_1",
    ],
    "mail_unit": ["mail_unit_no", "mail_unit", "mail_apt"],
    "mail_street": [
        "mail_addr_street",                                 # confirmed real name (county + Property Insights)
        "tax_address", "tax_mailing_address", "mailing_address", "mail_address",
        "mail_addr", "mail_street", "mailaddress", "mailing_street", "taxpayer_address",
        "owner_address", "own_addr", "tax_add_1", "tax_addr",
    ],
    "mail_city": ["tax_city", "mail_city", "mailing_city", "owner_city", "own_city"],
    "mail_state": ["tax_state", "mail_state", "mailing_state", "owner_state", "own_state"],
    "mail_zip": ["tax_zip", "tax_zipcode", "mail_zip", "mailing_zip", "owner_zip", "own_zip"],
    "mail_full": ["tax_mailing_full", "mailing_full", "full_mailing_address", "mailing_address_full"],
    "parcel_key": [
        "parcel_id", "parcelpin", "parcel_number", "parcelnumber", "parcel", "ppn", "pin", "parcel_pin",
    ],
    "parcel_site": [
        "par_addr_all", "par_addr", "parcel_address", "site_address", "address", "full_address",
        "property_address", "situs_address",
    ],
}

# Used only when no candidate matches: any field whose normalized name contains one of these.
CONTAINS_FALLBACK = {
    "address": ["address"],
    "parcel": ["parcel", "ppn"],
    "date": ["date"],
    "status": ["status"],
    "description": ["descr", "note"],
}


def _overrides(source: str) -> dict:
    try:
        data = json.loads(config.FIELD_OVERRIDES.read_text())
    except (OSError, ValueError):
        return {}
    merged = dict(data.get("*", {}))
    merged.update(data.get(source, {}))
    return merged


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or v == ""


def build_index(attrs: dict) -> dict:
    return {norm(k): k for k in attrs}


def pick(attrs: dict, role: str, source: str = "*", index: dict | None = None):
    """Return the value for a role, or None."""
    index = index or build_index(attrs)
    forced = _overrides(source).get(role)
    if forced:
        key = index.get(norm(forced))
        if key is not None and not _empty(attrs[key]):
            return attrs[key]
    for cand in CANDIDATES.get(role, []):
        key = index.get(norm(cand))
        if key is not None and not _empty(attrs[key]):
            return attrs[key]
    for frag in CONTAINS_FALLBACK.get(role, []):
        for nk, orig in index.items():
            if frag in nk and not _empty(attrs[orig]):
                return attrs[orig]
    return None


def detect_field(field_names: list, role: str, source: str = "*") -> str | None:
    """Pick a layer field *name* for a role (used to build WHERE clauses)."""
    index = {norm(f): f for f in field_names}
    forced = _overrides(source).get(role)
    if forced and norm(forced) in index:
        return index[norm(forced)]
    for cand in CANDIDATES.get(role, []):
        if norm(cand) in index:
            return index[norm(cand)]
    return None
