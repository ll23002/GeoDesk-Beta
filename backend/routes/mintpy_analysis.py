import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
import pyproj
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import warnings

from PIL.ImageOps import scale
from dotenv import load_dotenv
load_dotenv()
import h5py
from mintpy.smallbaselineApp import TimeSeriesAnalysis
import mintpy.ifgram_inversion as inv
import numpy as np
import pandas as pd
import rasterio
from fastapi import APIRouter, File, Header, HTTPException, UploadFile, Form
from fastapi.responses import FileResponse
import rasterio.warp
from rasterio.windows import Window
from scipy.interpolate import griddata

# Bugfix: MintPy's calc_inv_quality returns a 1D array of size 1 for single-pixel inversions,
# which crashes NumPy 1.24+ with "ValueError: setting an array element with a sequence."
# We monkey-patch the estimate_timeseries function globally here to prevent multi-patching
# across sequential API calls.
from mintpy.ifgram_inversion import estimate_timeseries as original_estimate_timeseries
def patched_estimate_timeseries(*args, **kwargs):
    tsi, inv_quali, num_obsi = original_estimate_timeseries(*args, **kwargs)
    if getattr(inv_quali, "size", 0) == 1:
        inv_quali = float(inv_quali.item())
    return tsi, inv_quali, num_obsi

inv.estimate_timeseries = patched_estimate_timeseries

