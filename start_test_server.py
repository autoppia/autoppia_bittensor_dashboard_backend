#!/usr/bin/env python3
"""
Start the API server in test mode with mock database.
"""

import os
import sys
import uvicorn
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent))

# Set environment variables for test mode
os.environ["USE_MOCK_DB"] = "true"
os.environ["API_KEYS"] = '["test-api-key"]'
os.environ["CORS_ORIGINS"] = '["*"]'

# Import the app
from app.main import app

if __name__ == "__main__":
    print("🚀 Starting Autoppia Validator Pipeline API Server (Test Mode)")
    print("=" * 60)
    print("📊 Using Mock Database (JSON files)")
    print("🔑 API Key: test-api-key")
    print("🌐 CORS: Enabled for all origins")
    print("📖 API Docs: http://localhost:8000/docs")
    print("=" * 60)
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
