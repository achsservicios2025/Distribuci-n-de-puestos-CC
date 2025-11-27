import streamlit as st
import pandas as pd
import datetime
import os
import uuid
import json
import re
from pathlib import Path
import matplotlib.pyplot as plt
from fpdf import FPDF
from PIL import Image as PILImage
from PIL import Image
from io import BytesIO
from dataclasses import dataclass
import base64

# ---------------------------------------------------------
# 1. PARCHE (Mantenemos este fix por seguridad)
# ---------------------------------------------------------
import streamlit.elements.lib.image_utils
if hasattr(streamlit.elements.lib.image_utils, "image_to_url"):
    _orig_image_to_url = streamlit.elements.lib.image_utils.image_to_url
    @dataclass
    class WidthConfig:
        width: int
    def _patched_image_to_url(image_data, width=None, clamp=False, channels="RGB", output_format="JPEG", image_id=None):
        if isinstance(width, int):
            width = WidthConfig(width=width)
        return _orig_image_to_url(image_data, width, clamp, channels, output_format, image_id)
    streamlit.elements.lib.image_utils.image_to_url = _patched_image_to_url

# ---------------------------------------------------------
# 2. IMPORTACIONES
# ---------------------------------------------------------
from modules.database import (
    get_conn, init_db, insert_distribution, clear_distribution,
    read_distribution_df, save_setting, get_all_settings,
    add_reservation, user_has_reservation, list_reservations_df,
    add_room_reservation, get_room_reservations_df,
    count_monthly_free_spots, delete_reservation_from_db, 
    delete_room_reservation_from_db, perform_granular_delete,
    ensure_reset_table, save_reset_token, validate_and_consume_token
)
from modules.auth import get_admin_credentials
from modules.layout import admin_appearance_ui, apply_appearance_styles
from modules.seats import compute_distribution_from_excel
from modules.emailer import send_reservation_email
from modules.rooms import generate_time_slots, check_room_conflict
from modules.zones import generate_colored_plan, load_zones, save_zones
from streamlit_drawable_canvas import st_canvas

# ---------------------------------------------------------
# 3. CONFIGURACIÓN
# ---------------------------------------------------------
st.set_page_config(page_title="Gestor de Puestos", layout="wide")

BASE_DIR = Path(__file__).parent.resolve()
PLANOS_DIR = BASE_DIR / "planos"
DATA_DIR = BASE_DIR / "data"
COLORED_DIR = BASE_DIR / "planos_coloreados"

for d in [DATA_DIR, PLANOS_DIR, COLORED_DIR]: d.mkdir(exist_ok=True)

if "gcp_service_account" not in st.secrets:
    st.error("🚨 Faltan secretos.")
    st.stop()

try:
    creds_dict = dict(st.secrets["gcp_service_account"])
    from google.oauth2.service_account import Credentials
    import gspread
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open(st.secrets["sheets"]["sheet_name"])
except Exception as e:
    st.error(f"🔥 Conexión fallida: {str(e)}")
    st.stop()

ORDER_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

# ---------------------------------------------------------
# 4. UTILS
# ---------------------------------------------------------
def clean_pdf_text(text: str) -> str:
    if not isinstance(text, str): return str(text)
    replacements = {"•": "-", "—": "-", "–": "-", "⚠": "ATENCION:", "º": "o"}
    for bad, good in replacements.items(): text = text.replace(bad, good)
    return text.encode('latin-1', 'replace').decode('latin-1')

def sort_floors(floor_list):
    def extract_num(text):
        num = re.findall(r'\d+', str(text))
        return int(num[0]) if num else 0
    return sorted(list(floor_list), key=extract_num)

