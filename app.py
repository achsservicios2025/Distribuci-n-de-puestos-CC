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
#   - tamaños x2 aprox
# ---------------------------------------------------------
st.session_state.setdefault("ui", {
    "app_title": "Gestor de Puestos y Salas",
    "bg_color": "#ffffff",
    "logo_path": "assets/logo.png",
    "title_font_size": 64,   # x2
    "logo_width": 420,       # x2
})

st.session_state.setdefault("menu_open", False)
st.session_state.setdefault("screen", "Administrador")
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
# (si en admin guardas tamaño título, se respeta, pero lo escalamos si viene chico)
try:
    ts = int(settings.get("title_font_size", st.session_state["ui"]["title_font_size"]))
    st.session_state["ui"]["title_font_size"] = max(40, ts * 2)  # x2, con mínimo razonable
except Exception:
    pass

# ---------------------------------------------------------
# 5) CSS
#   - márgenes 5cm a ambos lados (igual)
#   - subir tamaño global de tipografías y widgets (x2 aprox)
#   - botones más grandes
# ---------------------------------------------------------
st.markdown(f"""
<style>
.stApp {{
  background: {st.session_state.ui["bg_color"]};
}}

/* oculta header streamlit */
header {{
  visibility: hidden;
  height: 0px;
}}

/* quitar padding top que se ve como bloque blanco */
div[data-testid="stAppViewContainer"] > .main {{
  padding-top: 0rem !important;
}}
section.main > div {{
  padding-top: 0rem !important;
}}

/* márgenes 5cm exactos a ambos lados */
.block-container {{
  max-width: 100% !important;
  padding-top: 0.75rem !important;
  padding-left: 5cm !important;
  padding-right: 5cm !important;
}}

/* escala global (labels, inputs, captions) */
html, body, [class*="css"] {{
  font-size: 20px !important;
}}

/* títulos, headers */
h1 {{ font-size: 48px !important; }}
h2 {{ font-size: 40px !important; }}
h3 {{ font-size: 32px !important; }}
p, li, label, span {{ font-size: 20px !important; }}

/* inputs y selects */
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

/* botones grandes */
.stButton button {{
  font-size: 20px !important;
  font-weight: 900 !important;
  padding: 12px 18px !important;
  border-radius: 16px !important;
}}

/* Topbar */
.mk-topbar {{
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 18px;
  margin-top: 8px;
  margin-bottom: 6px;
}}
.mk-title {{
  text-align: center;
  font-weight: 900;
  margin: 0;
  line-height: 1.05;
}}
.mk-right {{
  display:flex;
  justify-content:flex-end;
  align-items:center;
  gap:12px;
}}
.mk-toggle button {{
  padding: 10px 16px !important;
}}
.mk-drawer {{
  background: #f3f5f7;
  border-radius: 18px;
  padding: 14px;
  box-shadow: 0 10px 24px rgba(0,0,0,0.12);
  margin-top: 10px;
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
# TOPBAR (logo izq, título centro, botón << der)
# ---------------------------------------------------------
def render_topbar():
    logo_path = Path(st.session_state.ui["logo_path"])
    size = int(st.session_state.ui.get("title_font_size", 64))
    title = st.session_state.ui.get("app_title", "Gestor de Puestos y Salas")

    st.markdown("<div class='mk-topbar'>", unsafe_allow_html=True)

    # col 1: logo
    with st.container():
        if logo_path.exists():
            st.image(str(logo_path), width=int(st.session_state.ui.get("logo_width", 420)))
        else:
            st.write("🧩 (Logo aquí)")

    # col 2: title
    st.markdown(f"<div class='mk-title' style='font-size:{size}px;'>{title}</div>", unsafe_allow_html=True)

    # col 3: toggle
    st.markdown("<div class='mk-right mk-toggle'>", unsafe_allow_html=True)
    if st.button("<<" if not st.session_state["menu_open"] else ">>", key="btn_menu_toggle"):
        toggle_menu()
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------
# Drawer: en vez de botones -> "listado" tipo selectbox
# ---------------------------------------------------------
def render_menu_drawer():
    if not st.session_state["menu_open"]:
        return

    _, _, c = st.columns([2.4, 2.4, 1.2])
    with c:
        st.markdown("<div class='mk-drawer'>", unsafe_allow_html=True)

        choice = st.selectbox(
            "Navegación",
            ["— Selecciona —", "Reservas", "Ver Distribución y Planos"],
            index=0,
            key="drawer_select",
            label_visibility="collapsed",
        )

        if choice == "Reservas":
            go("Reservas")
            st.session_state["menu_open"] = False
            st.session_state["drawer_select"] = "— Selecciona —"
            st.rerun()
        elif choice == "Ver Distribución y Planos":
            go("Planos")
            st.session_state["menu_open"] = False
            st.session_state["drawer_select"] = "— Selecciona —"
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------
# ADMIN (principal)
#   - Acceder alineado en la MISMA fila que "Olvidaste tu contraseña"
# ---------------------------------------------------------
def screen_admin(conn):
    st.subheader("Administrador")
    st.session_state.setdefault("forgot_mode", False)

    if not st.session_state["forgot_mode"]:
        email = st.text_input("Ingresar correo", key="admin_login_email")
        password = st.text_input("Contraseña", type="password", key="admin_login_pass")

        # misma línea: Olvidaste... (izq) | Acceder (der)
        c1, c2, c3 = st.columns([2.2, 2.2, 1.0], vertical_alignment="center")
        with c1:
            if st.button("Olvidaste tu contraseña", key="btn_admin_forgot"):
                st.session_state["forgot_mode"] = True
                st.rerun()

        with c3:
            if st.button("Acceder", type="primary", key="btn_admin_login"):
                e = st.session_state.get("admin_login_email", "").strip()
                p = st.session_state.get("admin_login_pass", "")
                if not e or not p:
                    st.warning("Completa correo y contraseña.")
                else:
                    st.success("Login recibido (validación real pendiente).")

    else:
        reset_email = st.text_input("Correo de acceso", key="admin_reset_email")
        st.caption("Ingresa el código recibido en tu correo.")
        code = st.text_input("Código", key="admin_reset_code")

        c1, c2, c3 = st.columns([2.2, 2.2, 1.0], vertical_alignment="center")
        with c1:
            if st.button("Volver a Acceso", key="btn_admin_back"):
                st.session_state["forgot_mode"] = False
                st.rerun()

        with c3:
            if st.button("Enviar código", type="primary", key="btn_admin_send_code"):
                e = st.session_state.get("admin_reset_email", "").strip()
                if not e:
                    st.warning("Ingresa tu correo.")
                else:
                    st.success("Código enviado (simulado).")

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("Validar código", type="primary", key="btn_admin_validate"):
            c = st.session_state.get("admin_reset_code", "").strip()
            if not c:
                st.warning("Ingresa el código.")
            else:
                st.success("Código validado (simulado).")

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
            elif not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$', em):
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
        elif not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$', e_s):
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
                pdf.set_font("Arial", "B", 18)
                pdf.cell(0, 12, clean_pdf_text("Distribución"), ln=True, align="C")
                pdf.ln(4)

                pdf.set_font("Arial", "B", 11)
                cols = list(df_show.columns)
                widths = [max(22, min(55, int(190 / max(1, len(cols))))) for _ in cols]
                for w, c in zip(widths, cols):
                    pdf.cell(w, 8, clean_pdf_text(str(c))[:20], border=1)
                pdf.ln()

                pdf.set_font("Arial", "", 11)
                for _, r in df_show.iterrows():
                    for w, c in zip(widths, cols):
                        pdf.cell(w, 8, clean_pdf_text(str(r.get(c, "")))[:20], border=1)
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
                st.info(f"No se pudo generar PDF aquí: {e}")

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

def screen_reservas_tabs(conn):
    st.subheader("Reservas")
    tabs = st.tabs(["Reservar Puesto Flex", "Reserva Salas de Reuniones", "Mis Reservas y Listados"])
    with tabs[0]:
        screen_reservar_puesto_flex(conn)
    with tabs[1]:
        screen_reservar_sala(conn)
    with tabs[2]:
        st.info("Aquí va tu pantalla 'Mis Reservas y Listados' si la quieres pegar completa.")
        # Si ya la tienes completa en tu app original, pégala aquí.

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
    st.session_state["screen"] = "Administrador"
    screen_admin(conn)
