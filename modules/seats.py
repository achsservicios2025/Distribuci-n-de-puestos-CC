import pandas as pd
import math
import re

def normalize_text(text):
    if pd.isna(text) or text == "": return ""
    text = str(text).strip().lower()
    replacements = {'á':'a', 'é':'e', 'í':'i', 'ó':'o', 'ú':'u', 'ñ':'n', '/': ' ', '-': ' '}
    for bad, good in replacements.items(): text = text.replace(bad, good)
    return re.sub(r'\s+', ' ', text)

def parse_days_from_text(text):
    if pd.isna(text): return {'fijos': set(), 'flexibles': []}
    mapa = {"lunes":"Lunes", "martes":"Martes", "miercoles":"Miércoles", "jueves":"Jueves", "viernes":"Viernes"}
    options = re.split(r'\s+o\s+|,\s*o\s+', text, flags=re.IGNORECASE)
    all_days = set()
    for o in options:
        norm = normalize_text(o)
        for k, v in mapa.items():
            if k in norm: all_days.add(v)
    return {'fijos': all_days, 'flexibles': options}

def compute_distribution_from_excel(equipos_df, parametros_df, cupos_reserva=2, ignore_params=False):
    rows = []
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    deficit_report = [] 

    equipos_df.columns = [str(c).strip().lower() for c in equipos_df.columns]
    parametros_df.columns = [str(c).strip().lower() for c in parametros_df.columns]

    col_piso = next((c for c in equipos_df.columns if 'piso' in normalize_text(c)), None)
    col_equipo = next((c for c in equipos_df.columns if 'equipo' in normalize_text(c)), None)
    col_personas = next((c for c in equipos_df.columns if 'personas' in normalize_text(c) or 'total' in normalize_text(c)), None)
    col_minimos = next((c for c in equipos_df.columns if 'minimo' in normalize_text(c) or 'mínimo' in normalize_text(c)), None)
    
    if not (col_piso and col_equipo and col_personas and col_minimos): return [], []

    # --- LECTURA DE CAPACIDAD ---
    capacidad_pisos = {}
    reglas_full_day = {}
    RESERVA_OBLIGATORIA = 2 

    col_param = next((c for c in parametros_df.columns if 'criterio' in normalize_text(c) or 'parametro' in normalize_text(c)), '')
    col_valor = next((c for c in parametros_df.columns if 'valor' in normalize_text(c)), '')

    if col_param and col_valor:
        for _, row in parametros_df.iterrows():
            p = str(row.get(col_param, '')).strip().lower()
            v = str(row.get(col_valor, '')).strip()
            # Leer capacidad SIEMPRE
            if "cupos totales" in p or "capacidad" in p:
                match_p = re.search(r'piso\s+(\d+)', p)
                match_c = re.search(r'(\d+)', v)
                if match_p and match_c: capacidad_pisos[match_p.group(1)] = int(match_c.group(1))
            # Leer reglas (condicional)
            if not ignore_params:
                if "dia completo" in p:
                    nm = re.split(r'd[ií]a completo\s+', p)[-1].strip()
                    if v: reglas_full_day[normalize_text(nm)] = parse_days_from_text(v)

    # --- ALGORITMO ---
    for piso_raw in equipos_df[col_piso].dropna().unique():
        piso_str = str(int(piso_raw)) if isinstance(piso_raw, (int, float)) else str(piso_raw)
        
        # Determinar Total Real
        if piso_str in capacidad_pisos:
            cap_total_real = capacidad_pisos[piso_str]
        else:
            df_temp = equipos_df[equipos_df[col_piso] == piso_raw]
            cap_total_real = int(df_temp[col_personas].sum()) if col_personas else 50

        # LIMITE DURO (Total - 2)
        hard_limit = max(0, cap_total_real - RESERVA_OBLIGATORIA)
        
        df_piso = equipos_df[equipos_df[col_piso] == piso_raw].copy()
        
        # Pre-cálculo flexibles
        full_day_asignacion = {} 
        capacidad_fija_por_dia = {d: 0 for d in dias_semana}
        equipos_flexibles = []

        for _, r in df_piso.iterrows():
            nm = str(r[col_equipo]).strip(); per = int(r[col_personas] or 0)
            reglas = reglas_full_day.get(normalize_text(nm))
            is_flex = reglas and len(reglas['flexibles']) > 1
            if reglas and not is_flex:
                for d in reglas['fijos']: 
                    if d in dias_semana: capacidad_fija_por_dia[d] += per
            elif is_flex:
                equipos_flexibles.append({'eq': nm, 'per': per, 'dias_opt': reglas['fijos']})
            else:
                base = max(2, int(r[col_minimos] or 0)) if per >= 2 else per
                for d in dias_semana: capacidad_fija_por_dia[d] += base

        cap_libre_pre = {d: max(0, hard_limit - capacidad_fija_por_dia[d]) for d in dias_semana}
        for item in equipos_flexibles:
            best_day = None; max_l = -999
            for d in item['dias_opt']:
                if d in dias_semana and cap_libre_pre[d] > max_l:
                    max_l = cap_libre_pre[d]; best_day = d
            if best_day:
                full_day_asignacion[normalize_text(item['eq'])] = best_day
                cap_libre_pre[best_day] -= item['per']

        # Loop Diario
        for dia_idx, dia in enumerate(dias_semana):
            teams = []
            for _, r in df_piso.iterrows():
                nm = str(r[col_equipo]).strip(); per = int(r[col_personas] or 0)
                mini = int(r[col_minimos] or 0)
                reglas = reglas_full_day.get(normalize_text(nm))
                is_fd = False
                if reglas:
                    is_flex = len(reglas['flexibles']) > 1
                    if not is_flex and dia in reglas['fijos']: is_fd = True
                    elif is_flex and full_day_asignacion.get(normalize_text(nm)) == dia: is_fd = True
                
                teams.append({
                    'eq': nm, 'per': per, 'min': max(2, mini) if max(2, mini) <= per else per,
                    'asig': 0, 'is_fd': is_fd, 'deficit': 0
                })

            # 1. Asignar Full Day
            used = 0
            fd_teams = [t for t in teams if t['is_fd']]
            norm_teams = [t for t in teams if not t['is_fd']]
            
            for t in fd_teams:
                t['asig'] = t['per']
                used += t['asig']

            # 2. Round Robin Normales
            if norm_teams:
                shift = dia_idx % len(norm_teams)
                norm_teams = norm_teams[shift:] + norm_teams[:shift]
                keep = True
                while keep:
                    keep = False
                    for t in norm_teams:
                        if t['asig'] < t['per']:
                            t['asig'] += 1; used += 1; keep = True
                    if used > hard_limit + 20: break

            # --- LA GUILLOTINA (CORTE FINAL) ---
            total_asig = sum(t['asig'] for t in teams)
            exceso = total_asig - hard_limit
            
            if exceso > 0:
                # Quitar a los que más tienen
                while exceso > 0:
                    candidatos = [t for t in teams if t['asig'] > 0]
                    if not candidatos: break
                    candidatos.sort(key=lambda x: x['asig'], reverse=True)
                    candidatos[0]['asig'] -= 1
                    exceso -= 1
            
            # --- GUARDAR RESULTADOS ---
            final_asig_sum = 0
            for t in teams:
                if t['asig'] > 0:
                    pct = round(t['asig']/t['per']*100, 1) if t['per'] else 0
                    rows.append({"piso": f"Piso {piso_str}", "equipo": t['eq'], "dia": dia, "cupos": int(t['asig']), "pct": pct})
                    final_asig_sum += t['asig']
            
            # --- INSERCIÓN FORZADA DE CUPOS LIBRES ---
            # Calculamos lo que falta para llegar al Total Real
            remanente = cap_total_real - final_asig_sum
            
            # Seguridad: Si el remanente es menor a 2 (por error de redondeo), forzamos 2
            if remanente < RESERVA_OBLIGATORIA:
                remanente = RESERVA_OBLIGATORIA
            
            pct_lib = round(remanente/cap_total_real*100, 1)
            rows.append({"piso": f"Piso {piso_str}", "equipo": "Cupos libres", "dia": dia, "cupos": int(remanente), "pct": pct_lib})

            # Reporte Deficit
            for t in teams:
                if t['asig'] < t['per']:
                    cause = "Reserva de espacio priorizada" if t['asig'] < t['min'] else "Falta capacidad física"
                    deficit_report.append({
                        "piso": f"Piso {piso_str}", "equipo": t['eq'], "dia": dia, 
                        "dotacion": t['per'], "minimo": t['min'], "asignado": t['asig'], 
                        "deficit": t['per'] - t['asig'], "causa": cause
                    })

    return rows, deficit_report
