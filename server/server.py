import argparse
import asyncio
import contextlib
import signal
import socket
import sys
from contextlib import AsyncExitStack
from pathlib import Path

PUERTO_POR_DEFECTO = 6000
RUTA_PERSISTENCIA = Path(__file__).parent / "persistencia.py"

proceso_persistencia = None
lock_persistencia = asyncio.Lock()


async def main():
    parser = argparse.ArgumentParser(description="Servidor de turnos veterinaria")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    parser.add_argument("--db-host", default=None)
    parser.add_argument("--db-port", type=int, default=None)
    parser.add_argument("--db-user", default=None)
    parser.add_argument("--db-password", default=None)
    parser.add_argument("--db-name", default=None)
    args = parser.parse_args()


    loop = asyncio.get_running_loop()
    evento_apagado = asyncio.Event()
    for señal in (signal.SIGINT, signal.SIGTERM):
    # Logica de CTRL + C  ----> No signal.signal
        loop.add_signal_handler(señal, evento_apagado.set)

    await iniciar_persistencia(args)
    
    # Verificacion 1
    familias_candidatas = [
        ("IPv4", socket.AF_INET, "0.0.0.0"),
        ("IPv6", socket.AF_INET6, "::"),
    ]
    familias = []
    for nombre, familia, host in familias_candidatas:
        try:
        # bajando a nivel del kernel --> ver si la máquina soporta IPv6
            socket.getaddrinfo(
                None, args.puerto, family=familia, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
            )
            familias.append((nombre, host))
        except socket.gaierror as e:
            print(f"{nombre} no disponible en este sistema, se omite: {e}")

    # Verificacion 2
    servidores = []
    for nombre, host in familias:
        try:
        # Manejar fallos en tiempo de ejecución
            servidor = await asyncio.start_server(manejar_cliente, host=host, port=args.puerto)
            servidores.append((nombre, servidor))
        except OSError as e:
            print(f"{nombre} no disponible en este sistema, se omite: {e}")

    if not servidores:
        print("Ninguna familia de direcciones pudo levantarse, abortando.")
        await detener_persistencia()
        sys.exit(1)

    print(f"Escuchando en {', '.join(nombre for nombre, _ in servidores)}, puerto {args.puerto}")

    try:
        async with AsyncExitStack() as stack:
            for _, servidor in servidores:
                await stack.enter_async_context(servidor)

            tarea_servir = asyncio.gather(*(servidor.serve_forever() for _, servidor in servidores))
            tarea_señal = asyncio.create_task(evento_apagado.wait())
    
            # Guardia revisando si alarma suena      
            await asyncio.wait(
                # Nucleo de la concurrencia FIRST_COMPLETED
                {tarea_servir, tarea_señal}, return_when=asyncio.FIRST_COMPLETED
            )

            if tarea_señal.done():
                print("Señal de apagado recibida, cerrando servidor...")
            if not tarea_servir.done():
                tarea_servir.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tarea_servir
    finally:
        await detener_persistencia()


async def iniciar_persistencia(args):
    global proceso_persistencia
    comando = [sys.executable, str(RUTA_PERSISTENCIA)]
    opciones_db = {
        "--db-host": args.db_host,
        "--db-port": args.db_port,
        "--db-user": args.db_user,
        "--db-password": args.db_password,
        "--db-name": args.db_name,
    }
    for bandera, valor in opciones_db.items():
        if valor is not None:
            comando += [bandera, str(valor)]

    proceso_persistencia = await asyncio.create_subprocess_exec(
        *comando,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )


async def detener_persistencia():
    if proceso_persistencia is None:
        return
    try:
        proceso_persistencia.stdin.write(b"DB_SALIR\n")
        await proceso_persistencia.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    proceso_persistencia.stdin.close()
    await proceso_persistencia.wait()

async def manejar_cliente(reader, writer):
    direccion = writer.get_extra_info("peername")
    print(f"Cliente conectado: {direccion}")
    try:
        # conexion abierta
        while True:
            # el servidor se queda esperando líneas de comandos
            datos = await reader.readline()
            # cerró la terminal del cliente sin mandar SALIR
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
    # cierra el socket de ese cliente puntual de forma prolija, liberando los recursos que estaba usando.
    finally:
        print(f"Cliente desconectado: {direccion}")
        writer.close()
        await writer.wait_closed()


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


async def manejar_crear(partes):
    if len(partes) != 7:
        return "ERROR|formato invalido, se esperan 6 argumentos"

    _, veterinario, dueno, email, mascota, fecha, hora = partes
    return await enviar_a_persistencia(
        f"DB_CREAR_TURNO|{veterinario}|{dueno}|{email}|{mascota}|{fecha}|{hora}"
    )


async def manejar_listar():
    return await enviar_a_persistencia("DB_LISTAR_TURNOS")


async def _cambiar_estado(partes, nuevo_estado):
    if len(partes) != 2:
        return "ERROR|formato invalido, se espera un id de turno"
    try:
        int(partes[1])
    except ValueError:
        return "ERROR|id de turno invalido"

    comando = "DB_CONFIRMAR" if nuevo_estado == "confirmado" else "DB_CANCELAR"
    return await enviar_a_persistencia(f"{comando}|{partes[1]}")


async def manejar_cancelar(partes):
    return await _cambiar_estado(partes, "cancelado")


async def manejar_confirmar(partes):
    return await _cambiar_estado(partes, "confirmado")

async def enviar_a_persistencia(comando):
    async def _intercambio():
        # IDA: un solo canal físico  ​stdin de persistencia.py​. solo sirve para que server.py le mande cosas a persistencia.p
        proceso_persistencia.stdin.write((comando + "\n").encode())
        await proceso_persistencia.stdin.drain()

        # VUELTA: otro canal físico: el stdout de persistencia.py. solo sirve para que persistencia.py le conteste a server.py.
        linea = await proceso_persistencia.stdout.readline()
        if not linea:
            return "ERROR|conexion con el proceso de persistencia perdida"
        respuesta = linea.decode().rstrip("\n")

        if respuesta.startswith("OK|LISTAR|"):
            cantidad = int(respuesta.split("|")[2])
            filas = []
            for _ in range(cantidad):
                fila = await proceso_persistencia.stdout.readline()
                filas.append(fila.decode().rstrip("\n"))
            return "\n".join([respuesta] + filas)

        return respuesta

    async with lock_persistencia:
        try:
            return await asyncio.wait_for(_intercambio(), timeout=5)
        except asyncio.TimeoutError:
            return "ERROR|timeout esperando al proceso de persistencia"


if __name__ == "__main__":
    asyncio.run(main())