def apply_sorting_to_df(df):
    if df.empty: return df
    df = df.copy()
    cols = {c.lower(): c for c in df.columns}
    col_dia = cols.get('dia') or cols.get('día')
    col_piso = cols.get('piso')
    if col_dia: df[col_dia] = pd.Categorical(df[col_dia], categories=ORDER_DIAS, ordered=True)
    if col_piso:
        floors = sort_floors(df[col_piso].dropna().unique())
        df[col_piso] = pd.Categorical(df[col_piso], categories=floors, ordered=True)
    sort_cols = [c for c in [col_piso, col_dia] if c]
    if sort_cols: df = df.sort_values(sort_cols)
    return df

def get_distribution_proposal(df_e, df_p, strategy="random"):
    col_sort = next((c for c in df_e.columns if c.lower().strip() == "dotacion"), None)
    if not col_sort: strategy = "random"
    
    if strategy == "random": df_e = df_e.sample(frac=1).reset_index(drop=True)
    elif strategy == "size_desc": df_e = df_e.sort_values(by=col_sort, ascending=False).reset_index(drop=True)
    elif strategy == "size_asc": df_e = df_e.sort_values(by=col_sort, ascending=True).reset_index(drop=True)
    
    return compute_distribution_from_excel(df_e, df_p, 2)

def clean_reservation_df(df, tipo="puesto"):
    if df.empty: return df
    df = df.drop(columns=[c for c in df.columns if c.lower() in ['id','created_at','registro','id.1']], errors='ignore')
    map_p = {'user_name':'Nombre','user_email':'Correo','piso':'Piso','reservation_date':'Fecha Reserva','team_area':'Ubicación'}
    map_s = {'user_name':'Nombre','user_email':'Correo','piso':'Piso','room_name':'Sala','reservation_date':'Fecha','start_time':'Inicio','end_time':'Fin'}
    rename_map = map_p if tipo == "puesto" else map_s
    df = df.rename(columns=rename_map)
    return df[[c for c in rename_map.values() if c in df.columns]]

# --- PDF ---
def create_merged_pdf(piso_sel, conn, logo_path):
    pdf = FPDF(); pdf.set_auto_page_break(True, 15); found = False
    df = read_distribution_df(conn)
    base_config = st.session_state.get('last_style_config', {})
    for dia in ORDER_DIAS:
        subset = df[(df['piso']==piso_sel) & (df['dia']==dia)]
        seats = dict(zip(subset['equipo'], subset['cupos']))
        conf = base_config.copy(); conf["subtitle_text"] = conf.get("subtitle_text", f"Día: {dia}")
        img = generate_colored_plan(piso_sel, dia, seats, "PNG", conf, logo_path)
        if img and Path(img).exists():
            found = True; pdf.add_page()
            try: pdf.image(str(img), x=10, y=10, w=190)
            except: pass
    return pdf.output(dest='S').encode('latin-1') if found else None

