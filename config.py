"""Configuration for the Cleveland (Cuyahoga County, OH) code enforcement lead scraper.

Data comes from the City of Cleveland Open Data portal (an ArcGIS Hub site,
data.clevelandohio.gov). Every dataset is a hosted ArcGIS FeatureLayer, so the
scraper talks to the REST query API directly instead of scraping HTML.

The item IDs below come from the Hub dataset IDs ("<itemid>_<layerindex>").
Run discover_cleveland.py locally once to confirm service URLs and field names.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DEBUG_DIR = DATA_DIR / "debug"
DOCS_DIR = ROOT / "docs"
FIXTURE_DIR = ROOT / "tests" / "fixtures"

RECORDS_JSON = DATA_DIR / "records.json"
GHL_CSV = DATA_DIR / "ghl_cleveland_code_enforcement.csv"
DASHBOARD_JSON = DOCS_DIR / "cleveland_code_enforcement.json"
SERVICE_URL_CACHE = DATA_DIR / "service_urls.json"      # written by discover_cleveland.py
FIELD_OVERRIDES = DATA_DIR / "field_overrides.json"     # optional manual role -> field name map

ITEM_SHARING_URL = "https://www.arcgis.com/sharing/rest/content/items/{item_id}"

# ---- Lead sources (Cleveland Building & Housing datasets) -------------------
# all_active: the dataset only contains currently active records, so a blank
# status is treated as open.
SOURCES = {
    "violation_notices": {
        "label": "Building Complaint Violation Notice",
        "item_id": "3fbae9a4d11f4af0be19912109c0cbff",
        "layer": 0,
        "all_active": False,
        "base_score": 20,
    },
    "condemnations": {
        "label": "Active Condemnation",
        "item_id": "bd47abc2adc242f789313af4191610ae",
        "layer": 0,
        "all_active": True,
        "base_score": 40,
    },
    "civil_tickets": {
        "label": "Civil Ticket (Residents First)",
        "item_id": "aec7cde2885f4b56ad23a5166e4380c6",
        "layer": 0,
        "all_active": False,
        "base_score": 15,
    },
    "board_ups": {
        "label": "Board-Up (open/vacant/vandalized)",
        "item_id": "ba57c55fc6b54f34994420b67e831cc8",
        "layer": 0,
        "all_active": False,
        "base_score": 30,
    },
}

# ---- Owner enrichment sources -----------------------------------------------
# Cuyahoga County Taxparcel table (daily, public) and the City's Property
# Insights dataset (weekly; a superset of the county table with City fields).
PARCEL_SOURCES = [
    {"name": "county_taxparcel", "item_id": "ffaaa1651d5540419469375d680f3245", "layer": 1},
    {"name": "property_insights", "item_id": "44bd466d14ce4d90bc0671c524bf16c2", "layer": 0},
]

# ---- Behaviour ----------------------------------------------------------------
FIRST_RUN_LOOKBACK_DAYS = 60       # same as the Columbus scraper
INCREMENTAL_LOOKBACK_DAYS = 21
PRUNE_AFTER_DAYS = 75              # drop leads not seen in this long
PAGE_SIZE = 1000
REQUEST_TIMEOUT = 60
HTTP_RETRIES = 4
USER_AGENT = "cleveland-code-enforcement-leads/1.0 (public records research)"

CITY = "CLEVELAND"
STATE = "OH"

CLOSED_STATUS_WORDS = (
    "closed", "complete", "completed", "resolved", "withdrawn", "void", "voided",
    "dismissed", "paid", "cancel", "compliance", "abated", "satisfied", "rescinded",
    "demolished", "no violation", "unfounded",
    "payment received", "appeal granted",        # civil tickets: paid / overturned
)

# ---- Scoring knobs (set any to 0 to switch it off) ----------------------------
# Escalated enforcement stages seen in the real status values (violation notices):
#   court: 'Court Stat', 'Filed w/Clerk of Courts', 'Court Date Set', 'Court Judgment',
#          'Summons Approved', 'Prosecution Pkt Created', 'Paralegal to Law', 'Packet to Paralegal...'
#   demolition: 'Demo Packet to Legal', 'Demo File to Chief', 'Demolition Approved', 'Approved for Demo'
STAGE_BONUSES = [
    ("demolition track", ("demo",), 15),
    ("court / legal stage", ("court", "summons", "prosecution", "paralegal", "legal", "judgment"), 10),
]
INDIVIDUAL_OWNER_BONUS = 10        # a person, not a company: easier to reach and negotiate with
INSTITUTIONAL_OWNER_PENALTY = -15  # REO / bank / securitization trusts / large rental funds
