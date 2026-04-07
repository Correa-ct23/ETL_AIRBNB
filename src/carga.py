"""
carga.py

Clase `Carga` para insertar DataFrames transformados en SQLite,
exportarlos a XLSX, verificar cargas y registrar eventos en logs.

Uso mínimo:
    from carga import Carga
    c = Carga(dfs)
    resultado = c.run_all()

"""
from __future__ import annotations

import logging
import json
import re
from pathlib import Path
from typing import Dict, Optional
import sqlite3
import pandas as pd


class Carga:
    """Carga de datos transformados.

    Parámetros
    ----------
    dataframes: dict[str, pd.DataFrame]
        Diccionario con los DataFrames transformados (p.ej. 'listings', 'reviews', 'calendar').
    sqlite_path: str | Path | None
        Ruta al archivo SQLite donde crear tablas. Por defecto: project_root/data/airbnb.db
    xlsx_dir: str | Path | None
        Carpeta donde exportar archivos XLSX. Por defecto: project_root/exports
    log_path: str | None
        Ruta del fichero de log (si None usa logs/logs.txt del proyecto).
    """

    def __init__(
        self,
        dataframes: Dict[str, pd.DataFrame],
        sqlite_path: Optional[str] = None,
        xlsx_dir: Optional[str] = None,
        log_path: Optional[str] = None,
    ) -> None:
        self.dataframes = {k: v.copy() for k, v in dataframes.items()}

        project_root = Path(__file__).resolve().parent.parent
        self.sqlite_path = Path(sqlite_path) if sqlite_path else project_root / "data" / "airbnb.db"
        self.xlsx_dir = Path(xlsx_dir) if xlsx_dir else project_root / "exports"

        if log_path:
            self.log_path = Path(log_path)
        else:
            self.log_path = project_root / "logs" / "logs.txt"

        # asegurar directorios
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.xlsx_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # logger
        self.logger = logging.getLogger("Carga")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            fh = logging.FileHandler(self.log_path, encoding="utf-8")
            fh.setLevel(logging.INFO)
            fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            fh.setFormatter(fmt)
            self.logger.addHandler(fh)

    def save_to_sqlite(self, if_exists: str = "replace") -> None:
        """Inserta todos los DataFrames en SQLite como tablas.

        - `if_exists` se pasa a `DataFrame.to_sql` (replace/append).
        """
        self.logger.info(f"Guardando {len(self.dataframes)} tablas en SQLite: {self.sqlite_path}")
        conn = sqlite3.connect(self.sqlite_path)
        try:
            for name, df in self.dataframes.items():
                self.logger.info(f"  - Escribiendo tabla '{name}' ({len(df)} registros)")
                # SQLite (sqlite3) no soporta tipos como list/dict en binding; serializamos
                # columnas que contengan listas o diccionarios a JSON strings.
                df_to_write = df.copy()
                cols_converted = []
                for col in df_to_write.columns:
                    try:
                        sample = df_to_write[col].dropna().iloc[0]
                    except Exception:
                        sample = None
                    if isinstance(sample, (list, dict)):
                        cols_converted.append(col)
                        df_to_write[col] = df_to_write[col].apply(
                            lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True) if x is not None else None
                        )

                if cols_converted:
                    self.logger.info(f"    - Columnas serializadas a JSON para SQLite: {cols_converted}")

                # to_sql maneja tipos pandas -> sqlite
                df_to_write.to_sql(name, conn, if_exists=if_exists, index=False)
            conn.commit()
            self.logger.info("Guardado en SQLite completado")
        except Exception as exc:
            conn.rollback()
            self.logger.exception(f"Error guardando en SQLite: {exc}")
            raise
        finally:
            conn.close()

    def export_to_xlsx(self, per_table_files: bool = False, filename: Optional[str] = None) -> None:
        """Exporta los DataFrames a XLSX.

        - Si `per_table_files` es False (por defecto), crea un solo libro con una hoja por tabla.
        - Si True, crea un archivo por cada tabla en `xlsx_dir/{table}.xlsx`.
        - `filename` permite definir el nombre del libro principal (si no se pasa: 'transformed.xlsx').
        """
        # helper: sanitize and convert unsupported types for Excel cells
        def _prepare_cell(val):
            if val is None:
                return None
            # convert lists/dicts to JSON strings
            if isinstance(val, (list, dict)):
                try:
                    s = json.dumps(val, ensure_ascii=False, sort_keys=True)
                except Exception:
                    s = str(val)
                # remove illegal xml chars
                return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)
            # bytes -> decode
            if isinstance(val, (bytes, bytearray)):
                try:
                    val = val.decode("utf-8", errors="ignore")
                except Exception:
                    val = str(val)
            # strings: remove control chars invalid for Excel XML
            if isinstance(val, str):
                return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", val)
            # other types (int,float,bool,datetime,...) are fine
            return val

        if per_table_files:
            for name, df in self.dataframes.items():
                # Excel limit: 1,048,576 rows. Reserve 1 row for header to avoid overflow.
                excel_max_rows = 1048576
                chunk_rows = excel_max_rows - 1
                total = len(df)
                if total == 0:
                    self.logger.info(f"Skipping empty table {name}")
                    continue

                if total <= chunk_rows:
                    out = self.xlsx_dir / f"{name}.xlsx"
                    self.logger.info(f"Exportando '{name}' a {out}")
                    try:
                        df_to_write = df.copy()
                        # apply sanitization column-wise
                        for col in df_to_write.columns:
                            try:
                                df_to_write[col] = df_to_write[col].apply(_prepare_cell)
                            except Exception:
                                df_to_write[col] = df_to_write[col].astype(str).apply(
                                    lambda v: re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", v)
                                )
                        df_to_write.to_excel(out, sheet_name=name[:31], index=False)
                    except Exception:
                        self.logger.exception(f"Fallo exportando {name} a XLSX")
                        raise
                else:
                    # split into multiple files using safe chunk size (leave room for header)
                    parts = (total + chunk_rows - 1) // chunk_rows
                    for i in range(parts):
                        start = i * chunk_rows
                        end = min((i + 1) * chunk_rows, total)
                        out = self.xlsx_dir / f"{name}_part{i+1}.xlsx"
                        self.logger.info(f"Exportando chunk {i+1}/{parts} de '{name}' a {out} rows {start}-{end}")
                        try:
                            df_chunk = df.iloc[start:end].copy()
                            for col in df_chunk.columns:
                                try:
                                    df_chunk[col] = df_chunk[col].apply(_prepare_cell)
                                except Exception:
                                    df_chunk[col] = df_chunk[col].astype(str).apply(
                                        lambda v: re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", v)
                                    )
                            df_chunk.to_excel(out, sheet_name=(name + f"_p{i+1}")[:31], index=False)
                        except Exception:
                            self.logger.exception(f"Fallo exportando chunk {i+1} de {name} a XLSX")
                            raise
            self.logger.info("Exportación a archivos XLSX por tabla completada")
            return

        # Un solo libro con múltiples hojas
        out_file = self.xlsx_dir / (filename or "transformed.xlsx")
        self.logger.info(f"Exportando todas las tablas a un libro XLSX: {out_file}")
        try:
            written_any = False
            with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
                for name, df in self.dataframes.items():
                    total = len(df)
                    if total == 0:
                        continue
                    excel_max_rows = 1048576
                    chunk_rows = excel_max_rows - 1
                    if total <= chunk_rows:
                        sheet = name[:31]
                        df_to_write = df.copy()
                        # sanitize column-wise
                        for col in df_to_write.columns:
                            try:
                                df_to_write[col] = df_to_write[col].apply(_prepare_cell)
                            except Exception:
                                df_to_write[col] = df_to_write[col].astype(str).apply(
                                    lambda v: re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", v)
                                )
                        df_to_write.to_excel(writer, sheet_name=sheet, index=False)
                        written_any = True
                    else:
                        parts = (total + chunk_rows - 1) // chunk_rows
                        for i in range(parts):
                            start = i * chunk_rows
                            end = min((i + 1) * chunk_rows, total)
                            sheet = (name + f"_p{i+1}")[:31]
                            df_chunk = df.iloc[start:end].copy()
                            for col in df_chunk.columns:
                                try:
                                    df_chunk[col] = df_chunk[col].apply(_prepare_cell)
                                except Exception:
                                    df_chunk[col] = df_chunk[col].astype(str).apply(
                                        lambda v: re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", v)
                                    )
                            df_chunk.to_excel(writer, sheet_name=sheet, index=False)
                            written_any = True

                # ensure at least one visible sheet exists
                if not written_any:
                    import pandas as _pd

                    _pd.DataFrame({"info": ["no tables to export"]}).to_excel(writer, sheet_name="sheet1", index=False)

            self.logger.info("Exportación a XLSX completada")
        except Exception:
            self.logger.exception("Fallo exportando a XLSX")
            raise

    def verify_load(self) -> Dict[str, bool]:
        """Verifica que las cantidades en SQLite coincidan con los DataFrames.

        Retorna dict[table] = True/False si coinciden.
        """
        self.logger.info("Verificando registros cargados en SQLite...")
        results: Dict[str, bool] = {}
        conn = sqlite3.connect(self.sqlite_path)
        try:
            cur = conn.cursor()
            for name, df in self.dataframes.items():
                try:
                    cur.execute(f"SELECT COUNT(*) FROM '{name}'")
                    count = cur.fetchone()[0]
                    ok = int(count) == int(len(df))
                    results[name] = ok
                    if ok:
                        self.logger.info(f"Verificado '{name}': {count} registros (OK)")
                    else:
                        self.logger.warning(
                            f"Verificado '{name}': sqlite={count} vs dataframe={len(df)} (MISMATCH)"
                        )
                except Exception:
                    self.logger.exception(f"Error verificando tabla '{name}' en SQLite")
                    results[name] = False
        finally:
            conn.close()
        return results

    def run_all(self, if_exists: str = "replace", per_table_files: bool = False, filename: Optional[str] = None) -> Dict[str, bool]:
        """Ejecuta pipeline completo: guardar en SQLite, exportar a XLSX y verificar.

        Devuelve el resultado de `verify_load()`.
        """
        self.logger.info("INICIO del proceso de carga")
        self.save_to_sqlite(if_exists=if_exists)
        self.export_to_xlsx(per_table_files=per_table_files, filename=filename)
        results = self.verify_load()
        self.logger.info("FIN del proceso de carga")
        return results


