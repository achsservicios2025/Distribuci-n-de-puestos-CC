import pandas as pd
from fpdf import FPDF
from pathlib import Path
import os

# ... (Imports locales y configs igual que antes) ...

def clean_pdf_text(text: str) -> str:
    """Limpia caracteres y asegura codificación latin-1 para FPDF."""
    if not isinstance(text, str): return str(text)
    # Mapeo manual extendido para tildes comunes
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "Ñ": "N",
        "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U",
        "•": "-", "—": "-", "–": "-", "“": '"', "”": '"'
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    
    # Codificación final segura
    try:
        return text.encode('latin-1', 'ignore').decode('latin-1')
    except:
        return text

# --- FUNCIÓN DE REPORTE ACTUALIZADA ---
def generate_full_pdf(distrib_df, listado_reservas_df=None, listado_salas_df=None, logo_path=None, deficit_data=None, is_admin=False):
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    
    # 1. PORTADA / INFO GENERAL
    pdf.add_page()
    if logo_path and Path(logo_path).exists():
        try: pdf.image(str(logo_path), x=10, y=8, w=30)
        except: pass
    pdf.ln(25)
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, clean_pdf_text("Informe de Distribucion y Uso"), ln=True, align='C')
    pdf.ln(10)

    # 2. DISTRIBUCIÓN DIARIA
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, clean_pdf_text("1. Detalle de Distribucion Diaria"), ln=True)
    
    # Tabla Diaria
    pdf.set_font("Arial", 'B', 9)
    # Ajuste de anchos
    widths = [25, 70, 25, 20, 20] 
    headers = ["Piso", "Equipo", "Dia", "Cupos", "% Dia"]
    
    for w, h in zip(widths, headers): pdf.cell(w, 7, clean_pdf_text(h), 1, 0, 'C')
    pdf.ln()

    pdf.set_font("Arial", '', 9)
    # Filtrar y ordenar
    if not distrib_df.empty:
        # Asegurar orden lógico
        # (Aquí puedes llamar a tu apply_sorting_to_df si la tienes importada)
        for _, r in distrib_df.iterrows():
            pdf.cell(widths[0], 6, clean_pdf_text(str(r.get("piso",""))), 1)
            pdf.cell(widths[1], 6, clean_pdf_text(str(r.get("equipo",""))[:35]), 1)
            pdf.cell(widths[2], 6, clean_pdf_text(str(r.get("dia",""))), 1)
            pdf.cell(widths[3], 6, str(r.get("cupos",0)), 1, 0, 'C')
            pdf.cell(widths[4], 6, f"{r.get('pct',0)}%", 1, 0, 'C')
            pdf.ln()
    
    # 3. RESUMEN SEMANAL (MODIFICADO)
    pdf.add_page()
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, clean_pdf_text("2. Resumen Semanal por Equipo"), ln=True)
    
    if not distrib_df.empty:
        # Calcular métricas semanales
        df_clean = distrib_df[distrib_df['equipo'] != "Cupos libres"].copy()
        if not df_clean.empty:
            # Necesitamos dotación total. Si no viene en el DF, intentamos inferirla del %
            # Dotacion = cupos / (pct/100)
            df_clean['dot_estimada'] = df_clean.apply(lambda x: int(x['cupos'] / (x['pct']/100)) if x['pct']>0 else 0, axis=1)
            # Agrupar
            grp = df_clean.groupby('equipo')
            summary = grp.agg(
                total_semanal=('cupos', 'sum'),
                dotacion_ref=('dot_estimada', 'max') # Tomamos el maximo estimado como referencia
            ).reset_index()
            
            # Calculo % Distr Semanal = (Total Cupos Semanales / (Dotacion * 5)) * 100
            summary['pct_semanal'] = summary.apply(lambda row: round((row['total_semanal'] / (row['dotacion_ref']*5))*100, 1) if row['dotacion_ref']>0 else 0, axis=1)
            
            pdf.set_font("Arial", 'B', 9)
            ws = [80, 30, 30, 30]
            hs = ["Equipo", "Tot. Semanal", "Prom. Diario", "% Semanal"]
            for w, h in zip(ws, hs): pdf.cell(w, 7, clean_pdf_text(h), 1, 0, 'C')
            pdf.ln()
            
            pdf.set_font("Arial", '', 9)
            for _, r in summary.iterrows():
                pdf.cell(ws[0], 6, clean_pdf_text(str(r['equipo'])[:40]), 1)
                pdf.cell(ws[1], 6, str(int(r['total_semanal'])), 1, 0, 'C')
                prom = r['total_semanal'] / 5
                pdf.cell(ws[2], 6, f"{prom:.1f}", 1, 0, 'C')
                pdf.cell(ws[3], 6, f"{r['pct_semanal']}%", 1, 0, 'C')
                pdf.ln()

    # 4. INFORMES DE ADMINISTRADOR (SOLO SI ADMIN)
    if is_admin:
        pdf.add_page()
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, clean_pdf_text("Informes de Gestion (Solo Admin)"), ln=True, align='C')
        
        # A. SALAS
        pdf.ln(5)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, clean_pdf_text("3. Uso de Salas de Reuniones"), ln=True)
        if listado_salas_df is not None and not listado_salas_df.empty:
            # Agrupar por persona para ver quién usa más
            # Normalizar columnas
            cols = {c.lower(): c for c in listado_salas_df.columns}
            col_user = cols.get('nombre') or cols.get('user_name')
            col_sala = cols.get('sala') or cols.get('room_name')
            
            if col_user:
                top_users = listado_salas_df[col_user].value_counts().reset_index()
                top_users.columns = ['Usuario', 'Reservas']
                
                pdf.set_font("Arial", 'B', 9)
                pdf.cell(100, 7, "Usuario", 1); pdf.cell(30, 7, "Cant. Reservas", 1); pdf.ln()
                pdf.set_font("Arial", '', 9)
                for _, r in top_users.head(20).iterrows():
                     pdf.cell(100, 6, clean_pdf_text(str(r['Usuario'])), 1)
                     pdf.cell(30, 6, str(r['Reservas']), 1, 0, 'C')
                     pdf.ln()
        else:
            pdf.set_font("Arial", 'I', 10); pdf.cell(0, 10, "No hay datos de salas.", ln=True)

        # B. PUESTOS INDIVIDUALES
        pdf.ln(5)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, clean_pdf_text("4. Reservas de Cupos Flexibles"), ln=True)
        if listado_reservas_df is not None and not listado_reservas_df.empty:
             # Lógica similar para puestos
             cols = {c.lower(): c for c in listado_reservas_df.columns}
             col_user = cols.get('nombre') or cols.get('user_name')
             if col_user:
                top_puestos = listado_reservas_df[col_user].value_counts().reset_index()
                top_puestos.columns = ['Usuario', 'Reservas']
                
                pdf.set_font("Arial", 'B', 9)
                pdf.cell(100, 7, "Usuario", 1); pdf.cell(30, 7, "Cant. Reservas", 1); pdf.ln()
                pdf.set_font("Arial", '', 9)
                for _, r in top_puestos.head(20).iterrows():
                     pdf.cell(100, 6, clean_pdf_text(str(r['Usuario'])), 1)
                     pdf.cell(30, 6, str(r['Reservas']), 1, 0, 'C')
                     pdf.ln()
        else:
            pdf.set_font("Arial", 'I', 10); pdf.cell(0, 10, "No hay datos de puestos.", ln=True)

    try:
        return pdf.output(dest='S').encode('latin-1', 'ignore')
    except:
        return pdf.output(dest='S')
