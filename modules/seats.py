# seats.py
import pandas as pd
import re
import random
from typing import Any, Dict, List, Optional, Tuple


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
    - weights: dict equipo->peso
    - current: dict equipo->asignado actual (para divisor 2a+1)
    - caps: dict equipo->máximo adicional permitido
    - rng: desempate controlado por seed
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
# Helpers nuevos: desglose semanal -> diario con restricciones
# ---------------------------------------------------------
def _even_week_split(total_week: int, days: List[str], rng: random.Random) -> Dict[str, int]:
    """
    Reparte un total semanal en días lo más parejo posible:
      base = total_week // 5, resto = total_week % 5
    y distribuye el resto en días (mezclados por seed para variantes).
    """
    d = {day: 0 for day in days}
    if total_week <= 0:
        return d
    base = total_week // len(days)
    rem = total_week % len(days)
    for day in days:
        d[day] = base
    order = list(days)
    rng.shuffle(order)
    for i in range(rem):
        d[order[i]] += 1
    return d


def _apply_full_day_fixed(
    day_alloc: Dict[str, Dict[str, int]],
    eq: str,
    per: int,
    fixed_days: List[str],
    days: List[str]
) -> None:
    """Fija asignación diaria completa (per) en los días indicados."""
    for day in fixed_days:
        if day in days:
            day_alloc[day][eq] = max(day_alloc[day].get(eq, 0), int(per))


def _apply_full_day_choice(
    day_alloc: Dict[str, Dict[str, int]],
    eq: str,
    per: int,
    chosen_day: Optional[str],
    days: List[str]
) -> None:
    """Fija asignación diaria completa (per) en el día elegido (regla 'o')."""
    if chosen_day and chosen_day in days:
        day_alloc[chosen_day][eq] = max(day_alloc[chosen_day].get(eq, 0), int(per))


def _ensure_minimums_per_day(
    day_alloc: Dict[str, Dict[str, int]],
    eq: str,
    per: int,
    mini: int,
    days: List[str]
) -> None:
    """
    Aplica mínimos diarios: asignación diaria >= min(per, mini).
    (Como en tu lógica original: si per>=2, mini>=2)
    """
    target = int(min(per, mini))
    if target <= 0:
        return
    for day in days:
        cur = int(day_alloc[day].get(eq, 0))
        if cur < target:
            day_alloc[day][eq] = target


def _cap_day_and_collect_deficit(
    day_alloc: Dict[str, Dict[str, int]],
    per_map: Dict[str, int],
    min_map: Dict[str, int],
    hard_limit: int,
    ignore_params: bool,
    days: List[str],
    total_recortes_full_day_acc: List[int],
    deficit_report: List[Dict[str, Any]],
    piso_str: str,
    FORMULA_EQUIDAD: str,
    EXPLICACION_EQUIDAD: str,
) -> None:
    """
    Respeta capacidad diaria (hard_limit) recortando si se excede.
    Luego reporta déficit (solo si ignore_params=False), 1 vez por día/equipo.
    """
    for day in days:
        alloc_day = day_alloc[day]
        total = sum(int(v) for v in alloc_day.values())
        if total > hard_limit:
            exceso = total - hard_limit
            # Recorte simple: recortar 1 a 1 desde el que más tiene.
            # (Equivalente a tu recorte de full_day, pero generalizado al día)
            while exceso > 0 and alloc_day:
                # ordena por asignación desc
                items = sorted(alloc_day.items(), key=lambda kv: kv[1], reverse=True)
                k, v = items[0]
                if v <= 0:
                    break
                alloc_day[k] = v - 1
                exceso -= 1
                total_recortes_full_day_acc[0] += 1

        # Déficit (solo con params)
        if not ignore_params:
            for eq, per in per_map.items():
                if per <= 0:
                    continue
                asig = int(alloc_day.get(eq, 0))
                deficit = int(max(0, per - asig))
                if deficit > 0:
                    deficit_report.append({
                        "piso": piso_str,
                        "equipo": eq,
                        "dia": day,
                        "dotacion": int(per),
                        "minimo": int(min_map.get(eq, 0)),
                        "asignado": int(asig),
                        "deficit": deficit,
                        "formula": FORMULA_EQUIDAD,
                        "explicacion": EXPLICACION_EQUIDAD
                    })


def _fill_remaining_by_day_saint_lague(
    day_alloc: Dict[str, Dict[str, int]],
    per_map: Dict[str, int],
    hard_limit: int,
    days: List[str],
    rng: random.Random,
) -> None:
    """
    Para cada día: reparte cupos restantes (hard_limit - usados) con Saint-Laguë
    sobre demanda restante (per - asig).
    (Esto conserva el “corazón” Saint-Laguë, pero ahora tras un pre-diseño semanal).
    """
    for day in days:
        alloc_day = day_alloc[day]
        used = sum(int(v) for v in alloc_day.values())
        rem = max(0, hard_limit - used)
        if rem <= 0:
            continue

        weights = {}
        caps = {}
        current = {}
        for eq, per in per_map.items():
            cur = int(alloc_day.get(eq, 0))
            remaining = max(0, int(per) - cur)
            weights[eq] = remaining
            caps[eq] = remaining
            current[eq] = cur

        extra = saint_lague_allocate(weights=weights, seats=rem, current=current, caps=caps, rng=rng)
        for eq, add in extra.items():
            if add:
                alloc_day[eq] = int(alloc_day.get(eq, 0)) + int(add)


