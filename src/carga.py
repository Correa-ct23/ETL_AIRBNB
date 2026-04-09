import os
import time
import logging
import sqlite3
from typing import Optional, List, Tuple

from pathlib import Path
import pandas as pd
import json
import datetime
import re
import numpy as np

from .logger import setup_logger


class Carga:
    """Clase para cargar datos transformados rápidamente.

    Funcionalidades:
    - insertar DataFrame en SQLite de forma eficiente (batch + PRAGMA tuning)
    - exportar a uno o varios archivos XLSX
    - verificar conteo de registros cargados
    - registrar eventos principales en logs
    """

    def __init__(self, log_level: int = logging.INFO):
        self.logger = setup_logger("Carga", log_level)

    def _map_dtype(self, dtype) -> str:
        kind = getattr(dtype, "kind", "O")
        if kind in ("i",):
            return "INTEGER"
        if kind in ("f",):
            return "REAL"
        if kind in ("b",):
            return "INTEGER"
        if kind in ("M",):
            return "TEXT"
        return "TEXT"

    def _create_table_if_not_exists(self, conn: sqlite3.Connection, table: str, df: pd.DataFrame):
        cols = []
        for c, dt in zip(df.columns, df.dtypes):
            sql_type = self._map_dtype(dt)
            cols.append(f'"{c}" {sql_type}')
        ddl = f"CREATE TABLE IF NOT EXISTS \"{table}\" ({', '.join(cols)})"
        conn.execute(ddl)

    def _pythonize_value(self, x):
        """Convierte valores de pandas/numpy a tipos compatibles con sqlite3.

        - NaN/NaT/None -> None
        - Timestamp/datetime/date/numpy datetime -> ISO string
        - numpy scalars -> Python scalars
        - lists/dicts/ndarray -> JSON string
        - otros -> se devuelve tal cual
        """
        # manejar nulos primero
        try:
            if pd.isna(x):
                return None
        except Exception:
            # pd.isna puede devolver arrays para estructuras complejas
            pass

        # pandas Timestamp / datetime
        if isinstance(x, (pd.Timestamp, datetime.datetime, datetime.date)):
            try:
                return x.isoformat()
            except Exception:
                return str(x)

        # numpy datetime
        if isinstance(x, np.datetime64):
            try:
                return pd.to_datetime(x).isoformat()
            except Exception:
                return str(x)

        # numpy scalar types
        if isinstance(x, (np.integer,)):
            return int(x)
        if isinstance(x, (np.floating,)):
            return float(x)
        if isinstance(x, (np.bool_,)):
            return bool(x)

        # lists, tuples, dicts, numpy arrays -> JSON
        if isinstance(x, (list, tuple, dict, np.ndarray)):
            try:
                return json.dumps(x, default=str, ensure_ascii=False)
            except Exception:
                return str(x)

        return x

    def insert_into_sqlite(self, df: pd.DataFrame, db_path: str = "datos.db", table: str = "datos",
                           batch_size: int = 10000) -> int:
        """Inserta `df` en SQLite de forma eficiente.

        Retorna el número de registros insertados.
        """
        start = time.time()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        if df is None or df.shape[0] == 0:
            self.logger.info("DataFrame vacío: nada que insertar.")
            return 0

        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        try:
            # Speedups
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("PRAGMA journal_mode = MEMORY")
            conn.execute("PRAGMA temp_store = MEMORY")

            self._create_table_if_not_exists(conn, table, df)

            cols = [f'"{c}"' for c in df.columns]
            placeholders = ",".join(["?" for _ in cols])
            sql = f"INSERT INTO \"{table}\" ({', '.join(cols)}) VALUES ({placeholders})"

            total = 0
            conn.execute("BEGIN")
            it = df.itertuples(index=False, name=None)
            batch = []
            for row in it:
                # convertir cada valor a tipos Python/JSON compatibles
                vals = tuple(self._pythonize_value(x) for x in row)
                batch.append(vals)
                if len(batch) >= batch_size:
                    conn.executemany(sql, batch)
                    total += len(batch)
                    batch.clear()
            if batch:
                conn.executemany(sql, batch)
                total += len(batch)
            conn.commit()
            elapsed = time.time() - start
            self.logger.info(f"Insertados {total} registros en {db_path}:{table} en {elapsed:.2f}s")
            return total
        except Exception:
            conn.rollback()
            self.logger.exception("Error durante la inserción a SQLite")
            raise
        finally:
            conn.close()

    def export_to_xlsx(self, df: pd.DataFrame, output_path: str = "output.xlsx",
                       rows_per_file: Optional[int] = None, sheet_name: str = "Sheet1") -> List[str]:
        """Exporta el DataFrame a uno o varios archivos XLSX.

        Si `rows_per_file` se proporciona, dividirá el DataFrame en múltiples archivos.
        Retorna la lista de paths escritos.
        """
        start = time.time()
        if df is None or df.shape[0] == 0:
            self.logger.info("DataFrame vacío: nada que exportar a XLSX.")
            return []

        # Sanitizar el DataFrame para evitar caracteres ilegales en Excel
        def _sanitize_df_for_excel(df_in: pd.DataFrame) -> pd.DataFrame:
            df2 = df_in.copy()
            illegal = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

            for col in df2.columns:
                # Objetos/strings
                if df2[col].dtype == object:
                    def _clean(x):
                        # Primero tratar colecciones/arrays/dicts
                        if isinstance(x, (list, tuple, dict, np.ndarray)):
                            try:
                                s = json.dumps(x, ensure_ascii=False)
                            except Exception:
                                s = str(x)
                            return illegal.sub("", s)

                        # Nulos escalares
                        try:
                            if pd.isna(x):
                                return None
                        except Exception:
                            # pd.isna puede devolver arrays para estructuras complejas
                            pass

                        # Finalmente convertir a string y eliminar chars ilegales
                        s = str(x)
                        return illegal.sub("", s)

                    df2[col] = df2[col].map(_clean)
                # datetimes -> iso strings
                elif pd.api.types.is_datetime64_any_dtype(df2[col]):
                    df2[col] = df2[col].apply(lambda x: x.isoformat() if pd.notna(x) else None)
                else:
                    # convertir numpy scalars a python nativos
                    df2[col] = df2[col].apply(lambda x: self._pythonize_value(x))

            return df2

        df_safe = _sanitize_df_for_excel(df)

        # Excel limits
        MAX_EXCEL_ROWS = 1_048_576
        # Si el DataFrame excede el límite y no se pidió división explícita,
        # se activa división automática por el tamaño máximo de hoja.
        if rows_per_file is None and len(df_safe) > MAX_EXCEL_ROWS:
            self.logger.warning(
                f"DataFrame con {len(df_safe)} filas excede límite de Excel ({MAX_EXCEL_ROWS}). Dividiendo automáticamente."
            )
            rows_per_file = MAX_EXCEL_ROWS

        outputs: List[str] = []
        try:
            if rows_per_file is None or rows_per_file >= len(df_safe):
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                engine = "xlsxwriter"
                try:
                    with pd.ExcelWriter(output_path, engine=engine) as writer:
                        df_safe.to_excel(writer, index=False, sheet_name=sheet_name)
                except Exception:
                    # fallback a openpyxl
                    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                        df_safe.to_excel(writer, index=False, sheet_name=sheet_name)
                outputs.append(output_path)
                elapsed = time.time() - start
                self.logger.info(f"Exportado {len(df_safe)} filas a {output_path} en {elapsed:.2f}s")
                return outputs

            # Split into multiple sheets in the same workbook
            n = len(df_safe)
            parts = (n + rows_per_file - 1) // rows_per_file
            base_dir = os.path.dirname(output_path) or "."
            os.makedirs(base_dir, exist_ok=True)
            out = output_path
            engine = "xlsxwriter"
            try:
                with pd.ExcelWriter(out, engine=engine) as writer:
                    for i in range(parts):
                        start_row = i * rows_per_file
                        end_row = min((i + 1) * rows_per_file, n)
                        part_df = df_safe.iloc[start_row:end_row]
                        # Sheet names must be <=31 chars
                        sheet = sheet_name if i == 0 else f"{sheet_name}_part{i+1}"
                        if len(sheet) > 31:
                            sheet = sheet[:28] + f"_{i+1}"
                        part_df.to_excel(writer, index=False, sheet_name=sheet)
                        self.logger.info(f"Exportado filas {start_row}:{end_row} a hoja '{sheet}' in {out}")
                outputs.append(out)
            except Exception:
                # Fallback to openpyxl
                with pd.ExcelWriter(out, engine="openpyxl") as writer:
                    for i in range(parts):
                        start_row = i * rows_per_file
                        end_row = min((i + 1) * rows_per_file, n)
                        part_df = df_safe.iloc[start_row:end_row]
                        sheet = sheet_name if i == 0 else f"{sheet_name}_part{i+1}"
                        if len(sheet) > 31:
                            sheet = sheet[:28] + f"_{i+1}"
                        part_df.to_excel(writer, index=False, sheet_name=sheet)
                        self.logger.info(f"Exportado filas {start_row}:{end_row} a hoja '{sheet}' in {out}")
                outputs.append(out)

            elapsed = time.time() - start
            self.logger.info(f"Exportación completa en {elapsed:.2f}s ({len(outputs)} archivos) -> {out} con {parts} sheets")
            return outputs
        except Exception:
            self.logger.exception("Error durante exportación a XLSX")
            raise

    def verify_load(self, db_path: str = "datos.db", table: str = "datos", expected_count: Optional[int] = None) -> Tuple[int, bool]:
        """Verifica el número de registros cargados en la tabla.

        Retorna una tupla: (conteo_en_db, coincide_con_expected_o_None).
        """
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM \"{table}\"")
            (count,) = cur.fetchone()
            ok = (expected_count is None) or (count == expected_count)
            self.logger.info(f"Verificación: {count} registros en {db_path}:{table} (esperado={expected_count}) -> ok={ok}")
            return count, ok
        except Exception:
            self.logger.exception("Error durante verificación de carga")
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass


    def load_from_dict(
        self,
        dataframes: dict,
        db_path: str = "datos.db",
        xlsx_dir: str = "output",
        batch_size: int = 10000,
        rows_per_file: Optional[int] = None,
        export_xlsx: bool = True,
    ) -> dict:
        """Carga todos los DataFrames del dict en SQLite y opcionalmente los exporta a XLSX.

        Parámetros
        - dataframes: dict[name] = pd.DataFrame
        - db_path: ruta a la base SQLite
        - xlsx_dir: directorio donde escribir archivos XLSX (por colección)
        - batch_size: tamaño de lote para inserciones
        - rows_per_file: si se establece, parte la exportación en múltiples archivos
        - export_xlsx: si False, no exporta XLSX

        Retorna un dict con resultados por colección: {name: {"inserted": int, "xlsx_files": [...], "verify": (count, ok)}}
        """
        results = {}
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        if export_xlsx:
            os.makedirs(xlsx_dir, exist_ok=True)

        for name, df in dataframes.items():
            try:
                self.logger.info(f"Iniciando carga para colección: {name}")
                inserted = self.insert_into_sqlite(df, db_path=db_path, table=name, batch_size=batch_size)
                xlsx_files = []
                if export_xlsx:
                    out_path = os.path.join(xlsx_dir, f"{name}.xlsx")
                    xlsx_files = self.export_to_xlsx(df, output_path=out_path, rows_per_file=rows_per_file)
                verify = self.verify_load(db_path=db_path, table=name, expected_count=len(df))
                results[name] = {"inserted": inserted, "xlsx_files": xlsx_files, "verify": verify}
            except Exception:
                self.logger.exception(f"Fallo cargando colección {name}")
                results[name] = {"inserted": 0, "xlsx_files": [], "verify": (0, False)}

        return results


