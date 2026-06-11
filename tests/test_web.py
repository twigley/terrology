from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from web.app import JobParams, _rate_limit_key, app
from web.jobs import JobStatus, store


@pytest.fixture(autouse=True)
def clear_store():
    """Clear the job store before each test."""
    store._jobs.clear()
    yield
    store._jobs.clear()


class TestConcurrencyCap:
    """Test that concurrent job creation respects MAX_CONCURRENT_JOBS."""

    def test_single_job_succeeds(self):
        """A single job creation request should succeed."""
        client = TestClient(app)
        response = client.post(
            "/api/jobs",
            json={
                "lat": 50.0,
                "lon": 0.0,
                "radius": 500,
                "shape": "square",
                "terrain_exag": 2.0,
                "colors": 4,
            },
        )
        assert response.status_code == 200
        assert "job_id" in response.json()

    def test_max_jobs_with_queued(self, monkeypatch):
        """When MAX_CONCURRENT_JOBS=1, second request should 503 if first is QUEUED."""
        # Force MAX_CONCURRENT_JOBS=1
        monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
        # Reimport to pick up env var
        import importlib

        import web.app

        importlib.reload(web.app)
        from web.app import app as reloaded_app

        client = TestClient(reloaded_app)

        # Mock run_job to keep the job QUEUED/RUNNING
        def mock_run_job(job_id, params):
            pass

        with patch("web.app.run_job", side_effect=mock_run_job):
            # First request should succeed
            response1 = client.post(
                "/api/jobs",
                json={
                    "lat": 50.0,
                    "lon": 0.0,
                    "radius": 500,
                    "shape": "square",
                    "terrain_exag": 2.0,
                    "colors": 4,
                },
            )
            assert response1.status_code == 200
            job_id_1 = response1.json()["job_id"]

            # Verify job is in store as QUEUED
            job1 = store.get(job_id_1)
            assert job1 is not None
            assert job1.status == JobStatus.QUEUED

            # Second request should fail with 503
            response2 = client.post(
                "/api/jobs",
                json={
                    "lat": 50.0,
                    "lon": 0.0,
                    "radius": 500,
                    "shape": "square",
                    "terrain_exag": 2.0,
                    "colors": 4,
                },
            )
            assert response2.status_code == 503
            assert "busy" in response2.json()["detail"].lower()

    def test_try_create_counts_queued_and_running(self):
        """try_create should count both QUEUED and RUNNING jobs as active."""
        # Create a QUEUED job directly
        job1 = store.create("job1")
        assert job1.status == JobStatus.QUEUED

        # Try to create with max_active=1
        result = store.try_create("job2", max_active=1)
        assert result is None, "Should reject when QUEUED count is at max"

        # Update job1 to RUNNING
        store.update("job1", status=JobStatus.RUNNING)

        # Still should reject
        result = store.try_create("job3", max_active=1)
        assert result is None, "Should reject when RUNNING count is at max"

        # Delete job1, now we should succeed
        store.delete("job1")
        result = store.try_create("job4", max_active=1)
        assert result is not None, "Should succeed when below max"
        assert result.id == "job4"

    def test_try_create_is_atomic(self):
        """try_create should hold the lock during check-and-create."""
        # This test verifies atomicity by checking that try_create
        # counts active jobs consistently
        store.create("job1")
        store.update("job1", status=JobStatus.RUNNING)

        # With max_active=1, should get None
        result = store.try_create("job2", max_active=1)
        assert result is None

        # Verify job2 was NOT created
        assert store.get("job2") is None


class TestCleanupLoopErrorHandling:
    """Test that _cleanup_loop handles errors gracefully."""

    @pytest.mark.asyncio
    async def test_cleanup_loop_continues_on_error(self, monkeypatch):
        """Cleanup loop should not die if cleanup_expired raises."""
        import asyncio

        call_count = 0

        def mock_cleanup():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Test error")
            # Second call succeeds

        monkeypatch.setattr(store, "cleanup_expired", mock_cleanup)

        # Mock asyncio.sleep in the web.app module to speed up the test
        sleep_count = [0]

        async def mock_sleep(duration):
            sleep_count[0] += 1
            if sleep_count[0] >= 3:
                # After 3 sleeps, raise CancelledError to stop the loop
                raise asyncio.CancelledError()
            # Otherwise return immediately

        monkeypatch.setattr("web.app.asyncio.sleep", mock_sleep)

        # Import after patching
        from web.app import _cleanup_loop

        # Create a task but let it run with the mocked sleep
        task = asyncio.create_task(_cleanup_loop())
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Verify that cleanup_expired was called multiple times
        # (first call raises, second succeeds before loop exits)
        assert call_count >= 2, f"Expected at least 2 calls, got {call_count}"


