"""Proceso de persistencia de turnos veterinaria - v2.

Corre como proceso separado del servidor principal. Se comunica por
stdin/stdout (pipes) linea por linea, mismo estilo de protocolo que la
capa de red (COMANDO|arg1|arg2|...). Es el unico proceso que abre
conexion a MariaDB; el servidor nunca arma SQL directamente.

stdout se usa exclusivamente para el protocolo. Logs y errores de
arranque van a stderr.
"""
import argparse
import datetime
import os
import sys

import pymysql
from celery import Celery

# Duracion estandar de una consulta: cada turno "ocupa" un bloque de este
# tamaño para el mismo veterinario. Se valida en Python (no como constraint
# de MariaDB) porque un rango de superposicion no se puede expresar con un
# UNIQUE declarativo — el UNIQUE(id_veterinario, fecha, hora) que ya existe
# sigue ahi y sigue cubriendo el caso de horario identico.
DURACION_TURNO_MINUTOS = 40

# Cuantas horas antes del turno se manda el recordatorio por mail. Vive aca
# (y no en tasks.py) porque es lo unico que la necesita: el eta se calcula
# en el momento de crear el turno, no cuando corre la tarea.
VENTANA_AVISO_HORAS = 24

# Cliente Celery liviano: solo encola (send_task por nombre), no importa
# tasks.py ni conoce como se manda el mail. Evita que este proceso -que
# hasta ahora solo sabia hablar SQL- arrastre smtplib/dotenv/config de mail
# nada mas que para poder agendar un aviso. Mismo nombre de variable y
# mismo default que usa tasks.py, para que ambos apunten al mismo broker
# sin configuracion extra en local.
CELERY_BROKER_URL = os.environ.get("TURNOS_CELERY_BROKER_URL", "sqla+sqlite:///celery_broker.db")
celery_cliente = Celery(broker=CELERY_BROKER_URL)


def main():
    parser = argparse.ArgumentParser(description="Proceso de persistencia de turnos veterinaria")
    parser.add_argument("--db-host", default=os.environ.get("TURNOS_DB_HOST", "127.0.0.1"))
    parser.add_argument("--db-port", type=int, default=int(os.environ.get("TURNOS_DB_PORT", "3306")))
    parser.add_argument("--db-user", default=os.environ.get("TURNOS_DB_USER", "root"))
    parser.add_argument("--db-password", default=os.environ.get("TURNOS_DB_PASSWORD", ""))
    parser.add_argument("--db-name", default=os.environ.get("TURNOS_DB_NAME", "turnos_vet"))
    args = parser.parse_args()

    try:
        conexion = conectar(args)
    except pymysql.err.MySQLError as error:
        print(f"No se pudo conectar a la base de datos: {error}", file=sys.stderr, flush=True)
        sys.exit(1)

    print(
        f"Proceso de persistencia listo, conectado a {args.db_host}:{args.db_port}/{args.db_name}",
        file=sys.stderr,
        flush=True,
    )

    for linea in sys.stdin:
        linea = linea.rstrip("\n")
        if not linea:
            continue

        with conexion.cursor() as cursor:
            try:
                respuesta = procesar_linea(cursor, linea)
            except pymysql.err.MySQLError as error:
                respuesta = f"ERROR|fallo de base de datos: {error}"

        if respuesta is None:
            print("OK|CHAU", flush=True)
            break

        print(respuesta, flush=True)

    conexion.close()


def conectar(args):
    # El esquema (tablas, FKs, constraints) ya no se crea desde aca: vive en
    # db/schema.sql, aplicado por MariaDB al inicializar su volumen (patron
    # docker-entrypoint-initdb.d) o a mano en desarrollo local (ver README).
    # Este proceso asume que la base y las tablas ya existen.
    return pymysql.connect(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        database=args.db_name,
        autocommit=True,
    )



def procesar_linea(cursor, linea):
    partes = linea.split("|")
    comando = partes[0].upper()

    if comando == "DB_CREAR_TURNO":
        return manejar_crear_turno(cursor, partes)
    if comando == "DB_LISTAR_TURNOS":
        return manejar_listar_turnos(cursor)
    if comando == "DB_CONFIRMAR":
        return manejar_cambiar_estado(cursor, partes, "confirmado")
    if comando == "DB_CANCELAR":
        return manejar_cambiar_estado(cursor, partes, "cancelado")
    if comando == "DB_SALIR":
        return None

    return "ERROR|comando de persistencia desconocido"


