import pandas as pd
import math
import re
import os # Necesario para get_custom_font

# --- FUNCIONES AUXILIARES (SE CONSERVAN) ---

def normalize_text(text):
    """Limpia textos para comparaciones (maneja tildes y normaliza espacios)."""
    if pd.isna(text) or text == "": return ""
    text = str(text).strip().lower()
    replacements = {'á':'a', 'é':'e', 'í':'i', 'ó':'o', 'ú':'u', 'ñ':'n', '/': ' ', '-': ' '}
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return re.sub(r'\s+', ' ', text)

def parse_days_from_text(text):
    """
    Detecta días fijos y días flexibles (opcionales) en el texto.
    Retorna un diccionario con 'fijos' (set) y 'flexibles' (list of options).
    
    CORRECCIÓN: Aseguramos que 'fijos' solo contenga los días cuando hay reglas.
    """
    if pd.isna(text): return {'fijos': set(), 'flexibles': []}
    mapa = {"lunes":"Lunes", "martes":"Martes", "miercoles":"Miércoles", "jueves":"Jueves", "viernes":"Viernes"}
    
    # 1. Separar por 'o' o ', o' (para opciones flexibles)
    options = re.split(r'\s+o\s+|,\s*o\s+', text, flags=re.IGNORECASE)
    
    flexible_options = []
    
    for option_text in options:
        current_set = set()
        normalized_option = normalize_text(option_text)
        
        for key, val in mapa.items():
            if key in normalized_option: current_set.add(val)
            
        if current_set: flexible_options.append(current_set)
    
    # Si solo hay una opción, se considera fijo (o la opción base)
    if len(flexible_options) == 1:
         fijos = flexible_options[0]
         flexibles_list = []
    else:
         fijos = set()
         for s in flexible_options: fijos.update(s)
         flexibles_list = options

    # 2. BUG CORREGIDO: 'fijos' solo debe contener días de la primera opción si no hay flexibles.
    # En la lógica de distribución, 'fijos' representa los días que el equipo está OBLIGADO a ir.
    # Si hay múltiples opciones, los días son una piscina de flexibles (más de 1 opción flexible).
    
    # Usaremos 'fijos' como el set de días si NO es flexible. Si ES flexible, usamos la lista de opciones.
    
    return {'fijos': fijos, 'flexibles': flexibles_list}

# La librería PIL no es estándar aquí, la conservamos como si fuera de un módulo separado
from PIL import Image, ImageDraw, ImageFont, ImageColor 

def get_custom_font(font_name: str, size: int):
    """Carga fuentes de forma segura (Reutilizada de tu módulo zones.py)."""
    # Sanitizar el nombre de la fuente para evitar errores
    safe_font_name = re.sub(r'[^a-zA-Z0-9 ]', '', font_name)
    font_files = {
        "Arial": "arial.ttf", "Arial Black": "ariblk.ttf", "Poppins": "arial.ttf", 
        "Montserrat": "arial.ttf", "Roboto": "Roboto-Regular.ttf", "Inter": "arial.ttf",
        "Calibri": "calibri.ttf", "Lato": "arial.ttf", "Tahoma": "tahoma.ttf",
    }
    filename = font_files.get(safe_font_name, "arial.ttf")
    
    try:
        # Intentar cargar la fuente específica
        return ImageFont.truetype(filename, size)
    except:
        try: 
            # Fallback a Arial (seguro)
            return ImageFont.truetype("arial.ttf", size)
        except: 
            # Fallback a la fuente por defecto de PIL
            return ImageFont.load_default()

# --- HEADER CON TÍTULO Y SUBTÍTULO (Reutilizado de tu layout.py) ---

def get_x_pos(txt, fnt, align, width):
    """Calcula la posición X para centrar/alinear texto."""
    try:
        w = fnt.getbbox(txt)[2] - fnt.getbbox(txt)[0]
    except: 
        w = 0 
        
    if align == "Izquierda": return 30
    elif align == "Derecha": return width - w - 30
    else: return (width - w) // 2

def get_text_height(txt, fnt):
    """Calcula la altura real del texto."""
    try:
        return fnt.getbbox(txt)[3] - fnt.getbbox(txt)[1]
    except: 
        return fnt.size # Fallback

