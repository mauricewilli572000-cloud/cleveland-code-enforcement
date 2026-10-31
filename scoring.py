"""Group signals by property, merge owner info, and score the lead (0-100)."""
from datetime import date

import config
from normalize import is_entity, owner_class, property_key


def _days_old(iso: str | None, today: date) -> int | None:
    if not iso:
        return None
    try:
        return (today - date.fromisoformat(iso)).days
    except ValueError:
        return None


def mail_flags(mailing: str) -> tuple[bool, bool]:
    """(out_of_state, out_of_city) from a free-text mailing address."""
    m = (mailing or "").upper()
    if not m:
        return False, False
    out_state = " OH " not in f" {m.replace(',', ' ')} " and not m.rstrip().endswith(" OH")
    out_city = config.CITY not in m
    return out_state, out_city


def build_leads(signals: list[dict], lookup, today: date, foreclosure_keys: set[str] | None = None) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for s in signals:
        groups.setdefault(property_key(s), []).append(s)

    leads = []
    for key, sigs in groups.items():
        sigs.sort(key=lambda s: s.get("date") or "", reverse=True)
        head = next((s for s in sigs if s["address"]), sigs[0])
        parcel = next((s["parcel"] for s in sigs if s["parcel"]), "")
        owner = lookup.lookup(parcel, head["address"]) if lookup else None

        sources = sorted({s["source"] for s in sigs})
        score = max(config.SOURCES[s]["base_score"] for s in sources)
        reasons = [f"{config.SOURCES[s]['label']}" for s in sources]

        if len(sigs) > 1:
            bonus = min(20, 5 * (len(sigs) - 1))
            score += bonus
            reasons.append(f"{len(sigs)} open records (+{bonus})")
        if len(sources) > 1:
            score += 10
            reasons.append("multiple enforcement types (+10)")

        oldest = max((_days_old(s["date"], today) or 0 for s in sigs), default=0)
        if oldest > 180:
            score += 15
            reasons.append("open > 180 days (+15)")
        elif oldest > 90:
            score += 10
            reasons.append("open > 90 days (+10)")

        stage_text = " ".join(s.get("status", "").lower() for s in sigs)
        for label, words, pts in config.STAGE_BONUSES:
            if pts and any(w in stage_text for w in words):
                score += pts
                reasons.append(f"{label} (+{pts})")
                break                                  # only the highest-priority stage counts

        total_amount = sum(s["amount"] for s in sigs if s.get("amount"))
        if total_amount > 0:
            score += 10
            reasons.append(f"fines/fees ${total_amount:,.0f} (+10)")

        mailing = owner["mailing_address"] if owner else ""
        out_state, out_city = mail_flags(mailing)
        if owner:
            if out_state:
                score += 15
                reasons.append("owner mails out of state (+15)")
            elif out_city:
                score += 8
                reasons.append("owner mails outside Cleveland (+8)")
            cls = owner_class(owner["owner_name"])
            if cls == "individual" and config.INDIVIDUAL_OWNER_BONUS:
                score += config.INDIVIDUAL_OWNER_BONUS
                reasons.append(f"individual owner (+{config.INDIVIDUAL_OWNER_BONUS})")
            elif cls == "institutional" and config.INSTITUTIONAL_OWNER_PENALTY:
                score += config.INSTITUTIONAL_OWNER_PENALTY
                reasons.append(f"institutional/REO owner ({config.INSTITUTIONAL_OWNER_PENALTY})")
        else:
            reasons.append("owner not found")

        fc = bool(foreclosure_keys and (f"P:{parcel}" in foreclosure_keys or key in foreclosure_keys))
        if fc:
            score += 25
            reasons.append("also in foreclosure (+25)")

        leads.append({
            "lead_id": key,
            "property_address": head["address"],
            "city": "CLEVELAND",
            "state": config.STATE,
            "zip": next((s["zip"] for s in sigs if s["zip"]), ""),
            "parcel": parcel,
            "owner_name": owner["owner_name"] if owner else "",
            "owner_is_entity": bool(owner and is_entity(owner["owner_name"])),
            "owner_class": owner_class(owner["owner_name"]) if owner else "",
            "mailing_address": mailing,
            "out_of_state_owner": bool(owner and out_state),
            "lead_types": [config.SOURCES[s]["label"] for s in sources],
            "open_records": len(sigs),
            "case_numbers": sorted({s["case_number"] for s in sigs if s["case_number"]}),
            "cases_by_source": {
                src: sorted({s["case_number"] for s in sigs if s["source"] == src and s["case_number"]})
                for src in sources
            },
            "violation_types": sorted({s["type"] for s in sigs if s["type"]})[:6],
            "description": next((s["description"] for s in sigs if s["description"]), ""),
            "oldest_open_days": oldest,
            "latest_date": sigs[0].get("date") or "",
            "total_fines": round(total_amount, 2),
            "ward": next((s["ward"] for s in sigs if s["ward"]), ""),
            "neighborhood": next((s["neighborhood"] for s in sigs if s["neighborhood"]), ""),
            "in_foreclosure": fc,
            "score": max(0, min(100, score)),
            "score_reasons": reasons,
        })
    leads.sort(key=lambda l: (-l["score"], l["property_address"]))
    return leads
