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
curl -s "$API_URL?key=$API_KEY&website=autocinema&success=true&limit=5" | jq '.data.tasks | length'
echo ""

# 2. Tareas fallidas
echo "2️⃣ Tareas fallidas (success=false)"
echo "----------------------------------"
curl -s "$API_URL?key=$API_KEY&website=autocinema&success=false&limit=5" | jq '.data.tasks | length'
echo ""

# 3. Todas las tareas (sin filtro success)
echo "3️⃣ Todas las tareas"
echo "----------------------------------"
curl -s "$API_URL?key=$API_KEY&website=autocinema&limit=5" | jq '.data.tasks | length'
echo ""

# 4. Filtrar por caso de uso
echo "4️⃣ Por caso de uso"
echo "----------------------------------"
curl -s "$API_URL?key=$API_KEY&useCase=FILM%20DETAIL&limit=5" | jq '.data.tasks | length'
echo ""

# 5. Por miner UID
echo "5️⃣ Por miner UID"
echo "----------------------------------"
curl -s "$API_URL?key=$API_KEY&minerUid=42&limit=5" | jq '.data.tasks | length'
echo ""

# 6. Ver estructura completa (1 tarea)
echo "6️⃣ Estructura de respuesta (primera tarea)"
echo "----------------------------------"
curl -s "$API_URL?key=$API_KEY&website=autocinema&success=true&limit=1" | jq '.data.tasks[0]'
echo ""

# 7. Test sin API key (debe fallar)
echo "7️⃣ Sin API key (debe devolver 401)"
echo "----------------------------------"
curl -s "$API_URL?website=autocinema&limit=1" | jq '.detail'
echo ""

echo "=========================================="
echo "✅ Tests completados"
echo "=========================================="

