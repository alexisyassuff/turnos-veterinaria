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
import datetime
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
# compartido entre worker y beat (ver docker-compose.yml).
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
def revisar_turnos_proximos():
    """Busca turnos pendientes dentro de las proximas 24hs y encola su aviso.

    La columna `recordatorio_enviado` evita que un mismo turno se
    reencole en cada corrida (cada 60s) mientras sigue dentro de la
    ventana de 24hs; sin esa marca se mandarian decenas de mails
    repetidos por turno. Se agrega aca con `ADD COLUMN IF NOT EXISTS`
    (sintaxis propia de MariaDB) en vez de en persistencia.py porque es
    una necesidad exclusiva de v3, no del modelo base de v2.
    """
    conexion = _conectar_db()
    try:
        with conexion.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE turnos ADD COLUMN IF NOT EXISTS "
                "recordatorio_enviado TINYINT(1) NOT NULL DEFAULT 0"
            )
            cursor.execute(
                """
                SELECT t.id_turno, v.nombre, d.nombre, m.nombre, t.fecha, t.hora, d.email
                FROM turnos t
                JOIN veterinarios v ON v.id_veterinario = t.id_veterinario
                JOIN mascotas m ON m.id_mascota = t.id_mascota
                JOIN duenos d ON d.id_dueno = m.id_dueno
                WHERE t.estado = 'pendiente'
                  AND t.recordatorio_enviado = 0
                  AND TIMESTAMP(t.fecha, t.hora) BETWEEN NOW() AND NOW() + INTERVAL %s HOUR
                """,
                (VENTANA_AVISO_HORAS,),
            )
            turnos = cursor.fetchall()

            for id_turno, vet, dueno, mascota, fecha, hora, email in turnos:
                enviar_recordatorio.delay(
                    id_turno, vet, dueno, mascota, fecha.isoformat(), _formatear_hora(hora), email
                )
                cursor.execute(
                    "UPDATE turnos SET recordatorio_enviado = 1 WHERE id_turno = %s",
                    (id_turno,),
                )
    finally:
        conexion.close()



@app.task
def enviar_recordatorio(id_turno, vet, dueno, mascota, fecha, hora, email):
    """Manda el mail de recordatorio para un turno puntual via SMTP."""
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


def _formatear_hora(valor):
    # PyMySQL devuelve las columnas TIME como timedelta, no como time.
    if isinstance(valor, datetime.timedelta):
        total_segundos = int(valor.total_seconds())
        horas, resto = divmod(total_segundos, 3600)
        minutos = resto // 60
        return f"{horas:02d}:{minutos:02d}"
    return valor.strftime("%H:%M")
