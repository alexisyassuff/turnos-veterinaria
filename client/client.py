"""Cliente CLI de turnos veterinaria - v1.

Conexion persistente via asyncio, REPL interactivo con sub-comandos
parseados por argparse en cada linea.
"""
import argparse
import asyncio
import shlex

PUERTO_POR_DEFECTO = 6000


async def main():
    parser = argparse.ArgumentParser(description="Cliente CLI de turnos veterinaria")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    args = parser.parse_args()

    reader, writer = await asyncio.open_connection(args.host, args.puerto)
    try:
        await repl(reader, writer)
    finally:
        writer.close()
        await writer.wait_closed()



def crear_parser_repl():
    parser = ParserSinSalida(prog="", add_help=True)
    subparsers = parser.add_subparsers(dest="comando")

    p_crear = subparsers.add_parser("crear", help="Crear un turno nuevo")
    p_crear.add_argument("--vet", required=True)
    p_crear.add_argument("--dueno", required=True)
    p_crear.add_argument("--email", required=True)
    p_crear.add_argument("--mascota", required=True)
    p_crear.add_argument("--fecha", required=True)
    p_crear.add_argument("--hora", required=True)

    subparsers.add_parser("listar", help="Listar todos los turnos")

    p_cancelar = subparsers.add_parser("cancelar", help="Cancelar un turno")
    p_cancelar.add_argument("id", type=int)

    p_confirmar = subparsers.add_parser("confirmar", help="Confirmar un turno")
    p_confirmar.add_argument("id", type=int)

    subparsers.add_parser("salir", help="Cerrar la conexion y salir")

    return parser


def construir_mensaje(args):
    if args.comando == "crear":
        return f"CREAR|{args.vet}|{args.dueno}|{args.email}|{args.mascota}|{args.fecha}|{args.hora}"
    if args.comando == "listar":
        return "LISTAR"
    if args.comando == "cancelar":
        return f"CANCELAR|{args.id}"
    if args.comando == "confirmar":
        return f"CONFIRMAR|{args.id}"
    if args.comando == "salir":
        return "SALIR"
    return None


async def mostrar_respuesta(reader):
    datos = await reader.readline()
    if not datos:
        print("El servidor cerro la conexion.")
        return

    linea = datos.decode().rstrip("\r\n")
    partes = linea.split("|")

    if partes[0] == "OK" and len(partes) >= 3 and partes[1] == "LISTAR":
        cantidad = int(partes[2])
        print(f"{cantidad} turno(s):")
        for _ in range(cantidad):
            fila = (await reader.readline()).decode().rstrip("\r\n")
            id_turno, vet, dueno, mascota, fecha, hora, estado = fila.split("|")
            print(f"  #{id_turno} | {fecha} {hora} | {mascota} ({dueno}) con {vet} | estado: {estado}")
    else:
        print(linea)





class ParserSinSalida(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)
    

async def repl(reader, writer):
    parser = crear_parser_repl()
    loop = asyncio.get_event_loop()

    print("Cliente de turnos veterinaria. Escribi 'ayuda' para ver los comandos, 'salir' para terminar.")
    while True:
        try:
            linea = await loop.run_in_executor(None, input, "> ")
        except EOFError:
            linea = "salir"

        linea = linea.strip()
        if not linea:
            continue
        if linea in ("ayuda", "help"):
            parser.print_help()
            continue

        try:
            tokens = shlex.split(linea)
            args = parser.parse_args(tokens)
        except ValueError as error:
            print(f"Error: {error}")
            continue

        if not args.comando:
            print("Comando vacio. Escribi 'ayuda' para ver las opciones.")
            continue

        writer.write((construir_mensaje(args) + "\n").encode())
        await writer.drain()

        if args.comando == "salir":
            await mostrar_respuesta(reader)
            break

        await mostrar_respuesta(reader)


if __name__ == "__main__":
    asyncio.run(main())


# crear --vet "Dr. Gomez" --dueno "Juan Perez" --email "juan@ejemplo.com" --mascota "Firulais" --fecha 2026-07-15 --hora 10:00
# listar
# confirmar 1
# listar
# cancelar 1
# listar