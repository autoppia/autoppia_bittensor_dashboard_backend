# 🚀 Desarrollo con Base de Datos DEV

Este documento explica cómo conectarse a la base de datos de desarrollo mediante túnel SSH.

## 📋 Requisitos Previos

- SSH configurado con acceso al servidor DEV (`admin@195.179.228.132`)
- Python 3.10+
- Entorno virtual creado (`venv/`)
- PostgreSQL client instalado (para `psql`)

## 🎯 Inicio Rápido

### Iniciar el servidor en modo DEV

```bash
./scripts/start_dev.sh
```

Este script automáticamente:

1. ✅ Crea el túnel SSH si no existe
2. ✅ Verifica la conexión a la base de datos
3. ✅ Activa el entorno virtual
4. ✅ Verifica las dependencias
5. ✅ Inicia la aplicación en `http://localhost:8000`

### Detener el servidor

```bash
./scripts/stop_dev.sh
```

O simplemente presiona `Ctrl+C` en la terminal donde corre la aplicación.

## 🔧 Configuración Manual

Si prefieres hacerlo manualmente:

### 1. Crear el túnel SSH

```bash
ssh -f -N -L 5434:127.0.0.1:5432 admin@195.179.228.132
```

### 2. Verificar la conexión

```bash
psql -h localhost -p 5434 -U autoppia_user -d autoppia_dev
# Contraseña: REMOVED_DEV_DB_PASSWORD
```

### 3. Iniciar la aplicación

```bash
source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 📊 URLs Disponibles

Una vez iniciado:

- **API Base**: http://localhost:8000
- **Documentación (Swagger)**: http://localhost:8000/docs
- **Documentación (ReDoc)**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## 🔍 Verificación

### Verificar túnel SSH activo

```bash
netstat -an | grep 5434
# Debería mostrar: tcp 0 0 127.0.0.1:5434 0.0.0.0:* ESCUCHAR
```

### Verificar aplicación corriendo

```bash
curl http://localhost:8000/health
# Debería retornar: {"status":"healthy",...}
```

## 🛠️ Solución de Problemas

### El puerto 5434 está ocupado

```bash
# Ver qué proceso usa el puerto
lsof -i :5434

# Matar el proceso si es necesario
kill <PID>
```

### Error de autenticación

Verifica que la contraseña en `.env` sea correcta:

```bash
POSTGRES_PASSWORD_DEVELOPMENT=REMOVED_DEV_DB_PASSWORD
```

### El túnel SSH se desconecta

El túnel puede desconectarse por inactividad. Simplemente ejecuta de nuevo:

```bash
./scripts/start_dev.sh
```

## 📝 Variables de Entorno

El archivo `.env` debe contener:

```bash
ENVIRONMENT=development

# DATABASE DEVELOPMENT (via SSH tunnel)
POSTGRES_HOST_DEVELOPMENT=127.0.0.1
POSTGRES_PORT_DEVELOPMENT=5434
POSTGRES_USER_DEVELOPMENT=autoppia_user
POSTGRES_PASSWORD_DEVELOPMENT=REMOVED_DEV_DB_PASSWORD
POSTGRES_DB_DEVELOPMENT=autoppia_dev

# SERVER
HOST=0.0.0.0
PORT=8000
DEBUG=true
LOG_LEVEL=INFO
```

## 🔒 Seguridad

⚠️ **IMPORTANTE**:

- No commitear el archivo `.env` con credenciales
- El túnel SSH usa autenticación por llave pública
- La contraseña de la base de datos solo funciona a través del túnel SSH

## 📚 Más Información

- **Servidor DEV**: `195.179.228.132`
- **Base de Datos**: `autoppia_dev`
- **Usuario DB**: `autoppia_user`
- **Puerto Túnel Local**: `5434`
- **Puerto Servidor**: `5432`
