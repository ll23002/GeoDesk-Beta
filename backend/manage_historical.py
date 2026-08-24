import argparse
import sys
from pathlib import Path

from utils.timeseries_parquet import VOLCANOES, save_from_mintpy_timeseries_h5

def main():
    parser = argparse.ArgumentParser(
        description="Genera archivos históricos Parquet a partir de un timeseries.h5 de MintPy."
    )
    parser.add_argument(
        "volcano",
        choices=list(VOLCANOES.keys()),
        help="El ID del volcán (ej. santa_ana, san_miguel, etc.)",
    )
    parser.add_argument(
        "year",
        type=int,
        help="El año al que corresponde este procesamiento (ej. 2024)",
    )
    parser.add_argument(
        "h5_file",
        type=str,
        help="Ruta al archivo timeseries.h5 generado por MintPy",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobreescribe el archivo Parquet si ya existe",
    )

    args = parser.parse_args()

    h5_path = Path(args.h5_file).resolve()
    if not h5_path.exists():
        print(f"ERROR: No se encontró el archivo h5 en {h5_path}")
        sys.exit(1)

    print(f"Volcán seleccionado: {args.volcano} ({VOLCANOES[args.volcano]['name']})")
    print(f"Año: {args.year}")
    print(f"Archivo de origen: {h5_path}")
    print("Iniciando extracción y conversión a Parquet...")

    try:
        # Extraer el Bounding Box de la configuración del volcán
        bbox = VOLCANOES[args.volcano]["bbox"]
        lat_min = min(bbox[0], bbox[2])
        lat_max = max(bbox[0], bbox[2])
        lon_min = min(bbox[1], bbox[3])
        lon_max = max(bbox[1], bbox[3])

        out_path = save_from_mintpy_timeseries_h5(
            h5_path=h5_path,
            volcano=args.volcano,
            year=args.year,
            lat_min=lat_min,
            lat_max=lat_max,
            lon_min=lon_min,
            lon_max=lon_max,
            overwrite=args.overwrite,
        )
        print(f"\nÉXITO: Serie temporal histórica guardada en:")
        print(f"   {out_path}")
        print("\nYa puedes visualizar este año en la plataforma web.")

    except Exception as exc:
        print(f"\nERROR durante la conversión: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    main()
