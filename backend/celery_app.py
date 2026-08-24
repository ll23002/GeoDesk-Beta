import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "geodesk",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks.processing_tasks", "tasks.automated_pipeline", "tasks.orchestrator"],
)

from celery.schedules import crontab

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    timezone="America/El_Salvador",
    enable_utc=True,

    worker_concurrency=5,
    worker_prefetch_multiplier=1, # 1 tarea por proceso para evitar starvation

    task_default_queue="geodesk_heavy",
    task_routes={
        "tasks.processing_tasks.*": {"queue": "geodesk_heavy"},
        "tasks.automated_pipeline.*": {"queue": "geodesk_heavy"},
        "tasks.orchestrator.*": {"queue": "geodesk_heavy"},
    },

    result_expires=86400 * 7, # por que tanto tiempo?

    # Si un proceso hijo supera 11 GB de RAM, Celery lo reinicia limpiamente
    worker_max_memory_per_child=11 * 1024 * 1024,  # 11 GB en KB

    task_acks_late=True,
    task_reject_on_worker_lost=True,

    task_track_started=True,
    
    # Cron Jobs
    beat_schedule={
        "actualizacion-mensual-insar": {
            "task": "tasks.orchestrator.monthly_cron_update",
            "schedule": crontab(day_of_month="1", hour="0", minute="0"),
        },
        "verificar-historico-arranque": {
            "task": "tasks.orchestrator.bootstrap_historical",
            "schedule": 300.0, 
        }
    }
)
