# ETL_AIRBNB

## Descripción del Proyecto

Este proyecto implementa un proceso ETL (Extract, Transform, Load) completo para el procesamiento de datos de Airbnb. El objetivo principal es extraer datos desde una base de datos MongoDB (conteniendo las colecciones `listings`, `reviews` y `calendar`), realizar transformaciones de limpieza y normalización, y finalmente cargar los datos procesados en una base de datos SQLite o archivos XLSX para su posterior análisis y visualización.

El proyecto está estructurado en módulos reutilizables que permiten una ejecución eficiente y escalable del proceso ETL, con un sistema de logging centralizado que registra todas las operaciones realizadas.

## Instalación

### Creación del Entorno Virtual

Para aislar las dependencias del proyecto, se recomienda crear un entorno virtual:

```bash
python -m venv .venv
```

**Activación del entorno virtual:**

- En Windows:
  ```bash
  .venv\Scripts\activate
  ```

- En Linux/Mac:
  ```bash
  source .venv/bin/activate
  ```

### Instalación de Dependencias

Una vez activado el entorno virtual, instala las dependencias requeridas:

```bash
pip install -r requirements.txt
```

**Dependencias principales:**
- `pandas`: Para manipulación y análisis de datos
- `pymongo`: Para conexión y operaciones con MongoDB
- `python-dotenv`: Para carga de variables de entorno desde archivo .env

### Configuración Inicial

1. **Variables de entorno (opcional):** Crea un archivo `.env` en la raíz del proyecto con las siguientes variables si deseas personalizar la conexión a MongoDB:
   ```
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=ETL_AIRBNB
   MONGO_COLLS=listings,reviews,calendar
   ```

2. **Poblar MongoDB:** Si no tienes datos en MongoDB, ejecuta el notebook `notebooks/insert_data.ipynb` para descargar y cargar los datos de Airbnb desde las URLs públicas.

## Ejecución del Proyecto

### Proceso ETL Completo

El proceso ETL se puede ejecutar fácilmente usando el script principal `main.py`:

```bash
python main.py
```

Este script ejecuta automáticamente las tres etapas del ETL:
1. **EXTRACCIÓN**: Conecta a MongoDB y extrae los datos de las colecciones
2. **TRANSFORMACIÓN**: Limpia y normaliza los datos extraídos  
3. **CARGA**: Carga los datos transformados en una base de datos SQLite

### Ejecución por Etapas Individuales

Si necesitas más control, puedes ejecutar cada etapa por separado en un script personalizado:

```python
from src.extraccion import Extraccion
from src.transformacion import Transformacion
from src.carga import Carga

# 1. EXTRACCIÓN: Conectar a MongoDB y extraer datos
ext = Extraccion()
dfs = ext.extract_all()
ext.close()

# 2. TRANSFORMACIÓN: Limpiar y normalizar los datos
tr = Transformacion(dfs)
dfs_transformados = tr.transformar_todo()

# 3. CARGA: Cargar datos transformados a SQLite
carga = Carga()
carga.cargar_sqlite(dfs_transformados, 'airbnb.db')
```

### Ejecución por Etapas Individuales

También puedes ejecutar cada etapa por separado para mayor control:

```python
# Solo extracción
ext = Extraccion()
listings_df = ext.extract_collection('listings')
reviews_df = ext.extract_collection('reviews')
calendar_df = ext.extract_collection('calendar')
ext.close()

# Solo transformación
dfs = {'listings': listings_df, 'reviews': reviews_df, 'calendar': calendar_df}
tr = Transformacion(dfs)
dfs_limpios = tr.transformar_todo()

# Solo carga a XLSX
carga = Carga()
carga.exportar_xlsx(dfs_limpios, 'output_airbnb.xlsx')
```

## Integrantes del Grupo

- **Brayan Alexis Correa Torres**
- **Camilo Andrés Castrillon Quiroz**
- **Jhon Alejandro Isaza Pérez**

## Ejemplo de Ejecución del Proceso ETL

Para ejemplos completos de ejecución y análisis exploratorio, consulta los notebooks incluidos:

1. **`notebooks/insert_data.ipynb`**: Descarga datos de Airbnb desde URLs públicas y los carga en MongoDB local.

2. **`notebooks/exploracion_airbnb.ipynb`**: Análisis exploratorio de datos (EDA) con visualizaciones y estadísticas descriptivas.

Los logs de cada ejecución se guardarán automáticamente en la carpeta `logs/` con nombres como `log_20240107_1430.txt`.