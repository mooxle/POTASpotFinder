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

# Smart POTA score ranking with interactive map
python3 pota_finder.py score DE-0042.geojson --html
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
| `score` | Scores spots by prominence, quietness, horizon, comfort and accessibility |

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

# Only loungers
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
| **Prominence** | 30 | How much higher the spot is than its surroundings (200m radius) — favors ridges over flat plateaus |
| **Quietness** | 25 | Distance to roads and tourist infrastructure — sweet spot 300–800m from roads |
| **Horizon** | 20 | Line-of-sight analysis across 8 compass directions — percentage of directions with a clear radio path |
| **Comfort** | 15 | Picnic table, shelter, bench, viewpoint marker |
| **Accessibility** | 10 | Parking within 200–800m — close enough to carry equipment, far enough to be quiet |

### Arguments

| Argument | Description |
|---|---|
| `geojson` | Path to the GeoJSON file (required) |
| `--top N` | Top-N spots (default: 10) |
| `--grid M` | Grid cell size in metres for clustering (default: 150) |
| `--refresh` | Ignore cache and re-query all APIs |
| `--horizon` | Full horizon sampling (8 dirs × 3 distances). More accurate but slower on first run — cached after. |
| `--html` | Generate interactive HTML map report |
| `-o FILE` | JSON output (default: `score_<park>.json`) |

```bash
# Standard run
python3 pota_finder.py score DE-0042.geojson

# Top 15 with interactive HTML map
python3 pota_finder.py score DE-0042.geojson --top 15 --html

# Full horizon analysis (slower first run, then cached)
python3 pota_finder.py score DE-0042.geojson --horizon --html

# Re-run with all cached data (0 API calls)
python3 pota_finder.py score DE-0042.geojson --top 20

# Force fresh Overpass data
python3 pota_finder.py score DE-0042.geojson --refresh
```

### Sample Output

```
================================================================================
  POTA SCORE RANKING — Top 10
  Prominence 30 · Quietness 25 · Horizon 20 · Comfort 15 · Access 10
================================================================================
  #  Score  Prom  Quiet  Horiz  Comf  Acc  Reason
  ---  -----  -----  -----  -----  -----  -----  ----------------------------------------
    1     84     22     20     20     15     7  629m +21m · bench + shelter + picnic table · open horizon (100%) · quiet (419m) · parking 1090m
    2     78     22     18     20      8    10  780m +18m · picnic table · open horizon (100%) · quiet (307m) · parking 275m
    3     78     22     20     20      6    10  432m +22m · viewpoint + bench · open horizon (100%) · very quiet (760m) · parking 520m
```

Spot #1 scores highest despite not being the tallest — it sits on a ridge with full horizon in all directions, has a picnic table and shelter, and is well away from roads. Exactly what you want for a comfortable POTA activation.

---

## 🗺️ Interactive HTML Map

The `--html` flag generates a self-contained HTML report with a full **Leaflet interactive map** alongside the ranked table:

- Each spot appears as a **colour-coded pin** (green ≥ 70, orange ≥ 50, red below)
- **Click any pin** for a popup with score breakdown, horizon percentage and direct OSM/Maps links
- **Click any table row** to fly the map to that spot
- The file works completely **offline** — Leaflet is inlined at generation time
- OSM attribution is included as required by ODbL

```bash
python3 pota_finder.py score DE-0042.geojson --top 15 --html
# → opens score_DE-0042.html in your browser
```

---

## 🐍 Python API

Both modes are importable as clean functions — useful if you want to embed the logic in your own project.

```python
from pota_finder import find_by_elevation, find_by_score

# Elevation mode
result = find_by_elevation("DE-0042.geojson", tables=10, benches=20)

# Score mode
result = find_by_score("DE-0042.geojson", top=15, grid=150)

# Score mode with full horizon sampling
result = find_by_score("DE-0042.geojson", top=15, horizon=True)

# Both return the same format
for spot in result["spots"]:
    print(spot["rank"], spot["elevation_m"], spot["score"],
          spot["horizon_open_pct"], spot["gmaps_url"])
```

### Output Format

Both modes return the same JSON structure — `score`, `breakdown` and `horizon_open_pct` are `null`/`{}` in elevation mode:

