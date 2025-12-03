import pandas as pd
import math
import re

# ---------------------------------------------------------
# Helpers de texto / normalización
# ---------------------------------------------------------
def normalize_text(text):
    """Limpia textos para comparaciones básicas de columnas."""
    if pd.isna(text) or text == "":
        return ""
    text = str(text).strip().lower()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n",
        "/": " ", "-": " "
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return re.sub(r"\s+", " ", text)


def extract_clean_number_str(val):
    """
    Normalizador agresivo de Pisos.
    Convierte: "Piso 1", 1, 1.0, "1 ", "Nivel 1" -> "1"
    Devuelve siempre un STRING limpio o None.
    """
    if pd.isna(val):
        return None

    s = str(val).strip()

    # Si es un float puro (ej: 1.0), convertirlo a int primero para quitar el decimal
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass

    # Si es texto ("Piso 1"), buscar el primer dígito
    nums = re.findall(r"\d+", s)
    if nums:
        return str(int(nums[0]))

    return None


def parse_days_from_text(text):
    """
    Soporta cosas tipo:
    - "Lunes"
    - "Lunes o Miércoles"
    - "Martes, o Jueves"
    Devuelve:
    - fijos: set de días encontrados normalizados (Lunes..Viernes)
    - flexibles: lista de opciones originales (solo para detectar si venía como "A o B")
    """
    if pd.isna(text):
        return {"fijos": set(), "flexibles": []}

    mapa = {
        "lunes": "Lunes",
        "martes": "Martes",
        "miercoles": "Miércoles",
        "miércoles": "Miércoles",
        "jueves": "Jueves",
        "viernes": "Viernes"
    }

    # Si hay " o " o ", o " lo tratamos como opción flexible
    options = re.split(r"\s+o\s+|,\s*o\s+", str(text), flags=re.IGNORECASE)

    all_days = set()
    for o in options:
        norm = normalize_text(o)
        for k, v in mapa.items():
            if k in norm:
                all_days.add(v)

    return {"fijos": all_days, "flexibles": options}


