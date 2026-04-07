"""
extraccion.py

Clase `Extraccion` para conectar a MongoDB, extraer colecciones y cargar
los datos en DataFrames de pandas. Registra en un log la conexión y la
cantidad de registros extraídos por colección.

Uso mínimo:
    from extraccion import Extraccion
    ext = Extraccion(uri='mongodb://localhost:27017', db_name='mi_db')
    dfs = ext.extract_all()

"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import pandas as pd
except Exception as e:  # pragma: no cover - environment dependent
    raise ImportError("pandas is required to use Extraccion: pip install pandas") from e

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except Exception as e:  # pragma: no cover - environment dependent
    raise ImportError("pymongo is required to use Extraccion: pip install pymongo") from e


class Extraccion:
    """Clase para extracción de datos desde MongoDB a pandas.DataFrame.

    Parámetros
    ---------
    uri: str
        URI de conexión a MongoDB (por defecto 'mongodb://localhost:27017').
    db_name: Optional[str]
        Nombre de la base de datos a usar. Si es `None`, se usará la
        base de datos por defecto del cliente (si aplica) o se lanzará
        excepción al intentar acceder.
    log_path: str
        Ruta al fichero de log donde se registrarán conexiones y conteos.
    mongo_kwargs: dict
        Argumentos adicionales para `pymongo.MongoClient`.

    Métodos principales
    -------------------
    connect(): establece la conexión y prepara `self.db`.
    list_collections(): devuelve la lista de colecciones en la BD.
    extract_collection(name): devuelve un DataFrame con los documentos.
    extract_all(): extrae todas las colecciones y devuelve dict[name] = DataFrame.
    close(): cierra la conexión al cliente Mongo.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        db_name: Optional[str] = None,
        log_path: Optional[str] = None,
        **mongo_kwargs,
    ) -> None:
        # Prefer explicit uri, then environment, then sensible default
        env_uri = os.environ.get("MONGO_URI")
        self.uri = uri or env_uri or "mongodb://localhost:27017"

        # Prefer explicit db_name, then environment, then the project default
        self.db_name = db_name or os.environ.get("MONGO_DB") or "ETL_AIRBNB"
        if log_path:
            self.log_path = log_path
        else:
            project_root = Path(__file__).resolve().parent.parent
            self.log_path = str(project_root / "logs" / "logs.txt")
        self.mongo_kwargs = mongo_kwargs
        self.client: Optional[MongoClient] = None
        self.db = None

        # configurar logger
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("Extraccion")
        self.logger.setLevel(logging.INFO)
        # evitar múltiples handlers si se instancia varias veces
        if not self.logger.handlers:
            fh = logging.FileHandler(self.log_path, encoding="utf-8")
            fh.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)

    def connect(self):
        """Establece conexión con MongoDB y selecciona la base de datos.

        Registra en el log la conexión establecida.
        """
        if self.client:
            return self.db

        try:
            # Ensure directConnection is set unless already provided in kwargs or URI
            kwargs = dict(self.mongo_kwargs or {})
            if (
                "directConnection" not in kwargs
                and "directConnection=" not in (self.uri or "")
            ):
                kwargs["directConnection"] = True

            self.client = MongoClient(self.uri, **kwargs)
            # Forzar selección del servidor para detectar fallos de conexión inmediatamente
            self.client.admin.command("ping")
            if self.db_name:
                self.db = self.client[self.db_name]
            else:
                # si no hay db_name, usar la base por defecto si existe
                self.db = self.client.get_default_database()

            self.logger.info(f"Conectado a MongoDB: {self.uri} BD: {self.db_name}")
            return self.db
        except PyMongoError as exc:
            self.logger.exception(f"Error conectando a MongoDB: {exc}")
            raise

    def list_collections(self) -> list:
        """Retorna la lista de nombres de colecciones en la base de datos.
        Llama a `connect()` si es necesario.
        """
        db = self.connect()
        return db.list_collection_names()

    def extract_collection(self, collection: str, query: Optional[dict] = None) -> "pd.DataFrame":
        """Extrae una colección y retorna un `pandas.DataFrame`.

        También registra en el log la cantidad de documentos extraídos.
        """
        db = self.connect()
        q = query or {}
        cursor = db[collection].find(q)
        docs = list(cursor)
        df = pd.DataFrame(docs)
        count = len(df)
        self.logger.info(f"Extraídos {count} registros de la colección '{collection}'")
        return df

    def extract_all(
        self, query: Optional[dict] = None, allowed_collections: Optional[list] = None
    ) -> Dict[str, "pd.DataFrame"]:
        """Extrae colecciones y devuelve un diccionario de DataFrames.

        - Si `allowed_collections` se pasa, solo extrae esas colecciones (lista de nombres).
        - Si no se pasa, intenta leer la variable de entorno `MONGO_COLLS` (coma-separada).
        - Si no hay `MONGO_COLLS`, intenta leer `src/collections.json` en el paquete.
        - Si ninguno aparece, extrae todas las colecciones de la base de datos.
        """
        # determinar lista blanca de colecciones si no fue pasada
        if allowed_collections is None:
            env = os.environ.get("MONGO_COLLS")
            if env:
                allowed_collections = [c.strip() for c in env.split(",") if c.strip()]
            else:
                try:
                    cfg_path = Path(__file__).resolve().parent / "collections.json"
                    if cfg_path.exists():
                        import json

                        with cfg_path.open(encoding="utf-8") as fh:
                            allowed_collections = json.load(fh)
                except Exception:
                    allowed_collections = None

        cols = self.list_collections()
        # filtrar si hay lista permitida
        if allowed_collections:
            cols = [c for c in cols if c in allowed_collections]

        result: Dict[str, pd.DataFrame] = {}
        for c in cols:
            try:
                df = self.extract_collection(c, query=query)
            except Exception:
                self.logger.exception(f"Fallo extrayendo colección {c}")
                df = pd.DataFrame()
            result[c] = df
        return result

    def close(self) -> None:
        """Cierra la conexión con MongoDB."""
        if self.client:
            try:
                self.client.close()
                self.logger.info("Conexión a MongoDB cerrada")
            except Exception:
                self.logger.exception("Error cerrando la conexión a MongoDB")
            finally:
                self.client = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


if __name__ == "__main__":
    import os
    import sys

    # Valores por defecto: carga desde .env (si existe).
    # Use MONGO_URI y MONGO_DB si están presentes, sino caen a valores por defecto.
    db_name = os.environ.get("MONGO_DB", "ETL_AIRBNB")
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")

    ext = Extraccion(uri=uri, db_name=db_name)
    try:
        db = ext.connect()
    except Exception as e:
        print("No se pudo conectar a MongoDB:", e)
        sys.exit(1)

    cols = ext.list_collections()
    print(f"Collections in DB ({db_name}): {cols}")
    for c in cols:
        df = ext.extract_collection(c)
        print(f"- {c}: {len(df)} records")

    ext.close()
