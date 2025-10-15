# Validator Pipeline API Documentation

## Overview

This API has been completely redesigned to support the validator pipeline for Bittensor network evaluation. The system manages rounds where validators evaluate miners (agents) by generating synthetic tasks, distributing them to miners, collecting responses, evaluating performance, and assigning weights.

## Architecture

### Core Concepts

1. **Round**: A complete evaluation cycle (20 epochs, ~360 blocks)
2. **Task**: Individual synthetic task sent to miners
3. **Agent Evaluation Run**: All tasks and results for one miner in one round
4. **Task Execution**: Individual task execution by a miner
5. **Validator**: Network validator running the evaluation
6. **Miner**: Network participant deploying an agent

### Data Flow

```
Round Start → Task Generation → Task Distribution → Response Collection → 
Evaluation → Scoring → Weight Assignment → Round Completion
```

## API Endpoints

### Validator Pipeline Endpoints

#### 1. Start Round
**POST** `/v1/rounds/start`

Initialize a new round with validator and miner information.

**Request Body:**
```json
{
  "validator_round_id": "round_123",
  "validator_info": {
    "validator_uid": 123,
    "validator_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
  },
  "start_block": 1000,
  "start_epoch": 50,
  "n_tasks": 5,
  "n_miners": 3,
  "n_winners": 3,
  "miners": [
    {
      "miner_uid": 1,
      "miner_hotkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    }
  ],
  "max_epochs": 20,
  "max_blocks": 360
}
```

#### 2. Generate Tasks
**POST** `/v1/rounds/{validator_round_id}/generate-tasks`

Generate N synthetic tasks for the round.

**Request Body:**
```json
{
  "validator_round_id": "round_123",
  "validator_info": {
    "validator_uid": 123,
    "validator_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
  },
  "n_tasks": 5,
  "web_projects": ["ecommerce", "blog"],
  "use_cases": ["search", "navigate", "purchase"]
}
```

#### 3. Distribute Tasks
**POST** `/v1/rounds/{validator_round_id}/distribute-tasks`

Distribute tasks to miners and create task executions.

**Request Body:**
```json
{
  "validator_round_id": "round_123",
  "validator_info": {
    "validator_uid": 123,
    "validator_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
  },
  "task_ids": ["round_123_task_0000", "round_123_task_0001"],
  "miner_uids": [1, 2, 3],
  "batch_size": 1,
  "timeout_seconds": 30.0
}
```

#### 4. Submit Task Responses
**POST** `/v1/rounds/{validator_round_id}/task-responses`

Submit task responses from miners.

**Request Body:**
```json
{
  "validator_round_id": "round_123",
  "validator_info": {
    "validator_uid": 123,
    "validator_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
  },
  "responses": [
    {
      "task_id": "round_123_task_0000",
      "agent_run_id": "round_123_1_round_123_task_0000",
      "validator_round_id": "round_123",
      "validator_info": {...},
      "miner_info": {...},
      "response": {
        "actions": [
          {"type": "click", "selector": "button.submit"},
          {"type": "type", "text": "test input", "selector": "input[name='query']"}
        ],
        "execution_time": 5.5,
        "success": true
      }
    }
  ]
}
```

#### 5. Evaluate Tasks
**POST** `/v1/rounds/{validator_round_id}/evaluate`

Evaluate task responses and assign scores.

**Request Body:**
```json
{
  "validator_round_id": "round_123",
  "validator_info": {
    "validator_uid": 123,
    "validator_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
  },
  "task_execution_ids": ["exec_1", "exec_2"],
  "evaluation_criteria": {
    "correctness_weight": 0.7,
    "efficiency_weight": 0.3,
    "max_score": 1.0
  }
}
```

#### 6. Calculate Scores
**POST** `/v1/rounds/{validator_round_id}/score`

Calculate final scores and rankings for miners.

**Request Body:**
```json
{
  "validator_round_id": "round_123",
  "validator_info": {
    "validator_uid": 123,
    "validator_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
  },
  "scoring_method": "weighted_average",
  "weight_distribution": {
    "1": 0.8,
    "2": 0.15,
    "3": 0.05
  }
}
```

#### 7. Assign Weights
**POST** `/v1/rounds/{validator_round_id}/assign-weights`

Assign final weights to miners based on rankings.

