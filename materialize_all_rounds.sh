#!/bin/bash
# Script para materializar todas las rounds disponibles

echo "🔄 Materializando todas las rounds..."
echo ""

# Obtener el número de la última round
LAST_ROUND=$(curl -s http://localhost:8080/api/v1/rounds?page=1&limit=1 | python3 -c "import sys, json; data=json.load(sys.stdin); rounds=data.get('data', {}).get('rounds', []); print(rounds[0]['roundNumber'] if rounds else 0)" 2>/dev/null)

if [ "$LAST_ROUND" = "0" ]; then
    echo "❌ No se pudo obtener la última round. ¿Está el servidor corriendo?"
    exit 1
fi

echo "📊 Última round detectada: $LAST_ROUND"
echo ""

# Determinar desde qué round empezar (últimas 50 o todas las disponibles)
START_ROUND=$((LAST_ROUND - 49))
if [ $START_ROUND -lt 1 ]; then
    START_ROUND=1
fi

echo "🎯 Materializando desde round $START_ROUND hasta $LAST_ROUND"
echo ""

SUCCESS=0
SKIPPED=0
FAILED=0

for ((i=START_ROUND; i<=LAST_ROUND; i++)); do
    echo -n "Round $i: "
    
    RESPONSE=$(curl -s -X POST http://localhost:8080/admin/materialize-round/$i)
    
    if echo "$RESPONSE" | grep -q '"ok":true'; then
        if echo "$RESPONSE" | grep -q '"already_existed":true'; then
            echo "⏭️  Ya existía"
            SKIPPED=$((SKIPPED + 1))
        else
            SIZE=$(echo "$RESPONSE" | python3 -c "import sys, json; data=json.load(sys.stdin); print(f\"{data.get('data_size_kb', 0):.1f}KB\")" 2>/dev/null || echo "?KB")
            echo "✅ Materializada ($SIZE)"
            SUCCESS=$((SUCCESS + 1))
        fi
    else
        echo "❌ Error"
        FAILED=$((FAILED + 1))
    fi
    
    # Pequeña pausa para no saturar
    sleep 0.1
done

echo ""
echo "=========================================="
echo "Resumen:"
echo "  ✅ Materializadas: $SUCCESS"
echo "  ⏭️  Ya existían:    $SKIPPED"
echo "  ❌ Fallidas:       $FAILED"
echo "  📊 Total:          $((LAST_ROUND - START_ROUND + 1))"
echo "=========================================="