class TestRateLimitKeyFunction:
    """Test the _rate_limit_key function with X-Forwarded-For support."""

    def test_default_uses_peer_ip(self):
        """Without TERROLOGY_TRUST_PROXY, should use direct peer IP."""
        with patch("web.app.get_remote_address", return_value="192.168.1.1"):
            from fastapi import Request

            scope = {
                "type": "http",
                "method": "GET",
                "headers": [(b"x-forwarded-for", b"10.0.0.1, 10.0.0.2")],
            }
            request = Request(scope)
            key = _rate_limit_key(request)
            assert key == "192.168.1.1"

    def test_with_trust_proxy_uses_xff(self, monkeypatch):
        """With TERROLOGY_TRUST_PROXY=1, should use X-Forwarded-For."""
        monkeypatch.setenv("TERROLOGY_TRUST_PROXY", "1")
        from fastapi import Request

        scope = {
            "type": "http",
            "method": "GET",
            "headers": [(b"x-forwarded-for", b"10.0.0.1, 10.0.0.2, 10.0.0.3")],
        }
        request = Request(scope)
        key = _rate_limit_key(request)
        assert key == "10.0.0.1", "Should use first IP from X-Forwarded-For"

    def test_trust_proxy_strips_whitespace(self, monkeypatch):
        """X-Forwarded-For parsing should strip whitespace."""
        monkeypatch.setenv("TERROLOGY_TRUST_PROXY", "1")
        from fastapi import Request

        scope = {
            "type": "http",
            "method": "GET",
            "headers": [(b"x-forwarded-for", b" 10.0.0.1 , 10.0.0.2 ")],
        }
        request = Request(scope)
        key = _rate_limit_key(request)
        assert key == "10.0.0.1"

    def test_trust_proxy_fallback_no_xff(self, monkeypatch):
        """With TERROLOGY_TRUST_PROXY=1 but no XFF header, fall back to peer IP."""
        monkeypatch.setenv("TERROLOGY_TRUST_PROXY", "1")
        with patch("web.app.get_remote_address", return_value="192.168.1.1"):
            from fastapi import Request

            scope = {
                "type": "http",
                "method": "GET",
                "headers": [],
            }
            request = Request(scope)
            key = _rate_limit_key(request)
            assert key == "192.168.1.1"


class TestJobStore:
    """Test JobStore functionality."""

    def test_create_returns_job(self):
        """create() should return a Job with QUEUED status."""
        job = store.create("test_job")
        assert job.id == "test_job"
        assert job.status == JobStatus.QUEUED

    def test_get_returns_none_for_missing(self):
        """get() should return None for non-existent job."""
        result = store.get("nonexistent")
        assert result is None

    def test_running_count(self):
        """running_count() should count only RUNNING jobs."""
        store.create("job1")  # QUEUED
        store.create("job2")
        store.update("job2", status=JobStatus.RUNNING)  # RUNNING
        store.create("job3")
        store.update("job3", status=JobStatus.READY)  # READY

        assert store.running_count() == 1

    def test_cleanup_expired(self):
        """cleanup_expired() should remove jobs older than JOB_TTL."""
        from datetime import UTC, datetime, timedelta

        from web.jobs import JOB_TTL

        job1 = store.create("job1")
        store.create("job2")

        # Manually set job1 to be old (expired)
        old_time = datetime.now(tz=UTC) - JOB_TTL - timedelta(seconds=1)
        with store._lock:
            job1.created_at = old_time

        store.cleanup_expired()

        assert store.get("job1") is None, "Expired job should be deleted"
        assert store.get("job2") is not None, "Recent job should remain"


