"""Cleveland code enforcement lead pipeline.

    python run_pipeline.py                      # live run (first run: 60-day look-back)
    python run_pipeline.py --days 90            # custom look-back
    python run_pipeline.py --sources condemnations,board_ups
    python run_pipeline.py --parcel-csv Parcel.csv     # owner data from a local file
    python run_pipeline.py --offline tests/fixtures    # no network (fixtures)
"""
import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import config
import enrich
import fields
import housing_court
import normalize
import scoring
import store
from arcgis_client import ArcGISClient

log = logging.getLogger("pipeline")


def fetch_source_live(client: ArcGISClient, key: str, since: date, known_cases: set[str]) -> list[dict]:
    src = config.SOURCES[key]
    since = source_since(key, since)
    url = client.resolve_layer_url(src["item_id"], src["layer"])
    names = [f["name"] for f in client.layer_info(url).get("fields", [])]
    date_field = fields.detect_field(names, "date", key)
    where = "1=1"
    if date_field and not src["all_active"]:
        where = f"{date_field} >= timestamp '{since.isoformat()} 00:00:00'"
    try:
        rows = client.query(url, where)
    except Exception as exc:
        if where == "1=1":
            raise
        log.warning("%s: date filter rejected (%s); pulling everything and filtering locally", key, exc)
        rows = client.query(url, "1=1")
    # Re-check cases we already store (opened before the window) so closures are noticed.
    case_field = fields.detect_field(names, "case_number", key)
    if known_cases and case_field and not src["all_active"] and src.get("recheck", True):
        cases = sorted(known_cases)
        for i in range(0, len(cases), 100):
            inlist = ",".join("'" + c.replace("'", "''") + "'" for c in cases[i:i + 100])
            try:
                rows.extend(client.query(url, f"{case_field} IN ({inlist})"))
            except Exception as exc:
                log.warning("%s: re-check of stored cases failed: %s", key, exc)
                break
    log.info("%s: %d raw rows (%s; %d stored cases re-checked)", key, len(rows), where, len(known_cases))
    return rows


def source_since(key: str, since: date) -> date:
    """A source may look back further than the run's window (e.g. a year of demolition permits)."""
    days = config.SOURCES[key].get("lookback_days")
    return min(since, date.today() - timedelta(days=days)) if days else since


def fetch_source_offline(fixture_dir: Path, key: str) -> list[dict]:
    return json.loads((fixture_dir / f"{key}.json").read_text())


def collect_signals(raw_by_source: dict, since: date, include_closed: bool, include_units: bool,
                    known: dict | None = None):
    known = known or {}
    open_sigs, closed_keys_all, stats, seen = [], set(), {}, set()
    for key, rows in raw_by_source.items():
        n = dict(raw=len(rows), no_addr=0, closed=0, units=0, old=0, dup=0, kept=0)
        for attrs in rows:
            sig = normalize.to_signal(key, attrs)
            if sig is None:
                n["no_addr"] += 1
                continue
            ident = (key, sig["case_number"] or sig["address"], sig["address"], sig["date"])
            if ident in seen:
                n["dup"] += 1
                continue
            seen.add(ident)
            # closure check comes BEFORE the date window so old cases that closed are noticed
            if sig["closed"] and not include_closed:
                n["closed"] += 1
                closed_keys_all.add(normalize.property_key(sig))
                continue
            src_cfg = config.SOURCES[key]
            if src_cfg.get("lookback_days"):     # fixed window: kept for lookback_days, then ages out
                in_window = bool(sig["date"]) and sig["date"] >= source_since(key, since).isoformat()
            else:
                in_window = (not sig["date"] or src_cfg["all_active"]
                             or sig["date"] >= since.isoformat()
                             or sig["case_number"] in known.get(key, ()))
            if not in_window:
                n["old"] += 1
                continue
            if not include_units and normalize.has_unit(sig["address"]):
                n["units"] += 1
                continue
            open_sigs.append(sig)
            n["kept"] += 1
        stats[key] = n
    open_keys = {normalize.property_key(s) for s in open_sigs}
    return open_sigs, closed_keys_all - open_keys, stats


