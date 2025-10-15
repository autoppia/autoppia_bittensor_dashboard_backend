#!/usr/bin/env python3
"""
Test the POST endpoint with generated data.
"""

import os
import sys
import asyncio
import json
import requests
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

# Import the generator directly
sys.path.append(str(Path(__file__).parent.parent / "data_generation"))
from generate_test_data_new import NewTestDataGenerator


async def test_post_endpoint():
    """Test the POST endpoint with generated data."""
    print("🧪 Testing POST Endpoint")
    print("=" * 40)
    
    # Set environment variable for mock mode
    os.environ["USE_MOCK_DB"] = "true"
    
    generator = NewTestDataGenerator()
    
    # Generate test data
    print("📊 Generating test data...")
    submissions = await generator.generate_all_data(num_rounds=1)
    
    if not submissions:
        print("❌ No test data generated!")
        return
    
    submission = submissions[0]
    print(f"✅ Generated submission for round: {submission.round.validator_round_id}")
    
    # Convert to dict for JSON serialization
    payload = submission.model_dump(mode='json')
    
    # Test the POST endpoint
    url = "http://localhost:8001/v1/rounds/submit"
    print(f"\n🚀 Testing POST {url}")
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            response_data = response.json()
            print(f"   ✅ Success!")
            print(f"   Round ID: {response_data.get('validator_round_id', 'N/A')}")
            print(f"   Validator UID: {response_data.get('validator_uid', 'N/A')}")
            print(f"   Processing Time: {response_data.get('processing_time_seconds', 0):.3f}s")
            print(f"   Summary: {response_data.get('summary', {})}")
            return True
        else:
            print(f"   ❌ Error: {response.status_code}")
            try:
                error_data = response.json()
                print(f"   Error Details: {error_data}")
            except:
                print(f"   Error Text: {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"   ❌ Connection Error: Server not running or not accessible")
        return False
    except requests.exceptions.Timeout:
        print(f"   ❌ Timeout: Request took too long")
        return False
    except Exception as e:
        print(f"   ❌ Exception: {e}")
        return False


async def test_get_endpoints():
    """Test the GET endpoints."""
    print("\n🔍 Testing GET Endpoints")
    print("=" * 40)
    
    # Test rounds list
    print(f"\n📋 Testing GET /v1/rounds/")
    try:
        response = requests.get("http://localhost:8001/v1/rounds/", timeout=10)
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Success!")
            print(f"   Rounds Retrieved: {len(data)}")
            if data:
                first_round = data[0]
                print(f"   First Round ID: {first_round.get('validator_round_id', 'N/A')}")
                print(f"   Agent Runs: {len(first_round.get('agent_evaluation_runs', []))}")
        else:
            print(f"   ❌ Error: {response.status_code}")
            print(f"   Error: {response.text}")
            
    except Exception as e:
        print(f"   ❌ Exception: {e}")
    
    # Test health endpoint
    print(f"\n🏥 Testing GET /health")
    try:
        response = requests.get("http://localhost:8001/health", timeout=5)
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Success!")
            print(f"   Status: {data.get('status', 'N/A')}")
        else:
            print(f"   ❌ Error: {response.status_code}")
            
    except Exception as e:
        print(f"   ❌ Exception: {e}")


async def main():
    """Main function."""
    print("🧪 Autoppia Validator Pipeline - Endpoint Test")
    print("=" * 60)
    
    # Test POST endpoint
    post_success = await test_post_endpoint()
    
    # Test GET endpoints
    await test_get_endpoints()
    
    # Summary
    print(f"\n📊 Test Summary")
    print("=" * 40)
    if post_success:
        print("🎉 POST endpoint test passed!")
        print("✅ The API is ready for validator submissions!")
    else:
        print("⚠️  POST endpoint test failed!")
        print("❌ Check the server logs for details")
    
    return post_success


if __name__ == "__main__":
    asyncio.run(main())
