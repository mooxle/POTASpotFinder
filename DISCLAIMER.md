# Disclaimer

## Intended Use

This tool is designed for **personal, low-frequency use** by individual POTA activators.

It is intended for:
- Finding activation spots in a specific park before a trip
- Running one query per park, occasionally

It is **not** intended for:
- Bulk or automated data extraction
- Repeated programmatic querying in scripts or pipelines
- Commercial use or redistribution of the fetched data
- Any use that would place excessive load on the free public APIs listed below

## API Rate Limits & Compliance

This tool queries the following free public APIs. Users are responsible for complying with each service's terms:

| API | Limit | Terms |
|---|---|---|
| Overpass API (OpenStreetMap) | No hard limit — fair use expected, max ~1 query/2s | [wiki.openstreetmap.org/wiki/Overpass_API](https://wiki.openstreetmap.org/wiki/Overpass_API) |
| Open-Topo-Data | 1 req/s, **1000 req/day** | [opentopodata.org](https://www.opentopodata.org) |
| Open-Elevation | No published hard limit — conservative use expected | [open-elevation.com](https://api.open-elevation.com) |

If you need higher query volumes, you should self-host these services:
- Overpass: [github.com/drolbr/Overpass-API](https://github.com/drolbr/Overpass-API)
- Open-Topo-Data: [github.com/ajnisbet/opentopodata](https://github.com/ajnisbet/opentopodata)

## OpenStreetMap Data License

All amenity, road and infrastructure data is © OpenStreetMap contributors, licensed under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/).

Any use or display of results produced by this tool must include the attribution:

> © OpenStreetMap contributors — https://www.openstreetmap.org/copyright

## No Warranty

This software is provided "as is", without warranty of any kind.
The author is not responsible for misuse, API policy violations caused by users,
or any consequences arising from use of this tool.

See `LICENSE` for the full MIT license terms.
