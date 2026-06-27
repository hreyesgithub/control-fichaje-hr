import os
import json
import logging
import hashlib
import math
from datetime import datetime, timezone, UTC
from typing import Any

# Importaciones de FastAPI
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware

# Importación de la librería base de Google Cloud
from google.cloud import firestore as gcloud_firestore

# Importaciones de Firebase Admin SDK
import firebase_admin
from firebase_admin import auth, firestore as admin_firestore

# Forzar la ruta temporal si no existe entorno gráfico
if "APPDATA" not in os.environ:
    os.environ["APPDATA"] = "/tmp"

# Inicializar la aplicación FastAPI
app = FastAPI(title="Backend PYMEasyHR", version="2.0.0")

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializar Firebase Admin SDK
if not firebase_admin._apps:
    service_account_env = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_env:
        cred = firebase_admin.credentials.Certificate(json.loads(service_account_env))
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

logger = logging.getLogger(__name__)

# ==================== FUNCIONES AUXILIARES ====================

def calcular_distancia(lat1, lon1, lat2, lon2):
    """Calcula la distancia en metros entre dos coordenadas (fórmula de Haversine)."""
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

def verificar_permiso_empresa(uid_usuario: str, company_id: str, db):
    """
    Verifica que el usuario pertenezca a la empresa indicada.
    Lanza HTTPException 403 si no coincide.
    """
    index_ref = db.collection("empleados_index").document(uid_usuario)
    index_snap = index_ref.get()
    if not index_snap.exists:
        raise HTTPException(
            status_code=403,
            detail="El usuario no está registrado en ninguna empresa."
        )
    if index_snap.get("company_id") != company_id:
        raise HTTPException(
            status_code=403,
            detail="No tienes permisos para acceder a esta empresa."
        )
    return True

def emular_trigger_fichaje(company_id: str, employee_id: str, fichaje_data: dict):
    """
    Tarea en segundo plano para actualizar Google Sheets y enviar email.
    """
    logger.info(">>> Fase 2: Tarea en segundo plano activada.")
    try:
        db = admin_firestore.client()
        timestamp_str = datetime.now(UTC).isoformat()

        empleado_ref = (
            db.collection("companies")
            .document(company_id)
            .collection("employees")
            .document(employee_id)
        )
        empleado_doc = empleado_ref.get()

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
            "event_type": "ENTRADA" if fichaje_data.get("event_type") == "IN" else "SALIDA",
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

# ==================== ENDPOINTS ====================

@app.get("/")
def hello_pyme():
    return {"status": "success", "message": "Backend PYMEasyHR operativo en Render"}

