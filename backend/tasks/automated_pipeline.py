import os
import shutil
import logging
from pathlib import Path
from datetime import datetime
from celery import shared_task
from redis import Redis

from utils.hyp3_client import check_hyp3_quota, submit_insar_jobs
from utils.timeseries_parquet import VOLCANOES, save_from_mintpy_timeseries_h5
from routes.solicitar_imagenes_automatico import search_scenes, build_pairs
from routes.mintpy_analysis import _run_mintpy_pipeline
import hyp3_sdk as sdk

logger = logging.getLogger(__name__)

# Lock distribuido: evita que múltiples workers ejecuten el mismo pipeline en paralelo.
# TTL = 4 días (tiempo suficiente para cubrir el countdown de reintento de 3 días + margen).
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_LOCK_TTL_SECONDS = 4 * 24 * 3600  # 4 días


def _get_redis() -> Redis:
    return Redis.from_url(_REDIS_URL, decode_responses=True)


def _pipeline_lock_key(volcano: str, year: int) -> str:
    return f"pipeline_lock:{volcano}:{year}"


def _acquire_lock(volcano: str, year: int, task_id: str) -> bool:
    """Intenta adquirir el lock para este (volcano, year).
    Retorna True si lo adquirió (puede proceder), False si ya existe (otro worker está corriendo).
    Usa SET NX (set-if-not-exists) para garantizar atomicidad.
    """
    r = _get_redis()
    key = _pipeline_lock_key(volcano, year)
    acquired = r.set(key, task_id, ex=_LOCK_TTL_SECONDS, nx=True)
    return acquired is True


def _release_lock(volcano: str, year: int, task_id: str):
    """Libera el lock sólo si lo posee esta tarea (evita liberar el lock de otra instancia)."""
    r = _get_redis()
    key = _pipeline_lock_key(volcano, year)
    current = r.get(key)
    if current == task_id:
        r.delete(key)


def _refresh_lock(volcano: str, year: int, task_id: str):
    """Renueva el TTL del lock. Se llama antes de cada reintento para que no expire."""
    r = _get_redis()
    key = _pipeline_lock_key(volcano, year)
    current = r.get(key)
    if current == task_id:
        r.expire(key, _LOCK_TTL_SECONDS)

MIN_COHERENCE = 0.5
DOWNLOAD_BASE = Path("/app/alaska_descargas/auto_pipeline")
WORK_BASE = Path("/tmp/mintpy_auto")