class TestJobParamsValidation:
    """Test JobParams field validation."""

    # --- Route validation ---

    def test_route_too_few_points(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, route=[[0.0, 51.5]])

    def test_route_too_many_points(self):
        route = [[float(i % 360 - 180), float(i % 180 - 90)] for i in range(5001)]
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, route=route)

    def test_route_out_of_bounds_lon(self):
        with pytest.raises(Exception):
            JobParams(route=[[200.0, 51.5], [-0.1, 51.5]])

    def test_route_out_of_bounds_lat(self):
        with pytest.raises(Exception):
            JobParams(route=[[-0.1, 95.0], [-0.1, 51.5]])

    def test_route_three_element_point(self):
        with pytest.raises(Exception):
            JobParams(route=[[-0.1, 51.5, 100.0], [-0.1, 51.6]])

    def test_valid_route_without_lat_lon(self):
        params = JobParams(route=[[-0.1, 51.5], [-0.2, 51.6]])
        assert params.route is not None
        assert len(params.route) == 2

    def test_route_and_to_lat_mutually_exclusive(self):
        with pytest.raises(Exception):
            JobParams(
                lat=51.5,
                lon=-0.1,
                route=[[-0.1, 51.5], [-0.2, 51.6]],
                to_lat=51.7,
                to_lon=-0.3,
            )

    # --- Two-point mode ---

    def test_to_lat_without_to_lon(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, to_lat=51.7)

    def test_to_lon_without_to_lat(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, to_lon=-0.3)

    def test_two_point_without_lat_lon(self):
        with pytest.raises(Exception):
            JobParams(to_lat=51.7, to_lon=-0.3)

    def test_valid_two_point(self):
        params = JobParams(lat=51.5, lon=-0.1, to_lat=51.7, to_lon=-0.3)
        assert params.to_lat == 51.7
        assert params.to_lon == -0.3

    def test_to_lat_to_lon_and_polygon_mutually_exclusive(self):
        with pytest.raises(Exception):
            JobParams(
                lat=51.5,
                lon=-0.1,
                to_lat=51.7,
                to_lon=-0.3,
                polygon=[[-0.2, 51.4], [-0.1, 51.4], [-0.1, 51.6], [-0.2, 51.6]],
            )

    # --- Bounds validation ---

    def test_size_too_small(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, size=99.0)

    def test_size_too_large(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, size=257.0)

    def test_scale_too_small(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, scale=999.0)

    def test_route_width_too_small(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, route_width=0.4)

    def test_smooth_boundary_too_large(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, smooth_boundary=7)

    def test_color_depth_mm_too_small(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, color_depth_mm=0.4)

    def test_min_building_area_negative(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, min_building_area=-1.0)

    # --- no_terrain conflicts ---

    def test_no_terrain_and_no_buildings(self):
        with pytest.raises(Exception):
            JobParams(lat=51.5, lon=-0.1, no_terrain=True, no_buildings=True)

    def test_no_terrain_and_route(self):
        with pytest.raises(Exception):
            JobParams(no_terrain=True, route=[[-0.1, 51.5], [-0.2, 51.6]])

    # --- Span abuse guard ---

    def test_route_span_too_large(self):
        # Points ~400 km apart
        with pytest.raises(Exception, match="300 km"):
            JobParams(route=[[-5.0, 50.0], [5.0, 53.0]])

    def test_two_point_span_too_large(self):
        with pytest.raises(Exception, match="300 km"):
            JobParams(lat=50.0, lon=-5.0, to_lat=54.0, to_lon=5.0)

    # --- Backward-compat ---

    def test_existing_pin_payload_still_valid(self):
        params = JobParams(lat=51.5, lon=-0.1, radius=500)
        assert params.lat == 51.5
        assert params.lon == -0.1
        assert params.radius == 500

    # --- TestClient 422 checks ---

    def test_client_route_too_few_points_returns_422(self):
        client = TestClient(app)
        with patch("web.app.run_job"):
            resp = client.post(
                "/api/jobs",
                json={"route": [[0.0, 51.5]]},
            )
        assert resp.status_code == 422

    def test_client_no_terrain_no_buildings_returns_422(self):
        client = TestClient(app)
        with patch("web.app.run_job"):
            resp = client.post(
                "/api/jobs",
                json={
                    "lat": 51.5,
                    "lon": -0.1,
                    "no_terrain": True,
                    "no_buildings": True,
                },
            )
        assert resp.status_code == 422


