from __future__ import annotations

from pathlib import Path
import streamlit as st

from modules.database import save_setting, get_all_settings

STATIC_DIR = Path("static")
STATIC_DIR.mkdir(exist_ok=True)

FONTS = ["Poppins", "Montserrat", "Roboto", "Inter", "Lato"]


def admin_appearance_ui(conn):
    st.subheader("Apariencia y branding")
    settings = get_all_settings(conn) or {}

    col1, col2 = st.columns(2)

    with col1:
        primary = st.color_picker("Color primario", value=settings.get("primary", "#00A04A"))
        accent = st.color_picker("Color acento", value=settings.get("accent", "#006B32"))
        bg = st.color_picker("Color fondo", value=settings.get("bg", "#ffffff"))

    with col2:
        text = st.color_picker("Color texto", value=settings.get("text", "#111111"))

        current_font = settings.get("font", "Poppins")
        try:
            font_index = FONTS.index(current_font)
        except ValueError:
            font_index = 0

        font = st.selectbox("Fuente", FONTS, index=font_index)
        site_title = st.text_input(
            "Título del sitio",
            value=settings.get("site_title", "Gestor de Puestos y Salas — ACHS Servicios"),
        )

    logo = st.file_uploader("Subir logo (opcional)", type=["png", "jpg", "jpeg"])

    c1, c2 = st.columns([1, 3])
    with c1:
        save = st.button("Guardar apariencia", type="primary")
    with c2:
        st.caption("Tip: después de guardar, recarga la página si tu app cachea estilos.")

    if save:
        save_setting(conn, "primary", primary)
        save_setting(conn, "accent", accent)
        save_setting(conn, "bg", bg)
        save_setting(conn, "text", text)
        save_setting(conn, "font", font)
        save_setting(conn, "site_title", site_title)

        if logo is not None:
            # Guardamos siempre como png (aunque suban jpg), para simplificar consumo
            logo_path = STATIC_DIR / "logo.png"
            with open(logo_path, "wb") as f:
                f.write(logo.getbuffer())
            save_setting(conn, "logo_path", str(logo_path))

        st.success("Apariencia guardada.")


def apply_appearance_styles(conn):
    settings = get_all_settings(conn) or {}

    font = settings.get("font", "Poppins")
    primary = settings.get("primary", "#00A04A")
    accent = settings.get("accent", "#006B32")
    bg = settings.get("bg", "#ffffff")
    text = settings.get("text", "#111111")

    # Google Fonts (pedimos varios weights para títulos/énfasis)
    font_q = font.replace(" ", "+")
    google_font_import = f"@import url('https://fonts.googleapis.com/css2?family={font_q}:wght@300;400;500;600;700&display=swap');"

    css = f"""
    <style>
    {google_font_import}

    :root {{
        --primary: {primary};
        --accent: {accent};
        --bg: {bg};
        --text: {text};
        --radius: 12px;
    }}

    html, body, [class*="css"] {{
        font-family: '{font}', system-ui, -apple-system, Segoe UI, Roboto, sans-serif !important;
        color: var(--text);
    }}

    /* Fondo general */
    .stApp {{
        background: var(--bg);
    }}

    /* Sidebar */
    section[data-testid="stSidebar"] {{
        background: color-mix(in srgb, var(--bg) 92%, #000000 8%);
    }}

    /* Botones (Streamlit cambia clases seguido, usamos data-testid) */
    button[data-testid="baseButton-primary"] {{
        background: var(--primary) !important;
        border: 1px solid color-mix(in srgb, var(--primary) 70%, #000 30%) !important;
        border-radius: var(--radius) !important;
        color: #fff !important;
    }}
    button[data-testid="baseButton-secondary"],
    button[data-testid="baseButton-minimal"] {{
        border-radius: var(--radius) !important;
    }}

    /* Inputs / selects */
    div[data-baseweb="select"] > div,
    div[data-baseweb="input"] > div,
    div[data-baseweb="textarea"] > div {{
        border-radius: var(--radius) !important;
    }}

    /* Resaltar enfoque */
    div[data-baseweb="input"] input:focus,
    div[data-baseweb="textarea"] textarea:focus {{
        outline: none !important;
        box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary) 45%, transparent) !important;
    }}

    /* Links y acentos */
    a {{
        color: var(--accent);
    }}

    /* Chips/badges típicos */
    [data-testid="stMetric"] {{
        border-radius: var(--radius);
    }}
    </style>
    """

    st.markdown(css, unsafe_allow_html=True)

    """
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


