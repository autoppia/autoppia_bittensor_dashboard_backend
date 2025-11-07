#!/bin/bash
# Ejemplos de uso del endpoint /api/v1/tasks/with-solutions

API_URL="http://localhost:8080/api/v1/tasks/with-solutions"
API_KEY="AIagent2025"

echo "=========================================="
echo "🚀 Testing /with-solutions endpoint"
echo "=========================================="
echo ""

# 1. Tareas exitosas de autocinema
echo "1️⃣ Tareas exitosas (success=true)"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&website=autocinema&success=true&limit=5")
if echo "$RESPONSE" | grep -q '"success":true'; then
    echo "✅ OK - Respuesta recibida"
    echo "Respuesta: $(echo "$RESPONSE" | head -c 200)..."
else
    echo "❌ Error: $RESPONSE"
fi
echo ""

# 2. Tareas fallidas
echo "2️⃣ Tareas fallidas (success=false)"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&website=autocinema&success=false&limit=5")
if echo "$RESPONSE" | grep -q '"success":true'; then
    echo "✅ OK - Respuesta recibida"
    echo "Respuesta: $(echo "$RESPONSE" | head -c 200)..."
else
    echo "❌ Error: $RESPONSE"
fi
echo ""

# 3. Todas las tareas (sin filtro success)
echo "3️⃣ Todas las tareas"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&website=autocinema&limit=5")
if echo "$RESPONSE" | grep -q '"success":true'; then
    echo "✅ OK - Respuesta recibida"
    echo "Respuesta: $(echo "$RESPONSE" | head -c 200)..."
else
    echo "❌ Error: $RESPONSE"
fi
echo ""

# 4. Filtrar por caso de uso
echo "4️⃣ Por caso de uso"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&useCase=FILM%20DETAIL&limit=5")
if echo "$RESPONSE" | grep -q '"success":true'; then
    echo "✅ OK - Respuesta recibida"
    echo "Respuesta: $(echo "$RESPONSE" | head -c 200)..."
else
    echo "❌ Error: $RESPONSE"
fi
echo ""

# 5. Por miner UID
echo "5️⃣ Por miner UID"
echo "----------------------------------"
RESPONSE=$(curl -s "$API_URL?key=$API_KEY&minerUid=42&limit=5")
if echo "$RESPONSE" | grep -q '"success":true'; then
    echo "✅ OK - Respuesta recibida"
    echo "Respuesta: $(echo "$RESPONSE" | head -c 200)..."
else
    echo "❌ Error: $RESPONSE"
fi
echo ""

# 6. Ver estructura completa (1 tarea)
echo "6️⃣ Estructura de respuesta (primera tarea)"
echo "----------------------------------"
curl -s "$API_URL?key=$API_KEY&website=autocinema&success=true&limit=1"
echo ""
echo ""

# 7. Test sin API key (debe fallar con 422 - FastAPI valida parámetros antes)
echo "7️⃣ Sin API key (debe devolver 422 - parámetro requerido faltante)"
echo "----------------------------------"
RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" "$API_URL?website=autocinema&limit=1")
HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE" | cut -d: -f2)
if [ "$HTTP_CODE" = "422" ]; then
    echo "✅ OK - Devuelve 422 como esperado (parámetro 'key' requerido faltante)"
    echo "Respuesta: $(echo "$RESPONSE" | grep -v "HTTP_CODE")"
else
    echo "❌ Error: Esperaba 422, recibió $HTTP_CODE"
    echo "Respuesta: $RESPONSE"
fi
echo ""

echo "=========================================="
echo "✅ Tests completados"
echo "=========================================="

