"""
find_highest_amenities.py
=========================
Sucht innerhalb eines beliebigen GeoJSON-Polygons nach den
hoechstgelegenen Picknicktischen, Baenken und Liegen.

Verwendung:
  python3 find_highest_amenities.py DE-0042.geojson            # alle 3 Kategorien, Top 5
  python3 find_highest_amenities.py DE-0042.geojson -l 5       # nur Liegen, Top 5
  python3 find_highest_amenities.py DE-0042.geojson -t 10 -b 20  # nur Tische + Baenke
  python3 find_highest_amenities.py park.geojson -t 10 -b 20 -l 5 -o ergebnisse.json
  python3 find_highest_amenities.py park.geojson --html-output

Optionen:
  geojson              Pfad zur GeoJSON-Datei (Pflicht)
  -t, --tables N       Top-N Picknicktische  (ohne Argumente: alle 3 Kategorien Top 5)
  -b, --benches N      Top-N Baenke          (einzelne Flags -> nur diese Kategorie)
  -l, --loungers N     Top-N Liegen/Lounger  (einzelne Flags -> nur diese Kategorie)
  -o, --output FILE    JSON-Ausgabedatei     (Standard: results_<parkname>.json)
  --html-output [FILE] Zusaetzlich HTML-Bericht erzeugen (Standard: results_<parkname>.html)

Benoetigt: pip install requests
"""

import argparse
import json
import math
import os
import time
import urllib.parse
import requests
import html


# ── Punkt-im-Polygon (Ray-Casting) ───────────────────────────────────────────

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


# ── Overpass-Abfrage ──────────────────────────────────────────────────────────

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

HEADERS = {
    "Accept":       "*/*",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent":   "POTA-amenity-finder/1.0",
}


def query_overpass(bbox, key, value):
    s, w, n, e = bbox
    query = (
        f"[out:json][timeout:60];\n"
        f"(\n"
        f'  node["{key}"="{value}"]({s},{w},{n},{e});\n'
        f'  way["{key}"="{value}"]({s},{w},{n},{e});\n'
        f");\n"
        f"out center tags;\n"
    )
    for endpoint in OVERPASS_ENDPOINTS:
        print(f"  → {endpoint.split('/')[2]} ...")
        try:
            resp = requests.post(
                endpoint,
                data=urllib.parse.urlencode({"data": query}),
                headers=HEADERS,
                timeout=90,
            )
            if resp.status_code == 406:
                resp = requests.get(
                    endpoint,
                    params={"data": query},
                    headers={"Accept": "*/*", "User-Agent": "POTA-amenity-finder/1.0"},
                    timeout=90,
                )
            resp.raise_for_status()
            data = resp.json()
            print(f"    OK – {len(data.get('elements', []))} Elemente")
            return data.get("elements", [])
        except Exception as ex:
            print(f"    Fehler: {ex}")
            time.sleep(2)
    raise RuntimeError("Alle Overpass-Endpunkte fehlgeschlagen.")


def elements_to_points(elements):
    results = []
    for el in elements:
        if el["type"] == "node":
            lat, lon = el["lat"], el["lon"]
        elif el["type"] == "way" and "center" in el:
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        results.append({
            "osm_type":    el["type"],
            "osm_id":      el["id"],
            "lat":         lat,
            "lon":         lon,
            "tags":        el.get("tags", {}),
            "elevation_m": None,
        })
    return results


# ── Hoehenabfrage ─────────────────────────────────────────────────────────────
#
# Reihenfolge der Provider:
#   1. open-topo-data.com  (SRTM30m, zuverlaessiger, kein API-Key)
#   2. open-elevation.com  (Fallback, haeufig ueberlastet)
#
# Pro Batch: bis zu RETRY_COUNT Versuche mit exponentiellem Backoff.

ELEVATION_PROVIDERS = [
    {
        "name": "open-topo-data (SRTM30m)",
        "url":  "https://api.opentopodata.org/v1/srtm30m",
        # opentopodata erwartet locations als "lat,lon|lat,lon|..."
        "build_payload": lambda locs: {"locations": "|".join(f"{l['latitude']},{l['longitude']}" for l in locs)},
        "parse":         lambda data: [r.get("elevation") for r in data.get("results", [])],
        "method":        "GET",   # opentopodata bevorzugt GET mit query-param
        "batch_size":    100,
    },
    {
        "name": "open-elevation.com",
        "url":  "https://api.open-elevation.com/api/v1/lookup",
        "build_payload": lambda locs: {"locations": locs},
        "parse":         lambda data: [r.get("elevation") for r in data.get("results", [])],
        "method":        "POST",
        "batch_size":    100,
    },
]

RETRY_COUNT = 3