**Request Body:**
```json
{
  "validator_round_id": "round_123",
  "validator_info": {
    "validator_uid": 123,
    "validator_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
  },
  "winners": [
    {"miner_uid": 1, "rank": 1, "score": 0.95, "reward": 9.5},
    {"miner_uid": 2, "rank": 2, "score": 0.87, "reward": 8.7},
    {"miner_uid": 3, "rank": 3, "score": 0.78, "reward": 7.8}
  ],
  "weight_distribution": {
    "1": 0.8,
    "2": 0.15,
    "3": 0.05
  }
}
```

#### 8. Complete Round
**POST** `/v1/rounds/{validator_round_id}/complete`

Complete a round and finalize all data.

**Request Body:**
```json
{
  "validator_round_id": "round_123",
  "validator_info": {
    "validator_uid": 123,
    "validator_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
  },
  "final_stats": {
    "total_tasks": 5,
    "total_evaluations": 15,
    "avg_score": 0.87,
    "completion_rate": 1.0,
    "total_time": 120.5
  }
}
```

### Leaderboard Endpoints

#### 1. Get Rounds Leaderboard
**GET** `/v1/rounds/leaderboard/rounds`

Get leaderboard of rounds with optional filtering.

**Query Parameters:**
- `validator_uid` (optional): Filter by validator
- `limit` (default: 100): Number of results
- `offset` (default: 0): Pagination offset
- `sort_by` (default: "started_at"): Sort field
- `sort_order` (default: "desc"): Sort direction

#### 2. Get Miners Leaderboard
**GET** `/v1/rounds/leaderboard/miners`

Get leaderboard of miners with performance metrics.

**Query Parameters:**
- `validator_uid` (optional): Filter by validator
- `limit` (default: 100): Number of results
- `offset` (default: 0): Pagination offset
- `sort_by` (default: "avg_score"): Sort field
- `sort_order` (default: "desc"): Sort direction

#### 3. Get Round Details
**GET** `/v1/rounds/{validator_round_id}/details`

Get detailed information about a specific round.

**Query Parameters:**
- `validator_uid` (required): Validator UID

#### 4. Get Round Status
**GET** `/v1/rounds/{validator_round_id}/status`

Get current status and progress of a round.

**Query Parameters:**
- `validator_uid` (required): Validator UID

## Data Models

### Core Models

#### Round
```python
class Round(BaseModel):
    validator_round_id: str
    validator_info: ValidatorInfo
    status: RoundStatus
    start_block: int
    start_epoch: int
    end_block: Optional[int]
    end_epoch: Optional[int]
    started_at: float
    ended_at: Optional[float]
    elapsed_sec: Optional[float]
    max_epochs: int = 20
    max_blocks: int = 360
    n_tasks: int
    n_miners: int
    n_winners: int
    miners: List[MinerInfo]
    tasks: List[Task]
    agent_evaluation_runs: List[str]
    winners: Optional[List[Dict[str, Any]]]
    weights: Optional[Dict[int, float]]
    metadata: Dict[str, Any]
```

#### Task
```python
class Task(BaseModel):
    task_id: str
    prompt: str
    website: str
    web_project: str
    use_case: str
    expected_actions: Optional[List[Dict[str, Any]]]
    max_execution_time: Optional[float]
    difficulty: Optional[float]
    metadata: Dict[str, Any]
```

#### AgentEvaluationRun
```python
class AgentEvaluationRun(BaseModel):
    agent_run_id: str
    validator_round_id: str
    validator_info: ValidatorInfo
    miner_info: MinerInfo
    started_at: float
    ended_at: Optional[float]
    elapsed_sec: Optional[float]
    task_ids: List[str]
    n_tasks_total: int
    n_tasks_completed: int
    n_tasks_failed: int
    avg_eval_score: Optional[float]
    avg_execution_time: Optional[float]
    total_reward: Optional[float]
    rank: Optional[int]
    weight: Optional[float]
    status: EvaluationStatus
    metadata: Dict[str, Any]
```

#### TaskExecution
```python
class TaskExecution(BaseModel):
    task_id: str
    agent_run_id: str
    validator_round_id: str
    validator_info: ValidatorInfo
    miner_info: MinerInfo
    task: Task
    sent_at: Optional[float]
    started_at: Optional[float]
    completed_at: Optional[float]
    execution_time: Optional[float]
    miner_response: Optional[Dict[str, Any]]
    web_actions: Optional[List[Dict[str, Any]]]
    eval_score: Optional[float]
    time_score: Optional[float]
    total_score: Optional[float]
    reward: Optional[float]
    evaluation_result: Optional[Dict[str, Any]]
    test_results: Optional[Dict[str, Any]]
    status: TaskStatus
    metadata: Dict[str, Any]
```

