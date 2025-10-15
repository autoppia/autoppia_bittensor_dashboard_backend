#!/usr/bin/env python3
"""
Performance testing script for API endpoints.
Tests all endpoints with and without cache to identify performance issues.
"""

import asyncio
import time
import requests
import json
import statistics
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import argparse


@dataclass
class EndpointResult:
    """Result of an endpoint performance test."""
    endpoint: str
    status_code: int
    response_time: float
    success: bool
    error: Optional[str] = None
    data_size: Optional[int] = None


@dataclass
class PerformanceReport:
    """Performance report for an endpoint."""
    endpoint: str
    avg_time: float
    min_time: float
    max_time: float
    median_time: float
    success_rate: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    errors: List[str]


class APIPerformanceTester:
    """API Performance Tester."""
    
    def __init__(self, base_url: str = "http://localhost:8000", disable_cache: bool = False):
        self.base_url = base_url.rstrip('/')
        self.disable_cache = disable_cache
        self.session = requests.Session()
        
        # Add cache-busting headers if cache is disabled
        if self.disable_cache:
            self.session.headers.update({
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            })
        
        # Control server-side cache if needed
        if self.disable_cache:
            self._disable_server_cache()
        else:
            self._enable_server_cache()
    
    def _disable_server_cache(self):
        """Disable server-side cache."""
        try:
            response = self.session.post(f"{self.base_url}/debug/cache-disable")
            if response.status_code == 200:
                print("✅ Server-side cache disabled")
            else:
                print(f"⚠️  Failed to disable server cache: {response.status_code}")
        except Exception as e:
            print(f"⚠️  Could not disable server cache: {e}")
    
    def _enable_server_cache(self):
        """Enable server-side cache."""
        try:
            response = self.session.post(f"{self.base_url}/debug/cache-enable")
            if response.status_code == 200:
                print("✅ Server-side cache enabled")
            else:
                print(f"⚠️  Failed to enable server cache: {response.status_code}")
        except Exception as e:
            print(f"⚠️  Could not enable server cache: {e}")
    
    def _clear_server_cache(self):
        """Clear server-side cache."""
        try:
            response = self.session.post(f"{self.base_url}/debug/cache-clear")
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Server cache cleared: {data.get('cleared', 0)} entries")
            else:
                print(f"⚠️  Failed to clear server cache: {response.status_code}")
        except Exception as e:
            print(f"⚠️  Could not clear server cache: {e}")
    
    def get_all_endpoints(self) -> List[Tuple[str, str, Dict]]:
        """Get all API endpoints to test."""
        return [
            # Overview endpoints
            ("/api/v1/overview", "Overview metrics", {}),
            ("/api/v1/overview/metrics", "Overview metrics detailed", {}),
            ("/api/v1/overview/validators", "Validators list", {}),
            ("/api/v1/overview/validators?page=1&limit=5", "Validators list (paginated)", {}),
            ("/api/v1/overview/validators/validator_124", "Validator detail", {}),
            ("/api/v1/overview/rounds/current", "Current round", {}),
            ("/api/v1/overview/rounds", "Rounds list", {}),
            ("/api/v1/overview/rounds?page=1&limit=5", "Rounds list (paginated)", {}),
            ("/api/v1/overview/rounds/20", "Round detail", {}),
            ("/api/v1/overview/leaderboard", "Leaderboard", {}),
            ("/api/v1/overview/statistics", "Statistics", {}),
            ("/api/v1/overview/network-status", "Network status", {}),
            ("/api/v1/overview/recent-activity", "Recent activity", {}),
            ("/api/v1/overview/performance-trends", "Performance trends", {}),
            
            # Rounds endpoints
            ("/api/v1/rounds", "Rounds API list", {}),
            ("/api/v1/rounds?page=1&limit=5", "Rounds API list (paginated)", {}),
            ("/api/v1/rounds/20", "Round API detail", {}),
            ("/api/v1/rounds/20/progress", "Round progress", {}),
            ("/api/v1/rounds/20/statistics", "Round statistics", {}),
            ("/api/v1/rounds/20/miners", "Round miners", {}),
            ("/api/v1/rounds/20/validators", "Round validators", {}),
            ("/api/v1/rounds/20/activity", "Round activity", {}),
            ("/api/v1/rounds/20/timeline", "Round timeline", {}),
            
            # Agents endpoints
            ("/api/v1/agents", "Agents list", {}),
            ("/api/v1/agents?page=1&limit=5", "Agents list (paginated)", {}),
            ("/api/v1/agents/anthropic-cua", "Agent detail", {}),
            
            # Tasks endpoints
            ("/api/v1/tasks", "Tasks list", {}),
            ("/api/v1/tasks?page=1&limit=5", "Tasks list (paginated)", {}),
            ("/api/v1/tasks/task-3413", "Task detail", {}),
            ("/api/v1/tasks/search", "Task search", {}),
            ("/api/v1/tasks/analytics", "Task analytics", {}),
            
            # Agent runs endpoints
            ("/api/v1/agent-runs", "Agent runs list", {}),
            ("/api/v1/agent-runs?page=1&limit=5", "Agent runs list (paginated)", {}),
            ("/api/v1/agent-runs/run-001", "Agent run detail", {}),
            ("/api/v1/agent-runs/agents/anthropic-cua/runs", "Agent runs by agent", {}),
            ("/api/v1/agent-runs/rounds/20/agent-runs", "Agent runs by round", {}),
            
            # Health check
            ("/health", "Health check", {}),
        ]
    
    def test_endpoint(self, endpoint: str, description: str, params: Dict) -> EndpointResult:
        """Test a single endpoint."""
        url = f"{self.base_url}{endpoint}"
        
        try:
            start_time = time.time()
            response = self.session.get(url, params=params, timeout=30)
            response_time = time.time() - start_time
            
            # Get response size
            data_size = len(response.content) if response.content else 0
            
            # Check if response is successful
            success = response.status_code == 200
            error = None
            
            if not success:
                try:
                    error_data = response.json()
                    error = error_data.get('error', f'HTTP {response.status_code}')
                except:
                    error = f'HTTP {response.status_code}'
            
            return EndpointResult(
                endpoint=endpoint,
                status_code=response.status_code,
                response_time=response_time,
                success=success,
                error=error,
                data_size=data_size
            )
            
        except requests.exceptions.Timeout:
            return EndpointResult(
                endpoint=endpoint,
                status_code=0,
                response_time=30.0,
                success=False,
                error="Timeout (30s)"
            )
        except Exception as e:
            return EndpointResult(
                endpoint=endpoint,
                status_code=0,
                response_time=0.0,
                success=False,
                error=str(e)
            )
    
    def test_endpoint_multiple_times(self, endpoint: str, description: str, params: Dict, iterations: int = 5) -> PerformanceReport:
        """Test an endpoint multiple times and generate a performance report."""
        results = []
        
        print(f"Testing {endpoint} ({iterations} iterations)...")
        
        for i in range(iterations):
            result = self.test_endpoint(endpoint, description, params)
            results.append(result)
            
            # Small delay between requests to avoid overwhelming the server
            if i < iterations - 1:
                time.sleep(0.1)
        
        # Calculate statistics
        response_times = [r.response_time for r in results]
        successful_results = [r for r in results if r.success]
        failed_results = [r for r in results if not r.success]
        
        return PerformanceReport(
            endpoint=endpoint,
            avg_time=statistics.mean(response_times),
            min_time=min(response_times),
            max_time=max(response_times),
            median_time=statistics.median(response_times),
            success_rate=len(successful_results) / len(results) * 100,
            total_requests=len(results),
            successful_requests=len(successful_results),
            failed_requests=len(failed_results),
            errors=[r.error for r in failed_results if r.error]
        )
    
    def run_performance_test(self, iterations: int = 5, parallel: bool = False) -> List[PerformanceReport]:
        """Run performance test on all endpoints."""
        endpoints = self.get_all_endpoints()
        reports = []
        
        print(f"🚀 Starting API Performance Test")
        print(f"📊 Testing {len(endpoints)} endpoints")
        print(f"🔄 {iterations} iterations per endpoint")
        print(f"💾 Cache: {'DISABLED' if self.disable_cache else 'ENABLED'}")
        print(f"⚡ Parallel: {'YES' if parallel else 'NO'}")
        print("=" * 80)
        
        # Clear cache before testing to ensure consistent results
        if not self.disable_cache:
            self._clear_server_cache()
        
        if parallel:
            # Test endpoints in parallel (be careful not to overwhelm the server)
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = []
                for endpoint, description, params in endpoints:
                    future = executor.submit(
                        self.test_endpoint_multiple_times, 
                        endpoint, description, params, iterations
                    )
                    futures.append(future)
                
                for future in futures:
                    reports.append(future.result())
        else:
            # Test endpoints sequentially
            for endpoint, description, params in endpoints:
                report = self.test_endpoint_multiple_times(endpoint, description, params, iterations)
                reports.append(report)
        
        return reports
    
    def print_report(self, reports: List[PerformanceReport]):
        """Print a comprehensive performance report."""
        print("\n" + "=" * 80)
        print("📊 PERFORMANCE REPORT")
        print("=" * 80)
        
        # Sort by average response time (slowest first)
        reports.sort(key=lambda x: x.avg_time, reverse=True)
        
        # Performance thresholds
        FAST_THRESHOLD = 0.1  # 100ms
        SLOW_THRESHOLD = 1.0  # 1 second
        VERY_SLOW_THRESHOLD = 5.0  # 5 seconds
        
        fast_endpoints = []
        slow_endpoints = []
        very_slow_endpoints = []
        failed_endpoints = []
        
        print(f"{'Endpoint':<50} {'Avg(ms)':<10} {'Min(ms)':<10} {'Max(ms)':<10} {'Success%':<10} {'Status'}")
        print("-" * 100)
        
        for report in reports:
            avg_ms = report.avg_time * 1000
            min_ms = report.min_time * 1000
            max_ms = report.max_time * 1000
            
            if report.success_rate < 100:
                status = "❌ FAILED"
                failed_endpoints.append(report)
            elif report.avg_time >= VERY_SLOW_THRESHOLD:
                status = "🐌 VERY SLOW"
                very_slow_endpoints.append(report)
            elif report.avg_time >= SLOW_THRESHOLD:
                status = "⚠️  SLOW"
                slow_endpoints.append(report)
            elif report.avg_time >= FAST_THRESHOLD:
                status = "✅ OK"
                fast_endpoints.append(report)
            else:
                status = "🚀 FAST"
                fast_endpoints.append(report)
            
            print(f"{report.endpoint:<50} {avg_ms:<10.1f} {min_ms:<10.1f} {max_ms:<10.1f} {report.success_rate:<10.1f} {status}")
        
        # Summary
        print("\n" + "=" * 80)
        print("📈 SUMMARY")
        print("=" * 80)
        
        total_endpoints = len(reports)
        print(f"Total endpoints tested: {total_endpoints}")
        print(f"🚀 Fast (<100ms): {len(fast_endpoints)}")
        print(f"✅ OK (100ms-1s): {len([r for r in reports if FAST_THRESHOLD <= r.avg_time < SLOW_THRESHOLD and r.success_rate == 100])}")
        print(f"⚠️  Slow (1s-5s): {len(slow_endpoints)}")
        print(f"🐌 Very slow (>5s): {len(very_slow_endpoints)}")
        print(f"❌ Failed: {len(failed_endpoints)}")
        
        # Detailed analysis of slow endpoints
        if slow_endpoints or very_slow_endpoints:
            print("\n" + "=" * 80)
            print("⚠️  SLOW ENDPOINTS ANALYSIS")
            print("=" * 80)
            
            for report in very_slow_endpoints + slow_endpoints:
                print(f"\n🔍 {report.endpoint}")
                print(f"   Average time: {report.avg_time:.3f}s")
                print(f"   Min time: {report.min_time:.3f}s")
                print(f"   Max time: {report.max_time:.3f}s")
                print(f"   Success rate: {report.success_rate:.1f}%")
                if report.errors:
                    print(f"   Errors: {', '.join(set(report.errors))}")
        
        # Failed endpoints
        if failed_endpoints:
            print("\n" + "=" * 80)
            print("❌ FAILED ENDPOINTS")
            print("=" * 80)
            
            for report in failed_endpoints:
                print(f"\n💥 {report.endpoint}")
                print(f"   Success rate: {report.success_rate:.1f}%")
                print(f"   Errors: {', '.join(set(report.errors))}")
        
        # Recommendations
        print("\n" + "=" * 80)
        print("💡 RECOMMENDATIONS")
        print("=" * 80)
        
        if very_slow_endpoints:
            print("🔧 CRITICAL: Optimize very slow endpoints (>5s):")
            for report in very_slow_endpoints:
                print(f"   - {report.endpoint}")
        
        if slow_endpoints:
            print("🔧 HIGH: Consider optimizing slow endpoints (1-5s):")
            for report in slow_endpoints:
                print(f"   - {report.endpoint}")
        
        if failed_endpoints:
            print("🔧 CRITICAL: Fix failed endpoints:")
            for report in failed_endpoints:
                print(f"   - {report.endpoint}")
        
        if not (slow_endpoints or very_slow_endpoints or failed_endpoints):
            print("🎉 All endpoints are performing well!")
    
    def save_report(self, reports: List[PerformanceReport], filename: str = None):
        """Save performance report to JSON file."""
        if filename is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            cache_suffix = "no_cache" if self.disable_cache else "with_cache"
            filename = f"performance_report_{cache_suffix}_{timestamp}.json"
        
        report_data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "base_url": self.base_url,
            "cache_disabled": self.disable_cache,
            "reports": [
                {
                    "endpoint": report.endpoint,
                    "avg_time": report.avg_time,
                    "min_time": report.min_time,
                    "max_time": report.max_time,
                    "median_time": report.median_time,
                    "success_rate": report.success_rate,
                    "total_requests": report.total_requests,
                    "successful_requests": report.successful_requests,
                    "failed_requests": report.failed_requests,
                    "errors": report.errors
                }
                for report in reports
            ]
        }
        
        with open(filename, 'w') as f:
            json.dump(report_data, f, indent=2)
        
        print(f"\n💾 Report saved to: {filename}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="API Performance Tester")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the API")
    parser.add_argument("--iterations", type=int, default=5, help="Number of iterations per endpoint")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache for testing")
    parser.add_argument("--parallel", action="store_true", help="Test endpoints in parallel")
    parser.add_argument("--save", help="Save report to file")
    
    args = parser.parse_args()
    
    # Create tester
    tester = APIPerformanceTester(
        base_url=args.url,
        disable_cache=args.no_cache
    )
    
    # Run performance test
    reports = tester.run_performance_test(
        iterations=args.iterations,
        parallel=args.parallel
    )
    
    # Print report
    tester.print_report(reports)
    
    # Save report if requested
    if args.save:
        tester.save_report(reports, args.save)


if __name__ == "__main__":
    main()
