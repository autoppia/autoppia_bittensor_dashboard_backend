#!/bin/bash

# ════════════════════════════════════════════════════════════════════════════
# Script para detener el backend y el túnel SSH
# ════════════════════════════════════════════════════════════════════════════

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

LOCAL_PORT="5434"
APP_PORT="8000"

echo -e "${YELLOW}════════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  Deteniendo Autoppia Backend${NC}"
echo -e "${YELLOW}════════════════════════════════════════════════════════════════${NC}"
echo ""

# ─── 1. Detener aplicación uvicorn ───
echo -e "${YELLOW}[1/2]${NC} Deteniendo aplicación..."
UVICORN_PIDS=$(pgrep -f "uvicorn app.main:app")
if [ ! -z "$UVICORN_PIDS" ]; then
    echo "$UVICORN_PIDS" | xargs kill 2>/dev/null
    echo -e "${GREEN}✓${NC} Aplicación detenida"
else
    echo -e "${YELLOW}⚠${NC} No se encontró la aplicación corriendo"
fi

# ─── 2. Detener túnel SSH ───
echo -e "${YELLOW}[2/2]${NC} Deteniendo túnel SSH..."
SSH_PID=$(lsof -Pi :$LOCAL_PORT -sTCP:LISTEN -t 2>/dev/null)
if [ ! -z "$SSH_PID" ]; then
    kill $SSH_PID 2>/dev/null
    echo -e "${GREEN}✓${NC} Túnel SSH detenido (PID: $SSH_PID)"
else
    echo -e "${YELLOW}⚠${NC} No se encontró el túnel SSH"
fi

echo ""
echo -e "${GREEN}✓ Todo detenido correctamente${NC}"
echo ""

