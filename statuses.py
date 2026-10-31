"""Show every status value in each Cleveland dataset and how the scraper classifies it.

    python statuses.py
Run from the project folder (needs internet). Paste the output back for review.
"""
import json

import config
import fields
from arcgis_client import ArcGISClient
from normalize import is_closed


def main():
    client = ArcGISClient()
    for key, src in config.SOURCES.items():
        url = client.resolve_layer_url(src["item_id"], src["layer"])
        info = client.layer_info(url)
        names = [f["name"] for f in info["fields"]]
        status_field = fields.detect_field(names, "status", key)
        date_field = fields.detect_field(names, "date", key)
        oid = info.get("objectIdField", "OBJECTID")
        print(f"\n== {key}  | status field: {status_field}  | date field: {date_field}  | all_active: {src['all_active']} ==")
        if not status_field:
            print("  no status field detected. Fields:", ", ".join(names))
            continue
        stats = [{"statisticType": "count", "onStatisticField": oid, "outStatisticFieldName": "n"}]
        data = client._request(
            f"{url}/query",
            {"where": "1=1", "groupByFieldsForStatistics": status_field,
             "outStatistics": json.dumps(stats), "returnGeometry": "false"},
            post=True,
        )
        rows = sorted((f["attributes"] for f in data.get("features", [])), key=lambda a: -(a.get("n") or 0))
        for a in rows[:40]:
            val = a.get(status_field)
            verdict = "CLOSED -> dropped" if is_closed(val, src["all_active"]) else "open   -> kept"
            print(f"  {a.get('n', 0):8}  {verdict:18}  {val!r}")
        if len(rows) > 40:
            print(f"  ... {len(rows) - 40} more values")
    # any "active" flag columns (e.g. ACTIVE_VIOLATION, Active_Condemnation): show their values too
    for key, src in config.SOURCES.items():
        url = client.resolve_layer_url(src["item_id"], src["layer"])
        info = client.layer_info(url)
        oid = info.get("objectIdField", "OBJECTID")
        for f in info["fields"]:
            if "active" in f["name"].lower():
                stats = [{"statisticType": "count", "onStatisticField": oid, "outStatisticFieldName": "n"}]
                data = client._request(f"{url}/query", {"where": "1=1", "groupByFieldsForStatistics": f["name"],
                                       "outStatistics": json.dumps(stats), "returnGeometry": "false"}, post=True)
                vals = sorted((x["attributes"] for x in data.get("features", [])), key=lambda a: -(a.get("n") or 0))
                print(f"\n-- {key}.{f['name']} values --")
                for a in vals[:15]:
                    print(f"  {a.get('n', 0):8}  {a.get(f['name'])!r}")


if __name__ == "__main__":
    main()
