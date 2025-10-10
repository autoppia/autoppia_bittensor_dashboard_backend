#!/usr/bin/env python3
"""
Simple test to verify UI endpoints are working.
"""
import asyncio
import sys
import os
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import httpx


async def test_ui_endpoints():
    """Test UI endpoints with a simple approach."""
    base_url = "http://localhost:8001"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("🔍 Testing UI endpoints...")
        
        # Test health first
        try:
            response = await client.get(f"{base_url}/health")
            print(f"Health: {response.status_code}")
        except Exception as e:
            print(f"Health error: {e}")
            return False
        
        # Test overview endpoint
        try:
            response = await client.get(f"{base_url}/v1/ui/overview")
            print(f"Overview: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Overview success: {data.get('success', False)}")
        except Exception as e:
            print(f"Overview error: {e}")
        
        # Test leaderboard endpoint
        try:
            response = await client.get(f"{base_url}/v1/ui/leaderboard?type=rounds&limit=5")
            print(f"Leaderboard: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Leaderboard success: {data.get('success', False)}")
        except Exception as e:
            print(f"Leaderboard error: {e}")
        
        # Test agents endpoint
        try:
            response = await client.get(f"{base_url}/v1/ui/agents?limit=5")
            print(f"Agents: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Agents success: {data.get('success', False)}")
        except Exception as e:
            print(f"Agents error: {e}")
        
        # Test analytics endpoint
        try:
            response = await client.get(f"{base_url}/v1/ui/analytics")
            print(f"Analytics: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Analytics success: {data.get('success', False)}")
        except Exception as e:
            print(f"Analytics error: {e}")
        
        return True


if __name__ == "__main__":
    asyncio.run(test_ui_endpoints())
