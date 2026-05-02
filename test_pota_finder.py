"""
Regression tests for pota_finder.py
Run with: .venv/bin/pytest test_pota_finder.py -v
"""

import json
import math
import os
import tempfile
import time
from unittest.mock import patch

import pytest

import pota_finder as pf


# ─── Fixtures ────────────────────────────────────────────────────────────────

# A simple 1°×1° square around 9–10 E, 50–51 N
POLYGON = [
    [9.0, 50.0], [10.0, 50.0], [10.0, 51.0], [9.0, 51.0], [9.0, 50.0],
]

PARK_PROPS = {"name": "Test Park"}


@pytest.fixture
def geojson_file(tmp_path):
    feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [POLYGON]},
        "properties": PARK_PROPS,
    }
    p = tmp_path / "test_park.geojson"
    p.write_text(json.dumps(feature), encoding="utf-8")
    return str(p)


def _node(osm_id, lat, lon, tags=None):
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lon, "tags": tags or {}}


# ─── 1. Geometry ─────────────────────────────────────────────────────────────

class TestGeometry:
    def test_point_inside_polygon(self):
        assert pf.point_in_polygon(50.5, 9.5, POLYGON) is True

    def test_point_outside_polygon(self):
        assert pf.point_in_polygon(52.0, 9.5, POLYGON) is False

    def test_point_on_southern_edge(self):
        # Ray-casting: points exactly on the edge are implementation-defined,
        # but should not raise.
        pf.point_in_polygon(50.0, 9.5, POLYGON)

    def test_haversine_equator(self):
        # 1° longitude on the equator ≈ 111 320 m
        d = pf.haversine_m(0.0, 0.0, 0.0, 1.0)
        assert abs(d - 111_320) < 200

    def test_haversine_symmetry(self):
        d1 = pf.haversine_m(50.0, 9.0, 50.5, 9.5)
        d2 = pf.haversine_m(50.5, 9.5, 50.0, 9.0)
        assert abs(d1 - d2) < 1e-6

    def test_haversine_same_point(self):
        assert pf.haversine_m(50.0, 9.0, 50.0, 9.0) == pytest.approx(0.0, abs=1e-9)

    def test_offset_point_north_1km(self):
        lat2, lon2 = pf.offset_point(50.0, 9.0, 0, 1000)
        # Moving north: lat increases, lon unchanged
        assert lat2 > 50.0
        assert abs(lon2 - 9.0) < 1e-4
        # Distance back should be ~1000 m
        assert pf.haversine_m(50.0, 9.0, lat2, lon2) == pytest.approx(1000, abs=1)

    def test_offset_round_trip(self):
        lat2, lon2 = pf.offset_point(50.5, 9.5, 90, 500)
        lat3, lon3 = pf.offset_point(lat2, lon2, 270, 500)
        assert abs(lat3 - 50.5) < 1e-4
        assert abs(lon3 - 9.5) < 1e-4


# ─── 2. Configuration ────────────────────────────────────────────────────────

class TestConfig:
    def test_all_providers_have_rate_key(self):
        for p in pf.ELEVATION_PROVIDERS:
            assert "rate_key" in p, f"Provider '{p['name']}' missing rate_key"

    def test_rate_keys_are_valid_strings(self):
        valid = {"opentopo", "openelevation"}
        for p in pf.ELEVATION_PROVIDERS:
            assert p["rate_key"] in valid, (
                f"Provider '{p['name']}' has unknown rate_key '{p['rate_key']}'"
            )

    def test_earth_radius_defined_before_geometry_functions(self):
        # EARTH_RADIUS_M must exist and be a sensible value
        assert pf.EARTH_RADIUS_M == pytest.approx(6_371_000)

    def test_cache_max_age_h_exists(self):
        assert isinstance(pf.CACHE_MAX_AGE_H, (int, float))
        assert pf.CACHE_MAX_AGE_H > 0

    def test_score_weights_sum_to_100(self):
        total = (
            pf.SCORE_MAX_PROMINENCE
            + pf.SCORE_MAX_QUIETNESS
            + pf.SCORE_MAX_HORIZON
            + pf.SCORE_MAX_COMFORT
            + pf.SCORE_MAX_ACCESSIBILITY
        )
        assert total == 100


