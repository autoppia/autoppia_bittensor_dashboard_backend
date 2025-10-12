# Validator Integration Guide

## Overview

This guide explains how validators should integrate with the Autoppia Bittensor Dashboard Backend to submit complete round data for storage and UI display.

## 🎯 **Single Endpoint Solution**

Validators should use **ONE primary endpoint** to submit all round data:

```
POST /v1/rounds/optimized/submit
```

This endpoint accepts a complete `RoundSubmissionRequest` containing all necessary data for a full round evaluation.

## 📋 **Complete Data Structure**

### RoundSubmissionRequest Schema

```json
{
  "round": {
    "round_id": "round_123",
    "validators": [
      {
        "uid": 123,
        "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        "coldkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
        "stake": 1000.0,
        "vtrust": 0.95,
        "name": "Autoppia Validator",
        "version": "1.0.0"
      }
    ],
    "start_block": 1000,
    "start_epoch": 50,
    "end_block": 1360,
    "end_epoch": 70,
    "started_at": 1704067200.0,
    "ended_at": 1704070800.0,
    "elapsed_sec": 3600.0,
    "max_epochs": 20,
    "max_blocks": 360,
    "n_tasks": 5,
    "n_miners": 3,
    "n_winners": 3,
    "miners": [
      {
        "uid": 1,
        "hotkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
        "coldkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        "agent_name": "OpenAI CUA",
        "agent_image": "https://example.com/openai-logo.png",
        "github": "https://github.com/openai/cua"
      }
    ],
    "winners": [
      {
        "miner_uid": 1,
        "score": 0.85,
        "rank": 1,
        "reward": 100.0
      }
    ],
    "winner_scores": [0.85, 0.72, 0.68],
    "weights": {
      "1": 0.85,
      "2": 0.72,
      "3": 0.68
    },
    "average_score": 0.75,
    "top_score": 0.85,
    "status": "completed"
  },
  "agent_evaluation_runs": [
    {
      "agent_run_id": "round_123_1_round_123_task_0000",
      "round_id": "round_123",
      "validator_uid": 123,
      "miner_uid": 1,
      "version": "1.0",
      "started_at": 1704067200.0,
      "ended_at": 1704070800.0,
      "elapsed_sec": 3600.0,
      "avg_eval_score": 0.85,
      "avg_execution_time": 45.2,
      "avg_reward": 100.0,
      "rank": 1,
      "weight": 0.85
    }
  ],
  "tasks": [
    {
      "task_id": "round_123_task_0000",
      "round_id": "round_123",
      "agent_run_id": "round_123_1_round_123_task_0000",
      "scope": "local",
      "is_web_real": true,
      "web_project_id": "ecommerce_demo",
      "url": "https://demo-store.example.com",
      "prompt": "Navigate to the electronics section and add a laptop to the cart",
      "html": "<html>...</html>",
      "clean_html": "<html>...</html>",
      "interactive_elements": "{\"buttons\": [...], \"forms\": [...]}",
      "screenshot": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...",
      "screenshot_description": "E-commerce homepage with navigation menu",
      "specifications": {
        "browser": "chrome",
        "viewport": {"width": 1920, "height": 1080},
        "timeout": 30
      },
      "tests": [
        {
          "type": "CheckUrlTest",
          "url": "/electronics",
          "match_type": "contains",
          "description": "Check if browser navigated to electronics section"
        },
        {
          "type": "FindInHtmlTest",
          "content": "laptop",
          "match_type": "contains",
          "description": "Find laptop in the page content"
        }
      ],
      "milestones": null,
      "relevant_data": {
        "category": "electronics",
        "product_type": "laptop"
      },
      "success_criteria": "Successfully navigate to electronics and add laptop to cart",
      "use_case": null,
      "should_record": true
    }
  ],
  "task_solutions": [
    {
      "solution_id": "solution_round_123_task_0000_1",
      "task_id": "round_123_task_0000",
      "round_id": "round_123",
      "agent_run_id": "round_123_1_round_123_task_0000",
      "miner_uid": 1,
      "validator_uid": 123,
      "actions": [
        {
          "type": "click",
          "attributes": {
            "selector": "a[href='/electronics']",
            "text": "Electronics"
          }
        },
        {
          "type": "type",
          "attributes": {
            "selector": "input[placeholder='Search products']",
            "text": "laptop"
          }
        },
        {
          "type": "click",
          "attributes": {
            "selector": "button.add-to-cart",
            "text": "Add to Cart"
          }
        }
      ],
      "web_agent_id": "openai-cua-v1",
      "recording": {
        "actions_timeline": [...],
        "browser_events": [...],
        "performance_metrics": {...}
      }
    }
  ],
  "evaluation_results": [
    {
      "evaluation_id": "eval_round_123_task_0000_1",
      "task_id": "round_123_task_0000",
      "task_solution_id": "solution_round_123_task_0000_1",
      "round_id": "round_123",
      "agent_run_id": "round_123_1_round_123_task_0000",
      "miner_uid": 1,
      "validator_uid": 123,
      "final_score": 0.85,
      "test_results_matrix": [
        [
          {
            "success": true,
            "extra_data": {
              "actual_url": "/electronics",
              "expected_url": "/electronics"
            }
          }
        ],
        [
          {
            "success": true,
            "extra_data": {
              "found_content": "laptop",
              "search_time": 1.2
            }
          }
        ]
      ],
      "execution_history": [
        {
          "action": "click",
          "timestamp": 1704067200.0,
          "success": true,
          "duration": 0.5
        }
      ],
      "feedback": {
        "task_prompt": "Navigate to the electronics section and add a laptop to the cart",
        "final_score": 8.5,
        "executed_actions": 3,
        "failed_actions": 0,
        "passed_tests": 2,
        "failed_tests": 0,
        "total_execution_time": 45.2,
        "time_penalty": 0.0,
        "critical_test_penalty": 0,
        "test_results": [
          {
            "success": true,
            "extra_data": {"test_type": "url_check"}
          }
        ],
        "execution_history": [...]
      },
      "web_agent_id": "openai-cua-v1",
      "raw_score": 0.85,
      "evaluation_time": 2.1,
      "stats": {
        "web_agent_id": "openai-cua-v1",
        "task_id": "round_123_task_0000",
        "action_count": 3,
        "action_types": {
          "click": 2,
          "type": 1
        },
        "start_time": 1704067200.0,
        "total_time": 45.2,
        "browser_setup_time": 2.1,
        "action_execution_times": [0.5, 1.2, 0.8],
        "test_execution_time": 1.5,
        "random_clicker_time": 0.0,
        "raw_score": 0.85,
        "final_score": 0.85,
        "tests_passed": 2,
        "total_tests": 2,
        "had_errors": false,
        "error_message": ""
      },
      "gif_recording": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7..."
    }
  ]
}
```

