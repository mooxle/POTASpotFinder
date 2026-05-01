"""
pota_score.py
=============
Findet die besten POTA-Aktivierungsspots innerhalb eines GeoJSON-Parks.

Score-Komponenten (0-100 Punkte):
  Prominenz   30 Pkt  — wie viel hoeher ist der Punkt als seine Umgebung (Kamm/Ruecken)
  Ruhe        25 Pkt  — Distanz zu Hauptstrassen und Touristeninfrastruktur
  Freie Sicht 20 Pkt  — Proxy aus Prominenz + Abwesenheit von Touristen-Hotspots
  Komfort     15 Pkt  — Picknicktisch, Bank, Shelter
  Erreichbar  10 Pkt  — Parkplatz 200-800m = ideal (nicht zu nah = ruhig, nicht zu weit)

Strategie fuer minimale API-Calls:
  1. Ein Overpass-Call fuer alle Kategorien (inkl. Strassen, Tourismus)
  2. Polygon-Filter lokal (Ray-Casting)
  3. Grid-Clustering → Spot-Kandidaten
  4. Elevation fuer Spots + 4 Nachbarpunkte (N/E/S/W, 300m) → Prominenz
  5. Scoring komplett lokal
  6. Cache — zweiter Aufruf = 0 API-Calls

Verwendung:
  python3 pota_score.py DE-0042.geojson
  python3 pota_score.py DE-0042.geojson --top 15 --grid 150
  python3 pota_score.py DE-0042.geojson --refresh --html

Optionen:
  geojson        Pfad zur GeoJSON-Datei (Pflicht)
  --top N        Top-N Spots (Standard: 10)
  --grid M       Rastergroesse in Metern (Standard: 150)
  --refresh      Cache ignorieren
  --html         HTML-Report erzeugen
  -o FILE        JSON-Ausgabedatei (Standard: score_<park>.json)

Benoetigt: pip install requests
"""

import argparse
import json
import math
import os
import time
import urllib.parse
import requests


# ═══════════════════════════════════════════════════════════════════════════════
# GEOMETRIE
# ═══════════════════════════════════════════════════════════════════════════════

def point_in_polygon(lat, lon, polygon):
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
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def offset_point(lat, lon, bearing_deg, dist_m):
    """Verschiebt einen Punkt um dist_m Meter in eine Himmelsrichtung."""
    R = 6_371_000
    d = dist_m / R
    b = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(d) +
                     math.cos(phi1) * math.sin(d) * math.cos(b))
    lam2 = lam1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(phi1),
                              math.cos(d) - math.sin(phi1) * math.sin(phi2))
    return math.degrees(phi2), math.degrees(lam2)


# ═══════════════════════════════════════════════════════════════════════════════
# OVERPASS — EIN CALL FUER ALLES
# ═══════════════════════════════════════════════════════════════════════════════

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

HEADERS = {
    "Accept":       "*/*",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent":   "POTA-score-finder/2.0",
}