```json
{
  "mode": "elevation | score",
  "park": { "name": "Hoher Vogelsberg Nature Park", "id": "DE-0042" },
  "_attribution": {
    "osm": "© OpenStreetMap contributors, ODbL 1.0 — https://www.openstreetmap.org/copyright",
    "elevation": "SRTM elevation data, public domain — NASA/USGS"
  },
  "spots": [
    {
      "rank":             1,
      "lat":              50.475,
      "lon":              9.254,
      "elevation_m":      629,
      "prominence_m":     21.0,
      "horizon_open_pct": 100,
      "score":            84.0,
      "breakdown": {
        "prominenz": 22, "ruhe": 20, "horizon": 20, "komfort": 15, "erreichbar": 7
      },
      "amenities":        ["bench", "shelter", "picnic_table"],
      "reason":           "629m +21m · bench + shelter + picnic table · open horizon (100%) · quiet (419m from road) · parking 1090m",
      "osm_url":          "https://www.openstreetmap.org/node/123456789",
      "gmaps_url":        "https://www.google.com/maps?q=50.475,9.254"
    }
  ]
}
```

---

## 🔧 How It Works

### elevation mode
1. Queries Overpass API for each requested category separately
2. Filters results to park polygon via ray-casting
3. Fetches elevation from Open-Topo-Data (SRTM30m) with Open-Elevation fallback — rate-limited, results cached to `.cache_elevation.json`
4. Sorts by elevation, outputs table + links

### score mode
1. **One Overpass call** — fetches all categories at once (comfort objects, roads, parking, tourist infrastructure)
2. **Polygon filter** — ray-casting, only park-interior objects kept
3. **Grid clustering** — groups objects into 150m cells, one representative spot per cell
4. **Elevation + horizon** — two-phase progressive sampling:
   - Phase 1 (all spots): centre elevation + 8 directions × 200m for prominence and near horizon
   - Phase 2 (top 50% only, with `--horizon`): 8 directions × 500m + 1000m for full line-of-sight score
5. **Scoring** — all calculations local, no extra API calls
6. **Two-layer cache** — Overpass result saved as `.cache_pota_<park>.json`; elevation results saved to `.cache_elevation.json`; subsequent runs require zero API calls

### Caching behaviour

| Run | Overpass | Elevation |
|---|---|---|
| First run | 1 call | ~27 batches (phase 1) |
| `--horizon` first run | 1 call | ~51 batches (phase 1 + 2) |
| Any subsequent run | 0 calls | 0 calls |

---

## 📡 Data Sources & Attribution

| Source | What for | License |
|---|---|---|
| [pota-map.info](https://pota-map.info) | Park boundary GeoJSON files | See pota-map.info |
| [OpenStreetMap](https://www.openstreetmap.org) via [Overpass API](https://overpass-api.de) | Amenities, roads, parking, tourist infrastructure | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) |
| [Open-Topo-Data](https://www.opentopodata.org) | Elevation data — primary (SRTM30m) | Public domain (NASA SRTM) |
| [Open-Elevation](https://api.open-elevation.com) | Elevation data — fallback (SRTM) | Public domain (NASA SRTM) |
| [Leaflet](https://leafletjs.com) | Interactive map in HTML output | BSD 2-Clause |

### Required Attribution

This tool uses **OpenStreetMap** data, licensed under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/). Any use or display of results must include:

> © OpenStreetMap contributors — [openstreetmap.org/copyright](https://www.openstreetmap.org/copyright)

This attribution is automatically included in all HTML and JSON output.

---

## ⚠️ API Usage

This tool relies on free public APIs. Rate limiting and caching are built in to be a good citizen. For heavy usage, self-host the APIs — see `DISCLAIMER.md` and `THIRD_PARTY_SERVICES.md`.

---

## 🎯 Intended Use

This tool is designed for **personal, low-frequency use** by individual POTA activators.

✅ Intended for:
- Finding a good activation spot in a specific park before a trip
- Running one query per park, occasionally

❌ Not intended for:
- Bulk or automated data extraction
- Repeated programmatic querying in scripts or pipelines
- Any use that places excessive load on the free public APIs it depends on

---

## 📝 Notes

- Elevation data is SRTM-based, accurate to roughly ±10 m — sufficient for relative ranking within a park.
- OSM coverage varies. Dense tourist areas are well-mapped; remote parks may have fewer tagged amenities.
- The horizon score uses a 200m near-ring by default. Add `--horizon` for full 200m/500m/1000m line-of-sight analysis.
- `score` mode caches Overpass results automatically. Use `--refresh` only when you need fresh OSM data.

---

## 73 de the field

*Built for POTA activators who want to make the most of their time on the air — and enjoy the view while doing it.*

---

## 🤖 Transparency

The idea and concept behind this tool were conceived by **mooxle (DA6MAX)**. The code was generated with the assistance of [Claude](https://claude.ai) by Anthropic.
