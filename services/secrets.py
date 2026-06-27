import os

def obtener_secreto(nombre_secreto):
    """Lee las credenciales desde las variables de entorno de Render."""
    # Convierte 'pymeasyhr-sheet-id' en 'PYMEASYHR_SHEET_ID'
    env_var_name = nombre_secreto.upper().replace("-", "_")
    valor = os.environ.get(env_var_name)
    
    if not valor:
        print(f"❌ Error: La variable de entorno {env_var_name} no está configurada.")
        raise ValueError(f"Falta la configuración para {env_var_name}")
        
    return valor.strip()