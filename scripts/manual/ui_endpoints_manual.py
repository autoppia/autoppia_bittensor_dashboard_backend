#!/usr/bin/env python3
"""
Test script for UI endpoints to ensure they work correctly with mock data.
"""
import asyncio
import sys
import os
import json
import time
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import httpx
from scripts.data_generation.generate_test_data_new import NewTestDataGenerator


class UIEndpointTester:
    """Test all UI endpoints to ensure they work correctly."""
    
    def __init__(self, base_url: str = "http://localhost:8001"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)
        self.test_data_generator = NewTestDataGenerator()
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()
    
    async def test_health(self):
        """Test health endpoint."""
        print("🔍 Testing health endpoint...")
        try:
            response = await self.client.get(f"{self.base_url}/health")
            if response.status_code == 200:
                print("✅ Health endpoint working")
                return True
            else:
                print(f"❌ Health endpoint failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Health endpoint error: {e}")
            return False
    
    async def generate_and_submit_test_data(self):
        """Generate and submit test data."""
        print("📊 Generating and submitting test data...")
        try:
            # Generate test data
            submission = await self.test_data_generator.generate_round_submission("test_ui_round_001")
            payload = submission.model_dump(mode='json')
            
            # Submit data
            response = await self.client.post(
                f"{self.base_url}/v1/rounds/submit",
                json=payload,
                headers={"X-API-Key": "test-api-key"}
            )
            
            if response.status_code == 200:
                print("✅ Test data submitted successfully")
                return True
            else:
                print(f"❌ Failed to submit test data: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Error generating/submitting test data: {e}")
            return False
    
    async def test_overview_endpoint(self):
        """Test overview dashboard endpoint."""
        print("🔍 Testing overview endpoint...")
        try:
            response = await self.client.get(f"{self.base_url}/v1/ui/overview")
            if response.status_code == 200:
                data = response.json()
                print("✅ Overview endpoint working")
                print(f"   - Success: {data.get('success', False)}")
                print(f"   - Active validators: {data.get('overview', {}).get('active_validators', 0)}")
                print(f"   - Registered miners: {data.get('overview', {}).get('registered_miners', 0)}")
                print(f"   - Total rounds: {data.get('overview', {}).get('total_rounds', 0)}")
                return True
            else:
                print(f"❌ Overview endpoint failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Overview endpoint error: {e}")
            return False
    
    async def test_leaderboard_endpoints(self):
        """Test leaderboard endpoints."""
        print("🔍 Testing leaderboard endpoints...")
        
        endpoints = [
            ("rounds", "Rounds leaderboard"),
            ("miners", "Miners leaderboard"),
            ("validators", "Validators leaderboard")
        ]
        
        all_passed = True
        for endpoint_type, description in endpoints:
            try:
                response = await self.client.get(
                    f"{self.base_url}/v1/ui/leaderboard",
                    params={"type": endpoint_type, "limit": 5}
                )
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ {description} working")
                    print(f"   - Success: {data.get('success', False)}")
                    print(f"   - Total entries: {data.get('total_entries', 0)}")
                else:
                    print(f"❌ {description} failed: {response.status_code} - {response.text}")
                    all_passed = False
            except Exception as e:
                print(f"❌ {description} error: {e}")
                all_passed = False
        
        return all_passed
    
    async def test_agents_endpoint(self):
        """Test agents list endpoint."""
        print("🔍 Testing agents endpoint...")
        try:
            response = await self.client.get(
                f"{self.base_url}/v1/ui/agents",
                params={"limit": 10}
            )
            if response.status_code == 200:
                data = response.json()
                print("✅ Agents endpoint working")
                print(f"   - Success: {data.get('success', False)}")
                print(f"   - Total agents: {data.get('agents', {}).get('total_agents', 0)}")
                print(f"   - Agents returned: {len(data.get('agents', {}).get('agents', []))}")
                return True
            else:
                print(f"❌ Agents endpoint failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Agents endpoint error: {e}")
            return False
    
    async def test_analytics_endpoint(self):
        """Test analytics endpoint."""
        print("🔍 Testing analytics endpoint...")
        try:
            response = await self.client.get(f"{self.base_url}/v1/ui/analytics")
            if response.status_code == 200:
                data = response.json()
                print("✅ Analytics endpoint working")
                print(f"   - Success: {data.get('success', False)}")
                performance_data = data.get('analytics', {}).get('performance', [])
                print(f"   - Performance data points: {len(performance_data)}")
                return True
            else:
                print(f"❌ Analytics endpoint failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Analytics endpoint error: {e}")
            return False
    
    async def test_rounds_endpoints(self):
        """Test rounds GET endpoints."""
        print("🔍 Testing rounds GET endpoints...")
        try:
            # Test list rounds
            response = await self.client.get(f"{self.base_url}/v1/rounds/")
            if response.status_code == 200:
                data = response.json()
                print("✅ Rounds list endpoint working")
                print(f"   - Rounds returned: {len(data)}")
                if data:
                    validator_round_id = data[0].get('validator_round_id')
                    print(f"   - First round ID: {validator_round_id}")
                    
                    # Test get specific round
                    if validator_round_id:
                        round_response = await self.client.get(f"{self.base_url}/v1/rounds/{validator_round_id}")
                        if round_response.status_code == 200:
                            print("✅ Get specific round endpoint working")
                        else:
                            print(f"❌ Get specific round failed: {round_response.status_code}")
                return True
            else:
                print(f"❌ Rounds list endpoint failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Rounds endpoints error: {e}")
            return False
    
    async def run_all_tests(self):
        """Run all UI endpoint tests."""
        print("🚀 Starting UI Endpoint Tests")
        print("=" * 50)
        
        tests = [
            ("Health Check", self.test_health),
            ("Generate Test Data", self.generate_and_submit_test_data),
            ("Overview Dashboard", self.test_overview_endpoint),
            ("Leaderboard Endpoints", self.test_leaderboard_endpoints),
            ("Agents List", self.test_agents_endpoint),
            ("Analytics", self.test_analytics_endpoint),
            ("Rounds GET Endpoints", self.test_rounds_endpoints),
        ]
        
        results = []
        for test_name, test_func in tests:
            print(f"\n📋 {test_name}")
            print("-" * 30)
            try:
                result = await test_func()
                results.append((test_name, result))
            except Exception as e:
                print(f"❌ {test_name} failed with exception: {e}")
                results.append((test_name, False))
        
        # Summary
        print("\n" + "=" * 50)
        print("📊 TEST SUMMARY")
        print("=" * 50)
        
        passed = 0
        total = len(results)
        
        for test_name, result in results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{status} - {test_name}")
            if result:
                passed += 1
        
        print(f"\n🎯 Results: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All UI endpoints are working correctly!")
        else:
            print("⚠️  Some UI endpoints have issues that need to be fixed.")
        
        return passed == total


async def main():
    """Main test function."""
    async with UIEndpointTester() as tester:
        success = await tester.run_all_tests()
        return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
