import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import streamlit as st
import os

def send_reservation_email(to_email, subject, body_html, logo_path="static/logo.png"):
    """
    Envía un correo HTML con el logo incrustado (si es posible mediante URL pública o CID).
    Para simplificar en local, usaremos un diseño HTML limpio.
    """
    # Validar email
    if not to_email or '@' not in to_email:
        print(f"❌ Email inválido: {to_email}")
        return False
    
    # Intentar obtener credenciales de secrets
    try:
        smtp_server = st.secrets["smtp"]["server"]
        smtp_port = int(st.secrets["smtp"]["port"])
        smtp_user = st.secrets["smtp"]["user"]
        smtp_password = st.secrets["smtp"]["password"]
        # Usar 'sender' si está disponible (para Brevo), sino usar smtp_user
        sender_email = st.secrets["smtp"].get("sender", smtp_user)
        print(f"✅ Credenciales SMTP encontradas: servidor={smtp_server}, puerto={smtp_port}, usuario={smtp_user}, remitente={sender_email}")
    except KeyError as e:
        print(f"❌ No se encontró la clave SMTP en secrets: {e}")
        print("💡 Asegúrate de tener configurado en .streamlit/secrets.toml:")
        print("   [smtp]")
        print("   server = 'smtp-relay.brevo.com'  # o tu servidor SMTP")
        print("   port = 587")
        print("   user = 'tu_usuario_smtp'")
        print("   password = 'tu_contraseña_o_api_key'")
        print("   sender = 'tu_email@ejemplo.com'  # Email del remitente (opcional)")
        return False
    except Exception as e:
        print(f"❌ Error al leer credenciales SMTP: {e}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email  # Usar el email del remitente, no el usuario SMTP
    msg["To"] = to_email

    # Diseño HTML Profesional
    # Nota: Las imágenes locales no se ven en correos a menos que se adjunten como CID o estén en un servidor público.
    # Aquí usamos un marcador de posición para el título si no hay imagen pública.
    
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }}
            .header {{ background-color: #00A04A; padding: 20px; text-align: center; color: white; }}
            .content {{ padding: 20px; }}
            .footer {{ background-color: #f9f9f9; padding: 15px; text-align: center; font-size: 12px; color: #888; }}
            h2 {{ margin-top: 0; }}
            ul {{ background: #f0f8ff; padding: 15px; border-radius: 5px; }}
            li {{ list-style: none; padding: 5px 0; }}
        </style>
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
        print(f"📧 Intentando enviar correo a {to_email}...")
        print(f"   Servidor: {smtp_server}:{smtp_port}")
        
        # Crear conexión SMTP
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.set_debuglevel(1)  # Activar debug para ver qué pasa
        server.starttls()
        
        print(f"   Iniciando sesión como {smtp_user}...")
        server.login(smtp_user, smtp_password)
        
        print(f"   Enviando mensaje desde {sender_email} a {to_email}...")
        # Usar sender_email como remitente en sendmail
        server.sendmail(sender_email, to_email, msg.as_string())
        server.quit()
        
        print(f"✅ Correo enviado exitosamente a {to_email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Error de autenticación SMTP: {e}")
        print("💡 Verifica que:")
        print("   1. El usuario y contraseña sean correctos")
        print("   2. Si usas Gmail, habilites 'Contraseñas de aplicaciones'")
        print("   3. Si usas Gmail, desactives 'Verificación en 2 pasos' o uses una contraseña de app")
        return False
    except smtplib.SMTPException as e:
        print(f"❌ Error SMTP: {e}")
        return False
    except Exception as e:
        print(f"❌ Error enviando email a {to_email}: {e}")
        import traceback
        print(traceback.format_exc())
        return False
