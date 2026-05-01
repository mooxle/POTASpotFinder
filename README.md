# 🏕️ POTA Amenity & Score Finder

> Find the best POTA activation spots within any park boundary — ranked by elevation, prominence, quietness and comfort.

As a POTA activator you want to operate from a great location. This toolset takes the official park boundary from [pota-map.info](https://pota-map.info), queries OpenStreetMap for benches, picnic tables and loungers inside the park, and helps you find the ideal spot — either by raw elevation or by a multi-factor POTA score. Each result comes with a direct Google Maps link — where you can often preview the exact spot through Street View or user photos before you even leave home.

---

## 🛠️ Two Tools

| Script | What it does |
|---|---|
| `find_highest_amenities.py` | Finds the highest-elevation picnic tables, benches and loungers |
| `pota_score.py` | Scores spots by prominence, quietness, open view, comfort and accessibility |

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
python3 find_highest_amenities.py DE-0042.geojson

# Smart POTA score ranking
python3 pota_score.py DE-0042.geojson
```

---

## 📡 find_highest_amenities.py

Finds the highest-elevation picnic tables, benches and loungers within the park. Fast, simple, great for a first overview.

### Arguments

| Argument | Description |
|---|---|
| `geojson` | Path to the GeoJSON file (required) |
| `-t`, `--tables N` | Show top-N picnic tables |
| `-b`, `--benches N` | Show top-N benches |
| `-l`, `--loungers N` | Show top-N loungers |
| `-o`, `--output FILE` | Output JSON filename (default: `results_<parkname>.json`) |
| `--html-output [FILE]` | Also generate an HTML report |

> **Category logic:** No flags → all 3 categories, top 5 each. As soon as you specify any flag, only the explicitly named categories are queried.

```bash
# All 3 categories, top 5 each
python3 find_highest_amenities.py DE-0042.geojson

# Only tables + benches
python3 find_highest_amenities.py DE-0042.geojson -t 10 -b 20

# Only loungers
python3 find_highest_amenities.py DE-0042.geojson -l 5

# With HTML report
python3 find_highest_amenities.py DE-0042.geojson --html-output
```

---

## 🎯 pota_score.py

Goes beyond elevation — scores every spot by what actually makes a POTA activation great.

### Score Components (0–100 points)

| Component | Points | What it measures |
|---|---|---|
| **Prominence** | 30 | How much higher is the spot than its surroundings (300m radius) — favors ridges and summits over flat plateaus |
| **Quietness** | 25 | Distance to roads and tourist infrastructure — sweet spot 300–800m from roads |
| **Open View** | 20 | Proxy from prominence + absence of tourist hotspots — likely open horizon |
| **Comfort** | 15 | Picnic table, shelter, bench, viewpoint marker |
| **Accessibility** | 10 | Parking within 200–800m — close enough to carry equipment, far enough to be quiet |

### Arguments

| Argument | Description |
|---|---|
| `geojson` | Path to the GeoJSON file (required) |
| `--top N` | Top-N spots to show (default: 10) |
| `--grid M` | Grid cell size in meters for clustering (default: 150) |
| `--refresh` | Ignore cache and re-query all APIs |
| `--html` | Generate an HTML report |
| `-o FILE` | JSON output file (default: `score_<parkname>.json`) |

```bash
# Standard run
python3 pota_score.py DE-0042.geojson

# Top 15 with HTML report
python3 pota_score.py DE-0042.geojson --top 15 --html

# Re-run with cached Overpass data (0 extra API calls)
python3 pota_score.py DE-0042.geojson --top 20

# Force fresh data
python3 pota_score.py DE-0042.geojson --refresh
```

### Sample Output

```
================================================================================
  POTA SCORE RANKING — Top 10
  Prominenz 30 · Ruhe 25 · Freie Sicht 20 · Komfort 15 · Erreichbar 10
================================================================================
  #  Score  Prom  Ruhe  Sicht  Komf  Weg  Begruendung
  ---  -----  -----  -----  -----  -----  -----  ----------------------------------------
    1     81     22     20     15      8    10  612m +12m Prominenz · Bank · ruhig (520m von Strasse) · Parkplatz 380m
    2     74     14     20     10     15    10  783m +8m Prominenz · Picknicktisch + Bank · Parkplatz 340m
    3     68      7     25     10      8    10  540m +4m Prominenz · Bank · sehr ruhig (920m von Strasse) · Parkplatz 680m
```

Note how spot #1 scores higher than the higher-elevation spot #2 — it sits on a ridge with a better view and is further from roads, exactly what you want for a POTA activation.

---

## 🔧 How It Works

### find_highest_amenities.py
1. Queries Overpass API for each requested category
2. Filters results to park polygon via ray-casting
3. Fetches elevation from Open-Topo-Data (SRTM30m) with Open-Elevation fallback
4. Sorts by elevation, outputs table + links

### pota_score.py
1. **One Overpass call** — fetches all categories at once (comfort objects, roads, parking, tourist infrastructure)
2. **Polygon filter** — ray-casting, only park-interior objects kept
3. **Grid clustering** — groups objects into 150m cells, one representative spot per cell
4. **Elevation** — fetches height for each spot centre + 4 neighbour points (N/E/S/W, 300m) to compute local prominence
5. **Scoring** — all calculations local, no extra API calls
6. **Cache** — Overpass result saved as `.cache_score_<park>.json`; subsequent runs skip the Overpass call entirely

---

## 📡 Data Sources

| Source | What for |
|---|---|
| [pota-map.info](https://pota-map.info) | Park boundary GeoJSON files |
| [OpenStreetMap](https://www.openstreetmap.org) via [Overpass API](https://overpass-api.de) | Amenities, roads, parking, tourist infrastructure |
| [Open-Topo-Data](https://www.opentopodata.org) | Elevation data — primary (SRTM30m) |
| [Open-Elevation](https://api.open-elevation.com) | Elevation data — fallback (SRTM) |

---

## 📝 Notes

- Elevation data is SRTM-based, accurate to roughly ±10 m — sufficient for relative ranking within a park.
- OSM coverage varies. Dense tourist areas are well-mapped; remote parks may have fewer tagged amenities.
- Both scripts use free public APIs — please don't run them in rapid loops. One query per park is the intended use.
- `pota_score.py` caches Overpass results automatically. Use `--refresh` only when you need fresh OSM data.

---

## 🤖 Transparency

The idea and concept behind this tool were conceived by **mooxle (DA6MAX)**. The code was generated with the assistance of [Claude](https://claude.ai) by Anthropic.

---

## 73 de the field

*Built for POTA activators who want to make the most of their time on the air — and enjoy the view while doing it.*
