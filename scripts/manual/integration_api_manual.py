#!/usr/bin/env python3
"""
Simple test script for the Autoppia Leaderboard API.
Run this after starting the API server to verify everything works.
"""

import asyncio
import json
import time
from typing import Dict, Any

import httpx


API_BASE = "http://localhost:8080"
API_KEY = "dev-token-123"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}


async def test_health():
    """Test the health endpoint."""
    print("Testing health endpoint...")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_BASE}/health")
        print(f"Health check: {response.status_code} - {response.json()}")
        return response.status_code == 200


async def test_start_round():
    """Test starting a round."""
    print("Testing start round...")
    
    round_data = {
        "validator_uid": 12,
        "validator_round_id": f"test-{int(time.time())}",
        "version": "test-1.0.0",
        "max_epochs": 20,
        "max_blocks": 7200,
        "started_at": time.time(),
        "start_block": 18172,
        "n_total_miners": 96,
        "task_set": [],
        "meta": {"netuid": 36, "test": True}
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/rounds/start",
            headers=HEADERS,
            json=round_data
        )
        print(f"Start round: {response.status_code} - {response.json()}")
        return response.status_code == 200, round_data["validator_round_id"]


async def test_post_event(validator_round_id: str):
    """Test posting an event."""
    print("Testing post event...")
    
    event_data = {
        "validator_uid": 12,
        "validator_round_id": validator_round_id,
        "phase": "sending_tasks",
        "message": "Test event message",
        "extra": {"test": True}
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/rounds/{validator_round_id}/events",
            headers=HEADERS,
            json=event_data
        )
        print(f"Post event: {response.status_code} - {response.json()}")
        return response.status_code == 200


async def test_task_runs(validator_round_id: str):
    """Test batch upserting task runs."""
    print("Testing task runs...")
    
    task_runs_data = {
        "validator_uid": 12,
        "validator_round_id": validator_round_id,
        "task_runs": [
            {
                "validator_uid": 12,
                "validator_round_id": validator_round_id,
                "task_id": "test-task-1",
                "miner_uid": 44,
                "miner_hotkey": "5Ftest...",
                "miner_coldkey": "5Gtest...",
                "eval_score": 0.8,
                "time_score": 0.9,
                "execution_time": 12.4,
                "reward": 0.7,
                "solution": {"test": "solution"},
                "test_results": {"passed": True},
                "evaluation_result": {"score": 0.8}
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/rounds/{validator_round_id}/task-runs:batch-upsert",
            headers=HEADERS,
            json=task_runs_data
        )
        print(f"Task runs: {response.status_code} - {response.json()}")
        return response.status_code == 200


async def test_agent_runs(validator_round_id: str):
    """Test upserting agent runs."""
    print("Testing agent runs...")
    
    agent_runs_data = {
        "validator_uid": 12,
        "validator_round_id": validator_round_id,
        "agent_runs": [
            {
                "validator_uid": 12,
                "validator_round_id": validator_round_id,
                "miner_uid": 44,
                "miner_hotkey": "5Ftest...",
                "miner_coldkey": "5Gtest...",
                "reward": 0.7,
                "eval_score": 0.8,
                "time_score": 0.9,
                "execution_time": 12.4,
                "tasks_count": 1
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/rounds/{validator_round_id}/agent-runs:upsert",
            headers=HEADERS,
            json=agent_runs_data
        )
        print(f"Agent runs: {response.status_code} - {response.json()}")
        return response.status_code == 200


async def test_weights(validator_round_id: str):
    """Test updating weights."""
    print("Testing weights...")
    
    weights_data = {
        "validator_uid": 12,
        "validator_round_id": validator_round_id,
        "weights": {
            "full_uids": [44, 45, 46],
            "rewards_full_avg": [0.7, 0.6, 0.8],
            "rewards_full_wta": [0.0, 0.0, 1.0],
            "winner_uid": 46
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{API_BASE}/v1/rounds/{validator_round_id}/weights",
            headers=HEADERS,
            json=weights_data
        )
        print(f"Weights: {response.status_code} - {response.json()}")
        return response.status_code == 200


async def test_finalize_round(validator_round_id: str):
    """Test finalizing a round."""
    print("Testing finalize round...")
    
    finalize_data = {
        "validator_uid": 12,
        "validator_round_id": validator_round_id,
        "ended_at": time.time(),
        "elapsed_sec": 300.0,
        "n_active_miners": 1,
        "n_total_miners": 96,
        "stats": {"total_tasks": 1, "completed_tasks": 1},
        "meta": {"test": True}
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/rounds/{validator_round_id}/finalize",
            headers=HEADERS,
            json=finalize_data
        )
        print(f"Finalize: {response.status_code} - {response.json()}")
        return response.status_code == 200


async def test_get_status(validator_round_id: str):
    """Test getting round status."""
    print("Testing get status...")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE}/v1/rounds/{validator_round_id}/status?validator_uid=12",
            headers=HEADERS
        )
        print(f"Get status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"  Round state: {data.get('data', {}).get('round', {}).get('state', 'unknown')}")
            print(f"  Task runs: {data.get('data', {}).get('task_runs_count', 0)}")
            print(f"  Agent runs: {data.get('data', {}).get('agent_runs_count', 0)}")
        return response.status_code == 200


async def main():
    """Run all tests."""
    print("Starting Autoppia Leaderboard API tests...")
    print("=" * 50)
    
    # Test health
    if not await test_health():
        print("❌ Health check failed!")
        return
    
    # Test start round
    success, validator_round_id = await test_start_round()
    if not success:
        print("❌ Start round failed!")
        return
    
    print(f"✅ Using round ID: {validator_round_id}")
    
    # Test other endpoints
    tests = [
        test_post_event(validator_round_id),
        test_task_runs(validator_round_id),
        test_agent_runs(validator_round_id),
        test_weights(validator_round_id),
        test_finalize_round(validator_round_id),
        test_get_status(validator_round_id)
    ]
    
    results = await asyncio.gather(*tests, return_exceptions=True)
    
    print("=" * 50)
    print("Test Results:")
    test_names = [
        "Post Event", "Task Runs", "Agent Runs", 
        "Weights", "Finalize", "Get Status"
    ]
    
    for name, result in zip(test_names, results):
        if isinstance(result, Exception):
            print(f"❌ {name}: Exception - {result}")
        elif result:
            print(f"✅ {name}: Passed")
        else:
            print(f"❌ {name}: Failed")
    
    print("=" * 50)
    print("Tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
