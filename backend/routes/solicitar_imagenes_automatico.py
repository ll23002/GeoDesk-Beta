from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple, Iterable  # Importa Iterable
from datetime import datetime, timezone
from fastapi import APIRouter, Header
import os
import re
import logging

logger = logging.getLogger(__name__)

from dateutil import parser
from dotenv import load_dotenv
import asf_search as asf
import hyp3_sdk as sdk

load_dotenv()

router = APIRouter()

# Credenciales del .env como FALLBACK
_ENV_USERNAME = os.getenv("HYP3_USERNAME")
_ENV_PASSWORD = os.getenv("HYP3_PASSWORD")

NOMBRE_PROYECTO_POR_DEFECTO = "prueba_api_2023_01"

CARPETA_BASE_SALIDA = "."


POLYGON = (
    "POLYGON(("
    "-90.1684 13.0484,"
    "-87.6389 13.0484,"
    "-87.6389 14.5881,"
    "-90.1684 14.5881,"
    "-90.1684 13.0484"
    "))"
)

RUTA_DEFAULT_DESCENDING = 128
MARCO_DEFAULT_DESCENDING = 547
RUTA_DEFAULT_ASCENDING = 63
MARCO_DEFAULT_ASCENDING = 37   # frame 37 covers full El Salvador on asc path 63

BEAM_MODE = "IW"
PROC_LEVEL = "SLC"
PLATFORM = "Sentinel-1"

DAY_INTERVAL = 12
INCLUDE_DEM = True
INCLUDE_LOOK_VECTORS = True
LOOKS = "20x4"


@dataclass
class SolicitudAutoIn:
    start_date: str   # ISO-8601
    end_date: str     # ISO-8601
    project_name: Optional[str] = None
    output_folder: Optional[str] = None
    flight_direction: Optional[str] = "DESCENDING"
    day_interval: int = 12
    ruta: Optional[int] = None         # Set by frontend via map selection
    marco: Optional[int] = None        # Set by frontend via map selection
    polygon: Optional[str] = None       # AOI from map; falls back to El Salvador bbox


@dataclass
class TimeWindow:
    start: str
    end: str


@dataclass
class SceneInfo:
    granule: Optional[str]
    platform: Optional[str]
    acquire_utc: Optional[str]


@dataclass
class JobSummary:
    job_id: Optional[str]
    name: str
    granule1: str
    granule2: str
    status: Optional[str] = None


