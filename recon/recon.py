import json, sys
sys.path.insert(0, ".")
from arcgis_client import ArcGISClient
import fields
c = ArcGISClient()
url = c.resolve_layer_url("53a4e551102a42c1b4af8a17f863f509", 0)
info = c.layer_info(url)
names = [f["name"] for f in info["fields"]]
print("@@fields@@", json.dumps({"url": url, "names": names, "roles": {r: fields.detect_field(names, r, "demolitions") for r in ("case_number","address","parcel","date","status","type","description")}}))
oid = info.get("objectIdField", "OBJECTID")
for fld in [n for n in names if any(w in n.lower() for w in ("status", "type", "category", "contractor_type", "program", "funding"))][:6]:
    stats = [{"statisticType": "count", "onStatisticField": oid, "outStatisticFieldName": "n"}]
    d = c._request(f"{url}/query", {"where": "1=1", "groupByFieldsForStatistics": fld, "outStatistics": json.dumps(stats), "returnGeometry": "false"}, post=True)
    print("@@values@@", json.dumps({"field": fld, "vals": sorted(((a.get(fld), a.get("n")) for a in (x["attributes"] for x in d.get("features", []))), key=lambda t: -(t[1] or 0))[:25]}, default=str))
rows = c.query(url, "1=1")
print("@@count@@", len(rows))
rows.sort(key=lambda a: -max([v for k, v in a.items() if "date" in k.lower() and isinstance(v, (int, float))] or [0]))
print("@@sample@@", json.dumps(rows[:3], default=str)[:4000])
