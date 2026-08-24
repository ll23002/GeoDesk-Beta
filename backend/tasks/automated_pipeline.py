import os
import shutil
import logging
from pathlib import Path
from datetime import datetime
from celery import shared_task

from utils.hyp3_client import check_hyp3_quota, submit_insar_jobs
from utils.timeseries_parquet import VOLCANOES, save_from_mintpy_timeseries_h5
from routes.solicitar_imagenes_automatico import search_scenes, build_pairs
from routes.mintpy_analysis import _run_mintpy_pipeline
import hyp3_sdk as sdk

logger = logging.getLogger(__name__)

MIN_COHERENCE = 0.5
DOWNLOAD_BASE = Path("/app/alaska_descargas/auto_pipeline")
WORK_BASE = Path("/tmp/mintpy_auto")

@shared_task(bind=True, max_retries=3, default_retry_delay=3600, queue="geodesk_heavy")
def pipeline_submit_hyp3(self, volcano: str, year: int, start_date_iso: str, end_date_iso: str):
    """Paso 1: Busca imágenes en ASF, arma pares y envía a HyP3 si hay cuota."""
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
        return {"job_ids": [], "volcano": volcano, "year": year}
        
    # Verificar cuota
    username = os.getenv("HYP3_USERNAME")
    password = os.getenv("HYP3_PASSWORD")
    if not check_hyp3_quota(username, password, required_credits=len(pairs)):
        logger.warning(f"[{volcano}-{year}] Cuota insuficiente en HyP3. Reintentando en 3 días.")
        # Reintenta en 3 días (3 * 24 * 3600 = 259200 segundos)
        raise self.retry(countdown=259200)
        
    # Enviar a HyP3
    logger.info(f"[{volcano}-{year}] Enviando {len(pairs)} pares a HyP3...")
    project_name = f"auto_{volcano}_{year}"
    summaries = submit_insar_jobs(pairs, project_name, username, password)
    job_ids = [s["job_id"] for s in summaries if s.get("job_id")]
    
    logger.info(f"[{volcano}-{year}] Enviados {len(job_ids)} trabajos exitosamente.")
    return {"job_ids": job_ids, "volcano": volcano, "year": year}


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
    
    if not work_dir:
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
        
    # Limpieza
    logger.info(f"[{volcano}-{year}] LIMPIEZA AUTOMÁTICA: Borrando {work_dir} y {download_dir}...")
    if work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)
    if download_dir:
        shutil.rmtree(download_dir, ignore_errors=True)
        
    logger.info(f"[{volcano}-{year}] Pipeline completado con éxito.")
    return True
