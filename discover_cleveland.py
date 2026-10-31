"""Run this ONCE on your own machine (it needs internet access to arcgis.com).

    python discover_cleveland.py

For every dataset it resolves the real FeatureServer URL, prints the field list,
record count and a few sample rows, and writes everything to data/debug/. It also
shows which field the scraper would pick for each role so mismatches are obvious.
Zip data/debug/ and send it back (or fix data/field_overrides.json yourself).
"""
import json
import re
import sys

import config
import fields
from arcgis_client import ArcGISClient

ROLES_SOURCE = ["case_number", "address", "parcel", "zip", "date", "status", "type", "description", "amount"]
ROLES_PARCEL = ["parcel_key", "parcel_site", "owner", "mail_street", "mail_city", "mail_state", "mail_zip", "mail_full"]


def main() -> int:
    config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    client = ArcGISClient()
    urls, problems = {}, 0
    targets = [(k, s["item_id"], s["layer"], ROLES_SOURCE) for k, s in config.SOURCES.items()]
    targets += [(p["name"], p["item_id"], p["layer"], ROLES_PARCEL) for p in config.PARCEL_SOURCES]

    for name, item_id, layer, roles in targets:
        print(f"\n=== {name}  ({item_id}_{layer}) ===")
        try:
            meta = client._request(config.ITEM_SHARING_URL.format(item_id=item_id), {})
            print("item title:", meta.get("title"), "| type:", meta.get("type"), "| url:", meta.get("url"))
            url = (meta.get("url") or "").rstrip("/")
            if not url:
                raise RuntimeError("item has no service url")
            if not re.search(r"/(FeatureServer|MapServer)/\d+$", url):
                url = f"{url}/{layer}"
            info = client.layer_info(url)
            names = [f["name"] for f in info.get("fields", [])]
            count = client._request(f"{url}/query", {"where": "1=1", "returnCountOnly": "true"}).get("count")
            data = client._request(f"{url}/query", {"where": "1=1", "outFields": "*",
                                                     "returnGeometry": "false", "resultRecordCount": 5})
            sample = [f["attributes"] for f in data.get("features", [])]
            urls[f"{item_id}_{layer}"] = url
            print("layer:", info.get("name"), "| records:", count, "| maxRecordCount:", info.get("maxRecordCount"))
            print("fields:", ", ".join(f"{f['name']}({f['type'].replace('esriFieldType', '')})"
                                       for f in info.get("fields", [])))
            print("scraper would use:")
            for role in roles:
                print(f"   {role:12s} -> {fields.detect_field(names, role, name)}")
            (config.DEBUG_DIR / f"{name}.json").write_text(json.dumps(
                {"url": url, "item": meta.get("title"), "count": count, "layer": info, "sample": sample}, indent=1))
        except Exception as exc:  # keep going; report all problems at the end
            problems += 1
            print("  !! FAILED:", exc)

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SERVICE_URL_CACHE.write_text(json.dumps(urls, indent=1))
    print(f"\nWrote data/debug/*.json and data/service_urls.json ({len(urls)} services resolved, {problems} failed).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
