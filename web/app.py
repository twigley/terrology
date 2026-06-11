from __future__ import annotations

import asyncio
import io
import math
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


def _rate_limit_key(request: Request) -> str:
    """
    Rate limiting key: IP address for per-user quotas.
    Honours X-Forwarded-For when TERROLOGY_TRUST_PROXY=1 (for reverse proxies).
    By default, trusts only direct peer IP to prevent spoofing.
    """
    if os.getenv("TERROLOGY_TRUST_PROXY") == "1":
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            # X-Forwarded-For may be a comma-separated list; take the first (originating client)
            return xff.split(",")[0].strip()
    return get_remote_address(request)


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        try:
            store.cleanup_expired()
        except Exception as e:
            print(f"Error in cleanup loop: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


_RATE_LIMIT = "5/hour" if not os.getenv("TERROLOGY_NO_RATE_LIMIT") else "10000/hour"
_MAX_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))

limiter = Limiter(key_func=_rate_limit_key)
app = FastAPI(title="Terrology", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_MAX_ROUTE_SPAN_KM = 300.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in km between two WGS84 points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


class JobParams(BaseModel):
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    # Upper bound: 800 colour-grid cells × 10 m major-road half-width = 8000 m,
    # the radius at which even primary roads (20 m wide) are exactly one colour cell.
    # Beyond this only area features (water, parks) and terrain shape are visible.
    radius: float = Field(500, ge=100, le=8000)
    shape: str = Field("square", pattern="^(square|circle|hexagon)$")
    polygon: list[list[float]] | None = None  # [[lng, lat], ...] GeoJSON ring
    terrain_exag: float = Field(2.0, ge=1.0, le=4.0)
    colors: int = Field(4, ge=1, le=7)
    no_buildings: bool = False
    roof_shapes: bool = False
    raceway: bool = False
    waterways: bool = False
    contour_interval: float | None = Field(None, ge=1)
    border_width_mm: float = Field(0.0, ge=0.0, le=20.0)
    water_depth_mm: float = Field(0.8, ge=0.0, le=5.0)
    building_exag: float | None = Field(None, ge=0.5, le=5.0)
    dem_source: str = Field("glo30", pattern="^(glo30|srtm|aw3d30)$")

    # New fields
    route: list[list[float]] | None = None  # [[lon, lat], ...] client-parsed GPX
    route_width: float = Field(1.5, ge=0.5, le=5.0)
    to_lat: float | None = Field(None, ge=-90, le=90)
    to_lon: float | None = Field(None, ge=-180, le=180)
    span_buffer: float = Field(0.05, ge=0.0, le=0.5)
    size: float = Field(190.0, ge=100.0, le=256.0)
    scale: float | None = Field(None, ge=1000.0, le=250000.0)
    raceway_width: float = Field(1.5, ge=0.5, le=5.0)
    waterway_width: float = Field(1.0, ge=0.2, le=5.0)
    color_depth_mm: float = Field(1.5, ge=0.5, le=3.0)
    min_building_area: float | None = Field(None, ge=0.0, le=500.0)  # None = auto
    smooth_boundary: int = Field(0, ge=0, le=6)
    no_terrain: bool = False

    @model_validator(mode="after")
    def check_location(self):
        # --- Mutual exclusions ---
        if self.route is not None and (
            self.to_lat is not None or self.to_lon is not None
        ):
            raise ValueError("route and to_lat/to_lon are mutually exclusive")

        if (
            self.to_lat is not None or self.to_lon is not None
        ) and self.polygon is not None:
            raise ValueError("to_lat/to_lon and polygon are mutually exclusive")

        if (self.to_lat is None) != (self.to_lon is None):
            raise ValueError("to_lat and to_lon must both be present or both absent")

        # Two-point mode requires a starting lat+lon
        if self.to_lat is not None and (self.lat is None or self.lon is None):
            raise ValueError("to_lat/to_lon (two-point mode) requires lat and lon")

        # --- no_terrain conflicts ---
        if self.no_terrain and self.no_buildings:
            raise ValueError(
                "no_terrain and no_buildings together produce an empty model"
            )
        if self.no_terrain and self.route is not None:
            raise ValueError(
                "no_terrain and route are incompatible (route is painted onto the terrain surface)"
            )

        # --- Validate route points ---
        if self.route is not None:
            if len(self.route) < 2:
                raise ValueError("route must have at least 2 points")
            if len(self.route) > 5000:
                raise ValueError("route must have at most 5000 points")
            for pt in self.route:
                if len(pt) != 2:
                    raise ValueError(
                        "Each route point must have exactly 2 elements [lon, lat]"
                    )
                lon_, lat_ = pt
                if not (-180 <= lon_ <= 180):
                    raise ValueError(
                        f"Route point longitude {lon_} out of range [-180, 180]"
                    )
                if not (-90 <= lat_ <= 90):
                    raise ValueError(
                        f"Route point latitude {lat_} out of range [-90, 90]"
                    )

            # Span check: approximate bounding box diagonal
            lats = [pt[1] for pt in self.route]
            lons = [pt[0] for pt in self.route]
            span_km = _haversine_km(min(lats), min(lons), max(lats), max(lons))
            if span_km > _MAX_ROUTE_SPAN_KM:
                raise ValueError(
                    f"Route spans approximately {span_km:.0f} km, which exceeds the "
                    f"{_MAX_ROUTE_SPAN_KM:.0f} km limit for free hosting"
                )

        # --- Two-point span check ---
        if self.to_lat is not None and self.lat is not None and self.lon is not None:
            span_km = _haversine_km(self.lat, self.lon, self.to_lat, self.to_lon)
            if span_km > _MAX_ROUTE_SPAN_KM:
                raise ValueError(
                    f"Two-point span is approximately {span_km:.0f} km, which exceeds the "
                    f"{_MAX_ROUTE_SPAN_KM:.0f} km limit for free hosting"
                )

        # --- Location requirement ---
        has_location = (
            self.polygon is not None
            or self.route is not None
            or (self.lat is not None and self.lon is not None)
        )
        if not has_location:
            raise ValueError("Provide either polygon, route, or lat+lon")

        # --- Polygon validation ---
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
    job_id = str(uuid.uuid4())
    job = store.try_create(job_id, _MAX_JOBS)
    if job is None:
        raise HTTPException(status_code=503, detail="Server busy – try again shortly")
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