#  UTILIDADES GENERALES
def _parse_iso_utc(value: str) -> datetime:
    dt = parser.isoparse(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_prop(scene: Any, *keys: str) -> Optional[Any]:
    for k in keys:
        if hasattr(scene, k):
            v = getattr(scene, k)
            if v not in (None, ""):
                return v
        if hasattr(scene, "properties") and isinstance(scene.properties, dict):
            if k in scene.properties and scene.properties[k] not in (None, ""):
                return scene.properties[k]
    return None


def get_granule_name(scene: Any) -> Optional[str]:
    return _get_prop(scene, "granuleName", "sceneName", "fileID", "productName")


def get_platform(scene: Any) -> Optional[str]:
    plat = _get_prop(scene, "platform", "PLATFORM")
    if not plat:
        g = get_granule_name(scene) or ""
        if g.startswith("S1A"):
            return "S1A"
        if g.startswith("S1B"):
            return "S1B"
        return None
    plat = str(plat).upper()
    if "S1A" in plat:
        return "S1A"
    if "S1B" in plat:
        return "S1B"
    return None


def acquire_date(scene: Any) -> Optional[datetime]:
    candidates = ("startTime", "sceneDate", "sensingStart", "beginPosition", "acquisitionDate")
    val = _get_prop(scene, *candidates)
    if not val:
        return None
    try:
        return _parse_iso_utc(str(val))
    except (TypeError, ValueError):
        return None


#  UTILIDADES PROYECTO
def _sanear_nombre_proyecto(raw: Optional[str]) -> str:
    if not raw or not raw.strip():
        return NOMBRE_PROYECTO_POR_DEFECTO

    name = raw.strip()
    return name


def _ruta_carpeta_salida(nombre_proyecto: str, base: Optional[str] = None) -> str:
    base_dir = base or CARPETA_BASE_SALIDA
    return os.path.join(base_dir, nombre_proyecto)


def _validar_nombre_proyecto_unico(
    nombre_proyecto: str,
    base: Optional[str] = None,
) -> Optional[str]:
    carpeta = _ruta_carpeta_salida(nombre_proyecto, base=base)
    if os.path.exists(carpeta):
        return (
            f"Ya existe una carpeta/proyecto llamada '{nombre_proyecto}'. "
            "Por favor elige otro nombre para evitar sobreescribir datos."
        )
    return None


#  LÓGICA PRINCIPAL
def search_scenes(start_iso: str, end_iso: str, ruta: int = 128, marco: int = 547, direction: Optional[str] = None) -> List[Any]:
    kwargs = {
        "platform": PLATFORM,
        "processingLevel": PROC_LEVEL,
        "beamMode": BEAM_MODE,
        "intersectsWith": POLYGON,
        "start": start_iso,
        "end": end_iso,
        "relativeOrbit": ruta,
        "frame": marco
    }
    if direction:
        kwargs["flightDirection"] = direction
    results = asf.search(**kwargs)
    return list(results)


def build_pairs(results: Iterable[Any], day_interval: int) -> List[Tuple[str, str]]:
    valid: List[Tuple[Any, str, datetime]] = []
    for r in results:
        g = get_granule_name(r)
        d = acquire_date(r)
        if g and d:
            valid.append((r, g, d))

    if not valid:
        return []

    valid.sort(key=lambda t: t[2])

    pairs: List[Tuple[str, str]] = []
    for i in range(len(valid)):
        for j in range(i + 1, min(i + 3, len(valid))):
            r1, g1, d1 = valid[i]
            r2, g2, d2 = valid[j]
            plat1 = get_platform(r1)
            plat2 = get_platform(r2)
            if plat1 and plat2 and plat1 != plat2:
                continue
            pairs.append((g1, g2))

    return pairs


def submit_jobs(
    pairs: List[Tuple[str, str]],
    project_name: str,
    ruta: int = 128,
    marco: int = 547,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> List[JobSummary]:
    if not pairs:
        return []

    # Prioridad: parámetros > .env
    usr = username or _ENV_USERNAME
    pwd = password or _ENV_PASSWORD
    hyp3 = sdk.HyP3(username=usr, password=pwd)
    summaries: List[JobSummary] = []

    for idx, (g1, g2) in enumerate(pairs, 1):
        job_name = f"{project_name}_{ruta}_{marco}_{idx:02d}"
        try:
            batch = hyp3.submit_insar_job(
                granule1=g1,
                granule2=g2,
                name=job_name,
                include_dem=INCLUDE_DEM,
                include_look_vectors=INCLUDE_LOOK_VECTORS,
                include_displacement_maps=True,
                looks=LOOKS,
            )
            job = batch[0] if len(batch) > 0 else None
            job_id = getattr(job, "job_id", None) if job else None
            status = getattr(job, "status_code", None) or getattr(job, "status", None) if job else None
            summaries.append(JobSummary(
                job_id=str(job_id) if job_id else None,
                name=job_name,
                granule1=g1,
                granule2=g2,
                status=str(status) if status else None
            ))
        except Exception as e:
            logger.error("[submit_jobs] ERROR enviando job %s (g1=%s, g2=%s): %s", job_name, g1, g2, e, exc_info=True)
            print(f"[submit_jobs] ERROR job {job_name}: {type(e).__name__}: {e}", flush=True)
            summaries.append(JobSummary(
                job_id=None,
                name=job_name,
                granule1=g1,
                granule2=g2,
                status=f"ERROR: {type(e).__name__}: {e}"
            ))
    return summaries


@router.post("/api/solicitar_imagenes")
def solicitar_imagenes_automatico(
    payload: SolicitudAutoIn,
    x_hyp3_username: Optional[str] = Header(default=None),
    x_hyp3_password: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    project_name = _sanear_nombre_proyecto(payload.project_name)
    base_dir = payload.output_folder or CARPETA_BASE_SALIDA
    carpeta_salida = _ruta_carpeta_salida(project_name, base=base_dir)

    error_nombre = _validar_nombre_proyecto_unico(project_name, base=base_dir)
    if error_nombre:
        return {
            "project_input": payload.project_name,
            "project": project_name,
            "time_window": None,
            "aoi": None,
            "insar_options": None,
            "summary": {
                "found_scenes": 0,
                "built_pairs": 0,
                "submitted_jobs": 0,
                "output_folder_hint": carpeta_salida,
            },
            "scenes": [],
            "pairs": [],
            "jobs": [],
            "error": {
                "code": "PROJECT_NAME_ALREADY_EXISTS",
                "message": error_nombre,
            },
        }

    start_dt = _parse_iso_utc(payload.start_date)
    end_dt = _parse_iso_utc(payload.end_date)

    start_iso = _iso_utc(start_dt)
    end_iso = _iso_utc(end_dt)
    
    ruta_val = payload.ruta
    marco_val = payload.marco

    # Fall back to preferred defaults if not provided by frontend
    if ruta_val is None or marco_val is None:
        if payload.flight_direction == "ASCENDING":
            ruta_val = RUTA_DEFAULT_ASCENDING
            marco_val = MARCO_DEFAULT_ASCENDING
        else:
            ruta_val = RUTA_DEFAULT_DESCENDING
            marco_val = MARCO_DEFAULT_DESCENDING

    aoi_polygon = payload.polygon or POLYGON

    results = search_scenes(start_iso, end_iso, ruta=ruta_val, marco=marco_val, direction=payload.flight_direction)

    escenas: List[SceneInfo] = []
    for r in results:
        acq_dt = acquire_date(r)
        escenas.append(SceneInfo(
            granule=get_granule_name(r),
            platform=get_platform(r),
            acquire_utc=_iso_utc(acq_dt) if acq_dt else None
        ))

    pairs = build_pairs(results, payload.day_interval)

    jobs = submit_jobs(
        pairs,
        project_name=project_name,
        ruta=ruta_val,
        marco=marco_val,
        username=x_hyp3_username,
        password=x_hyp3_password,
    )

    response: Dict[str, Any] = {
        "project_input": payload.project_name,
        "project": project_name,
        "time_window": asdict(TimeWindow(start=start_iso, end=end_iso)),
        "aoi": {
            "polygon_wkt": aoi_polygon,
            "relative_orbit": ruta_val,
            "frame": marco_val,
            "beam_mode": BEAM_MODE,
            "processing_level": PROC_LEVEL,
            "platform": PLATFORM,
            "flight_direction": payload.flight_direction
        },
        "insar_options": {
            "day_interval": payload.day_interval,
            "include_dem": INCLUDE_DEM,
            "include_look_vectors": INCLUDE_LOOK_VECTORS,
            "looks": LOOKS,
        },
        "summary": {
            "found_scenes": len(results),
            "built_pairs": len(pairs),
            "submitted_jobs": len([j for j in jobs if j.job_id]),
            "failed_jobs": len([j for j in jobs if j.job_id is None]),
            "job_errors": list({
                j.status for j in jobs if j.job_id is None and j.status
            }),
            "output_folder_hint": carpeta_salida,
        },
        "scenes": [asdict(s) for s in escenas],
        "pairs": [{"granule1": g1, "granule2": g2} for (g1, g2) in pairs],
        "jobs": [asdict(j) for j in jobs],
        "error": None,
    }
    return response


# Ejecución directa
if __name__ == "__main__":
    ejemplo = SolicitudAutoIn(
        start_date="2024-02-01T00:00:00Z",
        end_date="2024-03-15T23:59:59Z",
        project_name="mi_proyecto_insar_prueba",
        output_folder=".",
    )
    out = solicitar_imagenes_automatico(ejemplo)
    print(
        f"[{out['project']}] escenas={out['summary']['found_scenes']}, "
        f"pares={out['summary']['built_pairs']}, jobs_ok={out['summary']['submitted_jobs']}, "
        f"error={out['error']}"
    )