### Enums

#### RoundStatus
```python
class RoundStatus(str, Enum):
    initializing = "initializing"
    task_generation = "task_generation"
    task_distribution = "task_distribution"
    evaluation = "evaluation"
    scoring = "scoring"
    weight_assignment = "weight_assignment"
    completed = "completed"
    failed = "failed"
```

#### TaskStatus
```python
class TaskStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
```

#### EvaluationStatus
```python
class EvaluationStatus(str, Enum):
    pending = "pending"
    evaluating = "evaluating"
    completed = "completed"
    failed = "failed"
```

## Usage Examples

### Complete Validator Pipeline

```python
import requests
import time

# 1. Start round
start_response = requests.post("/v1/rounds/start", json={
    "validator_round_id": f"round_{int(time.time())}",
    "validator_info": {"validator_uid": 123, "validator_hotkey": "..."},
    "start_block": 1000,
    "start_epoch": 50,
    "n_tasks": 5,
    "n_miners": 3,
    "n_winners": 3,
    "miners": [...]
})

# 2. Generate tasks
tasks_response = requests.post(f"/v1/rounds/{validator_round_id}/generate-tasks", json={
    "validator_round_id": validator_round_id,
    "validator_info": {"validator_uid": 123, "validator_hotkey": "..."},
    "n_tasks": 5
})

# 3. Distribute tasks
dist_response = requests.post(f"/v1/rounds/{validator_round_id}/distribute-tasks", json={
    "validator_round_id": validator_round_id,
    "validator_info": {"validator_uid": 123, "validator_hotkey": "..."},
    "task_ids": ["task_1", "task_2"],
    "miner_uids": [1, 2, 3]
})

# 4. Submit responses (from miners)
responses_response = requests.post(f"/v1/rounds/{validator_round_id}/task-responses", json={
    "validator_round_id": validator_round_id,
    "validator_info": {"validator_uid": 123, "validator_hotkey": "..."},
    "responses": [...]
})

# 5. Evaluate tasks
eval_response = requests.post(f"/v1/rounds/{validator_round_id}/evaluate", json={
    "validator_round_id": validator_round_id,
    "validator_info": {"validator_uid": 123, "validator_hotkey": "..."},
    "task_execution_ids": [...]
})

# 6. Calculate scores
score_response = requests.post(f"/v1/rounds/{validator_round_id}/score", json={
    "validator_round_id": validator_round_id,
    "validator_info": {"validator_uid": 123, "validator_hotkey": "..."}
})

# 7. Assign weights
weights_response = requests.post(f"/v1/rounds/{validator_round_id}/assign-weights", json={
    "validator_round_id": validator_round_id,
    "validator_info": {"validator_uid": 123, "validator_hotkey": "..."},
    "winners": [...]
})

# 8. Complete round
complete_response = requests.post(f"/v1/rounds/{validator_round_id}/complete", json={
    "validator_round_id": validator_round_id,
    "validator_info": {"validator_uid": 123, "validator_hotkey": "..."}
})
```

### Leaderboard Queries

```python
# Get rounds leaderboard
rounds = requests.get("/v1/rounds/leaderboard/rounds", params={
    "validator_uid": 123,
    "limit": 50,
    "sort_by": "started_at",
    "sort_order": "desc"
})

# Get miners leaderboard
miners = requests.get("/v1/rounds/leaderboard/miners", params={
    "validator_uid": 123,
    "limit": 100,
    "sort_by": "avg_score",
    "sort_order": "desc"
})

# Get specific round details
round_details = requests.get(f"/v1/rounds/{validator_round_id}/details", params={
    "validator_uid": 123
})
```

## Database Collections

The API uses the following MongoDB collections:

- `rounds`: Round definitions and metadata
- `tasks`: Individual task definitions
- `task_executions`: Task execution records
- `agent_evaluation_runs`: Agent evaluation run summaries
- `events`: Round events and logs (optional)

## Authentication

All endpoints require API key authentication via the `token` parameter or header.

## Error Handling

All endpoints return standardized error responses:

```json
{
  "ok": false,
  "error": "Error message",
  "detail": "Detailed error information",
  "code": "ERROR_CODE"
}
```

## Response Format

All successful responses follow this format:

```json
{
  "ok": true,
  "message": "Optional success message",
  "data": {
    // Response data
  }
}
```
