#!/bin/bash
# Script para levantar el backend de Autoppia Dashboard

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

PORT="${1:-${BACKEND_PORT:-${PORT:-8080}}}"

echo "🚀 Iniciando Backend de Autoppia Dashboard..."
echo ""

# ============================================================================
# Verificar y levantar dependencias (Redis y Background Updater)
# ============================================================================

echo "📋 Verificando dependencias..."
echo ""

# 1. Verificar e iniciar Redis
echo "1️⃣  Verificando Redis..."
if pgrep -x "redis-server" > /dev/null; then
    echo "   ✅ Redis ya está corriendo (PID: $(pgrep -x redis-server))"
else
    echo "   ⚠️  Redis no está corriendo, iniciando..."
    if [ -f "scripts/bash/start_redis.sh" ]; then
        # Temporalmente desactivar set -e para que no falle si Redis ya está corriendo
        set +e
        bash scripts/bash/start_redis.sh
        set -e

        # Verificar nuevamente
        sleep 1
        if pgrep -x "redis-server" > /dev/null; then
            echo "   ✅ Redis iniciado correctamente"
        else
            echo "   ❌ Error: No se pudo iniciar Redis"
            echo "   💡 Intenta manualmente: bash scripts/bash/start_redis.sh"
            exit 1
        fi
    else
        echo "   ❌ Error: Script start_redis.sh no encontrado"
        exit 1
    fi
fi
echo ""

# 2. Verificar e iniciar Background Updater
echo "2️⃣  Verificando Background Updater..."
if command -v pm2 &> /dev/null; then
    # Buscar cualquier proceso que contenga "background-updater"
    BACKGROUND_RUNNING=false
    if pm2 list 2>/dev/null | grep -q "background-updater"; then
        BACKGROUND_RUNNING=true
    fi

    if [ "$BACKGROUND_RUNNING" = true ]; then
        BACKGROUND_NAME=$(pm2 list 2>/dev/null | grep "background-updater" | awk '{print $2}' | head -1)
        echo "   ✅ Background updater ya está corriendo en PM2 (nombre: ${BACKGROUND_NAME:-background-updater})"
    else
        echo "   ⚠️  Background updater no está corriendo, iniciando..."
        if [ -f "scripts/bash/start_background_updater.sh" ]; then
            # Ejecutar en modo no interactivo
            NO_INTERACTIVE=1 bash scripts/bash/start_background_updater.sh

            # Verificar nuevamente
            sleep 2
            if pm2 list 2>/dev/null | grep -q "background-updater"; then
                echo "   ✅ Background updater iniciado correctamente"
            else
                echo "   ⚠️  Warning: Background updater podría no haberse iniciado correctamente"
                echo "   💡 Verifica manualmente: pm2 list"
                echo "   💡 El backend funcionará pero el chain state podría no estar actualizado"
            fi
        else
            echo "   ⚠️  Warning: Script start_background_updater.sh no encontrado"
            echo "   💡 El backend funcionará pero el chain state podría no estar actualizado"
        fi
    fi
else
    echo "   ⚠️  Warning: PM2 no está instalado"
    echo "   💡 El backend funcionará pero el background updater no puede iniciarse"
    echo "   💡 Instala PM2: npm install -g pm2"
fi
echo ""

# ============================================================================
# Continuar con el inicio del backend
# ============================================================================

# Verificar que el puerto esté libre
if lsof -ti:"${PORT}" > /dev/null 2>&1; then
    echo "⚠️  Puerto ${PORT} está ocupado. Matando proceso..."
    lsof -ti:"${PORT}" | xargs kill -9 2>/dev/null || true
    sleep 2
fi

# Verificar que el puerto esté libre ahora
if lsof -ti:"${PORT}" > /dev/null 2>&1; then
    echo "❌ Error: No se pudo liberar el puerto ${PORT}"
    echo "   Ejecuta manualmente: lsof -ti:\"${PORT}\" | xargs kill -9"
    exit 1
fi

echo "✅ Puerto ${PORT} libre"
echo ""

# Verificar que existe .env
if [ ! -f .env ]; then
    echo "⚠️  Archivo .env no encontrado"
    echo "   Creando .env desde .env.example si existe..."
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "   ✅ .env creado desde .env.example"
    else
        echo "   ⚠️  .env.example no existe. Asegúrate de configurar .env manualmente"
    fi
fi

# Verificar variables críticas
echo "📋 Verificando configuración..."

# Determinar el ambiente desde .env o usar 'local' por defecto
ENV_MODE="local"
if [ -f .env ]; then
    ENV_VALUE=$(grep "^ENVIRONMENT=" .env 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d "'" | tr '[:lower:]' '[:upper:]' || echo "")
    if [ -n "$ENV_VALUE" ]; then
        ENV_MODE=$(echo "$ENV_VALUE" | tr '[:upper:]' '[:lower:]')
    fi
fi

# Verificar variables de base de datos según el ambiente
# El sistema construye DATABASE_URL automáticamente desde POSTGRES_*_LOCAL, etc.
DB_VARS_OK=true
if [ -f .env ]; then
    if grep -qE "^(POSTGRES_USER|POSTGRES_USER_${ENV_MODE^^})=" .env 2>/dev/null && \
       grep -qE "^(POSTGRES_PASSWORD|POSTGRES_PASSWORD_${ENV_MODE^^})=" .env 2>/dev/null && \
       grep -qE "^(POSTGRES_HOST|POSTGRES_HOST_${ENV_MODE^^})=" .env 2>/dev/null && \
       grep -qE "^(POSTGRES_DB|POSTGRES_DB_${ENV_MODE^^})=" .env 2>/dev/null; then
        echo "   ✅ Configuración de base de datos encontrada (ambiente: $ENV_MODE)"
    elif grep -q "DATABASE_URL" .env 2>/dev/null; then
        echo "   ✅ DATABASE_URL configurado directamente"
    else
        echo "   ⚠️  Variables de base de datos no encontradas en .env"
        echo "      Se esperan: POSTGRES_USER_${ENV_MODE^^}, POSTGRES_PASSWORD_${ENV_MODE^^}, etc."
        DB_VARS_OK=false
    fi
else
    echo "   ⚠️  Archivo .env no encontrado"
    DB_VARS_OK=false
fi

# Verificar Redis (opcional, tiene valores por defecto)
if grep -qE "^(REDIS_HOST|REDIS_ENABLED)" .env 2>/dev/null; then
    echo "   ✅ REDIS configurado"
else
    echo "   ℹ️  REDIS usando configuración por defecto (localhost:6379)"
fi

echo ""
echo "🔧 Levantando servidor..."
echo "   URL: http://localhost:${PORT}"
echo "   Docs: http://localhost:${PORT}/docs"
echo ""
echo "   Presiona Ctrl+C para detener"
echo ""

# Activar venv si existe
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "✅ Virtual environment activado"
    echo ""
fi

# Levantar uvicorn
exec python3 -m uvicorn app.main:app --reload --port "${PORT}" --host 0.0.0.0
