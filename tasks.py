"""Tareas asincronicas de turnos veterinaria - v3.

Celery con SQLite como broker (no Redis, para no sumar una dependencia
externa mas). Un solo rol, sin beat:

    celery -A tasks worker --loglevel=info   # ejecuta las tareas

No hay sondeo periodico de la base. Cada turno agenda su propio aviso una
unica vez, en el momento en que se crea: persistencia.py calcula el eta
(fecha/hora del turno menos la ventana de aviso) y encola
enviar_recordatorio directamente con ese eta (ver _agendar_recordatorio en
persistencia.py). Celery se limita a "despertar" en ese momento exacto,
sin ningun proceso consultando la base mientras tanto.

Dos flechas separadas entre este archivo y persistencia.py, cada una con
su motivo: persistencia.py -> broker de Celery para encolar (sabe que
existe la tarea "tasks.enviar_recordatorio" por nombre, nada mas - no
importa este modulo); y enviar_recordatorio -> MariaDB, ya del lado de
Celery, para revisar el estado actual del turno antes de mandar el mail
(el eta se calculo horas o dias antes; para cuando la tarea corre, el
turno pudo haberse confirmado o cancelado).
"""
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

# Broker y backend de Celery. Local por defecto (archivo en el directorio
# actual); en Docker se pisan por env var para apuntar al volumen
# compartido con persistencia.py, que encola en el mismo broker (ver
# docker-compose.yml).
BROKER_URL = os.environ.get("TURNOS_CELERY_BROKER_URL", "sqla+sqlite:///celery_broker.db")
BACKEND_URL = os.environ.get("TURNOS_CELERY_BACKEND_URL", "db+sqlite:///celery_results.db")

# Notificacion por mail al dueno real del turno (columna duenos.email,
# agregada en persistencia.py).
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
    """Manda el mail de recordatorio para un turno puntual via SMTP.

    El eta con el que se agendo esta tarea se calculo en persistencia.py
    al momento de crear el turno, potencialmente horas o dias antes de
    que esto corra. En el medio el dueno pudo haber confirmado o
    cancelado, asi que no confiamos en los datos que se le pasaron a la
    tarea al encolarla - se vuelve a consultar el estado actual antes de
    mandar nada.
    """
    conexion = _conectar_db()
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
    # Alternativa HTML (diseño en email_recordatorio.py). Se agrega como
    # add_alternative -> queda en un multipart/alternative junto con el
    # texto plano de arriba; los clientes que soportan HTML muestran esta
    # version, el resto cae al texto plano.
    mensaje.add_alternative(
        construir_html(id_turno, vet, dueno, mascota, fecha, hora),
        subtype="html",
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        if SMTP_USER:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(mensaje)
