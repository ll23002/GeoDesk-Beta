import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR      = Path(__file__).resolve().parent.parent
PARQUET_DIR   = BASE_DIR / "mintpy_results" / "timeseries_parquet"


def _ensure_parquet_dir() -> Path:
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    return PARQUET_DIR

VOLCANOES: dict[str, dict] = {
    "santa_ana": {
        "name":    "Volcán de Santa Ana (Ilamatepec)",
        "lat":     13.8533,
        "lon":     -89.6300,
        "bbox":    [13.80, -89.68, 13.90, -89.58],   # [lat_min, lon_min, lat_max, lon_max]
        "track":   128,
        "frame":   547,
        "polygon": "POLYGON((-89.68 13.80, -89.58 13.80, -89.58 13.90, -89.68 13.90, -89.68 13.80))",
    },
    "san_salvador": {
        "name":    "Volcán de San Salvador (Quezaltepeque)",
        "lat":     13.7342,
        "lon":     -89.2847,
        "bbox":    [13.68, -89.34, 13.78, -89.23],
        "track":   128,
        "frame":   547,
        "polygon": "POLYGON((-89.34 13.68, -89.23 13.68, -89.23 13.78, -89.34 13.78, -89.34 13.68))",
    },
    "san_miguel": {
        "name":    "Volcán de San Miguel (Chaparrastique)",
        "lat":     13.4331,
        "lon":     -88.2694,
        "bbox":    [13.38, -88.32, 13.48, -88.21],
        "track":   128,
        "frame":   547,
        "polygon": "POLYGON((-88.32 13.38, -88.21 13.38, -88.21 13.48, -88.32 13.48, -88.32 13.38))",
    },
    "san_vicente": {
        "name":    "Volcán de San Vicente (Chinchontepec)",
        "lat":     13.5961,
        "lon":     -88.8378,
        "bbox":    [13.54, -88.89, 13.65, -88.78],
        "track":   128,
        "frame":   547,
        "polygon": "POLYGON((-88.89 13.54, -88.78 13.54, -88.78 13.65, -88.89 13.65, -88.89 13.54))",
    },
    "ilopango": {
        "name":    "Caldera de Ilopango",
        "lat":     13.6717,
        "lon":     -89.0533,
        "bbox":    [13.62, -89.11, 13.72, -89.00],
        "track":   128,
        "frame":   547,
        "polygon": "POLYGON((-89.11 13.62, -89.00 13.62, -89.00 13.72, -89.11 13.72, -89.11 13.62))",
    },
}


def _parquet_path(volcano: str, year: int) -> Path:
    """Retorna la ruta canónica del archivo Parquet para un volcán y año dados."""
    return _ensure_parquet_dir() / f"timeseries_{volcano}_{year}.parquet"


def save_timeseries(
    df: pd.DataFrame,
    volcano: str,
    year: int,
    overwrite: bool = False,
) -> Path:
    """
    Guarda la serie temporal de un año procesado en formato Parquet.

    Args:
        df:        DataFrame con columnas: lat, lon, date, deformation, volcano, year.
        volcano:   Identificador del volcán (ej. "santa_ana", "national").
        year:      Año de la pila de procesamiento.
        overwrite: Si True, sobreescribe el archivo si ya existe.

    Returns:
        Path al archivo Parquet guardado.

    Raises:
        FileExistsError: Si el archivo ya existe y overwrite=False.
        ValueError:      Si el DataFrame no tiene las columnas requeridas.
    """
    required = {"lat", "lon", "date", "deformation"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"El DataFrame no tiene las columnas requeridas: {missing}"
        )

    path = _parquet_path(volcano, year)
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"Ya existe una serie para '{volcano}' año {year}: {path}. "
            "Use overwrite=True para sobreescribir."
        )

    df = df.copy()
    df["date"]        = pd.to_datetime(df["date"])
    df["deformation"] = df["deformation"].astype(np.float64)
    df["lat"]         = df["lat"].astype(np.float64)
    df["lon"]         = df["lon"].astype(np.float64)
    df["volcano"]     = volcano
    df["year"]        = np.int16(year)

    df.to_parquet(path, engine="pyarrow", index=False, compression="snappy")
    size_mb = path.stat().st_size / (1024 ** 2)
    logger.info(
        "[Parquet] Serie guardada: %s (%d filas, %.2f MB)",
        path.name, len(df), size_mb,
    )
    return path