router = APIRouter(prefix="/api/mintpy", tags=["MintPy"])

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "mintpy_results"
RESULTS_DIR.mkdir(exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "latest_results.json"
CSV_FILE     = RESULTS_DIR / "velocidad_deformacion.csv"
XLSX_FILE    = RESULTS_DIR / "resumen_interferogramas.xlsx"
CSV_FILE_HYP3  = RESULTS_DIR / "velocidad_deformacion_hyp3.csv"
XLSX_FILE_HYP3 = RESULTS_DIR / "resumen_interferogramas_hyp3.xlsx"

MIN_INTERFEROGRAMS = 3
DATE_RE = re.compile(r"(20\d{6})T")

SESSION_BASE = Path("/tmp/mintpy_sessions")
SESSION_BASE.mkdir(parents=True, exist_ok=True)

def generate_phase_previews(active_work_dir: Path, active_zip_dir: Path, igram_meta_list: List[dict]):
    if not igram_meta_list:
        logging.warning("No interferogram metadata list provided for phase previews.")
        return None
    
    # Pick the first pair as representative
    first_pair = igram_meta_list[0]
    d1_str = first_pair["date1"].replace("-", "")
    d2_str = first_pair["date2"].replace("-", "")
    
    # Locate the extracted product directory in active_zip_dir
    target_folder = None
    if active_zip_dir.exists():
        for folder in active_zip_dir.iterdir():
            if folder.is_dir() and d1_str in folder.name and d2_str in folder.name:
                target_folder = folder
                break
                
        if not target_folder:
            # Fallback: search for any subfolder
            subfolders = [d for d in active_zip_dir.iterdir() if d.is_dir()]
            if subfolders:
                target_folder = subfolders[0]
                
    if not target_folder or not target_folder.exists():
        logging.warning("Target folder containing extracted TIF/PNG files not found.")
        return None
        
    # Search for color_phase.png and unw_phase.png inside target_folder
    color_phase_file = None
    unw_phase_file = None
    for f in target_folder.iterdir():
        if f.is_file():
            name = f.name.lower()
            if name.endswith("color_phase.png"):
                color_phase_file = f
            elif name.endswith("unw_phase.png"):
                unw_phase_file = f
            
    previews_dir = RESULTS_DIR / "phase_previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    
    preview_info = {
        "pair_label": f"{first_pair['date1']} → {first_pair['date2']}",
        "wrapped_url": None,
        "unwrapped_url": None,
        "corrected_url": None
    }
    
    # Copy wrapped phase
    if color_phase_file and color_phase_file.exists():
        wrapped_dest = previews_dir / f"wrapped_{d1_str}_{d2_str}.png"
        try:
            shutil.copy(color_phase_file, wrapped_dest)
            preview_info["wrapped_url"] = f"/mintpy_results/phase_previews/{wrapped_dest.name}"
        except Exception as err:
            logging.warning(f"Error copying wrapped phase file: {err}")
            
    # Copy unwrapped phase
    if unw_phase_file and unw_phase_file.exists():
        unwrapped_dest = previews_dir / f"unwrapped_{d1_str}_{d2_str}.png"
        try:
            shutil.copy(unw_phase_file, unwrapped_dest)
            preview_info["unwrapped_url"] = f"/mintpy_results/phase_previews/{unwrapped_dest.name}"
        except Exception as err:
            logging.warning(f"Error copying unwrapped phase file: {err}")
            
    # Render corrected phase from timeseries.h5
    timeseries_h5 = active_work_dir / "timeseries.h5"
    if timeseries_h5.exists():
        try:
            with h5py.File(timeseries_h5, "r") as f:
                if "timeseries" in f and "date" in f:
                    dates_array = [d.decode('utf-8') for d in f["date"][:]]
                    idx1 = None
                    idx2 = None
                    for idx, d_val in enumerate(dates_array):
                        if d_val == d1_str:
                            idx1 = idx
                        elif d_val == d2_str:
                            idx2 = idx
                            
                    # Fallbacks if index not found
                    if idx1 is None:
                        idx1 = 0
                    if idx2 is None:
                        idx2 = len(dates_array) - 1
                        
                    if idx1 < len(dates_array) and idx2 < len(dates_array):
                        ts_data = f["timeseries"]
                        disp1 = ts_data[idx1]
                        disp2 = ts_data[idx2]
                        # Difference in mm
                        corrected_disp = (disp2 - disp1) * 1000.0
                        
                        # Apply mask: where infinite, otherwise NaN
                        corrected_disp = np.where(np.isfinite(corrected_disp) & (corrected_disp != 0.0), corrected_disp, np.nan)
                        
                        import matplotlib
                        matplotlib.use("Agg")
                        import matplotlib.pyplot as plt
                        
                        fig, ax = plt.subplots(figsize=(8, 6))
                        # We use seismic colormap (red is subsidence, blue is uplift, white is stable)
                        vmax_val = np.nanpercentile(np.abs(corrected_disp), 98) if np.any(np.isfinite(corrected_disp)) else 15.0
                        if np.isnan(vmax_val) or vmax_val == 0:
                            vmax_val = 15.0
                        
                        im = ax.imshow(corrected_disp, cmap="seismic", vmin=-vmax_val, vmax=vmax_val)
                        ax.set_title(f"Deformación Corregida (SBAS MintPy)\n{first_pair['date1']} a {first_pair['date2']}", fontsize=12, color="white", pad=12)
                        ax.axis("off")
                        
                        fig.patch.set_facecolor('#0f172a')
                        ax.set_facecolor('#0f172a')
                        
                        cbar = fig.colorbar(im, ax=ax, orientation='horizontal', pad=0.08, shrink=0.7)
                        cbar.set_label('Deformación en Línea de Visión (mm)', color='white', fontsize=10, labelpad=8)
                        cbar.ax.tick_params(labelsize=8, labelcolor='white')
                        
                        corrected_dest = previews_dir / f"corrected_{d1_str}_{d2_str}.png"
                        plt.savefig(corrected_dest, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
                        plt.close(fig)
                        
                        preview_info["corrected_url"] = f"/mintpy_results/phase_previews/{corrected_dest.name}"
        except Exception as err:
            logging.warning(f"Error rendering corrected phase preview: {err}")
            
    return preview_info

@router.post("/upload_file")
async def upload_file(session_id: str = Form(...), file: UploadFile = File(...)):
    if not session_id:
        raise HTTPException(status_code=400, detail="Falta session_id.")
        
    try:
        import time
        now = time.time()
        for session_path in SESSION_BASE.iterdir():
            if session_path.is_dir() and session_path.name != session_id:
                if now - session_path.stat().st_mtime > 86400:
                    shutil.rmtree(session_path, ignore_errors=True)
    except Exception as e:
        logging.warning(f"Error limpiando sesiones antiguas: {e}")

    session_dir = SESSION_BASE / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    file_path = session_dir / (file.filename or "interferogram.zip")
    with file_path.open("wb") as f:
        # probablemente lo cambie para el servidor
        while chunk := await file.read(8192 * 1024):  # 8MB chunk buffer
            f.write(chunk)
    return {"success": True, "filename": file.filename}

@router.post("/clear_session")
async def clear_session(session_id: str = Form(...)):
    if session_id:
        session_dir = SESSION_BASE / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
    return {"success": True}

@router.get("/system_status")
async def system_status():
    import psutil
    try:
        svmem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        return {
            "success": True,
            "ram": {
                "total_gb": round(svmem.total / (1024**3), 2),
                "available_gb": round(svmem.available / (1024**3), 2),
                "used_gb": round(svmem.used / (1024**3), 2),
                "percent": svmem.percent
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "percent": disk.percent
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def generate_quiver_plots(results_list):
    if not results_list:
        return
    df = pd.DataFrame(results_list)
    if "vel_ew_mm_yr" not in df.columns or "vel_up_mm_yr" not in df.columns:
        return

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
        import matplotlib.cm as cm
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        import cartopy.io.img_tiles as cimgt
        import math
        has_cartopy = True
    except ImportError:
        has_cartopy = False

    # Cap to exactly 75 points, evenly spaced across the dataset
    n_target = 75
    n_total = len(df)
    if n_total > n_target:
        step = max(1, n_total // n_target)
        df_sub = df.iloc[::step].head(n_target)
    else:
        df_sub = df

    lons = df_sub["lon"].values
    lats = df_sub["lat"].values

    # ESRI World Imagery — highest-resolution base map
    class EsriImagery(cimgt.GoogleWTS):
        def _image_url(self, tile):
            x, y, z = tile
            return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

    # ESRI World Shaded Relief — greyscale terrain fallback
    class EsriShadedRelief(cimgt.GoogleWTS):
        def _image_url(self, tile):
            x, y, z = tile
            return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}"

    for mode in ["UP", "EW"]:
        fig, ax = plt.subplots(figsize=(10, 8),
                               dpi=150,
                               subplot_kw={'projection': ccrs.PlateCarree()} if has_cartopy else {})

        if has_cartopy:
            d_lon_full = max(lons.max() - lons.min(), 0.01)
            d_lat_full = max(lats.max() - lats.min(), 0.01)
            d_lon = d_lon_full * 0.15
            d_lat = d_lat_full * 0.15
            extent = [lons.min() - d_lon, lons.max() + d_lon,
                      lats.min() - d_lat, lats.max() + d_lat]
            ax.set_extent(extent, crs=ccrs.PlateCarree())

            # Dynamic zoom: ensure at least ~10 tiles across the narrowest extent
            span = min(d_lon_full, d_lat_full)
            zoom = min(14, max(10, int(math.log2(3600 / max(span, 0.001))) + 1))
            
            # Try providers in order of preference for scientific maps
            class EsriShadedRelief(cimgt.GoogleWTS):
                def _image_url(self, tile):
                    x, y, z = tile
                    return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}"

            class OpenTopoMap(cimgt.GoogleWTS):
                def _image_url(self, tile):
                    x, y, z = tile
                    return f"https://a.tile.opentopomap.org/{z}/{x}/{y}.png"

            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(ax.add_image, OpenTopoMap(), zoom)
                    future.result(timeout=15)
            except concurrent.futures.TimeoutError:
                logging.warning("OpenTopoMap tiles timed out (>15s) — using local features as fallback")
                ax.add_feature(cfeature.LAND, facecolor="#d4cfc9")
                ax.add_feature(cfeature.OCEAN, facecolor="#a8d8ea")
            except Exception as e:
                logging.warning(f"OpenTopoMap tiles failed: {e}")
                ax.add_feature(cfeature.LAND, facecolor="#d4cfc9")
                ax.add_feature(cfeature.OCEAN, facecolor="#a8d8ea")

            ax.add_feature(cfeature.COASTLINE, linewidth=0.6, edgecolor="#333")
            ax.add_feature(cfeature.BORDERS, linewidth=0.5, linestyle=":", edgecolor="#555")
            ax.gridlines(draw_labels=True, linewidth=0.4, color="gray",
                         alpha=0.6, linestyle="--", x_inline=False, y_inline=False)
            transform = ccrs.PlateCarree()
        else:
            transform = None

        if mode == "UP":
            U = np.zeros_like(lons)
            V = df_sub["vel_up_mm_yr"].values
            color_vals = V
            title = "Deformación Vertical (mm/a)"
            save_path = RESULTS_DIR / "quiver_up.png"
        else:
            U = df_sub["vel_ew_mm_yr"].values
            V = np.zeros_like(lons)
            color_vals = U
            title = "Deformación Este-Oeste (mm/a)"
            save_path = RESULTS_DIR / "quiver_ew.png"

        # Clip colormap to p5-p95 so near-zero values get saturated color
        vmax = max(abs(float(np.nanmin(color_vals))), abs(float(np.nanmax(color_vals))))
        if vmax == 0:
            vmax = 1
        p5  = float(np.nanpercentile(color_vals, 5))
        p95 = float(np.nanpercentile(color_vals, 95))
        clim = max(abs(p5), abs(p95))
        if clim == 0:
            clim = vmax
        norm = mcolors.TwoSlopeNorm(vmin=-clim, vcenter=0, vmax=clim)
        cmap = cm.get_cmap("RdBu_r")

        # Scale quiver so the longest arrow spans ~10% of the map's lon extent
        arrow_scale = (clim / (d_lon_full * 0.10)) if d_lon_full > 0 else clim

        kw = dict(cmap=cmap, norm=norm, scale=arrow_scale, scale_units="x",
                  pivot="tail", alpha=1.0, zorder=5, width=0.0025,
                  edgecolors="black", linewidths=0.3)
        if has_cartopy:
            ax.scatter(lons, lats, color="white", s=25, zorder=4, transform=transform, linewidths=0)
            ax.scatter(lons, lats, color="k", s=12, zorder=4, alpha=0.9, transform=transform)
            q = ax.quiver(lons, lats, U, V, color_vals, transform=transform, **kw)
        else:
            ax.scatter(lons, lats, color="white", s=25, zorder=4, linewidths=0)
            ax.scatter(lons, lats, color="k", s=12, zorder=4, alpha=0.9)
            q = ax.quiver(lons, lats, U, V, color_vals, **kw)

        sm = cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        plt.colorbar(sm, ax=ax, label="Velocidad (mm/a)", orientation="horizontal",
                     fraction=0.046, pad=0.06)
        ax.set_title(title, fontsize=13, fontweight="bold", pad=10)

        plt.savefig(save_path, bbox_inches="tight", dpi=150)
        plt.close(fig)


def _has_hyp3_displacement(zip_dir: Path) -> bool:
    """Returns True if at least one interferogram folder contains a los_disp.tif."""
    return bool(list(zip_dir.glob("*/*_los_disp.tif")))


# Maximum pixels per dimension for HyP3 velocity computation.
# Full-res HyP3 products can be 3500x2500 = 8.75M px which requires
# ~11 GB RAM for the weighted-median stack. We cap the reference grid
# at this many pixels total to keep memory under ~200 MB.
_HYPP3_MAX_PIXELS = 400 * 600  # ~240K pixels — matches MintPy output resolution


def _compute_hyp3_velocity(
    zip_dir: Path,
    igram_meta: list,
    mode: str,
    crop_lat_min: float = None,
    crop_lat_max: float = None,
    crop_lon_min: float = None,
    crop_lon_max: float = None,
    ref_work_dir: Path = None,
) -> dict:
    """Compute deformation velocity maps directly from HyP3 los_disp.tif/vert_disp.tif files.

    For each interferogram, converts cumulative displacement (metres) to velocity (mm/yr)
    using the date interval. Then computes a weighted median (weight = 1/days) across all
    interferograms on a common grid. Works for LOS, 2D, LOS ASC, and LOS DESC modes.

    Returns a dict with keys: hyp3_has_data, stats, sample, igram_stats.
    NOTE: Files are automatically downsampled to _HYPP3_MAX_PIXELS if they are larger,
    to prevent OOM conditions with full-resolution HyP3 products (3500x2500+).
    """
    from rasterio.warp import reproject, Resampling
    import math

    logging.info("[HyP3] _compute_hyp3_velocity START — mode=%s zip_dir=%s", mode, zip_dir)

    los_files = sorted(zip_dir.glob("*/*_los_disp.tif"))
    logging.info("[HyP3] Found %d los_disp.tif files", len(los_files))
    if not los_files:
        return {"hyp3_has_data": False}

    # Build reference grid.
    # Priority: (1) MintPy output grid (regular WGS84 — identical visual pattern),
    #           (2) downsampled native UTM grid from los_disp.tif (fallback).
    # Using MintPy's grid ensures HyP3 points are sampled at the exact same
    # lat/lon positions as the SBAS result, making side-by-side comparison valid.
    import rasterio.crs as _rcrs
    from rasterio.transform import from_bounds as _from_bounds

    _built_from_mintpy = False
    if ref_work_dir is not None:
        geo_h5  = ref_work_dir / "inputs" / "geometryGeo.h5"
        vel_h5_ = ref_work_dir / "velocity.h5"
        try:
            if geo_h5.exists():
                with h5py.File(str(geo_h5), "r") as gf:
                    _attrs = dict(gf.attrs)
                    if "latitude" in gf and "longitude" in gf:
                        lat_grid_h = gf["latitude"][:].astype(np.float64)
                        lon_grid_h = gf["longitude"][:].astype(np.float64)
                        ref_height, ref_width = lat_grid_h.shape
                        _built_from_mintpy = True
            if not _built_from_mintpy and vel_h5_.exists():
                with h5py.File(str(vel_h5_), "r") as vf:
                    _va = dict(vf.attrs)
                    _vel_shape = vf["velocity"].shape
                dlat = float(_va.get("Y_STEP", -0.001))
                dlon = float(_va.get("X_STEP",  0.001))
                lat0 = float(_va.get("Y_FIRST", 0))
                lon0 = float(_va.get("X_FIRST", 0))
                ref_height, ref_width = _vel_shape[-2], _vel_shape[-1]
                lat_arr = lat0 + np.arange(ref_height) * dlat
                lon_arr = lon0 + np.arange(ref_width)  * dlon
                lon_grid_h, lat_grid_h = np.meshgrid(lon_arr, lat_arr)
                if abs(lat0) > 90 or abs(lon0) > 180:
                    _epsg = int(_va.get("EPSG", 32616))
                    _tf = pyproj.Transformer.from_crs(_epsg, 4326, always_xy=True)
                    lon_grid_h, lat_grid_h = _tf.transform(lon_grid_h, lat_grid_h)
                _built_from_mintpy = True
        except Exception as _mint_err:
            logging.warning("[HyP3] Could not read MintPy grid from %s: %s — falling back to native grid.",
                            ref_work_dir, _mint_err)

    if _built_from_mintpy:
        # Reconstruct a regular WGS84 affine transform from the lat/lon arrays.
        # MintPy geocoded grids are regular in lat/lon (constant Y_STEP / X_STEP).
        _dlon = float(lon_grid_h[0, 1] - lon_grid_h[0, 0]) if ref_width  > 1 else 0.001
        _dlat = float(lat_grid_h[1, 0] - lat_grid_h[0, 0]) if ref_height > 1 else -0.001
        # rasterio Affine: top-left corner = center of pixel (0,0) - half pixel
        from affine import Affine
        ref_transform = Affine(
            _dlon, 0.0, lon_grid_h[0, 0] - _dlon / 2,
            0.0, _dlat, lat_grid_h[0, 0] - _dlat / 2,
        )
        ref_crs = _rcrs.CRS.from_epsg(4326)
        logging.info(
            "[HyP3] Using MintPy WGS84 grid as reference: %dx%d, dlat=%.5f dlon=%.5f",
            ref_width, ref_height, _dlat, _dlon
        )
    else:
        # Fallback: build grid from the los_disp.tif native projection (UTM) downsampled.
        with rasterio.open(str(los_files[0])) as ref_src:
            orig_w, orig_h = ref_src.width, ref_src.height
            orig_crs = ref_src.crs
            orig_nodata = ref_src.nodata

        native_pixels = orig_h * orig_w
        logging.info("[HyP3] Fallback — native size: %dx%d = %d px (EPSG:%s)",
                     orig_w, orig_h, native_pixels, orig_crs.to_epsg() if orig_crs else "?")

        if native_pixels > _HYPP3_MAX_PIXELS:
            _scale = math.sqrt(_HYPP3_MAX_PIXELS / native_pixels)
            ref_height = max(1, int(orig_h * _scale))
            ref_width  = max(1, int(orig_w  * _scale))
            logging.info("[HyP3] Downsampling to %dx%d (scale=%.3f).", ref_width, ref_height, _scale)
        else:
            ref_height, ref_width = orig_h, orig_w

        ref_crs = orig_crs
        with rasterio.open(str(los_files[0])) as ref_src:
            _x_scale = orig_w / ref_width
            _y_scale = orig_h / ref_height
            ref_transform = ref_src.transform * ref_src.transform.scale(_x_scale, _y_scale)

        # Build WGS84 coordinate arrays from the (possibly projected) transform
        col_arr = np.arange(ref_width)
        row_arr = np.arange(ref_height)
        c_grid, r_grid = np.meshgrid(col_arr, row_arr)
        lon_raw, lat_raw = ref_transform * (c_grid + 0.5, r_grid + 0.5)
        if ref_crs and ref_crs.to_epsg() != 4326:
            _tf2 = pyproj.Transformer.from_crs(ref_crs.to_epsg(), 4326, always_xy=True)
            lon_grid_h, lat_grid_h = _tf2.transform(lon_raw, lat_raw)
        else:
            lon_grid_h, lat_grid_h = lon_raw, lat_raw

    logging.info("[HyP3] Coordinate grid built: lat=[%.4f,%.4f] lon=[%.4f,%.4f]",
                 float(lat_grid_h.min()), float(lat_grid_h.max()),
                 float(lon_grid_h.min()), float(lon_grid_h.max()))

    def _read_on_ref(tif_path: Path) -> np.ndarray:
        """Read a displacement GeoTIFF resampled/downsampled onto the reference grid."""
        with rasterio.open(str(tif_path)) as src:
            nd = src.nodata
            if (src.width == ref_width and src.height == ref_height
                    and src.crs == ref_crs and src.transform == ref_transform):
                # Same grid — read directly
                data = src.read(1).astype(np.float32)
            else:
                # Different grid or resolution — reproject + downsample in one step
                data = np.zeros((ref_height, ref_width), dtype=np.float32)
                reproject(
                    source=rasterio.band(src, 1),
                    destination=data,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=ref_transform,
                    dst_crs=ref_crs,
                    resampling=Resampling.bilinear,
                    dst_nodata=np.nan,
                )
                nd = np.nan
        # Mask nodata
        if nd is not None and not (isinstance(nd, float) and np.isnan(nd)):
            data[data == nd] = np.nan
        data[data == 0.0] = np.nan  # HyP3 ocean/nodata mask
        return data

    # ------------------------------------------------------------------
    # Collect per-interferogram velocity arrays (mm/yr) and weights
    # ------------------------------------------------------------------
    # Map igram folder prefix -> days (from igram_meta)
    # Used as a fallback only if dates cannot be parsed from the folder name.
    days_map: dict[str, int] = {}
    for m in igram_meta:
        folder = m.get("extracted_folder") or m.get("filename", "").replace(".zip", "")
        days_map[folder] = m.get("days", 0)

    vel_stacks_los: list[np.ndarray] = []
    los_disp_mm_stacks: list[np.ndarray] = []   # cumulative displacement per ifgram (mm)
    vel_stacks_vert: list[np.ndarray] = []
    weights: list[float] = []
    date_pairs: list[tuple[str, str]] = []  # (d1_str, d2_str) for each accepted ifgram
    igram_stats_hyp3: list[dict] = []

    logging.info("[HyP3] Reading %d interferograms into velocity stacks...", len(los_files))
    for ifg_idx, los_f in enumerate(los_files):
        folder_name = los_f.parent.name
        logging.info("[HyP3] Processing interferogram %d/%d: %s", ifg_idx + 1, len(los_files), folder_name)

        # Derive days from the two dates embedded in the folder name (primary method).
        date_matches = DATE_RE.findall(folder_name)
        d1_str = date_matches[0] if len(date_matches) >= 1 else "?"
        d2_str = date_matches[1] if len(date_matches) >= 2 else "?"

        days = 0
        if len(date_matches) >= 2:
            try:
                from datetime import datetime as _dt
                _d1 = _dt.strptime(date_matches[0], "%Y%m%d").date()
                _d2 = _dt.strptime(date_matches[1], "%Y%m%d").date()
                days = abs((_d2 - _d1).days)
            except ValueError:
                pass

        # Fallback to igram_meta if date parsing failed
        if days <= 0:
            for key, d in days_map.items():
                if key in folder_name or folder_name in key:
                    days = d
                    break

        # Last resort: use 1 to avoid division by zero but log a warning
        if days <= 0:
            logging.warning(
                "[HyP3] No temporal interval found for %s — using 1 day as fallback.", folder_name
            )
            days = 1

        logging.info("[HyP3]   dates=%s→%s days=%d — reading los_disp...", d1_str, d2_str, days)
        try:
            los_disp = _read_on_ref(los_f)
            logging.info("[HyP3]   los_disp read OK, shape=%s valid=%.1f%%",
                         los_disp.shape, 100 * np.isfinite(los_disp).mean())
        except Exception as exc:
            logging.warning("[HyP3]   FAILED reading %s: %s", los_f, exc)
            continue

        # Convert m → mm (cumulative displacement) and mm/yr (annualised velocity)
        los_disp_mm = los_disp * 1000.0                    # mm — cumulative over the pair
        vel_los = los_disp_mm / days * 365.25              # mm/yr — annualised
        vel_stacks_los.append(vel_los)
        los_disp_mm_stacks.append(los_disp_mm)
        date_pairs.append((d1_str, d2_str))
        weights.append(1.0 / days)

        # Per-interferogram stats
        valid_vals = vel_los[np.isfinite(vel_los)].flatten()
        if len(valid_vals) > 0:
            igram_stats_hyp3.append({
                "date1": d1_str,
                "date2": d2_str,
                "label": f"{d1_str} -> {d2_str} (HyP3)",
                "mean": round(float(np.mean(valid_vals)), 2),
                "std": round(float(np.std(valid_vals)), 2),
                "max": round(float(np.max(valid_vals)), 2),
                "min": round(float(np.min(valid_vals)), 2),
            })

        # Vertical displacement — ONLY for true 2D dual-track decomposition.
        # HyP3 provides vert_disp.tif as a single-track approximation
        # (vert ≈ los / cos(incidence)) which is NOT valid in LOS-only mode
        # because it ignores horizontal deformation entirely.
        if mode == "2D":
            vert_f = los_f.parent / los_f.name.replace("_los_disp.tif", "_vert_disp.tif")
            if vert_f.exists():
                logging.info("[HyP3]   reading vert_disp (2D mode)...")
                try:
                    vert_disp = _read_on_ref(vert_f)
                    vel_vert = vert_disp * 1000.0 / days * 365.25
                    vel_stacks_vert.append(vel_vert)
                    logging.info("[HyP3]   vert_disp read OK")
                except Exception as exc:
                    logging.warning("[HyP3]   FAILED reading %s: %s", vert_f, exc)
        else:
            logging.debug("[HyP3]   mode=%s — skipping vert_disp (single-track LOS only)", mode)

    logging.info("[HyP3] All interferograms processed: %d LOS stacks, %d VERT stacks",
                 len(vel_stacks_los), len(vel_stacks_vert))
    if not vel_stacks_los:
        return {"hyp3_has_data": False}


    import time as _time
    w_arr = np.array(weights, dtype=np.float32)  # (N,)
    logging.info("[HyP3] Computing weighted median on %d stacks of shape %s...",
                 len(vel_stacks_los), vel_stacks_los[0].shape)
    _t0 = _time.time()

    def _weighted_median_stack(stack: list[np.ndarray], w: np.ndarray) -> np.ndarray:
        """Compute a weighted median across a stack of 2D arrays."""
        H, W = stack[0].shape
        stacked = np.stack(stack, axis=0)  # (N, H, W)
        # Sort along the N axis by value
        sort_idx = np.argsort(stacked, axis=0)  # (N, H, W)
        sorted_vals = np.take_along_axis(stacked, sort_idx, axis=0)
        # Broadcast 1D weights to (N, 1, 1) then tile to (N, H, W) using index order
        w_3d = w[:, np.newaxis, np.newaxis] * np.ones((1, H, W), dtype=np.float32)
        sorted_w = np.take_along_axis(w_3d, sort_idx, axis=0)
        cumw = np.cumsum(sorted_w, axis=0)
        total_w = cumw[-1:, :, :]  # (1, H, W)
        half_w = total_w / 2.0
        # Find the first index where cumulative weight >= half_w
        above = cumw >= half_w  # (N, H, W)
        idx_med = np.argmax(above, axis=0)  # (H, W)
        # Gather
        result = sorted_vals[idx_med, np.arange(H)[:, None], np.arange(W)[None, :]]
        # Pixels where all values were NaN stay NaN
        all_nan = np.all(~np.isfinite(stacked), axis=0)
        result[all_nan] = np.nan
        return result

    vel_med_los = _weighted_median_stack(vel_stacks_los, w_arr)
    logging.info("[HyP3] LOS weighted median done in %.1fs", _time.time() - _t0)

    # For the scalar map shown in UI: LOS is the main variable
    vel_primary = vel_med_los

    vel_med_vert = None
    if vel_stacks_vert and len(vel_stacks_vert) == len(vel_stacks_los):
        logging.info("[HyP3] Computing VERT weighted median...")
        vel_med_vert = _weighted_median_stack(vel_stacks_vert, w_arr)
        logging.info("[HyP3] VERT weighted median done")


    valid_mask = np.isfinite(vel_primary)

    # If the user selected an AOI crop, restrict HyP3 output to the same region.
    # MintPy already does this via mintpy.subset.lalo; here we replicate it for
    # the direct HyP3 velocity so both modes cover the same geographic extent.
    if any(v is not None for v in (crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max)):
        lat_lo = float(crop_lat_min if crop_lat_min is not None else -90)
        lat_hi = float(crop_lat_max if crop_lat_max is not None else  90)
        lon_lo = float(crop_lon_min if crop_lon_min is not None else -180)
        lon_hi = float(crop_lon_max if crop_lon_max is not None else  180)
        # Normalise: user may pass min>max if they drew the box right-to-left / bottom-to-top
        lat_lo, lat_hi = min(lat_lo, lat_hi), max(lat_lo, lat_hi)
        lon_lo, lon_hi = min(lon_lo, lon_hi), max(lon_lo, lon_hi)
        crop_mask = (
            (lat_grid_h >= lat_lo) & (lat_grid_h <= lat_hi) &
            (lon_grid_h >= lon_lo) & (lon_grid_h <= lon_hi)
        )
        valid_mask = valid_mask & crop_mask
        logging.info(
            "[HyP3] Crop applied: lat=[%.4f,%.4f] lon=[%.4f,%.4f] — %d pixels inside AOI",
            lat_lo, lat_hi, lon_lo, lon_hi, int(valid_mask.sum())
        )
    else:
        logging.info("[HyP3] No crop bounds provided — using full image extent.")

    # Apply MintPy's coherence/valid-pixel mask so HyP3 shows the exact same pixels
    # as MintPy SBAS (same diagonal-stripe SAR swath pattern, same coherent locations).
    # Without this, HyP3 fills the full rectangular crop while MintPy shows only
    # coherent pixels, making the visual patterns differ.
    if _built_from_mintpy and ref_work_dir is not None:
        _vel_h5_ref = ref_work_dir / "velocity.h5"
        if _vel_h5_ref.exists():
            try:
                with h5py.File(str(_vel_h5_ref), "r") as _vf:
                    _mintpy_vel = _vf["velocity"][:].astype(np.float32)
                # MintPy marks no-data as NaN or 0; pixels set to exactly 0 in
                # areas where InSAR had no coherent data should be excluded too.
                _mintpy_valid = np.isfinite(_mintpy_vel) & (_mintpy_vel != 0.0)
                if _mintpy_valid.shape == valid_mask.shape:
                    valid_mask = valid_mask & _mintpy_valid
                    logging.info(
                        "[HyP3] MintPy coherence mask applied: %d pixels remain (%.1f%% of grid)",
                        int(valid_mask.sum()), 100 * valid_mask.sum() / max(1, valid_mask.size)
                    )
                else:
                    logging.warning(
                        "[HyP3] MintPy velocity shape %s != HyP3 grid shape %s — skipping coherence mask.",
                        _mintpy_valid.shape, valid_mask.shape
                    )
            except Exception as _vmask_err:
                logging.warning("[HyP3] Could not read MintPy velocity for coherence mask: %s", _vmask_err)

    lats_v = lat_grid_h[valid_mask].flatten()
    lons_v = lon_grid_h[valid_mask].flatten()
    los_v = vel_primary[valid_mask].flatten()
    vert_v = vel_med_vert[valid_mask].flatten() if vel_med_vert is not None else None
    logging.info("[HyP3] Valid pixels after all masks: %d (%.1f%%)", len(los_v),
                 100 * len(los_v) / max(1, vel_primary.size))

    if len(los_v) == 0:
        logging.warning("[HyP3] No valid pixels found — returning hyp3_has_data=False")
        return {"hyp3_has_data": False}

    # Build results using vectorized numpy operations (avoids slow Python loop over 100K+ pixels)
    results_hyp3_dict: dict = {
        "lat": np.round(lats_v, 6).tolist(),
        "lon": np.round(lons_v, 6).tolist(),
        "velocidad_mm_yr": np.round(los_v, 2).tolist(),
    }
    if vert_v is not None:
        results_hyp3_dict["vel_up_mm_yr"] = np.round(vert_v, 2).tolist()

    # Build list of dicts only for the sample (the full list is too large for JSON serialization)
    # The sample is constructed directly from arrays below.
    n_pts = len(los_v)

    stats_hyp3 = {
        "min": round(float(np.nanmin(los_v)), 2),
        "max": round(float(np.nanmax(los_v)), 2),
        "mean": round(float(np.nanmean(los_v)), 2),
        "std": round(float(np.nanstd(los_v)), 2),
        "n_points": n_pts,
        "n_interferograms": len(vel_stacks_los),
    }

    if vert_v is not None:
        stats_hyp3["min_up"]  = round(float(np.nanmin(vert_v)), 2)
        stats_hyp3["max_up"]  = round(float(np.nanmax(vert_v)), 2)
        stats_hyp3["mean_up"] = round(float(np.nanmean(vert_v)), 2)
        stats_hyp3["std_up"]  = round(float(np.nanstd(vert_v)), 2)

    # Save CSV HyP3 — build DataFrame from arrays directly (much faster than list-of-dicts)
    # Include per-interferogram deformation and velocity columns to match MintPy CSV format.
    df_hyp3_data: dict = {
        "Latitud": np.round(lats_v, 6),
        "Longitud": np.round(lons_v, 6),
        "Velocidad_LOS_mm_ano": np.round(los_v, 2),
    }
    if vert_v is not None:
        df_hyp3_data["Velocidad_UP_mm_ano"] = np.round(vert_v, 2)

    # Per-interferogram columns: cumulative deformation (mm) and annualised velocity (mm/yr)
    for _i, (_d1, _d2) in enumerate(date_pairs):
        _suffix = f"{_d1}_{_d2}"
        _ifg_arr = los_disp_mm_stacks[_i][valid_mask].flatten()
        _vel_arr  = vel_stacks_los[_i][valid_mask].flatten()
        df_hyp3_data[f"Deform_HyP3_{_suffix}_mm"]     = np.round(_ifg_arr, 2)
        df_hyp3_data[f"Vel_HyP3_{_suffix}_mm_ano"]    = np.round(_vel_arr,  2)

    df_hyp3 = pd.DataFrame(df_hyp3_data)
    df_hyp3.to_csv(CSV_FILE_HYP3, index=False)

    if igram_stats_hyp3:
        df_stats_h = pd.DataFrame(igram_stats_hyp3)
        df_stats_h = df_stats_h[["label", "date1", "date2", "mean", "min", "max"]]
        df_stats_h.columns = ["Interferograma", "Fecha Inicio", "Fecha Fin",
                               "Vel. Media LOS (mm/a)", "Vel. Min LOS (mm/a)", "Vel. Max LOS (mm/a)"]
        df_stats_h.to_excel(XLSX_FILE_HYP3, index=False)
    else:
        if XLSX_FILE_HYP3.exists():
            XLSX_FILE_HYP3.unlink()

    # Build sample as list of dicts (max 1000 points) using vectorized slicing
    step_h = max(1, n_pts // 1000)
    idx_sample = np.arange(0, n_pts, step_h)[:1000]
    if vert_v is not None:
        sample_hyp3 = [
            {"lat": results_hyp3_dict["lat"][i], "lon": results_hyp3_dict["lon"][i],
             "velocidad_mm_yr": results_hyp3_dict["velocidad_mm_yr"][i],
             "vel_up_mm_yr": results_hyp3_dict["vel_up_mm_yr"][i]}
            for i in idx_sample
        ]
    else:
        sample_hyp3 = [
            {"lat": results_hyp3_dict["lat"][i], "lon": results_hyp3_dict["lon"][i],
             "velocidad_mm_yr": results_hyp3_dict["velocidad_mm_yr"][i]}
            for i in idx_sample
        ]

    return {
        "hyp3_has_data": True,
        "stats": stats_hyp3,
        "sample": sample_hyp3,
        "igram_stats": igram_stats_hyp3,
    }



def _extract_dates(filename: str):
    matches = DATE_RE.findall(filename)
    if len(matches) >= 2:
        try:
            d1 = datetime.strptime(matches[0], "%Y%m%d").date()
            d2 = datetime.strptime(matches[1], "%Y%m%d").date()
            return d1, d2
        except ValueError:
            pass
    return None, None


def _make_cfg(
    zip_dir: Path,
    ref_lat: float = None,
    ref_lon: float = None,
    crop_lat_min: float = None,
    crop_lat_max: float = None,
    crop_lon_min: float = None,
    crop_lon_max: float = None,
    has_triplets: bool = False,
    min_coherence: float = 0.6,
) -> str:
    """Generates a MintPy configuration file for SBAS processing.

    Constructs a configuration file (.cfg) with the necessary parameters
    to run the MintPy time-series analysis pipeline. The function automatically
    detects the minimum dimensions of available interferograms to ensure
    compatibility across all files.

    Args:
        zip_dir (Path): Path to the directory containing extracted interferograms.
            Expected to follow the standard HyP3 directory structure with
            subdirectories for each interferogram.

    Returns:
        str: Formatted configuration string with MintPy parameters ready for
            processing. Contains paths to input files (unwrapped phase, coherence,
            DEM, angles) and pipeline control parameters.

    Raises:
        ValueError: If zip_dir does not contain valid interferograms (.tif files).

    Warning:
        DO NOT add tabs (\t) to the returned string.
        MintPy requires whitespace characters as configuration delimiters.
        Tabs will cause parsing errors and silent failures in network inversion.

    """
    unw_pat = str(zip_dir / "*" / "*_unw_phase.tif")
    cor_pat = str(zip_dir / "*" / "*_corr.tif")
    dem_pat = str(zip_dir / "*" / "*_dem.tif")
    inc_pat = str(zip_dir / "*" / "*_lv_theta.tif")
    azi_pat = str(zip_dir / "*" / "*_lv_phi.tif")

    tifs = list(zip_dir.glob("*/*_unw_phase.tif"))
    min_h = min_w = "auto"
    subset_yx_val = "auto"
    ref_yx_val = "auto"
    
    if tifs:
        heights = []
        widths = []
        lefts, bottoms, rights, tops = [], [], [], []
        crs = None
        for t in tifs:
            try:
                with rasterio.open(str(t)) as src:
                    heights.append(src.height)
                    widths.append(src.width)
                    lefts.append(src.bounds.left)
                    bottoms.append(src.bounds.bottom)
                    rights.append(src.bounds.right)
                    tops.append(src.bounds.top)
                    if crs is None:
                        crs = src.crs
            except Exception as e:
                logging.warning(f"Error reading {t} con rasterio: {e}")
                
        if lefts:
            common_bounds = (max(lefts), max(bottoms), min(rights), min(tops))
            with rasterio.open(str(tifs[0])) as src:
                window = rasterio.windows.from_bounds(*common_bounds, transform=src.transform)
                transform = rasterio.windows.transform(window, src.transform)

            transformer = None
            if crs and crs.to_epsg() != 4326:
                transformer = pyproj.Transformer.from_crs(4326, crs.to_epsg() or 32616, always_xy=True)

            subset_lalo_val = "no"
            subset_yx_val = "auto"
            ref_lalo_val = "no"
            ref_yx_val = "auto"

            if crop_lat_min is not None and crop_lat_max is not None and crop_lon_min is not None and crop_lon_max is not None:
                subset_lalo_val = f"{crop_lat_min}:{crop_lat_max},{crop_lon_min}:{crop_lon_max}"
                subset_yx_val = "no"

            if ref_lat is not None and ref_lon is not None:
                ref_lalo_val = f"{ref_lat},{ref_lon}"
                ref_yx_val = "no"

    # Ensure the weather directory is created for persistent caching of GRIB files
    weather_dir = RESULTS_DIR / "weather"
    weather_dir.mkdir(parents=True, exist_ok=True)

    return f"""mintpy.load.processor    = hyp3
mintpy.load.unwFile      = {unw_pat}
mintpy.load.corFile      = {cor_pat}
mintpy.load.demFile      = {dem_pat}
mintpy.load.incAngleFile = {inc_pat}
mintpy.load.azAngleFile  = {azi_pat}
mintpy.subset.lalo       = {subset_lalo_val}
mintpy.subset.yx         = {subset_yx_val}
mintpy.network.coherenceBased     = yes
mintpy.network.minCoherence       = {min_coherence}
mintpy.reference.lalo             = {ref_lalo_val}
mintpy.reference.yx               = {ref_yx_val}
mintpy.troposphericDelay.method   = pyaps
mintpy.troposphericDelay.weatherModel = ERA5
mintpy.troposphericDelay.weatherDir = {weather_dir}
mintpy.deramp                     = linear
mintpy.topographicResidual        = yes
mintpy.topographicResidual.stepFuncDate = no
mintpy.unwrapError.method         = {'phase_closure' if has_triplets else 'no'}
mintpy.interferogram.filter.type  = gaussian
mintpy.interferogram.filter.wavelength = 400
mintpy.networkInversion.minTempCoh = 0.5
mintpy.compute.cluster = local
mintpy.compute.numWorker = 12
mintpy.plot = no
"""


def _get_excluded_ifgrams(work_dir: Path, igram_meta: list) -> list:
    """Compare submitted igrams against those MintPy kept in the network stack.

    MintPy selects a coherence-based network and silently drops low-coherence
    interferograms. This function detects those dropped pairs by comparing the
    date pairs in the submitted igram_meta list against the pairs stored in
    ifgramStack.h5 after load_data runs.

    Returns a list of dicts: {date1, date2, days, filename, reason}
    """
    ifgram_file = work_dir / "inputs" / "ifgramStack.h5"
    if not ifgram_file.exists():
        return []

    try:
        kept_pairs: set = set()
        with h5py.File(ifgram_file, "r") as f:
            if "date" in f:
                dates_arr = f["date"][:]
                for row in dates_arr:
                    if hasattr(row, '__len__') and len(row) >= 2:
                        d1 = row[0].decode("utf-8") if isinstance(row[0], bytes) else str(row[0])
                        d2 = row[1].decode("utf-8") if isinstance(row[1], bytes) else str(row[1])
                        # Normalize: MintPy stores as YYYYMMDD
                        kept_pairs.add((d1.replace("-", ""), d2.replace("-", "")))

        excluded = []
        for m in igram_meta:
            d1 = m["date1"].replace("-", "")
            d2 = m["date2"].replace("-", "")
            if (d1, d2) not in kept_pairs and (d2, d1) not in kept_pairs:
                excluded.append({
                    "date1": m["date1"],
                    "date2": m["date2"],
                    "days": m.get("days", 0),
                    "filename": m.get("filename", ""),
                    "reason": "Baja coherencia espacial promedio (< umbral de red)",
                })
        return excluded
    except Exception as exc:
        logging.warning("Could not detect excluded interferograms: %s", exc)
        return []


def _run_mintpy_pipeline(
    work_dir: Path,
    zip_dir: Path,
    ref_lat: float = None,
    ref_lon: float = None,
    crop_lat_min: float = None,
    crop_lat_max: float = None,
    crop_lon_min: float = None,
    crop_lon_max: float = None,
    has_triplets: bool = False,
    min_coherence: float = 0.6,
    era5_key: Optional[str] = None,
) -> bool:
    """Executes the complete MintPy SBAS time-series analysis pipeline.

    Orchestrates the full MintPy processing workflow from interferogram loading
    through deformation velocity estimation. Handles configuration generation,
    atmospheric data setup, and applies necessary patches for NumPy compatibility.

    Args:
        work_dir (Path): Working directory where MintPy outputs and logs will be
            stored. Created files include velocity.h5, interferogram stack, and
            atmospheric correction models.
        zip_dir (Path): Directory containing extracted HyP3 interferogram products.
            Should have been populated by unzipping multiple HyP3 product archives.

    Returns:
        bool: True if the phase closure correction was skipped by MintPy, False otherwise.

    Raises:
        ValueError: If the MintPy pipeline fails during any processing step. The
            exception message includes the last 3000 characters of the log file
            for debugging. Specific error conditions include:
            - Network inversion failures
            - NumPy array dimension mismatches
            - MintPy SystemExit with non-zero code
            - Missing or incompatible interferogram data


    Note:
        - Executes 6 sequential processing steps:
          1. load_data: Load interferograms and associated data
          2. reference_point: Establish reference point for unwrapped phase
          3. invert_network: Invert interferogram network for time series
          4. correct_troposphere: Apply ERA5-based atmospheric delay correction
          5. deramp: Remove linear phase ramps from interferograms
          6. velocity: Calculate deformation velocities
    """
    warnings.filterwarnings("ignore")
    skipped_phase_closure = False

    os.environ["MPLBACKEND"] = "Agg"
    try:
        import matplotlib
        matplotlib.use("Agg")
    except Exception:
        pass

    log_path = work_dir / "mintpy_run.log"
    mintpy_logger = logging.getLogger("mintpy")
    fh = logging.FileHandler(log_path)
    fh.setLevel(logging.DEBUG)
    mintpy_logger.addHandler(fh)

    cfg_path = work_dir / "mintpy.cfg"
    cfg_path.write_text(_make_cfg(zip_dir, ref_lat, ref_lon, crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max, has_triplets, min_coherence))

    cds_url = os.getenv("ERA5_URL", "https://cds.climate.copernicus.eu/api")
    # Prioridad: header del request > variable de entorno
    cds_key = era5_key or os.getenv("ERA5_KEY")
    if cds_key:
        cdsapirc_path = Path.home() / ".cdsapirc"
        cdsapirc_path.write_text(f"url: {cds_url}\nkey: {cds_key}\n")

    try:
        original_cwd = os.getcwd()
        tsa = TimeSeriesAnalysis(
            customTemplateFile=str(cfg_path),
            workDir=str(work_dir),
        )
        tsa.open()

        try:
            tsa.run(steps=["load_data", "reference_point"])
        except (ValueError, Exception) as ref_err:
            if "masked out" not in str(ref_err).lower():
                raise

            logging.warning(
                "Reference point in masked area — auto-recovering from maskConnComp.h5 and avgSpatialCoh.h5"
            )
            mask_file = work_dir / "maskConnComp.h5"
            coh_file = work_dir / "avgSpatialCoh.h5"

            if not mask_file.exists() or not coh_file.exists():
                raise ValueError(
                    "El punto de referencia cae en zona enmascarada y no se encontraron "
                    "los archivos de máscara para auto-recuperarse. "
                    "Selecciona manualmente un punto de referencia diferente."
                )

            with h5py.File(mask_file, "r") as f:
                mask = f["mask"][:]        # bool array (rows, cols)
            with h5py.File(coh_file, "r") as f:
                coh = f["coherence"][:]    # float32 array (rows, cols)

            # Find best coherence pixel that is inside the valid mask
            coh_masked = np.where(mask, coh, np.nan)
            if np.all(np.isnan(coh_masked)):
                raise ValueError(
                    "No hay píxeles válidos en la máscara de MintPy. "
                    "Revisa los datos de entrada o amplía el área de estudio."
                )

            best_yx = np.unravel_index(np.nanargmax(coh_masked), coh_masked.shape)
            best_y, best_x = int(best_yx[0]), int(best_yx[1])

            geom_file = work_dir / "inputs" / "geometryGeo.h5"
            with h5py.File(geom_file, "r") as f:
                meta = dict(f.attrs)

            y_first = float(meta.get("Y_FIRST", 0))
            x_first = float(meta.get("X_FIRST", 0))
            y_step = float(meta.get("Y_STEP", 0))
            x_step = float(meta.get("X_STEP", 0))

            new_lat = round(y_first + best_y * y_step, 6)
            new_lon = round(x_first + best_x * x_step, 6)

            logging.info(
                "Auto-recovered reference point: y/x=(%d,%d) → lat/lon=(%.6f, %.6f)",
                best_y, best_x, new_lat, new_lon
            )

            # Rewrite the cfg with the corrected reference and re-run load+reference
            cfg_path.write_text(
                _make_cfg(
                    zip_dir, new_lat, new_lon,
                    crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max,
                    has_triplets, min_coherence
                )
            )
            # Restart TSA with updated config
            tsa2 = TimeSeriesAnalysis(
                customTemplateFile=str(cfg_path),
                workDir=str(work_dir),
            )
            tsa2.open()
            tsa2.run(steps=["load_data", "reference_point"])
            # Continue using tsa2 for the remaining steps
            tsa = tsa2

        if has_triplets:
            #posible error
            # HyP3 Gamma products lack 'connectComponent', which phase_closure explicitly requires.
            # We inject a dummy single-component mask to prevent KeyError.
            ifgram_file = work_dir / "inputs" / "ifgramStack.h5"
            if ifgram_file.exists():
                with h5py.File(ifgram_file, "a") as f:
                    if "connectComponent" not in f and "unwrapPhase" in f:
                        unw = f["unwrapPhase"][:]
                        # Los píxeles nulos (NaN) o con valor 0.0 (máscara de agua de Gamma) deben ser 0.
                        cc = np.where(np.isnan(unw) | (unw == 0), 0, 1).astype(np.int16)
                        f.create_dataset("connectComponent", data=cc, compression="lzf")
            
            tsa.run(steps=["correct_unwrap_error"])

            if ifgram_file.exists():
                with h5py.File(ifgram_file, "r") as f:
                    if "unwrapPhase_phaseClosure" not in f:
                        skipped_phase_closure = True
                        mintpy_cfg = work_dir / "smallbaselineApp.cfg"
                        if mintpy_cfg.exists():
                            import re
                            cfg_text = mintpy_cfg.read_text()
                            cfg_text = re.sub(
                                r"mintpy\.unwrapError\.method\s*=\s*phase_closure",
                                "mintpy.unwrapError.method = no",
                                cfg_text
                            )
                            mintpy_cfg.write_text(cfg_text)
                            if hasattr(tsa, "_template"):
                                tsa._template["mintpy.unwrapError.method"] = "no"

        try:
            tsa.run(steps=["invert_network", "correct_troposphere"])
        except Exception as tropo_err:
            timeseries_h5 = work_dir / "timeseries.h5"
            if timeseries_h5.exists():
                logging.warning(f"Tropospheric correction with PyAPS/ERA5 failed: {tropo_err}")
                logging.warning("Falling back to height_correlation tropospheric correction...")
                mintpy_cfg = work_dir / "smallbaselineApp.cfg"
                if mintpy_cfg.exists():
                    import re
                    cfg_text = mintpy_cfg.read_text()
                    cfg_text = re.sub(
                        r"mintpy\.troposphericDelay\.method\s*=\s*pyaps",
                        "mintpy.troposphericDelay.method = height_correlation",
                        cfg_text
                    )
                    mintpy_cfg.write_text(cfg_text)
                    if hasattr(tsa, "_template"):
                        tsa._template["mintpy.troposphericDelay.method"] = "height_correlation"
                    if hasattr(tsa, "template"):
                        tsa.template["mintpy.troposphericDelay.method"] = "height_correlation"
                # Re-run correct_troposphere step with height_correlation
                tsa.run(steps=["correct_troposphere"])
            else:
                raise

        tsa.run(steps=[
            "deramp",
            "correct_topography",
            "velocity",
        ])

    except SystemExit as e:
        if str(e) != "0" and e.code != 0:
            log_tail = log_path.read_text()[-5000:] if log_path.exists() else "(no log)"
            logging.error("MintPy pipeline SystemExit(%s).\n--- MintPy log tail ---\n%s\n--- end ---", e.code, log_tail)
            raise ValueError(f"MintPy pipeline exited with code {e.code}.\nLog:\n{log_tail}")
    except Exception as e:
        log_tail = log_path.read_text()[-5000:] if log_path.exists() else "(no log)"
        logging.exception("MintPy pipeline raised %s: %s\n--- MintPy log tail ---\n%s\n--- end ---",
                          type(e).__name__, e, log_tail)

        err_str = str(e).lower()
        if isinstance(e, ValueError) and "sequence" in err_str:
            raise ValueError(
                "MintPy falló en la inversión de red (network_inversion)."
            )
        if isinstance(e, ValueError) and "masked out" in err_str:
            raise ValueError(
                "El punto de referencia seleccionado cae en una zona enmascarada (sin fase en algún interferograma). "
                "Vuelve a buscar puntos semilla y selecciona uno diferente."
            )
            
        raise ValueError(f"{type(e).__name__}: {e}\nLog:\n{log_tail}")
    finally:
        os.chdir(original_cwd)
        mintpy_logger.removeHandler(fh)
        fh.close()

    return skipped_phase_closure



@router.post("/process")
async def process_interferograms(
    session_id: str = Form(...),
    ref_lat: float = Form(None),
    ref_lon: float = Form(None),
    crop_lat_min: float = Form(None),
    crop_lat_max: float = Form(None),
    crop_lon_min: float = Form(None),
    crop_lon_max: float = Form(None),
    selected_mode: str = Form(None),
    min_coherence: float = Form(0.6),
    x_era5_key: Optional[str] = Header(default=None),
):
    session_dir = SESSION_BASE / session_id
    zips = list(session_dir.glob("*.zip"))
    if len(zips) < MIN_INTERFEROGRAMS:
        raise HTTPException(status_code=400, detail=f"Se requieren al menos {MIN_INTERFEROGRAMS} interferogramas.")

    if ref_lat is not None and ref_lon is not None:
        if crop_lat_min is not None and crop_lat_max is not None and crop_lon_min is not None and crop_lon_max is not None:
            eps = 1e-5
            lat_lo = min(crop_lat_min, crop_lat_max) - eps
            lat_hi = max(crop_lat_min, crop_lat_max) + eps
            lon_lo = min(crop_lon_min, crop_lon_max) - eps
            lon_hi = max(crop_lon_min, crop_lon_max) + eps
            if not (lat_lo <= ref_lat <= lat_hi and lon_lo <= ref_lon <= lon_hi):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"El punto de referencia ({ref_lat:.6f}, {ref_lon:.6f}) está fuera "
                        f"del área seleccionada. Vuelve a buscar puntos semilla con el nuevo recorte."
                    )
                )

    work_dir = Path(tempfile.mkdtemp(prefix="mintpy_run_"))
    zip_dir  = work_dir / "hyp3_products"
    zip_dir.mkdir()

    try:
        igram_pre_meta = []
        date_pairs = []
        for zfile in zips:
            zname = zfile.name
            zip_bytes = zip_dir / zname
            shutil.copy(zfile, zip_bytes)

            d1, d2 = _extract_dates(zname)
            if d1 is None or d2 is None:
                raise HTTPException(status_code=400, detail="Error fechas zip.")

            date_pairs.append((d1, d2))

            with zipfile.ZipFile(zip_bytes, "r") as zf:
                top_level = {p.split('/')[0] for p in zf.namelist() if '/' in p}
                zf.extractall(zip_dir)
            zip_bytes.unlink()

            igram_pre_meta.append({
                "filename": zname,
                "date1": d1,
                "date2": d2,
                "days": abs((d2 - d1).days),
                "extracted_folder": list(top_level)[0] if top_level else zname.replace('.zip', '')
            })

        igram_pre_meta.sort(key=lambda x: (x["date1"], x["date2"], x["filename"]))

        adj = {}
        for d1, d2 in date_pairs:
            a, b = min(d1, d2), max(d1, d2)
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

        has_triplets = False
        for d1, d2 in date_pairs:
            a, b = min(d1, d2), max(d1, d2)
            if adj.get(a) and adj.get(b) and adj[a].intersection(adj[b]):
                has_triplets = True
                break

        igram_meta = []
        asc_paths = []
        desc_paths = []
        for idx, meta in enumerate(igram_pre_meta):
            old_path = zip_dir / meta["extracted_folder"]
            new_path = zip_dir / f"{idx:04d}_{meta['extracted_folder']}"
            if old_path.exists(): old_path.rename(new_path)
            
            track_dir = "ASC"
            phi_files = list(new_path.glob("*_lv_phi.tif"))
            if phi_files:
                with rasterio.open(str(phi_files[0])) as src:
                    out_h, out_w = max(1, src.height // 10), max(1, src.width // 10)
                    data = src.read(1, out_shape=(1, out_h, out_w))
                    if src.nodata is not None: data[data == src.nodata] = np.nan
                    mean_phi = np.nanmean(data)
                    if not np.isnan(mean_phi):
                        mean_phi_deg = (np.degrees(mean_phi) + 360) % 360
                        if 90 < mean_phi_deg < 270: track_dir = "ASC"
                        else: track_dir = "DESC"
            
            if track_dir == "ASC": asc_paths.append(new_path)
            else: desc_paths.append(new_path)

            igram_meta.append({
                "filename": meta["filename"],
                "date1": meta["date1"].isoformat(),
                "date2": meta["date2"].isoformat(),
                "days": meta["days"],
                "track": track_dir
            })

        is_2d_mode = len(asc_paths) >= MIN_INTERFEROGRAMS and len(desc_paths) >= MIN_INTERFEROGRAMS
        
        if selected_mode == "LOS ASC":
            is_2d_mode = False
            for p in desc_paths:
                shutil.rmtree(p)
            desc_paths = []
            igram_meta = [m for m in igram_meta if m["track"] == "ASC"]
        elif selected_mode == "LOS DESC":
            is_2d_mode = False
            for p in asc_paths:
                shutil.rmtree(p)
            asc_paths = []
            igram_meta = [m for m in igram_meta if m["track"] == "DESC"]
        elif selected_mode == "2D":
            if not is_2d_mode:
                raise HTTPException(status_code=400, detail="No hay suficientes interferogramas para 2D.")
        
        def get_igram_stats(ts_dir, valid_msk, interp_args=None, prefix=""):
            stats_list = []
            if_stack = ts_dir / "inputs" / "ifgramStack.h5"
            if if_stack.exists():
                with h5py.File(if_stack, 'r') as stack_f:
                    if "unwrapPhase" in stack_f and "date" in stack_f:
                        dates_array = stack_f["date"][:]
                        phase_array = stack_f["unwrapPhase"][:]
                        wvl = float(stack_f.attrs.get("WAVELENGTH", 0.05546576))
                        rad2mm = (-1 * wvl / (4 * np.pi)) * 1000.0

                        for idx, d_pair in enumerate(dates_array):
                            if isinstance(d_pair, (list, tuple, np.ndarray)) and len(d_pair) >= 2:
                                d1 = d_pair[0].decode('utf-8')
                                d2 = d_pair[1].decode('utf-8')
                                
                                phase_2d = phase_array[idx].copy()
                                
                                pmask = np.isfinite(phase_2d) & (phase_2d != 0.0)

                                if interp_args:
                                    lat_src, lon_src, lat_grid_q, lon_grid_q = interp_args
                                    if np.any(pmask):
                                        valid_pts = np.column_stack((lat_src[pmask], lon_src[pmask]))
                                        phase_2d = griddata(valid_pts, phase_2d[pmask], (lat_grid_q, lon_grid_q), method='linear', fill_value=np.nan)
                                    else:
                                        phase_2d = np.full_like(lat_grid_q, np.nan)

                                def_mm_arr = phase_2d[valid_msk].flatten() * rad2mm
                                def_mm_arr = def_mm_arr[np.isfinite(def_mm_arr)]
                                if len(def_mm_arr) > 0:
                                    stats_list.append({
                                        "date1": d1,
                                        "date2": d2,
                                        "label": f"{prefix}{d1} -> {d2}",
                                        "mean": round(float(np.mean(def_mm_arr)), 2),
                                        "std": round(float(np.std(def_mm_arr)), 2),
                                        "max": round(float(np.max(def_mm_arr)), 2),
                                        "min": round(float(np.min(def_mm_arr)), 2),
                                    })
            return stats_list

        def append_interferograms(df_in, ts_dir, valid_msk, interp_args=None, prefix=""):
            if_stack = ts_dir / "inputs" / "ifgramStack.h5"
            if if_stack.exists():
                with h5py.File(if_stack, 'r') as stack_f:
                    if "unwrapPhase" in stack_f and "date" in stack_f:
                        dates_array = stack_f["date"][:]
                        phase_array = stack_f["unwrapPhase"][:]
                        wvl = float(stack_f.attrs.get("WAVELENGTH", 0.05546576))
                        rad2mm = (-1 * wvl / (4 * np.pi)) * 1000.0

                        for idx, d_pair in enumerate(dates_array):
                            if isinstance(d_pair, (list, tuple, np.ndarray)) and len(d_pair) >= 2:
                                d1 = d_pair[0].decode('utf-8')
                                d2 = d_pair[1].decode('utf-8')
                                col_suffix = f"{d1}_{d2}"
                                
                                phase_2d = phase_array[idx].copy()

                                pmask = np.isfinite(phase_2d) & (phase_2d != 0.0)

                                if interp_args:
                                    lat_src, lon_src, lat_grid_q, lon_grid_q = interp_args
                                    if np.any(pmask):
                                        valid_pts = np.column_stack((lat_src[pmask], lon_src[pmask]))
                                        phase_2d = griddata(valid_pts, phase_2d[pmask], (lat_grid_q, lon_grid_q), method='linear', fill_value=np.nan)
                                    else:
                                        phase_2d = np.full_like(lat_grid_q, np.nan)

                                def_mm_arr = phase_2d[valid_msk].flatten() * rad2mm
                                df_in[f"Deform_{prefix}{col_suffix}_mm"] = np.round(def_mm_arr, 2)
            return df_in

        if is_2d_mode:
            work_dir_asc = work_dir / "asc"
            work_dir_desc = work_dir / "desc"
            work_dir_asc.mkdir(); work_dir_desc.mkdir()
            zip_dir_asc = work_dir_asc / "hyp3_products"
            zip_dir_desc = work_dir_desc / "hyp3_products"
            zip_dir_asc.mkdir(); zip_dir_desc.mkdir()
            
            for p in asc_paths: shutil.move(str(p), str(zip_dir_asc / p.name))
            for p in desc_paths: shutil.move(str(p), str(zip_dir_desc / p.name))
                
            try:
                # Run ASC pipeline first
                skipped_asc = _run_mintpy_pipeline(work_dir_asc, zip_dir_asc, ref_lat, ref_lon, crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max, has_triplets, min_coherence, era5_key=x_era5_key)


                # If no reference was provided by the user, read the one MintPy auto-selected
                # for ASC and force DESC to use the exact same geographic point.
                # This is critical: LOS velocities are always relative to their reference point.
                # If ASC and DESC have different reference points, the 2D decomposition is physically invalid.
                forced_ref_lat, forced_ref_lon = ref_lat, ref_lon
                if ref_lat is None or ref_lon is None:
                    vel_h5_asc_check = work_dir_asc / "velocity.h5"
                    if vel_h5_asc_check.exists():
                        with h5py.File(vel_h5_asc_check, "r") as vf_check:
                            va = dict(vf_check.attrs)
                        _ref_lat = va.get("REF_LAT", None)
                        _ref_lon = va.get("REF_LON", None)
                        if _ref_lat is not None and _ref_lon is not None:
                            try:
                                forced_ref_lat = float(_ref_lat)
                                forced_ref_lon = float(_ref_lon)
                                # Validate that these are WGS84 coords (abs < 90/180)
                                # If data is in UTM, REF_LAT/REF_LON may be projected coords
                                if abs(forced_ref_lat) > 90 or abs(forced_ref_lon) > 180:
                                    epsg = int(va.get("EPSG", 32616))
                                    transformer = pyproj.Transformer.from_crs(epsg, 4326, always_xy=True)
                                    forced_ref_lon, forced_ref_lat = transformer.transform(forced_ref_lon, forced_ref_lat)
                            except (ValueError, TypeError):
                                forced_ref_lat, forced_ref_lon = ref_lat, ref_lon

                skipped_desc = _run_mintpy_pipeline(work_dir_desc, zip_dir_desc, forced_ref_lat, forced_ref_lon, crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max, has_triplets, min_coherence, era5_key=x_era5_key)

            except ValueError as exc:
                raise HTTPException(status_code=500, detail=str(exc))
                
            vel_h5_asc = work_dir_asc / "velocity.h5"
            vel_h5_desc = work_dir_desc / "velocity.h5"
            geo_asc = work_dir_asc / "inputs" / "geometryGeo.h5"
            geo_desc = work_dir_desc / "inputs" / "geometryGeo.h5"
            
            if not vel_h5_asc.exists() or not vel_h5_desc.exists() or not geo_asc.exists() or not geo_desc.exists():
                raise HTTPException(status_code=500, detail="MintPy no generó los archivos H5 para 2D.")
                
            with h5py.File(vel_h5_asc, "r") as vf_a, h5py.File(vel_h5_desc, "r") as vf_d:
                vel_asc = vf_a["velocity"][:] * 1000.0
                vel_desc = vf_d["velocity"][:] * 1000.0
                


                vel_attrs = dict(vf_a.attrs)
                
                with h5py.File(geo_asc, "r") as gf_a, h5py.File(geo_desc, "r") as gf_d:
                    inc_asc = np.radians(gf_a["incidenceAngle"][:])
                    azi_asc = np.radians(gf_a["azimuthAngle"][:])
                    inc_desc = np.radians(gf_d["incidenceAngle"][:])
                    azi_desc = np.radians(gf_d["azimuthAngle"][:])
    
                    def get_lat_lon(vf, gf):
                        lat = gf["latitude"][:] if "latitude" in gf else None
                        lon = gf["longitude"][:] if "longitude" in gf else None
                        if lat is None or lon is None:
                            attrs = dict(vf.attrs)
                            lat0, lon0 = float(attrs.get("Y_FIRST", 0)), float(attrs.get("X_FIRST", 0))
                            dlat, dlon = float(attrs.get("Y_STEP", -0.001)), float(attrs.get("X_STEP", 0.001))
                            # MintPy convention: Y_FIRST / X_FIRST is the CENTER of pixel (0,0).
                            # No +0.5 offset needed.
                            lat_arr = lat0 + np.arange(vf["velocity"].shape[0]) * dlat
                            lon_arr = lon0 + np.arange(vf["velocity"].shape[1]) * dlon
                            lon, lat = np.meshgrid(lon_arr, lat_arr)
                            if abs(lat0) > 90 or abs(lon0) > 180:
                                epsg = int(attrs.get("EPSG", 32616))
                                transformer = pyproj.Transformer.from_crs(epsg, 4326, always_xy=True)
                                lon, lat = transformer.transform(lon, lat)
                        return lat, lon
    
                    lat_asc, lon_asc = get_lat_lon(vf_a, gf_a)
                    lat_desc, lon_desc = get_lat_lon(vf_d, gf_d)

            # Spatial resampling (interpolation) of Ascending to Descending master grid
            valid_mask_a = np.isfinite(vel_asc)
            
            if np.any(valid_mask_a):
                valid_pts_a = np.column_stack((lat_asc[valid_mask_a], lon_asc[valid_mask_a]))
                vel_asc = griddata(valid_pts_a, vel_asc[valid_mask_a], (lat_desc, lon_desc), method='linear', fill_value=np.nan)
                inc_asc = griddata(valid_pts_a, inc_asc[valid_mask_a], (lat_desc, lon_desc), method='linear', fill_value=np.nan)
                azi_asc = griddata(valid_pts_a, azi_asc[valid_mask_a], (lat_desc, lon_desc), method='linear', fill_value=np.nan)
            else:
                vel_asc = np.full_like(vel_desc, np.nan)
                inc_asc = np.full_like(vel_desc, np.nan)
                azi_asc = np.full_like(vel_desc, np.nan)

            lat_grid, lon_grid = lat_desc, lon_desc

            # MintPy stores azimuthAngle as the azimuth of the HORIZONTAL COMPONENT of the
            # LOS vector — NOT the satellite heading. Values are typically:
            #   Ascending  S1: ~90–100° (looking toward ESE)  → sin(azi) ≈ +0.98
            #   Descending S1: ~260–280° (looking toward WNW) → sin(azi) ≈ -0.98
            # This gives a healthy determinant (~0.96) and physically correct E/U decomposition.
            # LOS unit vector (ground → satellite) in ENU:
            #   e_E = sin(inc) * sin(azi)
            #   e_U = cos(inc)
            A_asc_ew  = np.sin(inc_asc)  * np.sin(azi_asc)
            A_asc_up  = np.cos(inc_asc)
            A_desc_ew = np.sin(inc_desc) * np.sin(azi_desc)
            A_desc_up = np.cos(inc_desc)
            
            det = (A_asc_ew * A_desc_up) - (A_asc_up * A_desc_ew)
            
            mask_a = np.isfinite(vel_asc)
            mask_d = np.isfinite(vel_desc)
            
            valid = mask_a & mask_d & (det != 0) & np.isfinite(det)
            vel_ew = np.full_like(vel_desc, np.nan)
            vel_up = np.full_like(vel_desc, np.nan)
            vel_ew[valid] = (A_desc_up[valid] * vel_asc[valid] - A_asc_up[valid] * vel_desc[valid]) / det[valid]
            vel_up[valid] = (-A_desc_ew[valid] * vel_asc[valid] + A_asc_ew[valid] * vel_desc[valid]) / det[valid]

            lats_v, lons_v = lat_grid[valid].flatten(), lon_grid[valid].flatten()
            ew_v, up_v = vel_ew[valid].flatten(), vel_up[valid].flatten()
            los_v = vel_desc[valid].flatten()
            
            results = [
                {
                    "lat": round(float(lats_v[i]), 6), 
                    "lon": round(float(lons_v[i]), 6), 
                    "velocidad_mm_yr": round(float(los_v[i]), 2), 
                    "vel_ew_mm_yr": round(float(ew_v[i]), 2), 
                    "vel_up_mm_yr": round(float(up_v[i]), 2)
                }
                for i in range(len(ew_v))
            ]
            
            cols = ["lat", "lon", "velocidad_mm_yr", "vel_ew_mm_yr", "vel_up_mm_yr"]
            df_csv = pd.DataFrame([{k: r[k] for k in cols} for r in results])
            
            try:
                generate_quiver_plots(results)
            except Exception as exc:
                logging.error(f"Error generando quivers: {exc}")
                
            df_csv.rename(columns={"lat": "Latitud", "lon": "Longitud", "velocidad_mm_yr": "Velocidad_mm_amo", "vel_ew_mm_yr": "Velocidad_EW_mm_amo", "vel_up_mm_yr": "Velocidad_UP_mm_amo"}, inplace=True)
            df_csv = append_interferograms(df_csv, work_dir_asc, valid, interp_args=(lat_asc, lon_asc, lat_desc, lon_desc), prefix="Asc_")
            df_csv = append_interferograms(df_csv, work_dir_desc, valid, prefix="Desc_")
            df_csv.to_csv(CSV_FILE, index=False)
            
            igram_stats_asc = get_igram_stats(work_dir_asc, valid, interp_args=(lat_asc, lon_asc, lat_desc, lon_desc), prefix="Asc_")
            igram_stats_desc = get_igram_stats(work_dir_desc, valid, prefix="Desc_")
            igram_stats_2d = igram_stats_asc + igram_stats_desc
            if igram_stats_2d:
                df_stats = pd.DataFrame(igram_stats_2d)
                df_stats = df_stats[["label", "date1", "date2", "mean", "min", "max"]]
                df_stats.columns = ["Interferograma", "Fecha Inicio", "Fecha Fin", "Velocidad Media (mm/a)", "Velocidad Min (mm/a)", "Velocidad Max (mm/a)"]
                df_stats.to_excel(XLSX_FILE, index=False)
            else:
                if XLSX_FILE.exists(): XLSX_FILE.unlink()
            
            all_dates = [m["date1"] for m in igram_meta] + [m["date2"] for m in igram_meta]
            stats = {
                "min": round(float(np.min(up_v) if len(up_v) else 0), 2),
                "max": round(float(np.max(up_v) if len(up_v) else 0), 2),
                "mean": round(float(np.mean(up_v) if len(up_v) else 0), 2),
                "std": round(float(np.std(up_v) if len(up_v) else 0), 2),
                "mean_ew": round(float(np.mean(ew_v) if len(ew_v) else 0), 2),
                "std_ew": round(float(np.std(ew_v) if len(ew_v) else 0), 2),
                "min_ew": round(float(np.min(ew_v) if len(ew_v) else 0), 2),
                "max_ew": round(float(np.max(ew_v) if len(ew_v) else 0), 2),
                "n_points": len(results),
                "n_interferograms": len(igram_meta),
                "date_start": min(all_dates),
                "date_end": max(all_dates),
                "era5_successful": (
                    (work_dir_asc / "inputs" / "ERA5.h5").exists() or
                    (work_dir_desc / "inputs" / "ERA5.h5").exists()
                ),
                "tropo_method": "ERA5" if (
                    (work_dir_asc / "inputs" / "ERA5.h5").exists() or
                    (work_dir_desc / "inputs" / "ERA5.h5").exists()
                ) else "height_correlation",
                "phase_closure_skipped": skipped_asc or skipped_desc,
                "min_coherence": min_coherence,
                "excluded_ifgrams": (
                    _get_excluded_ifgrams(work_dir_asc, [m for m in igram_meta if m["track"] == "ASC"]) +
                    _get_excluded_ifgrams(work_dir_desc, [m for m in igram_meta if m["track"] == "DESC"])
                ),
            }
            
            step_s = max(1, len(results) // 1000)
            sample = results[::step_s][:1000]
            
            # Generate phase previews for 2D mode
            previews_2d = None
            try:
                asc_meta = [m for m in igram_meta if m["track"] == "ASC"]
                if asc_meta:
                    previews_2d = generate_phase_previews(work_dir_asc, zip_dir_asc, asc_meta)
                else:
                    desc_meta = [m for m in igram_meta if m["track"] == "DESC"]
                    if desc_meta:
                        previews_2d = generate_phase_previews(work_dir_desc, zip_dir_desc, desc_meta)
            except Exception as e_prev:
                logging.warning(f"Error generando phase previews 2D: {e_prev}")
            
            output = {"stats": stats, "interferograms": igram_meta, "igram_stats": igram_stats_2d, "sample": sample, "mode": "2D", "phase_previews": previews_2d}

            # HyP3 direct velocity comparison (if los_disp.tif files are present)
            # In 2D mode, scan both ASC and DESC directories
            # Wrapped in a thread with timeout to prevent indefinite hangs
            hyp3_result = None
            try:
                if _has_hyp3_displacement(zip_dir_asc) or _has_hyp3_displacement(zip_dir_desc):
                    _hyp3_dir = zip_dir_asc if _has_hyp3_displacement(zip_dir_asc) else zip_dir_desc
                    logging.info("[2D] HyP3 displacement files found. Computing with 120s timeout...")
                    import concurrent.futures as _cf
                    with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                        _fut = _ex.submit(
                            _compute_hyp3_velocity,
                            _hyp3_dir, igram_meta, "2D",
                            crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max,
                            work_dir_desc,  # use DESC MintPy grid as spatial reference
                        )
                        try:
                            hyp3_result = _fut.result(timeout=120)
                        except _cf.TimeoutError:
                            logging.warning("[2D] HyP3 velocity timed out (>120s) — skipping.")
                else:
                    logging.info("[2D] No HyP3 displacement files found. Skipping.")
            except Exception as hyp3_exc:
                logging.warning(f"HyP3 velocity computation failed (non-fatal): {hyp3_exc}")

            if hyp3_result and hyp3_result.get("hyp3_has_data"):
                output["hyp3"] = {
                    "stats": hyp3_result["stats"],
                    "sample": hyp3_result["sample"],
                    "igram_stats": hyp3_result["igram_stats"],
                }
            else:
                output["hyp3"] = None

            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            RESULTS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))
            shutil.rmtree(session_dir, ignore_errors=True)
            return output

        else:
            try:
                skipped = _run_mintpy_pipeline(work_dir, zip_dir, ref_lat, ref_lon, crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max, has_triplets, min_coherence, era5_key=x_era5_key)

            except ValueError as exc:
                raise HTTPException(status_code=500, detail=str(exc))

            logging.info("[LOS] _run_mintpy_pipeline complete. Reading velocity.h5...")
            velocity_h5 = work_dir / "velocity.h5"
            if not velocity_h5.exists():
                raise HTTPException(status_code=500, detail="MintPy no generó velocity.h5 en modo LOS.")

            geometry_geo = work_dir / "inputs" / "geometryGeo.h5"
            with h5py.File(velocity_h5, "r") as vf:
                vel_data  = vf["velocity"][:]
                vel_mm    = vel_data * 1000.0
                vel_attrs = dict(vf.attrs)

            logging.info("[LOS] velocity.h5 read. Building lat/lon grid...")
            use_grid = False
            if geometry_geo.exists():
                with h5py.File(geometry_geo, "r") as gf:
                    if "latitude" in gf and "longitude" in gf:
                        lat_grid = gf["latitude"][:]
                        lon_grid = gf["longitude"][:]
                        use_grid = True
            
            if not use_grid:
                dlat, dlon = float(vel_attrs.get("Y_STEP", -0.001)), float(vel_attrs.get("X_STEP", 0.001))
                lat0, lon0 = float(vel_attrs.get("Y_FIRST", 0)), float(vel_attrs.get("X_FIRST", 0))
                lat_arr_desc  = lat0 + np.arange(vel_mm.shape[0]) * dlat
                lon_arr_desc  = lon0 + np.arange(vel_mm.shape[1]) * dlon
                lon_grid, lat_grid = np.meshgrid(lon_arr_desc, lat_arr_desc)
                if abs(lat0) > 90 or abs(lon0) > 180:
                    epsg = int(vel_attrs.get("EPSG", 32616))
                    transformer = pyproj.Transformer.from_crs(epsg, 4326, always_xy=True)
                    lon_grid, lat_grid = transformer.transform(lon_grid, lat_grid)

            logging.info("[LOS] Grid built. Filtering valid pixels...")
            valid = np.isfinite(vel_mm)
            lats_v, lons_v, vels_v = lat_grid[valid].flatten(), lon_grid[valid].flatten(), vel_mm[valid].flatten()
            if len(vels_v) == 0:
                raise HTTPException(status_code=422, detail="No coherentes.")

            logging.info("[LOS] %d valid pixels. Building results list...", len(vels_v))
            results = [{"lat": round(float(lats_v[i]), 6), "lon": round(float(lons_v[i]), 6), "velocidad_mm_yr": round(float(vels_v[i]), 2)} for i in range(len(vels_v))]
            
            all_dates = [m["date1"] for m in igram_meta] + [m["date2"] for m in igram_meta]
            stats = {
                "min": round(float(np.min(vels_v)), 2),
                "max": round(float(np.max(vels_v)), 2),
                "mean": round(float(np.mean(vels_v)), 2),
                "std": round(float(np.std(vels_v)), 2),
                "n_points": len(results),
                "n_interferograms": len(igram_meta),
                "date_start": min(all_dates),
                "date_end": max(all_dates),
                "era5_successful": (work_dir / "inputs/ERA5.h5").exists(),
                "tropo_method": "ERA5" if (work_dir / "inputs/ERA5.h5").exists() else "height_correlation",
                "phase_closure_skipped": skipped,
                "min_coherence": min_coherence,
                "excluded_ifgrams": _get_excluded_ifgrams(work_dir, igram_meta),
            }

            step_s = max(1, len(results) // 1000)
            sample = results[::step_s][:1000]

            logging.info("[LOS] Building CSV DataFrame...")
            # Build DataFrame efficiently using numpy arrays directly (avoids slow list-of-dicts)
            df_csv = pd.DataFrame({
                "Latitud": np.round(lats_v, 6),
                "Longitud": np.round(lons_v, 6),
                "Velocidad_mm_año": np.round(vels_v, 2),
            })

            logging.info("[LOS] DataFrame built (%d rows). Appending interferograms...", len(df_csv))
            df_csv = append_interferograms(df_csv, work_dir, valid)

            logging.info("[LOS] Interferograms appended. Writing CSV...")
            df_csv.to_csv(CSV_FILE, index=False)

            logging.info("[LOS] CSV written. Computing igram stats...")
            igram_stats = get_igram_stats(work_dir, valid)
            if igram_stats:
                df_stats = pd.DataFrame(igram_stats)
                df_stats = df_stats[["label", "date1", "date2", "mean", "min", "max"]]
                df_stats.columns = ["Interferograma", "Fecha Inicio", "Fecha Fin", "Velocidad Media (mm/a)", "Velocidad Min (mm/a)", "Velocidad Max (mm/a)"]
                logging.info("[LOS] Writing Excel stats...")
                df_stats.to_excel(XLSX_FILE, index=False)
            else:
                if XLSX_FILE.exists(): XLSX_FILE.unlink()

            # Generate phase previews for LOS mode
            previews_los = None
            try:
                previews_los = generate_phase_previews(work_dir, zip_dir, igram_meta)
            except Exception as e_prev:
                logging.warning(f"Error generando phase previews LOS: {e_prev}")

            output = {"stats": stats, "interferograms": igram_meta, "igram_stats": igram_stats, "sample": sample, "mode": "LOS", "phase_previews": previews_los}

            # HyP3 direct velocity comparison — run in a thread with timeout to avoid hanging
            logging.info("[LOS] Checking for HyP3 displacement files...")
            hyp3_result = None
            try:
                if _has_hyp3_displacement(zip_dir):
                    logging.info("[LOS] HyP3 displacement files found. Computing with 120s timeout...")
                    import concurrent.futures as _cf
                    with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                        _fut = _ex.submit(
                            _compute_hyp3_velocity,
                            zip_dir, igram_meta, selected_mode or "LOS",
                            crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max,
                            work_dir,  # use MintPy grid as spatial reference
                        )
                        try:
                            hyp3_result = _fut.result(timeout=120)
                        except _cf.TimeoutError:
                            logging.warning("[LOS] HyP3 velocity timed out (>120s) — skipping.")
                else:
                    logging.info("[LOS] No HyP3 displacement files found. Skipping.")
            except Exception as hyp3_exc:
                logging.warning(f"HyP3 velocity computation failed (non-fatal): {hyp3_exc}")

            if hyp3_result and hyp3_result.get("hyp3_has_data"):
                output["hyp3"] = {
                    "stats": hyp3_result["stats"],
                    "sample": hyp3_result["sample"],
                    "igram_stats": hyp3_result["igram_stats"],
                }
            else:
                output["hyp3"] = None

            logging.info("[LOS] Writing results JSON and returning response...")
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            RESULTS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))
            shutil.rmtree(session_dir, ignore_errors=True)
            logging.info("[LOS] Done. Returning output.")
            return output

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

@router.get("/results")
def get_results():
    if not RESULTS_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail="No hay resultados. Procesa interferogramas primero.",
        )
    return json.loads(RESULTS_FILE.read_text())