def _fetch_batch(provider, locations):
    """Einzelner Batch-Request an einen Provider. Gibt Liste von Hoehenmetern zurueck."""
    payload = provider["build_payload"](locations)
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            if provider["method"] == "GET":
                resp = requests.get(provider["url"], params=payload, timeout=45)
            else:
                resp = requests.post(provider["url"], json=payload, timeout=45)
            resp.raise_for_status()
            return provider["parse"](resp.json())
        except Exception as e:
            wait = 2 ** attempt
            if attempt < RETRY_COUNT:
                print(f"    Versuch {attempt} fehlgeschlagen ({e}) – warte {wait}s ...")
                time.sleep(wait)
            else:
                raise


def get_elevations(points):
    """
    Fragt Hoehendaten fuer alle Punkte ab.
    Versucht Provider der Reihe nach; faellt ein Provider aus, wird der naechste probiert.
    """
    all_elevations = [None] * len(points)
    remaining_idx  = list(range(len(points)))   # Indizes noch ohne Hoehenangabe

    for provider in ELEVATION_PROVIDERS:
        if not remaining_idx:
            break

        batch_size = provider["batch_size"]
        name       = provider["name"]
        print(f"\n  Provider: {name}")

        failed_idx = []
        batches    = [remaining_idx[i : i + batch_size] for i in range(0, len(remaining_idx), batch_size)]

        for b_num, idx_batch in enumerate(batches, 1):
            batch_points = [points[i] for i in idx_batch]
            locations    = [{"latitude": p["lat"], "longitude": p["lon"]} for p in batch_points]
            print(f"  → Batch {b_num}/{len(batches)} ({len(idx_batch)} Punkte) ...", end=" ", flush=True)
            try:
                elevs = _fetch_batch(provider, locations)
                for idx, elev in zip(idx_batch, elevs):
                    all_elevations[idx] = elev
                print(f"OK")
            except Exception as e:
                print(f"FEHLER ({e})")
                failed_idx.extend(idx_batch)
            time.sleep(0.6)

        remaining_idx = failed_idx

    if remaining_idx:
        print(f"  ⚠  {len(remaining_idx)} Punkte ohne Hoehenangabe (alle Provider fehlgeschlagen).")

    return all_elevations


# ── Links & Ausgabe ───────────────────────────────────────────────────────────

def osm_link(r):
    return f"https://www.openstreetmap.org/{r['osm_type']}/{r['osm_id']}"

def gmaps_link(r):
    return f"https://www.google.com/maps?q={r['lat']},{r['lon']}"


def format_table(rows, title, n):
    sep = "-" * 80
    print(f"\n{'=' * 80}")
    print(f"  {title}  (Top {n})")
    print(f"{'=' * 80}")
    print(f"  {'#':>3}  {'Hoehe(m)':>8}  {'Lat':>10}  {'Lon':>10}  Name")
    print(sep)
    for rank, r in enumerate(rows[:n], 1):
        elev = f"{r['elevation_m']:.1f}" if r["elevation_m"] is not None else "n/a"
        name = r["tags"].get("name") or r["tags"].get("description") or ""
        print(f"  {rank:>3}  {elev:>8}  {r['lat']:>10.5f}  {r['lon']:>10.5f}  {name}")
    print(sep)


def print_links(rows, title, n):
    print(f"\n-- {title} {'-' * max(0, 74 - len(title))}")
    print(f"  {'#':>3}  {'Hoehe':>7}  {'OSM':<48}  Google Maps")
    print(f"  {'-'*3}  {'-'*7}  {'-'*48}  {'-'*42}")
    for rank, r in enumerate(rows[:n], 1):
        elev = f"{r['elevation_m']:.0f} m" if r["elevation_m"] else "n/a"
        print(f"  {rank:>3}  {elev:>7}  {osm_link(r):<48}  {gmaps_link(r)}")

# ── HTML - Ausgabe ───────────────────────────────────────────────────────────

def write_html_report(filename, data):
    def row(r, rank):
        name = r.get("tags", {}).get("name") or r.get("tags", {}).get("description") or ""
        elev = f'{r["elevation_m"]:.1f}' if r.get("elevation_m") is not None else "n/a"
        return f"""
        <tr>
          <td>{rank}</td>
          <td>{html.escape(elev)}</td>
          <td>{r["lat"]:.5f}</td>
          <td>{r["lon"]:.5f}</td>
          <td>{html.escape(name)}</td>
          <td><a href="{html.escape(r["osm_url"])}" target="_blank">OSM</a></td>
          <td><a href="{html.escape(r["gmaps_url"])}" target="_blank">Google Maps</a></td>
        </tr>"""

    def table(title, rows):
        body = "\n".join(row(r, i) for i, r in enumerate(rows, 1))
        return f"""
        <h2>{html.escape(title)}</h2>
        <table>
          <thead>
            <tr>
              <th>#</th><th>Höhe m</th><th>Lat</th><th>Lon</th><th>Name</th><th>OSM</th><th>Google Maps</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>"""

    content = f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>POTA Spot Finder Ergebnisse</title>