def load_timeseries(volcano: str, year: int) -> Optional[pd.DataFrame]:
    """
    Carga la serie temporal de un volcán y año desde el archivo Parquet.

    Returns:
        DataFrame o None si el archivo no existe.
    """
    path = _parquet_path(volcano, year)
    if not path.exists():
        logger.warning("[Parquet] No existe serie para '%s' año %d", volcano, year)
        return None
    df = pd.read_parquet(path, engine="pyarrow")
    df["date"] = pd.to_datetime(df["date"])
    return df


def get_year_end_offset(volcano: str, year: int) -> Optional[float]:
    """
    Retorna el valor de deformación acumulada en la ÚLTIMA fecha del año dado.
    Este valor se usa como offset para encadenar el año siguiente.

    La operación de concatenación es:
        D_total(t_siguiente) = offset + D_relativo_siguiente(t)

    Returns:
        Float en mm del último valor de deformación, o None si no hay datos.
    """
    df = load_timeseries(volcano, year)
    if df is None or df.empty:
        return None

    last_date = df["date"].max()
    last_rows = df[df["date"] == last_date]

    if last_rows.empty:
        return None

    # Promedio espacial de la deformación en la última fecha
    offset = float(last_rows["deformation"].mean())
    logger.info(
        "[Parquet] Offset final '%s' año %d (fecha %s): %.3f mm",
        volcano, year, last_date.strftime("%Y-%m-%d"), offset,
    )
    return offset