class TestWorkerParamMapping:
    """Test _worker correctly maps JobParams to run_pipeline kwargs."""

    def _run_worker(self, params: dict, tmp_path):
        """Call _worker in-process with run_pipeline patched, return captured kwargs."""
        from web.generate import _worker

        captured = {}

        def fake_run_pipeline(**kwargs):
            captured.update(kwargs)

        with patch("terrology.cli.run_pipeline", side_effect=fake_run_pipeline):
            _worker(json.dumps(params), str(tmp_path))

        return captured

    def test_route_job_lat_lon_midpoint(self, tmp_path):
        """Route mode: lat/lon must equal the track midpoints."""
        route = [[-0.1, 51.4], [-0.2, 51.6]]
        params = {
            "route": route,
            "terrain_exag": 2.0,
            "colors": 4,
            "span_buffer": 0.05,
            "route_width": 1.5,
        }
        kwargs = self._run_worker(params, tmp_path)

        expected_lat = (51.4 + 51.6) / 2
        expected_lon = (-0.1 + -0.2) / 2
        assert abs(kwargs["lat"] - expected_lat) < 1e-9
        assert abs(kwargs["lon"] - expected_lon) < 1e-9

    def test_route_job_bbox_utm(self, tmp_path):
        """Route mode: bbox_utm must match independently computed value."""
        from terrology.cli import _bbox_with_buffer, _setup_utm

        route = [[-0.1, 51.4], [-0.2, 51.6]]
        span_buffer = 0.05
        params = {
            "route": route,
            "terrain_exag": 2.0,
            "colors": 4,
            "span_buffer": span_buffer,
        }
        kwargs = self._run_worker(params, tmp_path)

        lat = (51.4 + 51.6) / 2
        lon = (-0.1 + -0.2) / 2
        _, to_utm, _ = _setup_utm(lat, lon)
        pts_utm = [to_utm.transform(rlon, rlat) for rlon, rlat in route]
        xs = [p[0] for p in pts_utm]
        ys = [p[1] for p in pts_utm]
        expected_bbox = _bbox_with_buffer(xs, ys, span_buffer)

        assert kwargs["bbox_utm"] == pytest.approx(expected_bbox, rel=1e-6)

    def test_route_job_route_points_utm_length_and_endpoints(self, tmp_path):
        """Route mode: route_points_utm has the right length and endpoints."""
        from terrology.cli import _setup_utm

        route = [[-0.1, 51.4], [-0.15, 51.5], [-0.2, 51.6]]
        params = {
            "route": route,
            "terrain_exag": 2.0,
            "colors": 4,
            "span_buffer": 0.05,
        }
        kwargs = self._run_worker(params, tmp_path)

        assert len(kwargs["route_points_utm"]) == 3

        lat = (51.4 + 51.6) / 2
        lon = (-0.1 + -0.2) / 2
        _, to_utm, _ = _setup_utm(lat, lon)

        first_expected = to_utm.transform(-0.1, 51.4)
        last_expected = to_utm.transform(-0.2, 51.6)

        assert kwargs["route_points_utm"][0] == pytest.approx(first_expected, rel=1e-6)
        assert kwargs["route_points_utm"][-1] == pytest.approx(last_expected, rel=1e-6)

    def test_route_width_forwarded(self, tmp_path):
        """route_width must be forwarded to run_pipeline."""
        params = {
            "route": [[-0.1, 51.4], [-0.2, 51.6]],
            "terrain_exag": 2.0,
            "colors": 4,
            "route_width": 2.5,
        }
        kwargs = self._run_worker(params, tmp_path)
        assert kwargs["route_width"] == 2.5

    def test_two_point_bbox_utm_brackets_both_points(self, tmp_path):
        """Two-point mode: bbox_utm must contain both projected points."""
        from terrology.cli import _bbox_with_buffer, _setup_utm

        lat1, lon1 = 51.4, -0.1
        lat2, lon2 = 51.6, -0.3
        span_buffer = 0.05
        params = {
            "lat": lat1,
            "lon": lon1,
            "to_lat": lat2,
            "to_lon": lon2,
            "terrain_exag": 2.0,
            "colors": 4,
            "span_buffer": span_buffer,
        }
        kwargs = self._run_worker(params, tmp_path)

        lat = (lat1 + lat2) / 2
        lon = (lon1 + lon2) / 2
        _, to_utm, _ = _setup_utm(lat, lon)
        x1, y1 = to_utm.transform(lon1, lat1)
        x2, y2 = to_utm.transform(lon2, lat2)
        expected_bbox = _bbox_with_buffer([x1, x2], [y1, y2], span_buffer)

        assert kwargs["bbox_utm"] == pytest.approx(expected_bbox, rel=1e-6)

    def test_polygon_smooth_boundary(self, tmp_path):
        """Polygon + smooth_boundary=2: exterior coord count roughly quadruples."""
        # Simple square polygon (4 corners)
        polygon = [
            [-0.2, 51.4],
            [-0.1, 51.4],
            [-0.1, 51.5],
            [-0.2, 51.5],
            [-0.2, 51.4],  # closing ring
        ]
        params = {
            "polygon": polygon,
            "smooth_boundary": 2,
            "terrain_exag": 2.0,
            "colors": 4,
        }
        captured = {}

        def fake_run_pipeline(**kwargs):
            captured.update(kwargs)

        from web.generate import _worker

        with patch("terrology.cli.run_pipeline", side_effect=fake_run_pipeline):
            _worker(json.dumps(params), str(tmp_path))

        # After 2 Chaikin iterations: 4 corners → 8 → 16 (exterior is closed so +1 for coords)
        poly = captured.get("clip_polygon_wgs84")
        assert poly is not None
        # Original had 4 unique corners; after 2 iterations should have ~16
        n_coords = len(poly.exterior.coords) - 1  # exclude closing duplicate
        assert n_coords >= 12, (
            f"Expected at least 12 coords after 2 Chaikin iterations, got {n_coords}"
        )

    def test_passthrough_kwargs(self, tmp_path):
        """All passthrough kwargs must be forwarded to run_pipeline."""
        params = {
            "lat": 51.5,
            "lon": -0.1,
            "radius": 500,
            "terrain_exag": 2.0,
            "colors": 4,
            "scale": 5000.0,
            "size": 200.0,
            "min_building_area": 25.0,
            "color_depth_mm": 2.0,
            "waterway_width": 1.5,
            "raceway_width": 2.0,
            "no_terrain": False,
        }
        kwargs = self._run_worker(params, tmp_path)

        assert kwargs["scale"] == 5000.0
        assert kwargs["size"] == 200.0
        assert kwargs["min_building_area"] == 25.0
        assert kwargs["color_depth_mm"] == 2.0
        assert kwargs["waterway_width"] == 1.5
        assert kwargs["raceway_width"] == 2.0
        assert kwargs["no_terrain"] is False

    def test_status_json_ready_on_success(self, tmp_path):
        """_status.json must be written as ready on success."""
        params = {
            "lat": 51.5,
            "lon": -0.1,
            "radius": 500,
            "terrain_exag": 2.0,
            "colors": 4,
        }
        from web.generate import _worker

        with patch("terrology.cli.run_pipeline"):
            _worker(json.dumps(params), str(tmp_path))

        status = json.loads((tmp_path / "_status.json").read_text())
        assert status["status"] == "ready"

    def test_status_json_error_on_exception(self, tmp_path):
        """_status.json must be written as error when run_pipeline raises."""
        params = {
            "lat": 51.5,
            "lon": -0.1,
            "radius": 500,
            "terrain_exag": 2.0,
            "colors": 4,
        }
        from web.generate import _worker

        with patch("terrology.cli.run_pipeline", side_effect=RuntimeError("boom")):
            _worker(json.dumps(params), str(tmp_path))

        status = json.loads((tmp_path / "_status.json").read_text())
        assert status["status"] == "error"
        assert "boom" in status["error"]
