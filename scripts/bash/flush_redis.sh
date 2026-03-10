#!/bin/bash
# Script para borrar todo el contenido de Redis (FLUSHALL)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "🗑️  Script para borrar todo el contenido de Redis"
echo ""

# Cargar configuración desde .env
REDIS_HOST="localhost"
REDIS_PORT="6379"
REDIS_PASSWORD=""
USE_DOCKER=false

if [[ -f .env ]]; then
    set -a
    # shellcheck source=/dev/null
    source <(grep -v '^#' .env | grep -v '^[[:space:]]*$')
    set +a
    ENVIRONMENT="${ENVIRONMENT:-local}"
    ENVIRONMENT_UPPER=$(echo "$ENVIRONMENT" | tr '[:lower:]' '[:upper:]')

    # Obtener configuración de Redis (indirect expansion, sin eval)
    REDIS_HOST_VAR="REDIS_HOST_${ENVIRONMENT_UPPER}"
    REDIS_PORT_VAR="REDIS_PORT_${ENVIRONMENT_UPPER}"
    REDIS_PASSWORD_VAR="REDIS_PASSWORD_${ENVIRONMENT_UPPER}"

    REDIS_HOST="${REDIS_HOST:-${!REDIS_HOST_VAR:-localhost}}"
    REDIS_PORT="${REDIS_PORT:-${!REDIS_PORT_VAR:-6379}}"
    REDIS_PASSWORD="${!REDIS_PASSWORD_VAR:-}"

    # Verificar si Redis está en Docker
    if [[ -f docker-compose.yml ]] && docker compose ps redis 2>/dev/null | grep -q "redis"; then
        USE_DOCKER=true
    fi
fi

echo "📋 Configuración detectada:"
echo "   Entorno: $ENVIRONMENT"
echo "   Host: $REDIS_HOST"
echo "   Puerto: $REDIS_PORT"
if [[ -n "$REDIS_PASSWORD" ]]; then
    echo "   Contraseña: *** (configurada)"
else
    echo "   Contraseña: (ninguna)"
fi
if [[ "$USE_DOCKER" == true ]]; then
    echo "   Modo: Docker Compose"
else
    echo "   Modo: Local"
fi
echo ""

# Función para ejecutar comandos Redis
run_redis_cmd() {
    local cmd="$1"
    if [[ "$USE_DOCKER" == true ]]; then
        if [[ -n "$REDIS_PASSWORD" ]]; then
            docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" "$cmd" 2>&1 | grep -v "Warning"
        else
            docker compose exec -T redis redis-cli "$cmd" 2>&1
        fi
    else
        if [[ -n "$REDIS_PASSWORD" ]]; then
            redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" "$cmd" 2>&1 | grep -v "Warning"
        else
            redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" "$cmd" 2>&1
        fi
    fi
    return
}

# Verificar conexión a Redis
echo "🔍 Verificando conexión a Redis..."
if run_redis_cmd "PING" | grep -q "PONG"; then
    echo "   ✅ Redis está accesible"
else
    echo "   ❌ No se pudo conectar a Redis"
    echo "   💡 Verifica que Redis esté corriendo:"
    if [[ "$USE_DOCKER" == true ]]; then
        echo "      docker compose ps redis"
    else
        echo "      redis-cli ping"
    fi
    exit 1
fi
echo ""

# Mostrar estadísticas antes de borrar
echo "📊 Estadísticas actuales de Redis:"
KEYS_COUNT=$(run_redis_cmd "DBSIZE" | tr -d '\r\n' | grep -oE '[0-9]+' || echo "0")
echo "   Total de claves: $KEYS_COUNT"

if [[ "$KEYS_COUNT" -gt 0 ]]; then
    echo ""
    echo "   🔍 Muestra de claves (primeras 10):"
    SAMPLE_KEYS=$(run_redis_cmd "KEYS *" | head -10 | grep -v "^$" || echo "")
    if [[ -n "$SAMPLE_KEYS" ]]; then
        echo "$SAMPLE_KEYS" | sed 's/^/      - /'
    else
        echo "      (no se pudieron listar claves)"
    fi
else
    echo "   ℹ️  Redis está vacío, no hay nada que borrar"
    exit 0
fi
echo ""

# Confirmación
echo "⚠️  ADVERTENCIA: Esta operación borrará TODAS las claves de Redis"
echo "   Esto no se puede deshacer."
echo ""
read -p "¿Estás seguro de que quieres continuar? (escribe 'SI' para confirmar): " CONFIRM

if [ "$CONFIRM" != "SI" ]; then
    echo ""
    echo "❌ Operación cancelada"
    exit 0
fi

echo ""
echo "🗑️  Borrando todas las claves de Redis..."

# Ejecutar FLUSHALL
RESULT=$(run_redis_cmd "FLUSHALL")

if echo "$RESULT" | grep -q "OK"; then
    echo "   ✅ Redis limpiado correctamente"
else
    echo "   ⚠️  Respuesta inesperada: $RESULT"
fi

# Verificar que se borró todo
echo ""
echo "🔍 Verificando..."
NEW_KEYS_COUNT=$(run_redis_cmd "DBSIZE" | tr -d '\r\n' | grep -oE '[0-9]+' || echo "0")

if [[ "$NEW_KEYS_COUNT" -eq 0 ]]; then
    echo "   ✅ Confirmado: Redis está vacío ($NEW_KEYS_COUNT claves)"
else
    echo "   ⚠️  Advertencia: Todavía hay $NEW_KEYS_COUNT claves en Redis"
fi

echo ""
echo "✅ Operación completada"
