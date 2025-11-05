#!/usr/bin/env python3
"""
Script to invalidate the agent aggregator cache by calling the API.
"""

import requests
import sys

API_BASE = "https://dev-api-leaderboard.autoppia.com"


def invalidate_cache():
    """Force cache invalidation by making a request with cache headers."""
    try:
        # Make a request that should bypass cache
        response = requests.get(
            f"{API_BASE}/api/v1/agents",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
            params={"limit": 1},
            timeout=10,
        )

        if response.status_code == 200:
            print(f"✅ Successfully fetched agents (status: {response.status_code})")
            print("⏳ Cache should rebuild on next request...")
            return True
        else:
            print(f"❌ Failed to fetch agents (status: {response.status_code})")
            return False

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


if __name__ == "__main__":
    print("🔄 Invalidating agent aggregator cache...")
    print(f"📡 API Base: {API_BASE}")
    print()

    success = invalidate_cache()

    if success:
        print()
        print("✅ Cache invalidation triggered!")
        print("💡 The cache will rebuild automatically on the next request.")
        print("   Images should now appear correctly for miners.")
        sys.exit(0)
    else:
        print()
        print("❌ Cache invalidation failed!")
        print("💡 Try restarting the backend server instead.")
        sys.exit(1)
