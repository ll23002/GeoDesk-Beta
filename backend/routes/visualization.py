import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
import pandas as pd

from utils.timeseries_parquet import (
    VOLCANOES,
    concatenate_timeseries,
    list_all_available,
    list_available_years,
    load_timeseries,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/viz", tags=["Visualization"])

BASE_DIR     = Path(__file__).resolve().parent.parent
RESULTS_DIR  = BASE_DIR / "mintpy_results"
PREVIEWS_DIR = RESULTS_DIR / "phase_previews"


PIPELINE_STEPS = [
    {
        "id":          "raw_phase",
        "order":       1,
        "label":       "Fase InSAR Original",
        "description": "Fase interferométrica cruda en radianes. Contiene la señal de deformación mezclada con ruido atmosférico, rampas orbitales y artefactos topográficos.",
        "unit":        "radianes",
        "file_key":    "raw_url",
        "color_scheme":"RdYlGn_r",
        "available_from": "HyP3",
    },
    {
        "id":          "corrected_phase",
        "order":       2,
        "label":       "Fase Corregida (MintPy)",
        "description": "Fase tras aplicar correcciones de rampa orbital y referencia espacial. La señal de deformación es más visible pero aún contiene señal troposférica.",
        "unit":        "radianes",
        "file_key":    "corrected_url",
        "color_scheme":"RdYlGn_r",
        "available_from": "MintPy",
    },
    {
        "id":          "pre_pyaps",
        "order":       3,
        "label":       "Velocidad Pre-ERA5",
        "description": "Mapa de velocidad de deformación antes de aplicar la corrección troposférica con PyAPS + ERA5. Puede mostrar patrones artificiales vinculados a la topografía y estaciones húmedas.",
        "unit":        "mm/año",
        "file_key":    "quiver_up",
        "color_scheme":"RdBu_r",
        "available_from": "MintPy (pre-ERA5)",
    },
    {
        "id":          "post_pyaps",
        "order":       4,
        "label":       "Velocidad Post-ERA5",
        "description": "Mapa final de velocidad de deformación tras corrección troposférica con ERA5 de ECMWF. Esta es la señal tectónica/volcánica más limpia disponible.",
        "unit":        "mm/año",
        "file_key":    "quiver_up",
        "color_scheme":"RdBu_r",
        "available_from": "MintPy + PyAPS/ERA5",
    },
    {
        "id":          "timeseries",
        "order":       5,
        "label":       "Serie Temporal de Deformación",
        "description": "Evolución acumulada de la deformación en milímetros a lo largo del tiempo. Múltiples años se encadenan con offset matemático para continuidad. Permite identificar tendencias, aceleraciones y anomalías estacionales.",
        "unit":        "mm (acumulado)",
        "file_key":    None,
        "color_scheme": None,
        "available_from": "Parquet histórico + MintPy actual",
    },
    {
        "id":          "multi_year",
        "order":       6,
        "label":       "Comparativa Multi-Anual",
        "description": "Vista comparativa de los resultados finales de cada año disponible. Permite observar la evolución interanual de la actividad volcánica y tasas de deformación.",
        "unit":        "mm/año",
        "file_key":    None,
        "color_scheme": "RdBu_r",
        "available_from": "Parquet histórico",
    },
]


@router.get("/pipeline/steps")
def get_pipeline_steps():
    """
    Retorna la lista de pasos del pipeline con su orden, descripción y
    disponibilidad. El frontend usa esto para construir el stepper visual.
    """
    # Verificar cuáles pasos tienen archivos disponibles
    steps_with_status = []
    for step in PIPELINE_STEPS:
        step_data = dict(step)

        # Verificar si hay previsualizaciones disponibles
        if step["id"] in ("raw_phase", "corrected_phase"):
            previews = list(PREVIEWS_DIR.glob("*.png")) if PREVIEWS_DIR.exists() else []
            step_data["has_data"] = len(previews) > 0
        elif step["id"] in ("pre_pyaps", "post_pyaps"):
            quiver_up = RESULTS_DIR / "quiver_up.png"
            step_data["has_data"] = quiver_up.exists()
        elif step["id"] == "timeseries":
            available = list_all_available()
            step_data["has_data"] = bool(available)
            step_data["available_years"] = available
        elif step["id"] == "multi_year":
            available = list_all_available()
            step_data["has_data"] = any(len(v) > 0 for v in available.values())
            step_data["available_years"] = available
        else:
            step_data["has_data"] = False

        steps_with_status.append(step_data)

    return {"ok": True, "steps": steps_with_status}


@router.get("/pipeline/preview/{step_id}")
def get_pipeline_preview(step_id: str):
    """
    Retorna la imagen PNG de previsualización de un paso específico del pipeline.
    Para fases individuales, se retorna la primera previsualización disponible.
    Para mapas de velocidad, se retorna el quiver_up o quiver_ew.
    """
    step = next((s for s in PIPELINE_STEPS if s["id"] == step_id), None)
    if not step:
        raise HTTPException(status_code=404, detail=f"Paso '{step_id}' no encontrado.")

    if step_id in ("raw_phase", "corrected_phase"):
        previews = sorted(PREVIEWS_DIR.glob("*.png")) if PREVIEWS_DIR.exists() else []
        if not previews:
            raise HTTPException(status_code=404, detail="No hay previsualizaciones de fase disponibles.")
        # Seleccionar raw o corrected según el paso
        keyword = "raw" if step_id == "raw_phase" else "corrected"
        matching = [p for p in previews if keyword in p.name.lower()]
        target = matching[0] if matching else previews[0]
        return FileResponse(target, media_type="image/png")

    elif step_id in ("pre_pyaps", "post_pyaps"):
        quiver = RESULTS_DIR / "quiver_up.png"
        if not quiver.exists():
            raise HTTPException(status_code=404, detail="Mapa de velocidad no disponible aún.")
        return FileResponse(quiver, media_type="image/png")

    raise HTTPException(
        status_code=400,
        detail=f"El paso '{step_id}' no tiene previsualización de imagen estática.",
    )


@router.get("/timeseries/{volcano}")
def get_timeseries(
    volcano: str,
    years: Optional[str] = Query(None, description="Años históricos separados por coma, ej: 2024,2025"),
    include_current: bool = Query(True, description="Incluir el año actual del resultado en memoria"),
):
    """
    Retorna la serie temporal de deformación concatenada para un volcán.

    Combina los años históricos (Parquet) con el resultado del año en curso
    (CSV en disco si está disponible) aplicando el offset matemático de
    continuidad entre años.

    Query params:
      - years: lista de años históricos a incluir (ej: ?years=2024,2025)
               Si se omite, se usan todos los años disponibles en Parquet.
      - include_current: si incluir el resultado del año actual (default: true)

    Returns:
      JSON con lista de puntos: [{date, deformation, lat, lon, year}]
    """
    if volcano not in VOLCANOES and volcano != "national":
        raise HTTPException(
            status_code=404,
            detail=f"Volcán '{volcano}' no reconocido. Opciones: {list(VOLCANOES.keys())} + 'national'",
        )

    # Resolver años
    if years:
        try:
            historical_years = [int(y.strip()) for y in years.split(",")]
        except ValueError:
            raise HTTPException(status_code=400, detail="El parámetro 'years' debe ser una lista de enteros separados por coma.")
    else:
        historical_years = list_available_years(volcano)

    if not historical_years and not include_current:
        raise HTTPException(
            status_code=404,
            detail=f"No hay datos históricos disponibles para '{volcano}'.",
        )

    # Cargar resultado actual desde CSV si existe y se solicita
    current_df: Optional[pd.DataFrame] = None
    if include_current:
        csv_path = RESULTS_DIR / "velocidad_deformacion.csv"
        if csv_path.exists():
            try:
                raw = pd.read_csv(csv_path)
                # El CSV de MintPy tiene velocidades, no serie temporal acumulada.
                # Estimamos la acumulada como velocidad × tiempo desde inicio del año.
                if "velocidad_mm_yr" in raw.columns and "lat" in raw.columns:
                    import datetime
                    current_year = datetime.date.today().year
                    # Usar la velocidad promedio como representación del año actual
                    current_df = raw[["lat", "lon", "velocidad_mm_yr"]].copy()
                    current_df = current_df.rename(columns={"velocidad_mm_yr": "deformation"})
                    current_df["date"] = pd.Timestamp(f"{current_year}-06-15")  # Midpoint del año
                    current_df["year"] = current_year
                    current_df["volcano"] = volcano
            except Exception as exc:
                logger.warning("[Viz] No se pudo cargar CSV actual: %s", exc)

    # Concatenar con offset
    try:
        if historical_years:
            df = concatenate_timeseries(volcano, historical_years, current_df)
        elif current_df is not None:
            df = current_df
        else:
            raise HTTPException(status_code=404, detail="No hay datos disponibles.")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Serializar a JSON (limitamos a 5000 puntos para no saturar el frontend)
    MAX_POINTS = 5000
    if len(df) > MAX_POINTS:
        step = max(1, len(df) // MAX_POINTS)
        df = df.iloc[::step].head(MAX_POINTS)

    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    records = df[["date", "deformation", "lat", "lon", "year"]].to_dict(orient="records")

    return {
        "ok":           True,
        "volcano":      volcano,
        "n_points":     len(records),
        "historical_years": historical_years,
        "records":      records,
    }


@router.get("/timeseries/available")
def get_available_timeseries():
    """
    Retorna qué volcanes y años tienen datos históricos en Parquet.
    El frontend usa esto para saber qué pestañas/filtros mostrar.
    """
    available = list_all_available()
    return {
        "ok":        True,
        "available": available,
        "total_years": sum(len(y) for y in available.values()),
    }


@router.get("/volcanoes")
def get_volcanoes():
    """
    Retorna las coordenadas y metadatos de los 5 volcanes de monitoreo permanente.
    """
    result = []
    for key, meta in VOLCANOES.items():
        years = list_available_years(key)
        result.append({
            "id":           key,
            "name":         meta["name"],
            "lat":          meta["lat"],
            "lon":          meta["lon"],
            "bbox":         meta["bbox"],
            "years_available": years,
            "has_data":     len(years) > 0,
        })
    return {"ok": True, "volcanoes": result}


@router.get("/results/latest")
def get_latest_results():
    """
    Retorna el resumen del último procesamiento disponible (latest_results.json).
    Incluye estadísticas, modo de procesamiento y timestamps.
    Disponible para usuarios públicos (no requiere autenticación).
    """
    results_file = RESULTS_DIR / "latest_results.json"
    if not results_file.exists():
        return {
            "ok":      False,
            "message": "No hay resultados de procesamiento disponibles aún.",
            "results": None,
        }
    try:
        import json
        data = json.loads(results_file.read_text(encoding="utf-8"))
        return {"ok": True, "results": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error leyendo resultados: {exc}")


@router.get("/results/images")
def list_result_images():
    """
    Lista las imágenes de resultados disponibles (quiver, mapas de velocidad).
    Retorna URLs relativas para que el frontend las muestre directamente.
    """
    images = {}
    for fname, key in [
        ("quiver_up.png",  "velocity_vertical"),
        ("quiver_ew.png",  "velocity_eastwest"),
    ]:
        path = RESULTS_DIR / fname
        if path.exists():
            images[key] = f"/api/mintpy/result_image/{fname}"

    previews = []
    if PREVIEWS_DIR.exists():
        for p in sorted(PREVIEWS_DIR.glob("*.png"))[:10]:  # Máximo 10 previsualizaciones
            previews.append({
                "filename": p.name,
                "url":      f"/api/mintpy/phase_preview/{p.name}",
            })

    return {
        "ok":      True,
        "images":  images,
        "previews": previews,
    }
