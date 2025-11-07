#!/bin/bash
# Script para aplicar la corrección de Redis en el servidor

echo "🔧 Arreglando Redis en el servidor..."
echo ""

# 1. Detener Redis
echo "1. Deteniendo Redis..."
docker compose down redis
echo ""

# 2. Verificar el archivo
echo "2. Verificando docker-compose.yml..."
if grep -q "version:" docker-compose.yml; then
    echo "   ⚠️  Necesitas actualizar docker-compose.yml (eliminar 'version: 3.8')"
else
    echo "   ✓ docker-compose.yml OK (sin version)"
fi
echo ""

# 3. Verificar REDIS_PASSWORD en .env
echo "3. Verificando configuración de Redis..."
if grep -q "REDIS_PASSWORD_DEVELOPMENT=" .env; then
    echo "   ✓ REDIS_PASSWORD_DEVELOPMENT encontrado"
else
    echo "   ⚠️  Falta REDIS_PASSWORD_DEVELOPMENT en .env"
fi
echo ""

# 4. Iniciar Redis
echo "4. Iniciando Redis con la nueva configuración..."
docker compose up -d redis
echo ""

# 5. Esperar que inicie
echo "5. Esperando 5 segundos..."
sleep 5
echo ""

# 6. Verificar estado
echo "6. Verificando estado de Redis..."
docker compose ps | grep redis
echo ""

# 7. Ver logs
echo "7. Últimas líneas de los logs:"
docker compose logs --tail=10 redis
echo ""

# 8. Probar conexión
echo "8. Probando conexión..."
if docker compose exec redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
    echo "   ✅ Redis funciona correctamente!"
else
    echo "   ⚠️  Redis puede no estar funcionando correctamente"
    echo "   Ver logs: docker compose logs redis"
fi
echo ""

echo "✅ Proceso completado"