## 🔄 **Validator Integration Flow**

### 1. **Round Initialization**
```python
# Validator starts a new round
round_data = {
    "round_id": f"round_{current_block}",
    "validators": [validator_info],
    "start_block": current_block,
    "start_epoch": current_epoch,
    "n_tasks": 5,
    "n_miners": len(participating_miners),
    "n_winners": 3,
    "miners": participating_miners,
    "status": "active"
}
```

### 2. **Task Generation & Distribution**
```python
# Generate tasks for the round
tasks = generate_synthetic_tasks(
    n_tasks=5,
    web_projects=["ecommerce", "blog"],
    use_cases=["search", "navigate", "purchase"]
)

# Distribute tasks to miners
for miner in participating_miners:
    agent_run_id = f"{round_id}_{miner.uid}_{task.task_id}"
    # Create agent evaluation run
    # Assign tasks to miner
```

### 3. **Collect Miner Responses**
```python
# Collect task solutions from miners
task_solutions = []
for task in tasks:
    for miner in participating_miners:
        solution = await collect_miner_response(task, miner)
        task_solutions.append(solution)
```

### 4. **Evaluate Performance**
```python
# Evaluate each task solution
evaluation_results = []
for solution in task_solutions:
    result = await evaluate_solution(solution)
    evaluation_results.append(result)
```

