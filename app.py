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
#   - Admin es la pantalla principal (por defecto)
# ---------------------------------------------------------
st.session_state.setdefault("ui", {
    "app_title": "Gestor de Puestos y Salas",
    "bg_color": "#ffffff",
    "logo_path": "assets/logo.png",
    "title_font_size": 32,     # más grande
    "logo_width": 210,         # más grande
})

st.session_state.setdefault("menu_open", False)
st.session_state.setdefault("screen", "Administrador")  # ✅ principal
st.session_state.setdefault("forgot_mode", False)

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
try:
    st.session_state["ui"]["title_font_size"] = int(settings.get("title_font_size", st.session_state["ui"]["title_font_size"]))
except Exception:
    pass

# ---------------------------------------------------------
# 5) CSS
#   - sin “Menú” suelto
#   - márgenes laterales grandes (≈5cm)
#   - quitar bloque blanco superior
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

/* quitar padding top que suele verse como bloque blanco */
div[data-testid="stAppViewContainer"] > .main {{
  padding-top: 0rem !important;
}}
section.main > div {{
  padding-top: 0rem !important;
}}

/* márgenes laterales ~5cm (aprox) */
.block-container {{
  max-width: 100% !important;
  padding-top: 0.5rem !important;
  padding-left: 5cm !important;
  padding-right: 5cm !important;
}}

/* Topbar */
.mk-topbar {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 10px;
  margin-bottom: 8px;
}}
.mk-title {{
  flex: 1;
  text-align: center;
  font-weight: 900;
  margin: 0;
  line-height: 1.05;
}}
.mk-right {{
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
}}
.mk-btn-compact button {{
  border-radius: 14px !important;
  padding: 8px 14px !important;
  font-weight: 900 !important;
}}
.mk-primary-big button {{
  border-radius: 14px !important;
  padding: 10px 18px !important;
  font-weight: 900 !important;
  font-size: 16px !important;
}}