def manejar_crear_turno(cursor, partes):
    if len(partes) != 7:
        return "ERROR|formato invalido, se esperan 6 argumentos"
    _, vet, dueno, email, mascota, fecha, hora = partes

    try:
        fecha_valor = datetime.date.fromisoformat(fecha)
        hora_valor = datetime.datetime.strptime(hora, "%H:%M").time()
    except ValueError:
        return "ERROR|formato invalido, use fecha AAAA-MM-DD y hora HH:MM"

    id_dueno = _obtener_o_crear(cursor, "duenos", "id_dueno", {"nombre": dueno}, extra={"email": email})
    id_mascota = _obtener_o_crear(
        cursor, "mascotas", "id_mascota", {"id_dueno": id_dueno, "nombre": mascota}
    )
    id_vet = _obtener_o_crear(cursor, "veterinarios", "id_veterinario", {"nombre": vet})

    cursor.execute(
        "SELECT hora FROM turnos WHERE id_veterinario=%s AND fecha=%s",
        (id_vet, fecha_valor),
    )
    minutos_nuevo = hora_valor.hour * 60 + hora_valor.minute
    for (hora_existente,) in cursor.fetchall():
        if abs(minutos_nuevo - _hora_a_minutos(hora_existente)) < DURACION_TURNO_MINUTOS:
            return "ERROR|ese veterinario ya tiene un turno que se superpone en ese horario (bloque de 40 minutos)"

    try:
        cursor.execute(
            "INSERT INTO turnos (id_veterinario, id_mascota, fecha, hora) VALUES (%s, %s, %s, %s)",
            (id_vet, id_mascota, fecha_valor, hora_valor),
        )
    except pymysql.err.IntegrityError:
        return "ERROR|ese veterinario ya tiene un turno en esa fecha y hora"

    id_turno = cursor.lastrowid
    _agendar_recordatorio(id_turno, vet, dueno, mascota, fecha_valor, hora_valor, email)
    return f"OK|{id_turno}"


def _agendar_recordatorio(id_turno, vet, dueno, mascota, fecha_valor, hora_valor, email):
    """Encola enviar_recordatorio para que dispare solo, en su momento.

    El eta se calcula con un datetime *aware* (con tzinfo del sistema) a
    proposito: si se le pasa uno naive a Celery, lo interpreta segun el
    timezone configurado del lado del worker (UTC en tasks.py), tratando
    la hora local del turno como si ya fuera UTC y desfasando el aviso.
    Con un datetime aware no hay ambiguedad que resolver.

    Si el turno queda a menos de VENTANA_AVISO_HORAS de haberse creado, el
    eta cae en el pasado y Celery ejecuta la tarea de inmediato -
    comportamiento correcto, no hace falta caso especial.
    """
    zona_local = datetime.datetime.now().astimezone().tzinfo
    momento_turno = datetime.datetime.combine(fecha_valor, hora_valor, tzinfo=zona_local)
    eta = momento_turno - datetime.timedelta(hours=VENTANA_AVISO_HORAS)

    try:
        celery_cliente.send_task(
            "tasks.enviar_recordatorio",
            args=[id_turno, vet, dueno, mascota, fecha_valor.isoformat(), hora_valor.strftime("%H:%M"), email],
            eta=eta,
        )
    except Exception as error:
        # No tiramos abajo la creacion del turno (ya esta commiteada en
        # MariaDB) por un problema de Celery/broker. Se loguea y listo.
        print(f"No se pudo agendar el recordatorio del turno {id_turno}: {error}", file=sys.stderr, flush=True)


def manejar_listar_turnos(cursor):
    cursor.execute(
        """
        SELECT t.id_turno, v.nombre, d.nombre, m.nombre, t.fecha, t.hora, t.estado
        FROM turnos t
        JOIN veterinarios v ON v.id_veterinario = t.id_veterinario
        JOIN mascotas m ON m.id_mascota = t.id_mascota
        JOIN duenos d ON d.id_dueno = m.id_dueno
        ORDER BY t.id_turno
        """
    )
    filas = cursor.fetchall()
    lineas = [
        "|".join([
            str(id_turno), vet, dueno, mascota,
            fecha.isoformat(), _formatear_hora(hora), estado,
        ])
        for id_turno, vet, dueno, mascota, fecha, hora, estado in filas
    ]
    return "\n".join([f"OK|LISTAR|{len(lineas)}"] + lineas)


def manejar_cambiar_estado(cursor, partes, nuevo_estado):
    if len(partes) != 2:
        return "ERROR|formato invalido, se espera un id de turno"
    try:
        id_turno = int(partes[1])
    except ValueError:
        return "ERROR|id de turno invalido"

    cursor.execute("UPDATE turnos SET estado=%s WHERE id_turno=%s", (nuevo_estado, id_turno))
    if cursor.rowcount == 0:
        return "ERROR|turno inexistente"
    return "OK"


def _obtener_o_crear(cursor, tabla, col_id, filtros, extra=None):
    columnas = list(filtros.keys())
    valores = list(filtros.values())
    condicion = " AND ".join(f"{columna}=%s" for columna in columnas)
    cursor.execute(f"SELECT {col_id} FROM {tabla} WHERE {condicion}", valores)
    fila = cursor.fetchone()
    if fila:
        return fila[0]

    extra = extra or {}
    columnas_insert = columnas + list(extra.keys())
    valores_insert = valores + list(extra.values())
    columnas_str = ", ".join(columnas_insert)
    placeholders = ", ".join(["%s"] * len(columnas_insert))
    cursor.execute(f"INSERT INTO {tabla} ({columnas_str}) VALUES ({placeholders})", valores_insert)
    return cursor.lastrowid




def _formatear_hora(valor):
    if isinstance(valor, datetime.timedelta):
        total_segundos = int(valor.total_seconds())
        horas, resto = divmod(total_segundos, 3600)
        minutos = resto // 60
        return f"{horas:02d}:{minutos:02d}"
    return valor.strftime("%H:%M")


def _hora_a_minutos(valor):
    if isinstance(valor, datetime.timedelta):
        return int(valor.total_seconds()) // 60
    return valor.hour * 60 + valor.minute


if __name__ == "__main__":
    main()
