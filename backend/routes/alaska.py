from __future__ import annotations
import math
import os
import re
import pathlib
import traceback
import urllib.parse
from typing import Any, Iterable, List, Optional, Tuple, Dict
from datetime import datetime, timezone
import requests
from fastapi import Depends
from utils.jwt_auth import require_admin, TokenData
from dateutil import parser
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from utils.jwt_auth import require_admin, TokenData
from pydantic import BaseModel, Field
import asf_search as asf
from asf_search import ASFSession
import hyp3_sdk as sdk
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

load_dotenv()

# Credenciales del .env como FALLBACK (pueden estar vacías en producción)
_ENV_ASF_USERNAME = os.getenv("ASF_USERNAME")
_ENV_ASF_PASSWORD = os.getenv("ASF_PASSWORD")
_ENV_HYP3_USERNAME = os.getenv("HYP3_USERNAME")
_ENV_HYP3_PASSWORD = os.getenv("HYP3_PASSWORD")
HYP3_PLUS_ENABLED = os.getenv("HYP3_PLUS_ENABLED")
HYP3_PLUS_URL = os.getenv("HYP3_PLUS_URL")

if not _ENV_HYP3_USERNAME or not _ENV_HYP3_PASSWORD:
    print("INFO: HYP3_USERNAME/HYP3_PASSWORD no configurados en .env — se usarán los del header por petición.")
if not _ENV_ASF_USERNAME or not _ENV_ASF_PASSWORD:
    print("INFO: ASF_USERNAME/ASF_PASSWORD no configurados en .env — se usarán los del header por petición.")
else:
    print("ASF_USERNAME detectado en .env (OK)")