@router.post("/preview_reference")
async def preview_reference(
        session_id: str = Form(...),
        crop_lat_min: float = Form(None),
        crop_lat_max: float = Form(None),
        crop_lon_min: float = Form(None),
        crop_lon_max: float = Form(None),
):
    """Generates a preview of optimal reference points for MintPy from ZIP files containing interferograms.

    This function receives multiple ZIP files with interferograms, extracts coherence maps, computes the coherence sum
    in the common region, and determines the point with the highest coherence. Optionally, it crops the area of
    interest according to the provided geographic bounds. Returns the suggested seed points for MintPy spatial
    referencing.

    Args:
        files (List[UploadFile]): List of uploaded ZIP files, each containing an interferogram.
        crop_lat_min (float, optional): Lower latitude bound for geographic cropping.
        crop_lat_max (float, optional): Upper latitude bound for geographic cropping.
        crop_lon_min (float, optional): Lower longitude bound for geographic cropping.
        crop_lon_max (float, optional): Upper longitude bound for geographic cropping.

    Returns:
        dict: Dictionary with the key 'seed_points', containing a list of suggested reference
        points (lat, lon, is_mintpy_default).

    Raises:
        HTTPException: If there are fewer than the required minimum interferograms, if no coherence
        files are found, or if there are no valid pixels in the selected area.
    """
    
    session_dir = SESSION_BASE / session_id
    zips = list(session_dir.glob("*.zip"))
    if len(zips) < MIN_INTERFEROGRAMS:
        raise HTTPException(
            status_code=400,
            detail=f"Se requieren al menos {MIN_INTERFEROGRAMS} interferogramas."
        )

    work_dir = Path(tempfile.mkdtemp(prefix="mintpy_prev_"))
    zip_dir = work_dir / "hyp3_products"
    zip_dir.mkdir()

    try:
        for zfile in zips:
            zname = zfile.name
            zip_bytes = zip_dir / zname
            shutil.copy(zfile, zip_bytes)
            if not zipfile.is_zipfile(zip_bytes):
                logging.warning(f"preview_reference: archivo ignorado (no es ZIP válido): {zname}")
                zip_bytes.unlink()
                continue
            with zipfile.ZipFile(zip_bytes, "r") as zf:
                zf.extractall(zip_dir)
            zip_bytes.unlink()

        tifs = list(zip_dir.glob("*/*_corr.tif"))
        if not tifs:
            raise HTTPException(
                status_code=400,
                detail="No se encontraron archivos de coherencia en el ZIP."
            )

        from rasterio.warp import transform_bounds

        lefts, bottoms, rights, tops = [], [], [], []
        crs_list = []
        
        for t in tifs:
            with rasterio.open(str(t)) as src:
                crs_list.append(src.crs)
                
        all_same_crs = all(c == crs_list[0] for c in crs_list)

        if all_same_crs:
            for t in tifs:
                with rasterio.open(str(t)) as src:
                    lefts.append(src.bounds.left)
                    bottoms.append(src.bounds.bottom)
                    rights.append(src.bounds.right)
                    tops.append(src.bounds.top)
            
            cb_left, cb_bottom = max(lefts), max(bottoms)
            cb_right, cb_top = min(rights), min(tops)
            
            if cb_right <= cb_left or cb_top <= cb_bottom:
                raise HTTPException(status_code=400, detail="No hay solapamiento geográfico entre las imágenes.")
                
            common_bounds_src0 = (cb_left, cb_bottom, cb_right, cb_top)
            
            with rasterio.open(str(tifs[0])) as src0:
                base_transform = src0.transform
                window = rasterio.windows.from_bounds(*common_bounds_src0, transform=base_transform)
                window = window.round_offsets().round_shape()
                win_col_off, win_row_off = int(window.col_off), int(window.row_off)
                win_width, win_height = int(window.width), int(window.height)
                
                exact_window = Window(win_col_off, win_row_off, win_width, win_height)
                transform = rasterio.windows.transform(exact_window, base_transform)
                target_crs = src0.crs
                crs = src0.crs  # also set crs for the crop-mask transform below
        else:
            for t in tifs:
                with rasterio.open(str(t)) as src:
                    l, b, r, top_ = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
                    lefts.append(l)
                    bottoms.append(b)
                    rights.append(r)
                    tops.append(top_)

            common_bounds_ll = (max(lefts), max(bottoms), min(rights), min(tops))
            
            if common_bounds_ll[2] <= common_bounds_ll[0] or common_bounds_ll[3] <= common_bounds_ll[1]:
                raise HTTPException(status_code=400, detail="No hay solapamiento geográfico entre las imágenes.")

            with rasterio.open(str(tifs[0])) as src0:
                crs = src0.crs
                base_transform = src0.transform
                
                l0, b0, r0, t0 = transform_bounds("EPSG:4326", crs, *common_bounds_ll)
                common_bounds_src0 = (l0, b0, r0, t0)
                
                window = rasterio.windows.from_bounds(*common_bounds_src0, transform=base_transform)
                window = window.round_offsets().round_shape()
                win_col_off, win_row_off = int(window.col_off), int(window.row_off)
                win_width, win_height = int(window.width), int(window.height)
                
                exact_window = Window(win_col_off, win_row_off, win_width, win_height)
                transform = rasterio.windows.transform(exact_window, base_transform)
                target_crs = src0.crs

        coh_sum = np.zeros((win_height, win_width), dtype=np.float32)
        unw_tifs = list(zip_dir.glob("*/*_unw_phase.tif"))
        conn_mask = np.ones((win_height, win_width), dtype=bool)

        def read_to_dest(src_file, is_unw=False):
            with rasterio.open(str(src_file)) as src:
                # Ensure pixel-perfect match and identical results for aligned grids (e.g. single Asc/Desc tracks)
                if src.crs == target_crs and src.transform == base_transform:
                    target_nodata = 0.0 if is_unw else np.nan
                    out_data = src.read(1, window=exact_window, boundless=True, fill_value=target_nodata).astype(np.float32)
                else:
                    out_data = np.zeros((win_height, win_width), dtype=np.float32)
                    from rasterio.warp import reproject, Resampling
                    target_nodata = 0.0 if is_unw else np.nan
                    reproject(
                        source=rasterio.band(src, 1),
                        destination=out_data,
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=target_crs,
                        resampling=Resampling.nearest if is_unw else Resampling.bilinear,
                        dst_nodata=target_nodata
                    )
                return out_data, src.nodata

        for t in unw_tifs:
            data_unw, nodataval = read_to_dest(t, is_unw=True)
            valid_phase = ~np.isnan(data_unw)
            if nodataval is not None:
                valid_phase &= (data_unw != nodataval)
            valid_phase &= (data_unw != 0.0)
            conn_mask &= valid_phase

        for t in tifs:
            data_coh, nodataval = read_to_dest(t, is_unw=False)
            if nodataval is not None:
                data_coh[data_coh == nodataval] = np.nan

            valid = ~np.isnan(data_coh)
            coh_sum[valid] += data_coh[valid]

        # Apply ConnComp mask so we never suggest a pixel that MintPy will mask out.
        coh_sum[~conn_mask] = -1.0

        if crop_lat_min is not None and crop_lat_max is not None and crop_lon_min is not None and crop_lon_max is not None:
            # Generate meshgrid for the window
            c_arr = np.arange(win_width)
            r_arr = np.arange(win_height)
            c_grid, r_grid = np.meshgrid(c_arr, r_arr)
            # Use 0.5 for evaluating inclusion precisely inside bounds. 
            # This is safe because it only determines the geographical mask filtering array.
            lon_proj, lat_proj = transform * (c_grid + 0.5, r_grid + 0.5)

            transformer = None
            if crs and crs.to_epsg() != 4326:
                transformer = pyproj.Transformer.from_crs(crs.to_epsg() or 32616, 4326, always_xy=True)

            if transformer:
                # Need to reshape for pyproj
                lon_proj_flat, lat_proj_flat = lon_proj.flatten(), lat_proj.flatten()
                lon_val_flat, lat_val_flat = transformer.transform(lon_proj_flat, lat_proj_flat)
                lon_val = lon_val_flat.reshape(lon_proj.shape)
                lat_val = lat_val_flat.reshape(lat_proj.shape)
            else:
                lon_val, lat_val = lon_proj, lat_proj

            mask = (lat_val >= crop_lat_min) & (lat_val <= crop_lat_max) & \
                   (lon_val >= crop_lon_min) & (lon_val <= crop_lon_max)

            coh_sum[~mask] = -1.0

        max_val = np.nanmax(coh_sum)
        if np.isnan(max_val) or max_val < 0:
            raise HTTPException(status_code=422, detail="No hay pixeles de coherencia en el área seleccionada.")

        ys, xs = np.where(coh_sum == max_val)

        transformer = None
        if crs and crs.to_epsg() != 4326:
            transformer = pyproj.Transformer.from_crs(crs.to_epsg() or 32616, 4326, always_xy=True)

        tied_points = []
        for i in range(len(ys)):
            r, c = ys[i], xs[i]
            # Instead of 0.5 (center), we use 0.1 to avoid numpy's "round half to even" bug!
            # Mintpy will `np.round((lat - Y_FIRST) / Y_STEP)`. If we pass exactly the center (0.5), it will
            # shift odd numbered pixels randomly into adjacent invalid zones. 0.1 locks it safely.
            lon_proj, lat_proj = transform * (c + 0.1, r + 0.1)

            if transformer:
                lon_val, lat_val = transformer.transform(lon_proj, lat_proj)
            else:
                lon_val, lat_val = lon_proj, lat_proj

            tied_points.append({
                "lat": round(float(lat_val), 6),
                "lon": round(float(lon_val), 6),
                "is_mintpy_default": (i == 0)
            })

        return {"seed_points": tied_points}

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
@router.get("/export_csv")
def export_csv():
    if not CSV_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail="No hay datos en CSV para exportar. Procesa interferogramas primero.",
        )
    return FileResponse(
        CSV_FILE,
        media_type="text/csv",
        filename="velocidad_deformacion_mintpy.csv",
    )

