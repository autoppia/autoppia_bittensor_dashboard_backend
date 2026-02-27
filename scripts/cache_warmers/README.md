# Cache Warmers - Multi-Frequency System

Pre-calienta el caché de Redis con diferentes frecuencias según la criticidad de los datos, garantizando que los usuarios **SIEMPRE** vean respuestas instantáneas (<300ms).

## 🎯 Filosofía

Diferentes tipos de datos requieren diferentes frecuencias de actualización:

- **Current Round**: Muy dinámico → actualiza cada 2 min
- **Recent Rounds**: Estable → actualiza cada 5 min
- **Overview Metrics**: General → actualiza cada 5 min
- **Lists**: Menos crítico → actualiza cada 10 min

## 📋 Scripts

### 1. `warm_current_round.sh` (Cada 2 minutos)

Pre-calienta el round actual y todos sus datos relacionados:

- `/api/v1/rounds/{current}`
- `/api/v1/rounds/{current}/basic`
- `/api/v1/rounds/{current}/miners`
- `/api/v1/rounds/{current}/validators`
- `/api/v1/miner-list?round={current}`
- `/api/v1/agent-runs?roundId={current}`

**Por qué 2 minutos:** El round actual cambia constantemente con nuevas evaluations.

### 2. `warm_recent_rounds.sh` (Cada 5 minutos)

Pre-calienta las 3 rounds anteriores al current (ya completadas):

- Round N-1
- Round N-2
- Round N-3

**Por qué 5 minutos:** Estos rounds ya están finalizados, no cambian.

### 3. `warm_overview.sh` (Cada 5 minutos)

Pre-calienta endpoints de overview (homepage):

- `/api/v1/overview/metrics`
- `/api/v1/overview/validators`
- `/api/v1/overview/leaderboard?timeRange=15R`
- `/api/v1/overview/network-status`
- `/api/v1/overview/statistics`
- `/api/v1/overview/rounds/current`

**Por qué 5 minutos:** Balance entre frescura de datos y carga del servidor.

### 4. `warm_lists.sh` (Cada 10 minutos)

Pre-calienta listas generales:

- `/api/v1/rounds` (paginated)
- `/api/v1/miner-list`
- `/api/v1/agent-runs`
- `/api/v1/agents`

**Por qué 10 minutos:** Datos menos críticos, cambian menos frecuentemente.

## 🚀 Instalación

```bash
cd /path/to/autoppia_bittensor_dashboard_backend
./scripts/cache_warmers/setup_cache_warmers.sh
```

Este script maestro:

1. Crea directorios necesarios (`/root/cache_warmers`, `/var/log/autoppia`)
2. Copia los 4 scripts de warming
3. Los hace ejecutables
4. Configura cron jobs con las frecuencias correctas
5. Ejecuta una primera ronda para llenar el caché

## 📊 Monitoreo

### Ver logs en tiempo real:

```bash
tail -f /var/log/autoppia/*_warmer.log
```

### Verificar que cron está activo:

```bash
crontab -l | grep cache_warmers
```

### Ejecutar manualmente un warmer:

```bash
/root/cache_warmers/warm_current_round.sh
```

## 🎯 Resultados Esperados

Con este sistema:

- ✅ Current round: <150ms SIEMPRE
- ✅ Rounds 13-15: <300ms SIEMPRE
- ✅ Overview: <10ms SIEMPRE
- ✅ Cache NUNCA vacío (warmers ejecutan antes de expiración)
- ✅ Sin asyncio/event loop issues
- ✅ Fácil de mantener y debuggear

## 🔧 Configuración

### Cambiar frecuencias:

Editar `/etc/crontab` o `crontab -e`:

```bash
*/2 * * * * /root/cache_warmers/warm_current_round.sh  # Cambiar a */3 para cada 3 min
```

### Añadir más endpoints:

Editar el script correspondiente y añadir:

```bash
curl -s "$BASE/api/v1/tu-endpoint" > /dev/null &
```

## ⚠️ Troubleshooting

### Si los warmers no ejecutan:

```bash
# Verificar permisos
ls -la /root/cache_warmers/
chmod +x /root/cache_warmers/*.sh

# Verificar cron
crontab -l

# Ver logs de cron
grep CRON /var/log/syslog | tail -20
```

### Si el caché sigue vacío:

```bash
# Ejecutar manualmente y ver errores
/root/cache_warmers/warm_current_round.sh

# Verificar que el backend está corriendo
curl http://localhost:8080/api/v1/overview/metrics
```

## 🎯 vs Background Thread

**Por qué cron jobs en vez de background thread:**

| Aspecto                    | Background Thread | Cron Jobs         |
| -------------------------- | ----------------- | ----------------- |
| **Asyncio conflicts**      | ❌ Sí             | ✅ No             |
| **Event loop issues**      | ❌ Sí             | ✅ No             |
| **Diferentes frecuencias** | ❌ Difícil        | ✅ Fácil          |
| **Debugging**              | ❌ Complejo       | ✅ Simple         |
| **Logs separados**         | ❌ Mezclados      | ✅ Uno por warmer |
| **Reinicio independiente** | ❌ Con API        | ✅ Independiente  |
| **Mantenibilidad**         | ❌ Media          | ✅ Alta           |

**Conclusión:** Cron jobs son más simples, robustos y flexibles.
