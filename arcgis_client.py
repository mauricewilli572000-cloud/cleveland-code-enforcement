"""Minimal ArcGIS REST client for hosted FeatureLayers (no geometry, paged)."""
import json
import logging
import re
import time
from datetime import datetime, timezone

import requests

import config

log = logging.getLogger("arcgis")


class ArcGISError(RuntimeError):
    pass


class ArcGISClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = config.USER_AGENT

    # -- low level -----------------------------------------------------------
    def _request(self, url: str, params: dict, post: bool = False) -> dict:
        params = {**params, "f": "json"}
        last = None
        for attempt in range(config.HTTP_RETRIES):
            try:
                if post:
                    r = self.s.post(url, data=params, timeout=config.REQUEST_TIMEOUT)
                else:
                    r = self.s.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
                r.raise_for_status()
                data = r.json()
                if isinstance(data, dict) and "error" in data:
                    raise ArcGISError(json.dumps(data["error"]))
                return data
            except ArcGISError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last = exc
                time.sleep(2 ** attempt)
        raise ArcGISError(f"request failed after retries: {url}: {last}")

    # -- service resolution --------------------------------------------------
    def resolve_layer_url(self, item_id: str, layer: int) -> str:
        """Hub dataset '<item>_<layer>' -> .../FeatureServer/<layer> (cached on disk)."""
        cache = {}
        try:
            cache = json.loads(config.SERVICE_URL_CACHE.read_text())
        except (OSError, ValueError):
            pass
        key = f"{item_id}_{layer}"
        if key in cache:
            return cache[key]
        meta = self._request(config.ITEM_SHARING_URL.format(item_id=item_id), {})
        url = (meta.get("url") or "").rstrip("/")
        if not url:
            raise ArcGISError(f"item {item_id} has no service url (is it a map/hosted view?)")
        if not re.search(r"/(FeatureServer|MapServer)/\d+$", url):
            url = f"{url}/{layer}"
        return url

    def layer_info(self, layer_url: str) -> dict:
        return self._request(layer_url, {})

    # -- querying ------------------------------------------------------------
    def query(self, layer_url: str, where: str = "1=1", out_fields: str = "*", **extra) -> list[dict]:
        """All attribute dicts matching `where`, paging as the service allows."""
        info = self.layer_info(layer_url)
        max_rec = int(info.get("maxRecordCount") or config.PAGE_SIZE)
        page = min(config.PAGE_SIZE, max_rec)
        base = {"where": where, "outFields": out_fields, "returnGeometry": "false", **extra}
        use_post = len(where) > 1500
        rows: list[dict] = []
        offset = 0
        order = {"orderByFields": info["objectIdField"]} if info.get("objectIdField") else {}
        try:
            while True:
                data = self._request(
                    f"{layer_url}/query",
                    {**base, **order, "resultOffset": offset, "resultRecordCount": page},
                    post=use_post,
                )
                feats = data.get("features", [])
                rows.extend(f.get("attributes", {}) for f in feats)
                if not data.get("exceededTransferLimit") and len(feats) < page:
                    break
                if not feats:
                    break
                offset += len(feats)
            return rows
        except ArcGISError as exc:
            if "paginat" not in str(exc).lower() and "resultOffset" not in str(exc):
                raise
            log.warning("pagination unsupported on %s; falling back to ObjectID batches", layer_url)
        ids = self._request(f"{layer_url}/query", {"where": where, "returnIdsOnly": "true"}, post=use_post)
        oid_name = ids.get("objectIdFieldName", "OBJECTID")
        oids = ids.get("objectIds") or []
        rows = []
        for i in range(0, len(oids), page):
            chunk = oids[i:i + page]
            data = self._request(
                f"{layer_url}/query",
                {"objectIds": ",".join(map(str, chunk)), "outFields": out_fields, "returnGeometry": "false"},
                post=True,
            )
            rows.extend(f.get("attributes", {}) for f in data.get("features", []))
        log.debug("fetched %d rows by %s", len(rows), oid_name)
        return rows


def epoch_ms_to_iso(value) -> str | None:
    """ArcGIS dates are epoch milliseconds (or already strings)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if s.isdigit() and len(s) >= 11:
        return epoch_ms_to_iso(int(s))
    return None