@router.get("/export_xlsx")
def export_xlsx():
    if not XLSX_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail="No hay datos en XLSX para exportar. Procesa interferogramas primero.",
        )
    return FileResponse(
        XLSX_FILE,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="resumen_interferogramas_mintpy.xlsx",
    )

@router.get("/export_quiver_ew")
def export_quiver_ew():
    p = RESULTS_DIR / "quiver_ew.png"
    if not p.exists():
        raise HTTPException(status_code=404, detail="No hay mapa quiver EW.")
    return FileResponse(p, media_type="image/png", filename="deformacion_ew_mintpy.png")

@router.get("/export_quiver_up")
def export_quiver_up():
    p = RESULTS_DIR / "quiver_up.png"
    if not p.exists():
        raise HTTPException(status_code=404, detail="No hay mapa quiver UP.")
    return FileResponse(p, media_type="image/png", filename="deformacion_up_mintpy.png")


@router.get("/export_csv_hyp3")
def export_csv_hyp3():
    if not CSV_FILE_HYP3.exists():
        raise HTTPException(
            status_code=404,
            detail="No hay datos HyP3 en CSV. Procesa interferogramas con archivos los_disp.tif primero.",
        )
    return FileResponse(
        CSV_FILE_HYP3,
        media_type="text/csv",
        filename="velocidad_deformacion_hyp3.csv",
    )


