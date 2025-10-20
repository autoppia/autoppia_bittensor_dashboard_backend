# Dashboard Section Responses

This guide documents the shape of the Autoppia dashboard APIs by functional section. Each endpoint entry includes a real response sampled from the current build and a short description of the non-obvious fields (aggregations, averages, derived metrics, etc.).

The samples were captured after seeding three illustrative rounds (`round_1001`-`round_1003`). Replace the IDs with the ones present in your deployment when querying the live service.

---

## Overview

### `GET /api/v1/overview`

```json
{
  "success": true,
  "data": {
    "metrics": {
      "topScore": 0.92,
      "totalWebsites": 1,
      "totalValidators": 1,
      "totalMiners": 1,
      "currentRound": 1003,
      "metricsRound": 1002,
      "subnetVersion": "1.0.0",
      "lastUpdated": "2025-10-20T17:33:10.676353+00:00"
    }
  }
}
```

- `topScore`: best validator score seen in the most recent completed round (`metricsRound`).
- `metricsRound`: the last fully finished round used for aggregated metrics (the current round may still be active).
- `lastUpdated`: ISO timestamp when the metrics snapshot was produced.

### `GET /api/v1/overview/validators`

```json
{
  "success": true,
  "data": {
    "validators": [
      {
        "id": "validator-1902",
        "name": "Validator 1902",
        "status": "Not Started",
        "totalTasks": 0,
        "weight": 0.0,
        "trust": 0.98,
        "uptime": 0.0
      }
    ],
    "total": 3,
    "page": 1,
    "limit": 10
  }
}
```

- `weight`: current subnet weight assigned to the validator (used for ranking in PoS).
- `trust`: internal trust score produced by the validator directory feed.
- `uptime`: rolling proportion of rounds in which this validator reported successfully.

### `GET /api/v1/overview/rounds`

```json
{
  "success": true,
  "data": {
    "rounds": [
      {
        "id": 1003,
        "status": "active",
        "totalTasks": 1,
        "averageScore": 0.75,
        "validatorRounds": [
          {
            "validatorRoundId": "round_1003",
            "averageScore": 0.75,
            "completedTasks": 0
          }
        ]
      }
    ],
    "currentRound": {
      "id": 1003,
      "progress": 0.0
    },
    "total": 3
  }
}
```

- `validatorRounds`: per-validator breakdown for each logical round, including rolled-up scoring.
- `progress`: percentage of blocks elapsed within the round window (0.0–1.0).

### `GET /api/v1/overview/leaderboard`

```json
{
  "success": true,
  "data": {
    "leaderboard": [
      {
        "round": 1003,
        "subnet36": 0.0,
        "timestamp": "1970-01-01T00:16:40+00:00"
      },
      {
        "round": 1002,
        "subnet36": 0.0,
        "timestamp": "1970-01-01T00:16:40+00:00"
      }
    ],
    "total": 3,
    "timeRange": {
      "start": "1970-01-01T00:16:40+00:00",
      "end": "1970-01-01T00:16:40+00:00"
    }
  }
}
```

- `subnet36`: aggregate score for Autoppia’s subnet 36. Other keys (`openai_cua`, `anthropic_cua`, etc.) appear when benchmark agents are configured.
- `timeRange`: window boundaries applied to the leaderboard query.

### `GET /api/v1/overview/statistics`

```json
{
  "success": true,
  "data": {
    "statistics": {
      "totalStake": 350123,
      "totalEmission": 17506,
      "averageTrust": 0.915,
      "networkUptime": 0.0,
      "activeValidators": 2,
      "registeredMiners": 2,
      "totalTasksCompleted": 0,
      "averageTaskScore": 0.92,
      "lastUpdated": "2025-10-20T17:33:10.978576+00:00"
    }
  }
}
```

- `totalStake` / `totalEmission`: cumulative Tao stake and emissions across all validators.
- `networkUptime`: proportion of validators that reported in the rolling window.
- `averageTaskScore`: mean of the latest evaluation scores across tasks.

---

## Rounds

The examples below use `round_1001` as the validator round identifier.

### `GET /api/v1/rounds`

```json
{
  "success": true,
  "data": {
    "rounds": [
      {
        "id": 1003,
        "status": "active",
        "averageScore": 0.75,
        "topScore": 0.9,
        "validatorRoundCount": 1
      }
    ],
    "total": 3,
    "page": 1,
    "limit": 10,
    "currentRound": {
      "id": 1003,
      "progress": 0.0
    }
  }
}
```

