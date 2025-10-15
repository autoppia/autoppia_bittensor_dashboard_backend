#!/usr/bin/env python3
"""
Submit test data to the API.
"""
import asyncio
import sys
import os
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import httpx
from scripts.data_generation.generate_test_data_new import NewTestDataGenerator


async def submit_test_data():
    """Submit test data to the API."""
    base_url = "http://localhost:8001"
    generator = NewTestDataGenerator()
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("📊 Generating and submitting test data...")
        
        try:
            # Generate test data
            submission = await generator.generate_round_submission("test_ui_round_001")
            payload = submission.model_dump(mode='json')
            
            # Submit data
            response = await client.post(
                f"{base_url}/v1/rounds/submit",
                json=payload,
                headers={"X-API-Key": "test-api-key"}
            )
            
            print(f"Submit response: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Success: {data.get('success', False)}")
                print(f"Round ID: {data.get('validator_round_id', 'N/A')}")
                print(f"Processing time: {data.get('processing_time_seconds', 0):.3f}s")
                return True
            else:
                print(f"Error: {response.text}")
                return False
                
        except Exception as e:
            print(f"Error: {e}")
            return False


if __name__ == "__main__":
    success = asyncio.run(submit_test_data())
    sys.exit(0 if success else 1)
