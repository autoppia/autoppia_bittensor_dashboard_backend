#!/usr/bin/env python3
"""
Demo script showing the validator pipeline API in action.
This script demonstrates the complete flow from round start to completion.
"""

import asyncio
import aiohttp
import json
import time
from typing import Dict, Any


class APIDemo:
    """Demo the validator pipeline API."""
    
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "test-api-key"):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = None
        
        # Demo data
        self.validator_info = {
            "validator_uid": 999,
            "validator_hotkey": "5DemoValidatorKeyForTestingPurposesOnly123456789"
        }
        
        self.miners = [
            {"miner_uid": 2001, "miner_hotkey": "5DemoMinerKey1ForTestingPurposesOnly123456789"},
            {"miner_uid": 2002, "miner_hotkey": "5DemoMinerKey2ForTestingPurposesOnly123456789"},
            {"miner_uid": 2003, "miner_hotkey": "5DemoMinerKey3ForTestingPurposesOnly123456789"}
        ]
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    async def make_request(self, method: str, endpoint: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make HTTP request."""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        async with self.session.request(method, url, headers=headers, json=data) as response:
            result = await response.json()
            return {
                "status_code": response.status,
                "data": result,
                "success": response.status < 400
            }
    
    async def demo_complete_pipeline(self):
        """Demo the complete validator pipeline."""
        print("🎬 Autoppia Validator Pipeline - Live Demo")
        print("=" * 50)
        
        validator_round_id = f"demo_round_{int(time.time())}"
        print(f"🚀 Starting demo round: {validator_round_id}")
        
        # Step 1: Start Round
        print("\n📋 Step 1: Starting Round...")
        start_data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "start_block": 1000,
            "start_epoch": 50,
            "n_tasks": 3,  # Small number for demo
            "n_miners": 3,
            "n_winners": 3,
            "miners": self.miners,
            "metadata": {"demo": True, "purpose": "api_demonstration"}
        }
        
        result = await self.make_request("POST", "/api/v1/rounds/start", start_data)
        if result["success"]:
            print(f"✅ Round started successfully")
            print(f"   Round ID: {validator_round_id}")
            print(f"   Validator: {self.validator_info['validator_uid']}")
            print(f"   Miners: {len(self.miners)}")
        else:
            print(f"❌ Failed to start round: {result['data']}")
            return
        
        # Step 2: Generate Tasks
        print("\n🎯 Step 2: Generating Tasks...")
        task_data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "n_tasks": 3,
            "web_projects": ["ecommerce", "blog"],
            "use_cases": ["search", "navigate", "purchase"],
            "metadata": {"generation_method": "demo"}
        }
        
        result = await self.make_request("POST", f"/api/v1/rounds/{validator_round_id}/generate-tasks", task_data)
        if result["success"]:
            print(f"✅ Tasks generated successfully")
            print(f"   Tasks created: {result['data']['data']['tasks_generated']}")
        else:
            print(f"❌ Failed to generate tasks: {result['data']}")
            return
        
        # Step 3: Distribute Tasks
        print("\n📤 Step 3: Distributing Tasks...")
        task_ids = [f"{validator_round_id}_task_{i:04d}" for i in range(3)]
        miner_uids = [miner["miner_uid"] for miner in self.miners]
        
        dist_data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "task_ids": task_ids,
            "miner_uids": miner_uids,
            "batch_size": 1,
            "timeout_seconds": 30.0,
            "metadata": {"distribution_method": "demo"}
        }
        
        result = await self.make_request("POST", f"/api/v1/rounds/{validator_round_id}/distribute-tasks", dist_data)
        if result["success"]:
            print(f"✅ Tasks distributed successfully")
            print(f"   Task executions created: {result['data']['data']['task_executions_created']}")
            print(f"   Agent runs created: {result['data']['data']['agent_runs_created']}")
        else:
            print(f"❌ Failed to distribute tasks: {result['data']}")
            return
        
        # Step 4: Submit Responses
        print("\n📥 Step 4: Submitting Task Responses...")
        responses = []
        for task_id in task_ids:
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
                            {"type": "type", "text": "demo query", "selector": "input[name='q']"},
                            {"type": "wait", "duration": 2.0}
                        ],
                        "execution_time": 5.5,
                        "success": True
                    },
                    "received_at": time.time(),
                    "metadata": {"demo": True}
                }
                responses.append(response)
        
        response_data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "responses": responses,
            "batch_id": f"demo_batch_{int(time.time())}",
            "metadata": {"demo": True}
        }
        
        result = await self.make_request("POST", f"/api/v1/rounds/{validator_round_id}/task-responses", response_data)
        if result["success"]:
            print(f"✅ Task responses submitted successfully")
            print(f"   Responses processed: {result['data']['data']['responses_processed']}")
        else:
            print(f"❌ Failed to submit responses: {result['data']}")
            return
        
        # Step 5: Evaluate Tasks
        print("\n🔍 Step 5: Evaluating Tasks...")
        task_execution_ids = []
        for i in range(3):
            for miner in self.miners:
                task_execution_ids.append(f"exec_{validator_round_id}_{miner['miner_uid']}_{i}")
        
        eval_data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "task_execution_ids": task_execution_ids,
            "evaluation_criteria": {
                "correctness_weight": 0.7,
                "efficiency_weight": 0.3,
                "max_score": 1.0
            },
            "metadata": {"evaluation_method": "demo"}
        }
        
        result = await self.make_request("POST", f"/api/v1/rounds/{validator_round_id}/evaluate", eval_data)
        if result["success"]:
            print(f"✅ Tasks evaluated successfully")
            print(f"   Evaluations completed: {result['data']['data']['evaluations_completed']}")
        else:
            print(f"❌ Failed to evaluate tasks: {result['data']}")
            return
        
        # Step 6: Calculate Scores
        print("\n📊 Step 6: Calculating Scores...")
        score_data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "scoring_method": "weighted_average",
            "weight_distribution": {
                "1": 0.8,
                "2": 0.15,
                "3": 0.05
            },
            "metadata": {"scoring_algorithm": "demo"}
        }
        
        result = await self.make_request("POST", f"/api/v1/rounds/{validator_round_id}/score", score_data)
        if result["success"]:
            print(f"✅ Scores calculated successfully")
            print(f"   Agent runs scored: {result['data']['data']['agent_runs_scored']}")
        else:
            print(f"❌ Failed to calculate scores: {result['data']}")
            return
        
        # Step 7: Assign Weights
        print("\n🏆 Step 7: Assigning Weights...")
        winners = [
            {"miner_uid": 2001, "rank": 1, "score": 0.95, "reward": 9.5},
            {"miner_uid": 2002, "rank": 2, "score": 0.87, "reward": 8.7},
            {"miner_uid": 2003, "rank": 3, "score": 0.78, "reward": 7.8}
        ]
        
        weight_data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "winners": winners,
            "weight_distribution": {
                "1": 0.8,
                "2": 0.15,
                "3": 0.05
            },
            "metadata": {"assignment_method": "demo"}
        }
        
        result = await self.make_request("POST", f"/api/v1/rounds/{validator_round_id}/assign-weights", weight_data)
        if result["success"]:
            print(f"✅ Weights assigned successfully")
            weights = result['data']['data']['weights_assigned']
            print(f"   Weights assigned to {len(weights)} miners:")
            for miner_uid, weight in weights.items():
                print(f"     Miner {miner_uid}: {weight}")
        else:
            print(f"❌ Failed to assign weights: {result['data']}")
            return
        
        # Step 8: Complete Round
        print("\n✅ Step 8: Completing Round...")
        complete_data = {
            "validator_round_id": validator_round_id,
            "validator_info": self.validator_info,
            "final_stats": {
                "total_tasks": 3,
                "total_evaluations": 9,
                "avg_score": 0.87,
                "completion_rate": 1.0,
                "total_time": 45.2
            },
            "metadata": {"completed_by": "demo_script"}
        }
        
        result = await self.make_request("POST", f"/api/v1/rounds/{validator_round_id}/complete", complete_data)
        if result["success"]:
            print(f"✅ Round completed successfully")
            print(f"   Status: {result['data']['data']['status']}")
            print(f"   Completed at: {result['data']['data']['completed_at']}")
        else:
            print(f"❌ Failed to complete round: {result['data']}")
            return
        
        # Step 9: Query Results
        print("\n🔍 Step 9: Querying Results...")
        
        # Get round status
        status_result = await self.make_request("GET", f"/api/v1/rounds/{validator_round_id}/status", 
                                               {"validator_uid": self.validator_info["validator_uid"]})
        if status_result["success"]:
            print(f"✅ Round status retrieved")
            status_data = status_result['data']['data']
            print(f"   Status: {status_data['status']}")
            print(f"   Progress: {status_data['progress']}")
        
        # Get round details
        details_result = await self.make_request("GET", f"/api/v1/rounds/{validator_round_id}/details",
                                                {"validator_uid": self.validator_info["validator_uid"]})
        if details_result["success"]:
            print(f"✅ Round details retrieved")
            details_data = details_result['data']['data']
            print(f"   Summary: {details_data['summary']}")
        
        print(f"\n🎉 Demo completed successfully!")
        print(f"📊 Round {validator_round_id} processed {len(task_ids)} tasks with {len(self.miners)} miners")
        print(f"🔗 View API docs at: {self.base_url}/docs")
        
        return validator_round_id
    
    async def demo_leaderboard(self):
        """Demo the leaderboard endpoints."""
        print("\n📈 Leaderboard Demo")
        print("=" * 30)
        
        # Get rounds leaderboard
        print("\n🏆 Rounds Leaderboard:")
        result = await self.make_request("GET", "/api/v1/rounds/leaderboard/rounds", 
                                        {"limit": 5, "sort_by": "started_at", "sort_order": "desc"})
        if result["success"]:
            rounds = result['data']['data']['rounds']
            print(f"✅ Found {len(rounds)} rounds")
            for i, round_data in enumerate(rounds[:3], 1):
                print(f"   {i}. Round {round_data['validator_round_id']} - Status: {round_data['status']}")
        else:
            print(f"❌ Failed to get rounds leaderboard: {result['data']}")
        
        # Get miners leaderboard
        print("\n⛏️ Miners Leaderboard:")
        result = await self.make_request("GET", "/api/v1/rounds/leaderboard/miners",
                                        {"limit": 5, "sort_by": "avg_score", "sort_order": "desc"})
        if result["success"]:
            miners = result['data']['data']['miners']
            print(f"✅ Found {len(miners)} miners")
            for i, miner_data in enumerate(miners[:3], 1):
                print(f"   {i}. Miner {miner_data['miner_info']['miner_uid']} - Avg Score: {miner_data['avg_score']:.3f}")
        else:
            print(f"❌ Failed to get miners leaderboard: {result['data']}")


async def main():
    """Main demo function."""
    print("🎬 Autoppia Validator Pipeline - Interactive Demo")
    print("=" * 60)
    print("This demo will show the complete validator pipeline in action.")
    print("Make sure the API server is running on http://localhost:8000")
    print("=" * 60)
    
    async with APIDemo() as demo:
        try:
            # Demo complete pipeline
            validator_round_id = await demo.demo_complete_pipeline()
            
            # Demo leaderboard
            await demo.demo_leaderboard()
            
            print(f"\n🎯 Demo Summary:")
            print(f"   ✅ Complete pipeline tested")
            print(f"   ✅ Leaderboard endpoints tested")
            print(f"   ✅ Round {validator_round_id} created and processed")
            print(f"   🔗 API Documentation: http://localhost:8000/docs")
            
        except Exception as e:
            print(f"❌ Demo failed: {e}")
            print("Make sure the API server is running: python start_test_server.py")


if __name__ == "__main__":
    asyncio.run(main())
