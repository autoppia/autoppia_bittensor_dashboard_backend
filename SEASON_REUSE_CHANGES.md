# 🎯 Season and Code Reuse Implementation

## ✅ Cambios Aplicados en la Base de Datos

### 1. **Tabla `validator_rounds`**
```sql
✅ Añadida columna: season_number INTEGER
✅ Añadido índice: idx_validator_rounds_season
```

### 2. **Tabla `validator_round_miners`**
```sql
✅ Ya existía: github_url VARCHAR(512)
```
Esta tabla registra qué código (github_url) envió cada miner en el handshake de cada round.

### 3. **Tabla `miner_evaluation_runs`**
```sql
✅ Añadida columna: github_url VARCHAR(1024)
✅ Añadida columna: is_reused BOOLEAN DEFAULT FALSE
✅ Añadida columna: reused_from_agent_run_id VARCHAR(128)
✅ Añadida FK: fk_miner_evaluation_runs_reused_from
✅ Añadido índice: idx_miner_evaluation_runs_github
✅ Añadido índice: idx_miner_evaluation_runs_reused_from
✅ Añadido índice parcial: idx_miner_evaluation_runs_reuse_lookup (solo is_reused=FALSE)
```

---

## 📊 Estructura de Datos

### **Conceptos:**

1. **Season** (calculada, no tabla):
   - Se calcula por bloques igual que round_number
   - `season_number = (current_block - DZ_STARTING_BLOCK) // SEASON_SIZE_BLOCKS`
   - Agrupa 20 rounds con las mismas tareas

2. **Round** (validator_rounds):
   - Ejecución individual
   - Ahora incluye `season_number`
   - Mantiene toda su funcionalidad actual

3. **Miner Submission** (validator_round_miners):
   - Ya tenía `github_url`
   - Registra qué código envió cada miner en el handshake

4. **Agent Run** (miner_evaluation_runs):
   - Ahora trackea el código usado (`github_url`)
   - Flag `is_reused` indica si es evaluación nueva o reutilizada
   - `reused_from_agent_run_id` apunta al agent_run original si es reuse

---

## 🔄 Flujo de Trabajo

### **Round 1 de Season 1:**

```python
# 1. Validator calcula season
season_number = calculate_season(current_block)  # = 1

# 2. Crea round con season_number
POST /validator/rounds/start
{
    "validator_round_id": "round-uuid-1",
    "round_number": 45,
    "season_number": 1,
    ...
}

# 3. Handshake - miners responden con github_url
# Ya se guarda en validator_round_miners.github_url (existente)

# 4. Validator verifica si hay evaluación previa (NO hay, es Round 1)
# 5. Despliega código y evalúa
# 6. Crea agent_run:
INSERT INTO miner_evaluation_runs (
    agent_run_id, validator_round_id,
    github_url, is_reused, reused_from_agent_run_id,
    ...
) VALUES (
    'run-123', 'round-uuid-1',
    'https://github.com/user/repo/tree/abc123', FALSE, NULL,
    ...
);
```

### **Round 2 de Season 1 (mismo código):**

```python
# 1. Handshake - miner responde con MISMO github_url

# 2. Validator busca evaluación previa en esta season:
SELECT agent_run_id, average_score, average_reward
FROM miner_evaluation_runs aer
JOIN validator_rounds vr ON aer.validator_round_id = vr.validator_round_id
WHERE vr.season_number = 1
  AND aer.miner_uid = 123
  AND aer.github_url = 'https://github.com/user/repo/tree/abc123'
  AND aer.is_reused = FALSE
ORDER BY aer.created_at DESC
LIMIT 1;

# 3. ENCUENTRA evaluación previa → REUSE
# 4. Crea agent_run reusado:
INSERT INTO miner_evaluation_runs (
    agent_run_id, validator_round_id,
    github_url, is_reused, reused_from_agent_run_id,
    average_score, average_reward,  # Copiados del original
    ...
) VALUES (
    'run-456', 'round-uuid-2',
    'https://github.com/user/repo/tree/abc123', TRUE, 'run-123',
    0.75, 8.5,  # Del original
    ...
);
```

### **Round 3 de Season 1 (código diferente):**

```python
# 1. Handshake - miner responde con NUEVO github_url
# 2. Validator busca → NO encuentra
# 3. Despliega y evalúa nuevamente
# 4. Crea agent_run nuevo (is_reused=FALSE)
```

---

## 🔍 Queries Útiles

### **Buscar evaluación para reutilizar:**

```sql
SELECT 
    aer.agent_run_id,
    aer.average_score,
    aer.average_reward,
    aer.total_tasks,
    aer.success_tasks,
    aer.github_url
FROM miner_evaluation_runs aer
JOIN validator_rounds vr ON aer.validator_round_id = vr.validator_round_id
WHERE vr.season_number = :season_number
  AND aer.miner_uid = :miner_uid
  AND aer.github_url = :github_url
  AND aer.is_reused = FALSE
ORDER BY aer.created_at DESC
LIMIT 1;
```

### **Rounds de una season:**

```sql
SELECT *
FROM validator_rounds
WHERE season_number = 1
ORDER BY round_number;
```

### **Performance de un miner en una season:**