@app.post("/registrar_fichaje")
def registrar_fichaje(
    request: Request,
    background_tasks: BackgroundTasks,
    data: dict = Body(...)
    ):
    print("🔵 LOG: La función registrar_fichaje ha sido invocada")
    print(f"📦 Body recibido: {data}")

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "3600",
    }

    # 1. Validar token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        print("❌ Error: Falta token")
        raise HTTPException(status_code=401, detail="Falta Token")

    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        email_autenticado = decoded_token.get("email")
        uid_usuario = decoded_token.get("uid")
        print(f"✅ Token válido: email={email_autenticado}, uid={uid_usuario}")
    except Exception as e:
        print(f"❌ Error al verificar token: {e}")
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    if not uid_usuario or not email_autenticado:
        print("❌ Token incompleto: falta email o uid")
        raise HTTPException(status_code=401, detail="Token incompleto")

    # 2. Obtener company_id
    company_id = data.get("company_id")
    if not company_id:
        print("❌ Falta company_id en el body")
        raise HTTPException(status_code=400, detail="Falta company_id en la solicitud")
    print(f"🏢 company_id: {company_id}")

    db = admin_firestore.client()

    # 3. Verificar documento de la empresa en Firestore
    try:
        print(f"🔍 Buscando empresa {company_id} en Firestore...")
        company_doc = db.collection("companies").document(company_id).get()
        if not company_doc.exists: #type:ignore
            print(f"❌ Empresa {company_id} NO existe en Firestore")
            raise HTTPException(status_code=404, detail=f"Empresa {company_id} no encontrada")
        company_data = company_doc.to_dict() #type:ignore
        print(f"✅ Empresa encontrada. Datos: {company_data}")
    except Exception as e:
        print(f"🔥 Error al leer empresa: {e}")
        raise HTTPException(status_code=500, detail=f"Error al leer datos de empresa: {str(e)}")

    # 4. Verificar empleado en la empresa
    try:
        print(f"🔍 Buscando empleado con email {email_autenticado} en {company_id}...")
        empleados_ref = db.collection("companies").document(company_id).collection("employees")
        query = empleados_ref.where("email", "==", email_autenticado).limit(1).get()
        if len(query) == 0:
            print(f"❌ Empleado {email_autenticado} NO encontrado en {company_id}")
            raise HTTPException(status_code=404, detail="Empleado no encontrado en BD")
        empleado_doc = query[0]
        employee_id_verificado = empleado_doc.id
        empleado_data = empleado_doc.to_dict()
        print(f"✅ Empleado encontrado: ID={employee_id_verificado}, datos={empleado_data}")
    except Exception as e:
        print(f"🔥 Error al buscar empleado: {e}")
        raise HTTPException(status_code=500, detail=f"Error al buscar empleado: {str(e)}")

    # 5. Verificar documento en empleados_index (para can_telework y permisos)
    try:
        print(f"🔍 Buscando índice del usuario {uid_usuario} en empleados_index...")
        index_ref = db.collection("empleados_index").document(uid_usuario)
        index_snap = index_ref.get()
        if not index_snap.exists: #type:ignore
            print(f"⚠️ No existe documento en empleados_index para {uid_usuario}")
            # No lanzamos error, pero registramos
        else:
            index_data = index_snap.to_dict() #type:ignore
            print(f"✅ empleados_index encontrado: {index_data}")
            can_telework = index_snap.get("can_telework") #type:ignore
            print(f"   can_telework = {can_telework}")

            # Validar que company_id coincida
            if index_snap.get("company_id") != company_id: #type:ignore
                print(f"❌ company_id en índice ({index_snap.get('company_id')}) no coincide con {company_id}") #type:ignore
                raise HTTPException(status_code=403, detail="El usuario no pertenece a esta empresa")
    except Exception as e:
        print(f"🔥 Error al leer empleados_index: {e}")
        raise HTTPException(status_code=500, detail=f"Error al verificar permisos: {str(e)}")

    # 6. Validar geo_data
    client_geo = data.get("geo_data")
    if not client_geo:
        print("❌ Falta geo_data en el body")
        raise HTTPException(status_code=400, detail="Falta ubicación GPS")
    if "lat" not in client_geo or "lon" not in client_geo:
        print(f"❌ geo_data incompleto: {client_geo}")
        raise HTTPException(status_code=400, detail="Ubicación GPS incompleta (falta lat o lon)")
    print(f"📍 Ubicación del cliente: lat={client_geo['lat']}, lon={client_geo['lon']}")

    # 7. Determinar si es teletrabajo
    is_telework = data.get("is_telework", False)
    print(f"🏠 is_telework enviado: {is_telework}")

    # 8. Validar oficina solo si NO es teletrabajo
    if not is_telework:
        print("📍 Validando oficina para fichaje presencial...")
        office_geo = company_data.get("office_location") #type:ignore
        if not office_geo:
            print("❌ La empresa no tiene office_location configurado")
            raise HTTPException(status_code=400, detail="Ubicación de oficina no configurada para esta empresa")
        if "lat" not in office_geo or "lon" not in office_geo:
            print(f"❌ office_location incompleto: {office_geo}")
            raise HTTPException(status_code=400, detail="Ubicación de oficina incompleta (falta lat o lon)")
        print(f"📍 Oficina: lat={office_geo['lat']}, lon={office_geo['lon']}")

        try:
            distancia = calcular_distancia(
                client_geo["lat"],
                client_geo["lon"],
                office_geo["lat"],
                office_geo["lon"]
            )
            print(f"📏 Distancia calculada: {distancia:.2f} metros")
        except Exception as e:
            print(f"🔥 Error al calcular distancia: {e}")
            raise HTTPException(status_code=500, detail=f"Error al calcular distancia: {str(e)}")

        RADIO_MAXIMO = 200
        if distancia > RADIO_MAXIMO:
            print(f"⛔ Distancia excede {RADIO_MAXIMO}m: {distancia}")
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
        print("✅ Distancia dentro del rango permitido")
    else:
        print("🏠 Teletrabajo activo, omitiendo validación de oficina")

    # 9. Validar tipo de evento
    event_type = data.get("event_type")
    source = data.get("source", "web_secure")
    if event_type not in ["IN", "OUT", "BREAK_START", "BREAK_END"]:
        print(f"❌ Tipo de evento no válido: {event_type}")
        raise HTTPException(status_code=400, detail=f"Tipo de evento no válido: {event_type}")
    print(f"📝 Evento: {event_type}, fuente: {source}")

    # 10. Construir documento del fichaje
    timestamp_ahora = datetime.now(timezone.utc)
    nuevo_fichaje = {
        "employee_id": employee_id_verificado,
        "company_id": company_id,
        "event_type": event_type,
        "source": source,
        "work_date": timestamp_ahora.strftime("%Y-%m-%d"),
        "timestamp": gcloud_firestore.SERVER_TIMESTAMP,
        "timezone": "Europe/Madrid",
        "ip_address": request.headers.get("X-Forwarded-For", "Desconocida"),
        "user_agent": request.headers.get("User-Agent", "Desconocido"),
        "geo_data": data.get("geo_data"),
    }

    # Hash opcional
    try:
        string_para_hash = f"{employee_id_verificado}{timestamp_ahora.isoformat()}{event_type}{company_id}"
        nuevo_fichaje["data_hash"] = hashlib.sha256(string_para_hash.encode()).hexdigest()
        print(f"🔐 Hash generado: {nuevo_fichaje['data_hash']}")
    except Exception as e:
        print(f"⚠️ No se pudo calcular el hash: {e}")

    # 11. Guardar en Firestore
    try:
        print("💾 Guardando en Firestore...")
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
                "updated_at": gcloud_firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        events_ref = user_doc_ref.collection("events")
        _, event_ref = events_ref.add(nuevo_fichaje)
        event_id = event_ref.id
        print(f"✅ Fichaje guardado con ID: {event_id}")
    except Exception as e:
        print(f"🔥 Error al guardar en Firestore: {e}")
        raise HTTPException(status_code=500, detail=f"Error al guardar fichaje: {str(e)}")

    # Actualizar estadísticas de la empresa
    try:
        company_ref = db.collection("companies").document(company_id)
        company_ref.update({
            "stats_mes.total_eventos": gcloud_firestore.Increment(1),
            "stats_mes.total_pagar": gcloud_firestore.Increment(0.066666667)
        })
        print("📊 Estadísticas actualizadas para", company_id)
    except Exception as e:
        print(f"⚠️ Error al actualizar estadísticas: {e}")
        # No lanzamos excepción para no interrumpir el fichaje

    # 12. Tarea en segundo plano (Sheets + Email)
    print("⏳ Encolando tarea en segundo plano...")
    background_tasks.add_task(emular_trigger_fichaje, company_id, employee_id_verificado, nuevo_fichaje)

    # 13. Respuesta exitosa
    return {
        "status": "success",
        "message": f"Fichaje {event_type} registrado correctamente.",
        "event_id": event_id
    }

