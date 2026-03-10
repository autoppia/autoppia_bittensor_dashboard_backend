#!/bin/bash
# Levanta EXACTAMENTE dos procesos PM2 del backend:
#   1) iwap-api
#   2) background-updater
# Uso: bash scripts/bash/start_backend_pm2.sh [puerto]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

PORT="${1:-${BACKEND_PORT:-${PORT:-8080}}}"
PM2_API_NAME="iwap-api"
PM2_BG_NAME="background-updater"

# Canonical main validator (autoppia validator 1)
# Se puede sobreescribir desde el entorno antes de ejecutar el script.
export MAIN_VALIDATOR_UID="${MAIN_VALIDATOR_UID:-21}"
export MAIN_VALIDATOR_HOTKEY="${MAIN_VALIDATOR_HOTKEY:-5DANs86MZknobepodgBt91DBp3gPiSxJjpopJw8BKDkpX3gZ}"

echo "🚀 Iniciando IWAP API con PM2 (puerto ${PORT})..."
echo ""

# PM2 requerido
if ! command -v pm2 &> /dev/null; then
    echo "❌ PM2 no está instalado. Instala con: npm install -g pm2"
    exit 1
fi

# 1. Redis
echo "1️⃣  Redis..."
if pgrep -x "redis-server" > /dev/null; then
    echo "   ✅ Redis ya está corriendo"
else
    echo "   ⚠️  Redis no está corriendo"
    if [ -f "scripts/bash/start_redis.sh" ]; then
        set +e
        bash scripts/bash/start_redis.sh
        set -e
        sleep 1
    fi
    if ! pgrep -x "redis-server" > /dev/null; then
        echo "   ❌ Inicia Redis antes: bash scripts/bash/start_redis.sh"
        exit 1
    fi
    echo "   ✅ Redis iniciado"
fi
echo ""

# 2. Background updater en PM2
echo "2️⃣  Background updater (${PM2_BG_NAME})..."
if pm2 list 2>/dev/null | grep -q "$PM2_BG_NAME"; then
    echo "   ✅ ${PM2_BG_NAME} ya está en PM2"
else
    echo "   Iniciando ${PM2_BG_NAME} con PM2..."
    NO_INTERACTIVE=1 bash scripts/bash/start_background_updater.sh 2>/dev/null || true
    sleep 1
    if pm2 list 2>/dev/null | grep -q "$PM2_BG_NAME"; then
        echo "   ✅ ${PM2_BG_NAME} iniciado"
    else
        echo "   ⚠️  ${PM2_BG_NAME} no se pudo iniciar (opcional)"
    fi
fi
echo ""

# 3. Liberar puerto si está ocupado
if lsof -ti:"${PORT}" > /dev/null 2>&1; then
    echo "⚠️  Puerto ${PORT} ocupado. Liberando..."
    lsof -ti:"${PORT}" | xargs kill -9 2>/dev/null || true
    sleep 2
fi

# 4. API en PM2
echo "3️⃣  IWAP API (iwap-api)..."
if pm2 list 2>/dev/null | grep -q "$PM2_API_NAME"; then
    echo "   Reiniciando $PM2_API_NAME..."
    pm2 restart "$PM2_API_NAME" --update-env
    echo "   ✅ $PM2_API_NAME reiniciado"
else
    if [ ! -d "venv" ]; then
        echo "   ❌ No se encontró venv. Crea uno con: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
        exit 1
    fi
    echo "   Iniciando $PM2_API_NAME con PM2..."
    pm2 start venv/bin/python3 --name "$PM2_API_NAME" -- \
        -m uvicorn app.main:app --port "$PORT" --host 0.0.0.0
    sleep 1
    if pm2 list 2>/dev/null | grep -q "$PM2_API_NAME"; then
        echo "   ✅ $PM2_API_NAME iniciado"
    else
        echo "   ❌ No se pudo iniciar $PM2_API_NAME"
        exit 1
    fi
fi

pm2 save 2>/dev/null || true
echo ""
echo "✅ Backend PM2 listo (2 procesos esperados)"
echo "   API:    http://localhost:${PORT} (pm2 name: $PM2_API_NAME)"
echo "   BG:     ${PM2_BG_NAME}"
echo "   Main validator: uid=${MAIN_VALIDATOR_UID} hotkey=${MAIN_VALIDATOR_HOTKEY}"
echo "   Docs:   http://localhost:${PORT}/docs"
echo ""
echo "Comandos útiles:"
echo "  pm2 logs $PM2_API_NAME        # logs de la API"
echo "  pm2 logs $PM2_BG_NAME         # logs del background updater"
echo "  pm2 restart $PM2_API_NAME     # reiniciar API"
echo "  pm2 stop $PM2_API_NAME        # parar API"
echo "  pm2 list                      # listar procesos"
echo ""
