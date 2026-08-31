import logging
from datetime import datetime
from dateutil.relativedelta import relativedelta
from celery import shared_task, chain
from celery.result import AsyncResult

from utils.timeseries_parquet import VOLCANOES, list_available_years
from tasks.automated_pipeline import (
    pipeline_submit_hyp3,
    pipeline_wait_and_download,
    pipeline_run_mintpy,
    pipeline_finalize_and_cleanup
)

logger = logging.getLogger(__name__)

def _pipeline_task_id(volcano: str, year: int) -> str:
    """ID determinístico para el primer paso del pipeline de un (volcán, año).
    Celery rechaza silenciosamente tareas con el mismo ID si ya están en cola o
    corriendo, previniendo duplicados.
    """
    return f"pipeline-submit-{volcano}-{year}"


def _is_pipeline_running(volcano: str, year: int) -> bool:
    """Retorna True si ya existe una tarea activa/pendiente para este (volcán, año)."""
    task_id = _pipeline_task_id(volcano, year)
    result = AsyncResult(task_id)
    # PENDING puede significar "no existe", pero combinado con el ID determinístico
    # es suficiente: si la tarea fue encolada con ese ID, su estado no será PENDING.
    return result.state in ("STARTED", "RECEIVED", "RETRY")


def _launch_pipeline_for(volcano: str, year: int, start_iso: str, end_iso: str):
    """Encola la cadena de tareas para un volcán y un año específico.
    Usa un task_id determinístico para que Celery descarte automáticamente
    cualquier intento de encolar la misma tarea dos veces.
    """
    logger.info(f"Orquestador: Lanzando pipeline para {volcano} ({year})")

    task_id = _pipeline_task_id(volcano, year)
    workflow = chain(
        pipeline_submit_hyp3.s(volcano, year, start_iso, end_iso),
        pipeline_wait_and_download.s(),
        pipeline_run_mintpy.s(),
        pipeline_finalize_and_cleanup.s()
    )
    workflow.apply_async(queue="geodesk_heavy", task_id=task_id)


@shared_task(name="tasks.orchestrator.bootstrap_historical")
def bootstrap_historical():
    """
    Se ejecuta al iniciar el sistema (o manualmente).
    Verifica qué años históricos (2024, 2025) faltan en la base de datos de Parquet
    para cada volcán, y lanza el pipeline para calcularlos.
    """
    logger.info("Orquestador: Verificando datos históricos faltantes...")
    
    historical_years = [2024, 2025]
    
    for volcano in VOLCANOES.keys():
        available = list_available_years(volcano)
        
        for year in historical_years:
            if year not in available:
                if _is_pipeline_running(volcano, year):
                    logger.info(f"Orquestador: Pipeline para {volcano}-{year} ya está activo. Omitiendo.")
                    continue
                logger.info(f"Orquestador: Faltan datos de {volcano} para el año {year}. Encolando.")
                start_iso = f"{year}-01-01T00:00:00Z"
                end_iso = f"{year}-12-31T23:59:59Z"
                _launch_pipeline_for(volcano, year, start_iso, end_iso)


@shared_task(name="tasks.orchestrator.monthly_cron_update")
def monthly_cron_update():
    """
    Se ejecuta el primer día de cada mes (ej. 1 de Marzo).
    Descarga el mes que acaba de terminar (ej. Febrero) y lo anexa a la pila del año en curso.
    """
    now = datetime.utcnow()
    current_year = now.year
    
    # Rango de fechas: Desde el inicio del año actual hasta "hoy" (fin del mes pasado).
    # Como la pila de MintPy necesita historia para ser coherente, descargamos todo el año en curso
    # hasta la fecha actual, y sobreescribimos el Parquet de este año.
    start_iso = f"{current_year}-01-01T00:00:00Z"
    end_iso = now.strftime("%Y-%m-%dT23:59:59Z")
    
    logger.info(f"Orquestador: Ejecutando actualización mensual para el año {current_year}...")
    
    for volcano in VOLCANOES.keys():
        _launch_pipeline_for(volcano, current_year, start_iso, end_iso)