@router.get("/export_xlsx_hyp3")
def export_xlsx_hyp3():
    if not XLSX_FILE_HYP3.exists():
        raise HTTPException(
            status_code=404,
            detail="No hay datos HyP3 en XLSX. Procesa interferogramas con archivos los_disp.tif primero.",
        )
    return FileResponse(
        XLSX_FILE_HYP3,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="resumen_interferogramas_hyp3.xlsx",
    )



@router.post("/preview_bounds")
async def preview_bounds(session_id: str = Form(...)):
    """Extracts geographic bounds from the first HyP3 interferogram ZIP file.

    Processes the first uploaded ZIP file containing HyP3 interferogram products,
    extracts its contents, and retrieves the geographic bounding box by reading
    the GeoTIFF metadata. Coordinates are automatically transformed to WGS84
    (EPSG:4326) format regardless of the source CRS.

    Args:
        files (List[UploadFile]): List of uploaded ZIP files. Only the first file
            is processed. Each ZIP should contain HyP3 product files including
            at least one GeoTIFF file (*_dem.tif or any *.tif).

    Returns:
        dict: A dictionary with the following structure:
            {
                "success": bool,
                "bounds": {
                    "lat_min": float,  # Minimum latitude in WGS84 (south edge)
                    "lon_min": float,  # Minimum longitude in WGS84 (west edge)
                    "lat_max": float,  # Maximum latitude in WGS84 (north edge)
                    "lon_max": float   # Maximum longitude in WGS84 (east edge)
                }
            }

    Raises:
        HTTPException (400): If the files list is empty or if no GeoTIFF files
            (*_dem.tif or *.tif) are found in the extracted ZIP. Status code 400.

    Note:
        - Only the first file in the files list is processed.
        - The function creates a temporary directory for extraction which is
          cleaned up after processing, even if an error occurs.
        - DEM files (*_dem.tif) are preferred, but any *.tif file will be used
          if DEM files are not found.
    """
    session_dir = SESSION_BASE / session_id
    zips = list(session_dir.glob("*.zip"))
    if not zips:
        raise HTTPException(status_code=400, detail="No files provided.")

    file_path = zips[0]
    work_dir = Path(tempfile.mkdtemp(prefix="mintpy_prev_bounds_"))
    zip_dir = work_dir /"hyp3_products"
    zip_dir.mkdir()

    try:
        zname = file_path.name
        zip_bytes = zip_dir / zname
        shutil.copy(file_path, zip_bytes)

        if not zipfile.is_zipfile(zip_bytes):
            raise HTTPException(status_code=400, detail=f"El archivo '{zname}' no es un ZIP válido (puede estar incompleto).")
        with zipfile.ZipFile(zip_bytes, "r") as zf:
            zf.extractall(zip_dir)
        zip_bytes.unlink()

        tifs = list(zip_dir.glob("*/*_dem.tif")) or list(zip_dir.glob(""
                                                                      "*/*.tif"))
        if not tifs:
            raise HTTPException(status_code=400, detail="No .tif files found in ZIP")

        with rasterio.open(str(tifs[0])) as src:
            left, bottom, right, top = rasterio.warp.transform_bounds(src.crs, "EPSG:4326", *src.bounds)

        return {
            "success": True,
            "bounds": {
                "lat_min": min(bottom, top),
                "lon_min": min(left, right),
                "lat_max": max(bottom, top),
                "lon_max": max(left, right)
            }
        }

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

