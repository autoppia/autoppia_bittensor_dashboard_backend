#!/bin/bash
# Script helper para ejecutar docker compose con Redis password según el entorno

set -e

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Cargar .env si existe
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo -e "${YELLOW}⚠️  No se encontró archivo .env${NC}"
    exit 1
fi

# Determinar el entorno
ENVIRONMENT="${ENVIRONMENT:-local}"
ENVIRONMENT_UPPER=$(echo "$ENVIRONMENT" | tr '[:lower:]' '[:upper:]')

# Obtener la contraseña según el entorno
REDIS_PASSWORD_VAR="REDIS_PASSWORD_${ENVIRONMENT_UPPER}"
REDIS_PASSWORD=$(eval echo \$${REDIS_PASSWORD_VAR})

# Exportar REDIS_PASSWORD para docker compose
export REDIS_PASSWORD

echo -e "${GREEN}🔧 Configurando Redis para entorno: ${ENVIRONMENT}${NC}"
if [ -n "$REDIS_PASSWORD" ]; then
    echo -e "${GREEN}✓${NC} Contraseña de Redis configurada (longitud: ${#REDIS_PASSWORD})"
else
    echo -e "${YELLOW}⚠️  No hay contraseña configurada para Redis (se usará sin contraseña)${NC}"
fi

# Ejecutar docker compose con los argumentos pasados
docker compose "$@"
