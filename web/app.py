from __future__ import annotations

import asyncio
import io
import os
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from web.generate import run_job
from web.jobs import JobStatus, store

_STATIC = Path(__file__).parent / "static"


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        store.cleanup_expired()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


_RATE_LIMIT = "5/hour" if not os.getenv("TERROLOGY_NO_RATE_LIMIT") else "10000/hour"
_MAX_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Terrology", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class JobParams(BaseModel):
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    radius: float = Field(500, ge=100, le=5000)
    shape: str = Field("square", pattern="^(square|circle|hexagon)$")
    polygon: list[list[float]] | None = None  # [[lng, lat], ...] GeoJSON ring
    terrain_exag: float = Field(2.0, ge=1.0, le=4.0)
    colors: int = Field(4, ge=1, le=7)
    no_buildings: bool = False
    roof_shapes: bool = False
    raceway: bool = False
    contour_interval: float | None = Field(None, ge=1)
    border_width_mm: float = Field(0.0, ge=0.0, le=20.0)
    water_depth_mm: float = Field(0.8, ge=0.0, le=5.0)
    building_exag: float | None = Field(None, ge=0.5, le=5.0)
    dem_source: str = Field("glo30", pattern="^(glo30|srtm|aw3d30)$")

    @model_validator(mode="after")
    def check_location(self):
        if self.polygon is None and (self.lat is None or self.lon is None):
            raise ValueError("Provide either polygon or lat+lon")
        if self.polygon is not None and len(self.polygon) < 3:
            raise ValueError("Polygon must have at least 3 points")
        return self


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/dem-sources")
async def dem_sources():
    from terrology.fetcher import DEM_SOURCES, ot_key_configured

    key_ok = ot_key_configured()
    return {
        "key_configured": key_ok,
        "sources": [
            {"id": s, "available": s == "glo30" or key_ok} for s in DEM_SOURCES
        ],
    }


@app.get("/")
async def index():
    return FileResponse(_STATIC / "index.html")


@app.post("/api/jobs")
@limiter.limit(_RATE_LIMIT)
async def create_job(
    request: Request,
    params: JobParams,
    background_tasks: BackgroundTasks,
):
    if store.running_count() >= _MAX_JOBS:
        raise HTTPException(status_code=503, detail="Server busy – try again shortly")
    job_id = str(uuid.uuid4())
    store.create(job_id)
    background_tasks.add_task(run_job, job_id, params.model_dump())
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.as_response()


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str, background_tasks: BackgroundTasks):
    job = store.get(job_id)
    if job is None or job.status != JobStatus.READY:
        raise HTTPException(status_code=404, detail="Job not ready or not found")

    out_dir = job.output_dir
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fname in ["model.obj", "model.mtl", "model.3mf"]:
            p = out_dir / fname
            if p.exists():
                zf.write(p, fname)
    buf.seek(0)

    background_tasks.add_task(store.delete, job_id)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=terrology_{job_id[:8]}.zip"
        },
    )


@app.get("/api/jobs/{job_id}/files/{filename}")
async def get_job_file(job_id: str, filename: str):
    if filename not in ("model.obj", "model.mtl"):
        raise HTTPException(status_code=404, detail="File not found")
    job = store.get(job_id)
    if job is None or job.status != JobStatus.READY:
        raise HTTPException(status_code=404, detail="Job not ready or not found")
    p = job.output_dir / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(p)


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
