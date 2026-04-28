# POTASpotFinder
CLI tool to find the highest-elevation picnic tables and benches within any GeoJSON park boundary. Park outlines sourced from pota-map.info. Queries OpenStreetMap via Overpass API, filters results with ray-casting point-in-polygon, and enriches each result with elevation data. Outputs ranked tables with OSM and Google Maps links.
