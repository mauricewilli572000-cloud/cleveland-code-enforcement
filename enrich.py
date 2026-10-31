"""Owner / mailing-address enrichment from Cuyahoga County parcel data.

The code-enforcement datasets do not carry owner names, so owners come from the
County taxparcel table (public tax-roll data) or the City's Property Insights
table, joined on parcel number first and normalized street address second.
"""
import csv
import logging
from pathlib import Path

import config
import fields
from normalize import address_key, clean_address, norm_parcel, split_address

log = logging.getLogger("enrich")


def parcel_variants(p: str) -> list[str]:
    p = norm_parcel(p)
    out = [p]
    if len(p) == 8 and p.isdigit():
        out.append(f"{p[:3]}-{p[3:5]}-{p[5:]}")
    return out


def _mailing(attrs: dict, source: str, idx: dict) -> str:
    full = fields.pick(attrs, "mail_full", source, idx)
    if full:
        return clean_address(full)
    parts = [fields.pick(attrs, r, source, idx) for r in ("mail_street", "mail_city", "mail_state", "mail_zip")]
    street, city, state, zipc = [str(p).strip() if p else "" for p in parts]
    unit = fields.pick(attrs, "mail_unit", source, idx)
    if unit and street:
        street = f"{street} {str(unit).strip()}"
    tail = " ".join(x for x in (state, zipc) if x)
    return clean_address(", ".join(x for x in (street, city, tail) if x))


class OwnerLookup:
    def __init__(self):
        self.by_parcel: dict[str, dict] = {}
        self.by_addr: dict[str, dict] = {}

    def __len__(self):
        return len(self.by_parcel)

    def add_rows(self, rows: list[dict], source: str = "*"):
        for attrs in rows:
            idx = fields.build_index(attrs)
            owner = fields.pick(attrs, "owner", source, idx)
            if not owner:
                continue
            rec = {
                "owner_name": clean_address(owner),
                "mailing_address": _mailing(attrs, source, idx),
            }
            pk = norm_parcel(fields.pick(attrs, "parcel_key", source, idx))
            if pk and pk not in self.by_parcel:
                self.by_parcel[pk] = rec
            site = fields.pick(attrs, "parcel_site", source, idx)
            ak = address_key(split_address(site)[0]) if site else ""
            if ak and ak not in self.by_addr:
                self.by_addr[ak] = rec

    def add_csv(self, path: Path):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            self.add_rows(list(csv.DictReader(fh)), "csv")

    def lookup(self, parcel: str, address: str) -> dict | None:
        if parcel and norm_parcel(parcel) in self.by_parcel:
            return self.by_parcel[norm_parcel(parcel)]
        ak = address_key(address) if address else ""
        return self.by_addr.get(ak) if ak else None

    # -- live service ----------------------------------------------------------
    MAX_ADDRESS_LOOKUPS = 300   # address fallback is slow; parcel match is the main path

    def load_from_service(self, client, pairs: list[tuple[str, str]]):
        """Query parcel layers only for the parcels we need; fall back to address
        only for properties that still have no owner after the parcel match.

        pairs: (parcel, address) for every open signal.
        """
        parcels = {norm_parcel(p) for p, _ in pairs if p}
        for ps in config.PARCEL_SOURCES:
            try:
                url = client.resolve_layer_url(ps["item_id"], ps["layer"])
                names = [f["name"] for f in client.layer_info(url).get("fields", [])]
            except Exception as exc:  # network / schema problems must not kill the run
                log.warning("parcel source %s unavailable: %s", ps["name"], exc)
                continue
            pk_field = fields.detect_field(names, "parcel_key", ps["name"])
            site_field = fields.detect_field(names, "parcel_site", ps["name"])
            missing = {p for p in parcels if p not in self.by_parcel}
            if pk_field and missing:
                variants = sorted({v for p in missing for v in parcel_variants(p)})
                for i in range(0, len(variants), 150):
                    chunk = variants[i:i + 150]
                    inlist = ",".join("'" + v.replace("'", "''") + "'" for v in chunk)
                    try:
                        self.add_rows(client.query(url, f"{pk_field} IN ({inlist})"), ps["name"])
                    except Exception as exc:
                        log.warning("parcel query failed (%s): %s", ps["name"], exc)
                        break
            unresolved = sorted({
                a for p, a in pairs
                if a and not self.lookup(p, a)          # no owner by parcel OR address yet
            })
            if len(unresolved) > self.MAX_ADDRESS_LOOKUPS:
                log.warning("%d properties still have no owner after parcel match; address-matching only the first %d",
                            len(unresolved), self.MAX_ADDRESS_LOOKUPS)
                unresolved = unresolved[:self.MAX_ADDRESS_LOOKUPS]
            if site_field and unresolved:
                for i in range(0, len(unresolved), 100):
                    chunk = unresolved[i:i + 100]
                    inlist = ",".join("'" + a.replace("'", "''") + "'" for a in chunk)
                    try:
                        self.add_rows(client.query(url, f"UPPER({site_field}) IN ({inlist})"), ps["name"])
                    except Exception as exc:
                        log.warning("address query failed (%s): %s", ps["name"], exc)
                        break
            log.info("owner lookup after %s: %d parcels", ps["name"], len(self.by_parcel))
