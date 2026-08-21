import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "geodesk",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks.processing_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    timezone="America/El_Salvador",
    enable_utc=True,

    worker_concurrency=5,
    worker_prefetch_multiplier=1, # 1 tarea por proceso para evitar starvation

    # Solo 1 tarea pesada activa a la vez: se usa solo la queue "geodesk_heavy".
    # Tareas adicionales se encolan automáticamente y esperan su turno.
    task_default_queue="geodesk_heavy",
    task_routes={
        "tasks.processing_tasks.*": {"queue": "geodesk_heavy"},
    },

    result_expires=86400 * 7, # por que tanto tiempo?

    # Si un proceso hijo supera 11 GB de RAM, Celery lo reinicia limpiamente
    # antes de que Linux OOM-killer lo mate de forma abrupta.
    worker_max_memory_per_child=11 * 1024 * 1024,  # 11 GB en KB

    # Re-encolar tareas si el worker muere antes de finalizar
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Visibilidad de tareas para el frontend
    task_track_started=True,
)
