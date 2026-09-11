-- db/schema.sql
-- Script de inicializacion para MariaDB (patron docker-entrypoint-initdb.d).
-- Se ejecuta automaticamente la primera vez que el contenedor arranca con
-- un volumen de datos vacio, contra la base que Docker ya crea y selecciona
-- via MARIADB_DATABASE. persistencia.py ya no crea tablas: solo se conecta.
--
-- Los IF NOT EXISTS no son necesarios para el arranque de Docker (este
-- script corre una sola vez, contra un volumen vacio) pero se mantienen
-- para poder re-aplicar este mismo archivo a mano contra una base de
-- desarrollo sin romper nada si ya se habia corrido antes.

CREATE TABLE IF NOT EXISTS veterinarios (
    id_veterinario INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(120) NOT NULL
);

CREATE TABLE IF NOT EXISTS duenos (
    id_dueno INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(120) NOT NULL,
    email VARCHAR(120)
);

CREATE TABLE IF NOT EXISTS mascotas (
    id_mascota INT AUTO_INCREMENT PRIMARY KEY,
    id_dueno INT NOT NULL,
    nombre VARCHAR(80) NOT NULL,
    FOREIGN KEY (id_dueno) REFERENCES duenos(id_dueno) ON DELETE RESTRICT,
    UNIQUE (id_dueno, nombre)
);

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
);