if __name__ == "__main__":
    # Orquestador: Extrae desde Mongo, transforma y carga.
    import sys
    from pathlib import Path

    # Añadir carpeta src al path para imports locales
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    try:
        from extraccion import Extraccion
        from transformacion import Transformacion
    except Exception:
        print("No se pueden importar Extraccion/Transformacion. Asegúrese de ejecutar desde el proyecto.")
        raise

    # Configuración básica (leer .env o variables de entorno)
    db_name = os.environ.get("MONGO_DB", "ETL_AIRBNB")
    uri = os.environ.get("MONGO_URI")
    if not uri:
        user = os.environ.get("MONGO_USER", "AIRBNB")
        pwd = os.environ.get("MONGO_PWD", "12345")  # noqa: S105
        host = os.environ.get("MONGO_HOST", "localhost:27017")
        auth = os.environ.get("MONGO_AUTH", "ETL_AIRBNB")
        uri = f"mongodb://{user}:{pwd}@{host}/{db_name}?authSource={auth}"

    print("Extrayendo datos de MongoDB...")
    ext = Extraccion(uri=uri, db_name=db_name)
    dfs = ext.extract_all()
    ext.close()

    print("Transformando datos...")
    tr = Transformacion(dfs)
    resultado = tr.transformar_todo()

    print("Cargando datos transformados (SQLite + XLSX)...")
    c = Carga()
    db_out = os.environ.get("OUTPUT_DB", "datos_transformados.db")
    out_dir = os.environ.get("OUTPUT_XLSX_DIR", str(Path(__file__).resolve().parent.parent / "output"))
    inicio = time.time()
    report = c.load_from_dict(resultado, db_path=db_out, xlsx_dir=out_dir, batch_size=10000, rows_per_file=None)
    total_time = time.time() - inicio

    print("Resumen de carga:")
    for name, info in report.items():
        print(f" - {name}: inserted={info['inserted']} verify={info['verify']} xlsx_files={len(info['xlsx_files'])}")
    print(f"Tiempo total de carga: {total_time:.2f}s")