@router.post("/preview_plan")
async def preview_plan(session_id: str = Form(...)):
    session_dir = SESSION_BASE / session_id
    zips = list(session_dir.glob("*.zip"))
    if not zips:
         raise HTTPException(status_code=400, detail="No files provided.")

    work_dir = Path(tempfile.mkdtemp(prefix="mintpy_prev_plan_"))
    zip_dir = work_dir / "hyp3_products"
    zip_dir.mkdir()
    
    asc_count = 0
    desc_count = 0
    try:
        for zfile in zips:
            zname = zfile.name
            zip_bytes = zip_dir / zname
            shutil.copy(zfile, zip_bytes)
            if not zipfile.is_zipfile(zip_bytes):
                logging.warning(f"preview_plan: archivo ignorado (no es ZIP válido): {zname}")
                zip_bytes.unlink()
                continue
            
            with zipfile.ZipFile(zip_bytes, "r") as zf:
                phi_files = [f for f in zf.namelist() if f.endswith("_lv_phi.tif")]
                if phi_files:
                    zf.extract(phi_files[0], zip_dir)
                    phi_path = zip_dir / phi_files[0]
                    with rasterio.open(str(phi_path)) as src:
                        # Read a small subset for speed
                        out_h = max(1, src.height // 10)
                        out_w = max(1, src.width // 10)
                        data = src.read(1, out_shape=(1, out_h, out_w))
                        if src.nodata is not None:
                            data[data == src.nodata] = np.nan
                        mean_phi = np.nanmean(data)
                        if not np.isnan(mean_phi):
                            mean_phi_deg = (np.degrees(mean_phi) + 360) % 360
                            # lv_phi ~90° (Este) = Ascendente, ~270° (Oeste) = Descendente
                            if 90 < mean_phi_deg < 270:
                                asc_count += 1
                            else:
                                desc_count += 1
            zip_bytes.unlink()
            
        available_modes = []
        if asc_count >= MIN_INTERFEROGRAMS and desc_count >= MIN_INTERFEROGRAMS:
            available_modes = ["2D", "LOS ASC", "LOS DESC"]
        else:
            if asc_count >= MIN_INTERFEROGRAMS:
                available_modes.append("LOS ASC")
            if desc_count >= MIN_INTERFEROGRAMS:
                available_modes.append("LOS DESC")
                
        mode = "2D" if "2D" in available_modes else (available_modes[0] if available_modes else "LOS")
        return {
            "success": True,
            "asc_count": asc_count,
            "desc_count": desc_count,
            "mode": mode,
            "available_modes": available_modes
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error previewing plan: {str(e)}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
