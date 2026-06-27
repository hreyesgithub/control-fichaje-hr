def escribir_en_google_sheets(datos: dict) -> str:
    try:
        import gspread
        import json
        from google.oauth2.service_account import Credentials
        from services.secrets import obtener_secreto

        creds_dict = json.loads(obtener_secreto("pymeasyhr-sheets-key"))
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet_id = obtener_secreto("pymeasyhr-sheet-id").strip()
        sh = client.open_by_key(sheet_id)
        worksheet = sh.get_worksheet(0)
        geo = datos.get("geo_data") or {}
        latitud = geo.get("lat", "N/A")
        longitud = geo.get("lon", "N/A")
        fila = [
            str(datos.get("timestamp", "")),  # A
            datos.get("employee_id", ""),  # B
            datos.get("employee_name", "ERROR_NOMBRE"),  # C
            datos.get("event_type", ""),  # D
            datos.get("source", ""),  # E
            latitud,  # F
            longitud,  # G
            "Sincronizado",  # H
            datos.get("activo", False),  # I
        ]
        worksheet.append_row(fila, value_input_option="USER_ENTERED")
        return "success"
    except Exception as e:
        return f"error: {str(e)}"
