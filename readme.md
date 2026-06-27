# PYMEasyHR - Backend (Firebase Functions)

Este repositorio contiene el backend de **PYMEasyHR**, un sistema automatizado para la gestión de asistencia, fichajes de empleados con geofence (validación de ubicación GPS) e integración con herramientas de terceros como Google Sheets y EmailJS.

El sistema está desarrollado sobre **Firebase Functions (v2)** utilizando Python y persistencia en **Cloud Firestore**.

## 🚀 Características Principales

* **Fichaje Seguro con Geolocalización**: Registro de entradas/salidas con verificación de distancia respecto a la oficina (Radio máximo de 200 metros).
* **Seguridad Hashing**: Integridad de datos mediante generación de hashes SHA-256 por cada evento de fichaje.
* **Triggers de Firestore**: Automatización en segundo plano para procesar nuevos fichajes.
* **Sincronización con Google Sheets**: Volcado en tiempo real de los registros de asistencia.
* **Notificaciones por Email**: Envío automático de confirmaciones de fichaje mediante EmailJS.
* **Gestión de Empleados**: Endpoints de administración para la creación de usuarios en Firebase Auth y perfiles en Firestore.

---

## 📂 Estructura de Archivos del Servicio

```text
├── main.py          # Punto de entrada principal, definición de endpoints HTTPS y triggers.
└── services/
    ├── __init__.py  # Inicializador del módulo de servicios.
    ├── email.py     # Integración con la API de EmailJS para envío de correos.
    ├── secrets.py   # Gestor de credenciales con Google Cloud Secret Manager.
    └── sheets.py    # Conector y escritor para la API de Google Sheets mediante gspread.
```

### 🔐 Variables de Entorno a configurar en Render (Environment Variables)

Añade las siguientes claves directamente en la sección Environment de tu servicio en Render:

| Key | Value|
|-------|-------|
|FIREBASE_SERVICE_ACCOUNT_JSON|El contenido completo (en una sola línea) de tu archivo .json de la cuenta de servicio de Firebase para que el Admin SDK se autentique fuera de GCP.|
|PYMEASYHR_SHEET_ID|Tu ID de la hoja de cálculo de Google Sheets.|
|PYMEASYHR_SHEETS_KEY|El JSON de la cuenta de servicio autorizada en la hoja de cálculo.|
|EMAILJS_SERVICE_ID|Tu Service ID de EmailJS.|
|EMAILJS_TEMPLATE_ID|Tu Template ID de EmailJS.|
|EMAILJS_USER_ID|Tu Public Key de EmailJS.|
|EMAILJS_ACCESS_TOKEN|Tu Private Key / Access Token de EmailJS.|



## Configuración con `Render`

### 🚀 Paso 1

+ Sube el código a GitHub o GitLab.

### 🌐 Paso 2

+ Crear el servicio en Render

1. Ve a la consola de Render e inicia sesión (puedes usar tu cuenta de GitHub).

2. Haz clic en el botón "New +" (Nuevo) en la esquina superior derecha.

3. Selecciona Web Service (Servicio Web).

### ⚙️ Paso 3

+ Configurar el despliegue

- En la pantalla de configuración, rellena los siguientes campos clave:

- Name: El nombre de tu aplicación (definirá tu URL gratuita: ://onrender.com).
- Region: Selecciona la más cercana a tus usuarios (ej. Frankfurt para Europa o Ohio/Oregon para América).
- Branch: La rama de Git que quieres desplegar (normalmente main o master).
- Language: Selecciona Python.
- Root Directory: `backend_render`
- Build Command: El comando para instalar las librerías. Por defecto es:
```bash
pip install -r requirements.txt
```
- Start Command (Comando de arranque en producción):
```bash
gunicorn main:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
```

+ Start Command: El comando exacto para encender tu aplicación en producción:

- Si usas FastAPI/Uvicorn: uvicorn main:app --host 0.0.0.0 --port $PORT
- Si usas Flask/Gunicorn: gunicorn app:app

+ Instance Type: Selecciona el plan Free (Gratuito).

### Paso 4

+ Variables de entorno (Opcional)

Si tu proyecto utiliza credenciales secretas, claves de API o conexiones a bases de datos (archivos .env):

1. En esa misma pantalla de configuración, baja hasta la sección Advanced (Avanzado).
2. Haz clic en Add Environment Variable.
3. Añade tus variables (ejemplo: Clave DATABASE_URL y su Valor correspondiente). Nunca subas el archivo .env a GitHub.

### 🚀 Paso 5

+ Desplegar y Monitorear

1. Haz clic en Deploy Web Service al final de la página.
2. Render abrirá una consola con los Logs de compilación. Verás cómo descarga Python, instala tus librerías y arranca el servidor.
3. Una vez termine (dirá Live), verás la URL de tu proyecto debajo del nombre de tu aplicación en la esquina superior izquierda. ¡Haz clic en ella para probarla!