```sql
SELECT 
    vr.round_number,
    vrm.github_url,
    aer.is_reused,
    aer.average_score,
    aer.average_reward,
    CASE 
        WHEN aer.is_reused THEN aer.reused_from_agent_run_id
        ELSE NULL
    END as reused_from
FROM miner_evaluation_runs aer
JOIN validator_rounds vr ON aer.validator_round_id = vr.validator_round_id
LEFT JOIN validator_round_miners vrm 
    ON vrm.validator_round_id = vr.validator_round_id 
    AND vrm.miner_uid = aer.miner_uid
WHERE vr.season_number = 1
  AND aer.miner_uid = 123
ORDER BY vr.round_number;
```

### **Cadena de reuse (original → reuses):**

```sql
WITH RECURSIVE reuse_chain AS (
    -- Base: run original
    SELECT 
        agent_run_id, 
        validator_round_id, 
        github_url,
        is_reused, 
        reused_from_agent_run_id, 
        0 as depth,
        average_score
    FROM miner_evaluation_runs
    WHERE agent_run_id = 'run-original-id'
    
    UNION ALL
    
    -- Recursivo: runs que reusan
    SELECT 
        aer.agent_run_id, 
        aer.validator_round_id, 
        aer.github_url,
        aer.is_reused,
        aer.reused_from_agent_run_id,
        rc.depth + 1,
        aer.average_score
    FROM miner_evaluation_runs aer
    JOIN reuse_chain rc ON aer.reused_from_agent_run_id = rc.agent_run_id
)
SELECT * FROM reuse_chain ORDER BY depth;
```

### **Estadísticas de reuse por season:**

```sql
SELECT 
    vr.season_number,
    COUNT(*) as total_agent_runs,
    COUNT(*) FILTER (WHERE aer.is_reused = TRUE) as reused_runs,
    COUNT(*) FILTER (WHERE aer.is_reused = FALSE) as new_evaluations,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE aer.is_reused = TRUE) / COUNT(*), 
        2
    ) as reuse_percentage
FROM miner_evaluation_runs aer
JOIN validator_rounds vr ON aer.validator_round_id = vr.validator_round_id
WHERE vr.season_number IS NOT NULL
GROUP BY vr.season_number
ORDER BY vr.season_number DESC;
```

---

## 📝 Modelos ORM Actualizados

### **ValidatorRoundORM:**

```python
class ValidatorRoundORM(TimestampMixin, Base):
    __tablename__ = "validator_rounds"
    
    # ... campos existentes ...
    season_number: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )
```

### **AgentEvaluationRunORM:**

```python
class AgentEvaluationRunORM(TimestampMixin, Base):
    __tablename__ = "miner_evaluation_runs"
    
    # ... campos existentes ...
    
    # Code reuse tracking
    github_url: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True, index=True
    )
    is_reused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    reused_from_agent_run_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("miner_evaluation_runs.agent_run_id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # Relationship para acceder al run original
    reused_from: Mapped[Optional["AgentEvaluationRunORM"]] = relationship(
        "AgentEvaluationRunORM",
        foreign_keys=[reused_from_agent_run_id],
        remote_side="AgentEvaluationRunORM.agent_run_id",
        uselist=False
    )
```

---

## 🎯 Próximos Pasos

1. ✅ **Migración DB aplicada**
2. ✅ **Modelos ORM actualizados**
3. ⏳ **Actualizar Pydantic models en `app/models/core.py`**
4. ⏳ **Modificar endpoints de validator:**
   - `POST /validator/rounds/start` - aceptar season_number
   - `POST /validator/agent-runs` - aceptar github_url, is_reused, reused_from
   - Nuevo: `GET /validator/agent-runs/check-reuse` - buscar evaluación previa
5. ⏳ **Actualizar servicios:**
   - `ValidatorStorageService` - incluir season_number en todas las operaciones
   - Nuevo: `ReuseService` - lógica de búsqueda y copia de evaluaciones
6. ⏳ **Ajustar subnet:**
   - Calcular season_number en SeasonManager
   - Modificar handshake para enviar season_number
   - Añadir lógica de check-reuse antes de desplegar
7. ⏳ **Frontend:**
   - Añadir filtro por season
   - Mostrar badge "Reused" en agent runs
   - Mostrar github_url/commit en detalles

---

## 🔧 Rollback (si es necesario)

```sql
DROP INDEX IF EXISTS idx_miner_evaluation_runs_reuse_lookup;
DROP INDEX IF EXISTS idx_miner_evaluation_runs_reused_from;
DROP INDEX IF EXISTS idx_miner_evaluation_runs_github;
ALTER TABLE miner_evaluation_runs DROP CONSTRAINT IF EXISTS fk_miner_evaluation_runs_reused_from;
ALTER TABLE miner_evaluation_runs DROP COLUMN IF EXISTS reused_from_agent_run_id;
ALTER TABLE miner_evaluation_runs DROP COLUMN IF EXISTS is_reused;
ALTER TABLE miner_evaluation_runs DROP COLUMN IF EXISTS github_url;
DROP INDEX IF EXISTS idx_validator_rounds_season;
ALTER TABLE validator_rounds DROP COLUMN IF EXISTS season_number;
```