def generate_full_pdf(df, logo_path, deficit_data=None):
    pdf = FPDF(); pdf.set_auto_page_break(True, 15); pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    if logo_path.exists():
        try: pdf.image(str(logo_path), x=10, y=8, w=30)
        except: pass
    pdf.ln(25); pdf.cell(0, 10, "Informe de Distribución", ln=True, align='C'); pdf.ln(6)
    
    pdf.set_font("Arial", 'B', 10); pdf.cell(0, 8, "1. Detalle Diario", ln=True)
    pdf.set_font("Arial", 'B', 8); w = [30, 60, 25, 25, 25]; h = ["Piso", "Equipo", "Día", "Cupos", "%"]
    for i, txt in enumerate(h): pdf.cell(w[i], 6, clean_pdf_text(txt), 1)
    pdf.ln(); pdf.set_font("Arial", '', 8)
    
    df = apply_sorting_to_df(df)
    def safe_get(r, k): return str(r.get(k, r.get(k.lower(), '')))
    for _, r in df.iterrows():
        pdf.cell(w[0], 6, clean_pdf_text(safe_get(r, "Piso")), 1)
        pdf.cell(w[1], 6, clean_pdf_text(safe_get(r, "Equipo")[:40]), 1)
        pdf.cell(w[2], 6, clean_pdf_text(safe_get(r, "Día")), 1)
        pdf.cell(w[3], 6, clean_pdf_text(safe_get(r, "Cupos")), 1)
        pdf.cell(w[4], 6, clean_pdf_text(f"{safe_get(r, '%Distrib')}%"), 1); pdf.ln()

    pdf.add_page(); pdf.set_font("Arial", 'B', 11); pdf.cell(0, 10, "2. Resumen Semanal", ln=True)
    try:
        df_calc = df.copy()
        df_calc.columns = [c.lower().strip() for c in df_calc.columns]
        col_pct = next((c for c in df_calc.columns if 'distrib' in c or 'pct' in c), None)
        col_eq = next((c for c in df_calc.columns if 'equipo' in c), None)

        if col_pct and col_eq:
            df_calc[col_pct] = pd.to_numeric(df_calc[col_pct], errors='coerce').fillna(0)
            ws = df_calc.groupby(col_eq)[col_pct].mean().reset_index()
            ws.columns = ["Equipo", "Promedio"]; ws = ws.sort_values("Equipo")
            pdf.set_font("Arial", 'B', 9); w_wk = [100, 40]; start_x = 35; pdf.set_x(start_x)
            pdf.cell(w_wk[0], 6, "Equipo", 1); pdf.cell(w_wk[1], 6, "Promedio Semanal", 1); pdf.ln()
            pdf.set_font("Arial", '', 9)
            for _, row in ws.iterrows():
                pdf.set_x(start_x); pdf.cell(w_wk[0], 6, clean_pdf_text(str(row["Equipo"])[:50]), 1)
                pdf.cell(w_wk[1], 6, clean_pdf_text(f"{row['Promedio']:.1f}%"), 1); pdf.ln()
    except Exception: pass

    if deficit_data:
        pdf.add_page(); pdf.set_font("Arial", 'B', 12); pdf.cell(0, 10, "Reporte de Déficit", ln=True)
        pdf.set_font("Arial", 'B', 8); w2 = [20, 50, 20, 20, 70]
        for _, txt in enumerate(["Piso", "Equipo", "Día", "Faltan", "Causa"]): pdf.cell(w2[_], 6, txt, 1)
        pdf.ln(); pdf.set_font("Arial", '', 8)
        for d in deficit_data:
            pdf.cell(w2[0], 6, clean_pdf_text(d.get('piso','')), 1)
            pdf.cell(w2[1], 6, clean_pdf_text(d.get('equipo','')), 1)
            pdf.cell(w2[2], 6, clean_pdf_text(d.get('dia','')), 1)
            pdf.cell(w2[3], 6, str(d.get('deficit','')), 1)
            pdf.cell(w2[4], 6, clean_pdf_text(d.get('causa','')), 1); pdf.ln()
    return pdf.output(dest='S').encode('latin-1')

# --- MODALES ---
@st.dialog("Confirmar")
def confirm_delete_dialog(conn, u, f, a, p):
    st.warning(f"¿Anular {u}?"); c1,c2=st.columns(2)
    if c1.button("Sí", key="y"): delete_reservation_from_db(conn, u, f, a); st.rerun()
    if c2.button("No", key="n"): st.rerun()

@st.dialog("Confirmar Sala")
def confirm_delete_room_dialog(conn, u, f, s, i):
    st.warning(f"¿Anular {u}?"); c1,c2=st.columns(2)
    if c1.button("Sí", key="ys"): delete_room_reservation_from_db(conn, u, f, s, i); st.rerun()
    if c2.button("No", key="ns"): st.rerun()

def generate_token(): return uuid.uuid4().hex[:8].upper()

# ---------------------------------------------------------
# INICIO APP
# ---------------------------------------------------------
conn = get_conn()
if "db_initialized" not in st.session_state:
    with st.spinner('Cargando...'): init_db(conn)
    st.session_state["db_initialized"] = True

apply_appearance_styles(conn)
if "app_settings" not in st.session_state: st.session_state["app_settings"] = get_all_settings(conn)
settings = st.session_state["app_settings"]
global_logo_path = settings.get("logo_path", "static/logo.png")

