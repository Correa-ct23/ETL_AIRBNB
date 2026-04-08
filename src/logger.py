"""
logger.py

Módulo centralizado para configurar logging en los scripts ETL.
Genera un archivo de log por ejecución con timestamp.
"""

import logging
import os
from datetime import datetime
from pathlib import Path


def setup_logger(script_name: str, log_level: int = logging.INFO) -> logging.Logger:
    """
    Configura y retorna un logger para el script dado.
    Crea un archivo de log único por ejecución: logs/log_YYYYMMDD_HHMM.txt

    Parámetros:
    - script_name: Nombre del script (e.g., 'extraccion', 'transformacion', 'carga')

    Retorna:
    - Logger configurado
    """
    # Crear directorio logs si no existe
    logs_dir = Path(__file__).resolve().parent.parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Generar nombre de archivo con timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    log_filename = f"log_{timestamp}.txt"
    log_path = logs_dir / log_filename

    # Crear logger
    logger = logging.getLogger(script_name)
    logger.setLevel(log_level)

    # Evitar múltiples handlers si se llama varias veces
    if not logger.handlers:
        # Formatter con fecha, hora, nivel y mensaje
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # FileHandler
        fh = logging.FileHandler(log_path, encoding='utf-8')
        fh.setLevel(log_level)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        # Opcional: StreamHandler para consola (puedes quitar si no quieres output en consola)
        # sh = logging.StreamHandler()
        # sh.setFormatter(formatter)
        # logger.addHandler(sh)

    return logger