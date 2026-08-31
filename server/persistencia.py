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

SENTENCIAS_ESQUEMA = [
    """
    CREATE TABLE IF NOT EXISTS veterinarios (
        id_veterinario INT AUTO_INCREMENT PRIMARY KEY,
        nombre VARCHAR(120) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duenos (
        id_dueno INT AUTO_INCREMENT PRIMARY KEY,
        nombre VARCHAR(120) NOT NULL
    )
    """,
    """
    ALTER TABLE duenos ADD COLUMN IF NOT EXISTS email VARCHAR(120)
    """,
    """
    CREATE TABLE IF NOT EXISTS mascotas (
        id_mascota INT AUTO_INCREMENT PRIMARY KEY,
        id_dueno INT NOT NULL,
        nombre VARCHAR(80) NOT NULL,
        FOREIGN KEY (id_dueno) REFERENCES duenos(id_dueno) ON DELETE RESTRICT,
        UNIQUE (id_dueno, nombre)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS turnos (
        id_turno INT AUTO_INCREMENT PRIMARY KEY,
        id_veterinario INT NOT NULL,
        id_mascota INT NOT NULL,
        fecha DATE NOT NULL,
        hora TIME NOT NULL,
        estado ENUM('pendiente', 'confirmado', 'cancelado') NOT NULL DEFAULT 'pendiente',
        FOREIGN KEY (id_veterinario) REFERENCES veterinarios(id_veterinario) ON DELETE RESTRICT,
        FOREIGN KEY (id_mascota) REFERENCES mascotas(id_mascota) ON DELETE RESTRICT,
        UNIQUE (id_veterinario, fecha, hora)
    )
    """,
]


def conectar(args):
    conexion = pymysql.connect(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        autocommit=True,
    )
    with conexion.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{args.db_name}`")
    conexion.select_db(args.db_name)
    with conexion.cursor() as cursor:
        for sentencia in SENTENCIAS_ESQUEMA:
            cursor.execute(sentencia)
    return conexion


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

    try:
        cursor.execute(
            "INSERT INTO turnos (id_veterinario, id_mascota, fecha, hora) VALUES (%s, %s, %s, %s)",
            (id_vet, id_mascota, fecha_valor, hora_valor),
        )
    except pymysql.err.IntegrityError:
        return "ERROR|ese veterinario ya tiene un turno en esa fecha y hora"

    return f"OK|{cursor.lastrowid}"


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


if __name__ == "__main__":
    main()