<style>
:root {{ color-scheme: dark;}}
body {{  font-family: system-ui, sans-serif;  margin: 2rem;  background: #0d1117;  color: #e6edf3;}}
h1 {{  margin-bottom: .2rem;  color: #f0f6fc;}}
h2 {{  margin-top: 2rem;  color: #f0f6fc;}}
.card {{  background: #161b22;  padding: 1.5rem;  border-radius: 14px;  box-shadow: 0 2px 18px #0008;  border: 1px solid #30363d;}}
table {{  width: 100%;  border-collapse: collapse;  margin: 1rem 0 2rem;}}
th, td {{ padding: .65rem .8rem;  border-bottom: 1px solid #30363d;  text-align: left;}}
th {{  background: #21262d;  color: #f0f6fc;}}
tr:hover {{  background: #1f6feb22;}}
a {{  color: #58a6ff;  font-weight: 600;}}
a:hover {{  color: #79c0ff;}}
.meta {{  color: #8b949e;  margin-bottom: 2rem;}}
</style>
</head>
<body>
<div class="card">
<h1>POTA Spot Finder Ergebnisse</h1>
<div class="meta">Park: {html.escape(data.get("park", {}).get("id", "Unbekannt"))} - {html.escape(data.get("park", {}).get("name", "Unbekannt"))}</div>
{table("Picknicktische", data.get("picnic_tables", []))}
{table("Bänke", data.get("benches", []))}
</div>
</body>
</html>"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)

# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Findet die hoechstgelegenen Picknicktische, Baenke und Liegen in einem GeoJSON-Park."
    )
    parser.add_argument(
        "geojson",
        help="Pfad zur GeoJSON-Datei, z.B. DE-0042.geojson"
    )
    parser.add_argument(
        "-t", "--tables",
        type=int, default=None, metavar="N",
        help="Top-N Picknicktische (Standard: 5; nur diese Kategorie wenn explizit angegeben)"
    )
    parser.add_argument(
        "-b", "--benches",
        type=int, default=None, metavar="N",
        help="Top-N Baenke (Standard: 5; nur diese Kategorie wenn explizit angegeben)"
    )
    parser.add_argument(
        "-l", "--loungers",
        type=int, default=None, metavar="N",
        help="Top-N Liegen/Lounger (Standard: 5; nur diese Kategorie wenn explizit angegeben)"
    )
    parser.add_argument(
        "-o", "--output",
        default=None, metavar="FILE",
        help="JSON-Ausgabedatei (Standard: results_<parkname>.json)"
    )
    parser.add_argument(
        "--html-output",
        nargs="?",
        const=True,
        default=None,
        metavar="FILE",
        help="HTML-Ausgabedatei (Standard: wie JSON, aber .html)"
    )
    args = parser.parse_args()

    # Ausgabedatei ableiten wenn nicht angegeben
    if args.output is None:
        base = os.path.splitext(os.path.basename(args.geojson))[0]
        args.output = f"results_{base}.json"

    # GeoJSON laden
    print(f"Lade GeoJSON: {args.geojson}")
    with open(args.geojson, "r", encoding="utf-8") as f:
        feature = json.load(f)

    # Geometry ermitteln (Feature oder FeatureCollection)
    if feature.get("type") == "FeatureCollection":
        feature = feature["features"][0]
    geom = feature["geometry"]
    # Aeusseren Ring des ersten Polygons nehmen
    if geom["type"] == "MultiPolygon":
        polygon = geom["coordinates"][0][0]
    else:
        polygon = geom["coordinates"][0]

    park_name = feature.get("properties", {}).get("name") or os.path.basename(args.geojson)
    print(f"  Park:    {park_name}")
    print(f"  Punkte:  {len(polygon)} Stuetzpunkte im Polygon")

    lons = [c[0] for c in polygon]
    lats = [c[1] for c in polygon]
    bbox = (min(lats), min(lons), max(lats), max(lons))
    print(f"  BBox:    S={bbox[0]:.4f} W={bbox[1]:.4f} N={bbox[2]:.4f} E={bbox[3]:.4f}")
    # Logik: keine Argumente -> alle Kategorien mit je Top 5
    #         mind. ein Argument -> nur die explizit genannten Kategorien
    any_explicit = any(x is not None for x in [args.tables, args.benches, args.loungers])
    n_tables   = args.tables   if args.tables   is not None else (5 if not any_explicit else None)
    n_benches  = args.benches  if args.benches  is not None else (5 if not any_explicit else None)
    n_loungers = args.loungers if args.loungers is not None else (5 if not any_explicit else None)

    active = {
        "tables":   n_tables   is not None,
        "benches":  n_benches  is not None,
        "loungers": n_loungers is not None,
    }
    suche_parts = []
    if active["tables"]:   suche_parts.append(f"Top {n_tables} Picknicktische")
    if active["benches"]:  suche_parts.append(f"Top {n_benches} Baenke")
    if active["loungers"]: suche_parts.append(f"Top {n_loungers} Liegen")
    print(f"  Suche:   {', '.join(suche_parts) or '(keine Kategorien aktiv)'}") 

    # Overpass — nur aktive Kategorien abfragen
    print("\n-- Overpass-Abfragen ----------------------------------------------------")
    picnic_tables, benches, loungers = [], [], []

    if active["tables"]:
        print("  leisure=picnic_table")
        raw = query_overpass(bbox, "leisure", "picnic_table")
        all_pts = elements_to_points(raw)
        picnic_tables = [p for p in all_pts if point_in_polygon(p["lat"], p["lon"], polygon)]
        print(f"    Im Park: {len(picnic_tables)} / {len(all_pts)}")
        time.sleep(2)

    if active["benches"]:
        print("  amenity=bench")
        raw = query_overpass(bbox, "amenity", "bench")
        all_pts = elements_to_points(raw)
        benches = [p for p in all_pts if point_in_polygon(p["lat"], p["lon"], polygon)]
        print(f"    Im Park: {len(benches)} / {len(all_pts)}")
        time.sleep(2)

    if active["loungers"]:
        print("  leisure=lounger")
        raw = query_overpass(bbox, "leisure", "lounger")
        all_pts = elements_to_points(raw)
        loungers = [p for p in all_pts if point_in_polygon(p["lat"], p["lon"], polygon)]
        print(f"    Im Park: {len(loungers)} / {len(all_pts)}")

    if not picnic_tables and not benches and not loungers:
        print("Keine Objekte im Park gefunden.")
        return

    # Hoehenabfrage — nur nicht-leere Kategorien
    print("\n-- Hoehenabfrage --------------------------------------------------------")
    all_points = picnic_tables + benches + loungers
    if not all_points:
        print("Nichts zu parsen.")
        return
    elevations = get_elevations(all_points)

    for i, pt in enumerate(all_points):
        pt["elevation_m"] = elevations[i] if i < len(elevations) else None

    # Sortieren
    def sort_key(p):
        return p["elevation_m"] if p["elevation_m"] is not None else -math.inf

    picnic_sorted  = sorted(picnic_tables, key=sort_key, reverse=True)
    bench_sorted   = sorted(benches,       key=sort_key, reverse=True)
    lounger_sorted = sorted(loungers,      key=sort_key, reverse=True)

    # Ausgabe — leere Kategorien ueberspringen
    if picnic_sorted:
        format_table(picnic_sorted, "Hoechstgelegene Picknicktische", n=n_tables)
        print_links(picnic_sorted, f"Links Picknicktische Top {n_tables}", n=n_tables)
    else:
        print("\n  Keine Picknicktische im Park gefunden — uebersprungen.")

    if bench_sorted:
        format_table(bench_sorted, "Hoechstgelegene Baenke", n=n_benches)
        print_links(bench_sorted, f"Links Baenke Top {n_benches}", n=n_benches)
    else:
        print("\n  Keine Baenke im Park gefunden — uebersprungen.")

    if lounger_sorted:
        format_table(lounger_sorted, "Hoechstgelegene Liegen", n=n_loungers)
        print_links(lounger_sorted, f"Links Liegen Top {n_loungers}", n=n_loungers)
    else:
        print("\n  Keine Liegen im Park gefunden — uebersprungen.")

    # Data enrichment
    def enrich(r):
        return {**r, "osm_url": osm_link(r), "gmaps_url": gmaps_link(r)}

    # Resultat in variable zwischenspeichern
    result_data = {
        "park": feature.get("properties", {}),
        "query": {
            "top_tables":   n_tables,
            "top_benches":  n_benches,
            "top_loungers": n_loungers,
        },
        "picnic_tables": [enrich(r) for r in picnic_sorted[:n_tables]],
        "benches":       [enrich(r) for r in bench_sorted[:n_benches]],
        "loungers":      [enrich(r) for r in lounger_sorted[:n_loungers]],
    }

    # JSON speichern
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    print(f"\nGespeichert: {args.output}")

    # HTML Ausgabe wenn gewuenscht
    if args.html_output is not None:
        base = os.path.splitext(os.path.basename(args.geojson))[0]

        if args.html_output is True:
            html_file = f"results_{base}.html"
        else:
            html_file = args.html_output

        write_html_report(html_file, result_data)
        print(f"HTML gespeichert: {html_file}")

if __name__ == "__main__":
    main()