def create_header_image(width, config, logo_path=None):
    """Función esencial del Header (CORRECCIÓN APLICADA)."""
    bg_color = config.get("bg_color", "#FFFFFF")
    use_logo = config.get("use_logo", False)
    logo_w_req = config.get("logo_width", 150)
    logo_align = config.get("logo_align", "Izquierda")
    t_text = config.get("title_text", "")
    t_font = config.get("title_font", "Arial")
    t_size = config.get("title_size", 36)
    t_color = config.get("title_color", "#000000")
    t_align = config.get("alignment", "Centro") 
    s_text = config.get("subtitle_text", "")
    s_font = config.get("subtitle_font", "Arial") 
    s_size = config.get("subtitle_size", 24)       
    s_color = config.get("subtitle_color", "#666666")

    font_t = get_custom_font(t_font, t_size)
    font_s = get_custom_font(s_font, s_size)
    
    h_t = get_text_height(t_text, font_t)
    h_s = get_text_height(s_text, font_s)
    
    gap_px = 20 if s_text else 0 
    padding_top = 40
    padding_bottom = 40
    
    logo_h, logo_w, logo_img = 0, 0, None
    
    if use_logo and logo_path and os.path.exists(logo_path):
        try:
            logo_img = Image.open(logo_path).convert("RGBA")
            asp = logo_img.height / logo_img.width
            logo_w = logo_w_req
            logo_h = int(logo_w * asp)
        except: use_logo = False
             
    content_height = h_t + gap_px + h_s
    header_height = max(150, content_height + padding_top + padding_bottom + logo_h)
    
    img = Image.new("RGB", (width, header_height), bg_color)
    draw = ImageDraw.Draw(img)

    block_y_start = (header_height - (content_height + logo_h)) // 2
    current_y = block_y_start
    
    # 2. Logo
    if use_logo and logo_img:
        try:
            logo_img = logo_img.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
            if logo_align == "Izquierda": x_pos_logo = 30
            elif logo_align == "Derecha": x_pos_logo = width - logo_w - 30
            else: x_pos_logo = (width - logo_w) // 2 
            
            img.paste(logo_img, (x_pos_logo, current_y), logo_img)
            current_y += logo_h + 20
        except Exception as e: pass
            
    # 3. Dibujar Textos (CORREGIDO: Bloque duplicado eliminado)
    if t_text:
        tx = get_x_pos(t_text, font_t, t_align, width)
        draw.text((tx, current_y), t_text, font=font_t, fill=t_color)
        current_y += h_t + gap_px
    
    if s_text:
        sx = get_x_pos(s_text, font_s, s_align, width)
        draw.text((sx, current_y), s_text, font=font_s, fill=s_color)

    return img

# --- ALGORITMO PRINCIPAL DE DISTRIBUCIÓN ---

