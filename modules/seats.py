import pandas as pd
import math
import re

def normalize_text(text):
    """Limpia textos para comparaciones."""
    if pd.isna(text) or text == "": return ""
    text = str(text).strip().lower()
    replacements = {'á':'a', 'é':'e', 'í':'i', 'ó':'o', 'ú':'u', 'ñ':'n', '/': ' ', '-': ' '}
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return re.sub(r'\s+', ' ', text)

def parse_days_from_text(text):
    """Detecta días fijos y flexibles."""
    if pd.isna(text): return {'fijos': set(), 'flexibles': []}
    mapa = {"lunes":"Lunes", "martes":"Martes", "miercoles":"Miércoles", "jueves":"Jueves", "viernes":"Viernes"}
    options = re.split(r'\s+o\s+|,\s*o\s+', text, flags=re.IGNORECASE)
    all_days = set()
    for option_text in options:
        normalized_option = normalize_text(option_text)
        for key, val in mapa.items():
            if key in normalized_option: all_days.add(val)
    return {'fijos': all_days, 'flexibles': options}

def compute_distribution_from_excel(equipos_df, parametros_df, cupos_reserva=2, ignore_params=False):
    rows = []
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    deficit_report = [] 

    # 1. Normalizar columnas
    equipos_df.columns = [str(c).strip().lower() for c in equipos_df.columns]
    parametros_df.columns = [str(c).strip().lower() for c in parametros_df.columns]

    # 2. Buscar columnas clave
    col_piso = next((c for c in equipos_df.columns if 'piso' in normalize_text(c)), None)
    col_equipo = next((c for c in equipos_df.columns if 'equipo' in normalize_text(c)), None)
    col_personas = next((c for c in equipos_df.columns if 'personas' in normalize_text(c) or 'total' in normalize_text(c)), None)
    col_minimos = next((c for c in equipos_df.columns if 'minimo' in normalize_text(c) or 'mínimo' in normalize_text(c)), None)
    
    if not (col_piso and col_equipo and col_personas and col_minimos):
        return [], []

    # 3. Procesar Parámetros (Lectura Robusta)
    capacidad_pisos = {}
    reglas_full_day = {}
    RESERVA_OBLIGATORIA = 2 

    # Intentar leer columnas de parámetros
    col_param = next((c for c in parametros_df.columns if 'criterio' in normalize_text(c) or 'parametro' in normalize_text(c)), '')
    col_valor = next((c for c in parametros_df.columns if 'valor' in normalize_text(c)), '')

    if col_param and col_valor:
        for _, row in parametros_df.iterrows():
            p = str(row.get(col_param, '')).strip().lower()
            v = str(row.get(col_valor, '')).strip()
            
            # Leer capacidad física (SIEMPRE)
            if "cupos totales" in p or "capacidad" in p:
                match_p = re.search(r'piso\s+(\d+)', p)
                match_c = re.search(r'(\d+)', v)
                if match_p and match_c: 
                    capacidad_pisos[match_p.group(1)] = int(match_c.group(1))
            
            # Leer reglas (Solo si no se ignoran)
            if not ignore_params:
                if "dia completo" in p or "día completo" in p:
                    equipo_nombre = re.split(r'd[ií]a completo\s+', p, flags=re.IGNORECASE)[-1].strip()
                    if v: reglas_full_day[normalize_text(equipo_nombre)] = parse_days_from_text(v)

    # 4. Algoritmo de Distribución
    pisos_unicos = equipos_df[col_piso].dropna().unique()

    for piso_raw in pisos_unicos:
        piso_str = str(int(piso_raw)) if isinstance(piso_raw, (int, float)) else str(piso_raw)
        
        # DEFINIR CAPACIDAD REAL
        if piso_str in capacidad_pisos:
            cap_total_real = capacidad_pisos[piso_str]
        else:
            # Fallback: Suma de personas si no hay parámetro
            df_temp = equipos_df[equipos_df[col_piso] == piso_raw]
            cap_total_real = int(df_temp[col_personas].sum()) if col_personas else 50

        # LÍMITE ESTRICTO PARA EQUIPOS
        # Si Total es 38, el Limite es 36.
        hard_limit = max(0, cap_total_real - RESERVA_OBLIGATORIA)
        
        df_piso = equipos_df[equipos_df[col_piso] == piso_raw].copy()

        # Pre-cálculo de flexibles
        full_day_asignacion = {} 
        capacidad_fija_por_dia = {d: 0 for d in dias_semana}
        equipos_flexibles = []

        for _, r in df_piso.iterrows():
            nm = str(r[col_equipo]).strip()
            per = int(r[col_personas]) if pd.notna(r[col_personas]) else 0
            reglas = reglas_full_day.get(normalize_text(nm))
            is_flexible = reglas and len(reglas['flexibles']) > 1
            is_fixed_full_day = reglas and not is_flexible

            if is_fixed_full_day:
                for dia in reglas['fijos']:
                    if dia in dias_semana: capacidad_fija_por_dia[dia] += per
            elif is_flexible:
                equipos_flexibles.append({'eq': nm, 'per': per, 'dias_opt': reglas['fijos']})
            else:
                min_req = int(r[col_minimos]) if col_minimos and pd.notna(r[col_minimos]) else 0
                base_demand = max(2, min_req) if per >= 2 else per
                for dia in dias_semana: capacidad_fija_por_dia[dia] += base_demand

        capacidad_libre_pre = {d: max(0, hard_limit - capacidad_fija_por_dia[d]) for d in dias_semana}
        
        for item_flex in equipos_flexibles:
            best_day = None; max_libre = -float('inf')
            for dia_opt in item_flex['dias_opt']:
                if dia_opt in dias_semana:
                    if capacidad_libre_pre[dia_opt] > max_libre:
                        max_libre = capacidad_libre_pre[dia_opt]; best_day = dia_opt
            if best_day:
                full_day_asignacion[normalize_text(item_flex['eq'])] = best_day
                capacidad_libre_pre[best_day] -= item_flex['per']

        # --- BUCLE DIARIO ---
        for dia_idx, dia in enumerate(dias_semana):
            fd_teams = []
            normal_teams = []
            
            for _, r in df_piso.iterrows():
                nm = str(r[col_equipo]).strip()
                per = int(r[col_personas]) if pd.notna(r[col_personas]) else 0
                min_excel = int(r[col_minimos]) if col_minimos and pd.notna(r[col_minimos]) else 0
                target_min = max(2, min_excel)
                if target_min > per: target_min = per
                
                reglas = reglas_full_day.get(normalize_text(nm))
                is_fd_today = False
                if reglas:
                    is_flex = len(reglas['flexibles']) > 1
                    if not is_flex and dia in reglas['fijos']: is_fd_today = True
                    elif is_flex and full_day_asignacion.get(normalize_text(nm)) == dia: is_fd_today = True
                
                t = {
                    'eq': nm, 'per': per, 'target_min': target_min, 
                    'asig': 0, 'deficit': 0
                }
                if is_fd_today: fd_teams.append(t)
                else: normal_teams.append(t)

            # CONTROL DE CAPACIDAD (INTENTO DE LLENADO)
            used_cap = 0
            
            # 1. Asignar Full Day
            for t in fd_teams:
                t['asig'] = t['per']
                used_cap += t['asig']

            # 2. Rotación
            if len(normal_teams) > 0:
                shift = dia_idx % len(normal_teams)
                normal_teams = normal_teams[shift:] + normal_teams[:shift]

            # 3. Llenado Round Robin (Sin limite estricto aquí, dejamos que se pasen para luego cortar)
            # Esto permite que el algoritmo "intente" llenar todo, y luego la guillotina ajusta.
            keep_going = True
            while keep_going:
                keep_going = False
                for t in normal_teams:
                    if t['asig'] < t['per']: # Si le falta gente
                        t['asig'] += 1
                        used_cap += 1
                        # Solo paramos si ya nos pasamos mucho, pero dejamos un margen para que la guillotina decida
                        if used_cap < hard_limit + 10: 
                            keep_going = True

            # --- LA GUILLOTINA: AJUSTE FORZADO ---
            # Sumamos lo que asignamos realmente
            all_teams_day = fd_teams + normal_teams
            total_asignado = sum(t['asig'] for t in all_teams_day)
            
            # Si nos pasamos del límite (Total - 2)
            exceso = total_asignado - hard_limit
            
            if exceso > 0:
                # Hay que quitar cupos.
                # Estrategia: Quitar a los que más tienen asignado (para ser justos y no dejar a uno en 0)
                # O quitar en orden inverso a la asignación.
                
                # Iteramos quitando 1 cupo a la vez hasta cumplir la meta
                while exceso > 0:
                    # Candidatos: Equipos normales con cupos > 0 (No tocamos Full Day si es posible)
                    candidatos = [t for t in normal_teams if t['asig'] > 0]
                    
                    if not candidatos:
                        # Si no hay normales, tocamos full day (caso extremo)
                        candidatos = [t for t in fd_teams if t['asig'] > 0]
                    
                    if not candidatos: break # No se puede quitar más
                    
                    # Ordenar por quién tiene más cupos asignados (para quitarle al que más tiene)
                    candidatos.sort(key=lambda x: x['asig'], reverse=True)
                    
                    victim = candidatos[0]
                    victim['asig'] -= 1
                    total_asignado -= 1
                    exceso -= 1

            # 4. Cálculo de Déficit y Reporte Final
            for t in all_teams_day:
                goal = t['target_min']
                if t['asig'] < t['per']:
                    t['deficit'] = t['per'] - t['asig']
                    reason = "Reserva de cupos libres priorizada"
                    if t['asig'] < goal: reason = "No alcanzó el mínimo requerido"
                    
                    deficit_report.append({
                        "piso": f"Piso {piso_str}", "equipo": t['eq'], "dia": dia, 
                        "dotacion": t['per'], "minimo": goal, "asignado": t['asig'],
                        "deficit": t['deficit'], "causa": reason
                    })

            # 5. INSERCIÓN DE CUPOS LIBRES
            # Ahora es seguro: total_asignado <= hard_limit (Total - 2)
            # Por lo tanto, el remanente es >= 2.
            remanente_real = cap_total_real - total_asignado
            
            # Guardar filas
            for t in all_teams_day:
                if t['asig'] > 0:
                    pct = round((t['asig'] / t['per']) * 100, 1) if t['per'] > 0 else 0.0
                    rows.append({"piso": f"Piso {piso_str}", "equipo": t['eq'], "dia": dia, "cupos": int(t['asig']), "pct": pct})
            
            if remanente_real > 0:
                pct_libres = round((remanente_real / cap_total_real) * 100, 1)
                rows.append({"piso": f"Piso {piso_str}", "equipo": "Cupos libres", "dia": dia, "cupos": int(remanente_real), "pct": pct_libres})

    return rows, deficit_report
