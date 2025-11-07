# 🔴 Redis Setup - Configuración por Entorno

## 📋 Resumen

Redis está configurado para usar diferentes contraseñas según el entorno (local, development, production) definido en tu `.env`.

## 🚀 Uso Rápido

### Opción 1: Usar el script helper (Recomendado)

```bash
# Iniciar Redis
./scripts/redis_compose.sh up -d redis

# Detener Redis
./scripts/redis_compose.sh down redis

# Ver logs
./scripts/redis_compose.sh logs -f redis

# Reiniciar Redis
./scripts/redis_compose.sh restart redis
```

### Opción 2: Usar docker compose directamente

Primero exporta la contraseña según tu entorno:

```bash
# Cargar .env
export $(grep -v '^#' .env | xargs)

# Determinar entorno y exportar contraseña
ENVIRONMENT="${ENVIRONMENT:-local}"
ENVIRONMENT_UPPER=$(echo "$ENVIRONMENT" | tr '[:lower:]' '[:upper:]')
REDIS_PASSWORD_VAR="REDIS_PASSWORD_${ENVIRONMENT_UPPER}"
export REDIS_PASSWORD=$(eval echo \$${REDIS_PASSWORD_VAR})

# Ahora ejecutar docker compose
docker compose up -d redis
```

## 🔧 Configuración en `.env`

Asegúrate de tener configurado en tu `.env`:

```bash
# Cambiar según el entorno donde estés
ENVIRONMENT=local  # o "development" o "production"

# Contraseñas según entorno
REDIS_PASSWORD_LOCAL=                    # Vacío = sin contraseña
REDIS_PASSWORD_DEVELOPMENT=             # Vacío = sin contraseña
REDIS_PASSWORD_PRODUCTION=autoppia-subnet  # Con contraseña
```

## ✅ Verificar que funciona

```bash
# Probar conexión (usando el script helper)
./scripts/redis_compose.sh exec redis redis-cli ping

# Si tiene contraseña, usar:
./scripts/redis_compose.sh exec redis redis-cli -a "TU_PASSWORD" ping
```

## 📝 Notas Importantes

1. **El script `redis_compose.sh` lee automáticamente** el `ENVIRONMENT` de tu `.env` y establece `REDIS_PASSWORD` según corresponda.

2. **Si `REDIS_PASSWORD_${ENVIRONMENT}` está vacío**, Redis se iniciará sin contraseña.

3. **En producción**, asegúrate de tener `REDIS_PASSWORD_PRODUCTION` configurado con una contraseña segura.

4. **El backend también necesita la contraseña** en su `.env`:
   ```bash
   REDIS_PASSWORD_LOCAL=
   REDIS_PASSWORD_DEVELOPMENT=
   REDIS_PASSWORD_PRODUCTION=autoppia-subnet
   ```

## 🔍 Troubleshooting

### Redis no inicia

```bash
# Ver logs
./scripts/redis_compose.sh logs redis

# Verificar que el directorio existe
mkdir -p data/redis
```

### No se puede conectar desde el backend

1. Verifica que `REDIS_PASSWORD` en el `.env` del backend coincida con la contraseña de Redis.
2. Verifica que Redis está corriendo: `docker compose ps`
3. Verifica el puerto: `sudo netstat -tuln | grep 6379`
