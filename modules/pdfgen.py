# pdfgen.py
from fpdf import FPDF
from pathlib import Path
from datetime import datetime
import math
import pandas as pd

STATIC_DIR = Path("static")


# --------------------------
# Helpers de formateo
# --------------------------
def _safe_float(x, default=0.0) -> float:
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return default
        return float(x)
    except Exception:
        return default


def _safe_int(x, default=0) -> int:
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return default
        return int(float(x))
    except Exception:
        return default


def _fmt_pct(x, decimals=1) -> str:
    v = _safe_float(x, 0.0)
    fmt = f"{{:.{decimals}f}}%"
    return fmt.format(v)


def _fmt_date(dt: datetime) -> str:
    # formato “bonito” para Chile / español
    return dt.strftime("%d-%m-%Y %H:%M")


# --------------------------
# PDF class con Header/Footer
# --------------------------
class ReportPDF(FPDF):
    def __init__(self, emitted_at: datetime, logo_path: Path | None = None):
        super().__init__()
        self.emitted_at = emitted_at
        self.logo_path = logo_path
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        # Logo arriba-izquierda
        if self.logo_path and self.logo_path.exists():
            try:
                self.image(str(self.logo_path), x=10, y=8, w=18)
            except Exception:
                pass

        # Fecha emisión arriba-derecha
        self.set_font("Arial", "", 9)
        self.set_xy(0, 10)
        self.cell(0, 5, f"Fecha de emisión: {_fmt_date(self.emitted_at)}", border=0, ln=0, align="R")
        self.ln(12)

    def footer(self):
        # Página “X de Y” centrado abajo
        self.set_y(-12)
        self.set_font("Arial", "", 9)
        self.cell(0, 8, f"{self.page_no()} de {{nb}}", 0, 0, "C")


# --------------------------
# Tablas genéricas
# --------------------------
def _render_table(pdf: FPDF, df: pd.DataFrame, headers: list[str], widths: list[int], aligns: list[str],
                  row_h: int = 6, header_h: int = 7, font_size: int = 9):
    pdf.set_font("Arial", "B", font_size)
    for i, h in enumerate(headers):
        pdf.cell(widths[i], header_h, h, 1, 0, "C")
    pdf.ln()

    pdf.set_font("Arial", "", font_size)

    for _, r in df.iterrows():
        # salto de página si queda poco espacio
        if pdf.get_y() > (pdf.h - pdf.b_margin - 20):
            pdf.add_page()
            pdf.set_font("Arial", "B", font_size)
            for i, h in enumerate(headers):
                pdf.cell(widths[i], header_h, h, 1, 0, "C")
            pdf.ln()
            pdf.set_font("Arial", "", font_size)

        for i, col in enumerate(headers):
            val = r.get(col, "")
            s = "" if val is None else str(val)
            # truncar texto largo
            if i == 1 and len(s) > 45:  # equipo
                s = s[:45] + "…"
            pdf.cell(widths[i], row_h, s, 1, 0, aligns[i])
        pdf.ln()


def _render_glossary(pdf: FPDF, title: str, lines: list[str]):
    pdf.ln(4)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 6, title, ln=True)
    pdf.set_font("Arial", "", 9)
    for line in lines:
        pdf.multi_cell(0, 5, f"• {line}")
    pdf.ln(1)


