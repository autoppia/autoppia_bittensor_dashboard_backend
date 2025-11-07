#!/bin/bash
# Script para probar Redis y verificar la contraseña

echo "🔍 Verificando Redis..."
echo ""

# 1. Ver estado
echo "1️⃣  Estado de Redis:"
docker compose ps redis
echo ""

# 2. Ver logs (últimas líneas)
echo "2️⃣  Últimos logs:"
docker compose logs --tail=5 redis
echo ""

# 3. Probar conexión SIN contraseña
echo "3️⃣  Probando conexión SIN contraseña:"
if docker compose exec redis redis-cli ping 2>&1 | grep -q "PONG"; then
    echo "   ✅ Redis responde SIN contraseña"
    echo "   ⚠️  Redis NO tiene contraseña configurada"
    HAS_PASSWORD="no"
elif docker compose exec redis redis-cli ping 2>&1 | grep -q "NOAUTH"; then
    echo "   ❌ Redis requiere contraseña"
    echo "   ✅ Redis SÍ tiene contraseña configurada"
    HAS_PASSWORD="yes"
else
    echo "   ⚠️  No se pudo determinar el estado"
    HAS_PASSWORD="unknown"
fi
echo ""

# 4. Si tiene contraseña, intentar con contraseña
if [ "$HAS_PASSWORD" = "yes" ]; then
    echo "4️⃣  Probando conexión CON contraseña..."
    
    # Cargar .env
    if [ -f .env ]; then
        export $(grep -v '^#' .env | xargs)
        ENVIRONMENT="${ENVIRONMENT:-local}"
        ENVIRONMENT_UPPER=$(echo "$ENVIRONMENT" | tr '[:lower:]' '[:upper:]')
        REDIS_PASSWORD_VAR="REDIS_PASSWORD_${ENVIRONMENT_UPPER}"
        PASSWORD=$(eval echo \$${REDIS_PASSWORD_VAR})
        
        if [ -n "$PASSWORD" ]; then
            echo "   Usando contraseña del entorno: $ENVIRONMENT"
            if docker compose exec redis redis-cli -a "$PASSWORD" ping 2>&1 | grep -q "PONG"; then
                echo "   ✅ Redis responde con la contraseña correcta"
                echo ""
                echo "   📝 Contraseña: $PASSWORD"
            else
                echo "   ❌ La contraseña del .env no funciona"
            fi
        else
            echo "   ⚠️  No hay contraseña en .env para el entorno $ENVIRONMENT"
        fi
    fi
fi
echo ""

# 5. Información de conexión
echo "5️⃣  Información de conexión:"
echo "   Host: localhost (o IP del servidor)"
echo "   Puerto: 6379"
if [ "$HAS_PASSWORD" = "yes" ]; then
    echo "   Contraseña: $PASSWORD"
else
    echo "   Contraseña: (ninguna)"
fi
echo ""

# 6. Probar set/get
echo "6️⃣  Probando SET/GET:"
if [ "$HAS_PASSWORD" = "yes" ] && [ -n "$PASSWORD" ]; then
    docker compose exec redis redis-cli -a "$PASSWORD" SET test_key "test_value" 2>&1 | grep -v "Warning"
    RESULT=$(docker compose exec redis redis-cli -a "$PASSWORD" GET test_key 2>&1 | grep -v "Warning")
else
    docker compose exec redis redis-cli SET test_key "test_value"
    RESULT=$(docker compose exec redis redis-cli GET test_key)
fi

if [ "$RESULT" = "test_value" ]; then
    echo "   ✅ SET/GET funciona correctamente"
    echo "   Valor almacenado: $RESULT"
else
    echo "   ⚠️  Problema con SET/GET"
fi
echo ""

echo "✅ Verificación completada"

