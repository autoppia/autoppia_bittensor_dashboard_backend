# 🚀 Cache Warmers - Guía Rápida

## ✅ ¿Está TODO Funcionando?

**Ejecuta este comando en el servidor:**

```bash
ssh contabo-iwap-production "/root/cache_warmers/check_status.sh"
```

Debería mostrar:
- ✅ 4 scripts instalados
- ✅ 4 cron jobs activos
- ✅ Backend API respondiendo
- ✅ Logs recientes (<5 minutos)
- ✅ Todos los endpoints <500ms

---

## 🔄 ¿Cómo Levantarlo Después de un Reinicio?

### **Escenario 1: Reinicio del Servidor (reboot)**

```bash
ssh contabo-iwap-production

# 1. Backend se levanta automáticamente (PM2)
pm2 list  # Verificar que api-leaderboard.autoppia.com está online

# 2. Cron se activa automáticamente
crontab -l | grep cache_warmers  # Verificar que están configurados

# 3. Esperar 2 minutos (primera ejecución del cron)
# O ejecutar manualmente:
/root/cache_warmers/warm_overview.sh
/root/cache_warmers/warm_current_round.sh

# 4. Verificar que funciona
curl -s http://localhost:8080/api/v1/overview/metrics | head -5
```

**TODO se levanta automáticamente. No hay que hacer nada.** ✅

---

### **Escenario 2: Reinicio Solo del Backend (PM2)**

```bash
ssh contabo-iwap-production
pm2 restart api-leaderboard.autoppia.com

# Cron sigue activo (no se detiene con PM2)
# En 2 minutos el caché estará lleno de nuevo
```

---

### **Escenario 3: Deploy de Código Nuevo**

```bash
ssh contabo-iwap-production
cd /root/autoppia_bittensor_dashboard_backend

# 1. Pull de cambios
git pull

# 2. Reiniciar backend
pm2 restart api-leaderboard.autoppia.com

# 3. Si es la primera vez, instalar cache warmers:
./scripts/cache_warmers/setup_cache_warmers.sh

# 4. Esperar 2 min o ejecutar manualmente
/root/cache_warmers/warm_current_round.sh
```

---

## 📊 ¿Cómo Saber que Funciona?

### **Método 1: Comando Rápido**

```bash
ssh contabo-iwap-production "
  echo 'Testing...'; 
  curl -s -w 'metrics: %{time_total}s | ' -o /dev/null http://localhost:8080/api/v1/overview/metrics;
  curl -s -w 'round 16: %{time_total}s\n' -o /dev/null http://localhost:8080/api/v1/rounds/16;
"
```

**Resultado esperado:** `metrics: 0.005s | round 16: 0.030s`

Si ves esto → ✅ **TODO FUNCIONA**

---

### **Método 2: Ver Logs de Cron**

```bash
ssh contabo-iwap-production "tail -10 /root/cache_warmers/*.log"
```

**Deberías ver:**
```
21:16:03: Current round 16    ← Menos de 2 min de antigüedad
21:15:01: Overview warmed     ← Menos de 5 min de antigüedad  
21:15:01: Recent rounds 13-15 ← Menos de 5 min de antigüedad
21:10:40: Lists warmed        ← Menos de 10 min de antigüedad
```

---

### **Método 3: Monitoreo del Sistema**

```bash
ssh contabo-iwap-production "/root/monitor_api.sh"
```

**Valores normales:**
```
✅ Load Average: <1.0
✅ Memoria: <5GB usado
✅ CPU: <30%
✅ Conexiones PostgreSQL: <20
✅ Tiempo respuesta: <0.01s
✅ Últimos errores: (vacío)
```

---

## 🔧 Troubleshooting

### **Problema: Endpoints tardan >1 segundo**

```bash
# 1. Ver si cron está ejecutando
ssh contabo-iwap-production "tail -20 /root/cache_warmers/*.log"

# 2. Si no hay logs recientes, ejecutar manualmente
ssh contabo-iwap-production "
  /root/cache_warmers/warm_overview.sh;
  /root/cache_warmers/warm_current_round.sh;
  /root/cache_warmers/warm_recent_rounds.sh;
"

# 3. Verificar que cron está activo
ssh contabo-iwap-production "crontab -l | grep cache_warmers"

# 4. Si falta, reinstalar:
ssh contabo-iwap-production "cd /root/autoppia_bittensor_dashboard_backend && ./scripts/cache_warmers/setup_cache_warmers.sh"
```

---

### **Problema: Backend no responde**

```bash
# Ver estado PM2
ssh contabo-iwap-production "pm2 list"

# Si está stopped, iniciar
ssh contabo-iwap-production "pm2 restart api-leaderboard.autoppia.com"

# Ver errores
ssh contabo-iwap-production "pm2 logs api-leaderboard.autoppia.com --lines 50"
```

---

### **Problema: Cron no ejecuta**

```bash
# Ver si cron daemon está activo
ssh contabo-iwap-production "systemctl status cron"

# Si no está activo
ssh contabo-iwap-production "systemctl start cron"

# Reinstalar crontab
ssh contabo-iwap-production "cd /root/autoppia_bittensor_dashboard_backend && ./scripts/cache_warmers/setup_cache_warmers.sh"
```

---

## 📋 Checklist Diario (Opcional)

```bash
# Ejecutar 1 vez al día para verificar salud del sistema:
ssh contabo-iwap-production "
  echo '=== Health Check ===';
  echo '1. Backend:' && curl -s http://localhost:8080/api/v1/overview/metrics > /dev/null && echo '   ✅ OK' || echo '   ❌ FAIL';
  echo '2. Cron:' && crontab -l | grep -q cache_warmers && echo '   ✅ OK' || echo '   ❌ FAIL';
  echo '3. Logs recientes:' && find /root/cache_warmers/*.log -mmin -10 | wc -l && echo '   logs de últimos 10 min';
  echo '4. Performance:' && curl -s -w '   metrics: %{time_total}s\n' -o /dev/null http://localhost:8080/api/v1/overview/metrics;
"
```

---

## 🎯 Resumen Ejecutivo

### **✅ Sistema Activo AHORA:**
- 4 cache warmers corriendo automáticamente
- 22 endpoints pre-calentados
- Cron ejecuta cada 2-10 minutos
- Backend respondiendo en <100ms

### **🔄 Después de Reinicio:**
- PM2 levanta backend automáticamente
- Cron levanta automáticamente
- En 2 minutos: Cache lleno de nuevo
- **NO hay que hacer nada manual** ✅

### **📊 Verificación:**
```bash
# Comando simple:
curl -s http://localhost:8080/api/v1/overview/metrics

# Si responde → TODO funciona ✅
```

---

**TODO ESTÁ LISTO Y FUNCIONANDO** 🎉

