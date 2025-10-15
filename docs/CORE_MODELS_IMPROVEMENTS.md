# Core Models Consolidation and Improvements

## Overview

This document outlines the consolidation and improvements made to the models to address inconsistencies and missing relationships between `AgentEvaluationRun`, `ValidatorRound`, and related models.

## Decision: Use Schemas Models (Primary)

After analysis, we determined that the **schemas models** are the primary models used throughout the application:
- 31 imports from `app.models.schemas` vs only 2 from `app.models.core`
- All main services, APIs, and data generation scripts use schemas
- Core models were only used in deprecated tests

**Action Taken**: 
1. Removed duplicate core models and consolidated to use schemas models exclusively
2. **Global Refactor**: Changed `round_id` to `validator_round_id` across the entire project (53 files)
3. **Deleted Core Folder**: Completely removed `app/models/core/` folder as it's no longer needed
4. **Moved to core.py**: Moved all model definitions to `app/models/core.py` for cleaner structure

## Issues Identified

### 1. Duplicate Models Removed

**Problem**: There were duplicate `AgentEvaluationRun` models in both `core` and `schemas` packages, causing confusion.

**Solution**: 
- ✅ **Kept schemas version** (primary, actively used)
- ❌ **Removed core version** (legacy, unused)
- ✅ **Enhanced schemas version** with better documentation

**Final Schemas Model**:
```python
class AgentEvaluationRun(BaseModel):
    agent_run_id: str
    validator_round_id: str                    # ✅ Links to Round.validator_round_id
    validator_uid: int               # ✅ Validator UID for context
    miner_uid: Optional[int]         # ✅ Miner UID (None for SOTA)
    miner_info: Optional[MinerInfo]  # ✅ Embedded miner info
    is_sota: bool                    # ✅ SOTA support
    # ... other fields
```

### 2. Confusion Between `round` and `validator_round_id`

**Clarification**:
- `round` (int): Sequential round number (1, 2, 3...)
- `validator_round_id` (UUID string): Unique identifier for each validator's round instance

**These are NOT the same thing!**

### 3. Missing Relationships in EvaluationResult

**Problem**: `EvaluationResult` also lacked proper relationships to the validator round context.

**Improvements**:
- Added `validator_round_id` field
- Added `round` field  
- Added `validator_uid` field
- Added proper validation for UUID format

## Model Relationships

### ValidatorRound
```python
class ValidatorRound(BaseModel):
    validator_round_id: str  # Unique UUID for this validator's round
    round: int = 0          # Sequential round number
    validator: ValidatorInfo
    agent_runs: List[AgentEvaluationRun]  # Contains agent runs
```

### AgentEvaluationRun
```python
class AgentEvaluationRun(BaseModel):
    agent_run_id: str
    validator_round_id: Optional[str]  # Links to ValidatorRound.validator_round_id
    round: Optional[int]               # Sequential round number
    validator_uid: Optional[int]       # Validator UID for context
    evaluations: List[Evaluation]      # Contains evaluations
```

### EvaluationResult
```python
class EvaluationResult(BaseModel):
    task_id: Optional[str]
    agent_run_id: Optional[str]        # Links to AgentEvaluationRun.agent_run_id
    validator_round_id: Optional[str]  # Links to ValidatorRound.validator_round_id
    round: Optional[int]               # Sequential round number
    validator_uid: Optional[int]       # Validator UID for context
```

## Benefits of These Improvements

1. **Proper Foreign Key Relationships**: Models now have clear relationships between each other
2. **Better Data Integrity**: UUID validation ensures proper format
3. **Clearer Documentation**: Added comprehensive docstrings explaining relationships
4. **Consistency**: Both core and schema models now follow similar patterns
5. **Query Efficiency**: Database queries can now properly join on these relationships

## Migration Considerations

When updating existing data:

1. **Backward Compatibility**: All new fields are optional, so existing data will continue to work
2. **Data Population**: You may need to populate the new fields for existing records
3. **API Updates**: Consider updating API endpoints to include the new relationship fields
4. **Database Indexes**: Consider adding indexes on the new relationship fields for better query performance

## Usage Examples

### Creating an AgentEvaluationRun with proper relationships:
```python
agent_run = AgentEvaluationRun(
    agent_run_id="run_123",
    validator_round_id="550e8400-e29b-41d4-a716-446655440000",
    round=5,
    validator_uid=123,
    miner=miner_info,
    # ... other fields
)
```

### Querying by validator round:
```python
# Find all agent runs for a specific validator round
agent_runs = await db.agent_evaluation_runs.find({
    "validator_round_id": "550e8400-e29b-41d4-a716-446655440000"
}).to_list()
```

### Joining data across models:
```python
# Get validator round with all its agent runs
validator_round = await db.validator_rounds.find_one({
    "validator_round_id": "550e8400-e29b-41d4-a716-446655440000"
})

agent_runs = await db.agent_evaluation_runs.find({
    "validator_round_id": validator_round["validator_round_id"]
}).to_list()
```
