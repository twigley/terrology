from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from web.app import _rate_limit_key, app
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
