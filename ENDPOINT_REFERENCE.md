# 📚 Endpoint `/api/v1/tasks/with-solutions` - Referencia Completa

## 🎯 URL Base

```
http://localhost:8002/api/v1/tasks/with-solutions
```

## 🔑 Autenticación

**Parámetro obligatorio:**

- `key=AIagent2025` (query parameter)

## 📋 Filtros Disponibles

### 1️⃣ Por Task ID específico

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&taskId=<TASK_ID>"
```

- Devuelve **1 tarea específica**
- No se puede combinar con otros filtros

### 2️⃣ Por Web Project (Website)

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&website=autocinema&limit=20"
```

**Websites disponibles:**

- `autocinema` (puerto 8000)
- `autobooks` (puerto 8001)
- `autozone` (puerto 8002)
- `autodining` (puerto 8003)
- `autocrm`, `automail`, `autodelivery`, `autolodge`, etc.

### 3️⃣ Por Use Case

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&website=autocinema&useCase=LOGIN&limit=20"
```

**Use cases de ejemplo (autocinema):**

- `LOGIN`
- `REGISTRATION`
- `ADD_FILM`
- `EDIT_FILM`
- `DELETE_FILM`
- `FILM_DETAIL`
- `FILTER_FILM`
- `SEARCH_FILM`
- `ADD_COMMENT`
- etc.

### 4️⃣ Por Miner UID

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&minerUid=80&limit=20"
```

### 5️⃣ Por Success/Failed

```bash
# Tareas exitosas (score = 1)
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&success=true&limit=20"

# Tareas fallidas (score = 0)
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&success=false&limit=20"
```

### 6️⃣ Combinación de Filtros

```bash
# Ejemplo: autocinema + FILTER_FILM + miner 80 + success
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&website=autocinema&useCase=FILTER_FILM&minerUid=80&success=true&limit=20"
```

**Filtros combinables:**

- ✅ `website` + `useCase` + `minerUid` + `success`
- ✅ `website` + `minerUid` + `success`
- ✅ `useCase` + `success`
- ❌ `taskId` NO se puede combinar con otros filtros

## 📊 Paginación

### Parámetros

- `page=1` (número de página, por defecto 1)
- `limit=50` (resultados por página, por defecto 50, máximo 500)

```bash
# Página 1 con 20 resultados
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&page=1&limit=20"

# Página 2 con 50 resultados
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&page=2&limit=50"
```

## 🔄 Ordenamiento

### Parámetro

- `sort=created_at_desc` (por defecto)

**Opciones de ordenamiento:**

- `created_at_desc` - Más recientes primero
- `created_at_asc` - Más antiguos primero
- `score_desc` - Mejor score primero
- `score_asc` - Peor score primero

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&sort=score_desc&limit=20"
```

## 📝 Formato de Respuesta

```json
{
  "success": true,
  "data": {
    "tasks": [
      {
        "task": {
          "taskId": "439573cf-2ff5-4256-b209-985be271096d",
          "website": "autocinema",
          "useCase": "FILTER_FILM",
          "intent": "Filter films released in '2010'.",
          "startUrl": "http://localhost:8000/?seed=167",
          "requiredUrl": null,
          "createdAt": "2025-11-07T11:21:11.691767+00:00"
        },
        "solution": {
          "taskSolutionId": "task_solution_80_439573cf...",
          "trajectory": [],
          "actions": [
            {
              "type": "navigate",
              "attributes": {
                "url": "http://localhost:8000/?genre=&year=2010&seed=167",
                "go_back": false,
                "go_forward": false
              }
            }
          ],
          "createdAt": "2025-11-07T11:22:44.208865+00:00"
        },
        "evaluation": {
          "evaluationResultId": "evaluation_80_439573cf...",
          "score": 1,
          "passed": true
        },
        "agentRun": {
          "agentRunId": "agent_run_80_c427c9a9c356",
          "minerUid": 80,
          "minerHotkey": "5FL1U8fvbz4b2XJBz1V3pZ7jPAkcbDW92XxvN9axAuKEGfXR",
          "validatorUid": 101,
          "validatorHotkey": "5DypvN3kYgf19DmpXNxqUU7fZkccRJS6HnsREaWj82sQdWd8"
        }
      }
    ],
    "total": 740,
    "page": 1,
    "limit": 20,
    "totalPages": 37
  }
}
```

## ✅ Estadísticas del Endpoint (Base de Datos Actual)

- **Total de tareas:** 3,172
- **Tareas exitosas (success=true):** 740
- **Tareas fallidas (success=false):** 2,432

**Por website:**

- autocinema: 706 tareas (327 exitosas)
- autobooks: 704 tareas (413 exitosas)
- autozone: 704 tareas (0 exitosas)
- autodining: 480 tareas (0 exitosas)

## 🧪 Ejemplos de Uso

### 1. Obtener todas las tareas exitosas de autocinema

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&website=autocinema&success=true&limit=50"
```

### 2. Obtener tareas de LOGIN del miner 80

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&useCase=LOGIN&minerUid=80&limit=20"
```

### 3. Obtener las 100 tareas más recientes

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&sort=created_at_desc&limit=100"
```

### 4. Obtener tareas exitosas de autocinema con FILTER_FILM del miner 80

```bash
curl "http://localhost:8002/api/v1/tasks/with-solutions?key=AIagent2025&website=autocinema&useCase=FILTER_FILM&minerUid=80&success=true&limit=20"
```

## ⚠️ Errores Comunes

### Sin API Key (422)

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["query", "key"],
      "msg": "Field required"
    }
  ]
}
```

### API Key Inválida (401)

```json
{
  "detail": "Invalid API key"
}
```

## 🚀 Para Producción

Cuando estés en el servidor de producción, cambia la URL base:

```bash
# Desarrollo (local)
http://localhost:8002/api/v1/tasks/with-solutions

# Producción
http://YOUR_PRODUCTION_SERVER:8080/api/v1/tasks/with-solutions
```

Recuerda actualizar `ENVIRONMENT=production` en el `.env` del servidor.
