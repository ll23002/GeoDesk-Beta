import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tasks.processing_tasks import (
    run_mintpy_pipeline,
    run_adhoc_analysis,
    run_monthly_ingest,
)
from utils.disk_guard import (
    DiskSpaceError,
    check_minimum_free,
    check_space_for_images,
    get_disk_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])

BASE_DIR  = Path(__file__).resolve().parent.parent
JOBS_DIR  = BASE_DIR / "job_status"
JOBS_DIR.mkdir(exist_ok=True)

TMP_SESSIONS = Path("/tmp/geodesk_adhoc_sessions")
TMP_SESSIONS.mkdir(parents=True, exist_ok=True)


class MintpyRunRequest(BaseModel):
    session_id: Optional[str] = None
    n_images: int = 0
    flight_direction: str = "DESCENDING"
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class AdhocRunRequest(BaseModel):
    n_images: int = 0
    flight_direction: str = "DESCENDING"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    polygon: Optional[str] = None


def _read_job_status(job_id: str) -> dict:
    """Lee el archivo JSON de estado de una tarea. Lanza 404 si no existe."""
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Tarea '{job_id}' no encontrada. Verifique el job_id.",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error leyendo estado de la tarea: {exc}",
        )


def _write_initial_status(job_id: str, task_type: str) -> None:
    """Escribe el estado inicial 'queued' antes de despachar a Celery."""
    payload = {
        "job_id":     job_id,
        "type":       task_type,
        "status":     "queued",
        "step":       "Tarea en cola, esperando worker disponible",
        "progress":   0,
        "queued_at":  datetime.utcnow().isoformat() + "Z",
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "error":      None,
        "result":     None,
    }
    path = JOBS_DIR / f"{job_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")




@router.post("/mintpy/run", status_code=202)
def enqueue_mintpy_pipeline(body: MintpyRunRequest): # falta JWT auth
    """
    Retorna HTTP 202 Accepted inmediatamente con el job_id.
    El frontend guarda el job_id en localStorage y consulta /status cada 10 s.
    """

    try:
        if body.n_images > 0:
            check_space_for_images(body.n_images)
        else:
            check_minimum_free() # por qué?
    except DiskSpaceError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict())

    job_id     = f"mintpy_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    session_id = body.session_id or f"session_{uuid.uuid4().hex[:8]}"


    _write_initial_status(job_id, "mintpy")

    run_mintpy_pipeline.apply_async(
        kwargs={
            "job_id":     job_id,
            "session_id": session_id,
            "config":     body.model_dump(),
        },
        queue="geodesk_heavy",
    )

    logger.info("Tarea MintPy encolada: job_id=%s session_id=%s", job_id, session_id)

    return {
        "ok":         True,
        "message":    "Pipeline MintPy iniciada. Consulte /api/jobs/{job_id}/status para seguir el progreso.",
        "job_id":     job_id,
        "session_id": session_id,
    }


@router.post("/adhoc/run", status_code=202)
def enqueue_adhoc_analysis(body: AdhocRunRequest): # falta JWT auth
    """
    Encola un análisis InSAR ad-hoc en un sandbox temporal aislado.
    Los resultados se empaquetan en un .zip descargable por el usuario.
    """
    
    try:
        if body.n_images > 0:
            check_space_for_images(body.n_images)
        else:
            check_minimum_free()
    except DiskSpaceError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict())

    job_id     = f"adhoc_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    session_id = f"adhoc_{uuid.uuid4().hex[:10]}"


    sandbox_dir = TMP_SESSIONS / session_id
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    _write_initial_status(job_id, "adhoc_analysis")

    run_adhoc_analysis.apply_async(
        kwargs={
            "job_id":     job_id,
            "session_id": session_id,
            "config":     body.model_dump(),
        },
        queue="geodesk_heavy",
    )

    logger.info("Tarea Ad-Hoc encolada: job_id=%s session_id=%s", job_id, session_id)

    return {
        "ok":         True,
        "message":    "Análisis ad-hoc iniciado. Los resultados serán empaquetados en un .zip descargable.",
        "job_id":     job_id,
        "session_id": session_id,
    }


@router.get("/{job_id}/status")
def get_job_status(job_id: str):
    """
    Retorna el estado actual de una tarea por su job_id.
    """
    return _read_job_status(job_id)


@router.get("/disk/status")
def disk_status():
    """
    Retorna el estado actual del disco del servidor en gigabytes.
    """
    status = get_disk_status()
    return {"ok": True, "disk": status.to_dict()}


@router.post("/adhoc/{session_id}/cleanup")
def cleanup_adhoc_session(session_id: str):
    """
    Elimina manualmente el sandbox de una sesión ad-hoc y libera el espacio
    del servidor.
    """
    sandbox_dir = TMP_SESSIONS / session_id
    if not sandbox_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Sesión '{session_id}' no encontrada o ya fue eliminada.",
        )
    try:
        shutil.rmtree(sandbox_dir, ignore_errors=False)
        logger.info("Sandbox ad-hoc eliminado manualmente: %s", sandbox_dir)
        return {
            "ok":      True,
            "message": f"Sesión '{session_id}' eliminada correctamente. Espacio liberado.",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error eliminando sesión: {exc}",
        )
