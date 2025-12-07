import streamlit as st
import pandas as pd
import re
import unicodedata
from pathlib import Path
from typing import Optional
import random
import datetime
import numpy as np
from io import BytesIO
from PIL import Image
from fpdf import FPDF

# ---------------------------------------------------------
# 1) CONFIG STREAMLIT
# ---------------------------------------------------------
st.set_page_config(
    page_title="Gestor de Puestos y Salas",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------
# 2) IMPORTS MÓDULOS
# ---------------------------------------------------------
from modules.database import (
    get_conn, init_db, insert_distribution, clear_distribution,
    read_distribution_df, save_setting, get_all_settings,
    add_reservation, user_has_reservation, list_reservations_df,
    add_room_reservation, get_room_reservations_df,
    count_monthly_free_spots, delete_reservation_from_db,
    delete_room_reservation_from_db, perform_granular_delete,
    ensure_reset_table, save_reset_token, validate_and_consume_token,
    get_worksheet
)

try:
    from modules.database import delete_distribution_row, delete_distribution_rows_by_indices
except ImportError:
    def delete_distribution_row(conn, piso, equipo, dia):
        return False
    def delete_distribution_rows_by_indices(conn, indices):
        return False

from modules.auth import get_admin_credentials
from modules.layout import admin_appearance_ui, apply_appearance_styles
from modules.seats import compute_distribution_from_excel, compute_distribution_variants
from modules.emailer import send_reservation_email
from modules.rooms import generate_time_slots, check_room_conflict
from modules.zones import generate_colored_plan, load_zones, save_zones

from streamlit_drawable_canvas import st_canvas
import streamlit.components.v1 as components

# ---------------------------------------------------------
# 3) CONSTANTES / DIRS
# ---------------------------------------------------------
ORDER_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

PLANOS_DIR = Path("modules/planos")
DATA_DIR = Path("data")
COLORED_DIR = Path("planos_coloreados")

for d in (PLANOS_DIR, DATA_DIR, COLORED_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# 4) SESSION STATE UI
# ---------------------------------------------------------
st.session_state.setdefault("ui", {
    "app_title": "Gestor de Puestos y Salas",
    "bg_color": "#ffffff",
    "logo_path": "assets/logo.png",
    "title_font_size": 64,
    "logo_width": 420,
})

# Inicio = Administrador
st.session_state.setdefault("screen", "Administrador")
st.session_state.setdefault("forgot_mode", False)

# login state
st.session_state.setdefault("admin_logged_in", False)

# ---------------------------------------------------------
# 4.5) DB + SETTINGS
# ---------------------------------------------------------
conn = get_conn()

if "db_initialized" not in st.session_state:
    with st.spinner("Conectando a Google Sheets..."):
        init_db(conn)
    st.session_state["db_initialized"] = True

apply_appearance_styles(conn)

settings = get_all_settings(conn) or {}
st.session_state["ui"]["app_title"] = settings.get("site_title", st.session_state["ui"]["app_title"])
st.session_state["ui"]["logo_path"] = settings.get("logo_path", st.session_state["ui"]["logo_path"])

# ---------------------------------------------------------
# 5) CSS
# ---------------------------------------------------------
st.markdown(f"""
<style>
.stApp {{
  background: {st.session_state.ui["bg_color"]};
}}
header {{
  visibility: hidden;
  height: 0px;
}}

div[data-testid="stAppViewContainer"] > .main {{
  padding-top: 0rem !important;
}}
section.main > div {{
  padding-top: 0rem !important;
}}

.block-container {{
  max-width: 100% !important;
  padding-top: 0.75rem !important;
  padding-left: 5cm !important;
  padding-right: 5cm !important;
}}

.mk-content {{
  width: 100%;
  max-width: 1200px;
  margin-left: auto;
  margin-right: auto;
}}

html, body, [class*="css"] {{
  font-size: 20px !important;
}}
h1 {{ font-size: 48px !important; }}
h2 {{ font-size: 40px !important; }}
h3 {{ font-size: 32px !important; }}
p, li, label, span {{ font-size: 20px !important; }}

div[data-baseweb="input"] input {{
  font-size: 20px !important;
  padding-top: 14px !important;
  padding-bottom: 14px !important;
}}

div[data-baseweb="select"] > div {{
  font-size: 20px !important;
  min-height: 56px !important;
  border-radius: 18px !important;
}}

.stButton button {{
  font-size: 20px !important;
  font-weight: 900 !important;
  padding: 12px 18px !important;
  border-radius: 16px !important;
}}

.mk-title {{
  text-align: center;
  font-weight: 900;
  margin: 0;
  line-height: 1.05;
}}

/* mismo ancho para ambos botones del login */
button[kind="primary"][data-testid="baseButton-primary"] {{
  width: 320px !important;
}}
button[data-testid="baseButton-secondary"] {{
  width: 320px !important;
}}

/* opcional: evita que en columnas se "encojan" los botones */
div[data-testid="column"] .stButton {{
  width: 100%;
}}

/* ✅ (si más adelante vuelves a usar logo clickeable) */
.mk-logo-btn button {{
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  box-shadow: none !important;
}}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def clean_pdf_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = (s.replace("\r", "")
           .replace("\t", " ")
           .replace("–", "-")
           .replace("—", "-")
           .replace("−", "-")
           .replace("“", '"')
           .replace("”", '"')
           .replace("’", "'")
           .replace("‘", "'")
           .replace("•", "-")
           .replace("\u00a0", " "))
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s

def go(screen: str):
    st.session_state["screen"] = screen

def _normalize_admin_creds(creds):
    """
    Soporta:
      - dict: {"email":..., "password":...} o {"admin_email":..., "admin_password":...}
      - tuple/list: (email, password)
      - str: "email:password" o "email,password"
      - None: -> (None, None)
    """
    if creds is None:
        return None, None

    if isinstance(creds, dict):
        email = (creds.get("email") or creds.get("admin_email") or "").strip().lower()
        pwd = (creds.get("password") or creds.get("pass") or creds.get("admin_password") or "").strip()
        return (email or None), (pwd or None)

    if isinstance(creds, (tuple, list)) and len(creds) >= 2:
        email = str(creds[0]).strip().lower()
        pwd = str(creds[1]).strip()
        return (email or None), (pwd or None)

    if isinstance(creds, str):
        s = creds.strip()
        if ":" in s:
            parts = s.split(":", 1)
        elif "," in s:
            parts = s.split(",", 1)
        else:
            # no separador: imposible inferir password
            return s.lower() or None, None
        email = parts[0].strip().lower()
        pwd = parts[1].strip()
        return (email or None), (pwd or None)

    # tipo inesperado
    return None, None

def _validate_admin_login(email: str, password: str) -> bool:
    creds = get_admin_credentials()
    e0, p0 = _normalize_admin_creds(creds)
    if not e0 or not p0:
        st.error("No se pudieron cargar credenciales de admin (revisa get_admin_credentials).")
        return False
    return email.strip().lower() == e0 and password == p0

# ---------------------------------------------------------
# TOPBAR
# ---------------------------------------------------------
def render_topbar_and_menu():
    logo_path = Path(st.session_state.ui["logo_path"])
    size = int(st.session_state.ui.get("title_font_size", 64))
    title = st.session_state.ui.get("app_title", "Gestor de Puestos y Salas")
    logo_w = int(st.session_state.ui.get("logo_width", 420))

    c1, c2, c3 = st.columns([1.2, 3.6, 1.2], vertical_alignment="center")

    with c1:
        if logo_path.exists():
            st.image(str(logo_path), width=logo_w)
        else:
            st.write("🧩 (Logo aquí)")

    with c2:
        st.markdown(f"<div class='mk-title' style='font-size:{size}px;'>{title}</div>", unsafe_allow_html=True)

    with c3:
        # ✅ "Inicio" fijo como una opción más (no resetea nada)
        menu_choice = st.selectbox(
            "Menú",
            ["—", "Inicio", "Reservas", "Ver Distribución y Planos"],
            index=0,
            key="top_menu_select",
        )
        if menu_choice == "Inicio":
            go("Administrador")
        elif menu_choice == "Reservas":
            go("Reservas")
        elif menu_choice == "Ver Distribución y Planos":
            go("Planos")

# ---------------------------------------------------------
# ADMIN (login + tabs)
# ---------------------------------------------------------
def admin_tabs_after_login(conn):
    st.subheader("Administrador")

    tabs = st.tabs(["Cargar Datos"])

    with tabs[0]:
        st.markdown("### Cargar Excel para generar distribución")
        st.info("Sube el Excel base y generamos la distribución usando seats.py")

        up = st.file_uploader("Sube tu Excel", type=["xlsx"], key="uploader_excel_dist")

        # parámetros mínimos para probar
        cA, cB, cC = st.columns([1, 1, 1], vertical_alignment="center")
        with cA:
            cupos_reserva = st.number_input("Cupos reserva por piso", min_value=0, value=2, step=1)
        with cB:
            ignore_params = st.checkbox("Ignorar parámetros", value=False)
        with cC:
            variant_mode = st.selectbox("Modo 'o' (día completo)", ["holgura", "equilibrar", "aleatorio"], index=0)

        if st.button("Generar distribución", type="primary", key="btn_gen_dist"):
            if up is None:
                st.warning("Primero sube un archivo Excel.")
                st.stop()

            try:
                xls = pd.ExcelFile(up)
                # Ajusta estos nombres si tus hojas se llaman distinto:
                # equipos_df: piso/equipo/personas/minimos
                # parametros_df: criterios/valor
                # capacidades_df: piso/capacidad
                sheet_names = [s.lower() for s in xls.sheet_names]

                def pick_sheet(candidates):
                    for cand in candidates:
                        for real in xls.sheet_names:
                            if cand in real.lower():
                                return real
                    return None

                sh_equipos = pick_sheet(["equipos", "equipo", "dotacion", "dotación"])
                sh_params = pick_sheet(["parametros", "parámetros", "criterios", "param"])
                sh_caps = pick_sheet(["capacidad", "capacidades", "cap"])

                if not sh_equipos:
                    st.error(f"No encontré hoja de equipos. Hojas disponibles: {xls.sheet_names}")
                    st.stop()

                equipos_df = pd.read_excel(xls, sheet_name=sh_equipos)
                parametros_df = pd.read_excel(xls, sheet_name=sh_params) if sh_params else pd.DataFrame()
                capacidades_df = pd.read_excel(xls, sheet_name=sh_caps) if sh_caps else pd.DataFrame()

                rows, deficit, audit, score = compute_distribution_from_excel(
                    equipos_df=equipos_df,
                    parametros_df=parametros_df,
                    df_capacidades=capacidades_df,
                    cupos_reserva=int(cupos_reserva),
                    ignore_params=bool(ignore_params),
                    variant_seed=42,
                    variant_mode=str(variant_mode),
                )

                if not rows:
                    st.error("No se generaron filas de distribución. Revisa columnas del Excel.")
                    st.stop()

                df_out = pd.DataFrame(rows)
                st.success(f"Distribución generada. Score: {score.get('score'):.2f}")
                st.dataframe(df_out, use_container_width=True, hide_index=True)

                # (Opcional) guardar en DB/Sheets si tu flujo lo usa:
                # clear_distribution(conn)
                # for r in rows: insert_distribution(conn, r["piso"], r["equipo"], r["dia"], r["cupos"])

            except Exception as ex:
                st.exception(ex)

def screen_admin(conn):
    # si ya logueó, mostrar pestañas admin
    if st.session_state.get("admin_logged_in"):
        admin_tabs_after_login(conn)
        return

    st.subheader("Administrador")
    st.session_state.setdefault("forgot_mode", False)

    if not st.session_state["forgot_mode"]:
        st.text_input("Ingresar correo", key="admin_login_email")
        st.text_input("Contraseña", type="password", key="admin_login_pass")

        c1, c2 = st.columns([1, 1], vertical_alignment="center")

        with c1:
            if st.button("Olvidaste tu contraseña", key="btn_admin_forgot"):
                st.session_state["forgot_mode"] = True
                st.rerun()

        with c2:
            # ✅ empuja el botón a la derecha dentro de su columna (sin romper el margen global)
            spacer, btn_col = st.columns([3, 1], vertical_alignment="center")
            with btn_col:
                if st.button("Acceder", type="primary", key="btn_admin_login"):
                    e = st.session_state.get("admin_login_email", "").strip()
                    p = st.session_state.get("admin_login_pass", "")
                    if not e or not p:
                        st.warning("Completa correo y contraseña.")
                    else:
                        ok = _validate_admin_login(e, p)
                        if ok:
                            st.session_state["admin_logged_in"] = True
                            st.success("Bienvenido/a 👋")
                            st.rerun()
                        else:
                            st.error("Credenciales incorrectas.")

    else:
        st.text_input("Correo de acceso", key="admin_reset_email")
        st.caption("Ingresa el código recibido en tu correo.")
        st.text_input("Código", key="admin_reset_code")

        c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="center")
        with c1:
            if st.button("Volver a Acceso", key="btn_admin_back"):
                st.session_state["forgot_mode"] = False
                st.rerun()
        with c2:
            if st.button("Enviar código", type="primary", key="btn_admin_send_code"):
                e = st.session_state.get("admin_reset_email", "").strip()
                if not e:
                    st.warning("Ingresa tu correo.")
                else:
                    st.success("Código enviado (simulado).")
        with c3:
            if st.button("Validar código", type="primary", key="btn_admin_validate"):
                c = st.session_state.get("admin_reset_code", "").strip()
                if not c:
                    st.warning("Ingresa el código.")
                else:
                    st.success("Código validado (simulado).")

# ---------------------------------------------------------
# RESERVAS (placeholder)
# ---------------------------------------------------------
def screen_reservas_tabs(conn):
    st.subheader("Reservas")
    tabs = st.tabs(["Reservar Puesto Flex", "Reserva Salas de Reuniones", "Mis Reservas y Listados"])
    with tabs[0]:
        st.info("Pega aquí tu pantalla completa de 'Reservar Puesto Flex'.")
    with tabs[1]:
        st.info("Pega aquí tu pantalla completa de 'Reserva Salas de Reuniones'.")
    with tabs[2]:
        st.info("Pega aquí tu pantalla completa de 'Mis Reservas y Listados'.")

# ---------------------------------------------------------
# DESCARGAS
# ---------------------------------------------------------
def _df_to_xlsx_bytes(df: pd.DataFrame, sheet_name="data") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        (df if df is not None else pd.DataFrame()).to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return output.getvalue()

def screen_descargas_distribucion_planos(conn):
    st.subheader("Ver Distribución y Planos (solo descarga)")
    t1, t2 = st.tabs(["Distribución", "Planos"])

    with t1:
        st.markdown("### Distribución (Descargar)")
        df = read_distribution_df(conn)
        if df is None or df.empty:
            st.warning("No hay distribución cargada para descargar.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
            xlsx_bytes = _df_to_xlsx_bytes(df, sheet_name="distribucion")
            st.download_button(
                "⬇️ Descargar Distribución (XLSX)",
                data=xlsx_bytes,
                file_name="distribucion.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with t2:
        st.markdown("### Planos (Descargar)")
        patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.PNG", "*.JPG", "*.JPEG", "*.WEBP"]
        imgs = []
        for pat in patterns:
            imgs.extend(sorted(PLANOS_DIR.glob(pat)))

        if not imgs:
            st.warning("No se encontraron imágenes de planos.")
            st.write(f"Ruta buscada: `{PLANOS_DIR.resolve()}`")
        else:
            selected = st.selectbox("Selecciona un plano", [p.name for p in imgs], key="dl_plano_sel")
            img_path = next(p for p in imgs if p.name == selected)
            st.image(str(img_path), use_container_width=True)
            st.download_button(
                "⬇️ Descargar plano (imagen)",
                data=img_path.read_bytes(),
                file_name=img_path.name,
                mime="image/png" if img_path.suffix.lower() == ".png" else "image/jpeg",
            )

# ---------------------------------------------------------
# APP
# ---------------------------------------------------------
st.markdown("<div class='mk-content'>", unsafe_allow_html=True)
render_topbar_and_menu()
st.divider()

screen = st.session_state.get("screen", "Administrador")

if screen == "Administrador":
    screen_admin(conn)
elif screen == "Reservas":
    screen_reservas_tabs(conn)
elif screen == "Planos":
    screen_descargas_distribucion_planos(conn)
else:
    st.session_state["screen"] = "Administrador"
    screen_admin(conn)

st.markdown("</div>", unsafe_allow_html=True)
