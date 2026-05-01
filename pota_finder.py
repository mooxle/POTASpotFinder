"""
pota_finder.py
==============
Finds the best POTA activation spots within a GeoJSON park boundary.
Two modes — both return the same JSON format.

USAGE (CLI):
  python3 pota_finder.py elevation DE-0042.geojson
  python3 pota_finder.py elevation DE-0042.geojson -t 10 -b 20 -l 5
  python3 pota_finder.py score     DE-0042.geojson
  python3 pota_finder.py score     DE-0042.geojson --top 15 --grid 150 --html
  python3 pota_finder.py elevation --help
  python3 pota_finder.py score     --help

USAGE (Python API):
  from pota_finder import find_by_elevation, find_by_score

  result = find_by_elevation("DE-0042.geojson", tables=10, benches=20)
  result = find_by_score("DE-0042.geojson", top=15, grid=150)

  for spot in result["spots"]:
      print(spot["rank"], spot["elevation_m"], spot["score"], spot["gmaps_url"])

OUTPUT FORMAT (both modes):
  {
    "mode":  "elevation" | "score",
    "park":  { ...GeoJSON properties... },
    "spots": [
      {
        "rank":         1,
        "lat":          50.517,
        "lon":          9.238,
        "elevation_m":  783,
        "score":        null | 81.0,       # score mode only
        "breakdown":    {},                # score mode only
        "reason":       "783m · picnic table · quiet (520m) · parking 380m",
        "amenities":    ["picnic_table", "bench"],
        "osm_url":      "https://www.openstreetmap.org/node/123",
        "gmaps_url":    "https://www.google.com/maps?q=50.517,9.238"
      }
    ]
  }

Requires: pip install requests
"""

import argparse
import hashlib
import html as html_lib
import json
import math
import os
import time
import urllib.parse
import requests


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED — GEOMETRY
# ═══════════════════════════════════════════════════════════════════════════════

def point_in_polygon(lat, lon, polygon):
    """Ray-casting algorithm: returns True if (lat, lon) is inside the polygon."""
    x, y = lon, lat
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in metres between two WGS84 coordinates."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def offset_point(lat, lon, bearing_deg, dist_m):
    """Offsets a point by dist_m metres in the given bearing direction."""
    R = 6_371_000
    d = dist_m / R
    b = math.radians(bearing_deg)
    phi1, lam1 = math.radians(lat), math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(d) +
                     math.cos(phi1) * math.sin(d) * math.cos(b))
    lam2 = lam1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(phi1),
                              math.cos(d) - math.sin(phi1) * math.sin(phi2))
    return math.degrees(phi2), math.degrees(lam2)


def load_geojson(path):
    """Loads GeoJSON file, returns (polygon, park_props, bbox)."""
    with open(path, "r", encoding="utf-8") as f:
        feature = json.load(f)
    if feature.get("type") == "FeatureCollection":
        feature = feature["features"][0]
    geom = feature["geometry"]
    polygon = geom["coordinates"][0][0] if geom["type"] == "MultiPolygon" \
              else geom["coordinates"][0]
    props = feature.get("properties", {})
    lons  = [c[0] for c in polygon]
    lats  = [c[1] for c in polygon]
    bbox  = (min(lats), min(lons), max(lats), max(lons))
    return polygon, props, bbox


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED — OVERPASS
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# These constants control API behaviour. Override them before calling the
# Python API functions if you need different limits for your use case.
# ═══════════════════════════════════════════════════════════════════════════════

# Overpass mirror servers — tried in order until one succeeds
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    # "https://maps.mail.ru/osm/tools/overpass/api/interpreter",  # disabled by default
]

# Maximum requests per second for each API (set lower to be a better citizen)
RATE_LIMIT_OVERPASS   = 0.5   # req/s  — Overpass fair-use recommendation
RATE_LIMIT_OPENTOPO   = 1.0   # req/s  — open-topo-data.org guidelines
RATE_LIMIT_OPENELEVATION = 0.5  # req/s — conservative for open-elevation.com

# Elevation cache: stores results on disk so repeated runs skip API calls entirely
ELEVATION_CACHE_ENABLED = True
ELEVATION_CACHE_FILE    = ".cache_elevation.json"

# Open-Topo-Data daily limit (free tier: 1000 req/day).
# The tool tracks batch calls and warns when you approach the limit.
# Each batch = 1 request. Set to None to disable the warning.
OPENTOPO_DAILY_LIMIT    = 1000
OPENTOPO_DAILY_WARN_AT  = 900   # warn when this many requests used today

HEADERS = {
    "Accept":       "*/*",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent":   "POTA-finder/3.0 (github.com/mooxle/pota-finder; personal/low-volume use)",
}

# ODbL requires attribution wherever OSM data is used or displayed
OSM_ATTRIBUTION = {
    "osm":       "© OpenStreetMap contributors, ODbL 1.0 — https://www.openstreetmap.org/copyright",
    "elevation": "SRTM elevation data, public domain — NASA/USGS",
}


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════════

_rate_last: dict = {}

def _rate_limit(key: str, per_second: float) -> None:
    """
    Blocks until the minimum interval for `key` has elapsed.
    Call before every outbound API request.

    Args:
        key:        Identifier for the API endpoint (e.g. "overpass", "opentopo")
        per_second: Maximum allowed requests per second
    """
    now     = time.monotonic()
    min_gap = 1.0 / per_second
    last    = _rate_last.get(key, 0.0)
    gap     = now - last
    if gap < min_gap:
        time.sleep(min_gap - gap)
    _rate_last[key] = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════════