def compute_distribution_from_excel(equipos_df, parametros_df, cupos_reserva=2):
    rows = []
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    deficit_report = [] 

    # 1. Normalizar columnas del DataFrame (headers)
    equipos_df.columns = [str(c).strip().lower() for c in equipos_df.columns]
    parametros_df.columns = [str(c).strip().lower() for c in parametros_df.columns]

    # 2. Buscar columnas clave dinámicamente
    col_piso = next((c for c in equipos_df.columns if 'piso' in normalize_text(c)), None)
    col_equipo = next((c for c in equipos_df.columns if 'equipo' in normalize_text(c)), None)
    col_personas = next((c for c in equipos_df.columns if 'dotacion' in normalize_text(c) or 'personas' in normalize_text(c) or 'total' in normalize_text(c)), None)
    col_minimos = next((c for c in equipos_df.columns if 'minimo' in normalize_text(c) or 'mínimo' in normalize_text(c)), None)
    
    if not (col_piso and col_equipo and col_personas and col_minimos):
        # ERROR: Faltan columnas clave
        return [], [{"piso": "Error", "equipo": "Error", "dia": "", "dotacion": 0, "minimo": 0, "deficit": 0, "causa": "Faltan columnas DOTACION o MINIMO en el Excel."}]

    # 3. Procesar Parámetros
    col_param = next((c for c in parametros_df.columns if 'criterio' in normalize_text(c) or 'parametro' in normalize_text(c)), '')
    col_valor = next((c for c in parametros_df.columns if 'valor' in normalize_text(c)), '')

    capacidad_pisos = {}
    reglas_full_day = {}
    cap_reserva_fija = 0 
    
    for _, row in parametros_df.iterrows():
        p = str(row.get(col_param, '')).strip().lower()
        v = str(row.get(col_valor, '')).strip()
        if "cupos totales piso" in p:
            match_p = re.search(r'piso\s+(\d+)', p)
            match_c = re.search(r'(\d+)', v)
            if match_p and match_c: capacidad_pisos[match_p.group(1)] = int(match_c.group(1))
        if "cupos libres por piso" in p:
            match_r = re.search(r'(\d+)', v)
            if match_r: cap_reserva_fija = int(match_r.group(1))
        if "dia completo" in p or "día completo" in p:
            equipo_nombre = re.split(r'd[ií]a completo\s+', p, flags=re.IGNORECASE)[-1].strip()
            if v: reglas_full_day[normalize_text(equipo_nombre)] = parse_days_from_text(v)

    # 4. Algoritmo de Distribución
    pisos_unicos = equipos_df[col_piso].dropna().unique()

    for piso_raw in pisos_unicos:
        piso_str = str(int(piso_raw)) if isinstance(piso_raw, (int, float)) else str(piso_raw)
        cap_total_piso = capacidad_pisos.get(piso_str, 50) 
        df_piso = equipos_df[equipos_df[col_piso] == piso_raw].copy()

        # --- Lógica de Flexibles (Pre-asignación de día óptimo) ---
        full_day_asignacion = {} 
        capacidad_fija_por_dia = {d: 0 for d in dias_semana}
        equipos_flexibles = []

        for _, r in df_piso.iterrows():
            nm = str(r[col_equipo]).strip()
            per = int(r[col_personas]) if pd.notna(r[col_personas]) else 0
            reglas = reglas_full_day.get(normalize_text(nm))
            # Es flexible si el texto contenía "o" o ", o" y resultó en más de una opción
            is_flexible = reglas and len(reglas.get('flexibles', [])) > 1 
            is_fixed_full_day = reglas and not is_flexible

            if is_fixed_full_day:
                for dia in reglas['fijos']:
                    if dia in dias_semana: capacidad_fija_por_dia[dia] += per
            elif is_flexible:
                equipos_flexibles.append({'eq': nm, 'per': per, 'dias_opt': reglas['fijos']})
            else:
                # Estimación base para balanceo (usamos la regla de 2 o el mínimo excel)
                min_req = int(r[col_minimos]) if col_minimos and pd.notna(r[col_minimos]) else 0
                base_demand = max(2, min_req) if per >= 2 else per
                for dia in dias_semana: capacidad_fija_por_dia[dia] += base_demand

        capacidad_libre_pre = {d: max(0, cap_total_piso - capacidad_fija_por_dia[d]) for d in dias_semana}
        for item_flex in equipos_flexibles:
            best_day = None; max_libre = -float('inf')
            # Busca el día de su pool de opciones con más capacidad libre
            for dia_opt in item_flex['dias_opt']:
                if dia_opt in dias_semana:
                    if capacidad_libre_pre[dia_opt] > max_libre:
                        max_libre = capacidad_libre_pre[dia_opt]; best_day = dia_opt
            if best_day:
                full_day_asignacion[normalize_text(item_flex['eq'])] = best_day
                capacidad_libre_pre[best_day] -= item_flex['per']

        # --- BUCLE DE ASIGNACIÓN DIARIA (Algoritmo por Rondas) ---
        for dia_idx, dia in enumerate(dias_semana):
            fd_teams = []
            normal_teams = []
            
            # 1. Clasificación
            for _, r in df_piso.iterrows():
                nm = str(r[col_equipo]).strip()
                per = int(r[col_personas]) if pd.notna(r[col_personas]) else 0
                min_excel = int(r[col_minimos]) if col_minimos and pd.notna(r[col_minimos]) else 0
                
                target_min = max(2, min_excel)
                if target_min > per: target_min = per
                
                reglas = reglas_full_day.get(normalize_text(nm))
                is_fd_today = False
                if reglas:
                    is_flex = len(reglas.get('flexibles', [])) > 1
                    if not is_flex and dia in reglas['fijos']: is_fd_today = True
                    elif is_flex and full_day_asignacion.get(normalize_text(nm)) == dia: is_fd_today = True
                
                t = {
                    'eq': nm, 
                    'per': per, 
                    'min_excel': min_excel, 
                    'target_min': target_min, 
                    'asig': 0, 
                    'deficit': 0
                }
                
                if is_fd_today: fd_teams.append(t)
                else: normal_teams.append(t)

            # 2. Prioridad 0: Full Day (Entran completos sí o sí)
            current_cap = cap_total_piso
            for t in fd_teams:
                t['asig'] = t['per']
                current_cap -= t['asig']
            
            remaining_cap = max(0, current_cap)

            # --- Rotación Inicial (Aplica solo a normal_teams) ---
            if len(normal_teams) > 0:
                shift = dia_idx % len(normal_teams)
                normal_teams = normal_teams[shift:] + normal_teams[:shift]

            # 3. ALGORITMO DE RONDAS (Equidad Garantizada)
            
            # RONDA 1: Supervivencia (Asegurar 1 cupo a todos que lo necesiten)
            for t in normal_teams:
                if remaining_cap > 0 and t['asig'] < t['per']:
                    t['asig'] += 1
                    remaining_cap -= 1
            
            # RONDA 2: Regla del Mínimo 2 (Asegurar 2 cupos a todos que lo necesiten)
            for t in normal_teams:
                if remaining_cap > 0 and t['asig'] < 2 and t['asig'] < t['per']:
                    t['asig'] += 1
                    remaining_cap -= 1
            
            # RONDA 3: Mínimo del Excel (Priorizar cumplir el requerimiento formal)
            for t in normal_teams:
                if remaining_cap > 0 and t['asig'] < t['min_excel'] and t['asig'] < t['per']:
                    needed = t['min_excel'] - t['asig']
                    give = min(needed, remaining_cap)
                    t['asig'] += give
                    remaining_cap -= give

            # RONDA 4: Crecimiento Proporcional (Llenar con lo que sobra)
            if remaining_cap > 0:
                pool = [t for t in normal_teams if t['asig'] < t['per']]
                if pool:
                    total_gap = sum(t['per'] - t['asig'] for t in pool)
                    factor = remaining_cap / total_gap if total_gap > 0 else 0
                    
                    dist_round = 0
                    for t in pool:
                        gap = t['per'] - t['asig']
                        # Asegurar que solo se asignen cupos enteros y no excedan el máximo
                        extra = min(math.floor(gap * factor), gap)
                        t['asig'] += extra
                        dist_round += extra
                    
                    remaining_cap -= dist_round
                    
                    # Saldo final (repartir cupos sueltos por redondeo)
                    pool = [t for t in normal_teams if t['asig'] < t['per']]
                    if len(pool) > 0:
                        # Rotamos aquí para que el "cupo extra de la suerte" no se lo lleve siempre el mismo
                        shift_pool = dia_idx % len(pool)
                        pool = pool[shift_pool:] + pool[:shift_pool]

                        for t in pool:
                            if remaining_cap > 0 and t['asig'] < t['per']:
                                t['asig'] += 1
                                remaining_cap -= 1

            # 4. Cálculo de Déficit y Reporte
            for t in normal_teams:
                goal = t['target_min']
                if t['asig'] < goal:
                    t['deficit'] = goal - t['asig']
                    deficit_report.append({
                        "piso": f"Piso {piso_str}", 
                        "equipo": t['eq'], 
                        "dia": dia, 
                        "dotacion": t['per'],
                        "minimo": goal,
                        "asignado": t['asig'],
                        "deficit": t['deficit'],
                        "causa": "Capacidad crítica (Piso lleno)"
                    })

            # 5. Cupos Libres (Solo si sobró después de satisfacer a TODOS)
            final_libres = 0
            alguien_falta = any(t['asig'] < t['per'] for t in normal_teams)
            
            # Si no falta nadie, usamos la capacidad restante
            if not alguien_falta and remaining_cap > 0:
                 # Usamos min(remaining_cap, cap_reserva_fija) si cap_reserva_fija está configurado
                 final_libres = min(remaining_cap, cap_reserva_fija) if cap_reserva_fija > 0 else remaining_cap

            # Guardar resultados
            all_teams = fd_teams + normal_teams
            for t in all_teams:
                if t['asig'] > 0:
                    pct = round((t['asig'] / t['per']) * 100, 1) if t['per'] > 0 else 0.0
                    rows.append({"piso": f"Piso {piso_str}", "equipo": t['eq'], "dia": dia, "cupos": int(t['asig']), "pct": pct})
            
            if final_libres > 0:
                pct = round((final_libres / cap_total_piso) * 100, 1) if cap_total_piso > 0 else 0.0
                rows.append({"piso": f"Piso {piso_str}", "equipo": "Cupos libres", "dia": dia, "cupos": int(final_libres), "pct": pct})


    return rows, deficit_report
