# Third-Party Services

This project queries the following free public APIs. All are used with rate
limiting and caching to minimise load. Users are responsible for complying
with each service's terms of use.

---

## OpenStreetMap / Overpass API

- **What for:** Amenities (benches, picnic tables, loungers), roads, parking, tourist infrastructure
- **Endpoints used:**
  - `https://overpass-api.de/api/interpreter` (primary)
  - `https://overpass.kumi.systems/api/interpreter` (fallback)
- **Rate limit applied:** 0.5 req/s (Overpass fair-use recommendation)
- **Data license:** © OpenStreetMap contributors, [ODbL 1.0](https://opendatacommons.org/licenses/odbl/)
- **Attribution required:** Yes — see `DISCLAIMER.md`
- **Terms:** https://wiki.openstreetmap.org/wiki/Overpass_API
- **Self-hosting:** https://github.com/drolbr/Overpass-API

---

## Open-Topo-Data

- **What for:** Elevation lookup (SRTM30m, primary provider)
- **Endpoint:** `https://api.opentopodata.org/v1/srtm30m`
- **Rate limit applied:** 1 req/s
- **Daily limit:** 1000 requests/day (tracked in `.cache_opentopo_daily.json`)
- **Data license:** Public domain (NASA SRTM)
- **Terms:** https://www.opentopodata.org/#rate-limits
- **Self-hosting:** https://github.com/ajnisbet/opentopodata

---

## Open-Elevation

- **What for:** Elevation lookup (SRTM, fallback provider)
- **Endpoint:** `https://api.open-elevation.com/api/v1/lookup`
- **Rate limit applied:** 0.5 req/s
- **Data license:** Public domain (NASA SRTM)
- **Terms:** https://open-elevation.com
- **Self-hosting:** https://github.com/Jorl17/open-elevation

---

## pota-map.info

- **What for:** Source of GeoJSON park boundary files (downloaded manually by the user)
- **Note:** This tool does not query pota-map.info directly. The user downloads the GeoJSON file manually and passes it as an argument.
- **Website:** https://pota-map.info

---

## General Note

This project is not affiliated with any of the services listed above.
If any service changes its terms or limits, update `RATE_LIMIT_*` and
`OPENTOPO_DAILY_LIMIT` constants in `pota_finder.py` accordingly.