# ELEVATION CACHE
# Persists elevation results to disk so subsequent runs need zero API calls
# for already-queried coordinates.
# ═══════════════════════════════════════════════════════════════════════════════

_elev_cache: dict = {}
_elev_cache_dirty = False


def _load_elevation_cache() -> None:
    global _elev_cache
    if not ELEVATION_CACHE_ENABLED:
        return
    if os.path.exists(ELEVATION_CACHE_FILE):
        try:
            with open(ELEVATION_CACHE_FILE, "r") as f:
                _elev_cache = json.load(f)
            print(f"  Elevation cache loaded: {len(_elev_cache)} entries ({ELEVATION_CACHE_FILE})")
        except Exception:
            _elev_cache = {}


def _save_elevation_cache() -> None:
    if not ELEVATION_CACHE_ENABLED or not _elev_cache_dirty:
        return
    try:
        with open(ELEVATION_CACHE_FILE, "w") as f:
            json.dump(_elev_cache, f)
    except Exception as e:
        print(f"  ⚠  Could not save elevation cache: {e}")


def _elev_cache_key(lat: float, lon: float) -> str:
    """Rounds to ~11m precision to maximise cache hits."""
    return f"{lat:.4f},{lon:.4f}"


def _cached_elevations(points: list) -> tuple[list, list]:
    """
    Splits points into cache hits and misses.
    Returns (elevations_list, miss_indices) where elevations_list[i] is None for misses.
    """
    elevations = [None] * len(points)
    misses     = []
    for i, p in enumerate(points):
        key = _elev_cache_key(p["lat"], p["lon"])
        if key in _elev_cache:
            elevations[i] = _elev_cache[key]
        else:
            misses.append(i)
    return elevations, misses


def _store_elevations(points: list, indices: list, values: list) -> None:
    global _elev_cache_dirty
    for idx, val in zip(indices, values):
        if val is not None:
            key = _elev_cache_key(points[idx]["lat"], points[idx]["lon"])
            _elev_cache[key] = val
            _elev_cache_dirty = True


# ── Open-Topo-Data daily usage tracking ──────────────────────────────────────

_DAILY_COUNTER_FILE = ".cache_opentopo_daily.json"


def _opentopo_usage_today() -> int:
    """Returns the number of Open-Topo-Data batch requests made today."""
    try:
        with open(_DAILY_COUNTER_FILE) as f:
            data = json.load(f)
        today = time.strftime("%Y-%m-%d")
        return data.get(today, 0)
    except Exception:
        return 0


