#!/bin/bash

# API Performance Test Runner
# This script runs comprehensive performance tests on all API endpoints

echo "🚀 API Performance Test Runner"
echo "=============================="

# Check if server is running
if ! curl -s http://localhost:8000/health > /dev/null; then
    echo "❌ Server is not running on localhost:8000"
    echo "Please start the server first:"
    echo "  cd /home/usuario1/autoppia/autoppia_bittensor_dashboard_backend"
    echo "  python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
    exit 1
fi

echo "✅ Server is running"

# Create reports directory
mkdir -p reports

# Test with cache enabled (default)
echo ""
echo "📊 Testing with CACHE ENABLED..."
echo "================================"
python3 scripts/performance_test.py --iterations 3 --save reports/performance_with_cache.json

echo ""
echo "📊 Testing with CACHE DISABLED..."
echo "================================="
python3 scripts/performance_test.py --iterations 3 --no-cache --save reports/performance_no_cache.json

echo ""
echo "🎉 Performance testing complete!"
echo "📁 Reports saved in: reports/"
echo ""
echo "💡 To run individual tests:"
echo "  python3 scripts/performance_test.py --help"
echo ""
echo "🔍 To test specific scenarios:"
echo "  python3 scripts/performance_test.py --iterations 10 --no-cache"
echo "  python3 scripts/performance_test.py --parallel --iterations 5"