def registrar_fichaje_v0(
    request: Request,
    background_tasks: BackgroundTasks,
    data: dict = Body(...)
    ):
    print("LOG: La función registrar_fichaje ha sido invocada")
    print(f"📦 Body recibido: {data}")  # Muestra todo el payload

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "3600",
    }

    from google.cloud.firestore_v1 import Client, DocumentSnapshot
    db: Client = admin_firestore.client()

    # 1. Validar token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No autorizado: Falta Token")

    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        email_autenticado = decoded_token.get("email")
        uid_usuario = decoded_token.get("uid")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    if not uid_usuario or not email_autenticado:
        raise HTTPException(status_code=401, detail="Token incompleto")

    # 2. Obtener company_id del body
    company_id = data.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="Falta company_id en la solicitud")

    db = admin_firestore.client()

    # 3. Verificar que el usuario pertenece a la empresa (seguridad)
    try:
        verificar_permiso_empresa(uid_usuario, company_id, db)
    except HTTPException as e:
        raise e

    # 4. Buscar al empleado por email dentro de la empresa
    empleados_ref = (
        db.collection("companies")
        .document(company_id)
        .collection("employees")
    )
    query = empleados_ref.where("email", "==", email_autenticado).limit(1).get()

    if len(query) == 0:
        print(f"DEBUG: No se encontró el email {email_autenticado} en {company_id}")
        raise HTTPException(status_code=404, detail="Empleado no encontrado en BD")

    empleado_doc_snapshot = query[0]
    employee_id_verificado = empleado_doc_snapshot.id
    index_ref = db.collection("empleados_index").document(uid_usuario)
    index_snap = index_ref.get()
    if not index_snap.exists: # type: ignore
        raise HTTPException(403, "Usuario no registrado en empresa")
    if index_snap.get("company_id") != company_id: # type: ignore
        raise HTTPException(403, "Empresa no coincide")
    
    # Leer can_telework (sin valor por defecto)
    can_telework = index_snap.get("can_telework") # type: ignore
    is_telework = data.get("is_telework", False)
    # Priorizar el valor de la BD si está presente
    if can_telework is not None:
        is_telework = can_telework or is_telework

    # 5. Validar geolocalización
    client_geo = data.get("geo_data")
    if not client_geo or "lat" not in client_geo or "lon" not in client_geo:
        raise HTTPException(status_code=400, detail="Se requiere ubicación GPS (lat/lon)")

    # 6. Calcular distancia y validar rango (si no es teletrabajo)
    is_telework = data.get("is_telework", False)
    if not is_telework:
       # Solo validamos oficina si NO es teletrabajo
        company_doc: Any = db.collection("companies").document(company_id).get()
        if not company_doc.exists:
            raise HTTPException(404, "Configuración de empresa no encontrada")
        company_data = company_doc.to_dict()
        if not company_data:
            raise HTTPException(404, "Datos de la empresa vacíos")
        
        office_geo = company_data.get("office_location")
        if not office_geo or "lat" not in office_geo or "lon" not in office_geo:
            raise HTTPException(400, "Ubicación de oficina inválida o no configurada")

        distancia = calcular_distancia(
            client_geo["lat"],
            client_geo["lon"],
            office_geo["lat"],
            office_geo["lon"]
        )
        RADIO_MAXIMO = 200  # metros
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

    # 7. Obtener configuración de la empresa (ubicación de oficina)
    company_doc = db.collection("companies").document(company_id).get()
    if not company_doc.exists: # type: ignore
        raise HTTPException(status_code=404, detail="Configuración de empresa no encontrada")

    company_data = company_doc.to_dict() # type: ignore
    if not company_data:
        raise HTTPException(status_code=404, detail="Datos de la empresa vacíos")

    office_geo = company_data.get("office_location")
    if not office_geo or "lat" not in office_geo or "lon" not in office_geo:
        raise HTTPException(status_code=400, detail="Ubicación de oficina inválida")

    # 8. Validar tipo de evento
    event_type = data.get("event_type")
    source = data.get("source", "web_secure")
    if event_type not in ["IN", "OUT", "BREAK_START", "BREAK_END"]:
        raise HTTPException(status_code=400, detail="Tipo de evento no válido")

    # 9. Construir documento del fichaje
    timestamp_ahora = datetime.now(timezone.utc)
    nuevo_fichaje = {
        "employee_id": employee_id_verificado,
        "company_id": company_id,
        "event_type": event_type,
        "source": source,
        "work_date": timestamp_ahora.strftime("%Y-%m-%d"),
        "timestamp": gcloud_firestore.SERVER_TIMESTAMP,
        "timezone": "Europe/Madrid",
        "ip_address": request.headers.get("X-Forwarded-For", "Desconocida"),
        "user_agent": request.headers.get("User-Agent", "Desconocido"),
        "geo_data": data.get("geo_data"),
    }

    # Hash opcional
    try:
        string_para_hash = f"{employee_id_verificado}{timestamp_ahora.isoformat()}{event_type}{company_id}"
        nuevo_fichaje["data_hash"] = hashlib.sha256(string_para_hash.encode()).hexdigest()
    except Exception as e:
        logger.warning(f"No se pudo calcular el hash: {e}")

    # 10. Guardar en Firestore (subcolección por usuario)
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
            "updated_at": gcloud_firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    events_ref = user_doc_ref.collection("events")
    _, event_ref = events_ref.add(nuevo_fichaje)
    event_id = event_ref.id

    logger.info(f"Fichaje registrado para {employee_id_verificado} (Email: {email_autenticado})")

    # 11. Tarea en segundo plano (Sheets + Email)
    background_tasks.add_task(emular_trigger_fichaje, company_id, employee_id_verificado, nuevo_fichaje)

    # 12. Respuesta JSON
    return {
        "status": "success",
        "message": f"Fichaje {event_type} registrado correctamente.",
        "event_id": event_id
    }