if os.path.exists(global_logo_path):
    c1, c2 = st.columns([1, 5]); c1.image(global_logo_path, width=150); c2.title(settings.get("site_title", "Gestor"))
else: st.title(settings.get("site_title", "Gestor"))

menu = st.sidebar.selectbox("Menú", ["Vista pública", "Reservas", "Administrador"])

# ==========================================
# VISTA PÚBLICA
# ==========================================
if menu == "Vista pública":
    st.header("Cupos y Planos")
    df = read_distribution_df(conn)
    if df.empty: st.info("Sin datos.")
    else:
        df_view = apply_sorting_to_df(df.drop(columns=['id','created_at'], errors='ignore'))
        pisos = sort_floors(df["piso"].unique())
        t1, t2 = st.tabs(["Estadísticas", "Ver Planos"])
        with t1: st.dataframe(df_view, hide_index=True, use_container_width=True)
        with t2:
            c1, c2 = st.columns(2)
            pi = c1.selectbox("Piso", pisos)
            ds = c2.selectbox("Día", ["Todos"]+ORDER_DIAS)
            if ds == "Todos":
                if st.button("PDF Semanal"):
                    m = create_merged_pdf(pi, conn, global_logo_path)
                    if m: st.download_button("Descargar", m, "semana.pdf")
            else:
                subset = df[(df['piso']==pi) & (df['dia']==ds)]
                seats = dict(zip(subset['equipo'], subset['cupos']))
                conf = st.session_state.get('last_style_config', {})
                generate_colored_plan(pi, ds, seats, "PNG", conf, global_logo_path)
                f_prev = COLORED_DIR / f"piso_{pi.replace('Piso ','').strip()}_{ds.lower().replace('é','e').replace('á','a')}_combined.png"
                if f_prev.exists(): st.image(str(f_prev))
                else: st.warning("No generado.")

# ==========================================
# RESERVAS
# ==========================================
elif menu == "Reservas":
    st.header("Reservas")
    op = st.selectbox("Opción", ["Puesto Flex", "Sala", "Mis Reservas"])
    st.divider()
    
    if op == "Puesto Flex":
        df = read_distribution_df(conn)
        if not df.empty:
            c1, c2 = st.columns(2)
            fe = c1.date_input("Fecha", min_value=datetime.date.today())
            pi = c2.selectbox("Piso", sort_floors(df["piso"].unique()))
            dn = ORDER_DIAS[fe.weekday()] if fe.weekday() < 5 else "Fin"
            if dn != "Fin":
                rg = df[(df["piso"]==pi) & (df["dia"]==dn) & (df["equipo"]=="Cupos libres")]
                if not rg.empty:
                    tot = int(rg.iloc[0]["cupos"])
                    occ = len(list_reservations_df(conn)[lambda x: (x["reservation_date"].astype(str)==str(fe)) & (x["piso"]==pi) & (x["team_area"]=="Cupos libres")])
                    disp = tot - occ
                    st.metric("Disponibles", f"{disp}/{tot}")
                    with st.form("rp"):
                        n = st.text_input("Nombre"); e = st.text_input("Email")
                        if st.form_submit_button("Reservar", disabled=(disp<=0)):
                            if user_has_reservation(conn, e, str(fe)): st.error("Ya tienes reserva.")
                            elif count_monthly_free_spots(conn, e, fe) >= 2: st.error("Límite mensual alcanzado.")
                            else: 
                                add_reservation(conn, n, e, pi, str(fe), "Cupos libres", datetime.datetime.now().isoformat())
                                send_reservation_email(e, "Confirmado", f"Reserva: {fe} {pi}")
                                st.success("Listo!"); st.rerun()
                else: st.warning("Sin cupos libres.")
            else: st.error("Fin de semana.")

    elif op == "Sala":
        sl = st.selectbox("Sala", ["Sala Reuniones Pequeña Piso 1", "Sala Reuniones Grande Piso 1", "Sala Reuniones Piso 2", "Sala Reuniones Piso 3"])
        fe = st.date_input("Fecha", min_value=datetime.date.today())
        tm = generate_time_slots("08:00", "20:00", 15)
        c1, c2 = st.columns(2); i = c1.selectbox("Inicio", tm); f = c2.selectbox("Fin", tm, index=4)
        with st.form("rs"):
            n = st.text_input("Nombre"); e = st.text_input("Email")
            if st.form_submit_button("Reservar"):
                if check_room_conflict(get_room_reservations_df(conn).to_dict("records"), str(fe), sl, i, f): st.error("Ocupado.")
                else:
                    piso_det = "1"
                    if "Piso 2" in sl: piso_det = "Piso 2"
                    elif "Piso 3" in sl: piso_det = "Piso 3"
                    else: piso_det = "Piso 1"
                    add_room_reservation(conn, n, e, piso_det, sl, str(fe), i, f, datetime.datetime.now().isoformat())
                    st.success("Reservado.")

    elif op == "Mis Reservas":
        q = st.text_input("Tu Email")
        if q:
            dp = clean_reservation_df(list_reservations_df(conn), "puesto")
            if not dp.empty:
                mp = dp[dp['Correo'].str.contains(q, case=False)]
                for _, r in mp.iterrows():
                    c1, c2 = st.columns([4,1])
                    c1.write(f"📅 {r['Fecha Reserva']} - {r['Piso']}")
                    if c2.button("X", key=f"dp_{_}"): confirm_delete_dialog(conn, r['Nombre'], r['Fecha Reserva'], r['Ubicación'], r['Piso'])

