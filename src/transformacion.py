"""
transformacion.py

Clase `Transformacion` para limpiar, normalizar y preparar los DataFrames
extraídos de MongoDB (listings, reviews, calendar) para su carga en una
base de datos analítica.

Uso mínimo:
    from extraccion import Extraccion
    from transformacion import Transformacion

    ext = Extraccion(uri='mongodb://...', db_name='ETL_AIRBNB')
    dfs = ext.extract_all()
    ext.close()

    tr = Transformacion(dfs)
    resultado = tr.transformar_todo()
"""
from __future__ import annotations

import logging
import re
import json
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import numpy as np


class Transformacion:
    """Clase encargada de transformar los DataFrames crudos de Airbnb.

    Implementa limpieza de nulos/duplicados, normalización de precios,
    conversión de fechas, derivación de variables temporales,
    categorización de precios, tratamiento de campos anidados y
    registro de cada paso en logs.

    Parámetros
    ----------
    dataframes : dict[str, pd.DataFrame]
        Diccionario con los DataFrames extraídos.
        Claves esperadas: 'listings', 'reviews', 'calendar'.
    log_path : str | None
        Ruta del fichero de log. Si es None se usa logs/logs.txt del proyecto.
    """

    # Rangos de precio por defecto (límite superior exclusivo, excepto el último)
    PRICE_BINS = [0, 50, 100, 200, 500, 1_000, float("inf")]
    PRICE_LABELS = ["0-49", "50-99", "100-199", "200-499", "500-999", "1000+"]

    def __init__(
        self,
        dataframes: Dict[str, pd.DataFrame],
        log_path: Optional[str] = None,
    ) -> None:
        # Copiar DataFrames para no alterar los originales
        self.dataframes: Dict[str, pd.DataFrame] = {
            k: v.copy() for k, v in dataframes.items()
        }
        # Ruta del log
        if log_path:
            self.log_path = log_path
        else:
            project_root = Path(__file__).resolve().parent.parent
            self.log_path = str(project_root / "logs" / "logs.log")

        # Configurar logger
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("Transformacion")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            fh = logging.FileHandler(self.log_path, encoding="utf-8")
            fh.setLevel(logging.INFO)
            fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            fh.setFormatter(fmt)
            self.logger.addHandler(fh)

    # ------------------------------------------------------------------
    # Métodos auxiliares
    # ------------------------------------------------------------------

    def _registros(self, nombre: str) -> int:
        """Retorna la cantidad actual de filas del DataFrame indicado."""
        return len(self.dataframes.get(nombre, pd.DataFrame()))

    @staticmethod
    def _limpiar_precio(valor) -> Optional[float]:
        """Convierte un valor de precio con símbolos ($, ,) a float.

        Ejemplos:
            '$1,200.00' -> 1200.0
            '850'       -> 850.0
            None / NaN  -> None
        """
        if valor is None or (isinstance(valor, float) and np.isnan(valor)):
            return None
        texto = str(valor).strip()
        # Eliminar símbolos de moneda y separadores de miles
        texto = texto.replace("$", "").replace(",", "").strip()
        if texto == "" or texto.lower() == "nan":
            return None
        try:
            return float(texto)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # 1. Limpieza de nulos y duplicados
    # ------------------------------------------------------------------

    def limpiar_nulos_duplicados(self) -> None:
        """Elimina filas duplicadas y registra nulos por columna en cada DataFrame.

        - Duplicados: se eliminan filas completamente idénticas.
        - Nulos: se registran en el log; NO se eliminan filas automáticamente
          para no perder información valiosa (cada caso se trata en su
          transformación específica).
        """
        for nombre, df in self.dataframes.items():
            antes = len(df)

            # --- Duplicados ---
            # Pandas .duplicated() puede fallar si hay elementos no-hashables
            # (listas/diccionarios). Convertimos temporalmente esas columnas a
            # representaciones hashables antes de detectar duplicados.
            try:
                # detectar columnas que contienen listas o dicts
                cols_unhashable = [
                    col
                    for col in df.columns
                    if df[col].apply(lambda x: isinstance(x, (list, dict))).any()
                ]
            except Exception:
                cols_unhashable = []

            if cols_unhashable:
                df_temp = df.copy()

                def _make_hashable(x):
                    if isinstance(x, list):
                        return tuple(x)
                    if isinstance(x, dict):
                        try:
                            return json.dumps(x, sort_keys=True)
                        except Exception:
                            return str(x)
                    return x

                for col in cols_unhashable:
                    df_temp[col] = df_temp[col].apply(_make_hashable)

                dup_mask = df_temp.duplicated()
            else:
                dup_mask = df.duplicated()

            duplicados = int(dup_mask.sum())
            if duplicados > 0:
                # eliminar duplicados en el DataFrame original según la máscara
                df = df.loc[~dup_mask].reset_index(drop=True)
                self.dataframes[nombre] = df
                self.logger.info(
                    f"[{nombre}] Eliminados {duplicados} duplicados "
                    f"({antes} -> {len(df)} registros)"
                )
            else:
                self.logger.info(f"[{nombre}] Sin duplicados ({antes} registros)")

            # --- Nulos por columna ---
            nulos = df.isnull().sum()
            cols_con_nulos = nulos[nulos > 0]
            if not cols_con_nulos.empty:
                detalle = ", ".join(
                    f"{col}: {cnt}" for col, cnt in cols_con_nulos.items()
                )
                self.logger.warning(
                    f"[{nombre}] Columnas con nulos -> {detalle}"
                )
            else:
                self.logger.info(f"[{nombre}] Sin valores nulos")

    # ------------------------------------------------------------------
    # 2. Normalización de precios
    # ------------------------------------------------------------------

    def normalizar_precios(self) -> None:
        """Normaliza columnas de precio eliminando '$' y ',' y convirtiendo a float.

        Aplica sobre:
        - listings: 'price' (y 'weekly_price', 'monthly_price', 'security_deposit',
          'cleaning_fee', 'extra_people' si existen).
        - calendar: 'price', 'adjusted_price' si existe.
        """
        # --- Listings ---
        if "listings" in self.dataframes:
            df = self.dataframes["listings"]
            columnas_precio = [
                "price", "weekly_price", "monthly_price",
                "security_deposit", "cleaning_fee", "extra_people",
            ]
            convertidas = []
            for col in columnas_precio:
                if col in df.columns:
                    df[col] = df[col].apply(self._limpiar_precio)
                    convertidas.append(col)
            self.logger.info(
                f"[listings] Precios normalizados en columnas: {convertidas}"
            )

        # --- Calendar ---
        if "calendar" in self.dataframes:
            df = self.dataframes["calendar"]
            columnas_precio_cal = ["price", "adjusted_price"]
            convertidas = []
            for col in columnas_precio_cal:
                if col in df.columns:
                    df[col] = df[col].apply(self._limpiar_precio)
                    convertidas.append(col)
            self.logger.info(
                f"[calendar] Precios normalizados en columnas: {convertidas}"
            )

    # ------------------------------------------------------------------
    # 3. Conversión de fechas a formato estándar YYYY-MM-DD
    # ------------------------------------------------------------------

    def convertir_fechas(self) -> None:
        """Convierte campos de fecha a datetime64 con formato YYYY-MM-DD.

        Aplica sobre:
        - reviews: 'date'
        - calendar: 'date'
        - listings: 'host_since', 'last_scraped', 'first_review', 'last_review'
          (si existen).
        """
        mapeo_fechas: Dict[str, List[str]] = {
            "reviews": ["date"],
            "calendar": ["date"],
            "listings": [
                "host_since", "last_scraped",
                "first_review", "last_review",
            ],
        }

        for nombre, columnas in mapeo_fechas.items():
            if nombre not in self.dataframes:
                continue
            df = self.dataframes[nombre]
            for col in columnas:
                if col not in df.columns:
                    continue
                antes_nulos = df[col].isnull().sum()
                df[col] = pd.to_datetime(df[col], errors="coerce")
                despues_nulos = df[col].isnull().sum()
                nuevos_nulos = despues_nulos - antes_nulos
                if nuevos_nulos > 0:
                    self.logger.warning(
                        f"[{nombre}] Conversión de '{col}': "
                        f"{nuevos_nulos} valores no pudieron convertirse a fecha"
                    )
                self.logger.info(
                    f"[{nombre}] Columna '{col}' convertida a datetime"
                )

    # ------------------------------------------------------------------
    # 4. Derivación de variables temporales
    # ------------------------------------------------------------------

    def derivar_variables_fecha(self) -> None:
        """Crea columnas derivadas (anio, mes, dia, trimestre) a partir de 'date'.

        Se aplica sobre reviews y calendar.
        """
        for nombre in ("reviews", "calendar"):
            if nombre not in self.dataframes:
                continue
            df = self.dataframes[nombre]
            if "date" not in df.columns:
                continue
            # Asegurar tipo datetime
            if not pd.api.types.is_datetime64_any_dtype(df["date"]):
                df["date"] = pd.to_datetime(df["date"], errors="coerce")

            df["anio"] = df["date"].dt.year
            df["mes"] = df["date"].dt.month
            df["dia"] = df["date"].dt.day
            df["trimestre"] = df["date"].dt.quarter

            self.logger.info(
                f"[{nombre}] Variables derivadas creadas: anio, mes, dia, trimestre"
            )

    # ------------------------------------------------------------------
    # 5. Categorización de precios por rangos
    # ------------------------------------------------------------------

    def categorizar_precios(
        self,
        bins: Optional[List[float]] = None,
        labels: Optional[List[str]] = None,
    ) -> None:
        """Crea la columna 'precio_rango' según los bins proporcionados.

        Parámetros
        ----------
        bins : list[float] | None
            Límites de los rangos. Por defecto [0, 50, 100, 200, 500, 1000, inf].
        labels : list[str] | None
            Etiquetas correspondientes a cada rango.

        Se aplica sobre listings y calendar (si la columna 'price' existe y es numérica).
        """
        bins = bins or self.PRICE_BINS
        labels = labels or self.PRICE_LABELS

        for nombre in ("listings", "calendar"):
            if nombre not in self.dataframes:
                continue
            df = self.dataframes[nombre]
            if "price" not in df.columns:
                continue
            # Solo categorizar si el campo ya es numérico
            if not pd.api.types.is_numeric_dtype(df["price"]):
                self.logger.warning(
                    f"[{nombre}] 'price' no es numérico; "
                    "ejecute normalizar_precios() primero."
                )
                continue

            df["precio_rango"] = pd.cut(
                df["price"], bins=bins, labels=labels, right=False
            )
            conteo = df["precio_rango"].value_counts().to_dict()
            self.logger.info(
                f"[{nombre}] Columna 'precio_rango' creada. Distribución: {conteo}"
            )

    # ------------------------------------------------------------------
    # 6. Expansión / tratamiento de campos anidados
    # ------------------------------------------------------------------

    @staticmethod
    def _contar_elementos(val) -> int:
        """Cuenta elementos en un campo que puede ser lista o string JSON."""
        if isinstance(val, list):
            return len(val)
        if isinstance(val, str):
            items = re.findall(r'"([^"]+)"', val)
            if not items:
                items = re.findall(r"'([^']+)'", val)
            return len(items)
        return 0

    def _eliminar_columna_id(self) -> None:
        """Elimina la columna '_id' de MongoDB en todos los DataFrames."""
        for nombre, df in self.dataframes.items():
            if "_id" in df.columns:
                df.drop(columns=["_id"], inplace=True)
                self.logger.info(f"[{nombre}] Columna '_id' eliminada")

    def expandir_campos_anidados(self) -> None:
        """Expande o aplana campos que contengan diccionarios o listas anidadas.

        Campos habituales en Airbnb:
        - listings.host_verifications (lista JSON como string)
        - listings.amenities (lista JSON como string)

        Además elimina la columna '_id' de MongoDB si existe, ya que es un
        ObjectId no serializable que no aporta valor analítico.
        """
        self._eliminar_columna_id()

        if "listings" not in self.dataframes:
            return

        df = self.dataframes["listings"]

        # --- amenities: convertir string JSON a cantidad de amenities ---
        if "amenities" in df.columns:
            df["amenities_count"] = df["amenities"].apply(self._contar_elementos)
            self.logger.info(
                "[listings] Campo 'amenities' procesado -> 'amenities_count'"
            )

        # --- host_verifications: convertir a cantidad ---
        if "host_verifications" in df.columns:
            df["host_verifications_count"] = df["host_verifications"].apply(
                self._contar_elementos
            )
            self.logger.info(
                "[listings] Campo 'host_verifications' procesado "
                "-> 'host_verifications_count'"
            )

        self.dataframes["listings"] = df

    # ------------------------------------------------------------------
    # 7. Pipeline completo de transformación
    # ------------------------------------------------------------------

    def transformar_todo(self) -> Dict[str, pd.DataFrame]:
        """Ejecuta todas las transformaciones en orden y retorna los DataFrames limpios.

        Orden de ejecución:
        1. Limpieza de nulos y duplicados
        2. Normalización de precios
        3. Conversión de fechas
        4. Derivación de variables temporales
        5. Categorización de precios
        6. Expansión de campos anidados

        Retorna
        -------
        dict[str, pd.DataFrame]
            Diccionario con los DataFrames transformados.
        """
        self.logger.info("=" * 60)
        self.logger.info("INICIO del proceso de transformación")
        self.logger.info("=" * 60)

        # Registrar cantidad de registros iniciales
        for nombre, df in self.dataframes.items():
            self.logger.info(f"[{nombre}] Registros iniciales: {len(df)}")

        # 1. Limpieza
        self.logger.info("-" * 40)
        self.logger.info("Paso 1: Limpieza de nulos y duplicados")
        self.limpiar_nulos_duplicados()

        # 2. Normalización de precios
        self.logger.info("-" * 40)
        self.logger.info("Paso 2: Normalización de precios")
        self.normalizar_precios()

        # 3. Conversión de fechas
        self.logger.info("-" * 40)
        self.logger.info("Paso 3: Conversión de fechas")
        self.convertir_fechas()

        # 4. Derivación de variables temporales
        self.logger.info("-" * 40)
        self.logger.info("Paso 4: Derivación de variables temporales")
        self.derivar_variables_fecha()

        # 5. Categorización de precios
        self.logger.info("-" * 40)
        self.logger.info("Paso 5: Categorización de precios por rangos")
        self.categorizar_precios()

        # 6. Campos anidados
        self.logger.info("-" * 40)
        self.logger.info("Paso 6: Expansión de campos anidados")
        self.expandir_campos_anidados()

        # Resumen final
        self.logger.info("=" * 60)
        self.logger.info("FIN del proceso de transformación")
        for nombre, df in self.dataframes.items():
            self.logger.info(f"[{nombre}] Registros finales: {len(df)}")
        self.logger.info("=" * 60)

        return self.dataframes


