import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="DIAGNÓSTICO DE CONEXIÓN", layout="centered")

st.title("🕵️‍♂️ Diagnóstico de Conexión")
st.write("Vamos a probar paso a paso dónde se rompe la conexión.")

# PASO 1: VERIFICAR SI EXISTEN LOS SECRETOS
st.divider()
st.subheader("1. Verificando Secretos")

if "gcp_service_account" not in st.secrets:
    st.error("❌ ERROR: No se encuentran los secretos [gcp_service_account].")
    st.info("Revisa que en 'Manage app' -> 'Settings' -> 'Secrets' tengas el encabezado [gcp_service_account] correctamente escrito.")
    st.stop()
else:
    st.success("✅ Secretos encontrados en el sistema.")

# PASO 2: VERIFICAR FORMATO DE LA LLAVE
st.divider()
st.subheader("2. Verificando Llave Privada")

try:
    creds_dict = dict(st.secrets["gcp_service_account"])
    private_key = creds_dict.get("private_key", "")
    
    if "-----BEGIN PRIVATE KEY-----" not in private_key:
        st.error("❌ ERROR: La llave privada no tiene el formato correcto.")
        st.warning("Debe empezar con '-----BEGIN PRIVATE KEY-----'.")
        st.write(f"Tu llave empieza con: {private_key[:20]}...")
        st.stop()
    else:
        st.success("✅ Formato de llave correcto (empieza bien).")
except Exception as e:
    st.error(f"❌ Error leyendo el diccionario de secretos: {e}")
    st.stop()

# PASO 3: INTENTAR AUTENTICAR CON GOOGLE
st.divider()
st.subheader("3. Intentando Autenticar con Google")

try:
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    st.success("✅ Autenticación exitosa (El robot logró entrar a los servidores de Google).")
except Exception as e:
    st.error(f"❌ ERROR DE AUTENTICACIÓN: {str(e)}")
    st.info("Si el error dice 'Invalid Grant' o 'KeyError', tu llave privada está mal copiada en los secretos.")
    st.stop()

# PASO 4: INTENTAR ABRIR LA HOJA
st.divider()
st.subheader("4. Intentando Abrir la Hoja de Cálculo")

# Intentamos obtener el nombre desde secrets o hardcodeado
sheet_name = st.secrets["sheets"]["sheet_name"]
st.write(f"Buscando hoja llamada exactamente: **'{sheet_name}'**")

try:
    sh = client.open(sheet_name)
    st.success(f"🎉 ¡ÉXITO TOTAL! Se abrió la hoja '{sheet_name}'.")
    st.balloons()
    st.write("---")
    st.write("### ✅ CONCLUSIÓN: La conexión funciona perfectamente.")
    st.write("Si ves esto, el error estaba en tu código python, no en las credenciales. Ya puedes volver a poner tu código original.")
except Exception as e:
    error_msg = str(e)
    st.error(f"❌ ERROR AL ABRIR HOJA: {error_msg}")
    
    if "SpreadsheetNotFound" in error_msg:
        st.warning("👉 El robot entró a Google, pero NO encontró el archivo.")
        st.markdown(f"""
        **Soluciones probables:**
        1. El nombre en el Excel no es EXACTAMENTE igual a **'{sheet_name}'**. Revisa mayúsculas y espacios extra.
        2. No has compartido el archivo con el email del robot: `{creds_dict.get('client_email')}`.
        """)
    elif "403" in error_msg or "PERMISSION_DENIED" in error_msg:
        st.warning("👉 El robot encontró el archivo pero NO tiene permiso para leerlo.")
        st.markdown("**Solución:** Asegúrate de darle permiso de **Editor** al compartir.")
    elif "API has not been used" in error_msg or "project" in error_msg or "accessNotConfigured" in error_msg:
        st.warning("👉 ¡AJÁ! LO ENCONTRAMOS. Las APIs no están activadas.")
        st.markdown(f"**Solución:** Tienes que ir a la consola de Google Cloud del proyecto `{creds_dict.get('project_id')}` y habilitar **Google Sheets API** y **Google Drive API**.")
