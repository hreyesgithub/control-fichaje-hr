import os
import json
import logging
import hashlib
import math
from datetime import datetime, timezone, UTC

# Importaciones de FastAPI
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware

# Importación de la librería base de Google Cloud para resolver el tipado de SERVER_TIMESTAMP
from google.cloud import firestore as gcloud_firestore

# Importaciones de Firebase Admin SDK
import firebase_admin
from firebase_admin import auth, firestore as admin_firestore

# Forzar la ruta temporal si no existe entorno gráfico
if "APPDATA" not in os.environ:
    os.environ["APPDATA"] = "/tmp"

# Inicializar la aplicación FastAPI
app = FastAPI(title="Backend PYMEasyHR", version="2.0.0")

# Configuración nativa de CORS (Render manejará los preflights automáticamente)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializar Firebase Admin SDK en Render externo a GCP
if not firebase_admin._apps:
    service_account_env = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_env:
        cred = firebase_admin.credentials.Certificate(json.loads(service_account_env))
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

logger = logging.getLogger(__name__)


def calcular_distancia(lat1, lon1, lat2, lon2):
    """Calcula la distancia en metros entre dos coordenadas"""
    R = 6371000  # Radio de la Tierra en metros
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def emular_trigger_fichaje(company_id: str, employee_id: str, fichaje_data: dict):
    """
    Sustituye al trigger de Firestore. Se ejecuta de forma asíncrona en el ThreadPool
    de Render sin bloquear la respuesta inmediata de la API de cara al usuario.
    """
    logger.info(">>> Fase 2: Tarea en segundo plano activada. Preparando envío a Sheets y Email.")
    try:
        db = admin_firestore.client()
        timestamp_str = datetime.now(UTC).isoformat()
        
        # Consultar datos del empleado
        empleado_ref = (
            db.collection("companies")
            .document(company_id)
            .collection("employees")
            .document(employee_id)
        )
        # 💡 Aplicamos Any aquí también para blindar el archivo contra Pylance
        from typing import Any
        empleado_doc: Any = empleado_ref.get()
        
        nombre_empleado = "Desconocido"
        activo = "Desconocido"
        email_empleado = None
        
        if empleado_doc.exists:
            datos_emp = empleado_doc.to_dict()
            if datos_emp:
                nombre_empleado = datos_emp.get("nombre_completo", "Sin Nombre")
                activo = datos_emp.get("activo", "N/C")
                email_empleado = datos_emp.get("email")
                logger.info(f"Nombre encontrado: {nombre_empleado}")
        else:
            logger.warning(f"No se encontró el documento del empleado {employee_id}")

        from services.sheets import escribir_en_google_sheets
        
        datos_compartidos = {
            "timestamp": timestamp_str,
            "employee_id": employee_id,
            "employee_name": nombre_empleado,
            "employee_email": email_empleado,
            "activo": activo,
            "event_type": (
                "ENTRADA" if fichaje_data.get("event_type") == "IN" else "SALIDA"
            ),
            "source": fichaje_data.get("source", "web"),
            "geo_data": fichaje_data.get("geo_data") or {},
        }
        
        resultado = escribir_en_google_sheets(datos_compartidos)
        logger.info(f"Sheets actualizado para {nombre_empleado}: {resultado}")
        
        if email_empleado:
            from services.email import enviar_email_confirmacion
            res_email = enviar_email_confirmacion(datos_compartidos)
            logger.info(f"Email: {res_email}")
        else:
            logger.info("Se saltó el envío de email: el empleado no tiene correo registrado.")
            
    except Exception as e:
        logger.error(f">>> ERROR CRÍTICO en tarea en segundo plano: {str(e)}", exc_info=True)


@app.get("/")
def hello_pyme():
    return {"status": "success", "message": "Backend PYMEasyHR operativo en Render"}


