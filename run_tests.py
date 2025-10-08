#!/usr/bin/env python3
"""
Run the complete test suite for the validator pipeline API.
"""

import asyncio
import subprocess
import time
import sys
import os
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent))


async def run_test_suite():
    """Run the complete test suite."""
    print("🧪 Autoppia Validator Pipeline - Complete Test Suite")
    print("=" * 60)
    
    # Step 1: Generate test data
    print("📊 Step 1: Generating test data...")
    try:
        result = subprocess.run([
            sys.executable, "generate_test_data.py"
        ], capture_output=True, text=True, cwd=Path(__file__).parent)
        
        if result.returncode == 0:
            print("✅ Test data generated successfully")
            print(result.stdout)
        else:
            print("❌ Failed to generate test data")
            print(result.stderr)
            return False
    except Exception as e:
        print(f"❌ Error generating test data: {e}")
        return False
    
    # Step 2: Start the API server
    print("\n🚀 Step 2: Starting API server...")
    server_process = None
    try:
        server_process = subprocess.Popen([
            sys.executable, "start_test_server.py"
        ], cwd=Path(__file__).parent)
        
        # Wait for server to start
        print("⏳ Waiting for server to start...")
        await asyncio.sleep(5)
        
        # Check if server is running
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://localhost:8000/docs") as response:
                    if response.status == 200:
                        print("✅ API server started successfully")
                    else:
                        print(f"❌ API server returned status {response.status}")
                        return False
        except Exception as e:
            print(f"❌ Failed to connect to API server: {e}")
            return False
            
    except Exception as e:
        print(f"❌ Error starting API server: {e}")
        return False
    
    # Step 3: Run API tests
    print("\n🧪 Step 3: Running API tests...")
    try:
        result = subprocess.run([
            sys.executable, "test_api_comprehensive.py",
            "--url", "http://localhost:8000",
            "--api-key", "test-api-key"
        ], capture_output=True, text=True, cwd=Path(__file__).parent)
        
        print(result.stdout)
        if result.stderr:
            print("Errors:")
            print(result.stderr)
        
        if result.returncode == 0:
            print("✅ API tests completed successfully")
        else:
            print("❌ Some API tests failed")
            
    except Exception as e:
        print(f"❌ Error running API tests: {e}")
        return False
    
    # Step 4: Cleanup
    print("\n🧹 Step 4: Cleaning up...")
    if server_process:
        server_process.terminate()
        server_process.wait()
        print("✅ API server stopped")
    
    print("\n🎉 Test suite completed!")
    return True


async def main():
    """Main function."""
    success = await run_test_suite()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