if __name__ == "__main__":
    # Ejecutable: extrae, transforma y carga usando variables de entorno o args
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(description="Ejecuta pipeline: Extraccion->Transformacion->Carga")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017?directConnection=true"), help="Mongo URI")
    parser.add_argument("--mongo-db", default=os.environ.get("MONGO_DB", "ETL_AIRBNB"), help="Mongo DB name")
    parser.add_argument("--sqlite-path", default=None, help="Ruta al archivo sqlite de salida")
    parser.add_argument("--xlsx-dir", default=None, help="Directorio de salida para XLSX")
    parser.add_argument("--per-table", action="store_true", help="Exportar un archivo XLSX por cada colección")
    args = parser.parse_args()

    print("Extrayendo datos de MongoDB...")
    # importar Extraccion/Transformacion intentando ambas formas (ejecución desde /src o desde project root)
    try:
        from extraccion import Extraccion
        from transformacion import Transformacion
    except Exception:
        try:
            from src.extraccion import Extraccion
            from src.transformacion import Transformacion
        except Exception as exc:
            print("No se pudo importar los módulos de extracción/transformación:", exc)
            sys.exit(1)

    ext = Extraccion(uri=args.mongo_uri, db_name=args.mongo_db)
    try:
        dfs = ext.extract_all()
    finally:
        ext.close()

    for name, df in dfs.items():
        print(f"  {name}: {len(df)} registros, {len(df.columns) if not df.empty else 0} columnas")

    print("\nTransformando datos...")
    tr = Transformacion(dfs)
    dfs_t = tr.transformar_todo()

    print("\nCargando y exportando datos...")
    c = Carga(dfs_t, sqlite_path=args.sqlite_path, xlsx_dir=args.xlsx_dir, log_path=None)
    results = c.run_all(per_table_files=args.per_table)
    print("Verificación de carga:", results)