# --------------------------
# Core: generar informe
# --------------------------
def generate_pdf_from_df(
    df_daily: pd.DataFrame,
    deficit_report: pd.DataFrame | list[dict] | None = None,
    out_path: str = "distribucion_final.pdf",
    logo_path: Path = STATIC_DIR / "logo.png",
    titulo: str = "Distribución de puestos Casa Central",
):
    """
    Espera que df_daily venga desde Seats (rows) y contenga al menos:
      - piso, equipo, dia, cupos
      - "% uso diario" (por fila)  -> NUEVO en seats
      - "% uso semanal" (por fila o repetido por equipo) -> NUEVO en seats
      - dotacion (idealmente; si no, se calcula resumen sin dotación)
    Además:
      - "Cupos libres" puede existir en df_daily pero NO se muestra en el PDF.
    deficit_report:
      - lista de dicts o DataFrame desde Seats (deficit_report)
      - debe incluir: piso,equipo,dia,dotacion,asignado,deficit (sin “causa” en tabla)
    """
    emitted_at = datetime.now()
    pdf = ReportPDF(emitted_at=emitted_at, logo_path=logo_path)
    pdf.alias_nb_pages()

    df = df_daily.copy() if df_daily is not None else pd.DataFrame()

    if df.empty:
        # PDF mínimo de error
        pdf.add_page()
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, titulo, ln=True, align="C")
        pdf.ln(8)
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, "No hay datos para generar el informe.")
        pdf.output(out_path)
        return out_path

    # Normalizar columnas esperadas
    for c in ["piso", "equipo", "dia", "cupos"]:
        if c not in df.columns:
            raise ValueError(f"Falta columna requerida en df_daily: '{c}'")

    # Filtrar cupos libres para todo lo visible en PDF
    df_vis = df[df["equipo"].astype(str).str.strip().str.lower() != "cupos libres"].copy()

    # --------------------------
    # Portada
    # --------------------------
    pdf.add_page()
    pdf.ln(8)
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, titulo, ln=True, align="C")
    pdf.ln(4)
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(
        0, 6,
        "Informe de distribución de cupos diarios (Lunes a Viernes) y resumen semanal por equipo."
    )

    # --------------------------
    # Hoja: Distribución diaria (tabla)
    # --------------------------
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Distribución diaria (detalle)", ln=True)
    pdf.ln(1)

    # columnas esperadas para esta tabla
    # % uso diario viene desde seats; si faltara, lo ponemos vacío.
    if "% uso diario" not in df_vis.columns:
        df_vis["% uso diario"] = None

    daily_table = df_vis[["piso", "equipo", "dia", "cupos", "% uso diario"]].copy()
    daily_table = daily_table.sort_values(by=["piso", "dia", "equipo"], ascending=[True, True, True])

    daily_table["cupos"] = daily_table["cupos"].apply(_safe_int)
    daily_table["% uso diario"] = daily_table["% uso diario"].apply(lambda x: _fmt_pct(x, 1))

    headers = ["piso", "equipo", "dia", "cupos", "% uso diario"]
    display_headers = ["Piso", "Equipo", "Día", "Cupos", "% uso diario"]
    widths = [18, 92, 25, 18, 27]
    aligns = ["C", "L", "C", "C", "C"]

    # Mapear nombres display -> columnas reales
    daily_table_disp = daily_table.rename(columns={
        "piso": "Piso",
        "equipo": "Equipo",
        "dia": "Día",
        "cupos": "Cupos",
        "% uso diario": "% uso diario",
    })

    _render_table(pdf, daily_table_disp, display_headers, widths, aligns, font_size=9)

    _render_glossary(pdf, "Glosario (Distribución diaria)", [
        "Capacidad usable por día: Capacidad_usable = Capacidad_total - Reserva.",
        "Restricciones (si aplican): día completo y mínimos se consideran 'hard' y se asignan antes del remanente.",
        "Remanente: se distribuye proporcionalmente usando el método Sainte-Laguë.",
        "Sainte-Laguë asigna cupos uno a uno según el mayor cociente: w / (2a + 1), donde w es la demanda restante del equipo y a es lo ya asignado.",
        "% uso diario = (cupos_equipo_día / total_cupos_asignados_del_piso_ese_día) × 100.",
    ])

    # --------------------------
    # Hoja: Resumen semanal por equipo (tabla)
    # --------------------------
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Resumen semanal por equipo", ln=True)
    pdf.ln(1)

    # % uso semanal idealmente viene desde seats (repetido por fila o por equipo)
    if "% uso semanal" not in df_vis.columns:
        df_vis["% uso semanal"] = None

    # dotacion idealmente viene desde seats. Si no, dejamos vacío pero mantenemos tabla.
    if "dotacion" not in df_vis.columns:
        df_vis["dotacion"] = None

    # calcular cupos semana por equipo (L-V)
    week = df_vis.groupby("equipo", as_index=False)["cupos"].sum().rename(columns={"cupos": "Cupos semana"})
    week["Prom. cupos/día"] = week["Cupos semana"].apply(_safe_int) / 5.0

    # dotación por equipo (primero no nulo)
    dot = (
        df_vis.groupby("equipo")["dotacion"]
        .apply(lambda s: next((v for v in s.tolist() if pd.notna(v)), None))
        .reset_index()
        .rename(columns={"dotacion": "Dotación"})
    )

    # % uso semanal por equipo (primero no nulo)
    uso_sem = (
        df_vis.groupby("equipo")["% uso semanal"]
        .apply(lambda s: next((v for v in s.tolist() if pd.notna(v)), None))
        .reset_index()
        .rename(columns={"% uso semanal": "% uso semanal"})
    )

    week = week.merge(dot, on="equipo", how="left").merge(uso_sem, on="equipo", how="left")
    week = week.rename(columns={"equipo": "Equipo"})
    week["Dotación"] = week["Dotación"].apply(lambda x: "" if pd.isna(x) else str(_safe_int(x)))
    week["Cupos semana"] = week["Cupos semana"].apply(_safe_int)
    week["Prom. cupos/día"] = week["Prom. cupos/día"].apply(lambda x: f"{_safe_float(x):.2f}")
    week["% uso semanal"] = week["% uso semanal"].apply(lambda x: _fmt_pct(x, 2))
    week = week.sort_values(by=["Equipo"], ascending=True)

    headers2 = ["Equipo", "Dotación", "Cupos semana", "Prom. cupos/día", "% uso semanal"]
    widths2 = [85, 20, 25, 30, 30]
    aligns2 = ["L", "C", "C", "C", "C"]

    _render_table(pdf, week[headers2], headers2, widths2, aligns2, font_size=9)

    _render_glossary(pdf, "Glosario (Uso semanal)", [
        "Cupos semana = suma de cupos asignados (Lunes a Viernes).",
        "Prom. cupos/día = cupos_semana / 5.",
        "% uso semanal = (cupos_semana / (dotación_equipo × 5)) × 100.",
        "La reserva (Cupos libres) no se incluye en los cálculos de % uso.",
    ])

    # --------------------------
    # Hoja: Déficit (si existe)
    # --------------------------
    deficit_df = None
    if deficit_report is not None:
        if isinstance(deficit_report, list):
            deficit_df = pd.DataFrame(deficit_report)
        elif isinstance(deficit_report, pd.DataFrame):
            deficit_df = deficit_report.copy()

    if deficit_df is not None and not deficit_df.empty:
        # Asegurar columnas esperadas (sin causa)
        needed = ["piso", "equipo", "dia", "dotacion", "asignado", "deficit"]
        for c in needed:
            if c not in deficit_df.columns:
                # si falta algo crítico, igual mostramos lo que hay
                pass

        # Filtrar cupos libres por si acaso
        if "equipo" in deficit_df.columns:
            deficit_df = deficit_df[deficit_df["equipo"].astype(str).str.strip().str.lower() != "cupos libres"].copy()

        # Ordenar y formatear
        col_map = {
            "piso": "Piso",
            "equipo": "Equipo",
            "dia": "Día",
            "dotacion": "Dotación",
            "asignado": "Asignado",
            "deficit": "Déficit",
        }
        for k, v in col_map.items():
            if k in deficit_df.columns and v not in deficit_df.columns:
                deficit_df[v] = deficit_df[k]

        show_cols = ["Piso", "Equipo", "Día", "Dotación", "Asignado", "Déficit"]
        for c in show_cols:
            if c not in deficit_df.columns:
                deficit_df[c] = ""

        deficit_df["Piso"] = deficit_df["Piso"].astype(str)
        deficit_df["Equipo"] = deficit_df["Equipo"].astype(str)
        deficit_df["Día"] = deficit_df["Día"].astype(str)
        deficit_df["Dotación"] = deficit_df["Dotación"].apply(_safe_int)
        deficit_df["Asignado"] = deficit_df["Asignado"].apply(_safe_int)
        deficit_df["Déficit"] = deficit_df["Déficit"].apply(_safe_int)

        deficit_df = deficit_df.sort_values(by=["Piso", "Día", "Equipo"], ascending=[True, True, True])

        pdf.add_page()
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, "Reporte de déficit (detalle)", ln=True)
        pdf.ln(1)

        widths3 = [16, 86, 20, 20, 20, 18]
        aligns3 = ["C", "L", "C", "C", "C", "C"]
        _render_table(pdf, deficit_df[show_cols], show_cols, widths3, aligns3, font_size=9)

        _render_glossary(pdf, "Glosario (Déficit)", [
            "Déficit = dotación - asignado (cuando asignado < dotación).",
            "Se produce cuando la demanda del piso no cabe dentro de la capacidad usable diaria o por recortes derivados de restricciones hard.",
        ])

    pdf.output(out_path)
    return out_path