# max_retries=30: cubre hasta 90 días de espera (30 reintentos × 3 días cada uno).
@shared_task(bind=True, max_retries=30, default_retry_delay=3600, queue="geodesk_heavy")
def pipeline_submit_hyp3(self, volcano: str, year: int, start_date_iso: str, end_date_iso: str):
    """Paso 1: Busca imágenes en ASF, arma pares y envía a HyP3 si hay cuota."""
    task_id = self.request.id

    # --- Fix 3: Lock distribuido ---
    # Si no podemos adquirir el lock, otro worker ya está corriendo este pipeline.
    # En lugar de fallar ruidosamente, simplemente ignoramos esta ejecución duplicada.
    if not _acquire_lock(volcano, year, task_id):
        existing = _get_redis().get(_pipeline_lock_key(volcano, year))
        logger.warning(
            f"[{volcano}-{year}] Pipeline ya está corriendo (task_id={existing}). "
            f"Descartando ejecución duplicada (task_id={task_id})."
        )
        return {"job_ids": [], "volcano": volcano, "year": year, "skipped": True}

    try:
        logger.info(f"[{volcano}-{year}] Iniciando pipeline automático.")

        config = VOLCANOES.get(volcano)
        if not config:
            raise ValueError(f"Volcán no encontrado: {volcano}")

        track = config.get("track", 128)
        frame = config.get("frame", 547)
        polygon = config.get("polygon")

        # Busca imágenes en ASF
        logger.info(f"[{volcano}-{year}] Buscando SLCs en ASF...")
        results = search_scenes(start_date_iso, end_date_iso, ruta=track, marco=frame, direction="DESCENDING", polygon=polygon)
        pairs = build_pairs(results, day_interval=12)
        logger.info(f"[{volcano}-{year}] Pares construidos: {len(pairs)}")

        if not pairs:
            logger.info(f"[{volcano}-{year}] No hay pares para procesar. Fin.")
            _release_lock(volcano, year, task_id)
            return {"job_ids": [], "volcano": volcano, "year": year}

        # Verificar cuota
        username = os.getenv("HYP3_USERNAME")
        password = os.getenv("HYP3_PASSWORD")
        if not check_hyp3_quota(username, password, required_credits=len(pairs)):
            logger.warning(
                f"[{volcano}-{year}] Cuota insuficiente en HyP3 "
                f"(disponibles: 0, requeridos: {len(pairs)}). "
                f"Reintentando en 3 días. (Intento {self.request.retries + 1}/{self.max_retries})"
            )
            # Renovar el lock antes del reintento para que no expire durante la espera
            _refresh_lock(volcano, year, task_id)
            # Reintenta en 3 días (3 * 24 * 3600 = 259200 segundos)
            raise self.retry(countdown=259200)

        # Enviar a HyP3
        logger.info(f"[{volcano}-{year}] Enviando {len(pairs)} pares a HyP3...")
        project_name = f"auto_{volcano}_{year}"
        summaries = submit_insar_jobs(pairs, project_name, username, password)
        job_ids = [s["job_id"] for s in summaries if s.get("job_id")]

        logger.info(f"[{volcano}-{year}] Enviados {len(job_ids)} trabajos exitosamente.")
        # El lock se libera al final del pipeline (en pipeline_finalize_and_cleanup),
        # ya que los pasos siguientes también pertenecen a la misma ejecución.
        return {"job_ids": job_ids, "volcano": volcano, "year": year, "lock_task_id": task_id}

    except Exception as exc:
        # Celery usa una excepción Retry interna para programar reintentos.
        # Debemos dejar que se propague sin tocar el lock (el pipeline sigue vivo).
        from celery.exceptions import Retry
        if isinstance(exc, Retry):
            raise
        # Para cualquier otro error inesperado, liberamos el lock para que el
        # orquestador pueda volver a intentar el pipeline en el futuro.
        _release_lock(volcano, year, task_id)
        raise


@shared_task(bind=True, max_retries=168, default_retry_delay=3600, queue="geodesk_heavy")
def pipeline_wait_and_download(self, prev_result: dict):
    """Paso 2: Espera a que los trabajos de HyP3 terminen y los descarga. Reintenta cada hora (hasta 1 semana)."""
    job_ids = prev_result.get("job_ids", [])
    volcano = prev_result.get("volcano")
    year = prev_result.get("year")
    
    if not job_ids:
        return prev_result # Nada que descargar
        
    logger.info(f"[{volcano}-{year}] Revisando estado de {len(job_ids)} trabajos en HyP3...")
    username = os.getenv("HYP3_USERNAME")
    password = os.getenv("HYP3_PASSWORD")
    hyp3 = sdk.HyP3(username=username, password=password)
    
    pending = 0
    failed = 0
    completed_jobs = []
    
    for jid in job_ids:
        try:
            job = hyp3.get_job_by_id(jid)
            if not job.complete():
                pending += 1
            elif job.status_code == 'FAILED':
                failed += 1
            elif job.status_code == 'SUCCEEDED':
                completed_jobs.append(job)
        except Exception as e:
            logger.error(f"Error consultando job {jid}: {e}")
            pending += 1
            
    if pending > 0:
        logger.info(f"[{volcano}-{year}] {pending} trabajos pendientes. Reintentando en 1 hora.")
        raise self.retry(countdown=3600)
        
    logger.info(f"[{volcano}-{year}] Todos terminados. {len(completed_jobs)} éxitos, {failed} fallos.")
    
    if not completed_jobs:
        raise ValueError(f"[{volcano}-{year}] Todos los trabajos de HyP3 fallaron. Cancelando pipeline.")
        
    # Descargar
    download_dir = DOWNLOAD_BASE / f"{volcano}_{year}"
    download_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"[{volcano}-{year}] Descargando ZIPs a {download_dir}...")
    for job in completed_jobs:
        try:
            job.download_files(str(download_dir))
        except Exception as e:
            logger.error(f"Error descargando {job.job_id}: {e}")
            
    prev_result["download_dir"] = str(download_dir)
    return prev_result


