# pdfgen.py
from fpdf import FPDF
from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd
import tempfile
import os

from modules.zones import load_zones, generate_colored_plan, PLANOS_DIR, COLORED_DIR

STATIC_DIR = Path("static")
PLANOS_DIR = Path("planos")
COLORED_DIR = Path("planos_coloreados")


def _save_plot_series(series, filename, kind="barh"):
    plt.figure(figsize=(8, 4))
    ax = series.plot(kind=kind)
    plt.tight_layout()
    tmp = Path(tempfile.gettempdir()) / filename
    plt.savefig(tmp)
    plt.close()
    return tmp


def _require_cols(df: pd.DataFrame, cols):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas en df para PDF: {missing}. Columnas disponibles: {list(df.columns)}")


def generate_pdf_from_df(df, out_path="distribucion_final.pdf", logo_path=STATIC_DIR / "logo.png"):
    """
    PDF alineado con seats.py:
    - Reemplaza 'pct' por '% uso semanal'
    - Tabla: Piso, Equipo, Día, Cupos, % uso semanal
    - Gráfico 1: % uso semanal por equipo (único por equipo)
    - Gráfico 2: cupos por equipo x día (apilado)
    """
    if df is None or df.empty:
        raise ValueError("df vacío: no hay datos para generar el PDF.")

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    _require_cols(df, ["piso", "equipo", "dia", "cupos", "% uso semanal"])

    df["cupos"] = pd.to_numeric(df["cupos"], errors="coerce").fillna(0).astype(int)
    df["% uso semanal"] = pd.to_numeric(df["% uso semanal"], errors="coerce")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # ---------------------------------------------------------
    # Portada
    # ---------------------------------------------------------
    pdf.add_page()
    if Path(logo_path).exists():
        try:
            pdf.image(str(logo_path), x=10, y=8, w=30)
        except Exception:
            pass

    pdf.ln(25)
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Distribución de puestos Casa Central", ln=True, align="C")
    pdf.ln(6)
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(
        0,
        6,
        "Este informe presenta la distribución diaria de puestos por equipo y piso.\n"
        "Columna '% uso semanal' (por equipo) = (cupos_totales_equipo_en_semana / (dotación_equipo * 5)) * 100.\n"
        "Nota: 'Cupos libres' corresponde a la reserva diaria y no tiene % uso semanal.",
    )
    pdf.ln(6)

    # ---------------------------------------------------------
    # Tabla resumida (alineada a seats.py)
    # ---------------------------------------------------------
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Distribución diaria (resumen)", ln=True)
    pdf.set_font("Arial", "", 9)

    headers = ["Piso", "Equipo", "Día", "Cupos", "% uso semanal"]
    widths = [18, 72, 22, 18, 28]  # total ~158, cómodo con márgenes
    for i, h in enumerate(headers):
        pdf.cell(widths[i], 7, h, 1, 0, "C")
    pdf.ln()

    # Orden estable para lectura
    day_order = {"Lunes": 0, "Martes": 1, "Miércoles": 2, "Jueves": 3, "Viernes": 4}
    df_table = df.copy()
    df_table["_day_ord"] = df_table["dia"].map(day_order).fillna(99).astype(int)
    df_table = df_table.sort_values(["piso", "equipo", "_day_ord"]).drop(columns=["_day_ord"])

    for _, row in df_table.iterrows():
        piso = str(row["piso"])
        equipo = str(row["equipo"])[:45]
        dia = str(row["dia"])
        cupos = str(int(row["cupos"]))
        uso = row["% uso semanal"]
        uso_str = "" if pd.isna(uso) else f"{float(uso):.2f}%"

        pdf.cell(widths[0], 6, piso, 1)
        pdf.cell(widths[1], 6, equipo, 1)
        pdf.cell(widths[2], 6, dia, 1)
        pdf.cell(widths[3], 6, cupos, 1, 0, "R")
        pdf.cell(widths[4], 6, uso_str, 1, 0, "R")
        pdf.ln()

    # ---------------------------------------------------------
    # Gráfico: % uso semanal por equipo (horizontal)
    # (único por equipo; ignora "Cupos libres")
    # ---------------------------------------------------------
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "% uso semanal por equipo", ln=True)

    df_usage = df.copy()
    df_usage = df_usage[df_usage["equipo"].str.lower() != "cupos libres"]
    df_team_usage = (
        df_usage.dropna(subset=["% uso semanal"])
        .groupby("equipo")["% uso semanal"]
        .first()
        .sort_values(ascending=True)
    )

    if not df_team_usage.empty:
        plot = _save_plot_series(df_team_usage, "plot_team_usage.png", kind="barh")
        pdf.image(str(plot), x=15, w=180)
        try:
            os.remove(plot)
        except Exception:
            pass
    else:
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, "No hay datos de '% uso semanal' para graficar (¿dotación=0 o datos incompletos?).")

    # ---------------------------------------------------------
    # Gráfico: cupos por equipo (stacked) por día
    # ---------------------------------------------------------
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Cupos asignados por equipo (por día)", ln=True)

    df_week = df.copy()
    df_week = df_week[df_week["equipo"].str.lower() != "cupos libres"]
    df_week = df_week.groupby(["equipo", "dia"])["cupos"].sum().unstack(fill_value=0)

    # ordenar columnas días si existen
    cols_sorted = [d for d in ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"] if d in df_week.columns]
    if cols_sorted:
        df_week = df_week[cols_sorted]

    if not df_week.empty:
        plot2 = _save_plot_series(df_week, "plot_week_stacked.png", kind="barh")
        pdf.image(str(plot2), x=15, w=180)
        try:
            os.remove(plot2)
        except Exception:
            pass
    else:
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, "No hay datos de cupos por equipo/día para graficar.")

    # ---------------------------------------------------------
    # Planos coloreados (si existen zonas)
    # ---------------------------------------------------------
    zones = load_zones()
    pisos = sorted(df["piso"].unique())

    for piso in pisos:
        colored = generate_colored_plan(piso)
        if colored:
            pdf.add_page()
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 8, f"Plano {piso}", ln=True)

            # mostrar plano coloreado
            try:
                pdf.image(str(colored), x=10, y=25, w=190)
            except Exception:
                # fallback: insertar original si coloreado falla
                try:
                    piso_num = str(piso).replace("Piso ", "").strip()
                    orig = PLANOS_DIR / f"piso {piso_num}.png"
                    if orig.exists():
                        pdf.image(str(orig), x=10, y=25, w=190)
                except Exception:
                    pass

            # leyenda: listar zonas
            piso_zs = zones.get(piso, [])
            if piso_zs:
                pdf.ln(95)
                pdf.set_font("Arial", "", 10)
                pdf.cell(0, 6, "Leyenda:", ln=True)
                for z in piso_zs:
                    team = z.get("team", "")
                    color = z.get("color", "#00A04A")
                    pdf.cell(0, 5, f" - {team}  ({color})", ln=True)

    pdf.output(out_path)
    return out_path