/* Drawer */
.mk-drawer {{
  background: #f3f5f7;
  border-radius: 18px;
  padding: 12px 12px 14px 12px;
  box-shadow: 0 10px 24px rgba(0,0,0,0.12);
  margin-top: 10px;
}}
.mk-drawer .stButton button {{
  width: 100% !important;
  border-radius: 14px !important;
  font-weight: 900 !important;
}}
.mk-drawer hr {{
  margin: 10px 0;
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

def sort_floors(floor_list):
    def extract_num(text):
        text = str(text)
        num = re.findall(r"\d+", text)
        return int(num[0]) if num else 0
    return sorted(list(floor_list), key=extract_num)

def apply_sorting_to_df(df: pd.DataFrame) -> pd.DataFrame:
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

def toggle_menu():
    st.session_state["menu_open"] = not st.session_state["menu_open"]

def go(screen: str):
    st.session_state["screen"] = screen

# ---------------------------------------------------------
# TOPBAR (logo izq, título centro, botón << a la derecha)
#   - sin texto “Menú” suelto
# ---------------------------------------------------------
def render_topbar():
    c1, c2, c3 = st.columns([1.2, 3.6, 1.2], vertical_alignment="center")

    with c1:
        logo_path = Path(st.session_state.ui["logo_path"])
        if logo_path.exists():
            st.image(str(logo_path), width=int(st.session_state.ui.get("logo_width", 210)))
        else:
            st.write("🧩 (Logo aquí)")

    with c2:
        size = int(st.session_state.ui.get("title_font_size", 32))
        title = st.session_state.ui.get("app_title", "Gestor de Puestos y Salas")
        st.markdown(
            f"<div class='mk-title' style='font-size:{size}px;'>{title}</div>",
            unsafe_allow_html=True
        )

    with c3:
        st.markdown("<div class='mk-right'>", unsafe_allow_html=True)
        st.markdown("<div class='mk-btn-compact'>", unsafe_allow_html=True)
        if st.button("<<" if not st.session_state["menu_open"] else ">>", key="btn_menu_toggle"):
            toggle_menu()
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------
# DRAWER (solo 2 opciones + NO Administrador)
# ---------------------------------------------------------
def render_menu_drawer():
    if not st.session_state["menu_open"]:
        return

    _, _, c = st.columns([2.2, 2.2, 1.6])
    with c:
        st.markdown("<div class='mk-drawer'>", unsafe_allow_html=True)

        if st.button("Reservas", key="drawer_reservas"):
            go("Reservas")
            st.session_state["menu_open"] = False
            st.rerun()

        if st.button("Ver Distribución y Planos", key="drawer_planos"):
            go("Planos")
            st.session_state["menu_open"] = False
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------
# ADMIN (principal)
#  - “Acceder” a la derecha, en la misma línea del botón << (topbar)
#    => Solución práctica: duplicamos la línea de acción justo bajo el topbar,
#       alineando “Acceder” a la derecha y dejando orden visual.
# ---------------------------------------------------------
def screen_admin(conn):
    st.subheader("Administrador")
    st.session_state.setdefault("forgot_mode", False)

    # Fila alineada (botón Acceder grande a la derecha)
    left, right = st.columns([4.2, 1.0], vertical_alignment="center")

    with left:
        if not st.session_state["forgot_mode"]:
            email = st.text_input("Ingresar correo", key="admin_login_email")
            password = st.text_input("Contraseña", type="password", key="admin_login_pass")
        else:
            reset_email = st.text_input("Correo de acceso", key="admin_reset_email")
            st.caption("Ingresa el código recibido en tu correo.")
            code = st.text_input("Código", key="admin_reset_code")

    with right:
        if not st.session_state["forgot_mode"]:
            st.markdown("<div class='mk-primary-big'>", unsafe_allow_html=True)
            clicked = st.button("Acceder", type="primary", key="btn_admin_login_big")
            st.markdown("</div>", unsafe_allow_html=True)

            if clicked:
                email = st.session_state.get("admin_login_email", "").strip()
                password = st.session_state.get("admin_login_pass", "")
                if not email or not password:
                    st.warning("Completa correo y contraseña.")
                else:
                    st.success("Login recibido (validación real pendiente).")
        else:
            st.markdown("<div class='mk-primary-big'>", unsafe_allow_html=True)
            send = st.button("Enviar código", type="primary", key="btn_admin_send_code_big")
            st.markdown("</div>", unsafe_allow_html=True)

            if send:
                reset_email = st.session_state.get("admin_reset_email", "").strip()
                if not reset_email:
                    st.warning("Ingresa tu correo.")
                else:
                    st.success("Código enviado (simulado).")

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            if st.button("Validar", key="btn_admin_validate_small"):
                code = st.session_state.get("admin_reset_code", "").strip()
                if not code:
                    st.warning("Ingresa el código.")
                else:
                    st.success("Código validado (simulado).")

    c1, c2 = st.columns([1, 1])
    with c1:
        if not st.session_state["forgot_mode"]:
            if st.button("Olvidaste tu contraseña", key="btn_admin_forgot"):
                st.session_state["forgot_mode"] = True
                st.rerun()
        else:
            if st.button("Volver a Acceso", key="btn_admin_back"):
                st.session_state["forgot_mode"] = False
                st.rerun()

    with c2:
        # Atajo para volver a la página principal admin desde otras pantallas
        pass

# ---------------------------------------------------------
# RESERVAS (3 pestañas)
# ---------------------------------------------------------
def screen_reservar_puesto_flex(conn):
    import datetime as _dt

    st.header("Reservar Puesto Flex")
    st.info("Reserva de 'Cupos libres' (Máximo 2 días por mes).")

    df = read_distribution_df(conn)

    if df.empty:
        st.warning("⚠️ No hay configuración de distribución cargada en el sistema.")
        return

    c1, c2 = st.columns(2)
    fe = c1.date_input("Selecciona Fecha", min_value=_dt.date.today(), key="fp")
    pisos_disp = sort_floors(df["piso"].unique())
    pi = c2.selectbox("Selecciona Piso", pisos_disp, key="pp")

    dn = ORDER_DIAS[fe.weekday()] if fe.weekday() < 5 else "FinDeSemana"

    if dn == "FinDeSemana":
        st.error("🔒 Es fin de semana. No se pueden realizar reservas.")
        return

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
                    _dt.datetime.now(_dt.timezone.utc).isoformat()
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

def screen_reservar_sala(conn):
    import datetime as _dt

    st.header("Reserva Salas de Reuniones")
    st.info("Selecciona tu equipo/área y luego elige la sala y horario disponible")

    df_dist = read_distribution_df(conn)
    equipos_lista = []
    if not df_dist.empty:
        equipos_lista = sorted([e for e in df_dist["equipo"].unique() if e != "Cupos libres"])

    if not equipos_lista:
        st.warning("⚠️ No hay equipos configurados. Contacta al administrador.")
        return

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

    fe_s = c_fecha.date_input("Fecha", min_value=_dt.date.today(), key="fs_sala")

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
        return

    opciones_inicio = slots_completos[:-1]
    inicio_key = f"inicio_sala_{sl}"
    fin_key = f"fin_sala_{sl}"

    hora_inicio_sel = st.selectbox("Hora de inicio", opciones_inicio, index=0, key=inicio_key)

    pos_inicio = slots_completos.index(hora_inicio_sel)
    opciones_fin = slots_completos[pos_inicio + 1:]

    if not opciones_fin:
        st.warning("Selecciona una hora de inicio anterior a las 20:00 hrs.")
        return

    idx_fin = min(4, len(opciones_fin) - 1)
    hora_fin_sel = st.selectbox("Hora de término", opciones_fin, index=idx_fin, key=fin_key)

    inicio_dt = _dt.datetime.strptime(hora_inicio_sel, "%H:%M")
    fin_dt = _dt.datetime.strptime(hora_fin_sel, "%H:%M")
    duracion_min = int((fin_dt - inicio_dt).total_seconds() / 60)

    if duracion_min < 15:
        st.error("El intervalo debe ser de al menos 15 minutos.")
        return

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
                _dt.datetime.now(_dt.timezone.utc).isoformat(),
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

def screen_mis_reservas(conn):
    st.header("Mis Reservas y Listados")

    ver_tipo = st.selectbox("Ver:", ["Reserva de Puestos", "Reserva de Salas"], key="mis_reservas_ver_tipo")

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
    else:
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

    with st.expander("Anular Reservas", expanded=False):
        correo_buscar = st.text_input(
            "Correo asociado a la(s) reserva(s)",
            key="anular_correo",
            placeholder="usuario@ejemplo.com"
        ).strip()

        buscar = st.button("Buscar", type="primary", key="btn_buscar_reservas")

        st.session_state.setdefault("anular_candidates", [])
        st.session_state.setdefault("anular_checks", {})

        if buscar:
            st.session_state["anular_candidates"] = []
            st.session_state["anular_checks"] = {}

            if not correo_buscar:
                st.error("Ingresa un correo para buscar reservas.")
            elif not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{{2,}}$', correo_buscar):
                st.error("Ingresa un correo válido.")
            else:
                correo_norm = correo_buscar.lower()
                candidates = []

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

def screen_reservas_tabs(conn):
    st.subheader("Reservas")
    tabs = st.tabs(["Reservar Puesto Flex", "Reserva Salas de Reuniones", "Mis Reservas y Listados"])
    with tabs[0]:
        screen_reservar_puesto_flex(conn)
    with tabs[1]:
        screen_reservar_sala(conn)
    with tabs[2]:
        screen_mis_reservas(conn)

# ---------------------------------------------------------
# DESCARGAS: Distribución + Planos (descarga-only)
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
            df_show = apply_sorting_to_df(df)
            st.dataframe(df_show, use_container_width=True, hide_index=True)

            xlsx_bytes = _df_to_xlsx_bytes(df_show, sheet_name="distribucion")
            st.download_button(
                "⬇️ Descargar Distribución (XLSX)",
                data=xlsx_bytes,
                file_name="distribucion.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            try:
                pdf = FPDF()
                pdf.add_page()
                pdf.set_auto_page_break(True, 15)
                pdf.set_font("Arial", "B", 14)
                pdf.cell(0, 10, clean_pdf_text("Distribución"), ln=True, align="C")
                pdf.ln(4)

                pdf.set_font("Arial", "B", 8)
                cols = list(df_show.columns)
                widths = [max(18, min(45, int(190 / max(1, len(cols))))) for _ in cols]
                for w, c in zip(widths, cols):
                    pdf.cell(w, 6, clean_pdf_text(str(c))[:18], border=1)
                pdf.ln()

                pdf.set_font("Arial", "", 8)
                for _, r in df_show.iterrows():
                    for w, c in zip(widths, cols):
                        pdf.cell(w, 6, clean_pdf_text(str(r.get(c, "")))[:18], border=1)
                    pdf.ln()
                    if pdf.get_y() > 265:
                        pdf.add_page()

                pdf_bytes = pdf.output(dest="S").encode("latin-1")
                st.download_button(
                    "⬇️ Descargar Distribución (PDF)",
                    data=pdf_bytes,
                    file_name="distribucion.pdf",
                    mime="application/pdf",
                )
            except Exception as e:
                st.info(f"No se pudo generar PDF aquí (puedes mantener tu generador actual): {e}")

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

            try:
                pdf = FPDF()
                pdf.add_page()
                pdf.set_auto_page_break(True, 15)
                pdf.image(str(img_path), x=10, y=15, w=190)
                pdf_bytes = pdf.output(dest="S").encode("latin-1")
                st.download_button(
                    "⬇️ Descargar plano (PDF)",
                    data=pdf_bytes,
                    file_name=img_path.stem + ".pdf",
                    mime="application/pdf",
                )
            except Exception:
                pass

# ---------------------------------------------------------
# APP
# ---------------------------------------------------------
render_topbar()
render_menu_drawer()
st.divider()

screen = st.session_state.get("screen", "Administrador")

if screen == "Administrador":
    screen_admin(conn)
elif screen == "Reservas":
    screen_reservas_tabs(conn)
elif screen == "Planos":
    screen_descargas_distribucion_planos(conn)
else:
    # fallback seguro
    st.session_state["screen"] = "Administrador"
    screen_admin(conn)
