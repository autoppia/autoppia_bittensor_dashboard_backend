# 🔴 Guía Redis Insight - Verificar Redis

## 1️⃣ Verificar Redis en el servidor

Ejecuta el script de prueba:

```bash
cd ~/autoppia_bittensor_dashboard_backend
./test_redis.sh
```

Este script te dirá:

- ✅ Si Redis está funcionando
- ✅ Si tiene contraseña configurada
- ✅ Cuál es la contraseña
- ✅ Host y puerto para conectarte

## 2️⃣ Instalar Redis Insight

### Opción A: Desktop (tu máquina)

1. Descarga Redis Insight: https://redis.io/insight/
2. Instala la aplicación

### Opción B: Docker (en el servidor)

```bash
docker run -d \
  --name redis-insight \
  -p 5540:5540 \
  redis/redis-insight:latest
```

Luego accede desde tu navegador: `http://IP_DEL_SERVIDOR:5540`

## 3️⃣ Conectar Redis Insight

### Configuración de conexión:

**Si Redis está en el mismo servidor que Redis Insight:**

- Host: `host.docker.internal` (si usas Docker) o `localhost`
- Port: `6379`
- Name: `Autoppia DEV` (o el nombre que quieras)
- Username: (dejar vacío)
- Password: `(la que te dio el script test_redis.sh)` o vacío si no tiene

**Si Redis está en un servidor remoto:**

- Host: `IP_DEL_SERVIDOR` (ej: `195.179.228.132`)
- Port: `6379`
- Name: `Autoppia DEV`
- Username: (dejar vacío)
- Password: `(la contraseña de REDIS_PASSWORD_DEVELOPMENT)`

⚠️ **Importante**: Redis solo escucha en `127.0.0.1:6379`, así que **solo puedes conectarte desde el mismo servidor** a menos que cambies la configuración.

## 4️⃣ Configurar túnel SSH para conectar desde tu máquina

Si quieres conectarte desde tu máquina local:

```bash
# Crear túnel SSH
ssh -L 6379:127.0.0.1:6379 admin@195.179.228.132

# En otra terminal, conectar Redis Insight a:
# Host: localhost
# Port: 6379
# Password: (la del entorno)
```

## 5️⃣ Probar en Redis Insight

Una vez conectado:

### Crear una clave de prueba:

1. Ve a la pestaña "Browser"
2. Click en "Add key"
3. Tipo: String
4. Key name: `test:autoppia`
5. Value: `Hello from Redis Insight!`
6. TTL: 3600 (1 hora) o 0 (sin expiración)
7. Click "Add Key"

### Verificar desde el servidor:

```bash
# Si tiene contraseña
docker compose exec redis redis-cli -a "TU_PASSWORD" GET "test:autoppia"

# Si no tiene contraseña
docker compose exec redis redis-cli GET "test:autoppia"
```

Debería devolver: `Hello from Redis Insight!`

## 6️⃣ Probar desde el backend

Crea un endpoint de prueba o usa Python:

```python
# En el servidor
cd ~/autoppia_bittensor_dashboard_backend
source venv/bin/activate
python3

# En Python:
from app.services.redis_cache import redis_cache

# Verificar conexión
redis_cache.is_available()  # Debe devolver True

# Probar set
redis_cache.set("test:backend", "Hello from backend!", ttl=3600)

# Probar get
redis_cache.get("test:backend")  # Debe devolver "Hello from backend!"

# Ver estadísticas
redis_cache.get_stats()
```

## 7️⃣ Verificar desde el frontend

Una vez que el backend esté corriendo, las llamadas al API deberían usar Redis automáticamente para caché.

Para verificarlo:

1. Abre el frontend
2. Haz una llamada a un endpoint que use caché (ej: `/api/v1/rounds`)
3. Ve a Redis Insight y busca las claves que empiecen con `round:` o `agent_run:`
4. Deberías ver claves creadas automáticamente

### Ver claves en Redis Insight:

- Browser → Buscar `*` para ver todas las claves
- Deberías ver claves como:
  - `round:detail:*`
  - `agent_run:statistics:*`
  - `task:*`

## 🔍 Comandos útiles en Redis CLI

```bash
# Ver todas las claves
docker compose exec redis redis-cli KEYS "*"

# Contar claves
docker compose exec redis redis-cli DBSIZE

# Ver info de memoria
docker compose exec redis redis-cli INFO memory

# Borrar todas las claves (CUIDADO!)
docker compose exec redis redis-cli FLUSHALL

# Borrar claves que empiecen con "test:"
docker compose exec redis redis-cli --scan --pattern "test:*" | xargs docker compose exec redis redis-cli DEL
```

## ✅ Checklist de verificación

- [ ] Redis está corriendo (`docker compose ps`)
- [ ] Redis responde a ping
- [ ] Conoces la contraseña (si tiene)
- [ ] Redis Insight conectado correctamente
- [ ] Puedes crear y leer claves desde Redis Insight
- [ ] Backend se conecta a Redis (`redis_cache.is_available()`)
- [ ] Backend puede escribir y leer de Redis
- [ ] Las llamadas al API crean claves en Redis (visible en Redis Insight)

## 🚀 Siguiente paso: Frontend

Una vez que Redis funcione:

1. Asegúrate de que el backend esté corriendo con Redis habilitado
2. Haz llamadas desde el frontend
3. Verifica en Redis Insight que se crean las claves de caché
4. Las respuestas deberían ser más rápidas en la segunda llamada (caché)
