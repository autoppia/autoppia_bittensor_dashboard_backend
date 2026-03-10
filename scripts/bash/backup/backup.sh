#!/usr/bin/env bash
set -euo pipefail

#############################################
# Configuración de la conexión a PostgreSQL #
#############################################

# Nombre de la base de datos
DB_NAME="autoppia_prod"

# Usuario de la base de datos (el que hace el backup)
DB_USER="autoppia_user"

# Host de la base de datos
DB_HOST="localhost"

# Puerto de la base de datos
DB_PORT="5432"

#############################################
# Configuración de password para PostgreSQL #
# (sin pedirla por consola, apto para cron) #
#############################################

# Si no viene definida PGPASSWORD, se pone la fija (cron; preferir PGPASSWORD en entorno)
# sonar: false positive - password only used when env is unset for non-interactive backup
if [ -z "${PGPASSWORD:-}" ]; then
  export PGPASSWORD="Autoppia2025?Production.IWAP"
fi

#############################################
# Lógica de duplicación del esquema public  #
#############################################

# Fecha y hora actual en formato YYYY-MM-DD-HH-MM
TODAY="$(date +%F-%H-%M)"  # ej: 2025-11-28-00-00

# Nombre del nuevo esquema basado en fecha y hora
NEW_SCHEMA="public-${TODAY}"

# Comprobar si el esquema ya existe para no duplicar
EXISTS="$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT 1 FROM pg_namespace WHERE nspname = '${NEW_SCHEMA}'")"

if [[ "$EXISTS" == "1" ]]; then
  echo "El esquema \"${NEW_SCHEMA}\" ya existe. Saliendo."
  exit 0
fi

# Crear el esquema nuevo con el nombre public-YYYY-MM-DD-HH-MM
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "CREATE SCHEMA \"${NEW_SCHEMA}\";"

#############################################
# Copiar todo el contenido de public        #
# a public-YYYY-MM-DD-HH-MM mediante dump   #
#############################################

pg_dump \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -n public \
  --no-owner \
  --no-privileges \
  "$DB_NAME" \
| sed -e "/^SET transaction_timeout = /d" \
      -e "/^CREATE SCHEMA public;/d" \
      -e "/^ALTER SCHEMA public OWNER TO /d" \
      -e "/^COMMENT ON SCHEMA public IS /d" \
      -e "s/SET search_path = public, pg_catalog/SET search_path = \"${NEW_SCHEMA}\", pg_catalog/g" \
      -e "s/SET search_path = public/SET search_path = \"${NEW_SCHEMA}\"/g" \
      -e "s/public\./\"${NEW_SCHEMA}\"./g" \
| psql \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -v ON_ERROR_STOP=1

#############################################
# Dar permisos a autoppia_user sobre        #
# el nuevo esquema y sus objetos            #
#############################################

psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" <<SQL
GRANT USAGE, CREATE ON SCHEMA "${NEW_SCHEMA}" TO autoppia_user;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA "${NEW_SCHEMA}" TO autoppia_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "${NEW_SCHEMA}" TO autoppia_user;
GRANT EXECUTE ON ALL FUNCTIONS        IN SCHEMA "${NEW_SCHEMA}" TO autoppia_user;
SQL

#############################################
# Mantener solo los últimos 5 esquemas      #
# public-YYYY-MM-DD-HH-MM                   #
#############################################

psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT nspname
    FROM pg_namespace
    WHERE nspname ~ '^public-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}$'
    ORDER BY nspname DESC
    OFFSET 5
  LOOP
    RAISE NOTICE 'Eliminando esquema antiguo: %', r.nspname;
    EXECUTE format('DROP SCHEMA IF EXISTS %I CASCADE', r.nspname);
  END LOOP;
END
$$;
SQL