OVERPASS_QUERY = """
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


def query_overpass_all(bbox):
    s, w, n, e = bbox
    query = OVERPASS_QUERY.format(s=s, w=w, n=n, e=e)
    for endpoint in OVERPASS_ENDPOINTS:
        print(f"  → {endpoint.split('/')[2]} ...")
        try:
            resp = requests.post(
                endpoint,
                data=urllib.parse.urlencode({"data": query}),
                headers=HEADERS,
                timeout=150,
            )
            if resp.status_code == 406:
                resp = requests.get(endpoint, params={"data": query},
                                    headers={"Accept": "*/*",
                                             "User-Agent": "POTA-score-finder/2.0"},
                                    timeout=150)
            resp.raise_for_status()
            els = resp.json().get("elements", [])
            print(f"    OK – {len(els)} Elemente")
            return els
        except Exception as ex:
            print(f"    Fehler: {ex}")
            time.sleep(2)
    raise RuntimeError("Alle Overpass-Endpunkte fehlgeschlagen.")


def el_center(el):
    if el["type"] == "node":
        return el["lat"], el["lon"]
    if el["type"] == "way" and "center" in el:
        return el["center"]["lat"], el["center"]["lon"]
    return None


def classify_elements(elements, polygon):
    """
    Klassifiziert Elemente nach Kategorie.
    Komfort-Objekte werden auf Park-Polygon gefiltert.
    Strassen, Parkplaetze, Tourismus-Infra: bbox-weit behalten (fuer Distanzberechnung).
    """
    TAG_MAP = {
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
    OUTSIDE_OK = {"parking", "road_major", "road_minor", "tourist_hotspot"}

    cats = {k: [] for k in set(TAG_MAP.values())}

    for el in elements:
        c = el_center(el)
        if c is None:
            continue
        lat, lon = c
        tags = el.get("tags", {})
        cat = None
        for (k, v), c_name in TAG_MAP.items():
            if tags.get(k) == v:
                cat = c_name
                break
        if cat is None:
            continue
        pt = {"lat": lat, "lon": lon, "tags": tags,
              "osm_type": el["type"], "osm_id": el["id"], "category": cat}
        if cat in OUTSIDE_OK or point_in_polygon(lat, lon, polygon):
            cats[cat].append(pt)

    for cat, pts in cats.items():
        if pts:
            print(f"    {cat:20}: {len(pts)}")
    return cats


# ═══════════════════════════════════════════════════════════════════════════════
# GRID-CLUSTERING
# ═══════════════════════════════════════════════════════════════════════════════

COMFORT_PRIORITY = {
    "picnic_table": 5, "shelter": 4, "viewpoint": 3, "bench": 2, "lounger": 1
}


def grid_cluster(cats, grid_m):
    """
    Gruppiert Komfort-Objekte in grid_m x grid_m Meter Zellen.
    Jede Zelle wird zu einem Spot-Kandidaten.
    """
    comfort_cats = ["picnic_table", "bench", "lounger", "viewpoint", "shelter"]
    all_pts = [p for c in comfort_cats for p in cats.get(c, [])]

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
        anchor = max(cell_pts, key=lambda p: COMFORT_PRIORITY.get(p["category"], 0))
        amenities = list({p["category"] for p in cell_pts})
        spots.append({
            "lat":       anchor["lat"],
            "lon":       anchor["lon"],
            "amenities": amenities,
            "anchor":    anchor,
            "elevation_m":   None,
            "neighbors_elev": [],   # wird nach Elevation-Abfrage befuellt
            "score":     None,
            "breakdown": {},
        })
    return spots


# ═══════════════════════════════════════════════════════════════════════════════
# ELEVATION (Spots + Nachbarpunkte fuer Prominenz)
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


def _fetch_batch(provider, locations):
    payload = provider["build_payload"](locations)
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            if provider["method"] == "GET":
                r = requests.get(provider["url"], params=payload, timeout=45)
            else:
                r = requests.post(provider["url"], json=payload, timeout=45)
            r.raise_for_status()
            return provider["parse"](r.json())
        except Exception as e:
            if attempt < RETRY_COUNT:
                wait = 2 ** attempt
                print(f"    Versuch {attempt} fehlgeschlagen ({e}) – warte {wait}s ...")
                time.sleep(wait)
            else:
                raise


def get_elevations(points):
    """Holt Hoehendaten fuer eine Liste von {lat, lon} Dicts."""
    all_elev = [None] * len(points)
    remaining = list(range(len(points)))

    for provider in ELEVATION_PROVIDERS:
        if not remaining:
            break
        bs = provider["batch_size"]
        print(f"\n  Provider: {provider['name']}")
        failed = []
        batches = [remaining[i:i+bs] for i in range(0, len(remaining), bs)]

        for b_num, idx_batch in enumerate(batches, 1):
            locs = [{"latitude": points[i]["lat"], "longitude": points[i]["lon"]}
                    for i in idx_batch]
            print(f"  → Batch {b_num}/{len(batches)} ({len(idx_batch)} Punkte) ...",
                  end=" ", flush=True)
            try:
                elevs = _fetch_batch(provider, locs)
                for idx, elev in zip(idx_batch, elevs):
                    all_elev[idx] = elev
                print("OK")
            except Exception as e:
                print(f"FEHLER ({e})")
                failed.extend(idx_batch)
            time.sleep(0.6)

        remaining = failed

    if remaining:
        print(f"  ⚠  {len(remaining)} Punkte ohne Hoehenangabe.")
    return all_elev


PROMINENCE_DIST_M = 300   # Abstand der Nachbarpunkte in Metern
NEIGHBOR_BEARINGS = [0, 90, 180, 270]   # N, E, S, W


def fetch_spot_and_neighbor_elevations(spots):
    """
    Holt Hoehendate fuer jeden Spot PLUS 4 Nachbarpunkte (N/E/S/W, 300m).
    Berechnet danach die lokale Prominenz.
    """
    # Alle Punkte in einer flachen Liste sammeln
    all_pts = []
    spot_indices   = []  # Index in all_pts fuer jeden Spot
    neighbor_idx   = []  # Liste von 4 Indizes in all_pts fuer jeden Spot

    for spot in spots:
        spot_indices.append(len(all_pts))
        all_pts.append({"lat": spot["lat"], "lon": spot["lon"]})

        nb = []
        for bearing in NEIGHBOR_BEARINGS:
            nlat, nlon = offset_point(spot["lat"], spot["lon"], bearing, PROMINENCE_DIST_M)
            nb.append(len(all_pts))
            all_pts.append({"lat": nlat, "lon": nlon})
        neighbor_idx.append(nb)

    total = len(all_pts)
    spot_count = len(spots)
    print(f"\n-- Hoehenabfrage ({spot_count} Spots + {total - spot_count} Nachbarpunkte = {total} total) --")

    elevations = get_elevations(all_pts)

    # Zurueck in Spots schreiben
    for i, spot in enumerate(spots):
        spot["elevation_m"] = elevations[spot_indices[i]]
        nb_elevs = [elevations[j] for j in neighbor_idx[i] if elevations[j] is not None]
        spot["neighbors_elev"] = nb_elevs
        if spot["elevation_m"] is not None and nb_elevs:
            spot["prominence_m"] = round(spot["elevation_m"] - sum(nb_elevs) / len(nb_elevs), 1)
        else:
            spot["prominence_m"] = None

    return spots


# ═══════════════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def score_prominence(prom_m):
    """30 Punkte. Wie viel hoeher ist der Punkt als seine Umgebung?"""
    if prom_m is None: return 0
    if prom_m >= 30:   return 30
    if prom_m >= 15:   return 22
    if prom_m >= 8:    return 14
    if prom_m >= 3:    return 7
    if prom_m >= 0:    return 3
    return 0   # im Tal / Senke


def score_ruhe(spot, road_major, road_minor, tourist_hotspots):
    """
    25 Punkte. Distanz zu Hauptstrassen und Touristeninfrastruktur.
    Sweet-Spot: 300-800m von Strassen, weit von Tourist-Hotspots.
    """
    lat, lon = spot["lat"], spot["lon"]

    # Naechste Hauptstrasse
    def nearest_dist(pts):
        if not pts:
            return 99999
        return min(haversine_m(lat, lon, p["lat"], p["lon"]) for p in pts)

    d_major  = nearest_dist(road_major)
    d_minor  = nearest_dist(road_minor)
    d_hot    = nearest_dist(tourist_hotspots)

    # Strassen-Score (Ruhe)
    d_road = min(d_major, d_minor)
    if d_road < 100:    road_score = 0   # direkt an der Strasse
    elif d_road < 300:  road_score = 8
    elif d_road < 800:  road_score = 20  # ideal
    elif d_road < 2000: road_score = 15  # ruhig
    else:               road_score = 10  # sehr abgelegen

    # Tourismus-Hotspot-Malus
    if d_hot < 100:     hot_penalty = 5
    elif d_hot < 300:   hot_penalty = 2
    else:               hot_penalty = 0

    return max(0, min(25, road_score - hot_penalty)), d_road


def score_sicht(spot, tourist_hotspots):
    """
    20 Punkte. Proxy fuer freie Sicht:
    Prominenz > 5m = ueberhoht Umgebung (wahrscheinlich offen)
    + weit von dichten Touristeninfrastruktur (weniger bebaut/bewaldet)
    """
    prom = spot.get("prominence_m") or 0
    lat, lon = spot["lat"], spot["lon"]

    # Prominenz als Sicht-Proxy
    if prom >= 20:   sicht = 20
    elif prom >= 10: sicht = 15
    elif prom >= 5:  sicht = 10
    elif prom >= 0:  sicht = 5
    else:            sicht = 2   # Senke — wahrscheinlich verbaut/bewaldet

    # Viewpoint-Bonus (OSM-Mapper hat es explizit als Aussichtspunkt markiert)
    if "viewpoint" in spot.get("amenities", []):
        sicht = min(20, sicht + 5)

    return sicht


def score_comfort(amenities):
    """15 Punkte. Komfort-Ausstattung."""
    POINTS = {"picnic_table": 8, "shelter": 5, "bench": 4,
              "viewpoint": 2, "lounger": 2}
    return min(15, sum(POINTS.get(a, 0) for a in amenities))


def score_access(spot, parking_pts):
    """
    10 Punkte. Erreichbarkeit mit Funkausruestung.
    200-800m = ideal (ruhig aber erreichbar).
    """
    if not parking_pts:
        return 3, None
    lat, lon = spot["lat"], spot["lon"]
    dists = [haversine_m(lat, lon, p["lat"], p["lon"]) for p in parking_pts]
    d = min(dists)

    if d < 200:    pts = 4    # zu nah = belebt
    elif d < 800:  pts = 10   # ideal
    elif d < 2000: pts = 7
    else:          pts = 2
    return pts, round(d)


def compute_scores(spots, cats):
    """Berechnet den POTA-Score fuer alle Spots."""
    road_major      = cats.get("road_major", [])
    road_minor      = cats.get("road_minor", [])
    tourist_hotspot = cats.get("tourist_hotspot", [])
    parking         = cats.get("parking", [])

    for spot in spots:
        if spot["elevation_m"] is None:
            spot["score"] = None
            continue

        prom   = spot.get("prominence_m")
        s_prom = score_prominence(prom)
        s_ruhe, d_road = score_ruhe(spot, road_major, road_minor, tourist_hotspot)
        s_sicht        = score_sicht(spot, tourist_hotspot)
        s_comf         = score_comfort(spot.get("amenities", []))
        s_acc, d_park  = score_access(spot, parking)

        total = round(s_prom + s_ruhe + s_sicht + s_comf + s_acc, 1)

        spot["score"]            = total
        spot["nearest_road_m"]   = round(d_road) if d_road < 99999 else None
        spot["nearest_parking_m"] = d_park
        spot["breakdown"] = {
            "prominenz":    s_prom,
            "ruhe":         s_ruhe,
            "freie_sicht":  s_sicht,
            "komfort":      s_comf,
            "erreichbar":   s_acc,
        }

    return spots


def build_reason(spot):
    parts = []

    elev = spot.get("elevation_m")
    prom = spot.get("prominence_m")
    if elev is not None:
        prom_str = f" (+{prom:.0f}m Prominenz)" if prom and prom > 0 else ""
        parts.append(f"{elev:.0f}m{prom_str}")

    amenity_labels = {
        "picnic_table": "Picknicktisch", "bench": "Bank",
        "shelter": "Schutzdach", "viewpoint": "Aussichtspunkt", "lounger": "Liege",
    }
    am_str = " + ".join(amenity_labels[a] for a in spot.get("amenities", [])
                        if a in amenity_labels)
    if am_str:
        parts.append(am_str)

    d_road = spot.get("nearest_road_m")
    if d_road:
        if d_road >= 800:   parts.append(f"sehr ruhig ({d_road}m von Strasse)")
        elif d_road >= 300: parts.append(f"ruhig ({d_road}m von Strasse)")
        else:               parts.append(f"Strasse {d_road}m")

    d_park = spot.get("nearest_parking_m")
    if d_park:
        parts.append(f"Parkplatz {d_park}m")

    return " · ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════════

def cache_path(geojson_path):
    base = os.path.splitext(os.path.basename(geojson_path))[0]
    return f".cache_score_{base}.json"


def load_cache(geojson_path):
    cp = cache_path(geojson_path)
    if os.path.exists(cp):
        with open(cp, "r", encoding="utf-8") as f:
            data = json.load(f)
        age_h = (time.time() - data.get("_ts", 0)) / 3600
        print(f"  Cache gefunden ({age_h:.1f}h alt): {cp}")
        return data
    return None


def save_cache(geojson_path, data):
    cp = cache_path(geojson_path)
    data["_ts"] = time.time()
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Cache gespeichert: {cp}")


# ═══════════════════════════════════════════════════════════════════════════════
# AUSGABE
# ═══════════════════════════════════════════════════════════════════════════════

def print_results(spots, top_n):
    header = f"  {'#':>3}  {'Score':>5}  {'Prom':>5}  {'Ruhe':>5}  {'Sicht':>5}  {'Komf':>5}  {'Weg':>5}  Begruendung"
    sub    = f"  {'':>3}  {'':>5}  {'Pkt':>5}  {'Pkt':>5}  {'Pkt':>5}  {'Pkt':>5}  {'Pkt':>5}"
    sep    = "  " + "-" * 78

    print(f"\n{'=' * 80}")
    print(f"  POTA SCORE RANKING — Top {top_n}")
    print(f"  Prominenz 30 · Ruhe 25 · Freie Sicht 20 · Komfort 15 · Erreichbar 10")
    print(f"{'=' * 80}")
    print(header)
    print(sub)
    print(sep)

    for i, s in enumerate(spots[:top_n], 1):
        sc = s.get("score")
        bd = s.get("breakdown", {})
        sc_s = f"{sc:.0f}" if sc is not None else "n/a"
        print(f"  {i:>3}  {sc_s:>5}  "
              f"{str(bd.get('prominenz','?')):>5}  "
              f"{str(bd.get('ruhe','?')):>5}  "
              f"{str(bd.get('freie_sicht','?')):>5}  "
              f"{str(bd.get('komfort','?')):>5}  "
              f"{str(bd.get('erreichbar','?')):>5}  "
              f"{build_reason(s)}")

    print(sep)
    print(f"\n  {'#':>3}  {'Score':>5}  OSM{'':40}  Google Maps")
    print(f"  {'-'*3}  {'-'*5}  {'-'*43}  {'-'*42}")
    for i, s in enumerate(spots[:top_n], 1):
        sc  = s.get("score")
        sc_s = f"{sc:.0f}" if sc is not None else "n/a"
        anc  = s["anchor"]
        osm  = f"https://www.openstreetmap.org/{anc['osm_type']}/{anc['osm_id']}"
        gmap = f"https://www.google.com/maps?q={s['lat']},{s['lon']}"
        print(f"  {i:>3}  {sc_s:>5}  {osm:<43}  {gmap}")


# ═══════════════════════════════════════════════════════════════════════════════
# HTML
# ═══════════════════════════════════════════════════════════════════════════════

def write_html(filename, spots, park_props):
    park_name = park_props.get("name", "POTA Park")

    def bar(val, max_val, color):
        pct = round((val or 0) / max_val * 100)
        return (f"<div style='background:#1a2420;border-radius:2px;height:4px;width:80px;display:inline-block;vertical-align:middle'>"
                f"<div style='background:{color};width:{pct}%;height:100%;border-radius:2px'></div></div>")

    rows = ""
    for i, s in enumerate(spots, 1):
        sc  = s.get("score")
        sc_s = f"{sc:.0f}" if sc is not None else "n/a"
        bd  = s.get("breakdown", {})
        anc = s["anchor"]
        osm  = f"https://www.openstreetmap.org/{anc['osm_type']}/{anc['osm_id']}"
        gmap = f"https://www.google.com/maps?q={s['lat']},{s['lon']}"
        col  = "#4caf78" if (sc or 0) >= 70 else "#e8a030" if (sc or 0) >= 50 else "#c06040"

        score_bars = "".join([
            f"<div style='font-size:10px;color:#5a7060;margin-bottom:3px'>"
            f"{label} {val} {bar(val, mx, clr)}</div>"
            for label, val, mx, clr in [
                ("Prominenz",   bd.get("prominenz", 0),   30, "#e8a030"),
                ("Ruhe",        bd.get("ruhe", 0),        25, "#4caf78"),
                ("Freie Sicht", bd.get("freie_sicht", 0), 20, "#60aacc"),
                ("Komfort",     bd.get("komfort", 0),     15, "#cc80cc"),
                ("Erreichbar",  bd.get("erreichbar", 0),  10, "#c06040"),
            ]
        ])

        rows += f"""
        <tr>
          <td style='color:#5a7060;text-align:center;font-size:12px'>{i}</td>
          <td style='font-size:28px;font-weight:700;color:{col};text-align:center;
                     font-family:monospace;line-height:1'>{sc_s}</td>
          <td style='font-size:12px;color:#c8d8cc'>{build_reason(s)}<br>
              <span style='color:#5a7060;font-size:11px'>{s['lat']:.5f}, {s['lon']:.5f}</span>
          </td>
          <td style='min-width:200px'>{score_bars}</td>
          <td style='text-align:right;white-space:nowrap'>
            <a href='{osm}' target='_blank'
               style='color:#e8a030;font-size:11px;margin-right:8px;text-decoration:none'>OSM</a>
            <a href='{gmap}' target='_blank'
               style='color:#4caf78;font-size:11px;text-decoration:none'>Maps</a>
          </td>
        </tr>"""

    html_content = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>POTA Score – {park_name}</title>
<style>
  body{{background:#0d1410;color:#c8d8cc;font-family:Inter,sans-serif;
        padding:32px;max-width:1100px;margin:0 auto}}
  h1{{font-size:26px;color:#e8a030;margin-bottom:4px;letter-spacing:1px}}
  .sub{{color:#5a7060;font-size:12px;font-family:monospace;margin-bottom:32px}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{text-align:left;padding:8px 12px;color:#5a7060;font-weight:400;
      font-size:10px;letter-spacing:2px;text-transform:uppercase;
      border-bottom:1px solid #2a3d35}}
  td{{padding:12px;border-bottom:1px solid #141c18;vertical-align:middle}}
  tr:hover td{{background:#111810}}
</style>
</head>
<body>
<h1>🏕 POTA Score — {park_name}</h1>
<p class="sub">Prominenz 30 · Ruhe 25 · Freie Sicht 20 · Komfort 15 · Erreichbar 10</p>
<table>
  <thead>
    <tr><th>#</th><th>Score</th><th>Spot</th><th>Aufschluesselung</th><th></th></tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
</body>
</html>"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)


# ═══════════════════════════════════════════════════════════════════════════════
# HAUPTPROGRAMM
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Bewertet POTA-Spots nach Prominenz, Ruhe, Sicht, Komfort und Erreichbarkeit."
    )
    parser.add_argument("geojson", help="Pfad zur GeoJSON-Datei")
    parser.add_argument("--top",     type=int, default=10, metavar="N",
                        help="Top-N Spots anzeigen (Standard: 10)")
    parser.add_argument("--grid",    type=int, default=150, metavar="M",
                        help="Rastergroesse in Metern (Standard: 150)")
    parser.add_argument("--refresh", action="store_true",
                        help="Cache ignorieren und neu abfragen")
    parser.add_argument("--html",    action="store_true",
                        help="HTML-Report erzeugen")
    parser.add_argument("-o", "--output", default=None, metavar="FILE",
                        help="JSON-Ausgabedatei (Standard: score_<park>.json)")
    args = parser.parse_args()

    base = os.path.splitext(os.path.basename(args.geojson))[0]
    if args.output is None:
        args.output = f"score_{base}.json"

    # GeoJSON
    print(f"Lade GeoJSON: {args.geojson}")
    with open(args.geojson, "r", encoding="utf-8") as f:
        feature = json.load(f)
    if feature.get("type") == "FeatureCollection":
        feature = feature["features"][0]
    geom    = feature["geometry"]
    polygon = geom["coordinates"][0][0] if geom["type"] == "MultiPolygon" \
              else geom["coordinates"][0]
    park_props = feature.get("properties", {})
    park_name  = park_props.get("name") or base
    print(f"  Park:  {park_name}")

    lons = [c[0] for c in polygon]
    lats = [c[1] for c in polygon]
    bbox = (min(lats), min(lons), max(lats), max(lons))
    print(f"  BBox:  S={bbox[0]:.4f} W={bbox[1]:.4f} N={bbox[2]:.4f} E={bbox[3]:.4f}")

    # Cache
    cached = None if args.refresh else load_cache(args.geojson)
    if cached:
        cats = cached["categories"]
        print("  Verwende Cache — keine Overpass-Abfrage noetig.")
    else:
        print("\n-- Overpass (ein kombinierter Call) ---------------------------------")
        elements = query_overpass_all(bbox)
        print("\n  Polygon-Filter + Klassifikation:")
        cats = classify_elements(elements, polygon)
        save_cache(args.geojson, {"categories": cats})

    # Grid-Clustering
    print(f"\n-- Grid-Clustering ({args.grid}m) ---------------------------------------")
    spots = grid_cluster(cats, args.grid)
    comfort_total = sum(len(cats.get(c, [])) for c in
                        ["picnic_table", "bench", "lounger", "viewpoint", "shelter"])
    print(f"  {comfort_total} Komfort-Objekte → {len(spots)} Spot-Kandidaten")

    if not spots:
        print("Keine Spots gefunden.")
        return

    # Elevation + Prominenz
    spots = fetch_spot_and_neighbor_elevations(spots)

    # Scoring
    print("\n-- Scoring --------------------------------------------------------------")
    print(f"  Strassen (major): {len(cats.get('road_major', []))}")
    print(f"  Strassen (minor): {len(cats.get('road_minor', []))}")
    print(f"  Tourist-Hotspots: {len(cats.get('tourist_hotspot', []))}")
    print(f"  Parkplaetze:      {len(cats.get('parking', []))}")
    spots = compute_scores(spots, cats)

    spots_sorted = sorted(
        [s for s in spots if s["score"] is not None],
        key=lambda s: s["score"],
        reverse=True
    )

    print_results(spots_sorted, args.top)

    # JSON
    output_data = {
        "park":   park_props,
        "params": {"top": args.top, "grid_m": args.grid},
        "spots": [
            {
                **{k: v for k, v in s.items() if k != "neighbors_elev"},
                "reason":    build_reason(s),
                "osm_url":   f"https://www.openstreetmap.org/{s['anchor']['osm_type']}/{s['anchor']['osm_id']}",
                "gmaps_url": f"https://www.google.com/maps?q={s['lat']},{s['lon']}",
            }
            for s in spots_sorted[:args.top]
        ],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\nGespeichert: {args.output}")

    if args.html:
        html_file = f"score_{base}.html"
        write_html(html_file, spots_sorted[:args.top], park_props)
        print(f"HTML:        {html_file}")


if __name__ == "__main__":
    main()