#@app.post("/registrar_fichaje")

@app.post("/admin_crear_empleado")
def admin_crear_empleado(data: dict = Body(...)):
    try:
        company_id = data.get("company_id")
        if not company_id:
            raise HTTPException(status_code=400, detail="Falta company_id")

        email = data.get("email")
        nombre = data.get("nombre_completo")
        dni = data.get("dni")
        employee_id = data.get("employee_id")
        fecha_alta = data.get("fecha_alta")
        horario_inicio = data.get("lunes_viernes_inicio")
        horario_fin = data.get("lunes_viernes_fin")

        # Crear usuario en Firebase Auth
        password_temp = "Pymeasy123!"
        user_record = auth.create_user(
            email=email,
            password=password_temp,
            display_name=nombre
        )

        db = admin_firestore.client()
        emp_ref = (
            db.collection("companies")
            .document(company_id)
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
                "company_id": company_id,
                "employee_id": employee_id,
                "fecha_alta": fecha_alta,
                "horario_esperado": {
                    "lunes_viernes_fin": horario_fin,
                    "lunes_viernes_inicio": horario_inicio,
                },
            }
        )

        # Opcional: crear entrada en empleados_index
        index_ref = db.collection("empleados_index").document(user_record.uid)
        index_ref.set({
            "company_id": company_id,
            "employee_id": employee_id,
            "first_login": True
        })

        return {
            "status": "success",
            "message": f"Usuario creado con UID {user_record.uid}",
            "uid": user_record.uid
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/obtener_resumen_mensual")
def obtener_resumen_mensual(data: dict = Body(...)):
    try:
        company_id = data.get("company_id")
        if not company_id:
            raise HTTPException(status_code=400, detail="Falta company_id")

        employee_id = data.get("employee_id")
        mes_buscado = data.get("mes")
        if not employee_id or not mes_buscado:
            raise HTTPException(status_code=400, detail="Faltan employee_id o mes")

        db = admin_firestore.client()
        # Nota: la estructura de datos podría ser diferente; ajusta según tu modelo.
        # Aquí se asume que los eventos están en clock_events/{uid}/events
        # Pero el código original usaba una colección "clocks_events" a nivel de empresa.
        # Para mantener compatibilidad, usamos la misma lógica que antes, pero con company_id dinámico.
        events_ref = (
            db.collection("companies")
            .document(company_id)
            .collection("clock_events")
            .document(employee_id)  # Nota: en el original se usaba employee_id como documento?
            .collection("events")
        )

        # Filtrar por work_date (si el campo existe)
        query = (
            events_ref
            .where("work_date", ">=", f"{mes_buscado}-01")
            .where("work_date", "<=", f"{mes_buscado}-31")
        )

        docs = query.stream()
        jornadas = {}
        for doc in docs:
            doc_data = doc.to_dict()
            fecha = doc_data.get("work_date")
            if fecha:
                if fecha not in jornadas:
                    jornadas[fecha] = []
                jornadas[fecha].append(doc_data)

        return jornadas
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agregar_empleados_prueba")
def agregar_empleados_prueba(data: dict = Body(...)):
    try:
        company_id = data.get("company_id")
        if not company_id:
            raise HTTPException(status_code=400, detail="Falta company_id")

        db = admin_firestore.client()
        empleados_prueba = [
            {
                "employee_id": "EMP_001",
                "company_id": company_id,
                "nombre_completo": "Pedro Ramón",
                "email": "pedro@ejemplo.com",
                "activo": True,
                "fecha_alta": datetime.now().isoformat(),
            },
            {
                "employee_id": "EMP_002",
                "company_id": company_id,
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))