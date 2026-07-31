import numpy as np
import rasterio
import matplotlib.pyplot as plt
from pathlib import Path
import cartopy.crs as ccrs
import datetime

def classify_kind(filepath: Path) -> str:
    """Classifies a raster file based on its filename.

    Analyzes the filename to determine the type of raster data it contains.
    Classification is based on keyword matching in the lowercase filename.

    Args:
        filepath: Path object pointing to the raster file to classify.

    Returns:
        A string indicating the file classification.
    """
    name = filepath.name.lower()
    if any(k in name for k in ["unw_phase", "color_phase", "lv_phi", "lv_theta"]):
        return "fase"
    if any(k in name for k in ["corr", "coh"]):
        return "coherencia"
    if "dem" in name:
        return "elevacion"
    if any(k in name for k in ["water_mask", "mask"]):
        return "ignorar"
    if filepath.suffix.lower() in [".tif", ".tiff"]:
        return "coherencia"
    return "desconocido"

def compute_stats(arr: np.ndarray, nodata=None):
    """Computes statistical measures for a raster array.

    Calculates various statistical metrics from a NumPy array, handling
    no-data values and non-finite numbers appropriately.

    Args:
        arr: NumPy array containing the raster data to analyze.
        nodata: Optional value representing no-data pixels. If provided,
        these values are treated as NaN before computing statistics.

    Returns:
        A dictionary containing statistical measures:
        - "min": Minimum finite value in the array.
        - "max": Maximum finite value in the array.
        - "mean": Mean of finite values.
        - "std": Standard deviation of finite values.
        - "p2": 2nd percentile value.
        - "p98": 98th percentile value.
        - "count": Total count of finite values.
        Returns an empty dictionary if no finite values are found.
    """
    a = arr.astype(float)
    if nodata is not None:
        a = np.where(a == nodata, np.nan, a)
    finite = np.isfinite(a)
    if not finite.any():
        return {}
    vals = a[finite]
    return {
        "min": float(np.nanmin(vals)),
        "max": float(np.nanmax(vals)),
        "mean": float(np.nanmean(vals)),
        "std": float(np.nanstd(vals)),
        "p2": float(np.nanpercentile(vals, 2)),
        "p98": float(np.nanpercentile(vals, 98)),
        "count": int(vals.size),
    }

def render_raster_tiff(in_path: Path, out_tiff: Path, title: str, cmap: str = "viridis", vmin=None, vmax=None, nodata=None):
    """Renders a raster file as a PNG image with color mapping.

    Reads a raster file, applies statistical normalization and color mapping,
    and saves the visualization as a PNG file with a colorbar.

    Args:
        in_path: Path to the input raster file to render.
        out_tiff: Path where the output PNG image will be saved.
        title: Title to display on the rendered image.
        cmap: Matplotlib color map name to use for visualization. Default is "viridis".
        vmin: Minimum value for color scaling. If None, uses the 2nd percentile
            from computed statistics.
        vmax: Maximum value for color scaling. If None, uses the 98th percentile
            from computed statistics.
        nodata: Value representing no-data pixels. If None, attempts to read from
            raster metadata. These pixels are converted to NaN.

    Returns:
        A dictionary containing statistical measures of the raster data,
        or an empty dictionary if no valid data is found.
    """
    with rasterio.open(in_path) as ds:
        data = ds.read(1)
        if nodata is None and ds.nodata is not None:
            nodata = ds.nodata

        stats = compute_stats(data, nodata=nodata)
        if vmin is None or vmax is None:
            if stats:
                vmin = stats.get("p2", vmin)
                vmax = stats.get("p98", vmax)

        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)

        fig, ax = plt.subplots(figsize=(10, 8))
        cax = ax.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax)
        ax.set_title(title, fontsize=14)
        cbar = fig.colorbar(cax)
        cbar.set_label('Valor', rotation=270, labelpad=15)

        plt.tight_layout()
        plt.savefig(out_tiff, format='png')
        plt.close(fig)

    return stats

def generar_mapa_el_salvador(ruta_tif: Path, salida_png: Path):
    with rasterio.open(ruta_tif) as src:
        data = src.read(1)
        extent = [
            src.bounds.left,
            src.bounds.right,
            src.bounds.bottom,
            src.bounds.top,
        ]

    plt.figure(figsize=(10, 8))
    ax = plt.axes(projection=ccrs.PlateCarree())
    ax.stock_img()
    ax.coastlines()
    im = ax.imshow(
        data,
        extent=extent,
        origin="upper",
        transform=ccrs.PlateCarree(),
        cmap="jet",
    )
    plt.colorbar(im, ax=ax, orientation="vertical", label="Deformación")
    plt.savefig(salida_png, dpi=150, bbox_inches="tight")
    plt.close()

def generar_imagen_sin_mapa(data, extent, outfile):
    masked = np.where(np.isfinite(data), data, np.nan)
    if np.isnan(masked).all():
        print("La imagen no contiene datos visibles.")
        return

    plt.figure(figsize=(10, 6))
    plt.imshow(masked, cmap="seismic", vmin=-5, vmax=5, extent=extent)
    plt.colorbar(label="Deformación (cm)")
    plt.title("Deformación estimada entre imágenes")
    plt.axis("off")
    plt.savefig(outfile, dpi=200, bbox_inches="tight")
    plt.close()