- `validatorRoundCount`: number of validators that have submitted data for this logical round.

### `GET /api/v1/rounds/{validator_round_id}`

```json
{
  "success": true,
  "data": {
    "round": {
      "roundNumber": 1001,
      "status": "active",
      "averageScore": 0.75,
      "topScore": 0.9,
      "validatorRounds": [
        {
          "validatorRoundId": "round_1001",
          "totalTasks": 1,
          "completedTasks": 0
        }
      ]
    }
  }
}
```

- `validatorRounds[].completedTasks`: tasks the validator marked as finished during this round.

### `GET /api/v1/rounds/{validator_round_id}/statistics`

```json
{
  "success": true,
  "data": {
    "statistics": {
      "roundId": 1001,
      "totalMiners": 1,
      "activeMiners": 0,
      "totalTasks": 1,
      "completedTasks": 0,
      "averageScore": 0.92,
      "successRate": 0.0,
      "averageDuration": 10.0,
      "totalStake": 0,
      "totalEmission": 0,
      "lastUpdated": "2025-10-20T17:33:10.783139+00:00"
    }
  }
}
```

- `activeMiners`: miners with at least one successful task in the round.
- `successRate`: ratio of completed to total tasks (0–1 range expressed as a fraction).
- `averageDuration`: mean execution time (seconds) for completed tasks.

### `GET /api/v1/rounds/{validator_round_id}/miners`

```json
{
  "success": true,
  "data": {
    "miners": [
      {
        "uid": 1701,
        "name": "Agent 1001",
        "score": 0.92,
        "success": false,
        "tasksCompleted": 0,
        "tasksTotal": 1,
        "duration": 10.0,
        "ranking": 1
      }
    ],
    "benchmarks": [],
    "total": 1,
    "page": 1,
    "limit": 20
  }
}
```

- `success`: indicates whether the miner completed the specific task successfully.
- `benchmarks`: separate entries whenever SOTA benchmark agents were evaluated in the round.

### `GET /api/v1/rounds/{validator_round_id}/validators`

```json
{
  "success": true,
  "data": {
    "validators": [
      {
        "id": "validator-1901",
        "status": "active",
        "averageScore": 0.75,
        "completedTasks": 0,
        "totalMiners": 1,
        "activeMiners": 0
      }
    ],
    "total": 1
  }
}
```

- `activeMiners`: miners that reported at least one task result to this validator in the round.

### `GET /api/v1/rounds/{validator_round_id}/activity`

```json
{
  "success": true,
  "data": {
    "activities": [
      {
        "id": "agent_run_1001-completed",
        "type": "run_completed",
        "message": "Agent run completed successfully",
        "timestamp": "1970-01-01T00:17:00+00:00",
        "metadata": {
          "runId": "agent_run_1001",
          "roundId": 1001,
          "score": 0.92
        }
      }
    ],
    "total": 1
  }
}
```

- `type`: enum describing the event category (`run_completed`, `round_started`, etc.).
- `metadata`: contextual attributes used by the frontend (run duration, score, validator IDs).

### `GET /api/v1/rounds/{validator_round_id}/summary`

```json
{
  "success": true,
  "data": {
    "roundId": 1001,
    "status": "active",
    "progress": 0.0,
    "totalMiners": 1,
    "averageScore": 0.92,
    "topScore": 0.92,
    "timeRemaining": "00:06:00"
  }
}
```

- `progress`: percentage (0–1) of the round duration that has elapsed.
- `timeRemaining`: human-readable estimate derived from block height and duration.

---

## Agents

Sample agent identifier: `agent-1701`.

### `GET /api/v1/agents`

```json
{
  "success": true,
  "data": {
    "agents": [
      {
        "id": "agent-1701",
        "uid": 1701,
        "type": "autoppia",
        "currentScore": 0.92,
        "currentRank": 1,
        "roundsParticipated": 1,
        "alphaWonInPrizes": 0.0,
        "averageResponseTime": 10.0
      }
    ],
    "total": 3,
    "page": 1,
    "limit": 20
  }
}
```

- `currentScore`: most recent aggregate score for the agent across rounds.
- `alphaWonInPrizes`: cumulative incentives awarded (denominated in Tao/alpha).

### `GET /api/v1/agents/statistics`

```json
{
  "success": true,
  "data": {
    "statistics": {
      "totalAgents": 3,
      "activeAgents": 3,
      "totalRuns": 3,
      "successfulRuns": 3,
      "averageScore": 0.92,
      "averageResponseTime": 10.0
    }
  }
}
```