# ---------------------------------------------------------
# Motor: distribución
# ---------------------------------------------------------
def compute_distribution_from_excel(
    equipos_df,
    parametros_df,
    df_capacidades,
    cupos_reserva=2,
    ignore_params=False
):
    """
    Devuelve:
      rows: lista dicts {piso,equipo,dia,cupos,pct}
        - pct = %Distrib del piso ese día (share de cupos del día, no % de dotación)
      deficit_report: lista dicts con causas + fórmula/explicación
    """
    rows = []
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    deficit_report = []

    # -----------------------------
    # 1) Normalizar headers
    # -----------------------------
    if equipos_df is None or equipos_df.empty:
        return [], []

    equipos_df = equipos_df.copy()
    equipos_df.columns = [str(c).strip().lower() for c in equipos_df.columns]

    if parametros_df is None:
        parametros_df = pd.DataFrame()
    else:
        parametros_df = parametros_df.copy()
        if not parametros_df.empty:
            parametros_df.columns = [str(c).strip().lower() for c in parametros_df.columns]

    if df_capacidades is None:
        df_capacidades = pd.DataFrame()
    else:
        df_capacidades = df_capacidades.copy()

    # -----------------------------
    # 2) Buscar columnas clave
    # -----------------------------
    col_piso = next((c for c in equipos_df.columns if "piso" in normalize_text(c)), None)
    col_equipo = next((c for c in equipos_df.columns if "equipo" in normalize_text(c)), None)
    col_personas = next((c for c in equipos_df.columns
                         if "personas" in normalize_text(c) or "dotacion" in normalize_text(c) or "dotación" in normalize_text(c) or "total" in normalize_text(c)), None)
    col_minimos = next((c for c in equipos_df.columns if "minimo" in normalize_text(c) or "mínimo" in normalize_text(c)), None)

    if not (col_piso and col_equipo and col_personas and col_minimos):
        return [], []

    # -----------------------------
    # 3) Capacidades por piso
    # -----------------------------
    capacidad_pisos = {}
    RESERVA_OBLIGATORIA = int(cupos_reserva) if cupos_reserva is not None else 2

    if not df_capacidades.empty:
        for _, row in df_capacidades.iterrows():
            try:
                raw_piso = row.iloc[0]
                raw_cap = row.iloc[1]
                key_piso = extract_clean_number_str(raw_piso)

                if key_piso is None:
                    continue

                cap_val = int(float(str(raw_cap).replace(",", ".")))
                if cap_val > 0:
                    capacidad_pisos[key_piso] = cap_val
            except Exception:
                continue

    # -----------------------------
    # 4) Reglas día completo
    # -----------------------------
    reglas_full_day = {}
    if not ignore_params and not parametros_df.empty:
        col_param = next((c for c in parametros_df.columns
                          if "criterio" in normalize_text(c) or "parametro" in normalize_text(c) or "parámetro" in normalize_text(c)), None)
        col_valor = next((c for c in parametros_df.columns if "valor" in normalize_text(c)), None)

        if col_param and col_valor:
            for _, row in parametros_df.iterrows():
                p = str(row.get(col_param, "")).strip().lower()
                v = str(row.get(col_valor, "")).strip()

                if "dia completo" in p or "día completo" in p:
                    nm = re.split(r"d[ií]a completo\s+", p)[-1].strip()
                    if v:
                        reglas_full_day[normalize_text(nm)] = parse_days_from_text(v)

    # -----------------------------
    # Fórmulas (para conflictos y glosario)
    # -----------------------------
    FORMULA_EQUIDAD = "Asignación objetivo ≈ (Dotación_equipo / Dotación_total_piso) × Capacidad_día"
    EXPLICACION_EQUIDAD = (
        "Distribución equitativa por proporción: cada equipo debería recibir cupos "
        "proporcionales a su dotación dentro del piso. Matemáticamente:\n"
        "Objetivo(eq,día) = (Dotación_eq / Σ Dotación_piso) × Capacidad_día.\n"
        "Luego se ajusta por reglas (día completo, mínimos) y por límite físico del piso."
    )

    # -----------------------------
    # 5) Iterar pisos
    # -----------------------------
    pisos_unicos = equipos_df[col_piso].dropna().unique()

    for piso_raw in pisos_unicos:
        piso_str = extract_clean_number_str(piso_raw)
        if not piso_str:
            continue

        # DF del piso: ojo, filtramos por igualdad exacta usando el valor original (piso_raw)
        df_piso = equipos_df[equipos_df[col_piso] == piso_raw].copy()
        if df_piso.empty:
            continue

        # Capacidad real del piso
        if piso_str in capacidad_pisos:
            cap_total_real = int(capacidad_pisos[piso_str])
        else:
            # fallback: suma dotación + reserva
            try:
                cap_total_real = int(df_piso[col_personas].fillna(0).astype(float).sum()) + RESERVA_OBLIGATORIA
            except Exception:
                cap_total_real = RESERVA_OBLIGATORIA

        cap_total_real = max(0, int(cap_total_real))

        # Límite duro utilizable para equipos (sin reserva)
        hard_limit = max(0, cap_total_real - RESERVA_OBLIGATORIA)

        # Pre-cálculo: asignación de flexibles (día completo con opciones)
        full_day_asignacion = {}
        capacidad_fija_por_dia = {d: 0 for d in dias_semana}
        equipos_flexibles = []

        # armamos lista equipos con dotación/min
        equipos_info = []
        for _, r in df_piso.iterrows():
            nm = str(r.get(col_equipo, "")).strip()
            if not nm:
                continue

            # dotación
            try:
                per = int(float(str(r.get(col_personas, 0)).replace(",", ".")))
            except Exception:
                per = 0
            per = max(0, per)

            # mínimo (lo mantengo como tú: min >=2 si per>=2, pero nunca > per)
            try:
                mini_raw = int(float(str(r.get(col_minimos, 0)).replace(",", ".")))
            except Exception:
                mini_raw = 0

            mini = mini_raw
            if per >= 2:
                mini = max(2, mini_raw)
            mini = min(per, mini)  # nunca más que la dotación

            equipos_info.append({"eq": nm, "per": per, "min": mini})

            # reglas día completo?
            reglas = reglas_full_day.get(normalize_text(nm))
            if reglas:
                is_flex = len(reglas["flexibles"]) > 1
                if not is_flex:
                    # fijo
                    for d in reglas["fijos"]:
                        if d in dias_semana:
                            capacidad_fija_por_dia[d] += per
                else:
                    # flexible: decidir después
                    equipos_flexibles.append({"eq": nm, "per": per, "dias_opt": reglas["fijos"]})
            else:
                # sin regla: reservar mínimo base todos los días
                base = mini if per >= 2 else per
                for d in dias_semana:
                    capacidad_fija_por_dia[d] += base

        # asignar flexibles al día con más holgura pre-estimada
        cap_libre_pre = {d: max(0, hard_limit - capacidad_fija_por_dia[d]) for d in dias_semana}
        for item in equipos_flexibles:
            best_day = None
            max_l = -10**9
            for d in item["dias_opt"]:
                if d in dias_semana and cap_libre_pre[d] > max_l:
                    max_l = cap_libre_pre[d]
                    best_day = d
            if best_day:
                full_day_asignacion[normalize_text(item["eq"])] = best_day
                cap_libre_pre[best_day] -= item["per"]

        # -----------------------------
        # Loop por día
        # -----------------------------
        for dia_idx, dia in enumerate(dias_semana):
            teams = []
            for info in equipos_info:
                nm = info["eq"]
                per = info["per"]
                mini = info["min"]

                reglas = reglas_full_day.get(normalize_text(nm))
                is_fd = False
                if reglas and not ignore_params:
                    is_flex = len(reglas["flexibles"]) > 1
                    if (not is_flex and dia in reglas["fijos"]) or (is_flex and full_day_asignacion.get(normalize_text(nm)) == dia):
                        is_fd = True

                teams.append({
                    "eq": nm,
                    "per": per,
                    "min": mini,
                    "asig": 0,
                    "is_fd": is_fd
                })

            # A) Día completo primero
            used = 0
            fd_teams = [t for t in teams if t["is_fd"]]
            norm_teams = [t for t in teams if not t["is_fd"]]

            for t in fd_teams:
                t["asig"] = t["per"]
                used += t["asig"]

            # Si el día completo ya excede hard_limit, recortamos proporcionalmente desde los mayores
            if used > hard_limit:
                exceso = used - hard_limit
                while exceso > 0:
                    candidatos = [t for t in fd_teams if t["asig"] > 0]
                    if not candidatos:
                        break
                    candidatos.sort(key=lambda x: x["asig"], reverse=True)
                    candidatos[0]["asig"] -= 1
                    used -= 1
                    exceso -= 1

            # B) Round robin para normales (hasta hard_limit)
            # (tu versión podía pasarse y luego recortar; acá evitamos pasarnos de entrada)
            if norm_teams and used < hard_limit:
                shift = dia_idx % len(norm_teams)
                norm_teams = norm_teams[shift:] + norm_teams[:shift]

                # repartimos 1 por vuelta mientras haya cupo y alguien tenga demanda
                keep = True
                while keep and used < hard_limit:
                    keep = False
                    for t in norm_teams:
                        if used >= hard_limit:
                            break
                        if t["asig"] < t["per"]:
                            t["asig"] += 1
                            used += 1
                            keep = True

            # C) Total asignado del día (solo equipos)
            total_asig = sum(t["asig"] for t in teams)
            total_asig = min(total_asig, hard_limit)  # seguridad extra

            # D) Calcular %Distrib (share del día) y escribir filas
            # %Distrib = cupos_equipo / total_asig_dia * 100 (excluye Cupos libres)
            for t in teams:
                if t["asig"] <= 0:
                    continue

                pct = round((t["asig"] / total_asig * 100.0), 1) if total_asig > 0 else 0.0

                rows.append({
                    "piso": piso_str,         # "1", "2"...
                    "equipo": t["eq"],
                    "dia": dia,
                    "cupos": int(t["asig"]),
                    "pct": float(pct)         # %Distrib (share del día)
                })

            # E) Cupos libres = capacidad total real - cupos_asignados_equipo
            remanente = cap_total_real - total_asig
            remanente = max(0, int(remanente))

            # pct de cupos libres como share de capacidad total del piso
            pct_lib = round((remanente / cap_total_real * 100.0), 1) if cap_total_real > 0 else 0.0

            rows.append({
                "piso": piso_str,
                "equipo": "Cupos libres",
                "dia": dia,
                "cupos": int(remanente),
                "pct": float(pct_lib)
            })

            # F) Reporte de conflictos:
            # - si no alcanza el mínimo
            # - o si no alcanza la dotación (capacidad física)
            for t in teams:
                per = int(t["per"])
                mini = int(t["min"])
                asig = int(t["asig"])

                # Sin dotación, no reportamos
                if per <= 0:
                    continue

                # Solo reportamos si hay un "problema real"
                # 1) No cumple mínimo
                # 2) No cumple dotación (capacidad)
                if asig < mini:
                    cause = "No alcanzó el mínimo requerido"
                    deficit_val = mini - asig
                elif asig < per:
                    cause = "Falta capacidad física del piso (límite diario)"
                    deficit_val = per - asig
                else:
                    continue

                deficit_report.append({
                    "piso": piso_str,
                    "equipo": t["eq"],
                    "dia": dia,
                    "dotacion": per,
                    "minimo": mini,
                    "asignado": asig,
                    "deficit": int(deficit_val),
                    "causa": cause,
                    "formula": FORMULA_EQUIDAD,
                    "explicacion": EXPLICACION_EQUIDAD
                })

    return rows, deficit_report