@app.post("/registrar_fichaje")
def registrar_fichaje(request: Request, background_tasks: BackgroundTasks, data: dict = Body(...)):
    print("LOG: La función registrar_fichaje ha sido invocada")
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "3600",
    }
    
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No autorizado: Falta Token")
        
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        email_autenticado = decoded_token.get("email")
        uid_usuario = decoded_token.get("uid")
        
        if not uid_usuario:
            raise HTTPException(status_code=401, detail="Token inválido: no se encontró UID.")
        if not email_autenticado:
            raise HTTPException(status_code=401, detail="Token inválido: no se encontró email.")
            
        db = admin_firestore.client()
        company_id = "COMP_987"
        
        empleados_ref = (
            db.collection("companies").document(company_id).collection("employees")
        )
        query = empleados_ref.where("email", "==", email_autenticado).limit(1).get()
        
        if len(query) == 0:
            print(f"DEBUG: No se encontró el email {email_autenticado} en COMP_987")
            raise HTTPException(status_code=404, detail="Empleado no encontrado en BD")
            
        empleado_doc_snapshot = query[0]
        employee_id_verificado = empleado_doc_snapshot.id
        
        client_geo = data.get("geo_data")
        if not client_geo or "lat" not in client_geo:
            raise HTTPException(status_code=400, detail="Error: Se requiere ubicación GPS.")
        
        # 💡 Forzamos a Any para solucionar el falso positivo de Pylance Awaitable
        from typing import Any
        company_doc: Any = db.collection("companies").document(company_id).get()
            
        company_doc = db.collection("companies").document(company_id).get()
        if not company_doc.exists:
            raise HTTPException(status_code=404, detail="Error: Configuración de empresa no encontrada.")
            
        company_data = company_doc.to_dict()
        if not company_data:
            raise HTTPException(status_code=404, detail="Error: Datos de la empresa vacíos.")
            
        office_geo = company_data.get("office_location")
        if not office_geo or "lat" not in office_geo or "lon" not in office_geo:
            raise HTTPException(status_code=400, detail="Error: Ubicación de oficina inválida.")
            
        distancia = calcular_distancia(
            client_geo["lat"], client_geo["lon"], office_geo["lat"], office_geo["lon"]
        )
        
        RADIO_MAXIMO = 200
        if distancia > RADIO_MAXIMO:
            logger.warning(f"Intento de fichaje fuera de rango: {distancia:.2f} metros.")
            return Response(
                content=json.dumps({
                    "status": "denied",
                    "message": f"Fuera de rango. Estás a {int(distancia)}m de la oficina.",
                    "distancia": int(distancia),
                }),
                status_code=403,
                media_type="application/json",
                headers=headers
            )
            
        event_type = data.get("event_type")
        source = data.get("source", "web_secure")
        if event_type not in ["IN", "OUT", "BREAK_START", "BREAK_END"]:
            raise HTTPException(status_code=400, detail="Tipo de evento no válido")
            
        timestamp_ahora = datetime.now(timezone.utc)
        nuevo_fichaje = {
            "employee_id": employee_id_verificado,
            "company_id": company_id,
            "event_type": event_type,
            "source": source,
            "work_date": timestamp_ahora.strftime("%Y-%m-%d"),
            "timestamp": gcloud_firestore.SERVER_TIMESTAMP,  # Solucionado usando google.cloud.firestore
            "timezone": "Europe/Madrid",
            "ip_address": request.headers.get("X-Forwarded-For", "Desconocida"),
            "user_agent": request.headers.get("User-Agent", "Desconocido"),
            "geo_data": data.get("geo_data"),
        }
        
        try:
            string_para_hash = f"{employee_id_verificado}{timestamp_ahora.isoformat()}{event_type}{company_id}"
            nuevo_fichaje["data_hash"] = hashlib.sha256(string_para_hash.encode()).hexdigest()
        except Exception as e:
            logger.warning(f"No se pudo calcular el hash: {e}")
            
        user_doc_ref = (
            db.collection("companies")
            .document(company_id)
            .collection("clock_events")
            .document(uid_usuario)
        )
        user_doc_ref.set(
            {
                "employee_id": employee_id_verificado,
                "email": email_autenticado,
                "company_id": company_id,
                "auth_uid": uid_usuario,
                "updated_at": gcloud_firestore.SERVER_TIMESTAMP,  # Solucionado
            },
            merge=True,
        )
        
        events_ref = user_doc_ref.collection("events")
        event_doc_ref = events_ref.add(nuevo_fichaje)
        event_id = event_doc_ref[1].id
        
        logger.info(f"Fichaje registrado para {employee_id_verificado} (Email: {email_autenticado})")
        
        # Encolar la sincronización en segundo plano de manera limpia y síncrona
        background_tasks.add_task(emular_trigger_fichaje, company_id, employee_id_verificado, nuevo_fichaje)
        
        return Response(
            content=f"Fichaje {event_type} registrado correctamente. Event ID: {event_id}",
            status_code=201,
            headers=headers
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error general en registrar_fichaje: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


@app.post("/admin_crear_empleado")
def admin_crear_empleado(data: dict = Body(...)):
    try:
        email = data.get("email")
        nombre = data.get("nombre_completo")
        dni = data.get("dni")
        companyId = data.get("company_id")
        employee_id = data.get("employee_id")
        fecha_alta = data.get("fecha_alta")
        new_horario_fin_lv = data.get("lunes_viernes_fin")
        new_horario_inicio_lv = data.get("lunes_viernes_inicio")
        
        password_temp = "Pymeasy123!"
        user_record = auth.create_user(
            email=email, password=password_temp, display_name=nombre
        )
        
        db = admin_firestore.client()
        emp_ref = (
            db.collection("companies")
            .document("COMP_987")
            .collection("employees")
            .document(user_record.uid)
        )
        emp_ref.set(
            {
                "nombre_completo": nombre,
                "email": email,
                "dni": dni,
                "activo": True,
                "first_login": True,
                "created_at": gcloud_firestore.SERVER_TIMESTAMP,
                "company_id": companyId,
                "employee_id": employee_id,
                "fecha_alta": fecha_alta,
                "horario_esperado": {
                    "lunes_viernes_fin": new_horario_fin_lv,
                    "lunes_viernes_inicio": new_horario_inicio_lv,
                },
            }
        )
        return {"status": "success", "message": f"Éxito: Usuario creado con UID {user_record.uid}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/obtener_resumen_mensual")
def obtener_resumen_mensual(data: dict = Body(...)):
    try:
        employee_id = data.get("employee_id")
        mes_buscado = data.get("mes")

        db = admin_firestore.client()
        events_ref = (
            db.collection("companies").document("COMP_987").collection("clocks_events")
        )
        query = (
            events_ref.where("employee_id", "==", employee_id)
            .where("work_date", ">=", f"{mes_buscado}-01")
            .where("work_date", "<=", f"{mes_buscado}-31")
        )

        docs = query.stream()

        jornadas = {}
        for doc in docs:
            doc_data = doc.to_dict()
            if doc_data and "events" in doc_data:
                event = doc_data["events"]
                fecha = event.get("work_date")
                if fecha:
                    if fecha not in jornadas:
                        jornadas[fecha] = []
                    jornadas[fecha].append(event)

        return jornadas
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agregar_empleados_prueba")
def agregar_empleados_prueba():
    db = admin_firestore.client()
    empleados_prueba = [
        {
            "employee_id": "EMP_001",
            "company_id": "COMP_987",
            "nombre_completo": "Pedro Ramón",
            "email": "pedro@ejemplo.com",
            "activo": True,
            "fecha_alta": datetime.now().isoformat(),
        },
        {
            "employee_id": "EMP_002",
            "company_id": "COMP_987",
            "nombre_completo": "Julio Álvarez",
            "email": "julio@ejemplo.com",
            "activo": True,
            "fecha_alta": datetime.now().isoformat(),
        },
    ]
    resultados = []
    for emp in empleados_prueba:
        try:
            doc_ref = (
                db.collection("companies")
                .document(emp["company_id"])
                .collection("employees")
                .document(emp["employee_id"])
            )
            doc_ref.set(emp)
            resultados.append(f"✅ {emp['employee_id']} creado")
        except Exception as e:
            resultados.append(f"❌ {emp['employee_id']} ERROR: {str(e)}")
    return {"resultados": resultados}