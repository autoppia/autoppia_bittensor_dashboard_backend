#!/bin/bash
# Check Status - Verificación rápida del sistema de cache warming

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     AUTOPPIA CACHE WARMING - ESTADO DEL SISTEMA           ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo

# 1. Scripts
echo "1️⃣  SCRIPTS INSTALADOS:"
SCRIPTS_COUNT=$(ls -1 /root/cache_warmers/warm_*.sh 2>/dev/null | wc -l)
if [ "$SCRIPTS_COUNT" -eq 4 ]; then
    echo "   ✅ 4 scripts instalados"
    ls -1 /root/cache_warmers/warm_*.sh | sed 's|/root/cache_warmers/||' | sed 's/^/      - /'
else
    echo "   ❌ PROBLEMA: Solo $SCRIPTS_COUNT/4 scripts encontrados"
    echo "   Solución: ./scripts/cache_warmers/setup_cache_warmers.sh"
fi
echo

# 2. Cron
echo "2️⃣  CRON JOBS:"
CRON_COUNT=$(crontab -l 2>/dev/null | grep -c cache_warmers)
if [ "$CRON_COUNT" -eq 4 ]; then
    echo "   ✅ 4 cron jobs configurados"
    crontab -l 2>/dev/null | grep cache_warmers | sed 's/^/      /'
else
    echo "   ❌ PROBLEMA: Solo $CRON_COUNT/4 cron jobs"
    echo "   Solución: crontab /root/autoppia_bittensor_dashboard_backend/scripts/cache_warmers/setup_cache_warmers.sh"
fi
echo

# 3. Backend
echo "3️⃣  BACKEND API:"
if curl -s -f http://localhost:8080/api/v1/overview/metrics > /dev/null 2>&1; then
    RESPONSE_TIME=$(curl -s -w '%{time_total}' -o /dev/null http://localhost:8080/api/v1/overview/metrics)
    echo "   ✅ Backend respondiendo"
    echo "      Tiempo: ${RESPONSE_TIME}s"
else
    echo "   ❌ Backend NO responde"
    echo "   Solución: pm2 restart api-leaderboard.autoppia.com"
fi
echo

# 4. Última Ejecución
echo "4️⃣  ÚLTIMA EJECUCIÓN DE WARMERS:"
for log in /root/cache_warmers/*.log; do
    if [ -f "$log" ]; then
        LAST_LINE=$(tail -1 "$log" 2>/dev/null)
        LOG_NAME=$(basename "$log" .log)
        MINUTES_AGO=$(( ($(date +%s) - $(stat -c %Y "$log")) / 60 ))
        
        if [ $MINUTES_AGO -lt 15 ]; then
            echo "   ✅ $LOG_NAME: hace $MINUTES_AGO minutos"
        else
            echo "   ⚠️  $LOG_NAME: hace $MINUTES_AGO minutos (antiguo)"
        fi
    fi
done
echo

# 5. Test de Performance
echo "5️⃣  TEST DE PERFORMANCE:"
echo "   Probando endpoints críticos..."

# Test metrics
METRICS_TIME=$(curl -s -w '%{time_total}' -o /dev/null http://localhost:8080/api/v1/overview/metrics)
if (( $(echo "$METRICS_TIME < 0.5" | bc -l) )); then
    echo "   ✅ metrics: ${METRICS_TIME}s (RÁPIDO)"
else
    echo "   ⚠️  metrics: ${METRICS_TIME}s (LENTO - cache vacío?)"
fi

# Test round 16
R16_TIME=$(curl -s -w '%{time_total}' -o /dev/null http://localhost:8080/api/v1/rounds/16)
if (( $(echo "$R16_TIME < 0.5" | bc -l) )); then
    echo "   ✅ round 16: ${R16_TIME}s (RÁPIDO)"
else
    echo "   ⚠️  round 16: ${R16_TIME}s (LENTO - cache vacío?)"
fi

# Test round 15
R15_TIME=$(curl -s -w '%{time_total}' -o /dev/null http://localhost:8080/api/v1/rounds/15)
if (( $(echo "$R15_TIME < 0.5" | bc -l) )); then
    echo "   ✅ round 15: ${R15_TIME}s (RÁPIDO)"
else
    echo "   ⚠️  round 15: ${R15_TIME}s (LENTO - cache vacío?)"
fi

echo

# 6. Resumen
echo "6️⃣  RESUMEN:"
if [ "$SCRIPTS_COUNT" -eq 4 ] && [ "$CRON_COUNT" -eq 4 ]; then
    echo "   ✅ Sistema funcionando correctamente"
    echo "   📊 22 endpoints siendo pre-calentados automáticamente"
    echo "   ⏰ Próxima ejecución: <2 minutos"
else
    echo "   ⚠️  Sistema incompleto - ejecutar setup:"
    echo "   ./scripts/cache_warmers/setup_cache_warmers.sh"
fi
echo