def _add_foreclosure_row(keys: set[str], row: dict):
    low = {str(k).lower().strip(): v for k, v in row.items() if k}
    p = normalize.norm_parcel(low.get("parcel") or low.get("parcel_number") or low.get("ppn"))
    a = (low.get("property_address") or low.get("prop_address") or low.get("address") or "")
    if p:
        keys.add(f"P:{p}")
    if a:
        keys.add(f"A:{normalize.address_key(normalize.split_address(a)[0])}")


def load_foreclosure_keys(sources) -> set[str]:
    """Keys (P:<parcel>, A:<address key>) of foreclosure properties.

    Each source is a local .csv/.json path or an http(s) URL of a .json feed such as the
    Cuyahoga foreclosure scraper's dashboard/cuyahoga.json ({"leads": [...]}). A source that
    can't be read is logged and skipped; it never fails the run.
    """
    import csv
    import requests
    keys: set[str] = set()
    if not sources:
        return keys
    if isinstance(sources, (str, Path)):
        sources = [sources]
    for src in sources:
        before = len(keys)
        try:
            s = str(src)
            if s.startswith(("http://", "https://")):
                r = requests.get(s, timeout=config.REQUEST_TIMEOUT, headers={"User-Agent": config.USER_AGENT})
                r.raise_for_status()
                data = r.json()
            elif s.lower().endswith(".json"):
                data = json.loads(Path(s).read_text(encoding="utf-8"))
            else:
                with open(s, newline="", encoding="utf-8-sig") as fh:
                    data = list(csv.DictReader(fh))
            rows = data if isinstance(data, list) else (data.get("leads") or data.get("records") or [])
            for row in rows:
                if isinstance(row, dict):
                    _add_foreclosure_row(keys, row)
            log.info("foreclosure source %s: %d rows, %d new keys", s[-60:], len(rows), len(keys) - before)
        except Exception as exc:
            log.warning("foreclosure source %s could not be read (%s); skipping it", src, exc)
    return keys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, help="look-back window in days")
    ap.add_argument("--sources", help="comma list of: " + ", ".join(config.SOURCES))
    ap.add_argument("--include-closed", action="store_true")
    ap.add_argument("--include-units", action="store_true", help="keep apartments / unit addresses")
    ap.add_argument("--parcel-csv", type=Path, help="local parcel/owner CSV to use for owners")
    ap.add_argument("--foreclosure-csv", "--foreclosure-source", dest="foreclosure_sources", action="append",
                    metavar="PATH_OR_URL",
                    help="foreclosure leads to cross-match (+25 score): a .csv/.json path or a URL of a .json feed; "
                         "repeat the flag for several sources")
    ap.add_argument("--include-public", action="store_true",
                    help="keep properties owned by the City, County, land bank or housing authority")
    ap.add_argument("--no-enrich", action="store_true", help="skip owner lookup")
    ap.add_argument("--min-score", type=int, default=0, help="minimum score for the GHL CSV")
    ap.add_argument("--offline", type=Path, help="read <source>.json fixtures instead of the network")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    today = date.fromisoformat(args.today) if args.today else date.today()
    state = store.load()
    first_run = not state.get("leads")
    days = args.days or (config.FIRST_RUN_LOOKBACK_DAYS if first_run else config.INCREMENTAL_LOOKBACK_DAYS)
    since = today - timedelta(days=days)
    keys = [k.strip() for k in args.sources.split(",")] if args.sources else list(config.SOURCES)
    bad = [k for k in keys if k not in config.SOURCES]
    if bad:
        ap.error(f"unknown source(s): {bad}")
    log.info("look-back %d days (since %s); sources: %s", days, since, ", ".join(keys))

    known: dict[str, set] = {}
    for lead in state.get("leads", {}).values():
        for src, cases in lead.get("cases_by_source", {}).items():
            known.setdefault(src, set()).update(cases)

    client = None if args.offline else ArcGISClient()
    raw, failures = {}, []
    court_keys = [k for k in keys if config.SOURCES[k].get("kind") == "housing_court_xlsx"]
    keys = [k for k in keys if k not in court_keys]
    for key in keys:
        try:
            raw[key] = (fetch_source_offline(args.offline, key) if args.offline
                        else fetch_source_live(client, key, since, known.get(key, set())))
        except Exception as exc:
            log.error("source %s failed: %s", key, exc)
            failures.append(key)
    if not raw and not court_keys:
        log.error("no source could be fetched; run discover_cleveland.py and send back data/debug/")
        return 2

    signals, closed_keys, stats = collect_signals(raw, since, args.include_closed, args.include_units, known)
    for k, n in stats.items():
        log.info("%s: %s", k, n)

    # Housing Court criminal cases (Excel report, not ArcGIS). A failure never stops the run.
    for key in court_keys:
        try:
            court_rows = ([] if args.offline and not (args.offline / f"{key}.json").exists()
                          else fetch_source_offline(args.offline, key) if args.offline
                          else housing_court.fetch_rows())
            court = housing_court.to_signals(court_rows)
            open_court = [s for s in court if not s["closed"] or args.include_closed]
            signals.extend(open_court)
            open_keys = {normalize.property_key(s) for s in signals}
            closed_keys |= {normalize.property_key(s) for s in court if s["closed"]} - open_keys
            log.info("%s: %d cases, %d open", key, len(court), len(open_court))
        except Exception as exc:
            log.error("source %s failed: %s", key, exc)
            failures.append(key)

    lookup = enrich.OwnerLookup()
    if not args.no_enrich:
        if args.parcel_csv:
            lookup.add_csv(args.parcel_csv)
        if args.offline:
            pf = args.offline / "parcels.json"
            if pf.exists():
                lookup.add_rows(json.loads(pf.read_text()))
        elif client:
            if court_keys:
                try:
                    housing_court.resolve_parcels(client, lookup, signals, config.PARCEL_SOURCES[0])
                except Exception as exc:  # noqa: BLE001
                    log.warning("court-case parcel lookup skipped: %s", exc)
            lookup.load_from_service(client, [(s["parcel"], s["address"]) for s in signals],
                                     priority={s["address"] for s in signals if s["source"] == "housing_criminal"})
        log.info("owner records loaded: %d", len(lookup))

    fc_keys = load_foreclosure_keys(args.foreclosure_sources)
    leads = scoring.build_leads(signals, lookup, today, fc_keys)
    if fc_keys:
        log.info("foreclosure cross-match: %d leads also in foreclosure", sum(1 for l in leads if l["in_foreclosure"]))
    if not args.include_public:
        gov = {l["lead_id"] for l in leads if l["owner_class"] == "government"}
        if gov:
            log.info("dropping %d city/county/land-bank-owned properties (--include-public keeps them)", len(gov))
        leads = [l for l in leads if l["lead_id"] not in gov]
        closed_keys = closed_keys | gov            # also removes them if stored earlier
    # The court report lists every case filed this year, so a stored lead built only from
    # court cases that isn't in this run's results is stale (e.g. re-keyed from address to parcel).
    if court_keys and "housing_criminal" not in failures:
        current_ids = {l["lead_id"] for l in leads}
        closed_keys |= {k for k, v in state.get("leads", {}).items()
                        if set(v.get("cases_by_source", {})) <= set(court_keys) and k not in current_ids}
    removed = len(closed_keys & set(state.get("leads", {})))
    state = store.merge(state, leads, closed_keys, today)
    store.save(state)
    store.export_ghl(state, args.min_score)
    store.export_dashboard(state)
    store.export_feed(state)

    with_owner = sum(1 for l in state["leads"].values() if l["owner_name"])
    log.info("done: %d leads in store (%d with owner), %d new/updated this run, %d closed removed",
             len(state["leads"]), with_owner, len(leads), removed)
    if failures:
        log.warning("sources that failed this run: %s", failures)
    return 0


if __name__ == "__main__":
    sys.exit(main())
