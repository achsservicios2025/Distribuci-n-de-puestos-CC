# Añadir esta función en database.py

def delete_reservations_by_ids(conn, reservation_ids, reservation_type="puesto"):
    """
    Elimina reservas por IDs (para puestos o salas)
    reservation_type: "puesto" o "sala"
    """
    if reservation_type == "puesto":
        ws = get_worksheet(conn, "reservations")
    else:
        ws = get_worksheet(conn, "room_reservations")
        
    if ws is None:
        return False
        
    try:
        all_data = ws.get_all_values()
        # Los IDs son los índices de las filas (empezando desde 1, considerando la cabecera)
        # reservation_ids son los índices de las filas a eliminar (empezando desde 0 para la primera fila de datos)
        # Ordenar de mayor a menor para evitar problemas con los índices al eliminar
        rows_to_delete = sorted([int(id) for id in reservation_ids], reverse=True)
        
        for row_index in rows_to_delete:
            # Ajustar el índice: +1 porque get_all_values incluye la cabecera en índice 0
            # y la primera fila de datos es índice 1, pero nuestros IDs son basados en 0 para datos
            ws.delete_rows(row_index + 1)
            
        # Limpiar caché
        if reservation_type == "puesto":
            list_reservations_df.clear()
        else:
            get_room_reservations_df.clear()
            
        return True
    except Exception as e:
        print(f"Error eliminando reservas: {e}")
        return False
