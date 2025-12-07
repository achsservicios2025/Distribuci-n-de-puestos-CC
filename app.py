import streamlit as st
import pandas as pd
import re
import unicodedata
from pathlib import Path
from typing import Optionalimport random
import datetime
import numpy as np
from io import BytesIO
from PIL import Image
from fpdf import FPDF

# ---------------------------------------------------------
# 1) CONFIGURACIÓN STREAMLIT (una sola vez y arriba)
# ---------------------------------------------------------
st.set_page_config(
    page_title="Gestor de puestos y reservas",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------
# 2) IMPORTS DE MÓDULOS (se mantienen, pero pueden no usarse aún)
# ---------------------------------------------------------
# Si algunos módulos no existen en tu proyecto, coméntalos temporalmente.
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

# Si no lo estás usando todavía, puedes comentar st_canvas y components.
from streamlit_drawable_canvas import st_canvas
import streamlit.components.v1 as components

# ---------------------------------------------------------
# 3) CONSTANTES / DIRECTORIOS
# ---------------------------------------------------------
ORDER_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

PLANOS_DIR = Path("modules/planos")      # Ajusta si tus planos están en otro lugar
DATA_DIR = Path("data")
COLORED_DIR = Path("planos_coloreados")

for d in (PLANOS_DIR, DATA_DIR, COLORED_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# 4) ESTADO UI (editable a futuro desde Admin)
# ---------------------------------------------------------
st.session_state.setdefault("ui", {
    "app_title": "Gestor de puestos y reservas",
    "bg_color": "#ffffff",
    "logo_path": "assets/logo.png",
})

st.session_state.setdefault("nav_collapsed", False)
st.session_state.setdefault("screen", "Inicio")
st.session_state.setdefault("forgot_mode", False)

RESERVAS_OPTS = [
    "—",
    "Reservar Puesto Flex",
    "Reservar Sala de Reuniones",
    "Mis Reservas y Listados",
    "Planos (ver)",
    "Administrador",
]

# ---------------------------------------------------------
# 4.5) DB + SETTINGS (DEBE IR ANTES DE DIBUJAR UI)
# ---------------------------------------------------------
conn = get_conn()

if "db_initialized" not in st.session_state:
    with st.spinner("Conectando a Google Sheets..."):
        init_db(conn)
    st.session_state["db_initialized"] = True

# estilos desde DB (si tu módulo lo usa)
apply_appearance_styles(conn)

settings = get_all_settings(conn) or {}
# sincroniza UI con settings
st.session_state["ui"]["app_title"] = settings.get("site_title", st.session_state["ui"]["app_title"])
st.session_state["ui"]["logo_path"] = settings.get("logo_path", st.session_state["ui"]["logo_path"])

# por compatibilidad con el resto del código
site_title = st.session_state["ui"]["app_title"]
global_logo_path = st.session_state["ui"]["logo_path"]

# ---------------------------------------------------------
# 5) CSS (menú izquierdo compacto, estilo imagen 1)
# ---------------------------------------------------------
st.markdown(f"""
<style>
.stApp {{ background: {st.session_state.ui["bg_color"]}; }}
header {{ visibility: hidden; }}

section.main > div {{
  max-width: 100% !important;
  padding-left: 1.5rem !important;
  padding-right: 1.5rem !important;
}}
.block-container {{ max-width: 100% !important; }}

.mk-shell {{
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 24px;
  align-items: start;
}}
.mk-shell.mk-collapsed {{ grid-template-columns: 76px 1fr; }}

.mk-menu-card {{
  background: #f3f5f7;
  border-radius: 18px;
  padding: 14px;
  box-shadow: 0 10px 24px rgba(0,0,0,0.12);
  position: sticky;
  top: 12px;
  min-height: calc(100vh - 24px);
}}

.mk-collapse-row {{ display:flex; justify-content:flex-end; }}
.mk-collapse-btn button {{
  border-radius: 14px !important;
  padding: 6px 10px !important;
}}

.mk-menu-title {{
  font-size: 14px;
  font-weight: 800;
  margin: 6px 0 10px 2px;
  color: rgba(0,0,0,0.65);
}}

div[data-baseweb="select"] > div {{
  border-radius: 18px !important;
  border: 2px solid rgba(255, 60, 60, 0.85) !important;
  background: #fff !important;
  min-height: 44px !important;
}}

ul[role="listbox"] {{
  border-radius: 14px !important;
  box-shadow: 0 16px 36px rgba(0,0,0,0.18) !important;
  overflow: hidden !important;
}}

.mk-collapsed .mk-menu-title {{ display:none; }}
.mk-collapsed .stSelectbox label {{ display:none !important; }}

.mk-title {{
  text-align:center;
  font-size: 22px;
  font-weight: 800;
  margin: 0;
}}
</style>
""", unsafe_allow_html=True)

import uuid

def _fmt_item(item: dict) -> str:
    if item["kind"] == "puesto":
        return (f"📌 Puesto | {item['reservation_date']} | Piso: {item.get('piso','')} | "
                f"Área: {item.get('team_area','')} | Nombre: {item.get('user_name','')}")
    return (f"🏢 Sala | {item['reservation_date']} | {item.get('room_name','')} | "
            f"{item.get('start_time','')}–{item.get('end_time','')} | Equipo: {item.get('user_name','')}")

def open_confirm_delete_multi(selected_items: list[dict]):
    st.session_state["confirm_delete_multi"] = {"items": selected_items}
    st.rerun()

def render_confirm_delete_multi_dialog(conn):
    payload = st.session_state.get("confirm_delete_multi")
    if not payload:
        return

    items = payload.get("items") or []
    if not items:
        st.session_state.pop("confirm_delete_multi", None)
        return

    if not hasattr(st, "dialog"):
        st.error("Tu Streamlit no soporta popups centrados (st.dialog). Actualiza Streamlit.")
        return

    @st.dialog("Confirmar anulación")
    def _dlg():
        st.markdown("### ¿Anular las reservas seleccionadas?")
        st.caption("Se eliminarán únicamente las reservas marcadas. Las no marcadas se mantienen.")

        with st.expander("Ver detalle", expanded=True):
            for it in items:
                st.write("• " + _fmt_item(it))

        c1, c2 = st.columns(2)

        if c1.button("🔴 Sí, anular", type="primary", use_container_width=True, key="confirm_multi_yes"):
            deleted = 0
            for it in items:
                if it["kind"] == "puesto":
                    ok = delete_reservation_from_db(conn, it["user_email"], it["reservation_date"], it["team_area"])
                else:
                    inicio = str(it.get("start_time", "")).strip()
                    inicio = inicio[:5] if len(inicio) >= 5 else inicio
                    ok = delete_room_reservation_from_db(conn, it["user_email"], it["reservation_date"], it["room_name"], inicio)
                if ok:
                    deleted += 1

            st.session_state.pop("confirm_delete_multi", None)
            st.cache_data.clear()
            st.success(f"✅ Se anularon {deleted} reserva(s).")
            st.rerun()

        if c2.button("Cancelar", use_container_width=True, key="confirm_multi_no"):
            st.session_state.pop("confirm_delete_multi", None)
            st.rerun()

    _dlg()

# ---------------------------------------------------------
# 6) HELPERS & LÓGICA (tu parte, corregida)
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

def sort_floors(floor_list):
    """Ordena lista de pisos lógicamente (1, 2, 10)."""
    def extract_num(text):
        text = str(text)
        num = re.findall(r"\d+", text)
        return int(num[0]) if num else 0
    return sorted(list(floor_list), key=extract_num)

def apply_sorting_to_df(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica orden lógico a un DataFrame para Pisos y Días."""
    if df is None or df.empty:
        return df

    df = df.copy()
    cols_lower = {c.lower(): c for c in df.columns}
    col_dia = cols_lower.get("dia") or cols_lower.get("día")
    col_piso = cols_lower.get("piso")

    if col_dia:
        df[col_dia] = pd.Categorical(df[col_dia], categories=ORDER_DIAS, ordered=True)

    if col_piso:
        unique_floors = [str(x) for x in df[col_piso].dropna().unique()]
        sorted_floors = sort_floors(unique_floors)
        df[col_piso] = pd.Categorical(df[col_piso], categories=sorted_floors, ordered=True)

    sort_cols = [c for c in (col_piso, col_dia) if c]
    if sort_cols:
        df = df.sort_values(sort_cols)

    return df

# ---------------------------------------------------------
# 7) UI HELPERS
# ---------------------------------------------------------
def go(screen_name: str):
    st.session_state.screen = screen_name

def render_topbar():
    c1, c2, c3 = st.columns([1, 2, 1], vertical_alignment="center")
    with c1:
        logo_path = Path(st.session_state.ui["logo_path"])
        if logo_path.exists():
            st.image(str(logo_path), width=90)
        else:
            st.write("🧩 (Logo aquí)")
    with c2:
        st.markdown(f"<div class='mk-title'>{st.session_state.ui['app_title']}</div>", unsafe_allow_html=True)
    with c3:
        st.write("")

# ---------------------------------------------------------
# 8) PANTALLAS (base)
# ---------------------------------------------------------
def screen_inicio():
    st.info("Selecciona una opción desde el menú de la izquierda.")
    st.write("Más adelante aquí: distribución de cupos, reservas hechas, ver/descargar planos, etc.")

def screen_reservas(option: str, conn):
    if option == "Reservar Puesto Flex":
        screen_reservar_puesto_flex(conn)
    elif option == "Reservar Sala de Reuniones":
        screen_reservar_sala(conn)
    elif option == "Mis Reservas y Listados":
        screen_mis_reservas(conn)
    else:
        st.subheader(option)
        st.info("Sección en construcción.")

def screen_planos():
    st.subheader("Planos (ver)")

    # Busca imágenes comunes (sin base64, sin html)
    patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.PNG", "*.JPG", "*.JPEG", "*.WEBP"]
    imgs = []
    for pat in patterns:
        imgs.extend(sorted(PLANOS_DIR.glob(pat)))

    if not imgs:
        st.warning("No se encontraron imágenes de planos.")
        st.write(f"Ruta buscada: `{PLANOS_DIR.resolve()}`")
        st.write("Pon tus planos como .png/.jpg dentro de esa carpeta.")
        return

    selected = st.selectbox("Selecciona un plano", [p.name for p in imgs])
    img_path = next(p for p in imgs if p.name == selected)

    st.caption(f"Mostrando: `{img_path}`")
    st.image(str(img_path), use_container_width=True)

    st.download_button(
        "Descargar plano",
        data=img_path.read_bytes(),
        file_name=img_path.name,
        mime="image/png" if img_path.suffix.lower() == ".png" else "image/jpeg",
    )

def screen_admin():
    st.subheader("Administrador")

    if not st.session_state.forgot_mode:
        email = st.text_input("Ingresar correo", key="admin_email")
        password = st.text_input("Contraseña", type="password", key="admin_pass")

        c1, c2, _ = st.columns([1, 1, 2])
        with c1:
            if st.button("Olvidaste tu contraseña"):
                st.session_state.forgot_mode = True
                st.rerun()
        with c2:
            if st.button("Acceder"):
                if not email or not password:
                    st.warning("Completa correo y contraseña.")
                else:
                    st.success("Acceso recibido (validación real pendiente).")

    else:
        reset_email = st.text_input("Correo de acceso", key="reset_email")
        if st.button("Enviar código"):
            if not reset_email:
                st.warning("Ingresa tu correo.")
            else:
                st.success("Código enviado (simulado).")

        st.caption("Ingresa el código recibido en tu correo.")
        code = st.text_input("Código", key="reset_code")

        c1, c2, _ = st.columns([1, 1, 2])
        with c1:
            if st.button("Validar código"):
                if not code:
                    st.warning("Ingresa el código.")
                else:
                    st.success("Código validado (simulado).")
        with c2:
            if st.button("Volver a Acceso"):
                st.session_state.forgot_mode = False
                st.rerun()
def screen_reservar_puesto_flex(conn):
    # Pega aquí TU CÓDIGO de: elif screen == "Reservar Puesto Flex":
    pass

def screen_reservar_sala(conn):
    # Pega aquí TU CÓDIGO de: elif screen == "Reservar Sala de Reuniones":
    pass

def screen_mis_reservas(conn):
    # Pega aquí TU CÓDIGO de: elif screen == "Mis Reservas y Listados":
    pass
    
# ---------------------------------------------------------
# 9) APP (layout + menú)
# ---------------------------------------------------------
shell_class = "mk-shell mk-collapsed" if st.session_state.nav_collapsed else "mk-shell"
st.markdown(f'<div class="{shell_class}">', unsafe_allow_html=True)

# Menú izquierdo
st.markdown('<div class="mk-menu-card">', unsafe_allow_html=True)

st.markdown('<div class="mk-collapse-row mk-collapse-btn">', unsafe_allow_html=True)
if st.button("<<" if not st.session_state.nav_collapsed else ">>", key="collapse"):
    st.session_state.nav_collapsed = not st.session_state.nav_collapsed
    st.rerun()
st.markdown("</div>", unsafe_allow_html=True)

if not st.session_state.nav_collapsed:
    st.markdown('<div class="mk-menu-title">Menú</div>', unsafe_allow_html=True)

choice = st.selectbox(
    "Reservas",
    RESERVAS_OPTS,
    index=0,
    key="left_nav",
    label_visibility="collapsed" if st.session_state.nav_collapsed else "visible",
)
if choice and choice != "—":
    go(choice)

st.markdown("</div>", unsafe_allow_html=True)

# Contenido principal
st.markdown("<div>", unsafe_allow_html=True)
render_topbar()
st.divider()

screen = st.session_state.screen

if screen == "Inicio":
    screen_inicio()
elif screen == "Planos (ver)":
    screen_planos()
elif screen == "Administrador":
    screen_admin()
elif screen in ["Reservar Puesto Flex", "Reservar Sala de Reuniones", "Mis Reservas y Listados"]:
    screen_reservas(screen)
else:
    screen_inicio()

st.markdown("</div>", unsafe_allow_html=True)  # end main
st.markdown("</div>", unsafe_allow_html=True)  # end shell

# ---------------------------------------------------------
# TOPBAR (logo izquierda + título centro)
# ---------------------------------------------------------
c1, c2, c3 = st.columns([1, 2, 1], vertical_alignment="center")
with c1:
    lp = Path(global_logo_path)
    if lp.exists():
        st.image(str(lp), width=120)
    else:
        st.write("🧩 (Logo aquí)")
with c2:
    st.markdown(f"<h3 style='text-align:center;margin:0;'>{site_title}</h3>", unsafe_allow_html=True)
with c3:
    st.write("")
st.divider()

# ---------------------------------------------------------
# MENÚ IZQUIERDO (continuación del layout que te di)
#   - Reservas -> selectbox con 3 opciones
#   - Administrador -> selectbox acceso/login
# ---------------------------------------------------------

# Si ya tienes el "shell" grid y la card izquierda, este bloque va dentro.
# Si NO lo tienes, igual puedes usarlo con st.columns, pero asumo que ya está.

# === Menú izquierdo: Reservas (con tus 3 opciones) ===
opcion_reserva = st.selectbox(
    "Reservas",
    ["—", "Reservar Puesto Flex", "Reservar Sala de Reuniones", "Mis Reservas y Listados"],
    index=0,
    key="nav_reservas_select",
    label_visibility="collapsed",
)

# === Menú izquierdo: Administrador ===
opcion_admin = st.selectbox(
    "Administrador",
    ["—", "Acceso"],
    index=0,
    key="nav_admin_select",
    label_visibility="collapsed",
)

# Decide pantalla (prioriza lo último escogido)
if opcion_reserva != "—":
    st.session_state["screen"] = opcion_reserva
    st.session_state["nav_admin_select"] = "—"
elif opcion_admin != "—":
    st.session_state["screen"] = "Administrador"
    st.session_state["nav_reservas_select"] = "—"
else:
    st.session_state.setdefault("screen", "Inicio")

screen = st.session_state.get("screen", "Inicio")

# ---------------------------------------------------------
# PANTALLAS
# ---------------------------------------------------------

# ==========================================
# INICIO
# ==========================================
if screen == "Inicio":
    st.info("Selecciona una opción en **Reservas** o entra a **Administrador**.")

# ==========================================
# A) RESERVAR PUESTO FLEX (tu lógica intacta)
# ==========================================
elif screen == "Reservar Puesto Flex":
    import datetime

    st.header("Reservar Puesto Flex")
    st.info("Reserva de 'Cupos libres' (Máximo 2 días por mes).")

    df = read_distribution_df(conn)

    if df.empty:
        st.warning("⚠️ No hay configuración de distribución cargada en el sistema.")
    else:
        c1, c2 = st.columns(2)
        fe = c1.date_input("Selecciona Fecha", min_value=datetime.date.today(), key="fp")
        pisos_disp = sort_floors(df["piso"].unique())
        pi = c2.selectbox("Selecciona Piso", pisos_disp, key="pp")

        dn = ORDER_DIAS[fe.weekday()] if fe.weekday() < 5 else "FinDeSemana"

        if dn == "FinDeSemana":
            st.error("🔒 Es fin de semana. No se pueden realizar reservas.")
        else:
            sub_libres = df[
                (df["piso"].astype(str) == str(pi)) &
                (df["dia"].astype(str) == str(dn)) &
                (df["equipo"].astype(str).str.strip().str.lower() == "cupos libres")
            ]

            total_cupos = int(pd.to_numeric(sub_libres["cupos"], errors="coerce").fillna(0).sum()) if not sub_libres.empty else 0

            all_res = list_reservations_df(conn)
            if all_res is None or all_res.empty:
                ocupados = 0
            else:
                def _norm_piso_local(x):
                    s = str(x).strip()
                    if s.lower().startswith("piso piso"):
                        s = s[5:].strip()
                    if not s.lower().startswith("piso"):
                        s = f"Piso {s}"
                    return s

                mask = (
                    all_res["reservation_date"].astype(str) == str(fe)
                ) & (
                    all_res["piso"].astype(str).map(_norm_piso_local) == _norm_piso_local(pi)
                )
                ocupados = int(mask.sum())

            disponibles = max(0, total_cupos - ocupados)

            if disponibles > 0:
                st.success(f"✅ **Hay cupo: Quedan {disponibles} puestos disponibles**")
            else:
                st.error(f"🔴 **AGOTADO: Se ocuparon los {total_cupos} puestos del día.**")

            st.markdown("### Datos del Solicitante")

            equipos_disponibles = sorted(df[df["piso"] == pi]["equipo"].unique().tolist())
            equipos_disponibles = [e for e in equipos_disponibles if e != "Cupos libres"]

            with st.form("form_puesto"):
                cf1, cf2 = st.columns(2)
                nombre = cf1.text_input("Nombre")
                area_equipo = cf1.selectbox("Área / Equipo", equipos_disponibles if equipos_disponibles else ["General"])
                em = cf2.text_input("Correo Electrónico")

                submitted = st.form_submit_button(
                    "Confirmar Reserva",
                    type="primary",
                    disabled=(disponibles <= 0)
                )

                if submitted:
                    if not nombre.strip():
                        st.error("Por favor ingresa tu nombre.")
                    if not em:
                        st.error("Por favor ingresa tu correo electrónico.")
                    elif not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', em):
                        st.error("Por favor ingresa un correo electrónico válido (ejemplo: usuario@ejemplo.com).")
                    elif user_has_reservation(conn, em, str(fe)):
                        st.error("Ya tienes una reserva registrada para esta fecha.")
                    elif count_monthly_free_spots(conn, em, fe) >= 2:
                        st.error("Has alcanzado el límite de 2 reservas mensuales.")
                    elif disponibles <= 0:
                        st.error("Lo sentimos, el cupo se acaba de agotar.")
                    else:
                        add_reservation(
                            conn,
                            area_equipo,
                            em,
                            pi,
                            str(fe),
                            "Cupos libres",
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )
                        msg = (
                            f"✅ Reserva Confirmada:\n\n"
                            f"- Área/Equipo: {area_equipo}\n"
                            f"- Fecha: {fe}\n"
                            f"- Piso: {pi}\n"
                            f"- Tipo: Puesto Flex"
                        )
                        st.success(msg)

                        email_sent = send_reservation_email(em, "Confirmación Puesto", msg.replace("\n", "<br>"))
                        if email_sent:
                            st.info("📧 Correo de confirmación enviado")
                        else:
                            st.warning("⚠️ No se pudo enviar el correo. Verifica la configuración SMTP.")

                        st.rerun()

# ==========================================
# B) RESERVAR SALA (tu lógica intacta)
# ==========================================
elif screen == "Reservar Sala de Reuniones":
    import datetime

    st.header("Reservar Sala de Reuniones")
    st.info("Selecciona tu equipo/área y luego elige la sala y horario disponible")

    df_dist = read_distribution_df(conn)
    equipos_lista = []
    if not df_dist.empty:
        equipos_lista = sorted([e for e in df_dist["equipo"].unique() if e != "Cupos libres"])

    if not equipos_lista:
        st.warning("⚠️ No hay equipos configurados. Contacta al administrador.")
    else:
        st.markdown("### Selecciona tu Equipo/Área")
        equipo_seleccionado = st.selectbox("Equipo/Área", equipos_lista, key="equipo_sala_sel")

        st.markdown("---")
        st.markdown("### Selecciona Sala y Horario")

        salas_disponibles = [
            "Sala Reuniones Pequeña Piso 1",
            "Sala Reuniones Grande Piso 1",
            "Sala Reuniones Piso 2",
            "Sala Reuniones Piso 3",
        ]

        c_sala, c_fecha = st.columns(2)
        sl = c_sala.selectbox("Selecciona Sala", salas_disponibles, key="sala_sel")

        if "Piso 1" in sl:
            pi_s = "Piso 1"
        elif "Piso 2" in sl:
            pi_s = "Piso 2"
        elif "Piso 3" in sl:
            pi_s = "Piso 3"
        else:
            pi_s = "Piso 1"

        fe_s = c_fecha.date_input("Fecha", min_value=datetime.date.today(), key="fs_sala")

        df_reservas_sala = get_room_reservations_df(conn)
        reservas_hoy = []
        if df_reservas_sala is not None and not df_reservas_sala.empty:
            mask = (df_reservas_sala["reservation_date"].astype(str) == str(fe_s)) & (df_reservas_sala["room_name"] == sl)
            reservas_hoy = df_reservas_sala[mask].to_dict("records")

        st.markdown("#### Horarios Disponibles")

        if reservas_hoy:
            horarios_ocupados = ", ".join([f"{r.get('start_time','')} - {r.get('end_time','')}" for r in reservas_hoy])
            st.warning(f"⚠️ Horarios ocupados: {horarios_ocupados}")

        slots_completos = generate_time_slots("08:00", "20:00", 15)
        if len(slots_completos) < 2:
            st.error("No hay horarios configurados para esta sala.")
        else:
            opciones_inicio = slots_completos[:-1]
            inicio_key = f"inicio_sala_{sl}"
            fin_key = f"fin_sala_{sl}"

            hora_inicio_sel = st.selectbox("Hora de inicio", opciones_inicio, index=0, key=inicio_key)

            pos_inicio = slots_completos.index(hora_inicio_sel)
            opciones_fin = slots_completos[pos_inicio + 1:]

            if not opciones_fin:
                st.warning("Selecciona una hora de inicio anterior a las 20:00 hrs.")
            else:
                idx_fin = min(4, len(opciones_fin) - 1)  # default ~ 1 hora
                hora_fin_sel = st.selectbox("Hora de término", opciones_fin, index=idx_fin, key=fin_key)

                inicio_dt = datetime.datetime.strptime(hora_inicio_sel, "%H:%M")
                fin_dt = datetime.datetime.strptime(hora_fin_sel, "%H:%M")
                duracion_min = int((fin_dt - inicio_dt).total_seconds() / 60)

                if duracion_min < 15:
                    st.error("El intervalo debe ser de al menos 15 minutos.")
                else:
                    conflicto_actual = check_room_conflict(reservas_hoy, str(fe_s), sl, hora_inicio_sel, hora_fin_sel)
                    if conflicto_actual:
                        st.error("❌ Ese intervalo ya está reservado. Elige otro horario.")

                    st.markdown("---")
                    st.markdown(f"### Confirmar Reserva: {hora_inicio_sel} - {hora_fin_sel}")

                    with st.form("form_sala"):
                        st.info(
                            f"**Equipo/Área:** {equipo_seleccionado}\n\n"
                            f"**Sala:** {sl}\n\n"
                            f"**Fecha:** {fe_s}\n\n"
                            f"**Horario:** {hora_inicio_sel} - {hora_fin_sel}"
                        )
                        e_s = st.text_input("Correo Electrónico", key="email_sala")
                        sub_sala = st.form_submit_button("✅ Confirmar Reserva", type="primary", disabled=conflicto_actual)

                    if sub_sala:
                        if not e_s:
                            st.error("Por favor ingresa tu correo electrónico.")
                        elif not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', e_s):
                            st.error("Por favor ingresa un correo electrónico válido (ejemplo: usuario@ejemplo.com).")
                        elif check_room_conflict(get_room_reservations_df(conn).to_dict("records"), str(fe_s), sl, hora_inicio_sel, hora_fin_sel):
                            st.error("❌ Conflicto: La sala ya está ocupada en ese horario.")
                        else:
                            add_room_reservation(
                                conn,
                                equipo_seleccionado,
                                e_s,
                                pi_s,
                                sl,
                                str(fe_s),
                                hora_inicio_sel,
                                hora_fin_sel,
                                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            )

                            msg = (
                                f"✅ Sala Confirmada:\n\n"
                                f"- Equipo/Área: {equipo_seleccionado}\n"
                                f"- Sala: {sl}\n"
                                f"- Fecha: {fe_s}\n"
                                f"- Horario: {hora_inicio_sel} - {hora_fin_sel}"
                            )
                            st.success(msg)

                            try:
                                email_sent = send_reservation_email(e_s, "Reserva Sala", msg.replace("\n", "<br>"))
                                if email_sent:
                                    st.info("📧 Correo de confirmación enviado")
                                else:
                                    st.warning("⚠️ No se pudo enviar el correo. Verifica la configuración SMTP.")
                            except Exception as email_error:
                                st.warning(f"⚠️ Error al enviar correo: {email_error}")

                            st.rerun()

# ==========================================
# C) MIS RESERVAS Y LISTADOS
# ==========================================
elif screen == "Mis Reservas y Listados":
    st.header("Mis Reservas y Listados")

    # =========================
    # A) TABLAS DE RESERVAS
    # =========================
    ver_tipo = st.selectbox(
        "Ver:",
        ["Reserva de Puestos", "Reserva de Salas"],
        key="mis_reservas_ver_tipo"
    )

    df_puestos = list_reservations_df(conn) or pd.DataFrame()
    df_salas = get_room_reservations_df(conn) or pd.DataFrame()

    if ver_tipo == "Reserva de Puestos":
        if df_puestos.empty:
            st.info("No hay reservas de puestos registradas.")
        else:
            df_view = df_puestos.copy().rename(columns={
                "piso": "Piso",
                "user_name": "Nombre",
                "team_area": "Equipo",
                "user_email": "Correo",
                "reservation_date": "Fecha de Reserva",
            })
            cols = ["Piso", "Nombre", "Equipo", "Correo", "Fecha de Reserva"]
            st.dataframe(df_view[[c for c in cols if c in df_view.columns]], hide_index=True, use_container_width=True)

    else:  # Reserva de Salas
        if df_salas.empty:
            st.info("No hay reservas de salas registradas.")
        else:
            df_view = df_salas.copy().rename(columns={
                "room_name": "Sala",
                "user_name": "Equipo",
                "user_email": "Correo",
                "reservation_date": "Fecha de Reserva",
                "start_time": "Hora Inicio",
                "end_time": "Hora Fin",
            })
            cols = ["Sala", "Equipo", "Correo", "Fecha de Reserva", "Hora Inicio", "Hora Fin"]
            st.dataframe(df_view[[c for c in cols if c in df_view.columns]], hide_index=True, use_container_width=True)

    st.markdown("---")

    # =========================
    # B) ANULAR RESERVAS (por correo + checklist + popup bonito)
    # =========================
    with st.expander("Anular Reservas", expanded=False):
        correo_buscar = st.text_input(
            "Correo asociado a la(s) reserva(s)",
            key="anular_correo",
            placeholder="usuario@ejemplo.com"
        ).strip()

        buscar = st.button("Buscar", type="primary", key="btn_buscar_reservas")

        st.session_state.setdefault("anular_candidates", [])
        st.session_state.setdefault("anular_checks", {})  # dict index->bool

        if buscar:
            st.session_state["anular_candidates"] = []
            st.session_state["anular_checks"] = {}

            if not correo_buscar:
                st.error("Ingresa un correo para buscar reservas.")
            elif not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', correo_buscar):
                st.error("Ingresa un correo válido.")
            else:
                correo_norm = correo_buscar.lower()
                candidates = []

                # --- Puestos ---
                if not df_puestos.empty and "user_email" in df_puestos.columns:
                    hits = df_puestos[df_puestos["user_email"].astype(str).str.lower().str.strip() == correo_norm].copy()
                    for _, r in hits.iterrows():
                        candidates.append({
                            "kind": "puesto",
                            "user_email": str(r.get("user_email", "")).strip(),
                            "reservation_date": str(r.get("reservation_date", "")).strip(),
                            "team_area": str(r.get("team_area", "")).strip(),
                            "piso": str(r.get("piso", "")).strip(),
                            "user_name": str(r.get("user_name", "")).strip(),
                        })

                # --- Salas ---
                if not df_salas.empty and "user_email" in df_salas.columns:
                    hits = df_salas[df_salas["user_email"].astype(str).str.lower().str.strip() == correo_norm].copy()
                    for _, r in hits.iterrows():
                        inicio_raw = str(r.get("start_time", "")).strip()
                        inicio = inicio_raw[:5] if len(inicio_raw) >= 5 else inicio_raw
                        candidates.append({
                            "kind": "sala",
                            "user_email": str(r.get("user_email", "")).strip(),
                            "reservation_date": str(r.get("reservation_date", "")).strip(),
                            "room_name": str(r.get("room_name", "")).strip(),
                            "start_time": inicio,
                            "end_time": str(r.get("end_time", "")).strip(),
                            "piso": str(r.get("piso", "")).strip(),
                            "user_name": str(r.get("user_name", "")).strip(),
                        })

                st.session_state["anular_candidates"] = candidates
                st.session_state["anular_checks"] = {i: False for i in range(len(candidates))}

        candidates = st.session_state.get("anular_candidates") or []

        if candidates:
            st.markdown("### Marca las reservas que quieres anular")
            st.caption("Las que NO marques se mantienen.")

            b1, b2, _ = st.columns([1, 1, 2])
            with b1:
                if st.button("Marcar todas", key="btn_mark_all"):
                    st.session_state["anular_checks"] = {i: True for i in range(len(candidates))}
                    st.rerun()
            with b2:
                if st.button("Desmarcar todas", key="btn_unmark_all"):
                    st.session_state["anular_checks"] = {i: False for i in range(len(candidates))}
                    st.rerun()

            selected_items = []
            for i, item in enumerate(candidates):
                label = _fmt_item(item)
                current = bool(st.session_state["anular_checks"].get(i, False))
                new_val = st.checkbox(label, value=current, key=f"anular_ck_{i}")
                st.session_state["anular_checks"][i] = new_val
                if new_val:
                    selected_items.append(item)

            if st.button("Anular seleccionadas", type="primary", key="btn_anular_multi"):
                if not selected_items:
                    st.warning("Marca al menos una reserva.")
                else:
                    open_confirm_delete_multi(selected_items)

        elif correo_buscar:
            st.info("No se encontraron reservas para ese correo.")

    render_confirm_delete_multi_dialog(conn)

# ==========================================
# ADMINISTRADOR (solo acceso aquí; lo demás lo enchufas después)
# ==========================================
elif screen == "Administrador":
    st.header("Administrador")

    st.session_state.setdefault("forgot_mode", False)

    if not st.session_state["forgot_mode"]:
        email = st.text_input("Ingresar correo", key="admin_login_email")
        password = st.text_input("Contraseña", type="password", key="admin_login_pass")

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Olvidaste tu contraseña", key="btn_admin_forgot"):
                st.session_state["forgot_mode"] = True
                st.rerun()

        with c2:
            if st.button("Acceder", type="primary", key="btn_admin_login"):
                # TODO: valida con get_admin_credentials / tokens
                if not email or not password:
                    st.warning("Completa correo y contraseña.")
                else:
                    st.success("Login recibido (validación real pendiente).")
    else:
        reset_email = st.text_input("Correo de acceso", key="admin_reset_email")
        if st.button("Enviar código", key="btn_admin_send_code"):
            if not reset_email:
                st.warning("Ingresa tu correo.")
            else:
                st.success("Código enviado (simulado).")

        st.caption("Ingresa el código recibido en tu correo.")
        code = st.text_input("Código", key="admin_reset_code")

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Validar código", type="primary", key="btn_admin_validate"):
                if not code:
                    st.warning("Ingresa el código.")
                else:
                    st.success("Código validado (simulado).")

        with c2:
            if st.button("Volver a Acceso", key="btn_admin_back"):
                st.session_state["forgot_mode"] = False
                st.rerun()

# ---------------------------------------------------------
# FUNCIONES DE DISTRIBUCIÓN
# ---------------------------------------------------------
def _get_team_and_dotacion_cols(df_eq: pd.DataFrame):
    if df_eq is None or df_eq.empty:
        return None, None

    cols = list(df_eq.columns)
    col_team = next((c for c in cols if "equipo" in c.lower()), None)
    col_dot = next((c for c in cols if "personas" in c.lower()), None)
    if not col_dot:
        col_dot = next((c for c in cols if "dot" in c.lower()), None)
    return col_team, col_dot

def _equity_score(rows, deficit, dot_map: dict, days_per_week=5):
    """
    Score de equidad:
    - Convierte asignación semanal por equipo a "fracción de cobertura"
      coverage = cupos_asignados / (personas * 5)
    - Queremos que todas las coverages sean lo más parecidas posible.
    """
    assigned = {}
    for r in rows or []:
        eq = str(r.get("equipo", "")).strip()
        if not eq or eq.lower() == "cupos libres":
            continue
        try:
            cup = int(float(str(r.get("cupos", 0)).replace(",", ".")))
        except Exception:
            cup = 0
        assigned[eq] = assigned.get(eq, 0) + cup

    coverages = []
    for eq, people in dot_map.items():
        needed = float(people) * float(days_per_week)
        if needed <= 0:
            continue
        coverages.append(assigned.get(eq, 0) / needed)

    if not coverages:
        return 9999.0 + (len(deficit) if deficit else 0)

    coverages = np.array(coverages, dtype=float)
    std = float(np.std(coverages))                 # dispersión
    rng = float(np.max(coverages) - np.min(coverages))  # diferencia max-min
    conflicts = float(len(deficit) if deficit else 0)   # penalización por conflictos

    # Pesos: std manda, luego rango, luego conflictos
    return (1.0 * std) + (0.6 * rng) + (0.2 * conflicts)

def _dot_map_from_equipos(df_eq: pd.DataFrame) -> dict:
    if df_eq is None or df_eq.empty:
        return {}
    col_team = next((c for c in df_eq.columns if "equipo" in c.lower()), None)
    col_dot = None
    for key in ["personas", "dotacion", "dotación", "dot"]:
        col_dot = next((c for c in df_eq.columns if key in c.lower()), None)
        if col_dot:
            break
    if not col_team or not col_dot:
        return {}

    out = {}
    for _, r in df_eq.iterrows():
        team = str(r.get(col_team, "")).strip()
        if not team or team.lower() == "cupos libres":
            continue
        try:
            val = int(float(str(r.get(col_dot, 0)).replace(",", ".")))
        except Exception:
            continue
        if val > 0:
            out[team] = val
    return out

def _cap_map_from_capacidades(df_cap: pd.DataFrame) -> dict:
    if df_cap is None or df_cap.empty:
        return {}

    col_piso = next((c for c in df_cap.columns if "piso" in c.lower()), None)
    col_dia = next((c for c in df_cap.columns if "dia" in c.lower() or "día" in c.lower()), None)
    col_cap = next((c for c in df_cap.columns if "cap" in c.lower() or "cupo" in c.lower()), None)

    if not col_piso or not col_cap:
        return {}

    out = {}
    for _, r in df_cap.iterrows():
        piso_raw = str(r.get(col_piso, "")).strip()
        if not piso_raw or piso_raw.lower() == "nan":
            continue
        piso = piso_raw if piso_raw.lower().startswith("piso") else f"Piso {piso_raw}"

        dia = str(r.get(col_dia, "")).strip() if col_dia else ""
        if dia.lower() == "nan":
            dia = ""

        try:
            cap = int(float(str(r.get(col_cap, 0)).replace(",", ".")))
        except Exception:
            continue

        if cap <= 0:
            continue

        out[(piso, dia)] = cap

    return out

def _min_daily_for_team(dotacion: int, factor: float = 1.0) -> int:
    if dotacion >= 13: base = 6
    elif dotacion >= 8: base = 4
    elif dotacion >= 5: base = 3
    elif dotacion >= 3: base = 2
    else: base = 0
    return int(round(base * factor))

def _largest_remainder_allocation(weights: dict, total: int) -> dict:
    """
    Reparte 'total' proporcional a weights (enteros) con método Hamilton.
    """
    if total <= 0 or not weights:
        return {k: 0 for k in weights.keys()}

    s = sum(max(0, int(v)) for v in weights.values())
    if s <= 0:
        return {k: 0 for k in weights.keys()}

    quotas = {k: (max(0, int(w)) / s) * total for k, w in weights.items()}
    base = {k: int(q) for k, q in quotas.items()}
    used = sum(base.values())
    remain = total - used

    frac = sorted(((k, quotas[k] - base[k]) for k in quotas), key=lambda x: x[1], reverse=True)
    i = 0
    while remain > 0 and i < len(frac):
        k = frac[i][0]
        base[k] += 1
        remain -= 1
        i += 1
        if i >= len(frac) and remain > 0:
            i = 0
    return base

def generate_distribution_math_correct(
    df_eq: pd.DataFrame,
    df_cap: pd.DataFrame,
    cupos_libres_diarios: int = 2,
    min_dotacion_para_garantia: int = 3,
    min_factor: float = 1.0
):
    """
    Genera distribución diaria por piso/día:
    - Mantiene cupos libres fijos.
    - Garantiza mínimos diarios escalados para equipos con dotación >= 3.
    - Resto se reparte proporcional a dotación.
    """
    dot = _dot_map_from_equipos(df_eq)
    cap_map = _cap_map_from_capacidades(df_cap)

    # pisos: desde capacidades si existe, si no desde dot (un piso default)
    pisos = sorted({p for (p, _) in cap_map.keys()} or {"Piso 1"})

    rows = []
    deficits = []

    for piso in pisos:
        for dia in ORDER_DIAS:
            # capacidad para (piso,dia) o fallback (piso,"")
            cap = cap_map.get((piso, dia)) or cap_map.get((piso, "")) or 0
            if cap <= 0:
                # si no hay capacidades, no podemos ser "matemáticamente correctos" por piso/día
                deficits.append({"piso": piso, "dia": dia, "causa": "Sin capacidad definida en hoja Capacidades"})
                continue

            # reservar cupos libres
            libres = min(cupos_libres_diarios, cap)
            cap_rest = cap - libres

            # equipos elegibles
            teams = {k: v for k, v in dot.items() if int(v) >= min_dotacion_para_garantia}

            # mínimos diarios
            mins = {t: _min_daily_for_team(int(v), factor=min_factor) for t, v in teams.items()}
            sum_mins = sum(mins.values())

            if sum_mins > cap_rest:
                # no alcanza ni para mínimos => matemáticamente imposible con esa capacidad
                deficits.append({
                    "piso": piso, "dia": dia,
                    "causa": f"Capacidad insuficiente: mínimos({sum_mins}) > cap_restante({cap_rest})"
                })
                # asignar lo que se pueda proporcionalmente a mins (o 0)
                alloc = _largest_remainder_allocation(mins, cap_rest)
            else:
                # asigna mínimos + resto proporcional a dotación
                alloc = mins.copy()
                extra = cap_rest - sum_mins
                extra_alloc = _largest_remainder_allocation(teams, extra)
                for t, v in extra_alloc.items():
                    alloc[t] = alloc.get(t, 0) + v

            # construir filas
            total_asignado = 0
            for t, c in alloc.items():
                if c <= 0:
                    continue
                total_asignado += c
                rows.append({"piso": piso, "dia": dia, "equipo": t, "cupos": int(c)})

                rows.append({"piso": piso, "dia": dia, "equipo": "Cupos libres", "cupos": int(libres)})

            # sanity check
            if total_asignado + libres != cap:
                deficits.append({
                    "piso": piso, "dia": dia,
                    "causa": f"Mismatch: asignado({total_asignado + libres}) != capacidad({cap})"
                })

    return rows, deficits

def get_distribution_proposal(
    df_equipos,
    df_parametros,
    df_capacidades,
    strategy="random",
    ignore_params=False,
    variant_seed=None,
    variant_mode="holgura",
):
    """
    Propuesta única.
    - ignore_params=True => NO mínimos, NO días completos. Solo Saint-Laguë + reserva.
    - ignore_params=False => aplica día completo + mínimos + remanente Saint-Laguë.
    """
    eq_proc = df_equipos.copy()
    pa_proc = df_parametros.copy()

    col_sort = None
    for c in eq_proc.columns:
        if c.lower().strip() == "dotacion":
            col_sort = c
            break
    if not col_sort and strategy != "random":
        strategy = "random"

    if strategy == "random":
        eq_proc = eq_proc.sample(frac=1).reset_index(drop=True)
    elif strategy == "size_desc" and col_sort:
        eq_proc = eq_proc.sort_values(by=col_sort, ascending=False).reset_index(drop=True)
    elif strategy == "size_asc" and col_sort:
        eq_proc = eq_proc.sort_values(by=col_sort, ascending=True).reset_index(drop=True)

    CUPOS_LIBRES_FIJOS = 2

    rows, deficit_report, audit, score = compute_distribution_from_excel(
        equipos_df=eq_proc,
        parametros_df=pa_proc,
        df_capacidades=df_capacidades,
        cupos_reserva=CUPOS_LIBRES_FIJOS,
        ignore_params=ignore_params,
        variant_seed=variant_seed,
        variant_mode=variant_mode,
    )

    final_deficits = filter_minimum_deficits(deficit_report)

    if ignore_params:
        final_deficits = []

    # opcional: devolver score/audit si te sirve
    return rows, final_deficits, audit, score

def generate_balanced_distribution(
    df_eq: pd.DataFrame,
    df_pa: pd.DataFrame,
    df_cap: pd.DataFrame,
    ignore_params: bool,
    num_attempts: int = 80,
    seed: Optional[int] = None
):
    if seed is None:
        seed = random.randint(1, 10_000_000)

    dot_map = _dot_map_from_equipos(df_eq)

    best_score = float("inf")
    best_rows, best_def, best_meta = None, None, None

    if ignore_params:
        rng = random.Random(seed)
        factors = [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15]

        for i in range(num_attempts):
            f = factors[i % len(factors)]
            f = max(0.7, min(1.3, f + rng.uniform(-0.03, 0.03)))

            rows, deficits = generate_distribution_math_correct(
                df_eq, df_cap,
                cupos_libres_diarios=2,
                min_dotacion_para_garantia=3,
                min_factor=f
            )

            def_clean = filter_minimum_deficits(deficits)
            score = _equity_score(rows, def_clean, dot_map)

            hard = sum(1 for d in (deficits or []) if "Capacidad insuficiente" in str(d.get("causa", "")))
            score += hard * 10.0

            if score < best_score:
                best_score = score
                best_rows = rows
                best_def = def_clean
                best_meta = {"score": best_score, "seed": seed, "attempts": num_attempts, "min_factor": f}

        return best_rows, best_def, best_meta

    for i in range(num_attempts):
        attempt_seed = int(seed) + i * 9973
        eq_shuffled = df_eq.sample(frac=1, random_state=attempt_seed).reset_index(drop=True)

        rows, deficit = get_distribution_proposal(
            eq_shuffled, df_pa, df_cap,
            strategy="random",
            ignore_params=False
        )

        def_clean = filter_minimum_deficits(deficit)
        score = _equity_score(rows, def_clean, dot_map)

        if score < best_score:
            best_score = score
            best_rows = rows
            best_def = def_clean
            best_meta = {"score": best_score, "seed": attempt_seed, "attempts": num_attempts}

    return best_rows, best_def, best_meta

def filter_minimum_deficits(deficit_list):
    """Recalcula los déficits únicamente cuando el mínimo no se cumple."""
    filtered = []
    for item in deficit_list or []:
        try:
            minimo = int(float(str(item.get("minimo", 0)).strip()))
            asignado = int(float(str(item.get("asignado", 0)).strip()))
        except (TypeError, ValueError):
            continue
        deficit_val = max(0, minimo - asignado)
        if deficit_val > 0:
            fixed = dict(item)
            fixed["minimo"] = minimo
            fixed["asignado"] = asignado
            fixed["deficit"] = deficit_val
            fixed["causa"] = f"Faltan {deficit_val} puestos (capacidad insuficiente)"
            filtered.append(fixed)
    return filtered

def recompute_pct(rows):
    df = pd.DataFrame(rows)
    if df.empty or not {"piso","dia","equipo","cupos"}.issubset(df.columns):
        return None

    df["cupos"] = pd.to_numeric(df["cupos"], errors="coerce").fillna(0).astype(int)

    mask_libres = df["equipo"].astype(str).str.strip().str.lower().isin(["cupos libres", "cupo libre"])
    base = df[~mask_libres].groupby(["piso","dia"])["cupos"].sum()
    base = base.rename("total").reset_index()

    df = df.merge(base, on=["piso","dia"], how="left")
    df["total"] = df["total"].fillna(0)

    def calc_pct(r):
        if str(r["equipo"]).strip().lower() in ["cupos libres", "cupo libre"]:
            return 0
        if r["total"] <= 0:
            return 0
        return round((r["cupos"] / r["total"]) * 100, 2)

    df["pct"] = df.apply(calc_pct, axis=1)
    return df.to_dict("records")

def ensure_piso_label(rows):
    """Convierte piso '1' -> 'Piso 1' para compatibilidad con el resto de la app."""
    out = []
    for r in rows or []:
        rr = dict(r)
        p = str(rr.get("piso", "")).strip()
        if p and not p.lower().startswith("piso"):
            rr["piso"] = f"Piso {p}"
        out.append(rr)
    return out

def infer_team_dotacion_map(df):
    """Intenta inferir la dotación total por equipo usando la data disponible."""
    if df is None or df.empty:
        return {}
    
    cols_lower = {c.lower(): c for c in df.columns}
    col_equipo = None
    for key, col in cols_lower.items():
        if "equipo" in key:
            col_equipo = col
            break
    if not col_equipo:
        return {}
    
    col_dot = next((col for key, col in cols_lower.items() if "dotacion" in key or "dotación" in key), None)
    cupos_col = next((col for key, col in cols_lower.items() if "cupo" in key), None)
    pct_col = None
    for key, col in cols_lower.items():
        if "%distrib" in key or "pct" in key or "porcentaje" in key:
            pct_col = col
            break
    
    dot_map = {}
    
    if col_dot:
        series = df[[col_equipo, col_dot]].dropna()
        for _, row in series.iterrows():
            eq = str(row[col_equipo]).strip()
            if not eq or eq.lower().startswith("cupos libres"):
                continue
            try:
                dot = int(float(row[col_dot]))
            except (TypeError, ValueError):
                continue
            if dot > 0:
                dot_map.setdefault(eq, dot)
    
    if not dot_map and cupos_col and pct_col:
        temp = df[[col_equipo, cupos_col, pct_col]].dropna()
        for _, row in temp.iterrows():
            eq = str(row[col_equipo]).strip()
            if not eq or eq.lower().startswith("cupos libres"):
                continue
            try:
                cupos_val = float(str(row[cupos_col]).replace(",", "."))
                pct_val = float(str(row[pct_col]).replace("%", "").replace(",", "."))
            except (TypeError, ValueError):
                continue
            if pct_val <= 0:
                continue
            dot_est = int(round(cupos_val * 100 / pct_val))
            if dot_est > 0 and eq not in dot_map:
                dot_map[eq] = dot_est
    
    return dot_map

def clean_reservation_df(df, tipo="puesto"):
    if df.empty: return df
    cols_drop = [c for c in df.columns if c.lower() in ['id', 'created_at', 'registro', 'id.1']]
    df = df.drop(columns=cols_drop, errors='ignore')
    
    if tipo == "puesto":
        df = df.rename(columns={'user_name': 'Nombre', 'user_email': 'Correo', 'piso': 'Piso', 'reservation_date': 'Fecha Reserva', 'team_area': 'Ubicación'})
        cols = ['Fecha Reserva', 'Piso', 'Ubicación', 'Nombre', 'Correo']
        return df[[c for c in cols if c in df.columns]]
    elif tipo == "sala":
        df = df.rename(columns={'user_name': 'Nombre', 'user_email': 'Correo', 'piso': 'Piso', 'room_name': 'Sala', 'reservation_date': 'Fecha', 'start_time': 'Inicio', 'end_time': 'Fin'})
        cols = ['Fecha', 'Inicio', 'Fin', 'Sala', 'Piso', 'Nombre', 'Correo']
        return df[[c for c in cols if c in df.columns]]
    return df

def hex_to_rgba(hex_color, alpha=0.3):
    """Convierte color hex a formato rgba para el canvas."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"
    return f"rgba(0, 160, 74, {alpha})"

# --- GENERADORES DE PDF ---
def create_merged_pdf(piso_sel, conn, global_logo_path):
    # Blindaje: piso_sel puede venir None / NaN / int, etc.
    if piso_sel is None or (isinstance(piso_sel, float) and pd.isna(piso_sel)):
        piso_sel = "Piso 1"
    piso_sel = str(piso_sel)

    p_num = piso_sel.replace("Piso ", "").strip()
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    found_any = False

    df = read_distribution_df(conn)
    base_config = st.session_state.get('last_style_config', {})

    for dia in ORDER_DIAS:
        subset = df[(df['piso'] == piso_sel) & (df['dia'] == dia)]
        current_seats = dict(zip(subset['equipo'], subset['cupos']))

        day_config = base_config.copy()
        if not day_config.get("subtitle_text"):
            day_config["subtitle_text"] = f"Día: {dia}"
        else:
            if "Día:" not in str(day_config.get("subtitle_text","")):
                day_config["subtitle_text"] = f"Día: {dia}"

        img_path = generate_colored_plan(piso_sel, dia, current_seats, "PNG", day_config, global_logo_path)

        if img_path and Path(img_path).exists():
            found_any = True
            pdf.add_page()
            try:
                pdf.image(str(img_path), x=10, y=10, w=190)
            except:
                pass

    if not found_any:
        return None
    return pdf.output(dest='S').encode('latin-1')

def generate_full_pdf(
    distrib_df: pd.DataFrame,
    semanal_df: pd.DataFrame,
    out_path: str = "reporte.pdf",
    logo_path: Path = Path("static/logo.png"),
    deficit_data=None
):
    """
    PDF = INFORME (no motor de cálculo).
    - Usa los mismos valores que ves en la app (ideal: lo que viene de BD).
    - %Distrib diario:
        * si viene en la data, se usa
        * si no viene o viene vacío, se recalcula SOLO desde los cupos del mismo piso/día
          para que la suma por piso/día sea 100% (incluyendo Cupos libres)
    - Incluye Reporte de Déficit si existe.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)

    # -------------------------
    # Helpers
    # -------------------------
    def _clean_text(x):
        return clean_pdf_text(str(x) if x is not None else "")

    def _find_col(df, candidates):
        if df is None or df.empty:
            return None
        lower_map = {str(c).strip().lower(): c for c in df.columns}
        for cand in candidates:
            key = cand.strip().lower()
            if key in lower_map:
                return lower_map[key]
        # fallback: contains
        for cand in candidates:
            key = cand.strip().lower()
            hit = next((orig for low, orig in lower_map.items() if key in low), None)
            if hit:
                return hit
        return None

    def _to_num(x, default=0.0):
        try:
            s = str(x).replace("%", "").strip().replace(",", ".")
            if s.lower() in ["nan", "none", ""]:
                return default
            return float(s)
        except Exception:
            return default

    def _norm_piso(x):
        s = str(x).strip()
        if s.lower() in ["nan", "none", ""]:
            return "-"
        # si viene "2" => "Piso 2"
        if not s.lower().startswith("piso"):
            s = f"Piso {s}"
        return s

    def _pct_fmt(v):
        try:
            return f"{float(v):.1f}%"
        except Exception:
            return "-"

    # -------------------------
    # 1) Preparar data (copias)
    # -------------------------
    df_print = distrib_df.copy() if distrib_df is not None else pd.DataFrame()

    # columnas esperadas (acepta mayúsc/minúsc)
    col_piso  = _find_col(df_print, ["piso"])
    col_dia   = _find_col(df_print, ["dia", "día"])
    col_equ   = _find_col(df_print, ["equipo"])
    col_cup   = _find_col(df_print, ["cupos", "cupo"])
    col_pct   = _find_col(df_print, ["pct", "%distrib", "% distrib", "%distrib diario", "%distribdiario"])

    # si faltan columnas críticas, igual genera PDF con lo que haya
    if df_print.empty or not all([col_dia, col_equ, col_cup]):
        pdf.add_page()
        pdf.set_font("Arial", "B", 16)
        if logo_path and Path(logo_path).exists():
            try:
                pdf.image(str(logo_path), x=10, y=8, w=30)
            except Exception:
                pass
        pdf.ln(25)
        pdf.cell(0, 10, _clean_text("Informe de Distribución"), ln=True, align="C")
        pdf.ln(8)
        pdf.set_font("Arial", "", 11)
        pdf.multi_cell(0, 6, _clean_text("No hay datos suficientes para generar el informe."))
        return pdf.output(dest="S").encode("latin-1")

    # Normalizar piso
    if col_piso:
        df_print[col_piso] = df_print[col_piso].apply(_norm_piso)
    else:
        df_print["piso_tmp"] = "-"
        col_piso = "piso_tmp"

    # Normalizar día/equipo
    df_print[col_dia] = df_print[col_dia].astype(str).str.strip()
    df_print[col_equ] = df_print[col_equ].astype(str).str.strip()

    # Normalizar cupos a int
    df_print[col_cup] = df_print[col_cup].apply(lambda x: int(round(_to_num(x, 0))))

    # -------------------------
    # 2) %Distrib diario coherente (no > 100)
    # -------------------------
    # Si pct no existe o está todo vacío => recalcular desde cupos por piso/día
    need_recalc = False
    if not col_pct:
        need_recalc = True
        df_print["pct_tmp"] = 0.0
        col_pct = "pct_tmp"
    else:
        # si casi todo viene NaN/0 o strings vacíos, recalcula
        pct_vals = df_print[col_pct].apply(lambda x: _to_num(x, 0.0))
        if (pct_vals.fillna(0.0).abs().sum() == 0.0):
            need_recalc = True
        df_print[col_pct] = pct_vals

    if need_recalc:
        # base: total cupos por (piso,dia) incluyendo Cupos libres
        grp = df_print.groupby([col_piso, col_dia], dropna=False)[col_cup].sum().reset_index()
        grp = grp.rename(columns={col_cup: "_total_pd"})
        df_print = df_print.merge(grp, on=[col_piso, col_dia], how="left")
        df_print[col_pct] = df_print.apply(
            lambda r: (r[col_cup] / r["_total_pd"] * 100.0) if _to_num(r.get("_total_pd", 0), 0) > 0 else 0.0,
            axis=1
        )
        df_print.drop(columns=["_total_pd"], inplace=True, errors="ignore")

    # Clamp final (por seguridad visual)
    df_print[col_pct] = df_print[col_pct].apply(lambda v: max(0.0, min(100.0, float(_to_num(v, 0.0)))))

    # Orden
    try:
        # usa tu helper de ordenamiento si aplica
        tmp = df_print.rename(columns={col_piso: "piso", col_dia: "dia"})
        tmp = apply_sorting_to_df(tmp)
        # revierte nombres a lo que teníamos
        tmp = tmp.rename(columns={"piso": col_piso, "dia": col_dia})
        df_print = tmp
    except Exception:
        pass

    # -------------------------
    # Portada + Tabla diaria
    # -------------------------
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    if logo_path and Path(logo_path).exists():
        try:
            pdf.image(str(logo_path), x=10, y=8, w=30)
        except Exception:
            pass
    pdf.ln(25)
    pdf.cell(0, 10, _clean_text("Informe de Distribución"), ln=True, align="C")
    pdf.ln(4)

    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, _clean_text("1. Detalle de Distribución Diaria"), ln=True)
    pdf.ln(2)

    # Tabla diaria
    pdf.set_font("Arial", "B", 9)
    widths = [28, 78, 22, 18, 24]  # Piso, Equipo, Día, Cupos, %Distrib
    headers = ["Piso", "Equipo", "Día", "Cupos", "%Distrib Diario"]
    for w, h in zip(widths, headers):
        pdf.cell(w, 6, _clean_text(h), border=1)
    pdf.ln()

    pdf.set_font("Arial", "", 9)

    for _, r in df_print.iterrows():
        piso_val = r.get(col_piso, "-")
        eq_val = r.get(col_equ, "")
        dia_val = r.get(col_dia, "")
        cup_val = r.get(col_cup, 0)
        pct_val = r.get(col_pct, 0.0)

        pdf.cell(widths[0], 6, _clean_text(piso_val)[:12], border=1)
        pdf.cell(widths[1], 6, _clean_text(eq_val)[:45], border=1)
        pdf.cell(widths[2], 6, _clean_text(dia_val)[:12], border=1)
        pdf.cell(widths[3], 6, _clean_text(str(int(cup_val))), border=1, align="R")
        pdf.cell(widths[4], 6, _clean_text(_pct_fmt(pct_val)), border=1, align="R")
        pdf.ln()

        # salto de página si está muy abajo
        if pdf.get_y() > 265:
            pdf.add_page()
            pdf.set_font("Arial", "B", 9)
            for w, h in zip(widths, headers):
                pdf.cell(w, 6, _clean_text(h), border=1)
            pdf.ln()
            pdf.set_font("Arial", "", 9)

    # -------------------------
    # 2) Resumen semanal por equipo (desde lo mismo)
    # -------------------------
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, _clean_text("2. Resumen de Uso Semanal por Equipo"), ln=True)
    pdf.ln(2)

    # Totales semanales desde df_print (excluye Cupos libres para resumen)
    df_week = df_print.copy()
    df_week = df_week[df_week[col_equ].str.lower() != "cupos libres"].copy()

    # Total semanal = suma cupos
    wk = df_week.groupby(col_equ, dropna=False)[col_cup].sum().reset_index()
    wk = wk.rename(columns={col_equ: "Equipo", col_cup: "Total Semanal"})
    wk["Promedio Diario"] = (wk["Total Semanal"] / 5.0).round(2)

    # Dotación opcional desde semanal_df (hoja Equipos o similar)
    dot_map = infer_team_dotacion_map(semanal_df) if semanal_df is not None else {}
    wk["Dotación"] = wk["Equipo"].map(dot_map).fillna(0).astype(int)

    def _uso(row):
        dot = int(row["Dotación"])
        tot = float(row["Total Semanal"])
        return round((tot / (dot * 5.0) * 100.0), 2) if dot > 0 else 0.0

    wk["% Uso Semanal"] = wk.apply(_uso, axis=1)

    wk = wk.sort_values(["Promedio Diario", "Equipo"], ascending=[False, True])

    # tabla resumen
    pdf.set_font("Arial", "B", 9)
    w2 = [88, 32, 32, 28]  # Equipo, Prom, Total, %Uso
    h2 = ["Equipo", "Promedio", "Total Semanal", "% Uso Semanal"]
    for w, h in zip(w2, h2):
        pdf.cell(w, 6, _clean_text(h), border=1)
    pdf.ln()

    pdf.set_font("Arial", "", 9)
    for _, r in wk.iterrows():
        pdf.cell(w2[0], 6, _clean_text(r["Equipo"])[:45], border=1)
        pdf.cell(w2[1], 6, _clean_text(str(r["Promedio Diario"])), border=1, align="R")
        pdf.cell(w2[2], 6, _clean_text(str(int(r["Total Semanal"]))), border=1, align="R")
        pdf.cell(w2[3], 6, _clean_text(_pct_fmt(r["% Uso Semanal"])), border=1, align="R")
        pdf.ln()

        if pdf.get_y() > 265:
            pdf.add_page()
            pdf.set_font("Arial", "B", 9)
            for w, h in zip(w2, h2):
                pdf.cell(w, 6, _clean_text(h), border=1)
            pdf.ln()
            pdf.set_font("Arial", "", 9)

    # -------------------------
    # Glosario (reducido)
    # -------------------------
    pdf.ln(6)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 7, _clean_text("Glosario de Métricas y Cálculos:"), ln=True)
    pdf.set_font("Arial", "", 9)
    pdf.multi_cell(
        0, 6,
        _clean_text(
            "1. % Distribución Diario = (Cupos del equipo en el día / Total cupos del piso en ese día) * 100.\n"
            "2. Promedio Diario = (Total Semanal / 5)."
            "3. % Uso Semanal = (Cupos del equipo en la semana / (Dotación * 5)) * 100 (si hay dotación disponible).\n"
            "4. Déficit = Máximo(0, Mínimo requerido - Asignado)."
        )
    )

    # -------------------------
    # 3) Reporte de Déficit (si existe)
    # -------------------------
    deficits_ui = filter_minimum_deficits(deficit_data or [])
    if deficits_ui:
        pdf.add_page()
        pdf.set_font("Arial", "B", 13)
        pdf.set_text_color(180, 0, 0)
        pdf.cell(0, 8, _clean_text("Reporte de Déficit de Cupos"), ln=True, align="C")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

        # columnas 
        pdf.set_font("Arial", "B", 9)
        wd = [14, 58, 20, 14, 14, 14, 56]  # Piso, Equipo, Día, Dot, Min, Falt, Causa
        hd = ["Piso", "Equipo", "Día", "Dot.", "Min.", "Falt.", "Causa Detallada"]
        for w, h in zip(wd, hd):
            pdf.cell(w, 6, _clean_text(h), border=1, align="C")
        pdf.ln()

        pdf.set_font("Arial", "", 9)
        for item in deficits_ui:
            piso = _norm_piso(item.get("piso", "-")).replace("Piso ", "")
            equipo = str(item.get("equipo", "")).strip()
            dia = str(item.get("dia", item.get("día", ""))).strip()
            dot = int(_to_num(item.get("dotacion", item.get("dot", 0)), 0))
            minimo = int(_to_num(item.get("minimo", 0), 0))
            falt = int(_to_num(item.get("deficit", 0), 0))
            causa = str(item.get("causa", "")).strip()

            # fila (con multiline en causa)
            y0 = pdf.get_y()
            x0 = pdf.get_x()

            pdf.cell(wd[0], 6, _clean_text(piso), border=1)
            pdf.cell(wd[1], 6, _clean_text(equipo)[:28], border=1)
            pdf.cell(wd[2], 6, _clean_text(dia)[:10], border=1, align="C")
            pdf.cell(wd[3], 6, _clean_text(str(dot)), border=1, align="R")
            pdf.cell(wd[4], 6, _clean_text(str(minimo)), border=1, align="R")

            # falt en rojo
            pdf.set_text_color(180, 0, 0)
            pdf.cell(wd[5], 6, _clean_text(str(falt)), border=1, align="R")
            pdf.set_text_color(0, 0, 0)

            # causa multiline: usar multi_cell, pero mantener bordes con truco simple
            x_causa = pdf.get_x()
            y_causa = pdf.get_y()
            pdf.multi_cell(wd[6], 6, _clean_text(causa), border=1)
            y1 = pdf.get_y()

            # volver a la derecha para seguir (multi_cell ya avanzó)
            pdf.set_xy(x0, y1)

            # salto si necesario
            if pdf.get_y() > 265:
                pdf.add_page()
                pdf.set_font("Arial", "B", 9)
                for w, h in zip(wd, hd):
                    pdf.cell(w, 6, _clean_text(h), border=1, align="C")
                pdf.ln()
                pdf.set_font("Arial", "", 9)

    # Footer fecha
    pdf.set_font("Arial", "", 8)
    pdf.set_text_color(80, 80, 80)
    try:
        now = datetime.datetime.now()
        pdf.ln(2)
        pdf.cell(0, 6, _clean_text(f"Informe generado el {now.strftime('%d/%m/%Y %H:%M')} hrs"), ln=True, align="R")
    except Exception:
        pass
    pdf.set_text_color(0, 0, 0)

    return pdf.output(dest="S").encode("latin-1")



