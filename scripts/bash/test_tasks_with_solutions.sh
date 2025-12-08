#!/bin/bash
# Script de pruebas completo para /api/v1/tasks/with-solutions

API_URL="http://localhost:8002/api/v1/tasks/with-solutions"
API_KEY="AIagent2025"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}🚀 Testing /with-solutions endpoint${NC}"
echo -e "${GREEN}==========================================${NC}"
echo ""

# Test 1: Sin filtros
echo -e "${YELLOW}1️⃣  Sin filtros (todas las tareas)${NC}"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&limit=5")
TOTAL=$(echo "$RESPONSE" | grep -o '"total":[0-9]*' | head -1 | grep -o '[0-9]*')
if echo "$RESPONSE" | grep -q '"success":true' && [ -n "$TOTAL" ]; then
    echo -e "${GREEN}✓${NC} OK - Total: $TOTAL tareas"
    echo "   Primeros 200 caracteres:"
    echo "   $(echo "$RESPONSE" | head -c 200)..."
else
    echo -e "${RED}✗${NC} Error"
fi
echo ""

# Test 2: Tareas exitosas
echo -e "${YELLOW}2️⃣  Tareas exitosas (success=true)${NC}"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&success=true&limit=5")
TOTAL=$(echo "$RESPONSE" | grep -o '"total":[0-9]*' | head -1 | grep -o '[0-9]*')
if echo "$RESPONSE" | grep -q '"success":true' && [ -n "$TOTAL" ]; then
    echo -e "${GREEN}✓${NC} OK - Total: $TOTAL tareas exitosas"
else
    echo -e "${RED}✗${NC} Error"
fi
echo ""

# Test 3: Tareas fallidas
echo -e "${YELLOW}3️⃣  Tareas fallidas (success=false)${NC}"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&success=false&limit=5")
TOTAL=$(echo "$RESPONSE" | grep -o '"total":[0-9]*' | head -1 | grep -o '[0-9]*')
if echo "$RESPONSE" | grep -q '"success":true' && [ -n "$TOTAL" ]; then
    echo -e "${GREEN}✓${NC} OK - Total: $TOTAL tareas fallidas"
else
    echo -e "${RED}✗${NC} Error"
fi
echo ""

# Test 4: Filtrar por website (autocinema)
echo -e "${YELLOW}4️⃣  Filtro por website (autocinema)${NC}"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&website=autocinema&limit=5")
TOTAL=$(echo "$RESPONSE" | grep -o '"total":[0-9]*' | head -1 | grep -o '[0-9]*')
if echo "$RESPONSE" | grep -q '"success":true' && [ -n "$TOTAL" ]; then
    echo -e "${GREEN}✓${NC} OK - Total: $TOTAL tareas de autocinema"
else
    echo -e "${RED}✗${NC} Error"
fi
echo ""

# Test 5: Filtro combinado (website + success)
echo -e "${YELLOW}5️⃣  Website + success (autocinema exitosas)${NC}"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&website=autocinema&success=true&limit=5")
TOTAL=$(echo "$RESPONSE" | grep -o '"total":[0-9]*' | head -1 | grep -o '[0-9]*')
if echo "$RESPONSE" | grep -q '"success":true' && [ -n "$TOTAL" ]; then
    echo -e "${GREEN}✓${NC} OK - Total: $TOTAL tareas exitosas de autocinema"
else
    echo -e "${RED}✗${NC} Error"
fi
echo ""

# Test 6: Ordenamiento
echo -e "${YELLOW}6️⃣  Con ordenamiento (created_at_desc)${NC}"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&sort=created_at_desc&limit=3")
TOTAL=$(echo "$RESPONSE" | grep -o '"total":[0-9]*' | head -1 | grep -o '[0-9]*')
if echo "$RESPONSE" | grep -q '"success":true' && [ -n "$TOTAL" ]; then
    echo -e "${GREEN}✓${NC} OK - Total: $TOTAL tareas (ordenadas por fecha desc)"
else
    echo -e "${RED}✗${NC} Error"
fi
echo ""

# Test 7: Formato completo de respuesta
echo -e "${YELLOW}7️⃣  Estructura completa (1 tarea)${NC}"
echo "----------------------------------"
curl -s "$API_URL?key=$API_KEY&success=true&limit=1" | python3 -m json.tool | head -50
echo ""

# Test 8: Sin API key (debe fallar)
echo -e "${YELLOW}8️⃣  Sin API key (debe devolver 422)${NC}"
echo "----------------------------------"
RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" "$API_URL?limit=1")
HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE" | cut -d: -f2)
if [ "$HTTP_CODE" = "422" ]; then
    echo -e "${GREEN}✓${NC} OK - Devuelve 422 como esperado"
else
    echo -e "${RED}✗${NC} Error: Esperaba 422, recibió $HTTP_CODE"
fi
echo ""

echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}✅ Tests completados${NC}"
echo -e "${GREEN}==========================================${NC}"

