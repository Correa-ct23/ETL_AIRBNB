from src.extraccion import Extraccion
from src.transformacion import Transformacion
from src.carga import Carga

def main():
    print("Iniciando proceso ETL...")
    
    # Extracción
    ext = Extraccion()
    dfs = ext.extract_all()
    ext.close()
    
    # Transformación
    tr = Transformacion(dfs)
    dfs_transformados = tr.transformar_todo()
    
    # Carga
    carga = Carga()
    carga.load_from_dict(dfs_transformados, db_path='airbnb_analitico.db')
    
    print("Proceso ETL completado exitosamente!")

if __name__ == "__main__":
    main()