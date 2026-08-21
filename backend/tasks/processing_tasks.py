import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from celery import Task

from celery_app import celery_app
from utils.disk_guard import (
    DiskSpaceError,
    check_minimum_free,
    check_space_for_images,
)

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).resolve().parent.parent
JOBS_DIR    = BASE_DIR / "job_status"
JOBS_DIR.mkdir(exist_ok=True)

RESULTS_DIR = BASE_DIR / "mintpy_results"
RESULTS_DIR.mkdir(exist_ok=True)

TMP_SESSIONS = Path("/tmp/geodesk_adhoc_sessions")
TMP_SESSIONS.mkdir(parents=True, exist_ok=True)


def _status_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _write_status(
    job_id: str,
    status: str,
    step: str = "",
    progress: int = 0,
    error: Optional[str] = None,
    result: Optional[dict] = None,
) -> None:
    """
    Escribe el estado actual de la tarea en un archivo JSON liviano
    """
    payload = {
        "job_id":      job_id,
        "status":      status,   # queued | started | progress | completed | failed
        "step":        step,     # Descripción legible del paso actual
        "progress":    progress, # 0-100
        "updated_at":  datetime.utcnow().isoformat() + "Z",
        "error":       error,
        "result":      result,
    }
    path = _status_path(job_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[Job %s] %s — %s (%d%%)", job_id, status, step, progress)


@celery_app.task(
    bind=True,
    name="tasks.processing_tasks.run_mintpy_pipeline",
    max_retries=0,       # Sin reintentos automáticos (el trabajo puede tardar 30 h+)
    time_limit=None,     # Sin límite de tiempo — Systemd garantiza la resiliencia
    soft_time_limit=None,
    queue="geodesk_heavy",
)
def run_mintpy_pipeline(
    self: Task,
    job_id: str,
    session_id: str,
    config: dict,
) -> dict:
    """
    Ejecuta el procesamiento completo InSAR + MintPy + ERA5 para la pila de volcanes.

    Args:
        job_id:     Identificador único de la tarea (generado por la API al encolar).
        session_id: ID de sesión del directorio temporal de trabajo.
        config:     Diccionario con los parámetros del análisis (fechas, órbita, etc.).

    Returns:
        dict con el resumen del resultado al finalizar.
    """
    _write_status(job_id, "started", "Iniciando pipeline de procesamiento", 1)

    try:
        _write_status(job_id, "progress", "Verificando espacio en disco", 2)
        n_images = config.get("n_images", 0)
        if n_images > 0:
            check_space_for_images(n_images)
        else:
            check_minimum_free()


        _write_status(job_id, "progress", "Preparando directorio de trabajo", 5)
        from pathlib import Path as _Path
        session_base = _Path("/tmp/mintpy_sessions")
        session_base.mkdir(parents=True, exist_ok=True)
        work_dir = session_base / session_id
        work_dir.mkdir(parents=True, exist_ok=True)


        _write_status(job_id, "progress", "Solicitando interferogramas a ASF HyP3", 10)
        # La lógica de descarga se delega al servicio existente (routes/alaska.py)
        logger.info("[Job %s] HyP3: Lote solicitado para config=%s", job_id, config)
        time.sleep(1)

        _write_status(job_id, "progress", "Descargando interferogramas procesados de HyP3", 25)
        logger.info("[Job %s] Descargando paquetes HyP3...", job_id)
        time.sleep(1)

        _write_status(job_id, "progress", "Ejecutando inversión SBAS con MintPy (WLS)", 45)
        logger.info("[Job %s] MintPy: iniciando análisis SBAS...", job_id)
        # Aquí se llamará a TimeSeriesAnalysis de MintPy (ya presente en el código actual)
        time.sleep(1)

        _write_status(job_id, "progress", "Aplicando corrección atmosférica PyAPS + ERA5", 65)
        logger.info("[Job %s] PyAPS: corrección troposférica ERA5...", job_id)
        time.sleep(1)

        _write_status(job_id, "progress", "Generando GeoTIFFs y series temporales Parquet", 80)
        logger.info("[Job %s] Generando productos finales...", job_id)
        time.sleep(1)

        _write_status(job_id, "progress", "Actualizando perfiles de los 5 volcanes", 92)
        logger.info("[Job %s] Actualizando series de volcanes...", job_id)
        time.sleep(1)

        result_summary = {
            "job_id":       job_id,
            "session_id":   session_id,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
        _write_status(
            job_id, "completed",
            "Procesamiento finalizado correctamente", 100,
            result=result_summary,
        )
        return result_summary

    except DiskSpaceError as exc:
        logger.error("[Job %s] BLOQUEADO por Disk Guardrail: %s", job_id, exc)
        _write_status(
            job_id, "failed",
            "Bloqueado por falta de almacenamiento", 0,
            error=str(exc),
        )
        raise

    except Exception as exc:
        logger.exception("[Job %s] Error inesperado en el procesamiento: %s", job_id, exc)
        _write_status(
            job_id, "failed",
            f"Error inesperado: {type(exc).__name__}", 0,
            error=str(exc),
        )
        raise



@celery_app.task(
    bind=True,
    name="tasks.processing_tasks.run_adhoc_analysis",
    max_retries=0,
    time_limit=None,
    queue="geodesk_heavy",
)
def run_adhoc_analysis(
    self: Task,
    job_id: str,
    session_id: str,
    config: dict,
) -> dict:
    """
    Ejecuta un análisis InSAR en un sandbox temporal aislado para una
    zona personalizada fuera de los 5 volcanes de monitoreo permanente.

    Los resultados se empaquetan en un .zip descargable por el usuario.
    El sandbox se auto-elimina tras 24 horas.

    Args:
        job_id:     Identificador único de la tarea.
        session_id: Identificador de la sesión ad-hoc (carpeta temporal).
        config:     Parámetros del análisis (fechas, AOI, número de imágenes, etc.).
    """
    sandbox_dir = TMP_SESSIONS / session_id
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    _write_status(job_id, "started", "Iniciando análisis ad-hoc en sandbox aislado", 1)

    try:
        _write_status(job_id, "progress", "Verificando espacio en disco disponible", 3)
        n_images = config.get("n_images", 0)
        if n_images > 0:
            check_space_for_images(n_images)
        else:
            check_minimum_free()

        _write_status(job_id, "progress", "Descargando imágenes en sandbox aislado", 15)
        logger.info("[AdHoc %s] Sandbox: %s | Config: %s", job_id, sandbox_dir, config)
        time.sleep(1)

        _write_status(job_id, "progress", "Ejecutando MintPy en sandbox", 50)
        time.sleep(1)

        _write_status(job_id, "progress", "Empaquetando resultados en .zip", 85)
        zip_path = sandbox_dir / f"resultado_adhoc_{session_id}.zip"
        # (La generación real del zip ocurre aquí en implementación completa)
        time.sleep(1)

        result_summary = {
            "job_id":       job_id,
            "session_id":   session_id,
            "zip_path":     str(zip_path),
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
        _write_status(
            job_id, "completed",
            "Análisis ad-hoc completado. Resultado listo para descargar.", 100,
            result=result_summary,
        )
        return result_summary

    except DiskSpaceError as exc:
        logger.error("[AdHoc %s] BLOQUEADO por Disk Guardrail: %s", job_id, exc)
        _write_status(
            job_id, "failed",
            "Bloqueado por falta de almacenamiento", 0,
            error=str(exc),
        )
        
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        raise

    except Exception as exc:
        logger.exception("[AdHoc %s] Error en análisis ad-hoc: %s", job_id, exc)
        _write_status(
            job_id, "failed",
            f"Error inesperado: {type(exc).__name__}", 0,
            error=str(exc),
        )
        raise


@celery_app.task(
    bind=True,
    name="tasks.processing_tasks.run_monthly_ingest",
    max_retries=0,
    time_limit=None,
    queue="geodesk_heavy",
)
def run_monthly_ingest(self: Task, job_id: str) -> dict:
    """
    Tarea lanzada por el cron mensual (día 1 de cada mes, 02:00 AM).
    Descarga las 2-3 nuevas escenas Sentinel-1 del mes y actualiza la pila
    de los 5 volcanes. Si hay falta de espacio, registra la alerta y se
    detiene sin tumbar el servidor.
    """
    _write_status(job_id, "started", "Iniciando ingesta mensual automatizada", 1)

    try:
        _write_status(job_id, "progress", "Verificando espacio en disco (cron)", 5)
        disk = check_minimum_free()
        logger.info(
            "[Cron %s] Espacio libre: %.1f GB — Continuando ingesta mensual.",
            job_id, disk.free_gb,
        )

        check_space_for_images(n_images=3) # debe de ser reactivo

        _write_status(job_id, "progress", "Buscando escenas nuevas Sentinel-1", 15)
        time.sleep(1)

        _write_status(job_id, "progress", "Solicitando interferogramas a HyP3", 30)
        time.sleep(1)

        _write_status(job_id, "progress", "Actualizando pila MintPy con nuevas imágenes", 55)
        time.sleep(1)

        _write_status(job_id, "progress", "Regenerando GeoTIFFs y series Parquet", 75)
        time.sleep(1)

        _write_status(job_id, "progress", "Actualizando perfiles de los 5 volcanes", 90)
        time.sleep(1)

        result_summary = {
            "job_id":       job_id,
            "type":         "monthly_ingest",
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
        _write_status(
            job_id, "completed",
            "Ingesta mensual completada correctamente", 100,
            result=result_summary,
        )
        return result_summary

    except DiskSpaceError as exc:
        logger.error(
            "[Cron %s] INGESTA MENSUAL CANCELADA — Espacio insuficiente: %s",
            job_id, exc,
        )
        _write_status(
            job_id, "failed",
            "Ingesta mensual cancelada: almacenamiento insuficiente", 0,
            error=str(exc),
        )
        return {"job_id": job_id, "cancelled": True, "reason": str(exc)}

    except Exception as exc:
        logger.exception("[Cron %s] Error en ingesta mensual: %s", job_id, exc)
        _write_status(
            job_id, "failed",
            f"Error inesperado en ingesta mensual: {type(exc).__name__}", 0,
            error=str(exc),
        )
        raise
