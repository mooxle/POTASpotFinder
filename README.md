# 🏕️ POTA Highest Amenities Finder

> Find the highest-elevation picnic tables and benches within any POTA park boundary — because the best activation spot usually has the best view.

As a POTA activator you want to operate from a great location. This tool takes the official park boundary from [pota-map.info](https://pota-map.info), queries OpenStreetMap for benches and picnic tables inside the park, and ranks them by elevation. Each result comes with a direct Google Maps link — where you can often preview the exact spot through Street View or user photos before you even leave home.

---

## ✨ Features

- Works with **any POTA park** that has a GeoJSON boundary on pota-map.info
- Fetches amenities live from **OpenStreetMap** via the Overpass API
- Accurate **point-in-polygon filtering** (ray-casting) — no false positives from the bounding box
- **Elevation ranking** via Open-Topo-Data (SRTM30m), with automatic fallback to Open-Elevation
- Automatic **retry with exponential backoff** if an elevation provider is slow or overloaded
- One-click links to **OpenStreetMap** and **Google Maps** (with photo previews) for every result
- Fully configurable: choose how many tables and benches to return
- Saves results as **JSON** for further processing

---

## 🚀 Quick Start

### 1. Get the park boundary

Go to [pota-map.info](https://pota-map.info), find your park (e.g. `DE-0042` Hoher Vogelsberg), and download the GeoJSON file.

### 2. Install the dependency

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests
```

### 3. Run

```bash
python3 find_highest_amenities.py DE-0042.geojson
```

That's it. Results appear in the terminal and are saved to `results_DE-0042.json`.

---

## 🛠️ Usage

```
python3 find_highest_amenities.py <geojson> [options]
```

### Arguments

| Argument | Description |
|---|---|
| `geojson` | Path to the GeoJSON file (required) |
| `-t`, `--tables N` | Show top-N picnic tables (default: **10**) |
| `-b`, `--benches N` | Show top-N benches (default: **20**) |
| `-o`, `--output FILE` | Output JSON filename (default: `results_<parkname>.json`) |
| `--html-output` | Outputs result as HTML (default: `results_<parkname>.html`) |

### Examples

```bash
# Default — top 10 tables, top 20 benches
python3 find_highest_amenities.py DE-0042.geojson

# Custom counts
python3 find_highest_amenities.py DE-0042.geojson --tables 5 --benches 10

# Short flags
python3 find_highest_amenities.py DE-0042.geojson -t 5 -b 10

# Custom output file
python3 find_highest_amenities.py DE-0042.geojson -t 15 -b 30 -o vogelsberg.json

# Add HTML output with default filename
python3 find_highest_amenities.py DE-0042.geojson --html-output

# Add HTML output with our own filename
python3 find_highest_amenities.py DE-0042.geojson --html-output my_own_filename.html

# Any other park
python3 find_highest_amenities.py US-1234.geojson -t 10 -b 20

# Built-in help
python3 find_highest_amenities.py --help
```

---

## 📋 Sample Output

```
================================================================================
  Hoechstgelegene Picknicktische  (Top 10)
================================================================================
    #  Hoehe(m)         Lat         Lon  Name
--------------------------------------------------------------------------------
    1     772.0    50.51823     9.23901
    2     768.0    50.51713     9.23849
    3     761.0    50.51044     9.22585
  ...
--------------------------------------------------------------------------------

-- Links Picknicktische Top 10 ------------------------------------------------
  #    Hoehe  OSM                                               Google Maps
  ---  -------  ------------------------------------------------  ------------------------------------------
    1    772 m  https://www.openstreetmap.org/node/123456789      https://www.google.com/maps?q=50.51823,9.23901
    2    768 m  https://www.openstreetmap.org/node/123456790      https://www.google.com/maps?q=50.51713,9.23849
```

The Google Maps links are especially useful — Street View and user-uploaded photos often let you **scout the exact spot** before heading out into the field.

---

## 📦 Output JSON

Results are saved as structured JSON for easy reuse:

```json
{
  "park": { "name": "Hoher Vogelsberg Nature Park", "id": "DE-0042" },
  "query": { "top_tables": 10, "top_benches": 20 },
  "picnic_tables": [
    {
      "osm_type": "node",
      "osm_id": 123456789,
      "lat": 50.51823,
      "lon": 9.23901,
      "elevation_m": 772.0,
      "tags": {},
      "osm_url": "https://www.openstreetmap.org/node/123456789",
      "gmaps_url": "https://www.google.com/maps?q=50.51823,9.23901"
    }
  ],
  "benches": [ ... ]
}
```

---

## 🔧 How It Works

1. **Load boundary** — reads the GeoJSON polygon from pota-map.info
2. **Overpass API** — queries OpenStreetMap for `leisure=picnic_table` and `amenity=bench` within the bounding box (with automatic fallback to mirror servers)
3. **Point-in-polygon** — filters results using a ray-casting algorithm to ensure only objects truly inside the park boundary are kept
4. **Elevation** — queries [Open-Topo-Data](https://www.opentopodata.org) (SRTM30m) in batches; falls back to [Open-Elevation](https://api.open-elevation.com) for any failed batches. Each batch is retried up to 3× with exponential backoff before failing over.
5. **Rank & output** — sorts by elevation descending, prints tables, and saves JSON

---

## 📡 Data Sources

| Source | What for |
|---|---|
| [pota-map.info](https://pota-map.info) | Park boundary GeoJSON files |
| [OpenStreetMap](https://www.openstreetmap.org) via [Overpass API](https://overpass-api.de) | Benches and picnic tables |
| [Open-Topo-Data](https://www.opentopodata.org) | Elevation data — primary (SRTM30m) |
| [Open-Elevation](https://api.open-elevation.com) | Elevation data — fallback (SRTM) |

---

## 📝 Notes

- Elevation data is SRTM-based and accurate to roughly ±10 m — good enough to rank spots within a park.
- OSM coverage varies by park. Dense tourist areas are well-mapped; remote wilderness parks may have fewer tagged amenities.
- The Overpass API is a free public service — please don't run the script in rapid loops. One query per park is the intended use.
- If `overpass-api.de` is slow or unresponsive, the script automatically retries with mirror servers.

---

## 🤖 Transparency

The idea and concept behind this tool were conceived by **mooxle (DA6MAX)**. The code was generated with the assistance of [Claude](https://claude.ai) by Anthropic.

---

## 73 de the field

*Built for POTA activators who want to make the most of their time on the air — and enjoy the view while doing it.*
