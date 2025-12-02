import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound, APIError
import pandas as pd
import datetime
import time

# --- CONFIGURACIÓN DE CONEXIÓN ---
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ---------------------------------------------------------
# Helpers anti "int64 is not JSON serializable"
# ---------------------------------------------------------
def _to_py_scalar(v):
    """
    Convierte tipos de numpy/pandas (int64, float64, Timestamp, NA, etc)
    a tipos nativos de Python / strings seguros para gspread/JSON.
    """
    try:
        # NaN / NA / None-like
        if v is None:
            return ""
        # pandas NA / NaN
        try:
            if pd.isna(v):
                return ""
        except Exception:
            pass

        # numpy / pandas scalars -> python scalar
        if hasattr(v, "item") and callable(getattr(v, "item")):
            try:
                v = v.item()
            except Exception:
                pass

        # timestamps / dates
        if isinstance(v, (datetime.datetime, datetime.date)):
            return v.isoformat()

        return v
    except Exception:
        # Último recurso: string
        return str(v)

def _sanitize_row(row):
    """Sanitiza una fila

    msg = []
    if "Reservas" in option or "TODO" in option:
        ws = get_worksheet(conn, "reservations")
        if ws:
            ws.clear()
            ws.append_row(["user_name", "user_email", "piso", "reservation_date", "team_area", "created_at"])
            list_reservations_df.clear()
            msg.append("Reservas eliminadas")
        
        ws2 = get_worksheet(conn, "room_reservations")
        if ws2:
            ws2.clear()
            ws2.append_row(["user_name", "user_email", "piso", "room_name", "reservation_date", "start_time", "end_time", "created_at"])
            get_room_reservations_df.clear()
            msg.append("Salas eliminadas")
        
    if "Distribución" in option or "TODO" in option:
        ws = get_worksheet(conn, "distribution")
        if ws:
            ws.clear()
            ws.append_row(["piso", "equipo", "dia", "cupos", "pct", "created_at"])
            read_distribution_df.clear()
            msg.append("Distribución eliminada")
        

    return ", ".join(msg) + "."