### 5. **Calculate Scores & Rankings**
```python
# Calculate final scores and rankings
scores = calculate_scores(evaluation_results)
rankings = rank_miners(scores)
winners = select_winners(rankings, n_winners=3)
weights = assign_weights(winners)
```

### 6. **Submit Complete Round Data**
```python
import requests
import json

# Prepare complete submission
submission_data = {
    "round": round_data,
    "agent_evaluation_runs": agent_runs,
    "tasks": tasks,
    "task_solutions": task_solutions,
    "evaluation_results": evaluation_results
}

# Submit to API
response = requests.post(
    "http://localhost:8000/v1/rounds/optimized/submit",
    headers={
        "Content-Type": "application/json",
        "X-API-Key": "your-api-key"
    },
    json=submission_data
)

if response.status_code == 200:
    result = response.json()
    print(f"Round {result['round_id']} submitted successfully")
    print(f"Processing time: {result['processing_time_seconds']:.3f}s")
    print(f"Entities saved: {result['summary']}")
else:
    print(f"Submission failed: {response.text}")
```

## 🎯 **Key Benefits**

### ✅ **Complete Data Storage**
- All round metadata and configuration
- Full task specifications with HTML, screenshots, tests
- Complete miner responses and actions
- Detailed evaluation results and feedback
- Performance statistics and timing data

### ✅ **UI-Ready Data**
- Dashboard metrics and charts
- Leaderboards for rounds, miners, validators
- Agent performance tracking
- Task execution details
- Analytics and trends

### ✅ **Optimized Performance**
- Separated large data (HTML, screenshots, recordings)
- Computed fields for fast queries
- Efficient indexing strategy
- Caching for UI endpoints

### ✅ **Comprehensive Validation**
- Relationship validation between all entities
- Data integrity checks
- Error handling and rollback support

## 🔧 **Implementation Example**

Here's a complete Python example for validators:

```python
import asyncio
import requests
import time
from typing import List, Dict, Any

class AutoppiaValidator:
    def __init__(self, api_base_url: str, api_key: str):
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key
        }
    
    async def submit_complete_round(self, round_data: Dict[str, Any]) -> Dict[str, Any]:
        """Submit complete round data to the API."""
        
        # Validate data structure
        self._validate_round_data(round_data)
        
        # Submit to optimized endpoint
        response = requests.post(
            f"{self.api_base_url}/v1/rounds/optimized/submit",
            headers=self.headers,
            json=round_data
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Submission failed: {response.text}")
    
    def _validate_round_data(self, data: Dict[str, Any]) -> None:
        """Validate round data structure."""
        required_fields = ["round", "agent_evaluation_runs", "tasks", "task_solutions", "evaluation_results"]
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")

# Usage example
async def main():
    validator = AutoppiaValidator(
        api_base_url="http://localhost:8000",
        api_key="your-api-key"
    )
    
    # Your complete round data
    round_data = {
        "round": {...},  # Round metadata
        "agent_evaluation_runs": [...],  # Agent runs
        "tasks": [...],  # Tasks
        "task_solutions": [...],  # Solutions
        "evaluation_results": [...]  # Evaluations
    }
    
    try:
        result = await validator.submit_complete_round(round_data)
        print(f"✅ Round submitted successfully: {result['round_id']}")
        print(f"⏱️ Processing time: {result['processing_time_seconds']:.3f}s")
        print(f"📊 Entities saved: {result['summary']}")
    except Exception as e:
        print(f"❌ Submission failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
```

## 📊 **Data Flow Summary**

```
Validator → POST /v1/rounds/optimized/submit → MongoDB Storage → UI Endpoints → Dashboard
```

1. **Validator** submits complete round data via single API call
2. **API** validates and stores data in optimized MongoDB collections
3. **UI Endpoints** serve aggregated data for dashboard display
4. **Dashboard** displays comprehensive leaderboards, analytics, and details

This design ensures validators can submit all necessary data in one call while the system efficiently stores and serves it for the UI.