# ==========================================
# ADMINISTRADOR
# ==========================================
elif menu == "Administrador":
    st.header("Admin")
    au, ap = get_admin_credentials(conn)
    if "is_admin" not in st.session_state: st.session_state["is_admin"] = False
    if not st.session_state["is_admin"]:
        u = st.text_input("User"); p = st.text_input("Pass", type="password")
        if st.button("Login"): 
            if u==au and p==ap: st.session_state["is_admin"] = True; st.rerun()
            else: st.error("Error")
        st.stop()
    if st.button("Logout"): st.session_state["is_admin"] = False; st.rerun()
    
    t1, t2, t3, t4, t5, t6 = st.tabs(["Excel", "Editor Visual", "Reportes", "Config", "Estilos", "Mantenimiento"])
    
    with t1:
        up = st.file_uploader("Cargar Excel", type=["xlsx"])
        if up and st.button("Procesar"):
            eq = pd.read_excel(up, "Equipos"); pa = pd.read_excel(up, "Parámetros")
            r, d = get_distribution_proposal(eq, pa)
            st.session_state['pr'] = r; st.session_state['pd'] = d
            st.rerun()
        if 'pr' in st.session_state:
            st.dataframe(st.session_state['pr'], use_container_width=True)
            if st.button("Guardar"): 
                clear_distribution(conn); insert_distribution(conn, st.session_state['pr'])
                st.success("Guardado.")

    # --- T2: EDITOR VISUAL ARREGLADO ---
    with t2:
        st.info("Editor de Zonas")
        zonas = load_zones()
        c1, c2 = st.columns(2)
        df_d = read_distribution_df(conn)
        pisos = sort_floors(df_d["piso"].unique()) if not df_d.empty else ["Piso 1"]
        p_sel = c1.selectbox("Piso", pisos)
        d_sel = c2.selectbox("Día", ORDER_DIAS)
        
        # Búsqueda de imagen
        p_num = p_sel.replace("Piso ", "").strip()
        pim = None
        for f in [f"piso{p_num}.png", f"piso{p_num}.jpg", f"Piso{p_num}.png", f"Piso {p_num}.png"]:
            if (PLANOS_DIR / f).exists(): pim = PLANOS_DIR / f; break
        
        if not pim:
            st.error(f"❌ No imagen para {p_sel}")
        else:
            try:
                # CARGA Y REDIMENSIONAMIENTO MANUAL (Más seguro para Canvas)
                img = PILImage.open(pim).convert("RGB")
                canvas_width = 800
                w_orig, h_orig = img.size
                canvas_height = int(h_orig * (canvas_width / w_orig))
                
                # Redimensionar nosotros antes de pasar al canvas
                img_resized = img.resize((canvas_width, canvas_height))
                
                # Pasamos la imagen ya lista (Objeto PIL)
                canvas = st_canvas(
                    fill_color="rgba(0, 160, 74, 0.3)",
                    stroke_width=2, stroke_color="#00A04A",
                    background_image=img_resized,
                    update_streamlit=True,
                    width=canvas_width, 
                    height=canvas_height,
                    drawing_mode="rect",
                    key=f"cv_{p_sel}_final"
                )
                
                seats = dict(zip(df_d[(df_d['piso']==p_sel) & (df_d['dia']==d_sel)]['equipo'], df_d[(df_d['piso']==p_sel) & (df_d['dia']==d_sel)]['cupos'])) if not df_d.empty else {}
                eqs = sorted(seats.keys()) + ["Sala"]
                tn = st.selectbox("Asignar:", eqs)
                
                if st.button("Guardar Zona"):
                    if canvas.json_data and canvas.json_data.get("objects"):
                        o = canvas.json_data["objects"][-1]
                        zonas.setdefault(p_sel, []).append({
                            "team": tn, "x": int(o["left"]), "y": int(o["top"]), 
                            "w": int(o["width"] * o["scaleX"]), "h": int(o["height"] * o["scaleY"]), "color": "#00A04A"
                        })
                        save_zones(zonas); st.success("Guardado"); st.rerun()
                
                if p_sel in zonas:
                    for i, z in enumerate(zonas[p_sel]):
                        c1, c2 = st.columns([4, 1])
                        c1.markdown(f"<span style='color:{z['color']}'>■</span> {z['team']}", unsafe_allow_html=True)
                        if c2.button("X", key=f"del_{i}"): zonas[p_sel].pop(i); save_zones(zonas); st.rerun()
                        
                if st.button("Vista Previa"):
                    conf = {"title_text": f"{p_sel} - {d_sel}"}
                    st.session_state['last_style_config'] = conf
                    generate_colored_plan(p_sel, d_sel, seats, "PNG", conf, global_logo_path)
                    st.success("Generado")
                    
                dsf = d_sel.lower().replace("é","e").replace("á","a")
                prev = COLORED_DIR / f"piso_{p_num}_{dsf}_combined.png"
                if prev.exists(): st.image(str(prev), width=500)
            except Exception as e: st.error(f"Error imagen: {e}")

    with t3:
        if st.button("Excel"):
            df = read_distribution_df(conn)
            b = BytesIO(); with pd.ExcelWriter(b) as w: df.to_excel(w, index=False)
            st.download_button("Descargar", b.getvalue(), "d.xlsx")
        if st.button("PDF Completo"):
            df_raw = read_distribution_df(conn)
            d_data = st.session_state.get('deficit_report', [])
            pdf_bytes = generate_full_pdf(df_raw, df_raw, logo_path=Path(global_logo_path), deficit_data=d_data)
            st.download_button("Descargar PDF", pdf_bytes, "reporte.pdf", "application/pdf")

    with t4:
        n_u = st.text_input("User"); n_p = st.text_input("Pass", type="password")
        if st.button("Update"): save_setting(conn, "admin_user", n_u); save_setting(conn, "admin_pass", n_p); st.success("OK")

    with t5: admin_appearance_ui(conn)
    with t6:
        opt = st.radio("Borrar:", ["Reservas", "Distribución", "Planos/Zonas", "TODO"])
        if st.button("EJECUTAR"): msg = perform_granular_delete(conn, opt); st.success(msg)
