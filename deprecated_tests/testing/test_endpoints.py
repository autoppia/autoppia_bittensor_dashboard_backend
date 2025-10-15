#!/usr/bin/env python3
"""
Test script for the new validator pipeline API endpoints.
Tests both POST (data submission) and GET (data retrieval) endpoints.
"""

import os
import sys
import asyncio
import json
import time
from typing import List, Dict, Any
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

# Import the generator directly
sys.path.append(str(Path(__file__).parent.parent / "data_generation"))
from generate_test_data_new import NewTestDataGenerator
from app.models.schemas import RoundSubmissionRequest


class EndpointTester:
    """Test the API endpoints with generated data."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.generator = NewTestDataGenerator()
    
    async def test_post_submit_round(self, submission: RoundSubmissionRequest) -> Dict[str, Any]:
        """Test the POST /v1/rounds/submit endpoint."""
        import aiohttp
        
        url = f"{self.base_url}/v1/rounds/submit"
        
        # Convert to dict for JSON serialization
        payload = submission.model_dump()
        
        print(f"🚀 Testing POST {url}")
        print(f"   Round ID: {submission.round.validator_round_id}")
        print(f"   Validator UID: {submission.round.validator_info.uid}")
        print(f"   Tasks: {len(submission.tasks)}")
        print(f"   Agent Runs: {len(submission.agent_evaluation_runs)}")
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload) as response:
                    response_data = await response.json()
                    
                    print(f"   Status: {response.status}")
                    if response.status == 200:
                        print(f"   ✅ Success!")
                        print(f"   Processing Time: {response_data.get('processing_time_seconds', 0):.3f}s")
                        print(f"   Entities Saved: {response_data.get('summary', {})}")
                        return {"success": True, "data": response_data}
                    else:
                        print(f"   ❌ Error: {response_data}")
                        return {"success": False, "error": response_data}
                        
            except Exception as e:
                print(f"   ❌ Exception: {str(e)}")
                return {"success": False, "error": str(e)}
    
    async def test_get_rounds_list(self) -> Dict[str, Any]:
        """Test the GET /v1/rounds/ endpoint."""
        import aiohttp
        
        url = f"{self.base_url}/v1/rounds/"
        
        print(f"🔍 Testing GET {url}")
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    response_data = await response.json()
                    
                    print(f"   Status: {response.status}")
                    if response.status == 200:
                        print(f"   ✅ Success!")
                        print(f"   Rounds Retrieved: {len(response_data)}")
                        if response_data:
                            first_round = response_data[0]
                            print(f"   First Round ID: {first_round.get('validator_round_id', 'N/A')}")
                            print(f"   Agent Runs: {len(first_round.get('agent_evaluation_runs', []))}")
                        return {"success": True, "data": response_data}
                    else:
                        print(f"   ❌ Error: {response_data}")
                        return {"success": False, "error": response_data}
                        
            except Exception as e:
                print(f"   ❌ Exception: {str(e)}")
                return {"success": False, "error": str(e)}
    
    async def test_get_round_by_id(self, validator_round_id: str) -> Dict[str, Any]:
        """Test the GET /v1/rounds/{validator_round_id} endpoint."""
        import aiohttp
        
        url = f"{self.base_url}/v1/rounds/{validator_round_id}"
        
        print(f"🔍 Testing GET {url}")
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    response_data = await response.json()
                    
                    print(f"   Status: {response.status}")
                    if response.status == 200:
                        print(f"   ✅ Success!")
                        print(f"   Round ID: {response_data.get('validator_round_id', 'N/A')}")
                        print(f"   Validator UID: {response_data.get('validator_info', {}).get('uid', 'N/A')}")
                        print(f"   Miners: {len(response_data.get('miners', []))}")
                        print(f"   Agent Runs: {len(response_data.get('agent_evaluation_runs', []))}")
                        
                        # Check if agent runs have complete data
                        agent_runs = response_data.get('agent_evaluation_runs', [])
                        if agent_runs:
                            first_agent_run = agent_runs[0]
                            print(f"   First Agent Run Tasks: {len(first_agent_run.get('tasks', []))}")
                            print(f"   First Agent Run Solutions: {len(first_agent_run.get('task_solutions', []))}")
                            print(f"   First Agent Run Evaluations: {len(first_agent_run.get('evaluation_results', []))}")
                        
                        return {"success": True, "data": response_data}
                    else:
                        print(f"   ❌ Error: {response_data}")
                        return {"success": False, "error": response_data}
                        
            except Exception as e:
                print(f"   ❌ Exception: {str(e)}")
                return {"success": False, "error": str(e)}
    
    async def test_get_agent_run_by_id(self, agent_run_id: str) -> Dict[str, Any]:
        """Test the GET /v1/rounds/agent-runs/{agent_run_id} endpoint."""
        import aiohttp
        
        url = f"{self.base_url}/v1/rounds/agent-runs/{agent_run_id}"
        
        print(f"🔍 Testing GET {url}")
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    response_data = await response.json()
                    
                    print(f"   Status: {response.status}")
                    if response.status == 200:
                        print(f"   ✅ Success!")
                        print(f"   Agent Run ID: {response_data.get('agent_run_id', 'N/A')}")
                        print(f"   Miner UID: {response_data.get('miner_uid', 'N/A')}")
                        print(f"   Tasks: {len(response_data.get('tasks', []))}")
                        print(f"   Task Solutions: {len(response_data.get('task_solutions', []))}")
                        print(f"   Evaluation Results: {len(response_data.get('evaluation_results', []))}")
                        return {"success": True, "data": response_data}
                    else:
                        print(f"   ❌ Error: {response_data}")
                        return {"success": False, "error": response_data}
                        
            except Exception as e:
                print(f"   ❌ Exception: {str(e)}")
                return {"success": False, "error": str(e)}
    
    async def test_get_agent_runs_list(self) -> Dict[str, Any]:
        """Test the GET /v1/rounds/agent-runs/ endpoint."""
        import aiohttp
        
        url = f"{self.base_url}/v1/rounds/agent-runs/"
        
        print(f"🔍 Testing GET {url}")
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    response_data = await response.json()
                    
                    print(f"   Status: {response.status}")
                    if response.status == 200:
                        print(f"   ✅ Success!")
                        print(f"   Agent Runs Retrieved: {len(response_data)}")
                        if response_data:
                            first_agent_run = response_data[0]
                            print(f"   First Agent Run ID: {first_agent_run.get('agent_run_id', 'N/A')}")
                            print(f"   Tasks: {len(first_agent_run.get('tasks', []))}")
                        return {"success": True, "data": response_data}
                    else:
                        print(f"   ❌ Error: {response_data}")
                        return {"success": False, "error": response_data}
                        
            except Exception as e:
                print(f"   ❌ Exception: {str(e)}")
                return {"success": False, "error": str(e)}
    
    async def run_comprehensive_test(self):
        """Run a comprehensive test of all endpoints."""
        print("🧪 Autoppia Validator Pipeline - Comprehensive Endpoint Test")
        print("=" * 70)
        
        # Generate test data
        print("\n📊 Generating test data...")
        submissions = await self.generator.generate_all_data(num_rounds=2)
        
        if not submissions:
            print("❌ No test data generated!")
            return
        
        test_results = {
            "post_tests": [],
            "get_tests": []
        }
        
        # Test POST endpoints
        print("\n🚀 Testing POST Endpoints")
        print("-" * 40)
        
        for i, submission in enumerate(submissions):
            print(f"\n📝 Testing submission {i+1}/{len(submissions)}")
            result = await self.test_post_submit_round(submission)
            test_results["post_tests"].append({
                "validator_round_id": submission.round.validator_round_id,
                "result": result
            })
            
            # Wait a bit between submissions
            await asyncio.sleep(1)
        
        # Test GET endpoints
        print("\n🔍 Testing GET Endpoints")
        print("-" * 40)
        
        # Test rounds list
        print(f"\n📋 Testing rounds list...")
        result = await self.test_get_rounds_list()
        test_results["get_tests"].append({"endpoint": "rounds_list", "result": result})
        
        # Test specific round
        if submissions:
            validator_round_id = submissions[0].round.validator_round_id
            print(f"\n🎯 Testing specific round: {validator_round_id}")
            result = await self.test_get_round_by_id(validator_round_id)
            test_results["get_tests"].append({"endpoint": f"round_{validator_round_id}", "result": result})
            
            # Test agent runs list
            print(f"\n👥 Testing agent runs list...")
            result = await self.test_get_agent_runs_list()
            test_results["get_tests"].append({"endpoint": "agent_runs_list", "result": result})
            
            # Test specific agent run
            if submissions[0].agent_evaluation_runs:
                agent_run_id = submissions[0].agent_evaluation_runs[0].agent_run_id
                print(f"\n🎯 Testing specific agent run: {agent_run_id}")
                result = await self.test_get_agent_run_by_id(agent_run_id)
                test_results["get_tests"].append({"endpoint": f"agent_run_{agent_run_id}", "result": result})
        
        # Summary
        print("\n📊 Test Summary")
        print("=" * 40)
        
        post_success = sum(1 for test in test_results["post_tests"] if test["result"]["success"])
        post_total = len(test_results["post_tests"])
        print(f"POST Tests: {post_success}/{post_total} successful")
        
        get_success = sum(1 for test in test_results["get_tests"] if test["result"]["success"])
        get_total = len(test_results["get_tests"])
        print(f"GET Tests: {get_success}/{get_total} successful")
        
        total_success = post_success + get_success
        total_tests = post_total + get_total
        print(f"Overall: {total_success}/{total_tests} successful")
        
        if total_success == total_tests:
            print("🎉 All tests passed!")
        else:
            print("⚠️  Some tests failed. Check the output above for details.")
        
        return test_results


async def main():
    """Main function to run endpoint tests."""
    # Set environment variable for mock mode
    os.environ["USE_MOCK_DB"] = "true"
    
    tester = EndpointTester()
    
    try:
        results = await tester.run_comprehensive_test()
        return results
        
    except Exception as e:
        print(f"❌ Error running tests: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
