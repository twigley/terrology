from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from web.jobs import JobStatus, store


def run_job(job_id: str, params: dict) -> None:
    """Called by FastAPI BackgroundTasks; runs run_pipeline and updates the job store."""
    from main import run_pipeline

    out_dir = Path("/tmp/terrology") / job_id
    store.update(
        job_id,
        status=JobStatus.RUNNING,
        started_at=datetime.now(tz=UTC),
        output_dir=out_dir,
    )
    try:
        run_pipeline(
            lat=params["lat"],
            lon=params["lon"],
            radius=params["radius"],
            terrain_exag=params["terrain_exag"],
            colors=params["colors"],
            no_buildings=params.get("no_buildings", False),
            roof_shapes=params.get("roof_shapes", False),
            contour_interval=params.get("contour_interval"),
            output_dir=out_dir,
            color_grid_size=600,
            skip_stls=True,
        )
        store.update(job_id, status=JobStatus.READY)
    except Exception as exc:
        store.update(job_id, status=JobStatus.ERROR, error=str(exc))
