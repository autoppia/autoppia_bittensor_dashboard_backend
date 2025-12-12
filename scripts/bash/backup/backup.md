# Backup por snapshots de esquema `public` en PostgreSQL (Autoppia)

Este documento describe el script `backup.sh`, que crea **snapshots diarios** de la base de datos duplicando el esquema `public` en un nuevo esquema con marca temporal, y mantiene únicamente los **últimos 5** snapshots.

---

## Qué hace este script

En cada ejecución:

1. Genera un nombre de esquema destino con timestamp:
   - Formato: `public-YYYY-MM-DD-HH-MM`
   - Ejemplo: `public-2025-12-12-00-00`

2. Comprueba si ese esquema ya existe:
   - Si existe, **no duplica** y finaliza.

3. Crea el esquema nuevo.

4. Copia **todo el contenido del esquema `public`** (DDL + datos) al esquema nuevo:
   - Usa `pg_dump -n public`
   - Reescribe el `search_path` y los prefijos `public.` para apuntar al nuevo esquema
   - Restaura con `psql` dentro de la misma base de datos

5. Da permisos sobre el nuevo esquema a `autoppia_user`.

6. Limpia snapshots antiguos:
   - Mantiene **solo los 5 más recientes**
   - Elimina el resto con `DROP SCHEMA ... CASCADE`

---

## Requisitos

- PostgreSQL instalado y accesible desde el servidor.
- Comandos disponibles:
  - `psql`
  - `pg_dump`
  - `sed`
  - `date`
- Usuario con permisos suficientes en la DB para:
  - `CREATE SCHEMA`
  - Crear tablas, secuencias, funciones en el nuevo esquema
  - Leer `public`
  - `DROP SCHEMA ... CASCADE` para limpieza de históricos

---

## Configuración dentro del script

Variables principales:

- `DB_NAME="autoppia_prod"`
- `DB_USER="autoppia_user"`
- `DB_HOST="localhost"`
- `DB_PORT="5432"`

Password:

- Si `PGPASSWORD` no está definida, el script exporta:
  - `PGPASSWORD="Autoppia2025.Leaderboard.Production"`

> Nota: al estar embebida en el script, cualquiera con acceso al archivo puede leerla.

---

## Ubicación y permisos recomendados

Ejemplo (root):

- Ruta: `/root/backup.sh`
- Permisos:
  ```bash
  chmod +x /root/backup.sh

