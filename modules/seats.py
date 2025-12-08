# seats.py
import pandas as pd
import re
import random
import math
from typing import Any, Dict, List, Optional


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

    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass

    nums = re.findall(r"\d+", s)
    if nums:
        return str(int(nums[0]))

    return None


def round_half_up(x: float) -> int:
    """
    Redondeo como tú pediste:
      4.5 -> 5
      4.4 -> 4
    """
    if x is None:
        return 0
    return int(math.floor(x + 0.5))


# ---------------------------------------------------------
# Día completo: parseo con la semántica EXACTA pedida
# ---------------------------------------------------------
def parse_full_day_rule(text: Any) -> Dict[str, Any]:
    """
    Regla textual:
      - "Lunes, Miércoles" => fixed en ambos días
      - "Lunes o Miércoles" / "Lunes, o Miércoles" => choice (elige 1)
    """
    if pd.isna(text) or str(text).strip() == "":
        return {"type": "none", "days": []}

    raw = str(text).strip()

    mapa = {
        "lunes": "Lunes",
        "martes": "Martes",
        "miercoles": "Miércoles",
        "miércoles": "Miércoles",
        "jueves": "Jueves",
        "viernes": "Viernes"
    }

    is_choice = re.search(r"(\s+o\s+|,\s*o\s+)", raw, flags=re.IGNORECASE) is not None

    if is_choice:
        parts = re.split(r"\s+o\s+|,\s*o\s+", raw, flags=re.IGNORECASE)
        days = []
        for p in parts:
            norm = normalize_text(p)
            for k, v in mapa.items():
                if k in norm:
                    days.append(v)
        out, seen = [], set()
        for d in days:
            if d not in seen:
                seen.add(d)
                out.append(d)
        return {"type": "choice", "days": out}

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    days = []
    for p in (parts if parts else [raw]):
        norm = normalize_text(p)
        for k, v in mapa.items():
            if k in norm:
                days.append(v)
    out, seen = [], set()
    for d in days:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return {"type": "fixed", "days": out}


# ---------------------------------------------------------
# Saint-Laguë
# ---------------------------------------------------------
def saint_lague_allocate(
    weights: Dict[str, int],
    seats: int,
    current: Optional[Dict[str, int]] = None,
    caps: Optional[Dict[str, int]] = None,
    rng: Optional[random.Random] = None
) -> Dict[str, int]:
    """
    Asigna 'seats' unidades con método Sainte-Laguë.
    - weights: dict equipo->peso (demanda restante)
    - current: dict equipo->asignado actual (para calcular divisor 2a+1)
    - caps: dict equipo->máximo adicional permitido (para no pasarse)
    - rng: si hay empate, desempata con rng (controlado por variant_seed)
    """
    if seats <= 0 or not weights:
        return {k: 0 for k in weights.keys()}

    rng = rng or random.Random(0)
    current = current or {k: 0 for k in weights.keys()}
    alloc = {k: 0 for k in weights.keys()}

    def quotient(k: str) -> float:
        a = current.get(k, 0) + alloc.get(k, 0)
        w = weights.get(k, 0)
        if w <= 0:
            return -1e18
        return w / (2 * a + 1)

    for _ in range(seats):
        cand = []
        for k, w in weights.items():
            if w <= 0:
                continue
            if caps is not None and alloc[k] >= caps.get(k, 0):
                continue
            cand.append((quotient(k), k))
        if not cand:
            break

        cand.sort(key=lambda x: x[0], reverse=True)
        best_q = cand[0][0]
        tied = [k for (q, k) in cand if abs(q - best_q) < 1e-12]

        winner = tied[0] if len(tied) == 1 else rng.choice(tied)
        alloc[winner] += 1

    return alloc


# ---------------------------------------------------------
# Heurística para elegir día en reglas "o"
# ---------------------------------------------------------
def choose_flexible_day(
    opts: List[str],
    per: int,
    hard_limit: int,
    load_by_day: Dict[str, int],
    mode: str,
    rng: random.Random
) -> Optional[str]:
    """
    mode:
      - "holgura": maximiza holgura (hard_limit - (load + per))
      - "equilibrar": manda al día con menor carga total (load)
      - "aleatorio": aleatorio con seed
    """
    opts = [d for d in opts if d in load_by_day]
    if not opts:
        return None

    if mode == "aleatorio":
        return rng.choice(opts)

    if mode == "equilibrar":
        return min(opts, key=lambda d: (load_by_day[d], rng.random()))

    return max(opts, key=lambda d: ((hard_limit - (load_by_day[d] + per)), rng.random()))


