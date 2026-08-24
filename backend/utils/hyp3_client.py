import logging
from typing import Optional, List, Tuple, Any
import hyp3_sdk as sdk

logger = logging.getLogger(__name__)

def check_hyp3_quota(username: str, password: str, required_credits: int) -> bool:
    """Verifica si la cuenta tiene suficientes créditos para enviar los trabajos."""
    try:
        hyp3 = sdk.HyP3(username=username, password=password)
        info = hyp3.my_info()
        remaining = info.get("remaining_credits", 0)
        logger.info(f"Créditos HyP3 restantes: {remaining}. Requeridos: {required_credits}")
        return remaining >= required_credits
    except Exception as e:
        logger.error(f"Error verificando cuota de HyP3: {e}")
        return False

def submit_insar_jobs(
    pairs: List[Tuple[str, str]],
    project_name: str,
    username: str,
    password: str,
) -> List[dict]:
    """Envía los pares a HyP3 y devuelve resúmenes simples (no objetos de SDK para poder serializar en Celery)."""
    if not pairs:
        return []

    hyp3 = sdk.HyP3(username=username, password=password)
    summaries = []

    for idx, (g1, g2) in enumerate(pairs, 1):
        job_name = f"{project_name}_{idx:02d}"
        try:
            batch = hyp3.submit_insar_job(
                granule1=g1,
                granule2=g2,
                name=job_name,
                include_dem=True,
                include_look_vectors=True,
                include_displacement_maps=True,
                looks="20x4",
            )
            job = batch[0] if len(batch) > 0 else None
            if job:
                summaries.append({
                    "job_id": job.job_id,
                    "name": job.name,
                    "granule1": g1,
                    "granule2": g2,
                    "status": job.status_code
                })
        except Exception as e:
            logger.error(f"Error enviando el par {g1}-{g2} a HyP3: {e}")

    return summaries
