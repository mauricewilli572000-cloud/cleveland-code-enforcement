# Cleveland (Cuyahoga County) code enforcement lead scraper

Open code-enforcement signals from the City of Cleveland Open Data portal
(`data.clevelandohio.gov`, an ArcGIS Hub site), joined to owner/mailing data from
Cuyahoga County parcel records, scored, and exported for GoHighLevel plus a dashboard.

## Is that the right site?

Yes. The portal's "B&H" (Building & Housing) tag holds the City's code enforcement
data, sourced from the Accela permitting system. Unlike Columbus's Accela portal,
nothing here needs browser scraping: every dataset is an ArcGIS FeatureLayer with a
public REST API.

| Source key | Dataset | Why it matters |
|---|---|---|
| `condemnations` | Active Condemnations | Strongest signal; dataset holds only active records |
| `board_ups` | Board-up permits (open/vacant/vandalized) | Vacant, vandalized structures |
| `demolitions` | Demolition permits, last 12 months (City, Land Bank, private) | Building pending demolition, or now a vacant lot |
| `violation_notices` | Building Complaint Violation Notices (2015+) | Complaints escalated to a notice |
| `civil_tickets` | Civil Tickets, Residents First (2025+) | Fines accruing on the owner |

Owners come from the County Taxparcel table (fallback: City Property Insights),
because the enforcement datasets carry no owner names.

## Important: field names are unconfirmed

This was built without access to the live services, so field names are guesses. The
scraper tries many candidate names per role (`fields.py`), but run discovery once:

```
pip install -r requirements.txt
python discover_cleveland.py
```

It prints each dataset's real fields and which field the scraper would pick per role.
If something is wrong or missing, add `data/field_overrides.json`:

```json
{"violation_notices": {"address": "DW_Address", "date": "DW_Opened_Date"},
 "*": {"parcel": "DW_Parcel"}}
```

(`"*"` applies to every source; a source key overrides it.) Zip `data/debug/` and send
it back if you want me to tighten the mapping.

## Run

```
python run_pipeline.py                     # first run: 60-day look-back, later runs 21 days
python run_pipeline.py --days 90
python run_pipeline.py --sources condemnations,board_ups
python run_pipeline.py --parcel-csv Parcel.csv       # owners from a local file
python run_pipeline.py --foreclosure-csv foreclosures.csv   # +25 score if also in foreclosure
python run_pipeline.py --include-closed --include-units
python -m pytest -q tests
```

Foreclosure CSV columns: `parcel` and/or `property_address` (header names are case-insensitive).

## Behaviour (mirrors the Columbus scraper)

- Open/active cases only; closed cases are dropped, and a stored lead disappears when its case closes.
- Apartments / addresses with unit designators are excluded (`--include-units` overrides).
- Description emails and phone numbers are redacted.
- Records at the same parcel (or normalized address) merge into **one lead** with all signals.
- Leads accumulate in `data/records.json`; each keeps its original `added_date`; leads not seen for 75 days are pruned.
- Incremental runs also re-query the case numbers already stored, so cases opened before the window are still tracked and closures are noticed.

## Score (0-100)

Highest base among signals (condemnation 40, board-up 30, demolition permit 25, violation notice 20, civil ticket 15), plus: 5 per extra open record (max 20), +10 multiple enforcement types, +10/+15 open over 90/180 days, +10 fines owed, +15 owner mails out of state (+8 outside Cleveland), +25 also in foreclosure. Hover a dashboard row for the breakdown.

## Outputs

- `data/ghl_cleveland_code_enforcement.csv`: manual GoHighLevel upload (UTF-8 BOM, entity names go to Company Name, phone/email blank for skip tracing)
- `docs/cleveland_code_enforcement.json` + `docs/index.html`: dashboard (serve `docs/` with GitHub Pages)
- `data/records.json`: the accumulating store

## Automation

`.github/workflows/cleveland_code_enforcement.yml` runs nightly; Sundays (after the City's weekly refresh) it does a full 60-day re-check. It runs the tests first and commits the outputs back to the repo.

## Known limits

- Owner match is by parcel number, then exact address; unmatched leads show "owner not found". Phones/emails still need skip tracing.
- Address-only matching against the live service is exact-string; a local `--parcel-csv` uses fuzzy matching (number + street name).
- The Cleveland datasets update weekly, so running more than daily adds nothing.
