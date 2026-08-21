import shutil
import logging
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MINIMUM_FREE_GB: float = 50.0    # Reserva mínima del SO
WARNING_FREE_GB: float = 80.0    # Umbral de advertencia temprana
GB_PER_IMAGE: float    = 1.5     # Espacio estimado por interferograma HyP3

MONITORED_PATH: Path = Path("/")


class DiskSpaceError(Exception):
    """Se lanza cuando no hay suficiente espacio libre para ejecutar la tarea solicitada."""

    def __init__(self, free_gb: float, required_gb: float, minimum_gb: float):
        self.free_gb     = round(free_gb, 2)
        self.required_gb = round(required_gb, 2)
        self.minimum_gb  = minimum_gb
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        return (
            f"Acción bloqueada: El procesamiento solicitado requiere aproximadamente "
            f"{self.required_gb:.1f} GB de almacenamiento. "
            f"El servidor dispone actualmente de {self.free_gb:.1f} GB libres y la "
            f"reserva mínima de seguridad del sistema es de {self.minimum_gb:.0f} GB. "
            f"Por favor, elimine sesiones temporales o libere espacio antes de continuar."
        )

    def to_dict(self) -> dict:
        """Representación serializable para respuestas HTTP."""
        return {
            "error": "disk_space_insufficient",
            "message": str(self),
            "free_gb": self.free_gb,
            "required_gb": self.required_gb,
            "minimum_free_gb": self.minimum_gb,
        }


@dataclass
class DiskStatus:
    total_gb: float
    used_gb: float
    free_gb: float
    percent_used: float
    is_warning: bool
    is_critical: bool

    def to_dict(self) -> dict:
        return {
            "total_gb":     round(self.total_gb, 2),
            "used_gb":      round(self.used_gb, 2),
            "free_gb":      round(self.free_gb, 2),
            "percent_used": round(self.percent_used, 1),
            "is_warning":   self.is_warning,
            "is_critical":  self.is_critical,
            "minimum_free_gb": MINIMUM_FREE_GB,
            "warning_free_gb": WARNING_FREE_GB,
        }


def get_disk_status(path: Path = MONITORED_PATH) -> DiskStatus:
    """
    Retorna el estado actual del disco en gigabytes.

    Args:
        path: Ruta del sistema de archivos a inspeccionar.

    Returns:
        DiskStatus con métricas del disco y flags de advertencia/crítico.
    """
    usage = shutil.disk_usage(path)
    total_gb   = usage.total / (1024 ** 3)
    used_gb    = usage.used  / (1024 ** 3)
    free_gb    = usage.free  / (1024 ** 3)
    percent    = (used_gb / total_gb * 100) if total_gb > 0 else 0.0

    status = DiskStatus(
        total_gb     = total_gb,
        used_gb      = used_gb,
        free_gb      = free_gb,
        percent_used = percent,
        is_warning   = free_gb < WARNING_FREE_GB,
        is_critical  = free_gb < MINIMUM_FREE_GB,
    )

    if status.is_critical:
        logger.error(
            "[DiskGuard] CRÍTICO: Solo %.1f GB libres (mínimo requerido: %.0f GB). "
            "El procesamiento está bloqueado.",
            free_gb, MINIMUM_FREE_GB,
        )
    elif status.is_warning:
        logger.warning(
            "[DiskGuard] ADVERTENCIA: Solo %.1f GB libres (umbral de advertencia: %.0f GB).",
            free_gb, WARNING_FREE_GB,
        )

    return status


def check_space_for_images(
    n_images: int,
    path: Path = MONITORED_PATH,
    gb_per_image: float = GB_PER_IMAGE,
) -> DiskStatus:
    """
    Verifica si hay espacio suficiente para descargar y procesar `n_images`
    interferogramas de HyP3. Lanza DiskSpaceError si no hay espacio suficiente.

    Args:
        n_images:     Número de imágenes / pares interferométricos a descargar.
        path:         Ruta del sistema de archivos a inspeccionar.
        gb_per_image: Espacio estimado por imagen.

    Returns:
        DiskStatus si la operación es segura.

    Raises:
        DiskSpaceError: Si la operación proyectada viola la reserva mínima.
    """
    status = get_disk_status(path)
    required_gb = n_images * gb_per_image

    logger.info(
        "[DiskGuard] Solicitud: %d imágenes × %.1f GB/imagen = %.1f GB requeridos. "
        "Disponible: %.1f GB. Reserva mínima: %.0f GB.",
        n_images, gb_per_image, required_gb, status.free_gb, MINIMUM_FREE_GB,
    )

    projected_free_gb = status.free_gb - required_gb

    if projected_free_gb < MINIMUM_FREE_GB:
        raise DiskSpaceError(
            free_gb     = status.free_gb,
            required_gb = required_gb,
            minimum_gb  = MINIMUM_FREE_GB,
        )

    return status


def check_minimum_free(path: Path = MONITORED_PATH) -> DiskStatus:
    """
    Verifica únicamente que el disco no esté ya en estado crítico,
    sin calcular espacio proyectado. Útil para el cron automático antes
    de iniciar cualquier operación.

    Raises:
        DiskSpaceError: Si el disco ya está por debajo de la reserva mínima.
    """
    status = get_disk_status(path)
    if status.is_critical:
        raise DiskSpaceError(
            free_gb     = status.free_gb,
            required_gb = 0.0,
            minimum_gb  = MINIMUM_FREE_GB,
        )
    return status
