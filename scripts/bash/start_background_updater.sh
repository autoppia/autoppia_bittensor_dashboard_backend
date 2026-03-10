#!/bin/bash
# Script para levantar el background updater con PM2

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "🔍 Verificando background updater..."

# Verificar si PM2 está instalado
if ! command -v pm2 &> /dev/null; then
    echo "❌ PM2 no está instalado"
    echo "📦 Instalando PM2..."
    npm install -g pm2
fi

# Verificar si Redis está disponible
if ! pgrep -x "redis-server" > /dev/null; then
    echo "⚠️  Redis no está corriendo"
    echo "💡 Ejecuta primero: bash scripts/bash/start_redis.sh"
    exit 1
fi

# Verificar si el background updater ya está corriendo
# Buscar tanto "background-updater" como "background-updater.autoppia.com"
BACKGROUND_RUNNING=false
BACKGROUND_NAME=""
if pm2 list 2>/dev/null | grep -q "background-updater"; then
    BACKGROUND_RUNNING=true
    # Obtener el nombre exacto del proceso
    BACKGROUND_NAME=$(pm2 list 2>/dev/null | grep "background-updater" | awk '{print $2}' | head -1)
fi

if [ "$BACKGROUND_RUNNING" = true ] && [ -n "$BACKGROUND_NAME" ]; then
    # Si se llama de forma no interactiva (variable NO_INTERACTIVE), solo avisar y salir
    if [ "${NO_INTERACTIVE:-0}" = "1" ]; then
        echo "✅ Background updater ya está corriendo en PM2 (nombre: $BACKGROUND_NAME)"
        exit 0
    fi

    echo "⚠️  Background updater ya está corriendo en PM2 (nombre: $BACKGROUND_NAME)"
    echo "📊 Estado:"
    pm2 list 2>/dev/null | grep "background-updater"
    echo ""
    read -p "¿Quieres reiniciarlo? (y/n): " -n 1 -r
    echo
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        pm2 restart "$BACKGROUND_NAME"
        echo "✅ Background updater reiniciado"
    else
        echo "ℹ️  Manteniendo el proceso actual"
    fi
    exit 0
fi

# Verificar que existe el archivo background_updater.py
if [ ! -f "scripts/background_updater.py" ]; then
    echo "❌ Error: scripts/background_updater.py no encontrado en $PROJECT_ROOT"
    exit 1
fi

# Verificar que existe el venv
if [ ! -d "venv" ]; then
    echo "⚠️  Virtual environment no encontrado"
    echo "💡 Asegúrate de tener el venv configurado"
fi

echo "🚀 Iniciando background updater con PM2..."

# Crear directorio de logs si no existe
mkdir -p "$PROJECT_ROOT/logs"

# Iniciar el background updater con PM2
# Usar el nombre simple "background-updater" para consistencia
cd "$PROJECT_ROOT"

# Iniciar usando el comando básico de PM2 que funciona en todas las versiones
# El formato es: pm2 start <interpreter> --name <name> -- <script>
pm2 start venv/bin/python3 --name "background-updater" -- scripts/background_updater.py 2>&1

# Verificar que se inició correctamente
sleep 1
if pm2 list 2>/dev/null | grep -q "background-updater"; then
    # Guardar la configuración de PM2
    pm2 save 2>/dev/null || true
    echo "✅ Background updater iniciado correctamente"
else
    echo "⚠️  Warning: Background updater podría no haberse iniciado correctamente"
    echo "   Verifica con: pm2 list"
fi
echo ""
echo "📊 Comandos útiles:"
echo "  pm2 logs background-updater    # Ver logs"
echo "  pm2 status                     # Ver estado"
echo "  pm2 stop background-updater    # Detener"
echo "  pm2 restart background-updater # Reiniciar"
