# 🚀 Logging Quick Start Guide

## TL;DR

Ahora puedes ver **exactamente** qué requests llegan a tu backend y qué responses envías, todo guardado en archivos de log.

---

## ⚡ Setup Rápido (2 minutos)

### 1. **Añadir configuración al `.env`**

```bash
# Ejecuta el script de configuración automática
python3 scripts/setup_logging.py
```

O añade manualmente a tu `.env`:

```bash
# Logging Configuration
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_FILE_PATH=logs/app.log
LOG_REQUEST_BODY=true
LOG_RESPONSE_BODY=true
```

### 2. **Reiniciar el servidor**

```bash
# Matar proceso anterior (si está corriendo)
sudo pkill -f python

# Iniciar servidor
python3 run.py
```

### 3. **Ver logs en tiempo real**

```bash
# En otra terminal
tail -f logs/requests.log
```

¡Listo! 🎉

---

## 📋 ¿Qué verás en los logs?

### **Antes** (logs actuales):

```
2025-10-21 12:25:07,129 - app - INFO - GET /api/v1/agent-runs/... - 200 - 0.080s
```

😕 **Problema**: No sabes QUÉ datos llegaron ni QUÉ respondiste.

### **Ahora** (logs mejorados):

```json
→ POST /api/v1/validator-rounds | {
  "type": "request",
  "method": "POST",
  "path": "/api/v1/validator-rounds",
  "client": "172.68.229.32",
  "body": {
    "validator_round_id": "round_abc123",
    "validator_uid": 102,
    "agent_runs": [...],
    "tasks": [...]
  }
}

← POST /api/v1/validator-rounds 201 | {
  "type": "response",
  "status": 201,
  "elapsed_seconds": 0.045,
  "body": {
    "success": true,
    "data": {
      "round_id": "round_abc123"
    }
  }
}
```

😊 **Ahora sí**: Ves EXACTAMENTE qué llegó y qué respondiste.

---

## 📂 Archivos de Log

```
logs/
├── app.log              # Logs generales (errores, warnings, info)
├── requests.log         # Requests/responses detallados (JSON)
├── app.log.2025-10-21   # Backup automático del día anterior
└── requests.log.2025-10-21
```

- **Rotación**: Automática cada medianoche
- **Retención**: 30 días
- **Formato**: JSON estructurado para fácil búsqueda

---

## 🔍 Comandos Útiles

### Ver logs en tiempo real

```bash
# Requests/responses
tail -f logs/requests.log

# Logs generales
tail -f logs/app.log

# Ambos
tail -f logs/*.log
```

### Buscar requests específicos

```bash
# Buscar requests a un endpoint
grep "/api/v1/validator-rounds" logs/requests.log

# Buscar requests de un cliente
grep "172.68.229.32" logs/requests.log

# Buscar solo errores
grep "status.*[45][0-9][0-9]" logs/requests.log
```

### Ver solo responses

```bash
grep "← " logs/requests.log
```

### Ver solo requests

```bash
grep "→ " logs/requests.log
```

---

## ⚙️ Configuraciones por Entorno

### 🔧 **Desarrollo** (máximo detalle)

```bash
LOG_LEVEL=DEBUG
LOG_TO_FILE=true
LOG_REQUEST_BODY=true
LOG_RESPONSE_BODY=true
```

### 🚀 **Producción** (moderado)

```bash
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_REQUEST_BODY=false  # No loguear payloads completos
LOG_RESPONSE_BODY=false
```

### 🐛 **Debugging en Producción** (temporal)

```bash
LOG_LEVEL=DEBUG
LOG_TO_FILE=true
LOG_REQUEST_BODY=true
LOG_RESPONSE_BODY=true
```

⚠️ **Recuerda desactivar después de debuggear!**

---

## 🎯 Casos de Uso

### 1. **"No sé si el frontend me está enviando los datos correctos"**

```bash
# Ver todos los POST requests
grep "→ POST" logs/requests.log | tail -20
```

Verás exactamente qué JSON está llegando.

### 2. **"El endpoint devuelve error pero no sé por qué"**

```bash
# Ver errores 500
grep "← .* 500" logs/requests.log
```

Verás el request que causó el error + la respuesta.

### 3. **"¿Cuánto tarda este endpoint?"**

```bash
# Ver tiempos de respuesta
grep "elapsed_seconds" logs/requests.log | grep "/api/v1/validator-rounds"
```

### 4. **"¿Quién está haciendo requests raros?"**

```bash
# Ver IPs de clientes
grep "client" logs/requests.log | sort | uniq -c
```

---

## ⚠️ Importante

### Seguridad

- Los logs contienen **datos sensibles** (request bodies pueden tener passwords, tokens, etc.)
- En producción, considera desactivar `LOG_REQUEST_BODY` y `LOG_RESPONSE_BODY`
- Asegura permisos correctos: `chmod 640 logs/*.log`

### Performance

- El overhead es mínimo (~2-5ms por request)
- Los archivos se rotan automáticamente
- Se eliminan después de 30 días

### Storage

- Estima ~1-10MB por día dependiendo del tráfico
- Máximo ~300MB para 30 días de logs

---

## 🆘 Troubleshooting

### No se crean archivos de log

```bash
# Crear directorio manualmente
mkdir -p logs
chmod 755 logs

# Verificar configuración
grep LOG_ .env
```

### Los logs son demasiado verbosos

```bash
# Reducir nivel de log
LOG_LEVEL=WARNING
LOG_REQUEST_BODY=false
LOG_RESPONSE_BODY=false
```

### Quiero ver las queries SQL

```bash
SQLALCHEMY_LOG_LEVEL=DEBUG
```

---

## 📚 Documentación Completa

Ver `LOGGING.md` para:

- Configuración avanzada
- Análisis de logs
- Integración con herramientas de monitoring
- Best practices
- Security considerations

---

## 🎉 ¡Eso es todo!

Ya tienes logging profesional en tu backend. Ahora puedes:

✅ Ver qué requests llegan  
✅ Ver qué responses envías  
✅ Debuggear problemas fácilmente  
✅ Monitorear performance  
✅ Detectar errores rápidamente

**Siguiente paso**: Reinicia el servidor y haz algunos requests para ver los logs en acción! 🚀