def _get_hyp3_creds(
    x_hyp3_username: Optional[str] = None,
    x_hyp3_password: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Devuelve (username, password) dando prioridad a los headers del request
    y usando el .env como fallback."""
    user = x_hyp3_username or _ENV_HYP3_USERNAME
    pwd  = x_hyp3_password or _ENV_HYP3_PASSWORD
    return user, pwd


def _get_asf_creds(
    x_hyp3_username: Optional[str] = None,
    x_hyp3_password: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """ASF usa las mismas credenciales que HyP3 (cuenta EarthData unificada)."""
    user = x_hyp3_username or _ENV_ASF_USERNAME or _ENV_HYP3_USERNAME
    pwd  = x_hyp3_password or _ENV_ASF_PASSWORD or _ENV_HYP3_PASSWORD
    return user, pwd


router = APIRouter()


# Preferred default (ruta, marco) for each flight direction
# Values verified against live ASF data (pathNumber / frameNumber)
PREFERRED_PATHS: Dict[Optional[str], Tuple[int, int]] = {
    "DESCENDING": (128, 547),
    "ASCENDING":  (63, 37),   # frame 37 covers full El Salvador on asc path 63
    None: (128, 547),
}

class SearchParams(BaseModel):
    polygon: str = Field(
        default=(
            "POLYGON(("
            "-90.1684 13.0484,"
            "-87.6389 13.0484,"
            "-87.6389 14.5881,"
            "-90.1684 14.5881,"
            "-90.1684 13.0484"
            "))"
        ),
        description="WKT Polygon",
    )
    start_date: str = "2025-01-01T00:00:00Z"
    end_date: str   = "2025-01-15T23:59:59Z"
    ruta: int = 128
    marco: int = 547
    beam_mode: str = "IW"
    processing_level: str = "SLC"
    flight_direction: Optional[str] = None
    polarization: Optional[str] = None # VV, HH, VV+VH, HH+HV
    day_interval: int = 12
    same_platform: bool = True


class DiscoverPathsRequest(BaseModel):
    polygon: str
    start_date: str = "2025-01-01T00:00:00Z"
    end_date: str   = "2025-01-15T23:59:59Z"
    beam_mode: str = "IW"
    processing_level: str = "SLC"
    flight_direction: Optional[str] = None
    polarization: Optional[str] = None


class BBox(BaseModel):
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


class PathFrameOption(BaseModel):
    ruta: int
    marco: int
    scene_count: int
    bbox: BBox
    is_preferred: bool

class SceneOut(BaseModel):
    granule: str
    platform: Optional[str] = None
    date_utc: Optional[str] = None
    ruta: Optional[int] = None
    marco: Optional[int] = None
    beam_mode: Optional[str] = None
    flight_direction: Optional[str] = None
    polarization: Optional[str] = None
    download_url: Optional[str] = None

class PairOut(BaseModel):
    g1: str
    g2: str

class JobOptions(BaseModel):
    nombre_proyecto: str = "prueba_api"
    include_dem: bool = True
    include_look_vectors: bool = True
    include_displacement_maps: bool = True
    looks: str = "20x4"

class SubmitRequest(BaseModel):
    pairs: List[PairOut]
    ruta: int = 128
    marco: int = 547
    options: JobOptions = JobOptions()

class SubmitResult(BaseModel):
    index: int
    job_name: str
    job_id: Optional[str] = None
    status: str

class SubmitResponse(BaseModel):
    submitted: List[SubmitResult]
    total: int

class StatusRequest(BaseModel):
    job_ids: List[str]

class ProjectFileDownloadRequest(BaseModel):
    nombre_proyecto: str
    product_type: str = "INSAR_GAMMA"

class JobFile(BaseModel):
    file_name: str
    url: str
    size_mb: Optional[float] = None

class JobStatus(BaseModel):
    job_id: str
    job_name: Optional[str] = None
    status: str
    files: List[JobFile] = Field(default_factory=list)

class DownloadBody(BaseModel):
    file_url: str
    file_name: Optional[str] = None



class SubmitFromGranulesBody(BaseModel):
    granules: List[str]
    ruta: int
    marco: int
    day_interval: int = 12
    same_platform: bool = True
    options: JobOptions = JobOptions()



def update_project_name(new_name: str):
    JobOptions.nombre_proyecto = new_name


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
    return _get_prop(scene, "granuleName", "sceneName", "fileID", "productName", "fileName")

def get_platform(scene: Any) -> Optional[str]:
    plat = _get_prop(scene, "platform", "PLATFORM")
    if not plat:
        g = (get_granule_name(scene) or "").upper()
        if g.startswith("S1A"): return "S1A"
        if g.startswith("S1B"): return "S1B"
        return None
    return str(plat).upper()

def acquire_date(scene: Any) -> Optional[datetime]:
    candidates = ("startTime", "sceneDate", "sensingStart", "beginPosition", "acquisitionDate")
    val = _get_prop(scene, *candidates)
    if not val: return None
    try:
        dt = parser.isoparse(str(val))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        else: dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None

def get_ruta(scene: Any) -> Optional[int]:
    # ASF returns pathNumber in properties; relativeOrbit is a search param name only
    v = _get_prop(scene, "pathNumber", "relativeOrbit", "path")
    try: return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None

def get_marco(scene: Any) -> Optional[int]:
    # ASF returns frameNumber in properties; "frame" is a search param name only
    v = _get_prop(scene, "frameNumber", "frame", "FRAME")
    try: return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None

def get_download_url(scene: Any) -> Optional[str]:
    return _get_prop(scene, "url", "downloadUrl", "download_url", "fileURL", "link")

def get_size_mb_from_dict(d: Dict[str, Any]) -> Optional[float]:
    size = d.get("size")
    if not size: return None
    try:
        return round(float(size) / (1024 * 1024), 2)
    except (ValueError, TypeError):
        return None

def search_scenes(params: SearchParams) -> List[Any]:
    kwargs: Dict[str, Any] = dict(
        platform="Sentinel-1",
        processingLevel=params.processing_level,
        beamMode=params.beam_mode,
        intersectsWith=params.polygon,
        start=params.start_date,
        end=params.end_date,
        relativeOrbit=params.ruta,
        frame=params.marco,
    )
    if params.flight_direction:
        kwargs["flightDirection"] = params.flight_direction
    if params.polarization:
        kwargs["polarization"] = params.polarization
    return list(asf.search(**kwargs))




def make_asf_session(
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> ASFSession:
    s = ASFSession()
    u, p = _get_asf_creds(username, password)
    if u and p:
        s.auth = (u, p)
    return s

def ensure_dir(dir_path: pathlib.Path) -> pathlib.Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path

def human_size(nbytes: int) -> str:
    if nbytes is None:
        return "?"
    if nbytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    e = min(int(math.log(nbytes, 1024)), len(units)-1)
    return f"{nbytes/1024**e:.2f} {units[e]}"

def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', '_', name)

def download_file(url: str, dest_path: pathlib.Path, chunk=1024*1024) -> None:
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get('Content-Length', 0))
        written = 0
        with open(dest_path, 'wb') as f:
            for part in r.iter_content(chunk_size=chunk):
                if not part:
                    continue
                f.write(part)
                written += len(part)
                if total:
                    done = int(50 * written / total)
                    bar = f"[{'='*done}{'.'*(50-done)}]"
                    print(f"\r  {bar} {written/1024/1024:.1f}/{total/1024/1024:.1f} MB", end='')
        if total:
            print("\r  [==================================================] done     ")
            
def ensure_dir(dir_path: str) -> pathlib.Path:
    p = pathlib.Path(dir_path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


ASF_HOSTS = {"asf.alaska.edu", "datapool.asf.alaska.edu", "vertex.daac.asf.alaska.edu"}

def pick_filename_from_headers(resp: requests.Response) -> Optional[str]:
    cd = resp.headers.get("Content-Disposition") or resp.headers.get("content-disposition")
    if not cd: return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None

def guess_filename_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    base = pathlib.Path(path).name or "archivo.bin"
    return safe_filename(base)

def pick_session_for(url: str) -> requests.Session:
    host = urllib.parse.urlparse(url).hostname or ""
    if any(host.endswith(h) for h in ASF_HOSTS):
        return make_asf_session()
    return requests.Session()



@router.post("/api/update-project-name")
def api_update_project_name(new_name: str, current_user: TokenData = Depends(require_admin)):
    update_project_name(new_name)
    return {"ok": True, "new_name": new_name}

@router.get("/health")
def health():
    return {"ok": True, "service": "Sentinel-1 HyP3 API"}

@router.post("/api/discover_paths", response_model=List[PathFrameOption])
def api_discover_paths(params: DiscoverPathsRequest, current_user: TokenData = Depends(require_admin)):
    """Search ASF without path/frame filters and return every unique
    (ruta, marco) combination found in the results, with each combination's
    aggregate bounding box and a flag for the preferred default."""
    try:
        kwargs: Dict[str, Any] = dict(
            platform="Sentinel-1",
            processingLevel=params.processing_level,
            beamMode=params.beam_mode,
            intersectsWith=params.polygon,
            start=params.start_date,
            end=params.end_date,
        )
        if params.flight_direction:
            kwargs["flightDirection"] = params.flight_direction
        if params.polarization:
            kwargs["polarization"] = params.polarization

        results = list(asf.search(**kwargs))

        # Group by (ruta, marco)
        groups: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for scene in results:
            r = get_ruta(scene)
            m = get_marco(scene)
            if r is None or m is None:
                continue
            key = (r, m)
            # Attempt to get spatial bounds from the scene
            try:
                bb = scene.geometry  # GeoJSON-like dict or object
                if hasattr(bb, "get"):
                    coords = bb.get("coordinates", [[]])[0]
                elif hasattr(bb, "coordinates"):
                    coords = bb.coordinates[0]
                else:
                    coords = []
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
            except Exception:
                lons, lats = [], []

            if key not in groups:
                groups[key] = {"count": 0, "lons": [], "lats": []}
            groups[key]["count"] += 1
            groups[key]["lons"].extend(lons)
            groups[key]["lats"].extend(lats)

        preferred_pair = PREFERRED_PATHS.get(params.flight_direction, PREFERRED_PATHS[None])

        options: List[PathFrameOption] = []
        for (ruta_val, marco_val), data in sorted(groups.items()):
            lons = data["lons"]
            lats = data["lats"]
            if lons and lats:
                bbox = BBox(
                    lat_min=min(lats), lat_max=max(lats),
                    lon_min=min(lons), lon_max=max(lons),
                )
            else:
                # Fallback: use the search polygon's rough bbox if geometry missing
                bbox = BBox(lat_min=13.0, lat_max=14.6, lon_min=-90.2, lon_max=-87.6)

            options.append(PathFrameOption(
                ruta=ruta_val,
                marco=marco_val,
                scene_count=data["count"],
                bbox=bbox,
                is_preferred=(ruta_val, marco_val) == preferred_pair,
            ))

        return options
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en discover_paths: {e}")


@router.post("/api/search", response_model=List[SceneOut])
def api_search(params: SearchParams, current_user: TokenData = Depends(require_admin)):
    try:
        res = search_scenes(params)
        out: List[SceneOut] = []
        for r in res:
            out.append(SceneOut(
                granule=get_granule_name(r) or "<sin_nombre>",
                platform=get_platform(r),
                date_utc=(acquire_date(r).isoformat() if acquire_date(r) else None),
                ruta=get_ruta(r),
                marco=get_marco(r),
                beam_mode=params.beam_mode,
                flight_direction=params.flight_direction,
                polarization=params.polarization,
                download_url=None,
            ))
        out.sort(key=lambda x: (x.date_utc or "", x.granule))
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en búsqueda: {e}")



@router.post("/api/submit", response_model=SubmitResponse)
def api_submit(
    body: SubmitRequest,
    current_user: TokenData = Depends(require_admin),
    x_hyp3_username: Optional[str] = Header(default=None),
    x_hyp3_password: Optional[str] = Header(default=None),
):
    hyp3_user, hyp3_pass = _get_hyp3_creds(x_hyp3_username, x_hyp3_password)
    if not hyp3_user or not hyp3_pass:
        raise HTTPException(status_code=401, detail="Se requieren credenciales de HyP3. Inicia sesión primero.")
    try:
        hyp3 = sdk.HyP3(username=hyp3_user, password=hyp3_pass)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo autenticar en HyP3: {e}")

    submitted: List[SubmitResult] = []
    for idx, p in enumerate(body.pairs, start=1):
        job_name = f"{body.options.nombre_proyecto}_{body.ruta}_{body.marco}_{idx:02d}"
        try:
            job = hyp3.submit_insar_job(
                granule1=p.g1, granule2=p.g2, name=job_name,
                include_dem=body.options.include_dem,
                include_look_vectors=body.options.include_look_vectors,
                include_displacement_maps=body.options.include_displacement_maps,
                looks=body.options.looks,
            )
            job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
            submitted.append(SubmitResult(index=idx, job_name=job_name, job_id=job_id, status="submitted"))
        except Exception as e:
            submitted.append(SubmitResult(index=idx, job_name=job_name, job_id=None, status=f"error: {e}"))

    return SubmitResponse(submitted=submitted, total=len(submitted))



@router.post("/api/submit-from-granules", response_model=SubmitResponse)
def api_submit_from_granules(
    body: SubmitFromGranulesBody,
    current_user: TokenData = Depends(require_admin),
    x_hyp3_username: Optional[str] = Header(default=None),
    x_hyp3_password: Optional[str] = Header(default=None),
):
    hyp3_user, hyp3_pass = _get_hyp3_creds(x_hyp3_username, x_hyp3_password)
    if not hyp3_user or not hyp3_pass:
        raise HTTPException(status_code=401, detail="Se requieren credenciales de HyP3. Inicia sesión primero.")

    try:
        try:
            results = list(asf.search(platform="Sentinel-1", processingLevel="SLC", granule_list=body.granules))
        except TypeError:
            results = []
            for g in body.granules:
                results.extend(list(asf.search(platform="Sentinel-1", processingLevel="SLC", granule=g)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudieron leer metadatos de granules: {e}")

    valid = []
    for r in results:
        g = get_granule_name(r)
        d = acquire_date(r)
        if g and d: valid.append((r, g, d))
    valid.sort(key=lambda t: t[2])

    pairs: List[Tuple[str, str]] = []
    for i in range(len(valid)):
        for j in range(i + 1, min(i + 3, len(valid))):
            r1, g1, d1 = valid[i]
            r2, g2, d2 = valid[j]
            if body.same_platform:
                p1, p2 = get_platform(r1), get_platform(r2)
                if p1 and p2 and p1 != p2:
                    continue
            pairs.append((g1, g2))

    try:
        hyp3 = sdk.HyP3(username=hyp3_user, password=hyp3_pass)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo autenticar en HyP3: {e}")

    submitted: List[SubmitResult] = []
    for idx, (g1, g2) in enumerate(pairs, start=1):
        job_name = f"{body.options.nombre_proyecto}_{body.ruta}_{body.marco}_{idx:02d}"
        try:
            job = hyp3.submit_insar_job(
                granule1=g1, granule2=g2, name=job_name,
                include_dem=body.options.include_dem,
                include_look_vectors=body.options.include_look_vectors,
                include_displacement_maps=body.options.include_displacement_maps,
                looks=body.options.looks,
            )
            job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
            submitted.append(SubmitResult(index=idx, job_name=job_name, job_id=job_id, status="submitted"))
        except Exception as e:
            submitted.append(SubmitResult(index=idx, job_name=job_name, job_id=None, status=f"error: {e}"))
    return SubmitResponse(submitted=submitted, total=len(submitted))



@router.get("/api/projects")
def get_projects(
    current_user: TokenData = Depends(require_admin),
    x_hyp3_username: Optional[str] = Header(default=None),
    x_hyp3_password: Optional[str] = Header(default=None),
):
    hyp3_user, hyp3_pass = _get_hyp3_creds(x_hyp3_username, x_hyp3_password)
    logger.info("[get_projects] user_from_header=%s creds_ok=%s",
                x_hyp3_username, bool(hyp3_user and hyp3_pass))
    try:
        hyp3 = sdk.HyP3(username=hyp3_user, password=hyp3_pass)
        batch = hyp3.find_jobs().filter_jobs(running=False, include_expired=False, succeeded=True)

        projects = []
        for job in batch.jobs:
            if not job.name:
                continue
            m = re.match(r'^(.*?)_(\d+_\d+_\d+)$', job.name)
            project_name = m.group(1) if m else job.name

            if project_name not in [p['name'] for p in projects]:
                projects.append({"id": job.job_id, "name": project_name})

        return projects
    except Exception as e:
        logger.error("[get_projects] ERROR: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error al obtener proyectos: {str(e)}")



@router.post("/api/project-files", response_model=List[JobFile]) # usado
def get_project_files(
    body: ProjectFileDownloadRequest,
    current_user: TokenData = Depends(require_admin),
    x_hyp3_username: Optional[str] = Header(default=None),
    x_hyp3_password: Optional[str] = Header(default=None),
):
    nombre_proyecto = body.nombre_proyecto
    product_type = body.product_type
    hyp3_user, hyp3_pass = _get_asf_creds(x_hyp3_username, x_hyp3_password)

    if not nombre_proyecto:
        raise HTTPException(status_code=400, detail="El nombre del proyecto es obligatorio")

    try:
        hyp3 = sdk.HyP3(username=hyp3_user, password=hyp3_pass)

        import re
        batch = (
            hyp3.find_jobs(job_type=product_type)
                .filter_jobs(running=False, include_expired=False, succeeded=True)
        )

        filtered_jobs = []
        for job in batch.jobs:
            if not job.name:
                continue
            m = re.match(r'^(.*?)_\d+_\d+_\d+$', job.name)
            base_name = m.group(1) if m else job.name
            if base_name == nombre_proyecto:
                filtered_jobs.append(job)

        if not filtered_jobs:
            raise HTTPException(status_code=404, detail="No se encontraron trabajos disponibles para este proyecto")

        files = []
        index = 1
        for job in filtered_jobs:
            job_files = job.files or []
            for f in job_files:
                name = f.get('name') or f.get('filename') or f.get('key') or 'archivo_sin_nombre'
                url = f.get('url')
                size = f.get('size')
                
                if url:
                    files.append({
                        'file_name': name,
                        'url': url,
                        'size_mb': get_size_mb_from_dict(f),
                    })
                    index += 1
        
        if not files:
            raise HTTPException(status_code=404, detail="No hay archivos disponibles para este proyecto")

        return files

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener los archivos del proyecto: {str(e)}")


@router.post("/api/download")
def api_download(body: DownloadBody, current_user: TokenData = Depends(require_admin)):
    try:
        downloads_dir = ensure_dir(pathlib.Path(__file__).resolve().parent.parent / "alaska_descargas")
        sess = requests.Session()
        with sess.get(body.file_url, stream=True, allow_redirects=True, timeout=120) as r:
            if r.status_code in (401, 403):
                raise HTTPException(status_code=403, detail="Permisos EDL/URS requeridos")
            r.raise_for_status()
            fname = (body.file_name or "").strip() or safe_filename(body.file_name)
            dst = downloads_dir / fname
            download_file(body.file_url, dst)
        return {"ok": True, "saved_to": str(dst), "bytes": dst.stat().st_size, "filename": fname}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al descargar: {e}")



@router.get("/api/check-edl")
def api_check_edl(test_url: str = Query("https://datapool.asf.alaska.edu/")):
    try:
        sess = requests.Session()
        r = sess.head(test_url, allow_redirects=True, timeout=30)
        if r.status_code in (200, 301, 302, 303, 307, 308):
            return {"ok": True, "status": r.status_code, "url": r.url}
        if r.status_code in (401, 403):
            return {"ok": False, "status": r.status_code, "detail": "Falta login/permiso en URS/EDL"}
        return {"ok": False, "status": r.status_code, "detail": "Respuesta inesperada"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error check-edl: {e}")
