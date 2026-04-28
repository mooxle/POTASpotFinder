"""
find_highest_amenities.py
=========================
Sucht innerhalb eines beliebigen GeoJSON-Polygons nach den
hoechstgelegenen Picknicktischen und Baenken.

Verwendung:
  python3 find_highest_amenities.py DE-0042.geojson
  python3 find_highest_amenities.py DE-0042.geojson --tables 10 --benches 20
  python3 find_highest_amenities.py andere_park.geojson --tables 5 --benches 5
  python3 find_highest_amenities.py park.geojson -t 10 -b 20 -o ergebnisse.json

Optionen:
  geojson            Pfad zur GeoJSON-Datei (Pflicht)
  -t, --tables N     Top-N Picknicktische (Standard: 10)
  -b, --benches N    Top-N Baenke         (Standard: 20)
  -o, --output FILE  JSON-Ausgabedatei    (Standard: results_<parkname>.json)

Benoetigt: pip install requests
"""

import argparse
import json
import math
import os
import time
import urllib.parse
import requests


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

def get_elevations(points, batch_size=100):
    url = "https://api.open-elevation.com/api/v1/lookup"
    all_elevations = []
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        locations = [{"latitude": p["lat"], "longitude": p["lon"]} for p in batch]
        print(f"  → Batch {i // batch_size + 1} ({len(batch)} Punkte) ...")
        try:
            resp = requests.post(url, json={"locations": locations}, timeout=60)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            all_elevations.extend([r.get("elevation") for r in results])
        except Exception as e:
            print(f"    Fehler: {e}")
            all_elevations.extend([None] * len(batch))
        time.sleep(0.5)
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


# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Findet die hoechstgelegenen Picknicktische und Baenke in einem GeoJSON-Park."
    )
    parser.add_argument(
        "geojson",
        help="Pfad zur GeoJSON-Datei, z.B. DE-0042.geojson"
    )
    parser.add_argument(
        "-t", "--tables",
        type=int, default=10, metavar="N",
        help="Top-N Picknicktische anzeigen (Standard: 10)"
    )
    parser.add_argument(
        "-b", "--benches",
        type=int, default=20, metavar="N",
        help="Top-N Baenke anzeigen (Standard: 20)"
    )
    parser.add_argument(
        "-o", "--output",
        default=None, metavar="FILE",
        help="JSON-Ausgabedatei (Standard: results_<parkname>.json)"
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
    print(f"  Suche:   Top {args.tables} Picknicktische, Top {args.benches} Baenke")

    # Overpass
    print("\n-- Overpass-Abfragen ----------------------------------------------------")
    print("  leisure=picnic_table")
    raw_picnic = query_overpass(bbox, "leisure", "picnic_table")
    time.sleep(2)
    print("  amenity=bench")
    raw_bench  = query_overpass(bbox, "amenity", "bench")

    # Polygon-Filter
    picnic_all    = elements_to_points(raw_picnic)
    bench_all     = elements_to_points(raw_bench)
    picnic_tables = [p for p in picnic_all if point_in_polygon(p["lat"], p["lon"], polygon)]
    benches       = [p for p in bench_all  if point_in_polygon(p["lat"], p["lon"], polygon)]

    print(f"\n  Bbox:    {len(picnic_all)} Picknicktische, {len(bench_all)} Baenke")
    print(f"  Im Park: {len(picnic_tables)} Picknicktische, {len(benches)} Baenke")

    if not picnic_tables and not benches:
        print("Keine Objekte im Park gefunden.")
        return

    # Hoehenabfrage
    print("\n-- Open-Elevation -------------------------------------------------------")
    all_points = picnic_tables + benches
    elevations = get_elevations(all_points)
    for i, pt in enumerate(all_points):
        pt["elevation_m"] = elevations[i] if i < len(elevations) else None

    # Sortieren
    def sort_key(p):
        return p["elevation_m"] if p["elevation_m"] is not None else -math.inf

    picnic_sorted = sorted(picnic_tables, key=sort_key, reverse=True)
    bench_sorted  = sorted(benches,       key=sort_key, reverse=True)

    # Ausgabe
    format_table(picnic_sorted, "Hoechstgelegene Picknicktische", n=args.tables)
    format_table(bench_sorted,  "Hoechstgelegene Baenke",         n=args.benches)
    print_links(picnic_sorted, f"Links Picknicktische Top {args.tables}", n=args.tables)
    print_links(bench_sorted,  f"Links Baenke Top {args.benches}",        n=args.benches)

    # JSON speichern
    def enrich(r):
        return {**r, "osm_url": osm_link(r), "gmaps_url": gmaps_link(r)}

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({
            "park":         feature.get("properties", {}),
            "query": {
                "top_tables":  args.tables,
                "top_benches": args.benches,
            },
            "picnic_tables": [enrich(r) for r in picnic_sorted[:args.tables]],
            "benches":       [enrich(r) for r in bench_sorted[:args.benches]],
        }, f, ensure_ascii=False, indent=2)
    print(f"\nGespeichert: {args.output}")


if __name__ == "__main__":
    main()
