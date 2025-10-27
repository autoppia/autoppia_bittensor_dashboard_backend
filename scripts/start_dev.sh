#!/bin/bash

# ════════════════════════════════════════════════════════════════════════════
# Script para iniciar el backend con conexión a la base de datos de DEV
# ════════════════════════════════════════════════════════════════════════════

set -e  # Salir si hay algún error

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuración
SSH_USER="admin"
SSH_HOST="195.179.228.132"
SSH_PORT="22"
LOCAL_PORT="5434"
REMOTE_PORT="5432"
APP_PORT="8000"

echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Autoppia Backend - Modo Desarrollo${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo ""

# ─── 1. Verificar si el túnel SSH ya está activo ───
echo -e "${YELLOW}[1/5]${NC} Verificando túnel SSH..."
if lsof -Pi :$LOCAL_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    SSH_PID=$(lsof -Pi :$LOCAL_PORT -sTCP:LISTEN -t)
    echo -e "${GREEN}✓${NC} Túnel SSH ya está activo (PID: $SSH_PID)"
else
    echo -e "${YELLOW}⚠${NC} Túnel SSH no encontrado, creando..."
    
    # Crear túnel SSH en background
    ssh -f -N -L $LOCAL_PORT:127.0.0.1:$REMOTE_PORT $SSH_USER@$SSH_HOST
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} Túnel SSH creado correctamente"
        sleep 2
    else
        echo -e "${RED}✗${NC} Error al crear el túnel SSH"
        exit 1
    fi
fi

# ─── 2. Verificar conexión a la base de datos ───
echo -e "${YELLOW}[2/5]${NC} Verificando conexión a la base de datos..."
export PGPASSWORD='REMOVED_DEV_DB_PASSWORD'
if psql -h localhost -p $LOCAL_PORT -U autoppia_user -d autoppia_dev -c '\conninfo' >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Conexión a base de datos exitosa"
else
    echo -e "${RED}✗${NC} Error: No se puede conectar a la base de datos"
    exit 1
fi

# ─── 3. Activar entorno virtual ───
echo -e "${YELLOW}[3/5]${NC} Activando entorno virtual..."
if [ -d "venv" ]; then
    source venv/bin/activate
    echo -e "${GREEN}✓${NC} Entorno virtual activado"
else
    echo -e "${RED}✗${NC} Error: No se encuentra el entorno virtual"
    exit 1
fi

# ─── 4. Verificar dependencias ───
echo -e "${YELLOW}[4/5]${NC} Verificando dependencias..."
if python -c "import fastapi, uvicorn, sqlalchemy" 2>/dev/null; then
    echo -e "${GREEN}✓${NC} Dependencias instaladas"
else
    echo -e "${YELLOW}⚠${NC} Instalando dependencias..."
    pip install -q -r requirements.txt
    echo -e "${GREEN}✓${NC} Dependencias instaladas"
fi

# ─── 5. Iniciar la aplicación ───
echo -e "${YELLOW}[5/5]${NC} Iniciando aplicación..."
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Aplicación iniciada correctamente${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  🚀 API:          ${GREEN}http://localhost:$APP_PORT${NC}"
echo -e "  📚 Docs:         ${GREEN}http://localhost:$APP_PORT/docs${NC}"
echo -e "  📖 ReDoc:        ${GREEN}http://localhost:$APP_PORT/redoc${NC}"
echo -e "  ❤️  Health:       ${GREEN}http://localhost:$APP_PORT/health${NC}"
echo ""
echo -e "  🔒 DB Túnel:     ${GREEN}localhost:$LOCAL_PORT → $SSH_HOST:$REMOTE_PORT${NC}"
echo -e "  📊 Base de datos: ${GREEN}autoppia_dev${NC}"
echo ""
echo -e "${YELLOW}  Presiona Ctrl+C para detener${NC}"
echo ""

# Iniciar uvicorn
uvicorn app.main:app --reload --host 0.0.0.0 --port $APP_PORT