# ------------------------------------------------------------------
# Ejecución directa (demo / validación rápida)
# ------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys

    # Importar la clase de extracción del mismo paquete
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from extraccion import Extraccion

    # Construir URI de conexión
    db_name = os.environ.get("MONGO_DB", "ETL_AIRBNB")
    uri = os.environ.get("MONGO_URI")
    if not uri:
        user = os.environ.get("MONGO_USER", "AIRBNB")
        pwd = os.environ.get("MONGO_PWD", "12345")  # noqa: S105
        host = os.environ.get("MONGO_HOST", "localhost:27017")
        auth = os.environ.get("MONGO_AUTH", "ETL_AIRBNB")
        uri = f"mongodb://{user}:{pwd}@{host}/{db_name}?authSource={auth}"

    # 1. Extracción
    print("Extrayendo datos de MongoDB...")
    ext = Extraccion(uri=uri, db_name=db_name)
    dfs = ext.extract_all()
    ext.close()
    for nombre, df in dfs.items():
        print(f"  {nombre}: {len(df)} registros, {len(df.columns)} columnas")

    # 2. Transformación
    print("\nTransformando datos...")
    tr = Transformacion(dfs)
    resultado = tr.transformar_todo()

    # 3. Resumen
    print("\nResumen post-transformación:")
    for nombre, df in resultado.items():
        print(f"  {nombre}: {len(df)} registros, {len(df.columns)} columnas")
        print(f"    Columnas: {list(df.columns)}")
