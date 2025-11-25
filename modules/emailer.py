import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import streamlit as st
import os

def send_reservation_email(to_email, subject, body_html, logo_path="static/logo.png"):
    """
    Envía un correo HTML usando el SENDER verificado para evitar rechazos de Brevo.
    """
    try:
        smtp_server = st.secrets["smtp"]["server"]
        smtp_port = st.secrets["smtp"]["port"]
        smtp_user = st.secrets["smtp"]["user"] # Usuario de autenticación Brevo
        smtp_password = st.secrets["smtp"]["password"]
        
        # OBTENEMOS LA DIRECCIÓN VERIFICADA (Ej: achsservicios.2025@gmail.com)
        # Usamos .get para tomar el valor 'sender' de secrets.toml
        verified_sender = st.secrets["smtp"].get("sender") 
        if not verified_sender:
             verified_sender = smtp_user # Fallback a usuario Brevo si no se encuentra sender
        
    except KeyError:
        print("ERROR SMTP: No se encontraron todas las credenciales SMTP en secrets.toml")
        return False
    except Exception as e:
        print(f"Error al cargar secretos SMTP: {e}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    
    # CORRECCIÓN 1: Usamos la dirección VERIFICADA para el campo "From"
    display_name = "ACHS Servicios - Gestión"
    msg["From"] = f"{display_name} <{verified_sender}>"
    
    msg["To"] = to_email

    # ... (Contenido HTML omitido para brevedad) ...
    html_content = f"""
    <html>
    <head>
    ...
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>ACHS Servicios</h1>
                <p>Confirmación de Reserva</p>
            </div>
            <div class="content">
                {body_html}
            </div>
            <div class="footer">
                <p>Este es un mensaje automático, por favor no responder.</p>
                <p>© ACHS Servicios - Gestión de Espacios</p>
            </div>
        </div>
    </body>
    </html>
    """
    part = MIMEText(html_content, "html")
    msg.attach(part)

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            
            # CORRECCIÓN 2 (CRÍTICA): Usamos la dirección VERIFICADA para el sobre de envío
            # Esto resuelve el error "sender... is not valid"
            server.sendmail(verified_sender, to_email, msg.as_string()) 
        return True
    except Exception as e:
        print(f"Error enviando email: Falló la conexión SMTP. Detalle: {e}")
        return False

