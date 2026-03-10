#!/bin/bash
# Script para levantar Redis localmente

set -e

echo "🔍 Verificando Redis..."

# Verificar si Redis está corriendo
if pgrep -x "redis-server" > /dev/null; then
    echo "✅ Redis ya está corriendo (PID: $(pgrep -x redis-server))"
    # Verificar que realmente responde
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping &> /dev/null; then
            exit 0
        fi
    else
        # Si no hay redis-cli, asumir que está bien si el proceso existe
        exit 0
    fi
fi

# Verificar si Redis está instalado
if ! command -v redis-server &> /dev/null; then
    echo "❌ Redis no está instalado"
    echo "📦 Intentando instalar Redis..."

    # Intentar instalar Redis
    INSTALLED=false
    if command -v apt-get &> /dev/null; then
        if command -v sudo &> /dev/null; then
            echo "   Ejecutando: sudo apt-get update && sudo apt-get install -y redis-server redis-tools"
            sudo apt-get update && sudo apt-get install -y redis-server redis-tools && INSTALLED=true || true
        else
            echo "   ⚠️  sudo no disponible, intentando sin sudo..."
            apt-get update && apt-get install -y redis-server redis-tools && INSTALLED=true || true
        fi
    elif command -v yum &> /dev/null; then
        if command -v sudo &> /dev/null; then
            sudo yum install -y redis && INSTALLED=true || true
        else
            yum install -y redis && INSTALLED=true || true
        fi
    elif command -v brew &> /dev/null; then
        brew install redis && INSTALLED=true || true
    fi

    if [[ "$INSTALLED" == false ]]; then
        echo "❌ No se pudo instalar Redis automáticamente"
        echo "Por favor instala Redis manualmente:"
        echo "  - Ubuntu/Debian: sudo apt-get install redis-server redis-tools"
        echo "  - CentOS/RHEL: sudo yum install redis"
        echo "  - macOS: brew install redis"
        exit 1
    fi
fi

# Iniciar Redis
echo "🚀 Iniciando Redis..."
# Intentar iniciar sin sudo primero
redis-server --daemonize yes --port 6379 2>/dev/null || {
    # Si falla, podría ser un problema de permisos o que el puerto esté ocupado
    # Verificar si el puerto está ocupado
    if lsof -ti:"6379" > /dev/null 2>&1 || ss -tuln 2>/dev/null | grep -q ":6379"; then
        echo "   ⚠️  Puerto 6379 ya está en uso, verificando si es Redis..."
        sleep 1
        if pgrep -x "redis-server" > /dev/null; then
            echo "   ✅ Redis ya está corriendo"
            exit 0
        else
            echo "   ❌ Puerto 6379 ocupado por otro proceso"
            exit 1
        fi
    else
        echo "   ⚠️  No se pudo iniciar Redis automáticamente"
        echo "   💡 Intenta manualmente: redis-server --daemonize yes --port 6379"
        exit 1
    fi
}

# Esperar a que Redis esté listo
sleep 2

# Verificar que Redis esté corriendo y responda
if pgrep -x "redis-server" > /dev/null; then
    # Verificar que responda al ping
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping &> /dev/null; then
            echo "✅ Redis iniciado correctamente (PID: $(pgrep -x redis-server))"
            echo "📊 Puerto: 6379"
        else
            echo "⚠️  Redis está corriendo pero no responde al ping"
            echo "   Esperando un poco más..."
            sleep 2
            if redis-cli ping &> /dev/null; then
                echo "✅ Redis iniciado correctamente"
            else
                echo "❌ Redis no responde"
                exit 1
            fi
        fi
    else
        echo "✅ Redis iniciado correctamente (PID: $(pgrep -x redis-server))"
        echo "📊 Puerto: 6379"
        echo "   ⚠️  redis-cli no disponible para verificar conexión"
    fi
else
    echo "❌ Error: Redis no se pudo iniciar" >&2
    exit 1
fi