- `averageResponseTime`: mean completion time across all recorded runs for the time window.

### `GET /api/v1/agents/activity`

```json
{
  "success": true,
  "data": {
    "activities": [
      {
        "id": "agent_run_1001-completed",
        "type": "run_completed",
        "message": "Agent run completed successfully",
        "timestamp": "1970-01-01T00:17:00+00:00",
        "metadata": {
          "runId": "agent_run_1001",
          "roundId": 1001,
          "score": 0.92
        }
      }
    ],
    "total": 3
  }
}
```

- Same activity payload as the round feed, aggregated across all agents.

### `GET /api/v1/agents/{agent_id}`

```json
{
  "success": true,
  "data": {
    "agent": {
      "id": "agent-1701",
      "totalRuns": 1,
      "currentScore": 0.92,
      "bestRankEver": 1,
      "roundsParticipated": 1
    },
    "scoreRoundData": [
      {
        "round_id": 1001,
        "score": 0.92,
        "rank": 1,
        "reward": 0.0
      }
    ]
  }
}
```

- `scoreRoundData`: history of round-by-round performance, including rewards (if any).

### `GET /api/v1/agents/{agent_id}/performance`

```json
{
  "success": true,
  "data": {
    "metrics": {
      "agentId": "agent-1701",
      "timeRange": {
        "start": "1970-01-01T00:16:50+00:00",
        "end": "1970-01-01T00:17:00+00:00"
      },
      "totalRuns": 1,
      "successfulRuns": 1,
      "failedRuns": 0,
      "successRate": 100.0,
      "currentScore": 0.92,
      "worstScore": 0.92,
      "averageResponseTime": 10.0,
      "taskCompletionRate": 0.0,
      "scoreDistribution": {
        "excellent": 1,
        "good": 0,
        "average": 0,
        "poor": 0
      },
      "performanceTrend": [
        {
          "round": 1001,
          "score": 0.92,
          "responseTime": 10.0,
          "successRate": 100.0
        }
      ]
    }
  }
}
```

- `scoreDistribution`: histogram of task-level scores bucketed into qualitative bands.
- `performanceTrend`: per-round progression across the requested time range.
- `taskCompletionRate`: percentage of tasks finished out of those assigned during the window.

### `GET /api/v1/agents/{agent_id}/runs`

```json
{
  "success": true,
  "data": {
    "runs": [
      {
        "runId": "agent_run_1001",
        "roundId": 1001,
        "status": "completed",
        "totalTasks": 1,
        "completedTasks": 1,
        "duration": 10,
        "score": 0.92
      }
    ],
    "total": 1,
    "page": 1,
    "limit": 20,
    "availableRounds": [1001],
    "selectedRound": null
  }
}
```

- `availableRounds`: list of logical rounds that include runs for this agent (useful for filters).

### `GET /api/v1/agents/{agent_id}/activity`

Returns the same schema as the global activity feed but scoped to the specified agent.

---

## Agent Runs

Example run ID: `agent_run_1001`.

### `GET /api/v1/agent-runs`

```json
{
  "success": true,
  "data": {
    "runs": [
      {
        "runId": "agent_run_1003",
        "roundId": 1003,
        "status": "completed",
        "totalTasks": 0,
        "completedTasks": 0,
        "duration": 10,
        "score": 0.0
      }
    ],
    "total": 3,
    "page": 1,
    "limit": 20,
    "availableRounds": [1003, 1002, 1001],
    "selectedRound": null
  }
}
```

- `availableRounds` / `selectedRound`: help the UI apply round filters when browsing runs.

### `GET /api/v1/agent-runs/{run_id}`

```json
{
  "success": true,
  "data": {
    "run": {
      "runId": "agent_run_1001",
      "agentId": "agent-1701",
      "validatorId": "validator-1901",
      "status": "completed",
      "totalTasks": 1,
      "successfulTasks": 1,
      "score": 0.92,
      "duration": 10,
      "metadata": {
        "notes": "Test run"
      }
    }
  }
}
```

- `metadata`: validator-provided annotations about the run (e.g., debug notes).

### `GET /api/v1/agent-runs/{run_id}/summary`

```json
{
  "success": true,
  "data": {
    "summary": {
      "runId": "agent_run_1001",
      "score": 0.92,
      "successRate": 100.0,
      "averageTaskDuration": 5.0,
      "topPerformingWebsite": {
        "website": "https://example.com",
        "score": 0.92,
        "tasks": 1
      }
    }
  }
}
```

- `topPerformingWebsite`: best website/result pair for the run, useful for quick highlights.