@shared_task(bind=True, queue="geodesk_heavy")
def pipeline_run_mintpy(self, prev_result: dict):
    """Paso 3: Ejecuta el análisis MintPy sobre los ZIPs descargados."""
    download_dir = prev_result.get("download_dir")
    volcano = prev_result.get("volcano")
    year = prev_result.get("year")
    
    if not download_dir:
        return prev_result
        
    logger.info(f"[{volcano}-{year}] Iniciando procesamiento MintPy...")
    
    config = VOLCANOES[volcano]
    bbox = config["bbox"]
    lat_min = min(bbox[0], bbox[2])
    lat_max = max(bbox[0], bbox[2])
    lon_min = min(bbox[1], bbox[3])
    lon_max = max(bbox[1], bbox[3])
    
    work_dir = WORK_BASE / f"{volcano}_{year}"
    work_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        era5_key = os.getenv("ERA5_KEY") # Cuenta institucional
        _run_mintpy_pipeline(
            work_dir=Path(work_dir),
            zip_dir=Path(download_dir),
            ref_lat=None, # MintPy elegirá automático
            ref_lon=None,
            crop_lat_min=lat_min,
            crop_lat_max=lat_max,
            crop_lon_min=lon_min,
            crop_lon_max=lon_max,
            has_triplets=True,
            min_coherence=MIN_COHERENCE,
            era5_key=era5_key
        )
        prev_result["work_dir"] = str(work_dir)
        return prev_result
    except Exception as e:
        logger.error(f"[{volcano}-{year}] Falló MintPy: {e}")
        raise


@shared_task(bind=True, queue="geodesk_heavy")
def pipeline_finalize_and_cleanup(self, prev_result: dict):
    """Paso 4: Guarda los resultados a Parquet y borra las gigas de archivos temporales."""
    work_dir = prev_result.get("work_dir")
    download_dir = prev_result.get("download_dir")
    volcano = prev_result.get("volcano")
    year = prev_result.get("year")
    lock_task_id = prev_result.get("lock_task_id")

    if not work_dir:
        _release_lock(volcano, year, lock_task_id)
        return prev_result

    logger.info(f"[{volcano}-{year}] Guardando resultados Parquet...")

    h5_path = Path(work_dir) / "timeseries.h5"
    if h5_path.exists():
        config = VOLCANOES[volcano]
        bbox = config["bbox"]

        try:
            out_path = save_from_mintpy_timeseries_h5(
                h5_path=h5_path,
                volcano=volcano,
                year=year,
                lat_min=min(bbox[0], bbox[2]),
                lat_max=max(bbox[0], bbox[2]),
                lon_min=min(bbox[1], bbox[3]),
                lon_max=max(bbox[1], bbox[3]),
                overwrite=True
            )
            logger.info(f"[{volcano}-{year}] Parquet guardado exitosamente en: {out_path}")
        except Exception as e:
            logger.error(f"[{volcano}-{year}] Error guardando parquet: {e}")
    else:
        logger.error(f"[{volcano}-{year}] No se encontró timeseries.h5!")

    # Limpieza de archivos temporales
    logger.info(f"[{volcano}-{year}] LIMPIEZA AUTOMÁTICA: Borrando {work_dir} y {download_dir}...")
    if work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)
    if download_dir:
        shutil.rmtree(download_dir, ignore_errors=True)

    # Liberar el lock distribuido: el pipeline terminó exitosamente.
    # El orquestador podrá relanzar el pipeline si detecta que faltan datos.
    _release_lock(volcano, year, lock_task_id)
    logger.info(f"[{volcano}-{year}] Pipeline completado con éxito.")
    return True