# ---------------------------------------------------------
# Motor: distribución (semanal -> diario)
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
    Nuevo enfoque:
      1) Se calcula capacidad diaria usable: hard_limit = cap_total_real - reserva
      2) Se hace un plan semanal por equipo (en grande) y se desglosa a días
      3) Si ignore_params=False se aplican:
           - día completo (fixed / choice)
           - mínimos diarios
           - recortes para hard_limit diario
         y se rellena el remanente diario con Sainte-Laguë
      4) Si ignore_params=True:
           - no se aplican reglas de parámetros
           - se reparte con Sainte-Laguë (en la práctica, en el relleno diario puro)
    """
    rng = random.Random(variant_seed if variant_seed is not None else 0)

    rows: List[Dict[str, Any]] = []
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    deficit_report: List[Dict[str, Any]] = []
    audit = {
        "variant_seed": variant_seed,
        "variant_mode": variant_mode,
        "full_day_choices": [],
        "weekly_summary": []
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

    FORMULA_EQUIDAD = "Asignación objetivo ≈ (Dotación_equipo / Dotación_total_piso) × Capacidad_usable_día"
    EXPLICACION_EQUIDAD = (
        "Capacidad usable por día: Capacidad_usable = Capacidad_total - Reserva.\n"
        "Si ignore_params=False: se aplican restricciones hard (día completo y mínimos) y el remanente se reparte "
        "proporcionalmente con Sainte-Laguë sobre la demanda restante.\n"
        "Si ignore_params=True: se deshabilitan parámetros y se hace solo reparto proporcional con Sainte-Laguë + reserva."
    )

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

            mini = mini_raw
            if per >= 2:
                mini = max(2, mini_raw)
            mini = min(per, mini)

            equipos_info.append({"eq": nm, "per": per, "min": mini})

        if not equipos_info:
            continue

        per_map = {info["eq"]: int(info["per"]) for info in equipos_info}
        min_map = {info["eq"]: int(info["min"]) for info in equipos_info}
        weekly_dot = {info["eq"]: int(info["per"]) for info in equipos_info}

        # ---------------------------------------------
        # (1) Plan semanal “en grande” (base) -> split diario parejo
        # ---------------------------------------------
        # Aquí definimos un total semanal “deseado” por equipo:
        # En este diseño, apuntamos a que (en promedio) el equipo reciba aprox. per cada día,
        # pero SIEMPRE limitado por capacidad diaria hard_limit (se arregla después por día).
        # weekly_target = per * 5
        weekly_target = {eq: int(per) * 5 for eq, per in per_map.items()}

        # Base daily allocation (sin parámetros): split parejo de weekly_target a 5 días.
        day_alloc: Dict[str, Dict[str, int]] = {d: {} for d in dias_semana}
        for eq, wk in weekly_target.items():
            split = _even_week_split(wk, dias_semana, rng=rng)
            for d in dias_semana:
                day_alloc[d][eq] = int(split[d])

        # ---------------------------------------------
        # (2) Si hay parámetros: aplicar día completo y "o" (choice)
        # ---------------------------------------------
        full_day_choice_assignment: Dict[str, str] = {}
        if not ignore_params and reglas_full_day:
            load_by_day = {d: 0 for d in dias_semana}

            # Carga inicial estimada por fixed (para heurística de "o")
            for info in equipos_info:
                nm_norm = normalize_text(info["eq"])
                rule = reglas_full_day.get(nm_norm)
                if rule and rule["type"] == "fixed":
                    for d in rule["days"]:
                        if d in dias_semana:
                            load_by_day[d] += int(info["per"])

            # Elegir day para "choice"
            for info in equipos_info:
                nm = info["eq"]
                nm_norm = normalize_text(nm)
                rule = reglas_full_day.get(nm_norm)
                if rule and rule["type"] == "choice":
                    chosen = choose_flexible_day(
                        opts=rule["days"],
                        per=int(info["per"]),
                        hard_limit=hard_limit,
                        load_by_day=load_by_day,
                        mode=variant_mode,
                        rng=rng
                    )
                    if chosen:
                        full_day_choice_assignment[nm_norm] = chosen
                        load_by_day[chosen] += int(info["per"])
                        audit["full_day_choices"].append({
                            "piso": piso_str,
                            "equipo": nm,
                            "rule": " o ".join(rule["days"]),
                            "chosen_day": chosen,
                            "mode": variant_mode
                        })

            # Aplicar fixed/choice como asignación diaria completa (per)
            for info in equipos_info:
                eq = info["eq"]
                per = int(info["per"])
                nm_norm = normalize_text(eq)
                rule = reglas_full_day.get(nm_norm)

                if rule and rule["type"] == "fixed":
                    _apply_full_day_fixed(day_alloc, eq, per, rule["days"], dias_semana)
                elif rule and rule["type"] == "choice":
                    chosen = full_day_choice_assignment.get(nm_norm)
                    _apply_full_day_choice(day_alloc, eq, per, chosen, dias_semana)

            # Aplicar mínimos diarios
            for info in equipos_info:
                _ensure_minimums_per_day(day_alloc, info["eq"], int(info["per"]), int(info["min"]), dias_semana)

        # ---------------------------------------------
        # (3) Respetar hard_limit diario + reportar déficit (con params)
        # ---------------------------------------------
        total_recortes_acc = [0]
        _cap_day_and_collect_deficit(
            day_alloc=day_alloc,
            per_map=per_map,
            min_map=min_map,
            hard_limit=hard_limit,
            ignore_params=ignore_params,
            days=dias_semana,
            total_recortes_full_day_acc=total_recortes_acc,
            deficit_report=deficit_report,
            piso_str=piso_str,
            FORMULA_EQUIDAD=FORMULA_EQUIDAD,
            EXPLICACION_EQUIDAD=EXPLICACION_EQUIDAD
        )
        total_recortes_full_day += int(total_recortes_acc[0])

        # ---------------------------------------------
        # (4) Rellenar remanente diario con Sainte-Laguë
        #     (para acercarse a proporcionalidad en cada día)
        # ---------------------------------------------
        # Con ignore_params=True, esto es esencialmente el único "ajuste" (no hay reglas).
        _fill_remaining_by_day_saint_lague(
            day_alloc=day_alloc,
            per_map=per_map,
            hard_limit=hard_limit,
            days=dias_semana,
            rng=rng
        )

        # ---------------------------------------------
        # (5) Construir rows, métricas % y sumar semanal
        # ---------------------------------------------
        weekly_assigned = {eq: 0 for eq in per_map.keys()}

        for dia in dias_semana:
            alloc_day = day_alloc[dia]

            # Proporcionalidad (error vs target del día) para score
            sum_per = sum(max(0, per_map[eq]) for eq in per_map.keys())
            if sum_per > 0 and hard_limit > 0:
                for eq, per in per_map.items():
                    if per <= 0:
                        continue
                    target = (per / sum_per) * hard_limit
                    asig = int(alloc_day.get(eq, 0))
                    err = (asig - target)
                    total_sq_error += err * err
                    n_eval += 1

            # filas por equipo (solo si asig>0)
            for eq, per in per_map.items():
                asig = int(alloc_day.get(eq, 0))
                weekly_assigned[eq] += asig
                if asig <= 0:
                    continue
                uso_diario = round((asig / hard_limit) * 100.0, 2) if hard_limit > 0 else 0.0
                rows.append({
                    "piso": piso_str,
                    "equipo": eq,
                    "dia": dia,
                    "dotacion": int(per),
                    "cupos": int(asig),
                    "% uso diario": float(uso_diario),
                    "% uso semanal": None,
                })

            # Cupos libres (reserva diaria)
            libres = RESERVA_OBLIGATORIA if cap_total_real >= RESERVA_OBLIGATORIA else cap_total_real
            rows.append({
                "piso": piso_str,
                "equipo": "Cupos libres",
                "dia": dia,
                "dotacion": None,
                "cupos": int(libres),
                "% uso diario": None,
                "% uso semanal": None,
            })

        # % uso semanal por equipo
        weekly_usage_by_team: Dict[str, float] = {}
        for eq, wk_cupos in weekly_assigned.items():
            dot = int(weekly_dot.get(eq, 0))
            weekly_usage_by_team[eq] = round((wk_cupos / (dot * 5)) * 100.0, 2) if dot > 0 else 0.0

        for r in rows:
            if r.get("piso") != piso_str:
                continue
            eq = r.get("equipo")
            if not eq or normalize_text(eq) == normalize_text("Cupos libres"):
                continue
            r["% uso semanal"] = float(weekly_usage_by_team.get(eq, 0.0))

        # summary para PDF/UI
        for eq in weekly_assigned.keys():
            dot = int(weekly_dot.get(eq, 0))
            wk_cupos = int(weekly_assigned.get(eq, 0))
            avg_daily = round(wk_cupos / 5.0, 2)
            audit["weekly_summary"].append({
                "piso": piso_str,
                "equipo": eq,
                "dotacion": dot,
                "cupos_semana": wk_cupos,
                "cupos_promedio_diario": avg_daily,
                "% uso semanal": float(weekly_usage_by_team.get(eq, 0.0)),
            })

        # total déficit para score
        if not ignore_params and deficit_report:
            # acumular deficit de este piso
            for d in deficit_report:
                if d.get("piso") == piso_str:
                    total_deficit += int(d.get("deficit", 0) or 0)

    mse = (total_sq_error / max(1, n_eval))
    score = mse + (total_deficit * 50.0) + (total_recortes_full_day * 200.0)

    score_obj = {
        "score": float(score),
        "details": {
            "mse_proporcion": float(mse),
            "total_deficit": int(total_deficit),
            "recortes_full_day": int(total_recortes_full_day),
            "n_eval": int(n_eval)
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
