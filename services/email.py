import logging
import requests
import logging
from services.secrets import obtener_secreto

logger = logging.getLogger(__name__)

def enviar_email_confirmacion(datos: dict) -> str:
    """Envía un correo de confirmación usando EmailJS."""
    if not datos.get("employee_email"):
        return "skipped (no email)"
    try:
        service_id = obtener_secreto("emailjs-service-id")
        template_id = obtener_secreto("emailjs-template-id")
        user_id = obtener_secreto("emailjs-user-id")
        private_key = obtener_secreto("emailjs-private-key") 
        template_params = {
            "employee_name": datos["employee_name"],
            "event_type": datos["event_type"],
            "timestamp": datos["timestamp"],
            "company_name": "Su Empresa S.L.", 
            "device_source": (
                "desde el ordenador" if datos["source"] == "web" else "desde el móvil"
            ),
        }
        response = requests.post(
            "https://api.emailjs.com/api/v1.0/email/send",
            json={
                "service_id": service_id,
                "template_id": template_id,
                "user_id": user_id,
                "accessToken": private_key,
                "template_params": template_params 
            },
            headers={"Content-Type": "application/json"},
            timeout=10,  
        )
        if response.status_code == 200:
            logger.info(f"Email de confirmación enviado a {datos['employee_email']}")
            return "success"
        else:
            logger.error(f"EmailJS API error: {response.status_code} - {response.text}")
            return f"api_error: {response.status_code}"
    except requests.exceptions.Timeout:
        logger.error("Timeout al conectar con EmailJS")
        return "timeout"
    except Exception as e:
        logger.error(f"Error inesperado en EmailJS: {e}")
        return f"error: {str(e)}"