# ---------------------------------------------------------
# Motor: distribución
# ---------------------------------------------------------
def compute_distribution_from_excel(
    equipos_df,
    parametros_df,
    df_capacidades,
    cupos_reserva=2,
    ignore_params=False,
    variant_seed: Optional[int] = None,
    variant_mode: str = "holgura",
):
    """
    Reglas clave:
      - Reserva diaria: RESERVA_OBLIGATORIA (fila "Cupos libres")
      - hard_limit = cap_total_real - reserva

    ✅ Regla base SIEMPRE (con o sin parámetros):
        per >= 2 => mínimo diario base = 2
        per == 1 => mínimo diario base = 1

    ✅ Si ignore_params=False (parámetros activos):
        mínimo objetivo del Excel también aplica:
        min_obj = max(base_min, minimo_excel)  (y min_obj <= per)

    ✅ Déficit (solo cuando parámetros activos, porque ahí importa el mínimo de Excel):
        deficit = max(0, min_obj - asignado)

    ✅ % uso semanal:
        cupos_semana / (dotacion*5) * 100

    ✅ Promedio de cupos diarios:
        round_half_up(cupos_semana/5)
    """
    rng = random.Random(variant_seed if variant_seed is not None else 0)

    rows: List[Dict[str, Any]] = []
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    deficit_report: List[Dict[str, Any]] = []
    audit = {
        "variant_seed": variant_seed,
        "variant_mode": variant_mode,
        "full_day_choices": [],
        "weekly_summary": [],
        "infeasible_days": []
    }

    if equipos_df is None or equipos_df.empty:
        return [], [], audit, {"score": 1e18, "details": {}}

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

    col_piso = next((c for c in equipos_df.columns if "piso" in normalize_text(c)), None)
    col_equipo = next((c for c in equipos_df.columns if "equipo" in normalize_text(c)), None)
    col_personas = next((c for c in equipos_df.columns
                         if "personas" in normalize_text(c) or "dotacion" in normalize_text(c)
                         or "dotación" in normalize_text(c) or "total" in normalize_text(c)), None)
    col_minimos = next((c for c in equipos_df.columns if "minimo" in normalize_text(c) or "mínimo" in normalize_text(c)), None)

    if not (col_piso and col_equipo and col_personas and col_minimos):
        return [], [], audit, {"score": 1e18, "details": {"error": "Faltan columnas clave"}}

    capacidad_pisos: Dict[str, int] = {}
    RESERVA_OBLIGATORIA = int(cupos_reserva) if cupos_reserva is not None else 2

    if not df_capacidades.empty:
        for _, row in df_capacidades.iterrows():
            try:
                key_piso = extract_clean_number_str(row.iloc[0])
                if key_piso is None:
                    continue
                cap_val = int(float(str(row.iloc[1]).replace(",", ".")))
                if cap_val > 0:
                    capacidad_pisos[key_piso] = cap_val
            except Exception:
                continue

    reglas_full_day: Dict[str, Dict[str, Any]] = {}
    if (not ignore_params) and (not parametros_df.empty):
        col_param = next((c for c in parametros_df.columns
                          if "criterio" in normalize_text(c) or "parametro" in normalize_text(c) or "parámetro" in normalize_text(c)), None)
        col_valor = next((c for c in parametros_df.columns if "valor" in normalize_text(c)), None)

        if col_param and col_valor:
            for _, row in parametros_df.iterrows():
                p = str(row.get(col_param, "")).strip().lower()
                v = row.get(col_valor, "")
                if "dia completo" in p or "día completo" in p:
                    nm = re.split(r"d[ií]a completo\s+", p)[-1].strip()
                    rule = parse_full_day_rule(v)
                    if rule["type"] != "none" and len(rule["days"]) > 0:
                        reglas_full_day[normalize_text(nm)] = rule

    total_sq_error = 0.0
    total_deficit = 0
    total_recortes_full_day = 0
    n_eval = 0

    pisos_unicos = equipos_df[col_piso].dropna().unique()

    for piso_raw in pisos_unicos:
        piso_str = extract_clean_number_str(piso_raw)
        if not piso_str:
            continue

        df_piso = equipos_df[equipos_df[col_piso] == piso_raw].copy()
        if df_piso.empty:
            continue

        if piso_str in capacidad_pisos:
            cap_total_real = int(capacidad_pisos[piso_str])
        else:
            try:
                cap_total_real = int(df_piso[col_personas].fillna(0).astype(float).sum()) + RESERVA_OBLIGATORIA
            except Exception:
                cap_total_real = RESERVA_OBLIGATORIA

        cap_total_real = max(0, int(cap_total_real))
        hard_limit = max(0, cap_total_real - RESERVA_OBLIGATORIA)

        equipos_info: List[Dict[str, Any]] = []
        for _, r in df_piso.iterrows():
            nm = str(r.get(col_equipo, "")).strip()
            if not nm:
                continue

            try:
                per = int(float(str(r.get(col_personas, 0)).replace(",", ".")))
            except Exception:
                per = 0
            per = max(0, per)

            try:
                mini_raw = int(float(str(r.get(col_minimos, 0)).replace(",", ".")))
            except Exception:
                mini_raw = 0
            mini_raw = max(0, mini_raw)

            # ✅ base min SIEMPRE
            base_min = 0
            if per >= 2:
                base_min = 2
            elif per == 1:
                base_min = 1

            # ✅ mínimo objetivo final:
            # - con parámetros: max(base_min, minimo_excel)
            # - sin parámetros: base_min
            if not ignore_params:
                min_obj = max(base_min, mini_raw)
            else:
                min_obj = base_min

            min_obj = min(per, min_obj)

            equipos_info.append({"eq": nm, "per": per, "min_obj": min_obj, "min_excel": mini_raw, "base_min": base_min})

        weekly_assigned: Dict[str, int] = {info["eq"]: 0 for info in equipos_info}
        weekly_dot: Dict[str, int] = {info["eq"]: int(info["per"]) for info in equipos_info}

        full_day_choice_assignment = {}
        if not ignore_params and reglas_full_day:
            load_by_day = {d: 0 for d in dias_semana}

            for info in equipos_info:
                nm_norm = normalize_text(info["eq"])
                rule = reglas_full_day.get(nm_norm)
                if rule and rule["type"] == "fixed":
                    for d in rule["days"]:
                        if d in dias_semana:
                            load_by_day[d] += info["per"]

            for info in equipos_info:
                nm = info["eq"]
                nm_norm = normalize_text(nm)
                rule = reglas_full_day.get(nm_norm)
                if rule and rule["type"] == "choice":
                    chosen = choose_flexible_day(
                        opts=rule["days"],
                        per=info["per"],
                        hard_limit=hard_limit,
                        load_by_day=load_by_day,
                        mode=variant_mode,
                        rng=rng
                    )
                    if chosen:
                        full_day_choice_assignment[nm_norm] = chosen
                        load_by_day[chosen] += info["per"]
                        audit["full_day_choices"].append({
                            "piso": piso_str,
                            "equipo": nm,
                            "rule": " o ".join(rule["days"]),
                            "chosen_day": chosen,
                            "mode": variant_mode
                        })

        for dia in dias_semana:
            state = []
            for info in equipos_info:
                nm = info["eq"]
                nm_norm = normalize_text(nm)
                per = int(info["per"])
                min_obj = int(info["min_obj"])

                is_full_day = False
                if not ignore_params:
                    rule = reglas_full_day.get(nm_norm)
                    if rule:
                        if rule["type"] == "fixed" and dia in rule["days"]:
                            is_full_day = True
                        elif rule["type"] == "choice" and full_day_choice_assignment.get(nm_norm) == dia:
                            is_full_day = True

                state.append({
                    "eq": nm,
                    "per": per,
                    "min_obj": min_obj,
                    "full_day": is_full_day,
                    "asig": 0
                })

            used = 0

            # A) full-day (solo con parámetros)
            if not ignore_params:
                for t in state:
                    if t["full_day"] and t["per"] > 0:
                        t["asig"] = t["per"]
                        used += t["asig"]

                # recorte si explota hard_limit
                if used > hard_limit:
                    exceso = used - hard_limit
                    total_recortes_full_day += exceso
                    fulls = [t for t in state if t["full_day"] and t["asig"] > 0]
                    while exceso > 0 and fulls:
                        fulls.sort(key=lambda x: x["asig"], reverse=True)
                        fulls[0]["asig"] -= 1
                        used -= 1
                        exceso -= 1
                        fulls = [t for t in fulls if t["asig"] > 0]

            # B) ✅ REGLA 1 SIEMPRE: asignar mínimos base/objetivo antes de Sainte-Laguë
            min_need = {t["eq"]: max(0, min(t["per"], t["min_obj"]) - t["asig"]) for t in state if t["per"] > 0}
            total_need = sum(min_need.values())
            rem_for_mins = max(0, hard_limit - used)

            if total_need > 0:
                if rem_for_mins >= total_need:
                    # alcanza: cubrir mínimos completos
                    for t in state:
                        need = max(0, min(t["per"], t["min_obj"]) - t["asig"])
                        if need > 0:
                            t["asig"] += need
                            used += need
                else:
                    # Si esto pasa y tú estás seguro que "sí alcanza", entonces aquí detectamos inconsistencia de capacidad.
                    # Igual, intentamos priorizar per>=2 y luego per==1.
                    need_ge2 = {t["eq"]: max(0, min(t["per"], t["min_obj"]) - t["asig"])
                                for t in state if t["per"] >= 2}
                    sum_ge2 = sum(need_ge2.values())

                    if rem_for_mins < sum_ge2:
                        audit["infeasible_days"].append({
                            "piso": piso_str,
                            "dia": dia,
                            "hard_limit": int(hard_limit),
                            "required_min_for_ge2": int(sum_ge2),
                            "msg": "No alcanza capacidad para cumplir mínimo base 2 a todos los equipos con >=2 personas."
                        })

                    # repartir rem_for_mins SOLO entre >=2 con Sainte-Laguë
                    weights = {eq: need for eq, need in need_ge2.items() if need > 0}
                    caps = {eq: need for eq, need in need_ge2.items() if need > 0}
                    current = {t["eq"]: t["asig"] for t in state}
                    alloc_ge2 = saint_lague_allocate(weights=weights, seats=rem_for_mins, current=current, caps=caps, rng=rng)
                    for t in state:
                        give = int(alloc_ge2.get(t["eq"], 0))
                        if give > 0:
                            t["asig"] += give
                            used += give

            # C) Reparto del remanente por Sainte-Laguë según demanda restante
            rem = max(0, hard_limit - used)

            weights = {}
            caps = {}
            current_for_divisor = {}
            for t in state:
                remaining_demand = max(0, t["per"] - t["asig"])
                weights[t["eq"]] = remaining_demand
                caps[t["eq"]] = remaining_demand
                current_for_divisor[t["eq"]] = t["asig"]

            alloc_extra = saint_lague_allocate(
                weights=weights,
                seats=rem,
                current=current_for_divisor,
                caps=caps,
                rng=rng
            )

            for t in state:
                t["asig"] += int(alloc_extra.get(t["eq"], 0))

            # scoring proporción
            sum_per = sum(max(0, t["per"]) for t in state)
            if sum_per > 0 and hard_limit > 0:
                for t in state:
                    if t["per"] <= 0:
                        continue
                    target = (t["per"] / sum_per) * hard_limit
                    err = (t["asig"] - target)
                    total_sq_error += err * err
                    n_eval += 1

            # guardar filas + sumar semanal
            for t in state:
                weekly_assigned[t["eq"]] = weekly_assigned.get(t["eq"], 0) + int(t["asig"])
                uso_diario = round((t["asig"] / hard_limit) * 100.0, 2) if hard_limit > 0 else 0.0
                rows.append({
                    "piso": piso_str,
                    "equipo": t["eq"],
                    "dia": dia,
                    "dotacion": int(t["per"]),
                    "cupos": int(t["asig"]),
                    "promedio_cupos_diarios": None,   # se rellena después (por equipo)
                    "% uso diario": float(uso_diario),
                    "% uso semanal": None,            # se rellena después (por equipo)
                })

            # déficit SOLO cuando parámetros activos (porque compara contra min_obj que incluye Excel)
            if not ignore_params:
                for t in state:
                    if t["per"] <= 0:
                        continue
                    deficit = int(max(0, min(t["per"], t["min_obj"]) - t["asig"]))
                    if deficit > 0:
                        total_deficit += deficit
                        deficit_report.append({
                            "piso": piso_str,
                            "equipo": t["eq"],
                            "dia": dia,
                            "dotacion": int(t["per"]),
                            "minimo": int(min(t["per"], t["min_obj"])),
                            "asignado": int(t["asig"]),
                            "deficit": deficit,
                        })

            # cupos libres
            libres = RESERVA_OBLIGATORIA if cap_total_real >= RESERVA_OBLIGATORIA else cap_total_real
            rows.append({
                "piso": piso_str,
                "equipo": "Cupos libres",
                "dia": dia,
                "dotacion": None,
                "cupos": int(libres),
                "promedio_cupos_diarios": None,
                "% uso diario": None,
                "% uso semanal": None,
            })

        # Post: %uso semanal y promedio diario por equipo
        weekly_usage_by_team: Dict[str, float] = {}
        avg_daily_by_team: Dict[str, int] = {}

        for eq, wk_cupos in weekly_assigned.items():
            dot = int(weekly_dot.get(eq, 0))
            weekly_usage_by_team[eq] = round((wk_cupos / (dot * 5)) * 100.0, 2) if dot > 0 else 0.0
            avg_daily_by_team[eq] = round_half_up(wk_cupos / 5.0)

        for r in rows:
            if r.get("piso") != piso_str:
                continue
            eq = r.get("equipo")
            if not eq or normalize_text(eq) == normalize_text("Cupos libres"):
                continue
            r["% uso semanal"] = float(weekly_usage_by_team.get(eq, 0.0))
            r["promedio_cupos_diarios"] = int(avg_daily_by_team.get(eq, 0))

        for eq in weekly_assigned.keys():
            dot = int(weekly_dot.get(eq, 0))
            wk_cupos = int(weekly_assigned.get(eq, 0))
            audit["weekly_summary"].append({
                "piso": piso_str,
                "equipo": eq,
                "dotacion": dot,
                "cupos_semana": wk_cupos,
                "promedio_cupos_diarios": int(avg_daily_by_team.get(eq, 0)),
                "% uso semanal": float(weekly_usage_by_team.get(eq, 0.0)),
            })

    mse = (total_sq_error / max(1, n_eval))
    score = mse + (total_deficit * 50.0) + (total_recortes_full_day * 200.0)

    score_obj = {
        "score": float(score),
        "details": {
            "mse_proporcion": float(mse),
            "total_deficit": int(total_deficit),
            "recortes_full_day": int(total_recortes_full_day),
            "n_eval": int(n_eval),
        }
    }

    return rows, deficit_report, audit, score_obj


def compute_distribution_variants(
    equipos_df,
    parametros_df,
    df_capacidades,
    cupos_reserva=2,
    ignore_params=False,
    n_variants=5,
    variant_seed: int = 42,
    variant_mode: str = "holgura",
):
    variants = []
    for i in range(max(1, int(n_variants))):
        seed_i = int(variant_seed) + i
        rows, deficit, audit, score = compute_distribution_from_excel(
            equipos_df=equipos_df,
            parametros_df=parametros_df,
            df_capacidades=df_capacidades,
            cupos_reserva=cupos_reserva,
            ignore_params=ignore_params,
            variant_seed=seed_i,
            variant_mode=variant_mode
        )
        variants.append({
            "seed": seed_i,
            "mode": variant_mode,
            "rows": rows,
            "deficit_report": deficit,
            "audit": audit,
            "score": score
        })

    variants.sort(key=lambda v: v["score"]["score"])
    return variants
