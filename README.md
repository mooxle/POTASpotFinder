# 🏕️ POTA Finder

> Find the best POTA activation spots within any park boundary — ranked by elevation or by a multi-factor POTA score.

As a POTA activator you want to operate from a great location. This tool takes the official park boundary from [pota-map.info](https://pota-map.info), queries OpenStreetMap for benches, picnic tables and loungers inside the park, and helps you find the ideal spot. Each result comes with a direct Google Maps link — where you can often preview the exact spot through Street View or user photos before you even leave home.

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
# Simple elevation ranking
python3 pota_finder.py elevation DE-0042.geojson

# Smart POTA score ranking
python3 pota_finder.py score DE-0042.geojson
```

---

## 🛠️ Two Modes

```
python3 pota_finder.py <mode> <geojson> [options]
python3 pota_finder.py <mode> --help
```

| Mode | What it does |
|---|---|
| `elevation` | Finds the highest-elevation picnic tables, benches and loungers |
| `score` | Scores spots by prominence, quietness, open view, comfort and accessibility |

---

## 📡 elevation mode

Finds the highest-elevation picnic tables, benches and loungers within the park. Fast, simple, great for a first overview.

**Category logic:** No flags → all 3 categories, top 5 each. As soon as you specify any flag, only the explicitly named categories are queried.

### Arguments

| Argument | Description |
|---|---|
| `geojson` | Path to the GeoJSON file (required) |
| `-t`, `--tables N` | Top-N picnic tables |
| `-b`, `--benches N` | Top-N benches |
| `-l`, `--loungers N` | Top-N loungers |
| `-o`, `--output FILE` | JSON output (default: `results_<park>.json`) |
| `--html` | Generate HTML report |

```bash
# All 3 categories, top 5 each
python3 pota_finder.py elevation DE-0042.geojson

# Only tables + benches
python3 pota_finder.py elevation DE-0042.geojson -t 10 -b 20

# Only loungers, top 5
python3 pota_finder.py elevation DE-0042.geojson -l 5

# With HTML report
python3 pota_finder.py elevation DE-0042.geojson -t 10 --html
```

---

## 🎯 score mode

Goes beyond elevation — scores every spot by what actually makes a POTA activation great. A bench on a quiet ridge at 600m will often score higher than a picnic table on a busy plateau at 780m.

### Score Components (0–100 points)

| Component | Points | What it measures |
|---|---|---|
| **Prominence** | 30 | How much higher is the spot than its surroundings (300m radius) — favors ridges over flat plateaus |
| **Quietness** | 25 | Distance to roads and tourist infrastructure — sweet spot 300–800m from roads |
| **Open View** | 20 | Proxy from prominence + absence of tourist hotspots |
| **Comfort** | 15 | Picnic table, shelter, bench, viewpoint marker |
| **Accessibility** | 10 | Parking within 200–800m — close enough to carry equipment, far enough to be quiet |

### Arguments

| Argument | Description |
|---|---|
| `geojson` | Path to the GeoJSON file (required) |
| `--top N` | Top-N spots (default: 10) |
| `--grid M` | Grid cell size in meters for clustering (default: 150) |
| `--refresh` | Ignore cache and re-query all APIs |
| `--html` | Generate HTML report |
| `-o FILE` | JSON output (default: `score_<park>.json`) |

```bash
# Standard run
python3 pota_finder.py score DE-0042.geojson

# Top 15 with HTML report
python3 pota_finder.py score DE-0042.geojson --top 15 --html

# Re-run with cached Overpass data (0 extra API calls)
python3 pota_finder.py score DE-0042.geojson --top 20

# Force fresh data
python3 pota_finder.py score DE-0042.geojson --refresh

# Finer grid = more candidate spots
python3 pota_finder.py score DE-0042.geojson --grid 80
```

### Sample Output

```
================================================================================
  POTA SCORE RANKING — Top 10
  Prominenz 30 · Ruhe 25 · Freie Sicht 20 · Komfort 15 · Erreichbar 10
================================================================================
  #  Score  Prom  Ruhe  Sicht  Komf  Weg  Begruendung
  ---  -----  -----  -----  -----  -----  -----  ----------------------------------------
    1     81     22     20     15      8    10  612m +12m · Bank · ruhig (520m) · Parkplatz 380m
    2     74     14     20     10     15    10  783m +8m · Picknicktisch + Bank · Parkplatz 340m
    3     68      7     25     10      8    10  540m +4m · Bank · sehr ruhig (920m) · Parkplatz 680m
```

Spot #1 scores higher than the higher-elevation spot #2 — it sits on a ridge with a better view and is further from roads, exactly what you want for a POTA activation.

---

## 🐍 Python API

Both modes are importable as clean functions — useful if you want to embed the logic in your own project.

```python
from pota_finder import find_by_elevation, find_by_score

# Elevation mode
result = find_by_elevation("DE-0042.geojson", tables=10, benches=20)

# Score mode
result = find_by_score("DE-0042.geojson", top=15, grid=150)

# Both return the same format
for spot in result["spots"]:
    print(spot["rank"], spot["elevation_m"], spot["score"], spot["gmaps_url"])
```

### Output Format

Both modes return the same JSON structure — `score` and `breakdown` are `null`/`{}` in elevation mode:

```json
{
  "mode": "elevation | score",
  "park": { "name": "Hoher Vogelsberg Nature Park", "id": "DE-0042" },
  "spots": [
    {
      "rank":         1,
      "lat":          50.517,
      "lon":          9.238,
      "elevation_m":  783,
      "score":        81.0,
      "breakdown":    {
        "prominenz": 22, "ruhe": 20, "freie_sicht": 15, "komfort": 8, "erreichbar": 10
      },
      "amenities":    ["picnic_table", "bench"],
      "reason":       "783m +8m · Picknicktisch · ruhig (520m) · Parkplatz 340m",
      "osm_url":      "https://www.openstreetmap.org/node/123456789",
      "gmaps_url":    "https://www.google.com/maps?q=50.517,9.238"
    }
  ]
}
```

---

## 🔧 How It Works

### elevation mode
1. Queries Overpass API for each requested category separately
2. Filters results to park polygon via ray-casting
3. Fetches elevation from Open-Topo-Data (SRTM30m) with Open-Elevation fallback
4. Sorts by elevation, outputs table + links

### score mode
1. **One Overpass call** — fetches all categories at once (comfort objects, roads, parking, tourist infrastructure)
2. **Polygon filter** — ray-casting, only park-interior objects kept
3. **Grid clustering** — groups objects into 150m cells, one representative spot per cell
4. **Elevation + prominence** — fetches height for each spot centre + 4 neighbour points (N/E/S/W, 300m) to compute local prominence
5. **Scoring** — all calculations local, no extra API calls
6. **Cache** — Overpass result saved as `.cache_pota_<park>.json`; subsequent runs skip the Overpass call entirely

---

## 📡 Data Sources

| Source | What for |
|---|---|
| [pota-map.info](https://pota-map.info) | Park boundary GeoJSON files |
| [OpenStreetMap](https://www.openstreetmap.org) via [Overpass API](https://overpass-api.de) | Amenities, roads, parking, tourist infrastructure |
| [Open-Topo-Data](https://www.opentopodata.org) | Elevation data — primary (SRTM30m) |
| [Open-Elevation](https://api.open-elevation.com) | Elevation data — fallback (SRTM) |

## ⚠️ API Usage

This tool relies on free public APIs.

For heavy usage:
- run your own Overpass instance
- use a paid elevation API

Default endpoints are provided for testing only.

---

## 📝 Notes

- Elevation data is SRTM-based, accurate to roughly ±10 m — sufficient for relative ranking within a park.
- OSM coverage varies. Dense tourist areas are well-mapped; remote parks may have fewer tagged amenities.
- Both modes use free public APIs — please don't run them in rapid loops. One query per park is the intended use.
- `score` mode caches Overpass results automatically. Use `--refresh` only when you need fresh OSM data.

---


## 73 de the field

*Built for POTA activators who want to make the most of their time on the air — and enjoy the view while doing it.*

---

## 🤖 Transparency

The idea and concept behind this tool were conceived by **mooxle (DA6MAX)**. The code was generated with the assistance of [Claude](https://claude.ai) by Anthropic.