def concatenate_timeseries(
    volcano: str,
    years: list[int],
    new_year_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Concatena las series temporales de múltiples años en una serie continua.

    Aplica el offset acumulado año tras año para mantener la continuidad
    matemática de la deformación en la gráfica de la interfaz web.

    Algoritmo:
        - Para el primer año: usar valores tal como están.
        - Para cada año siguiente: sumar el offset final del año anterior.

    Args:
        volcano:     Identificador del volcán.
        years:       Lista de años históricos ya purgados (ej. [2024, 2025]).
                     Deben estar en orden cronológico ascendente.
        new_year_df: DataFrame del año en curso (aún no purgado, opcional).
                     Si se provee, se concatena al final con el offset aplicado.

    Returns:
        DataFrame concatenado con todos los años, con la deformación ajustada
        por offset para continuidad.

    Raises:
        ValueError: Si no se encuentra ningún año disponible.
    """
    all_dfs: list[pd.DataFrame] = []
    cumulative_offset: float = 0.0

    sorted_years = sorted(years)

    for i, year in enumerate(sorted_years):
        df = load_timeseries(volcano, year)
        if df is None:
            logger.warning(
                "[Parquet] Año %d no encontrado para '%s', saltando.", year, volcano
            )
            continue

        if i == 0:
            # Primer año: sin ajuste
            all_dfs.append(df.copy())
        else:
            # Años siguientes: aplicar offset acumulado
            df_adjusted = df.copy()
            df_adjusted["deformation"] = df_adjusted["deformation"] + cumulative_offset
            all_dfs.append(df_adjusted)

        # Actualizar offset acumulado con el valor final de este año
        year_end = get_year_end_offset(volcano, year)
        if year_end is not None:
            cumulative_offset = year_end

    # Concatenar el año en curso (no purgado) si se provee
    if new_year_df is not None and not new_year_df.empty:
        new_df = new_year_df.copy()
        new_df["date"]        = pd.to_datetime(new_df["date"])
        new_df["deformation"] = new_df["deformation"] + cumulative_offset
        new_df["volcano"]     = volcano
        all_dfs.append(new_df)

    if not all_dfs:
        raise ValueError(
            f"No se encontraron datos disponibles para el volcán '{volcano}' "
            f"en los años: {years}."
        )

    result = pd.concat(all_dfs, ignore_index=True)
    result = result.sort_values(["date", "lat", "lon"]).reset_index(drop=True)

    logger.info(
        "[Parquet] Serie concatenada '%s': %d años, %d filas, "
        "rango %s → %s",
        volcano,
        len(all_dfs),
        len(result),
        result["date"].min().strftime("%Y-%m-%d"),
        result["date"].max().strftime("%Y-%m-%d"),
    )
    return result


def list_available_years(volcano: str) -> list[int]:
    """
    Retorna la lista de años disponibles en Parquet para un volcán dado,
    ordenados cronológicamente.
    """
    parquet_dir = _ensure_parquet_dir()
    pattern = f"timeseries_{volcano}_*.parquet"
    files = sorted(parquet_dir.glob(pattern))
    years = []
    for f in files:
        # Extraer el año del nombre: timeseries_santa_ana_2024.parquet → 2024
        try:
            year = int(f.stem.split("_")[-1])
            years.append(year)
        except ValueError:
            continue
    return years


def list_all_available() -> dict[str, list[int]]:
    """
    Retorna un diccionario {volcán: [años disponibles]} para todos los volcanes.
    Útil para el endpoint de la API que informa al frontend qué datos existen.
    """
    result = {}
    for volcano_key in list(VOLCANOES.keys()) + ["national"]:
        years = list_available_years(volcano_key)
        if years:
            result[volcano_key] = years
    return result


# Exportación desde MintPy → Parquet
def save_from_mintpy_timeseries_h5(
    h5_path: Path,
    volcano: str,
    year: int,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    overwrite: bool = False,
) -> Path:
    """
    Extrae la serie temporal de un archivo timeseries.h5 de MintPy y la guarda
    en formato Parquet, recortando a la zona del volcán especificado.

    Args:
        h5_path:   Ruta al archivo timeseries.h5 generado por MintPy.
        volcano:   Identificador del volcán (ej. "santa_ana").
        year:      Año de la pila de procesamiento.
        lat_min / lat_max / lon_min / lon_max: Bounding box del área de recorte.
        overwrite: Si True, sobreescribe el Parquet si ya existe.

    Returns:
        Path al archivo Parquet generado.
    """
    try:
        import h5py
    except ImportError:
        raise ImportError("h5py es necesario para leer archivos timeseries.h5 de MintPy.")

    logger.info(
        "[Parquet] Extrayendo timeseries.h5 → Parquet para '%s' año %d...",
        volcano, year,
    )

    with h5py.File(h5_path, "r") as f:
        if "timeseries" not in f or "date" not in f:
            raise ValueError(
                f"El archivo {h5_path.name} no contiene los datasets "
                "'timeseries' y 'date' requeridos."
            )

        dates = [
            datetime.strptime(d.decode("utf-8"), "%Y%m%d").replace(tzinfo=timezone.utc)
            for d in f["date"][:]
        ]

        # Leer coordenadas geográficas desde los atributos del HDF5
        attrs = dict(f["timeseries"].attrs)
        n_rows = f["timeseries"].shape[1]
        n_cols = f["timeseries"].shape[2]

        # Reconstruir malla lat/lon desde los metadatos de MintPy
        lat_first  = float(attrs.get("Y_FIRST",   lat_max))
        lon_first  = float(attrs.get("X_FIRST",   lon_min))
        lat_step   = float(attrs.get("Y_STEP",    (lat_min - lat_max) / n_rows))
        lon_step   = float(attrs.get("X_STEP",    (lon_max - lon_min) / n_cols))

        lats = lat_first + np.arange(n_rows) * lat_step
        lons = lon_first + np.arange(n_cols) * lon_step

        # Máscara de recorte al bounding box del volcán
        lat_mask = (lats >= lat_min) & (lats <= lat_max)
        lon_mask = (lons >= lon_min) & (lons <= lon_max)

        if not lat_mask.any() or not lon_mask.any():
            logger.warning(
                "[Parquet] El bounding box [%.4f, %.4f, %.4f, %.4f] no intersecta "
                "con los datos del HDF5. Usando todos los píxeles disponibles.",
                lat_min, lon_min, lat_max, lon_max,
            )
            lat_mask = np.ones(n_rows, dtype=bool)
            lon_mask = np.ones(n_cols, dtype=bool)

        lats_crop = lats[lat_mask]
        lons_crop = lons[lon_mask]
        ts_data   = f["timeseries"][:]   # shape: (n_dates, n_rows, n_cols)

    # Construir DataFrame (long format)
    records = []
    for t_idx, date in enumerate(dates):
        slice_data = ts_data[t_idx][np.ix_(lat_mask, lon_mask)] * 1000.0  # m → mm
        for r_idx, lat in enumerate(lats_crop):
            for c_idx, lon in enumerate(lons_crop):
                val = slice_data[r_idx, c_idx]
                if np.isfinite(val):
                    records.append({
                        "lat":         round(float(lat), 6),
                        "lon":         round(float(lon), 6),
                        "date":        date,
                        "deformation": round(float(val), 4),
                        "volcano":     volcano,
                        "year":        year,
                    })

    if not records:
        raise ValueError(
            f"No se extrajeron puntos válidos del HDF5 para '{volcano}' año {year}."
        )

    df = pd.DataFrame(records)
    logger.info(
        "[Parquet] Extraídos %d registros válidos de %d fechas × %d×%d píxeles.",
        len(df), len(dates), lat_mask.sum(), lon_mask.sum(),
    )

    return save_timeseries(df, volcano, year, overwrite=overwrite)