def _opentopo_increment() -> int:
    """Increments today's Open-Topo-Data counter and returns the new total."""
    today = time.strftime("%Y-%m-%d")
    try:
        with open(_DAILY_COUNTER_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    # Prune old dates to keep the file small
    data = {k: v for k, v in data.items() if k == today}
    data[today] = data.get(today, 0) + 1
    with open(_DAILY_COUNTER_FILE, "w") as f:
        json.dump(data, f)
    return data[today]


def _opentopo_check_limit() -> None:
    """Warns or aborts if the daily Open-Topo-Data limit is approached."""
    if OPENTOPO_DAILY_LIMIT is None:
        return
    used = _opentopo_usage_today()
    if used >= OPENTOPO_DAILY_LIMIT:
        raise RuntimeError(
            f"Open-Topo-Data daily limit reached ({used}/{OPENTOPO_DAILY_LIMIT} requests). "
            f"Results may be incomplete. Try again tomorrow or use a self-hosted instance."
        )
    if OPENTOPO_DAILY_WARN_AT and used >= OPENTOPO_DAILY_WARN_AT:
        print(f"  ⚠  Open-Topo-Data: {used}/{OPENTOPO_DAILY_LIMIT} daily requests used.")

# Overpass query for elevation mode (single category)
def _overpass_query_single(bbox, key, value):
    s, w, n, e = bbox
    return (
        f"[out:json][timeout:60];\n(\n"
        f'  node["{key}"="{value}"]({s},{w},{n},{e});\n'
        f'  way["{key}"="{value}"]({s},{w},{n},{e});\n'
        f");\nout center tags;\n"
    )

# Overpass query for score mode (all categories in one call)
_SCORE_QUERY = """
[out:json][timeout:120];
(
  node["leisure"="picnic_table"]({s},{w},{n},{e});
  way["leisure"="picnic_table"]({s},{w},{n},{e});
  node["amenity"="bench"]({s},{w},{n},{e});
  node["leisure"="lounger"]({s},{w},{n},{e});
  node["tourism"="viewpoint"]({s},{w},{n},{e});
  node["amenity"="shelter"]({s},{w},{n},{e});
  way["amenity"="shelter"]({s},{w},{n},{e});
  node["amenity"="parking"]({s},{w},{n},{e});
  way["amenity"="parking"]({s},{w},{n},{e});
  way["highway"="primary"]({s},{w},{n},{e});
  way["highway"="secondary"]({s},{w},{n},{e});
  way["highway"="tertiary"]({s},{w},{n},{e});
  way["highway"="residential"]({s},{w},{n},{e});
  node["tourism"="attraction"]({s},{w},{n},{e});
  node["tourism"="information"]({s},{w},{n},{e});
);
out center tags;
"""


def _run_overpass(query):
    """Sends an Overpass query, tries all mirror servers. Rate-limited."""
    for endpoint in OVERPASS_ENDPOINTS:
        print(f"  → {endpoint.split('/')[2]} ...")
        try:
            _rate_limit("overpass", RATE_LIMIT_OVERPASS)
            resp = requests.post(
                endpoint,
                data=urllib.parse.urlencode({"data": query}),
                headers=HEADERS, timeout=150,
            )
            if resp.status_code == 406:
                _rate_limit("overpass", RATE_LIMIT_OVERPASS)
                resp = requests.get(endpoint, params={"data": query},
                                    headers={"Accept": "*/*", "User-Agent": "POTA-finder/3.0"},
                                    timeout=150)
            resp.raise_for_status()
            els = resp.json().get("elements", [])
            print(f"    OK – {len(els)} elements")
            return els
        except Exception as ex:
            print(f"    Error: {ex}")
            time.sleep(2)
    raise RuntimeError("All Overpass endpoints failed.")


def _el_to_point(el):
    """Extracts lat/lon from an Overpass element."""
    if el["type"] == "node":
        return el["lat"], el["lon"]
    if el["type"] == "way" and "center" in el:
        return el["center"]["lat"], el["center"]["lon"]
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED — ELEVATION
# ═══════════════════════════════════════════════════════════════════════════════

ELEVATION_PROVIDERS = [
    {
        "name": "open-topo-data (SRTM30m)",
        "url":  "https://api.opentopodata.org/v1/srtm30m",
        "build_payload": lambda locs: {
            "locations": "|".join(f"{l['latitude']},{l['longitude']}" for l in locs)},
        "parse":  lambda data: [r.get("elevation") for r in data.get("results", [])],
        "method": "GET",
        "batch_size": 100,
    },
    {
        "name": "open-elevation.com",
        "url":  "https://api.open-elevation.com/api/v1/lookup",
        "build_payload": lambda locs: {"locations": locs},
        "parse":  lambda data: [r.get("elevation") for r in data.get("results", [])],
        "method": "POST",
        "batch_size": 100,
    },
]
RETRY_COUNT = 3


def _fetch_elevation_batch(provider, locations):
    """Fetches one batch from a provider. Rate-limited + daily-limit-aware + retry."""
    rate_key = "opentopo" if "opentopodata" in provider["url"] else "openelevation"
    rate_val = RATE_LIMIT_OPENTOPO if rate_key == "opentopo" else RATE_LIMIT_OPENELEVATION
    payload  = provider["build_payload"](locations)

    if rate_key == "opentopo":
        _opentopo_check_limit()

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            _rate_limit(rate_key, rate_val)
            if provider["method"] == "GET":
                r = requests.get(provider["url"], params=payload, timeout=45)
            else:
                r = requests.post(provider["url"], json=payload, timeout=45)
            r.raise_for_status()
            if rate_key == "opentopo":
                used = _opentopo_increment()
                if OPENTOPO_DAILY_LIMIT and used >= OPENTOPO_DAILY_LIMIT:
                    print(f"  ⚠  Open-Topo-Data daily limit reached ({used}/{OPENTOPO_DAILY_LIMIT}).")
            return provider["parse"](r.json())
        except Exception as e:
            if attempt < RETRY_COUNT:
                wait = 2 ** attempt
                print(f"    Attempt {attempt} failed ({e}) — retrying in {wait}s ...")
                time.sleep(wait)
            else:
                raise


def get_elevations(points):
    """
    Fetches elevation data for a list of {lat, lon} dicts.

    1. Checks the persistent on-disk cache first — zero API calls for known coords.
    2. Fetches only cache misses, trying providers in order with rate limiting.
    3. Stores new results back into the cache.
    """
    # Cache lookup
    all_elev, miss_idx = _cached_elevations(points)
    hits = len(points) - len(miss_idx)
    if hits:
        print(f"  Elevation cache: {hits} hits, {len(miss_idx)} misses")
    if not miss_idx:
        return all_elev

    remaining = miss_idx

    for provider in ELEVATION_PROVIDERS:
        if not remaining:
            break
        bs = provider["batch_size"]
        print(f"\n  Provider: {provider['name']}")
        failed  = []
        batches = [remaining[i:i+bs] for i in range(0, len(remaining), bs)]

        for b_num, idx_batch in enumerate(batches, 1):
            locs = [{"latitude": points[i]["lat"], "longitude": points[i]["lon"]}
                    for i in idx_batch]
            print(f"  → Batch {b_num}/{len(batches)} ({len(idx_batch)} points) ...",
                  end=" ", flush=True)
            try:
                elevs = _fetch_elevation_batch(provider, locs)
                for idx, elev in zip(idx_batch, elevs):
                    all_elev[idx] = elev
                _store_elevations(points, idx_batch, elevs)
                print("OK")
            except Exception as e:
                print(f"FAILED ({e})")
                failed.extend(idx_batch)

        remaining = failed

    _save_elevation_cache()

    if remaining:
        print(f"  ⚠  {len(remaining)} points with no elevation data.")
    return all_elev


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED — CACHING (score mode only)
# ═══════════════════════════════════════════════════════════════════════════════

def _cache_path(geojson_path):
    base = os.path.splitext(os.path.basename(geojson_path))[0]
    return f".cache_pota_{base}.json"


def _load_cache(geojson_path):
    cp = _cache_path(geojson_path)
    if os.path.exists(cp):
        with open(cp, "r", encoding="utf-8") as f:
            data = json.load(f)
        age_h = (time.time() - data.get("_ts", 0)) / 3600
        print(f"  Cache found ({age_h:.1f}h old) — skipping Overpass.")
        return data
    return None


def _save_cache(geojson_path, data):
    cp = _cache_path(geojson_path)
    data["_ts"] = time.time()
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Cache saved: {cp}")


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED — OUTPUT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _osm_url(pt):
    return f"https://www.openstreetmap.org/{pt['osm_type']}/{pt['osm_id']}"

def _gmaps_url(lat, lon):
    return f"https://www.google.com/maps?q={lat},{lon}"


# ═══════════════════════════════════════════════════════════════════════════════
# MODE 1 — ELEVATION
# ═══════════════════════════════════════════════════════════════════════════════

def find_by_elevation(geojson_path, tables=5, benches=5, loungers=5):
    """
    Python API: Finds highest-elevation picnic tables, benches and loungers.

    Args:
        geojson_path: Path to the GeoJSON file
        tables:   Number of picnic tables to return (None = skip)
        benches:  Number of benches to return       (None = skip)
        loungers: Number of loungers to return      (None = skip)

    Returns:
        Dict with "mode", "park", "spots"
    """
    _load_elevation_cache()
    polygon, park_props, bbox = load_geojson(geojson_path)
    park_name = park_props.get("name") or os.path.basename(geojson_path)
    print(f"  Park:  {park_name}")
    print(f"  BBox:  S={bbox[0]:.4f} W={bbox[1]:.4f} N={bbox[2]:.4f} E={bbox[3]:.4f}")

    active = {
        "tables":   tables   is not None,
        "benches":  benches  is not None,
        "loungers": loungers is not None,
    }
    categories = {"picnic_table": [], "bench": [], "lounger": []}

    print("\n-- Overpass queries -----------------------------------------------------")
    if active["tables"]:
        print("  leisure=picnic_table")
        raw = _run_overpass(_overpass_query_single(bbox, "leisure", "picnic_table"))
        categories["picnic_table"] = [
            {"osm_type": e["type"], "osm_id": e["id"],
             "lat": c[0], "lon": c[1], "tags": e.get("tags", {})}
            for e in raw if (c := _el_to_point(e)) and point_in_polygon(c[0], c[1], polygon)
        ]
        print(f"    Inside park: {len(categories['picnic_table'])}")
        time.sleep(2)

    if active["benches"]:
        print("  amenity=bench")
        raw = _run_overpass(_overpass_query_single(bbox, "amenity", "bench"))
        categories["bench"] = [
            {"osm_type": e["type"], "osm_id": e["id"],
             "lat": c[0], "lon": c[1], "tags": e.get("tags", {})}
            for e in raw if (c := _el_to_point(e)) and point_in_polygon(c[0], c[1], polygon)
        ]
        print(f"    Inside park: {len(categories['bench'])}")
        time.sleep(2)

    if active["loungers"]:
        print("  leisure=lounger")
        raw = _run_overpass(_overpass_query_single(bbox, "leisure", "lounger"))
        categories["lounger"] = [
            {"osm_type": e["type"], "osm_id": e["id"],
             "lat": c[0], "lon": c[1], "tags": e.get("tags", {})}
            for e in raw if (c := _el_to_point(e)) and point_in_polygon(c[0], c[1], polygon)
        ]
        print(f"    Inside park: {len(categories['lounger'])}")

    all_pts = (categories["picnic_table"] +
               categories["bench"] +
               categories["lounger"])

    if not all_pts:
        print("No objects found.")
        return {"mode": "elevation", "park": park_props, "spots": []}

    print("\n-- Elevation lookup -----------------------------------------------------")
    elevations = get_elevations(all_pts)
    for i, pt in enumerate(all_pts):
        pt["elevation_m"] = elevations[i]

    def sort_key(p):
        return p["elevation_m"] if p["elevation_m"] is not None else -math.inf

    # Build spot list
    spots = []
    rank  = 0

    cat_map = [
        ("picnic_table", tables,   "Picknicktisch"),
        ("bench",        benches,  "Bank"),
        ("lounger",      loungers, "Liege"),
    ]
    for cat_key, top_n, label in cat_map:
        if top_n is None:
            continue
        sorted_pts = sorted(categories[cat_key], key=sort_key, reverse=True)
        for pt in sorted_pts[:top_n]:
            rank += 1
            spots.append({
                "rank":        rank,
                "category":    cat_key,
                "lat":         pt["lat"],
                "lon":         pt["lon"],
                "elevation_m": pt["elevation_m"],
                "score":       None,
                "breakdown":   {},
                "amenities":   [cat_key],
                "reason":      f"{pt['elevation_m']:.0f}m · {label}" if pt["elevation_m"] else label,
                "osm_url":     _osm_url(pt),
                "gmaps_url":   _gmaps_url(pt["lat"], pt["lon"]),
                "tags":        pt.get("tags", {}),
            })

    return {"mode": "elevation", "park": park_props, "_attribution": OSM_ATTRIBUTION, "spots": spots}


# ═══════════════════════════════════════════════════════════════════════════════
# MODUS 2 — SCORE
# ═══════════════════════════════════════════════════════════════════════════════

_COMFORT_PRIORITY = {"picnic_table": 5, "shelter": 4, "viewpoint": 3, "bench": 2, "lounger": 1}
_COMFORT_CATS     = ["picnic_table", "bench", "lounger", "viewpoint", "shelter"]
_OUTSIDE_OK       = {"parking", "road_major", "road_minor", "tourist_hotspot"}
_TAG_MAP = {
    ("leisure",  "picnic_table"): "picnic_table",
    ("amenity",  "bench"):        "bench",
    ("leisure",  "lounger"):      "lounger",
    ("tourism",  "viewpoint"):    "viewpoint",
    ("amenity",  "shelter"):      "shelter",
    ("amenity",  "parking"):      "parking",
    ("highway",  "primary"):      "road_major",
    ("highway",  "secondary"):    "road_major",
    ("highway",  "tertiary"):     "road_minor",
    ("highway",  "residential"):  "road_minor",
    ("tourism",  "attraction"):   "tourist_hotspot",
    ("tourism",  "information"):  "tourist_hotspot",
}


def _classify(elements, polygon):
    cats = {k: [] for k in set(_TAG_MAP.values())}
    for el in elements:
        c = _el_to_point(el)
        if c is None:
            continue
        lat, lon = c
        tags = el.get("tags", {})
        cat  = next((c_name for (k, v), c_name in _TAG_MAP.items()
                     if tags.get(k) == v), None)
        if cat is None:
            continue
        pt = {"lat": lat, "lon": lon, "tags": tags,
              "osm_type": el["type"], "osm_id": el["id"], "category": cat}
        if cat in _OUTSIDE_OK or point_in_polygon(lat, lon, polygon):
            cats[cat].append(pt)
    for cat, pts in cats.items():
        if pts:
            print(f"    {cat:20}: {len(pts)} objects")
    return cats


def _grid_cluster(cats, grid_m):
    all_pts = [p for c in _COMFORT_CATS for p in cats.get(c, [])]
    if not all_pts:
        return []
    center_lat = sum(p["lat"] for p in all_pts) / len(all_pts)
    dlat = grid_m / 111_320
    dlon = grid_m / (111_320 * math.cos(math.radians(center_lat)))
    cells = {}
    for p in all_pts:
        key = (int(p["lat"] / dlat), int(p["lon"] / dlon))
        cells.setdefault(key, []).append(p)
    spots = []
    for cell_pts in cells.values():
        anchor   = max(cell_pts, key=lambda p: _COMFORT_PRIORITY.get(p["category"], 0))
        amenities = list({p["category"] for p in cell_pts})
        spots.append({
            "lat": anchor["lat"], "lon": anchor["lon"],
            "amenities": amenities, "anchor": anchor,
            "elevation_m": None, "neighbors_elev": [],
            "score": None, "breakdown": {},
        })
    return spots


def _fetch_elevations_with_neighbors(spots, dist_m=300):
    """Holt Hoehen fuer jeden Spot + 4 Nachbarn (N/E/S/W) zur Prominenzberechnung."""
    all_pts, spot_idx, neighbor_idx = [], [], []
    for spot in spots:
        spot_idx.append(len(all_pts))
        all_pts.append({"lat": spot["lat"], "lon": spot["lon"]})
        nb = []
        for bearing in [0, 90, 180, 270]:
            nlat, nlon = offset_point(spot["lat"], spot["lon"], bearing, dist_m)
            nb.append(len(all_pts))
            all_pts.append({"lat": nlat, "lon": nlon})
        neighbor_idx.append(nb)

    total = len(all_pts)
    n_spots = len(spots)
    print(f"\n-- Elevation lookup ({n_spots} spots + {total - n_spots} neighbour points = {total} total) --")
    elevations = get_elevations(all_pts)

    for i, spot in enumerate(spots):
        spot["elevation_m"] = elevations[spot_idx[i]]
        nb_elevs = [elevations[j] for j in neighbor_idx[i] if elevations[j] is not None]
        spot["neighbors_elev"] = nb_elevs
        if spot["elevation_m"] is not None and nb_elevs:
            spot["prominence_m"] = round(spot["elevation_m"] - sum(nb_elevs) / len(nb_elevs), 1)
        else:
            spot["prominence_m"] = None
    return spots


def _score_prominence(prom):
    if prom is None: return 0
    if prom >= 30:   return 30
    if prom >= 15:   return 22
    if prom >= 8:    return 14
    if prom >= 3:    return 7
    if prom >= 0:    return 3
    return 0


def _score_ruhe(spot, road_major, road_minor, hotspots):
    lat, lon = spot["lat"], spot["lon"]

    def nearest(pts):
        return min((haversine_m(lat, lon, p["lat"], p["lon"]) for p in pts), default=99999)

    d_road = min(nearest(road_major), nearest(road_minor))
    d_hot  = nearest(hotspots)

    if d_road < 100:    rs = 0
    elif d_road < 300:  rs = 8
    elif d_road < 800:  rs = 20
    elif d_road < 2000: rs = 15
    else:               rs = 10

    penalty = 5 if d_hot < 100 else 2 if d_hot < 300 else 0
    return max(0, min(25, rs - penalty)), d_road


def _score_sicht(spot):
    prom = spot.get("prominence_m") or 0
    if prom >= 20:   s = 20
    elif prom >= 10: s = 15
    elif prom >= 5:  s = 10
    elif prom >= 0:  s = 5
    else:            s = 2
    if "viewpoint" in spot.get("amenities", []):
        s = min(20, s + 5)
    return s


def _score_comfort(amenities):
    POINTS = {"picnic_table": 8, "shelter": 5, "bench": 4, "viewpoint": 2, "lounger": 2}
    return min(15, sum(POINTS.get(a, 0) for a in amenities))


def _score_access(spot, parking_pts):
    if not parking_pts:
        return 3, None
    lat, lon = spot["lat"], spot["lon"]
    d = min(haversine_m(lat, lon, p["lat"], p["lon"]) for p in parking_pts)
    if d < 200:    pts = 4
    elif d < 800:  pts = 10
    elif d < 2000: pts = 7
    else:          pts = 2
    return pts, round(d)


def _build_reason(spot):
    parts = []
    elev = spot.get("elevation_m")
    prom = spot.get("prominence_m")
    if elev is not None:
        prom_str = f" (+{prom:.0f}m)" if prom and prom > 0 else ""
        parts.append(f"{elev:.0f}m{prom_str}")

    labels = {"picnic_table": "picnic table", "bench": "bench",
              "shelter": "shelter", "viewpoint": "viewpoint", "lounger": "lounger"}
    am = " + ".join(labels[a] for a in spot.get("amenities", []) if a in labels)
    if am:
        parts.append(am)

    d_road = spot.get("nearest_road_m")
    if d_road:
        if d_road >= 800:   parts.append(f"very quiet ({d_road}m from road)")
        elif d_road >= 300: parts.append(f"quiet ({d_road}m from road)")
        else:               parts.append(f"road {d_road}m away")

    d_park = spot.get("nearest_parking_m")
    if d_park:
        parts.append(f"parking {d_park}m")
    return " · ".join(parts)


def find_by_score(geojson_path, top=10, grid=150, refresh=False):
    """
    Python API: Scores spots by prominence, quietness, view, comfort and accessibility.

    Args:
        geojson_path: Path to the GeoJSON file
        top:     Number of spots to return
        grid:    Grid cell size in metres for clustering
        refresh: Ignore cache and re-query Overpass

    Returns:
        Dict with "mode", "park", "spots"
    """
    polygon, park_props, bbox = load_geojson(geojson_path)
    park_name = park_props.get("name") or os.path.basename(geojson_path)
    print(f"  Park:  {park_name}")
    print(f"  BBox:  S={bbox[0]:.4f} W={bbox[1]:.4f} N={bbox[2]:.4f} E={bbox[3]:.4f}")

    _load_elevation_cache()
    # Overpass (with cache)
    cached = None if refresh else _load_cache(geojson_path)
    if cached:
        cats = cached["categories"]
    else:
        print("\n-- Overpass (single combined call) ----------------------------------")
        s, w, n, e = bbox
        elements = _run_overpass(_SCORE_QUERY.format(s=s, w=w, n=n, e=e))
        print("\n  Classification + polygon filter:")
        cats = _classify(elements, polygon)
        _save_cache(geojson_path, {"categories": cats})

    # Grid clustering
    print(f"\n-- Grid clustering ({grid}m) -----------------------------------------")
    spots = _grid_cluster(cats, grid)
    comfort_total = sum(len(cats.get(c, [])) for c in _COMFORT_CATS)
    print(f"  {comfort_total} comfort objects → {len(spots)} spot candidates")

    if not spots:
        return {"mode": "score", "park": park_props, "_attribution": OSM_ATTRIBUTION, "spots": []}

    # Elevation + prominence
    spots = _fetch_elevations_with_neighbors(spots)

    # Scoring
    print("\n-- Scoring --------------------------------------------------------------")
    for spot in spots:
        if spot["elevation_m"] is None:
            spot["score"] = None
            continue
        s_prom              = _score_prominence(spot.get("prominence_m"))
        s_ruhe, d_road      = _score_ruhe(spot, cats.get("road_major", []),
                                           cats.get("road_minor", []),
                                           cats.get("tourist_hotspot", []))
        s_sicht             = _score_sicht(spot)
        s_comf              = _score_comfort(spot.get("amenities", []))
        s_acc, d_park       = _score_access(spot, cats.get("parking", []))
        spot["score"]             = round(s_prom + s_ruhe + s_sicht + s_comf + s_acc, 1)
        spot["nearest_road_m"]    = round(d_road) if d_road < 99999 else None
        spot["nearest_parking_m"] = d_park
        spot["breakdown"]         = {
            "prominenz":   s_prom, "ruhe":       s_ruhe,
            "freie_sicht": s_sicht, "komfort":   s_comf, "erreichbar": s_acc,
        }

    sorted_spots = sorted(
        [s for s in spots if s["score"] is not None],
        key=lambda s: s["score"], reverse=True
    )

    result_spots = []
    for rank, s in enumerate(sorted_spots[:top], 1):
        anc = s["anchor"]
        result_spots.append({
            "rank":              rank,
            "category":          anc["category"],
            "lat":               s["lat"],
            "lon":               s["lon"],
            "elevation_m":       s["elevation_m"],
            "prominence_m":      s.get("prominence_m"),
            "score":             s["score"],
            "breakdown":         s["breakdown"],
            "amenities":         s["amenities"],
            "nearest_road_m":    s.get("nearest_road_m"),
            "nearest_parking_m": s.get("nearest_parking_m"),
            "reason":            _build_reason(s),
            "osm_url":           _osm_url(anc),
            "gmaps_url":         _gmaps_url(s["lat"], s["lon"]),
            "tags":              anc.get("tags", {}),
        })

    return {"mode": "score", "park": park_props, "_attribution": OSM_ATTRIBUTION, "spots": result_spots}


# ═══════════════════════════════════════════════════════════════════════════════
# CLI — OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def _print_elevation_results(result):
    cats = {}
    for s in result["spots"]:
        cats.setdefault(s["category"], []).append(s)

    labels = {"picnic_table": "Picnic Tables", "bench": "Benches", "lounger": "Loungers"}
    for cat, label in labels.items():
        pts = cats.get(cat, [])
        if not pts:
            print(f"\n  No {label} found inside park — skipped.")
            continue
        sep = "-" * 80
        print(f"\n{'=' * 80}\n  Highest {label}  (Top {len(pts)})\n{'=' * 80}")
        print(f"  {'#':>3}  {'Elev(m)':>7}  {'Lat':>10}  {'Lon':>10}  Name")
        print(sep)
        for s in pts:
            elev = f"{s['elevation_m']:.1f}" if s["elevation_m"] else "n/a"
            name = s["tags"].get("name") or ""
            print(f"  {s['rank']:>3}  {elev:>8}  {s['lat']:>10.5f}  {s['lon']:>10.5f}  {name}")
        print(sep)
        print(f"\n  {'#':>3}  {'Elev':>6}  {'OSM':<46}  Google Maps")
        print(f"  {'-'*3}  {'-'*7}  {'-'*46}  {'-'*42}")
        for s in pts:
            elev = f"{s['elevation_m']:.0f}m" if s["elevation_m"] else "n/a"
            print(f"  {s['rank']:>3}  {elev:>7}  {s['osm_url']:<46}  {s['gmaps_url']}")


def _print_score_results(result):
    spots = result["spots"]
    print(f"\n{'=' * 82}")
    print(f"  POTA SCORE RANKING — Top {len(spots)}")
    print(f"  Prominence 30 · Quietness 25 · Open View 20 · Comfort 15 · Access 10")
    print(f"{'=' * 82}")
    print(f"  {'#':>3}  {'Score':>5}  {'Prom':>5}  {'Quiet':>5}  {'View':>5}  {'Comf':>5}  {'Acc':>5}  Reason")
    print(f"  {'-'*3}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*36}")
    for s in spots:
        bd  = s.get("breakdown", {})
        sc  = f"{s['score']:.0f}" if s["score"] else "n/a"
        print(f"  {s['rank']:>3}  {sc:>5}  "
              f"{str(bd.get('prominenz','?')):>5}  {str(bd.get('ruhe','?')):>5}  "
              f"{str(bd.get('freie_sicht','?')):>5}  {str(bd.get('komfort','?')):>5}  "
              f"{str(bd.get('erreichbar','?')):>5}  {s['reason']}")
    print(f"\n  {'#':>3}  {'Score':>5}  {'OSM':<44}  Google Maps")
    print(f"  {'-'*3}  {'-'*5}  {'-'*44}  {'-'*42}")
    for s in spots:
        sc = f"{s['score']:.0f}" if s["score"] else "n/a"
        print(f"  {s['rank']:>3}  {sc:>5}  {s['osm_url']:<44}  {s['gmaps_url']}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI — HTML
# ═══════════════════════════════════════════════════════════════════════════════

def _write_html_elevation(filename, result):
    park_name = result["park"].get("name", "POTA Park")
    rows = ""
    for s in result["spots"]:
        elev = f"{s['elevation_m']:.0f}m" if s["elevation_m"] else "n/a"
        name = html_lib.escape(s["tags"].get("name") or "")
        rows += (f"<tr><td>{s['rank']}</td><td>{elev}</td>"
                 f"<td>{html_lib.escape(s['category'])}</td><td>{name}</td>"
                 f"<td><a href='{s['osm_url']}' target='_blank'>OSM</a> "
                 f"<a href='{s['gmaps_url']}' target='_blank'>Maps</a></td></tr>")
    _write_html_file(filename, park_name,
                     "Highest Amenities by Elevation",
                     "<tr><th>#</th><th>Elevation</th><th>Type</th><th>Name</th><th>Links</th></tr>",
                     rows, subtitle="Ranked by elevation")


def _write_html_score(filename, result):
    park_name = result["park"].get("name", "POTA Park")
    rows = ""
    for s in result["spots"]:
        sc  = f"{s['score']:.0f}" if s["score"] else "n/a"
        col = "#4caf78" if (s["score"] or 0) >= 70 else "#e8a030" if (s["score"] or 0) >= 50 else "#c06040"
        bd  = s.get("breakdown", {})

        def bar(val, mx, clr):
            pct = round((val or 0) / mx * 100)
            return (f"<div style='display:inline-block;background:#1a2420;border-radius:2px;"
                    f"height:4px;width:60px;vertical-align:middle'>"
                    f"<div style='background:{clr};width:{pct}%;height:100%;border-radius:2px'>"
                    f"</div></div>")

        bars = "".join(
            f"<div style='font-size:10px;color:#5a7060'>{lbl} {v} {bar(v,mx,clr)}</div>"
            for lbl, v, mx, clr in [
                ("Prominence",  bd.get("prominenz",   0), 30, "#e8a030"),
                ("Quietness",   bd.get("ruhe",        0), 25, "#4caf78"),
                ("Open View",   bd.get("freie_sicht", 0), 20, "#60aacc"),
                ("Comfort",     bd.get("komfort",     0), 15, "#cc80cc"),
                ("Access",      bd.get("erreichbar",  0), 10, "#c06040"),
            ]
        )
        rows += (f"<tr><td>{s['rank']}</td>"
                 f"<td style='font-size:22px;font-weight:700;color:{col}'>{sc}</td>"
                 f"<td>{html_lib.escape(s['reason'])}<br>"
                 f"<span style='color:#5a7060;font-size:11px'>{s['lat']:.5f}, {s['lon']:.5f}</span></td>"
                 f"<td>{bars}</td>"
                 f"<td><a href='{s['osm_url']}' target='_blank'>OSM</a> "
                 f"<a href='{s['gmaps_url']}' target='_blank'>Maps</a></td></tr>")

    _write_html_file(filename, park_name, "POTA Score Ranking",
                     "<tr><th>#</th><th>Score</th><th>Spot</th><th>Breakdown</th><th>Links</th></tr>",
                     rows, subtitle="Prominence 30 · Quietness 25 · Open View 20 · Comfort 15 · Access 10")


def _write_html_file(filename, park_name, title, thead, tbody, subtitle=""):
    content = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>POTA – {html_lib.escape(park_name)}</title>
<style>
  body{{background:#0d1410;color:#c8d8cc;font-family:Inter,sans-serif;padding:32px;max-width:1100px;margin:0 auto}}
  h1{{font-size:24px;color:#e8a030;margin-bottom:4px}}
  .sub{{color:#5a7060;font-size:12px;font-family:monospace;margin-bottom:28px}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{text-align:left;padding:8px 12px;color:#5a7060;font-weight:400;font-size:10px;
      letter-spacing:2px;text-transform:uppercase;border-bottom:1px solid #2a3d35}}
  td{{padding:11px 12px;border-bottom:1px solid #141c18;vertical-align:middle}}
  tr:hover td{{background:#111810}}
  a{{color:#e8a030;text-decoration:none;margin-right:6px;font-size:11px}}
  a:nth-child(2){{color:#4caf78}}
</style></head>
<body>
<h1>🏕 {html_lib.escape(title)} — {html_lib.escape(park_name)}</h1>
<p class="sub">{html_lib.escape(subtitle)}</p>
<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>
<footer style="margin-top:40px;padding-top:16px;border-top:1px solid #1a2420;
               font-size:11px;color:#3a5040;font-family:monospace">
  © <a href="https://www.openstreetmap.org/copyright" target="_blank"
       style="color:#3a5040">OpenStreetMap contributors</a>,
  <a href="https://opendatacommons.org/licenses/odbl/" target="_blank"
     style="color:#3a5040">ODbL 1.0</a>
  &nbsp;·&nbsp; Elevation: SRTM, public domain (NASA/USGS)
  &nbsp;·&nbsp; Park boundaries: <a href="https://pota-map.info" target="_blank"
                                    style="color:#3a5040">pota-map.info</a>
</footer>
</body></html>"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI — MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="pota_finder.py",
        description="Finds the best POTA activation spots within a GeoJSON park boundary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 pota_finder.py elevation DE-0042.geojson\n"
            "  python3 pota_finder.py elevation DE-0042.geojson -t 10 -b 20\n"
            "  python3 pota_finder.py score     DE-0042.geojson\n"
            "  python3 pota_finder.py score     DE-0042.geojson --top 15 --html\n"
        ),
    )
    sub = parser.add_subparsers(dest="mode", required=True, metavar="mode")

    # ── elevation ──
    pe = sub.add_parser(
        "elevation",
        help="Rank by absolute elevation",
        description="Finds the highest-elevation picnic tables, benches and loungers.\n"
                    "No flags: all 3 categories, top 5 each. With flags: only named categories.",
    )
    pe.add_argument("geojson", help="Path to the GeoJSON file")
    pe.add_argument("-t", "--tables",   type=int, default=None, metavar="N",
                    help="Top-N picnic tables (no flag = all categories top 5)")
    pe.add_argument("-b", "--benches",  type=int, default=None, metavar="N",
                    help="Top-N benches")
    pe.add_argument("-l", "--loungers", type=int, default=None, metavar="N",
                    help="Top-N loungers")
    pe.add_argument("-o", "--output",   default=None, metavar="FILE",
                    help="JSON output file (default: results_<park>.json)")
    pe.add_argument("--html", action="store_true",
                    help="Generate HTML report")

    # ── score ──
    ps = sub.add_parser(
        "score",
        help="Rank by POTA score (prominence, quietness, view, comfort, accessibility)",
        description=(
            "Scores spots by:\n"
            "  Prominence  30 pts — ridge/summit beats flat plateau\n"
            "  Quietness   25 pts — distance to roads and tourist infrastructure\n"
            "  Open View   20 pts — proxy from prominence + surroundings\n"
            "  Comfort     15 pts — picnic table, bench, shelter\n"
            "  Access      10 pts — parking 200-800m is ideal\n\n"
            "Second run on the same park = 0 Overpass calls (cache)."
        ),
    )
    ps.add_argument("geojson",          help="Path to the GeoJSON file")
    ps.add_argument("--top",   type=int, default=10, metavar="N",
                    help="Top-N spots to return (default: 10)")
    ps.add_argument("--grid",  type=int, default=150, metavar="M",
                    help="Grid cell size in metres for clustering (default: 150)")
    ps.add_argument("--refresh", action="store_true",
                    help="Ignore cache and re-query Overpass")
    ps.add_argument("--html",    action="store_true",
                    help="Generate HTML report")
    ps.add_argument("-o", "--output", default=None, metavar="FILE",
                    help="JSON output file (default: score_<park>.json)")

    args = parser.parse_args()
    base = os.path.splitext(os.path.basename(args.geojson))[0]

    print(f"Loading GeoJSON: {args.geojson}")

    if args.mode == "elevation":
        any_explicit = any(x is not None for x in
                           [args.tables, args.benches, args.loungers])
        tables   = args.tables   if args.tables   is not None else (5 if not any_explicit else None)
        benches  = args.benches  if args.benches  is not None else (5 if not any_explicit else None)
        loungers = args.loungers if args.loungers is not None else (5 if not any_explicit else None)

        result = find_by_elevation(args.geojson,
                                   tables=tables, benches=benches, loungers=loungers)
        _print_elevation_results(result)

        out = args.output or f"results_{base}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\nSaved: {out}")

        if args.html:
            html_file = f"results_{base}.html"
            _write_html_elevation(html_file, result)
            print(f"HTML:  {html_file}")

    elif args.mode == "score":
        result = find_by_score(args.geojson,
                               top=args.top, grid=args.grid, refresh=args.refresh)
        _print_score_results(result)

        out = args.output or f"score_{base}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\nSaved: {out}")

        if args.html:
            html_file = f"score_{base}.html"
            _write_html_score(html_file, result)
            print(f"HTML:  {html_file}")


if __name__ == "__main__":
    main()
