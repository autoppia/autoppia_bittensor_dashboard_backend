#!/usr/bin/env python3
"""
Simple test to check what's working.
"""

import requests

def test_health():
    """Test health endpoint."""
    try:
        response = requests.get("http://localhost:8001/health", timeout=5)
        print(f"Health: {response.status_code} - {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Health error: {e}")
        return False

def test_post_simple():
    """Test POST with minimal data."""
    try:
        # Create minimal test data
        test_data = {
            "round": {
                "round_id": "test_round_001",
                "validator_info": {
                    "uid": 123,
                    "hotkey": "test_hotkey",
                    "stake": 1000.0
                },
                "miners": [
                    {
                        "uid": 1,
                        "hotkey": "miner_hotkey_1",
                        "stake": 500.0
                    }
                ],
                "start_block": 1000,
                "start_epoch": 50,
                "end_block": 1100,
                "end_epoch": 55,
                "started_at": 1234567890.0,
                "ended_at": 1234567890.0,
                "elapsed_sec": 300.0,
                "max_epochs": 20,
                "max_blocks": 360,
                "n_tasks": 1,
                "n_miners": 1,
                "n_winners": 1,
                "winners": [
                    {
                        "miner_uid": 1,
                        "rank": 1,
                        "score": 0.9,
                        "reward": 9.0
                    }
                ],
                "metadata": {"test": True}
            },
            "agent_evaluation_runs": [
                {
                    "agent_run_id": "test_round_001_1",
                    "round_id": "test_round_001",
                    "validator_uid": 123,
                    "miner_uid": 1,
                    "started_at": 1234567890.0,
                    "ended_at": 1234567890.0,
                    "elapsed_sec": 300.0,
                    "n_tasks_total": 1,
                    "n_tasks_completed": 1,
                    "n_tasks_failed": 0,
                    "avg_eval_score": 0.9,
                    "avg_execution_time": 5.0,
                    "total_reward": 9.0,
                    "rank": 1,
                    "weight": 0.8,
                    "status": "completed",
                    "metadata": {"test": True}
                }
            ],
            "tasks": [
                {
                    "task_id": "test_round_001_task_0000",
                    "round_id": "test_round_001",
                    "agent_run_id": "test_round_001_1",
                    "scope": "global",
                    "is_web_real": True,
                    "web_project_id": "test_project",
                    "url": "https://example.com",
                    "prompt": "Test task",
                    "html": "<html><body>Test</body></html>",
                    "clean_html": "<body>Test</body>",
                    "interactive_elements": "{}",
                    "screenshot": "base64_data",
                    "screenshot_description": "Test screenshot",
                    "specifications": {"browser": "chrome"},
                    "created_at": "2023-01-01T00:00:00Z"
                }
            ],
            "task_solutions": [
                {
                    "solution_id": "test_solution_001",
                    "task_id": "test_round_001_task_0000",
                    "round_id": "test_round_001",
                    "agent_run_id": "test_round_001_1",
                    "miner_uid": 1,
                    "validator_uid": 123,
                    "actions": [
                        {
                            "type": "click",
                            "attributes": {"selector": "button", "timestamp": 1234567890.0}
                        }
                    ],
                    "web_agent_id": "test_agent",
                    "recording": "test_recording"
                }
            ],
            "evaluation_results": [
                {
                    "evaluation_id": "test_eval_001",
                    "task_id": "test_round_001_task_0000",
                    "task_solution_id": "test_solution_001",
                    "round_id": "test_round_001",
                    "agent_run_id": "test_round_001_1",
                    "miner_uid": 1,
                    "validator_uid": 123,
                    "final_score": 9.0,
                    "test_results_matrix": [[{"success": True, "extra_data": {}}]],
                    "execution_history": [{"action": "start", "timestamp": 1234567890.0}],
                    "feedback": {
                        "task_prompt": "Test task",
                        "final_score": 9.0,
                        "executed_actions": 1,
                        "failed_actions": 0,
                        "passed_tests": 1,
                        "failed_tests": 0,
                        "total_execution_time": 5.0,
                        "time_penalty": 0.0,
                        "critical_test_penalty": 0,
                        "test_results": [{"success": True, "extra_data": {}}],
                        "execution_history": [{"action": "start", "timestamp": 1234567890.0}]
                    },
                    "web_agent_id": "test_agent",
                    "raw_score": 8.0,
                    "evaluation_time": 2.0
                }
            ]
        }
        
        response = requests.post("http://localhost:8001/v1/rounds/submit", json=test_data, timeout=30)
        print(f"POST: {response.status_code}")
        if response.status_code != 200:
            print(f"Error: {response.text}")
        return response.status_code == 200
        
    except Exception as e:
        print(f"POST error: {e}")
        return False

def test_get_rounds():
    """Test GET rounds endpoint."""
    try:
        response = requests.get("http://localhost:8001/v1/rounds/", timeout=10)
        print(f"GET rounds: {response.status_code}")
        if response.status_code != 200:
            print(f"Error: {response.text}")
        return response.status_code == 200
        
    except Exception as e:
        print(f"GET error: {e}")
        return False

def main():
    """Run all tests."""
    print("🧪 Simple System Test")
    print("=" * 40)
    
    # Test health
    print("\n🏥 Testing health endpoint...")
    health_ok = test_health()
    
    # Test POST
    print("\n🚀 Testing POST endpoint...")
    post_ok = test_post_simple()
    
    # Test GET
    print("\n🔍 Testing GET endpoint...")
    get_ok = test_get_rounds()
    
    # Summary
    print(f"\n📊 Test Summary:")
    print(f"   Health: {'✅' if health_ok else '❌'}")
    print(f"   POST: {'✅' if post_ok else '❌'}")
    print(f"   GET: {'✅' if get_ok else '❌'}")
    
    if health_ok and post_ok and get_ok:
        print("\n🎉 All tests passed!")
    else:
        print("\n⚠️  Some tests failed!")

if __name__ == "__main__":
    main()
