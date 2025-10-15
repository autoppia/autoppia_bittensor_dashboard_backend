#!/usr/bin/env python3
"""
Comprehensive API test script for the validator pipeline.
This script tests all endpoints with real HTTP requests.
"""

import asyncio
import aiohttp
import json
import time
import random
from typing import Dict, Any, List
from pathlib import Path


class APITester:
    """Comprehensive API tester for the validator pipeline."""
    
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "test-api-key"):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = None
        self.test_results = []
        
        # Test data
        self.validator_info = {
            "validator_uid": 999,
            "validator_hotkey": "5TestValidatorKeyForTestingPurposesOnly123456789"
        }
        
        self.miners = [
            {"miner_uid": 1001, "miner_hotkey": "5TestMinerKey1ForTestingPurposesOnly123456789"},
            {"miner_uid": 1002, "miner_hotkey": "5TestMinerKey2ForTestingPurposesOnly123456789"},
            {"miner_uid": 1003, "miner_hotkey": "5TestMinerKey3ForTestingPurposesOnly123456789"}
        ]
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    async def make_request(self, method: str, endpoint: str, data: Dict[str, Any] = None, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make HTTP request with proper headers."""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        try:
            if method.upper() == "GET":
                async with self.session.get(url, headers=headers, params=params) as response:
                    result = await response.json()
            elif method.upper() == "POST":
                async with self.session.post(url, headers=headers, json=data) as response:
                    result = await response.json()
            elif method.upper() == "PUT":
                async with self.session.put(url, headers=headers, json=data) as response:
                    result = await response.json()
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            return {
                "status_code": response.status,
                "data": result,
                "success": response.status < 400
            }
        except Exception as e:
            return {
                "status_code": 0,
                "data": {"error": str(e)},
                "success": False
            }
    
    def log_test(self, test_name: str, result: Dict[str, Any]):
        """Log test result."""
        status = "✅ PASS" if result["success"] else "❌ FAIL"
        print(f"{status} {test_name}")
        if not result["success"]:
            print(f"   Status: {result['status_code']}")
            print(f"   Error: {result['data']}")
        self.test_results.append({
            "test": test_name,
            "success": result["success"],
            "status_code": result["status_code"],
            "data": result["data"]
        })
    
    async def test_round_start(self) -> str:
        """Test starting a new round."""
        validator_round_id = f"test_round_{int(time.time())}"
        
        data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "start_block": 1000,
            "start_epoch": 50,
            "n_tasks": 5,
            "n_miners": 3,
            "n_winners": 3,
            "miners": self.miners,
            "max_epochs": 20,
            "max_blocks": 360,
            "metadata": {"test": True, "generated_by": "api_tester"}
        }
        
        result = await self.make_request("POST", "/v1/rounds/start", data)
        self.log_test("Start Round", result)
        return validator_round_id if result["success"] else None
    
    async def test_generate_tasks(self, validator_round_id: str):
        """Test task generation."""
        data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "n_tasks": 5,
            "web_projects": ["ecommerce", "blog"],
            "use_cases": ["search", "navigate", "purchase"],
            "metadata": {"generation_method": "test"}
        }
        
        result = await self.make_request("POST", f"/v1/rounds/{validator_round_id}/generate-tasks", data)
        self.log_test("Generate Tasks", result)
        return result["success"]
    
    async def test_distribute_tasks(self, validator_round_id: str):
        """Test task distribution."""
        task_ids = [f"{validator_round_id}_task_{i:04d}" for i in range(5)]
        miner_uids = [miner["miner_uid"] for miner in self.miners]
        
        data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "task_ids": task_ids,
            "miner_uids": miner_uids,
            "batch_size": 1,
            "timeout_seconds": 30.0,
            "metadata": {"distribution_method": "test"}
        }
        
        result = await self.make_request("POST", f"/v1/rounds/{validator_round_id}/distribute-tasks", data)
        self.log_test("Distribute Tasks", result)
        return result["success"]
    
    async def test_submit_responses(self, validator_round_id: str):
        """Test submitting task responses."""
        responses = []
        for task_id in [f"{validator_round_id}_task_{i:04d}" for i in range(5)]:
            for miner in self.miners:
                response = {
                    "task_id": task_id,
                    "agent_run_id": f"{validator_round_id}_{miner['miner_uid']}_{task_id}",
                    "validator_round_id": validator_round_id,
                    "validator_info": self.validator_info,
                    "miner_info": miner,
                    "response": {
                        "actions": [
                            {"type": "click", "selector": "button.search"},
                            {"type": "type", "text": "test query", "selector": "input[name='q']"},
                            {"type": "wait", "duration": 2.0}
                        ],
                        "execution_time": random.uniform(3.0, 10.0),
                        "success": True
                    },
                    "received_at": time.time(),
                    "metadata": {"test": True}
                }
                responses.append(response)
        
        data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "responses": responses,
            "batch_id": f"test_batch_{int(time.time())}",
            "metadata": {"test": True}
        }
        
        result = await self.make_request("POST", f"/v1/rounds/{validator_round_id}/task-responses", data)
        self.log_test("Submit Task Responses", result)
        return result["success"]
    
    async def test_evaluate_tasks(self, validator_round_id: str):
        """Test task evaluation."""
        # Generate mock task execution IDs
        task_execution_ids = []
        for i in range(5):
            for miner in self.miners:
                task_execution_ids.append(f"exec_{validator_round_id}_{miner['miner_uid']}_{i}")
        
        data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "task_execution_ids": task_execution_ids,
            "evaluation_criteria": {
                "correctness_weight": 0.7,
                "efficiency_weight": 0.3,
                "max_score": 1.0
            },
            "metadata": {"evaluation_method": "test"}
        }
        
        result = await self.make_request("POST", f"/v1/rounds/{validator_round_id}/evaluate", data)
        self.log_test("Evaluate Tasks", result)
        return result["success"]
    
    async def test_calculate_scores(self, validator_round_id: str):
        """Test score calculation."""
        data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "scoring_method": "weighted_average",
            "weight_distribution": {
                "1": 0.8,
                "2": 0.15,
                "3": 0.05
            },
            "metadata": {"scoring_algorithm": "test"}
        }
        
        result = await self.make_request("POST", f"/v1/rounds/{validator_round_id}/score", data)
        self.log_test("Calculate Scores", result)
        return result["success"]
    
    async def test_assign_weights(self, validator_round_id: str):
        """Test weight assignment."""
        winners = [
            {"miner_uid": 1001, "rank": 1, "score": 0.95, "reward": 9.5},
            {"miner_uid": 1002, "rank": 2, "score": 0.87, "reward": 8.7},
            {"miner_uid": 1003, "rank": 3, "score": 0.78, "reward": 7.8}
        ]
        
        data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "winners": winners,
            "weight_distribution": {
                "1": 0.8,
                "2": 0.15,
                "3": 0.05
            },
            "metadata": {"assignment_method": "test"}
        }
        
        result = await self.make_request("POST", f"/v1/rounds/{validator_round_id}/assign-weights", data)
        self.log_test("Assign Weights", result)
        return result["success"]
    
    async def test_complete_round(self, validator_round_id: str):
        """Test round completion."""
        data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "final_stats": {
                "total_tasks": 5,
                "total_evaluations": 15,
                "avg_score": 0.87,
                "completion_rate": 1.0,
                "total_time": 120.5
            },
            "metadata": {"completed_by": "api_tester"}
        }
        
        result = await self.make_request("POST", f"/v1/rounds/{validator_round_id}/complete", data)
        self.log_test("Complete Round", result)
        return result["success"]
    
    async def test_get_round_status(self, validator_round_id: str):
        """Test getting round status."""
        params = {"validator_uid": self.validator_info["validator_uid"]}
        result = await self.make_request("GET", f"/v1/rounds/{validator_round_id}/status", params=params)
        self.log_test("Get Round Status", result)
        return result["success"]
    
    async def test_get_round_details(self, validator_round_id: str):
        """Test getting round details."""
        params = {"validator_uid": self.validator_info["validator_uid"]}
        result = await self.make_request("GET", f"/v1/rounds/{validator_round_id}/details", params=params)
        self.log_test("Get Round Details", result)
        return result["success"]
    
    async def test_rounds_leaderboard(self):
        """Test rounds leaderboard."""
        params = {
            "validator_uid": self.validator_info["validator_uid"],
            "limit": 10,
            "sort_by": "started_at",
            "sort_order": "desc"
        }
        result = await self.make_request("GET", "/v1/rounds/leaderboard/rounds", params=params)
        self.log_test("Get Rounds Leaderboard", result)
        return result["success"]
    
    async def test_miners_leaderboard(self):
        """Test miners leaderboard."""
        params = {
            "validator_uid": self.validator_info["validator_uid"],
            "limit": 10,
            "sort_by": "avg_score",
            "sort_order": "desc"
        }
        result = await self.make_request("GET", "/v1/rounds/leaderboard/miners", params=params)
        self.log_test("Get Miners Leaderboard", result)
        return result["success"]
    
    async def test_complete_pipeline(self):
        """Test the complete validator pipeline."""
        print("🚀 Testing Complete Validator Pipeline")
        print("=" * 50)
        
        # Start round
        validator_round_id = await self.test_round_start()
        if not validator_round_id:
            print("❌ Failed to start round, aborting pipeline test")
            return False
        
        # Generate tasks
        if not await self.test_generate_tasks(validator_round_id):
            print("❌ Failed to generate tasks")
            return False
        
        # Distribute tasks
        if not await self.test_distribute_tasks(validator_round_id):
            print("❌ Failed to distribute tasks")
            return False
        
        # Submit responses
        if not await self.test_submit_responses(validator_round_id):
            print("❌ Failed to submit responses")
            return False
        
        # Evaluate tasks
        if not await self.test_evaluate_tasks(validator_round_id):
            print("❌ Failed to evaluate tasks")
            return False
        
        # Calculate scores
        if not await self.test_calculate_scores(validator_round_id):
            print("❌ Failed to calculate scores")
            return False
        
        # Assign weights
        if not await self.test_assign_weights(validator_round_id):
            print("❌ Failed to assign weights")
            return False
        
        # Complete round
        if not await self.test_complete_round(validator_round_id):
            print("❌ Failed to complete round")
            return False
        
        print("✅ Complete pipeline test successful!")
        return True
    
    async def test_leaderboard_endpoints(self):
        """Test all leaderboard endpoints."""
        print("\n📊 Testing Leaderboard Endpoints")
        print("=" * 40)
        
        await self.test_rounds_leaderboard()
        await self.test_miners_leaderboard()
    
    async def test_round_queries(self, validator_round_id: str):
        """Test round query endpoints."""
        print(f"\n🔍 Testing Round Query Endpoints for {validator_round_id}")
        print("=" * 50)
        
        await self.test_get_round_status(validator_round_id)
        await self.test_get_round_details(validator_round_id)
    
    def print_summary(self):
        """Print test summary."""
        print("\n📋 Test Summary")
        print("=" * 30)
        
        total_tests = len(self.test_results)
        passed_tests = sum(1 for result in self.test_results if result["success"])
        failed_tests = total_tests - passed_tests
        
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests} ✅")
        print(f"Failed: {failed_tests} ❌")
        print(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")
        
        if failed_tests > 0:
            print("\n❌ Failed Tests:")
            for result in self.test_results:
                if not result["success"]:
                    print(f"   - {result['test']}: {result['data']}")
    
    async def run_all_tests(self):
        """Run all tests."""
        print("🧪 Autoppia Validator Pipeline - Comprehensive API Test")
        print("=" * 60)
        
        # Test complete pipeline
        pipeline_success = await self.test_complete_pipeline()
        
        # Test leaderboard endpoints
        await self.test_leaderboard_endpoints()
        
        # Test round queries (if pipeline was successful)
        if pipeline_success:
            # Get the last round ID from test results
            validator_round_id = None
            for result in self.test_results:
                if result["test"] == "Start Round" and result["success"]:
                    validator_round_id = result["data"].get("data", {}).get("validator_round_id")
                    break
            
            if validator_round_id:
                await self.test_round_queries(validator_round_id)
        
        # Print summary
        self.print_summary()


async def main():
    """Main test function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test the Autoppia Validator Pipeline API")
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--api-key", default="test-api-key", help="API key for authentication")
    parser.add_argument("--pipeline-only", action="store_true", help="Test only the pipeline endpoints")
    parser.add_argument("--leaderboard-only", action="store_true", help="Test only the leaderboard endpoints")
    
    args = parser.parse_args()
    
    async with APITester(args.url, args.api_key) as tester:
        if args.pipeline_only:
            await tester.test_complete_pipeline()
        elif args.leaderboard_only:
            await tester.test_leaderboard_endpoints()
        else:
            await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
