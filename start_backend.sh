#!/bin/bash
# Script para levantar el backend de Autoppia Dashboard

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${1:-${BACKEND_PORT:-${PORT:-8000}}}"

echo "🚀 Iniciando Backend de Autoppia Dashboard..."
echo ""

# Verificar que el puerto ${PORT} esté libre
if lsof -ti:${PORT} > /dev/null 2>&1; then
    echo "⚠️  Puerto ${PORT} está ocupado. Matando proceso..."
    lsof -ti:${PORT} | xargs kill -9 2>/dev/null || true
    sleep 2
fi

# Verificar que el puerto esté libre ahora
if lsof -ti:${PORT} > /dev/null 2>&1; then
    echo "❌ Error: No se pudo liberar el puerto ${PORT}"
    echo "   Ejecuta manualmente: lsof -ti:${PORT} | xargs kill -9"
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
if grep -q "DATABASE_URL" .env 2>/dev/null; then
    echo "   ✅ DATABASE_URL configurado"
else
    echo "   ⚠️  DATABASE_URL no encontrado en .env"
fi

if grep -q "REDIS_HOST" .env 2>/dev/null; then
    echo "   ✅ REDIS configurado"
else
    echo "   ⚠️  REDIS no configurado en .env"
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
exec python3 -m uvicorn app.main:app --reload --port ${PORT} --host 0.0.0.0

