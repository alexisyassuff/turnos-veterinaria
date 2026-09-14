import os
import smtplib
from email.message import EmailMessage

import pymysql
from celery import Celery
from dotenv import load_dotenv

from email_recordatorio import construir_html

load_dotenv()

# --- Configuracion por variables de entorno (mismos defaults que persistencia.py) ---
DB_HOST = os.environ.get("TURNOS_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("TURNOS_DB_PORT", "3306"))
DB_USER = os.environ.get("TURNOS_DB_USER", "root")
DB_PASSWORD = os.environ.get("TURNOS_DB_PASSWORD", "")
DB_NAME = os.environ.get("TURNOS_DB_NAME", "turnos_vet")

BROKER_URL = os.environ.get("TURNOS_CELERY_BROKER_URL", "sqla+sqlite:///celery_broker.db")
BACKEND_URL = os.environ.get("TURNOS_CELERY_BACKEND_URL", "db+sqlite:///celery_results.db")

SMTP_HOST = os.environ.get("TURNOS_SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("TURNOS_SMTP_PORT", "25"))
SMTP_USER = os.environ.get("TURNOS_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("TURNOS_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("TURNOS_SMTP_FROM", "turnos@veterinaria.local")

app = Celery("tasks", broker=BROKER_URL, backend=BACKEND_URL)
app.conf.broker_connection_retry_on_startup = True
app.conf.timezone = "UTC"


def _conectar_db():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        autocommit=True,
    )


@app.task
def enviar_recordatorio(id_turno, vet, dueno, mascota, fecha, hora, email):
    conexion = _conectar_db()
    # chequea que el turno siga pendiente
    try:
        with conexion.cursor() as cursor:
            cursor.execute("SELECT estado FROM turnos WHERE id_turno=%s", (id_turno,))
            fila = cursor.fetchone()
    finally:
        conexion.close()

    if fila is None or fila[0] != "pendiente":
        return

    mensaje = EmailMessage()
    mensaje["Subject"] = f"Recordatorio de turno para {dueno}"
    mensaje["From"] = SMTP_FROM
    mensaje["To"] = email
    mensaje.set_content(
        f"Turno #{id_turno}\n"
        f"Dueno: {dueno}\n"
        f"Mascota: {mascota}\n"
        f"Veterinario: {vet}\n"
        f"Fecha y hora: {fecha} {hora}\n"
        "Favor de comunicarse para confirmar asistencia o cancelar su turno para liberar el lugar. Muchas gracias"
    )
    mensaje.add_alternative(
        construir_html(id_turno, vet, dueno, mascota, fecha, hora),
        subtype="html",
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        if SMTP_USER:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(mensaje)