### `GET /api/v1/agent-runs/{run_id}/tasks`

```json
{
  "success": true,
  "data": {
    "tasks": [
      {
        "taskId": "task_1001",
        "website": "https://example.com",
        "useCase": "Example",
        "status": "completed",
        "score": 0.92,
        "duration": 5
      }
    ]
  }
}
```

- `duration`: task execution time in seconds (per run).

### `GET /api/v1/agent-runs/{run_id}/metrics`

```json
{
  "success": true,
  "data": {
    "metrics": {
      "duration": 10,
      "peakCpu": 2.0,
      "peakMemory": 2.0,
      "totalNetworkTraffic": 200,
      "cpu": [
        {"timestamp": "1970-01-01T00:16:50+00:00", "value": 1.0},
        {"timestamp": "1970-01-01T00:17:00+00:00", "value": 2.0}
      ],
      "memory": [
        {"timestamp": "1970-01-01T00:16:50+00:00", "value": 1.0},
        {"timestamp": "1970-01-01T00:17:00+00:00", "value": 2.0}
      ],
      "network": [
        {"timestamp": "1970-01-01T00:16:50+00:00", "value": 1.0},
        {"timestamp": "1970-01-01T00:17:00+00:00", "value": 2.0}
      ]
    }
  }
}
```

- `totalNetworkTraffic`: cumulative bytes (converted to the unit used by the validator payload).
- `cpu` / `memory` / `network`: time-series samples gathered during the run for charting.

---

## Tasks

Example task ID: `task_1001`.

### `GET /api/v1/tasks`

```json
{
  "success": true,
  "data": {
    "tasks": [
      {
        "taskId": "task_1003",
        "website": "https://example.com",
        "useCase": "Example",
        "status": "pending",
        "score": 0.0,
        "startTime": "1970-01-01T00:16:50+00:00"
      }
    ],
    "total": 3,
    "page": 1,
    "limit": 20
  }
}
```

- `status`: reflects the latest evaluation result (`pending`, `completed`, `failed`, etc.).

### `GET /api/v1/tasks/{task_id}`

```json
{
  "success": true,
  "data": {
    "task": {
      "taskId": "task_1001",
      "website": "https://example.com",
      "useCase": "Example",
      "prompt": "Execute integration test task.",
      "status": "completed",
      "score": 0.92,
      "duration": 5,
      "metadata": {
        "browser": "chrome",
        "environment": "production",
        "resources": {
          "cpu": 1.0,
          "memory": 512,
          "network": 100
        }
      },
      "performance": {
        "totalActions": 1,
        "successfulActions": 1,
        "failedActions": 0,
        "averageActionDuration": 0.0,
        "totalWaitTime": 0.0,
        "totalNavigationTime": 0.0
      },
      "relationships": {
        "round": {
          "roundNumber": 1001,
          "status": "active"
        },
        "evaluation": {
          "finalScore": 0.92,
          "evaluationTime": 5.0
        },
        "solution": {
          "solutionId": "solution_1001",
          "actionsCount": 1
        }
      }
    }
  }
}
```

- `performance.averageActionDuration`: mean action duration in seconds across recorded actions.
- `relationships.round`: the logical round metadata used for navigation links.
- `relationships.evaluation.finalScore`: final validator-issued score for the solution.

### `GET /api/v1/tasks/{task_id}/statistics`

```json
{
  "success": true,
  "data": {
    "statistics": {
      "successRate": 92.0,
      "averageScore": 0.92,
      "medianScore": 0.92,
      "scoreDistribution": {
        "excellent": 1,
        "good": 0,
        "average": 0,
        "poor": 0
      }
    }
  }
}
```

- `successRate`: expressed as percentage (0–100) for UI progress widgets.
- `scoreDistribution`: bucketed counts for each score quality band.

### `GET /api/v1/tasks/{task_id}/metrics`

```json
{
  "success": true,
  "data": {
    "metrics": {
      "duration": 5,
      "actionsPerSecond": 0.0,
      "averageActionDuration": 0.0,
      "totalWaitTime": 0.0,
      "totalNavigationTime": 0.0,
      "cpuUsage": [],
      "memoryUsage": []
    }
  }
}
```

- `actionsPerSecond`: derived rate of actions executed over the task duration.
- `cpuUsage` / `memoryUsage`: time-series samples recorded while replaying the agent run (empty if the validator did not provide telemetry).

---

### Artifact provenance

- `docs/backend_samples.json` and `docs/backend_agent_detail.json` contain the raw payloads captured to assemble this document.