# ─── 3. Overpass cache TTL ───────────────────────────────────────────────────

class TestCacheTTL:
    def test_fresh_cache_is_returned(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = {"categories": {}, "_ts": time.time()}
        cp = tmp_path / ".cache_pota_test.json"
        cp.write_text(json.dumps(data))
        result = pf._load_cache("test.geojson")
        assert result is not None

    def test_expired_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        age_seconds = (pf.CACHE_MAX_AGE_H + 1) * 3600
        data = {"categories": {}, "_ts": time.time() - age_seconds}
        cp = tmp_path / ".cache_pota_test.json"
        cp.write_text(json.dumps(data))
        result = pf._load_cache("test.geojson")
        assert result is None

    def test_missing_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert pf._load_cache("nonexistent.geojson") is None


# ─── 4. Classify ─────────────────────────────────────────────────────────────

class TestClassify:
    def test_picnic_table_inside_polygon(self):
        el = _node(1, 50.5, 9.5, {"leisure": "picnic_table"})
        cats = pf._classify([el], POLYGON)
        assert len(cats["picnic_table"]) == 1

    def test_bench_outside_polygon_excluded(self):
        el = _node(2, 52.0, 9.5, {"amenity": "bench"})
        cats = pf._classify([el], POLYGON)
        assert len(cats["bench"]) == 0

    def test_road_outside_polygon_included(self):
        # Roads use _OUTSIDE_OK — polygon check skipped
        el = _node(3, 52.0, 9.5, {"highway": "primary"})
        cats = pf._classify([el], POLYGON)
        assert len(cats["road_major"]) == 1

    def test_unknown_tag_ignored(self):
        el = _node(4, 50.5, 9.5, {"foo": "bar"})
        cats = pf._classify([el], POLYGON)
        total = sum(len(v) for v in cats.values())
        assert total == 0


# ─── 5. Scoring functions ────────────────────────────────────────────────────

class TestScoring:
    def test_prominence_max(self):
        assert pf._score_prominence(30) == pf.SCORE_MAX_PROMINENCE
        assert pf._score_prominence(99) == pf.SCORE_MAX_PROMINENCE

    def test_prominence_zero(self):
        assert pf._score_prominence(0) == 3

    def test_prominence_negative(self):
        assert pf._score_prominence(-1) == 0

    def test_prominence_none(self):
        assert pf._score_prominence(None) == 0

    def test_comfort_picnic_table(self):
        assert pf._score_comfort(["picnic_table"]) == pf.COMFORT_POINTS["picnic_table"]

    def test_comfort_capped_at_max(self):
        score = pf._score_comfort(["picnic_table", "bench", "shelter", "viewpoint", "lounger"])
        assert score <= pf.SCORE_MAX_COMFORT

    def test_comfort_unknown_amenity(self):
        assert pf._score_comfort(["unknown_thing"]) == 0

    def test_access_close_parking(self):
        spot = {"lat": 50.5, "lon": 9.5}
        parking = [{"lat": 50.501, "lon": 9.5}]  # ~111 m away
        pts, dist = pf._score_access(spot, parking)
        assert dist is not None and dist < 200

    def test_access_no_parking(self):
        spot = {"lat": 50.5, "lon": 9.5}
        pts, dist = pf._score_access(spot, [])
        assert pts == 3
        assert dist is None

    def test_horizon_score_full_open(self):
        spot = {"horizon_open_pct": 100, "amenities": []}
        assert pf._score_horizon(spot) == pf.SCORE_MAX_HORIZON

    def test_horizon_score_fully_blocked(self):
        spot = {"horizon_open_pct": 0, "amenities": []}
        assert pf._score_horizon(spot) == 1

    def test_horizon_viewpoint_bonus(self):
        s1 = {"horizon_open_pct": 50, "amenities": []}
        s2 = {"horizon_open_pct": 50, "amenities": ["viewpoint"]}
        assert pf._score_horizon(s2) > pf._score_horizon(s1)

    def test_horizon_viewpoint_capped_at_max(self):
        spot = {"horizon_open_pct": 100, "amenities": ["viewpoint"]}
        assert pf._score_horizon(spot) <= pf.SCORE_MAX_HORIZON

    def test_prominence_string_in_reason(self):
        # prom=0.0 was falsely suppressed before the fix
        spot = {
            "elevation_m": 500, "prominence_m": 0.0,
            "amenities": [], "horizon_open_pct": None,
            "nearest_road_m": None, "nearest_parking_m": None,
        }
        reason = pf._build_reason(spot)
        assert "500" in reason
        assert "(+0m)" not in reason  # 0 prominence should not appear

    def test_positive_prominence_in_reason(self):
        spot = {
            "elevation_m": 700, "prominence_m": 12.0,
            "amenities": [], "horizon_open_pct": None,
            "nearest_road_m": None, "nearest_parking_m": None,
        }
        reason = pf._build_reason(spot)
        assert "(+12m)" in reason


# ─── 6. Grid clustering ──────────────────────────────────────────────────────

class TestGridCluster:
    def _make_cats(self, points):
        cats = {k: [] for k in pf._COMFORT_CATS}
        for lat, lon in points:
            cats["picnic_table"].append(
                {"lat": lat, "lon": lon, "tags": {}, "osm_type": "node",
                 "osm_id": 0, "category": "picnic_table"}
            )
        return cats

    def test_nearby_points_merged(self):
        # Two points ~11 m apart (0.0001° lat/lon) → same 150m grid cell
        cats = self._make_cats([(50.5000, 9.5000), (50.5001, 9.5001)])
        spots = pf._grid_cluster(cats, 150)
        assert len(spots) == 1

    def test_distant_points_separate(self):
        # Two points 500 m apart → different cells
        cats = self._make_cats([(50.5, 9.5), (50.505, 9.505)])
        spots = pf._grid_cluster(cats, 100)
        assert len(spots) == 2

    def test_empty_cats_returns_empty(self):
        cats = {k: [] for k in pf._COMFORT_CATS}
        assert pf._grid_cluster(cats, 150) == []


# ─── 7. Elevation mode — rank regression ─────────────────────────────────────

class TestElevationModeRanks:
    """Regression: each category must restart ranking at 1."""

    TABLES = [
        _node(10, 50.5, 9.5, {"leisure": "picnic_table"}),  # elevation 800
        _node(11, 50.4, 9.4, {"leisure": "picnic_table"}),  # elevation 750
    ]
    BENCHES = [
        _node(20, 50.5, 9.6, {"amenity": "bench"}),  # elevation 790
        _node(21, 50.4, 9.6, {"amenity": "bench"}),  # elevation 700
    ]

    def _overpass_side_effect(self, query):
        if "picnic_table" in query:
            return self.TABLES
        if "bench" in query:
            return self.BENCHES
        return []

    def test_ranks_reset_per_category(self, geojson_file, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pf._elev_cache.clear()

        elevations = [800, 750, 790, 700]
        call_count = [0]

        def fake_get_elevations(points):
            result = []
            for p in points:
                result.append(elevations[call_count[0] % len(elevations)])
                call_count[0] += 1
            return result

        with patch.object(pf, "_run_overpass", side_effect=self._overpass_side_effect), \
             patch.object(pf, "get_elevations", side_effect=fake_get_elevations):
            result = pf.find_by_elevation(geojson_file, tables=2, benches=2, loungers=None)

        table_spots = [s for s in result["spots"] if s["category"] == "picnic_table"]
        bench_spots = [s for s in result["spots"] if s["category"] == "bench"]

        assert [s["rank"] for s in table_spots] == [1, 2], "Tables must rank 1, 2"
        assert [s["rank"] for s in bench_spots] == [1, 2], "Benches must restart at 1"

    def test_tables_sorted_by_elevation_descending(self, geojson_file, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pf._elev_cache.clear()

        # Assign elevations in the order points are processed
        elev_map = {(50.5, 9.5): 800, (50.4, 9.4): 750}

        def fake_get_elevations(points):
            return [elev_map.get((round(p["lat"], 1), round(p["lon"], 1)), 700)
                    for p in points]

        with patch.object(pf, "_run_overpass", side_effect=self._overpass_side_effect), \
             patch.object(pf, "get_elevations", side_effect=fake_get_elevations):
            result = pf.find_by_elevation(geojson_file, tables=2, benches=None, loungers=None)

        elevs = [s["elevation_m"] for s in result["spots"] if s["category"] == "picnic_table"]
        assert elevs == sorted(elevs, reverse=True), "Spots must be sorted high→low"

    def test_loungers_none_skips_category(self, geojson_file, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pf._elev_cache.clear()

        with patch.object(pf, "_run_overpass", side_effect=self._overpass_side_effect), \
             patch.object(pf, "get_elevations", return_value=[800, 750]):
            result = pf.find_by_elevation(geojson_file, tables=2, benches=None, loungers=None)

        cats = {s["category"] for s in result["spots"]}
        assert "bench" not in cats
        assert "lounger" not in cats

    def test_output_contains_required_fields(self, geojson_file, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pf._elev_cache.clear()

        with patch.object(pf, "_run_overpass", side_effect=self._overpass_side_effect), \
             patch.object(pf, "get_elevations", return_value=[800, 750]):
            result = pf.find_by_elevation(geojson_file, tables=2, benches=None, loungers=None)

        assert result["mode"] == "elevation"
        assert "spots" in result
        for s in result["spots"]:
            for field in ("rank", "lat", "lon", "elevation_m", "osm_url", "gmaps_url",
                          "amenities", "reason"):
                assert field in s, f"Field '{field}' missing from spot"


# ─── 8. Score mode — output format ───────────────────────────────────────────

class TestScoreMode:
    ELEMENTS = [
        _node(1, 50.5, 9.5, {"leisure": "picnic_table"}),
        _node(2, 50.51, 9.51, {"amenity": "bench"}),
        _node(3, 51.5, 9.5, {"amenity": "parking"}),    # outside polygon → parking OK
        _node(4, 53.0, 8.0, {"highway": "primary"}),    # outside polygon → road_major OK
    ]

    def test_score_output_fields(self, geojson_file, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pf._elev_cache.clear()

        with patch.object(pf, "_run_overpass", return_value=self.ELEMENTS), \
             patch.object(pf, "get_elevations", return_value=[700] * 200):
            result = pf.find_by_score(geojson_file, top=3, grid=500)

        assert result["mode"] == "score"
        assert "park" in result
        for s in result["spots"]:
            for field in ("rank", "lat", "lon", "elevation_m", "score", "breakdown",
                          "amenities", "reason", "osm_url", "gmaps_url"):
                assert field in s, f"Field '{field}' missing from score spot"

    def test_score_ranks_are_sequential(self, geojson_file, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pf._elev_cache.clear()

        with patch.object(pf, "_run_overpass", return_value=self.ELEMENTS), \
             patch.object(pf, "get_elevations", return_value=[700] * 200):
            result = pf.find_by_score(geojson_file, top=5, grid=500)

        ranks = [s["rank"] for s in result["spots"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_score_sorted_descending(self, geojson_file, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pf._elev_cache.clear()

        with patch.object(pf, "_run_overpass", return_value=self.ELEMENTS), \
             patch.object(pf, "get_elevations", return_value=[700] * 200):
            result = pf.find_by_score(geojson_file, top=5, grid=500)

        scores = [s["score"] for s in result["spots"]]
        assert scores == sorted(scores, reverse=True)
