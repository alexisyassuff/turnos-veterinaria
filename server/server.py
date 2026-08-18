"""Servidor de turnos veterinaria - v1.

Asyncio dual-stack (IPv4 + IPv6), protocolo texto plano linea por linea,
estado en memoria (sin persistencia).
"""
import argparse
import asyncio
import sys
from contextlib import AsyncExitStack

PUERTO_POR_DEFECTO = 6000

turnos = {}
contador_id = 0
lock_estado = asyncio.Lock()


def _formatear_turno(id_turno, turno):
    return "|".join([
        str(id_turno),
        turno["veterinario"],
        turno["dueno"],
        turno["mascota"],
        turno["fecha"],
        turno["hora"],
        turno["estado"],
    ])


async def manejar_crear(partes):
    global contador_id
    if len(partes) != 6:
        return "ERROR|formato invalido, se esperan 5 argumentos"

    _, veterinario, dueno, mascota, fecha, hora = partes
    async with lock_estado:
        contador_id += 1
        id_turno = contador_id
        turnos[id_turno] = {
            "veterinario": veterinario,
            "dueno": dueno,
            "mascota": mascota,
            "fecha": fecha,
            "hora": hora,
            "estado": "pendiente",
        }
    return f"OK|{id_turno}"


async def manejar_listar():
    async with lock_estado:
        lineas = [_formatear_turno(id_turno, turno) for id_turno, turno in turnos.items()]
    return "\n".join([f"OK|LISTAR|{len(lineas)}"] + lineas)


async def _cambiar_estado(partes, nuevo_estado):
    if len(partes) != 2:
        return "ERROR|formato invalido, se espera un id de turno"
    try:
        id_turno = int(partes[1])
    except ValueError:
        return "ERROR|id de turno invalido"

    async with lock_estado:
        if id_turno not in turnos:
            return "ERROR|turno inexistente"
        turnos[id_turno]["estado"] = nuevo_estado
    return "OK"


async def manejar_cancelar(partes):
    return await _cambiar_estado(partes, "cancelado")


async def manejar_confirmar(partes):
    return await _cambiar_estado(partes, "confirmado")


async def procesar_linea(linea):
    partes = linea.split("|")
    comando = partes[0].upper()

    if comando == "CREAR":
        return await manejar_crear(partes)
    if comando == "LISTAR":
        return await manejar_listar()
    if comando == "CANCELAR":
        return await manejar_cancelar(partes)
    if comando == "CONFIRMAR":
        return await manejar_confirmar(partes)
    if comando == "SALIR":
        return None

    return "ERROR|comando desconocido"


async def manejar_cliente(reader, writer):
    direccion = writer.get_extra_info("peername")
    print(f"Cliente conectado: {direccion}")
    try:
        while True:
            datos = await reader.readline()
            if not datos:
                break

            linea = datos.decode().rstrip("\r\n")
            if not linea:
                continue

            respuesta = await procesar_linea(linea)
            if respuesta is None:
                writer.write(b"OK|CHAU\n")
                await writer.drain()
                break

            writer.write((respuesta + "\n").encode())
            await writer.drain()
    except ConnectionResetError:
        pass
    finally:
        print(f"Cliente desconectado: {direccion}")
        writer.close()
        await writer.wait_closed()


async def main():
    parser = argparse.ArgumentParser(description="Servidor de turnos veterinaria")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    args = parser.parse_args()

    familias = [
        ("IPv4", "0.0.0.0"),
        ("IPv6", "::"),
    ]
    servidores = []
    for nombre, host in familias:
        try:
            servidor = await asyncio.start_server(manejar_cliente, host=host, port=args.puerto)
            servidores.append((nombre, servidor))
        except OSError as e:
            print(f"{nombre} no disponible en este sistema, se omite: {e}")

    if not servidores:
        print("Ninguna familia de direcciones pudo levantarse, abortando.")
        sys.exit(1)

    print(f"Escuchando en {', '.join(nombre for nombre, _ in servidores)}, puerto {args.puerto}")

    async with AsyncExitStack() as stack:
        for _, servidor in servidores:
            await stack.enter_async_context(servidor)
        await asyncio.gather(*(servidor.serve_forever() for _, servidor in servidores))


if __name__ == "__main__":
    asyncio.run(main())
