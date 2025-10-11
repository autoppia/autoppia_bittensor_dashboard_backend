"""
Tasks service for AutoPPIA Bittensor Dashboard
"""

from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import logging
from ..models.tasks import (
    Task, TaskDetails, TaskResults, TaskStatistics, TaskMetrics,
    TaskAnalytics, PersonasData, TaskSearchResponse, TaskListResponse,
    TaskActionsResponse, TaskLogsResponse, TaskScreenshotsResponse,
    TaskTimelineResponse, CompareTasksResponse, WebsitePerformance,
    UseCasePerformance, RecentActivity, PerformanceOverTime,
    SearchFacets, FacetItem
)

logger = logging.getLogger(__name__)

class TasksService:
    """Service for managing tasks data and operations"""
    
    def __init__(self):
        self.mock_tasks = self._load_mock_tasks()
    
    def _load_mock_tasks(self) -> Dict[str, Dict[str, Any]]:
        """Load mock tasks data"""
        return {
            "task-3413": {
                "taskId": "task-3413",
                "agentRunId": "a7k2-9m4x",
                "website": "Autozone",
                "useCase": "buy_product",
                "prompt": "Buy a product which has a price of 1000",
                "status": "completed",
                "score": 0.82,
                "successRate": 75,
                "duration": 45,
                "startTime": "2024-01-15T10:30:00Z",
                "endTime": "2024-01-15T10:30:45Z",
                "createdAt": "2024-01-15T10:30:00Z",
                "updatedAt": "2024-01-15T10:30:45Z",
                "actions": [
                    {
                        "id": "action-1",
                        "type": "navigate",
                        "selector": None,
                        "value": "http://localhost:8000/",
                        "timestamp": "2024-01-15T10:30:00Z",
                        "duration": 2.1,
                        "success": True,
                        "screenshot": "screenshot-1.png"
                    },
                    {
                        "id": "action-2",
                        "type": "click",
                        "selector": "#product-link",
                        "value": None,
                        "timestamp": "2024-01-15T10:30:05Z",
                        "duration": 1.5,
                        "success": True,
                        "screenshot": "screenshot-2.png"
                    }
                ],
                "screenshots": ["screenshot-1.png", "screenshot-2.png"],
                "logs": ["Task started", "Navigation successful", "Product clicked", "Task completed"],
                "metadata": {
                    "environment": "production",
                    "browser": "chrome",
                    "viewport": {"width": 1920, "height": 1080},
                    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            },
            "task-3414": {
                "taskId": "task-3414",
                "agentRunId": "b8l3-0n5y",
                "website": "AutoDining",
                "useCase": "book_reservation",
                "prompt": "Book a table for 4 people at 7 PM",
                "status": "completed",
                "score": 0.75,
                "successRate": 80,
                "duration": 60,
                "startTime": "2024-01-15T11:00:00Z",
                "endTime": "2024-01-15T11:01:00Z",
                "createdAt": "2024-01-15T11:00:00Z",
                "updatedAt": "2024-01-15T11:01:00Z",
                "actions": [
                    {
                        "id": "action-1",
                        "type": "navigate",
                        "selector": None,
                        "value": "http://autodining.com/",
                        "timestamp": "2024-01-15T11:00:00Z",
                        "duration": 3.2,
                        "success": True,
                        "screenshot": "screenshot-1.png"
                    }
                ],
                "screenshots": ["screenshot-1.png"],
                "logs": ["Task started", "Navigation successful", "Reservation booked", "Task completed"],
                "metadata": {
                    "environment": "production",
                    "browser": "chrome",
                    "viewport": {"width": 1920, "height": 1080},
                    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            }
        }
    
    def get_task_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a task by its ID"""
        return self.mock_tasks.get(task_id)
    
    def get_tasks_list(
        self,
        page: int = 1,
        limit: int = 20,
        agent_run_id: Optional[str] = None,
        website: Optional[str] = None,
        use_case: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> TaskListResponse:
        """Get paginated list of tasks with filters"""
        tasks = list(self.mock_tasks.values())
        
        # Apply filters
        if agent_run_id:
            tasks = [t for t in tasks if t["agentRunId"] == agent_run_id]
        if website:
            tasks = [t for t in tasks if t["website"] == website]
        if use_case:
            tasks = [t for t in tasks if t["useCase"] == use_case]
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        
        # Remove detailed fields for list view
        for task in tasks:
            task.pop("actions", None)
            task.pop("screenshots", None)
            task.pop("logs", None)
            task.pop("metadata", None)
        
        # Sort tasks
        reverse = sort_order.lower() == "desc"
        if sort_by in ["startTime", "endTime", "createdAt", "updatedAt"]:
            tasks.sort(key=lambda t: t[sort_by], reverse=reverse)
        elif sort_by in ["score", "duration", "successRate"]:
            tasks.sort(key=lambda t: t[sort_by], reverse=reverse)
        
        # Pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_tasks = tasks[start_idx:end_idx]
        
        return TaskListResponse(
            tasks=paginated_tasks,
            total=len(tasks),
            page=page,
            limit=limit
        )
    
    def search_tasks(
        self,
        query: Optional[str] = None,
        website: Optional[str] = None,
        use_case: Optional[str] = None,
        status: Optional[str] = None,
        agent_run_id: Optional[str] = None,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        sort_by: str = "startTime",
        sort_order: str = "desc"
    ) -> TaskSearchResponse:
        """Search tasks with advanced filters"""
        tasks = list(self.mock_tasks.values())
        
        # Apply filters
        if website:
            tasks = [t for t in tasks if t["website"] == website]
        if use_case:
            tasks = [t for t in tasks if t["useCase"] == use_case]
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        if agent_run_id:
            tasks = [t for t in tasks if t["agentRunId"] == agent_run_id]
        if min_score is not None:
            tasks = [t for t in tasks if t["score"] >= min_score]
        if max_score is not None:
            tasks = [t for t in tasks if t["score"] <= max_score]
        
        # Text search
        if query:
            tasks = [
                t for t in tasks
                if query.lower() in t["prompt"].lower() or
                   query.lower() in t["website"].lower() or
                   query.lower() in t["useCase"].lower()
            ]
        
        # Remove detailed fields for search results
        for task in tasks:
            task.pop("actions", None)
            task.pop("screenshots", None)
            task.pop("logs", None)
            task.pop("metadata", None)
        
        # Pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_tasks = tasks[start_idx:end_idx]
        
        # Generate facets
        facets = self._generate_facets(tasks)
        
        return TaskSearchResponse(
            tasks=paginated_tasks,
            total=len(tasks),
            page=page,
            limit=limit,
            facets=facets
        )
    
    def _generate_facets(self, tasks: List[Dict[str, Any]]) -> SearchFacets:
        """Generate search facets from tasks"""
        websites = {}
        use_cases = {}
        statuses = {}
        score_ranges = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, "0.8-1.0": 0}
        
        for task in tasks:
            # Website facets
            website = task["website"]
            websites[website] = websites.get(website, 0) + 1
            
            # Use case facets
            use_case = task["useCase"]
            use_cases[use_case] = use_cases.get(use_case, 0) + 1
            
            # Status facets
            status = task["status"]
            statuses[status] = statuses.get(status, 0) + 1
            
            # Score range facets
            score = task["score"]
            if score < 0.2:
                score_ranges["0.0-0.2"] += 1
            elif score < 0.4:
                score_ranges["0.2-0.4"] += 1
            elif score < 0.6:
                score_ranges["0.4-0.6"] += 1
            elif score < 0.8:
                score_ranges["0.6-0.8"] += 1
            else:
                score_ranges["0.8-1.0"] += 1
        
        return SearchFacets(
            websites=[FacetItem(name=k, count=v) for k, v in websites.items()],
            useCases=[FacetItem(name=k, count=v) for k, v in use_cases.items()],
            statuses=[FacetItem(name=k, count=v) for k, v in statuses.items()],
            scoreRanges=[FacetItem(name=k, count=v) for k, v in score_ranges.items() if v > 0]
        )
    
    def get_task_actions(
        self,
        task_id: str,
        page: int = 1,
        limit: int = 20,
        action_type: Optional[str] = None,
        sort_by: str = "timestamp",
        sort_order: str = "asc"
    ) -> TaskActionsResponse:
        """Get actions for a specific task"""
        task = self.get_task_by_id(task_id)
        if not task:
            return TaskActionsResponse(actions=[], total=0, page=page, limit=limit)
        
        actions = task.get("actions", [])
        
        # Apply type filter
        if action_type:
            actions = [a for a in actions if a["type"] == action_type]
        
        # Sort actions
        reverse = sort_order.lower() == "desc"
        if sort_by == "timestamp":
            actions.sort(key=lambda a: a["timestamp"], reverse=reverse)
        elif sort_by == "duration":
            actions.sort(key=lambda a: a["duration"], reverse=reverse)
        
        # Pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_actions = actions[start_idx:end_idx]
        
        return TaskActionsResponse(
            actions=paginated_actions,
            total=len(actions),
            page=page,
            limit=limit
        )
    
    def get_task_screenshots(self, task_id: str) -> TaskScreenshotsResponse:
        """Get screenshots for a specific task"""
        task = self.get_task_by_id(task_id)
        if not task:
            return TaskScreenshotsResponse(screenshots=[])
        
        screenshots = task.get("screenshots", [])
        screenshot_data = [
            {
                "id": f"screenshot-{i+1}",
                "url": f"/screenshots/{task_id}/screenshot-{i+1}.png",
                "timestamp": "2024-01-15T10:30:00Z",
                "actionId": f"action-{i+1}",
                "description": f"Screenshot {i+1}"
            }
            for i, screenshot in enumerate(screenshots)
        ]
        
        return TaskScreenshotsResponse(screenshots=screenshot_data)
    
    def get_task_logs(
        self,
        task_id: str,
        level: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> TaskLogsResponse:
        """Get logs for a specific task"""
        task = self.get_task_by_id(task_id)
        if not task:
            return TaskLogsResponse(logs=[], total=0)
        
        logs = task.get("logs", [])
        log_data = [
            {
                "timestamp": "2024-01-15T10:30:00Z",
                "level": "info",
                "message": log,
                "metadata": {"taskId": task_id}
            }
            for log in logs
        ]
        
        # Apply level filter
        if level:
            log_data = [l for l in log_data if l["level"] == level]
        
        # Apply pagination
        paginated_logs = log_data[offset:offset + limit]
        
        return TaskLogsResponse(
            logs=paginated_logs,
            total=len(log_data)
        )
    
    def get_task_metrics(self, task_id: str) -> TaskMetrics:
        """Get performance metrics for a specific task"""
        task = self.get_task_by_id(task_id)
        if not task:
            return TaskMetrics(
                duration=0,
                actionsPerSecond=0.0,
                averageActionDuration=0.0,
                totalWaitTime=0.0,
                totalNavigationTime=0.0,
                memoryUsage=[],
                cpuUsage=[]
            )
        
        actions = task.get("actions", [])
        duration = task["duration"]
        
        return TaskMetrics(
            duration=duration,
            actionsPerSecond=len(actions) / duration if duration > 0 else 0.0,
            averageActionDuration=sum(a["duration"] for a in actions) / len(actions) if actions else 0.0,
            totalWaitTime=sum(a["duration"] for a in actions if a["type"] == "wait"),
            totalNavigationTime=sum(a["duration"] for a in actions if a["type"] == "navigate"),
            memoryUsage=[
                {"timestamp": "2024-01-15T10:30:00Z", "value": 256}
            ],
            cpuUsage=[
                {"timestamp": "2024-01-15T10:30:00Z", "value": 15.5}
            ]
        )
    
    def get_task_timeline(self, task_id: str) -> TaskTimelineResponse:
        """Get timeline of events for a specific task"""
        task = self.get_task_by_id(task_id)
        if not task:
            return TaskTimelineResponse(timeline=[])
        
        actions = task.get("actions", [])
        timeline = [
            {
                "timestamp": action["timestamp"],
                "action": f"{action['type'].title()}Action",
                "duration": action["duration"],
                "success": action["success"],
                "metadata": {
                    "actionId": action["id"],
                    "selector": action.get("selector"),
                    "value": action.get("value")
                }
            }
            for action in actions
        ]
        
        return TaskTimelineResponse(timeline=timeline)
    
    def get_task_statistics(self, task_id: str) -> TaskStatistics:
        """Get statistics for a specific task"""
        task = self.get_task_by_id(task_id)
        if not task:
            return TaskStatistics(
                totalTasks=0,
                completedTasks=0,
                failedTasks=0,
                runningTasks=0,
                averageScore=0.0,
                averageDuration=0.0,
                successRate=0.0,
                performanceByWebsite=[],
                performanceByUseCase=[],
                recentActivity=[]
            )
        
        return TaskStatistics(
            totalTasks=1,
            completedTasks=1 if task["status"] == "completed" else 0,
            failedTasks=1 if task["status"] == "failed" else 0,
            runningTasks=1 if task["status"] == "running" else 0,
            averageScore=task["score"],
            averageDuration=task["duration"],
            successRate=100.0 if task["status"] == "completed" else 0.0,
            performanceByWebsite=[
                WebsitePerformance(
                    website=task["website"],
                    tasks=1,
                    successful=1 if task["status"] == "completed" else 0,
                    failed=1 if task["status"] == "failed" else 0,
                    averageScore=task["score"],
                    averageDuration=task["duration"]
                )
            ],
            performanceByUseCase=[
                UseCasePerformance(
                    useCase=task["useCase"],
                    tasks=1,
                    successful=1 if task["status"] == "completed" else 0,
                    failed=1 if task["status"] == "failed" else 0,
                    averageScore=task["score"],
                    averageDuration=task["duration"]
                )
            ],
            recentActivity=[
                RecentActivity(
                    timestamp=task["endTime"],
                    action="task_completed",
                    details=f"Task completed successfully with score {task['score']}"
                )
            ]
        )
    
    def get_task_personas(self, task_id: str) -> PersonasData:
        """Get personas data for a task"""
        task = self.get_task_by_id(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        return PersonasData(
            round={
                "id": 11,
                "name": "Round 11",
                "status": "active",
                "startTime": "2024-01-15T00:00:00Z",
                "endTime": "2024-01-15T23:59:59Z"
            },
            validator={
                "id": "autoppia",
                "name": "AutoPPIA",
                "image": "/validators/Autoppia.png",
                "description": "AutoPPIA Validator",
                "website": "https://autoppia.com",
                "github": "https://github.com/autoppia"
            },
            agent={
                "id": "agent-42",
                "name": "AutoPPIA Agent",
                "type": "autoppia",
                "image": "/agents/autoppia.png",
                "description": "AutoPPIA's main agent"
            },
            task={
                "id": task_id,
                "website": task["website"],
                "useCase": task["useCase"],
                "status": task["status"],
                "score": task["score"]
            }
        )
    
    def compare_tasks(self, task_ids: List[str]) -> CompareTasksResponse:
        """Compare multiple tasks"""
        tasks = []
        for task_id in task_ids:
            task = self.get_task_by_id(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            tasks.append(task)
        
        if not tasks:
            raise ValueError("No tasks found for comparison")
        
        # Find best performers
        best_score_task = max(tasks, key=lambda t: t["score"])
        fastest_task = min(tasks, key=lambda t: t["duration"])
        most_actions_task = max(tasks, key=lambda t: len(t.get("actions", [])))
        best_success_rate_task = max(tasks, key=lambda t: t["successRate"])
        
        comparison = {
            "bestScore": best_score_task["taskId"],
            "fastest": fastest_task["taskId"],
            "mostActions": most_actions_task["taskId"],
            "bestSuccessRate": best_success_rate_task["taskId"]
        }
        
        return CompareTasksResponse(
            tasks=tasks,
            comparison=comparison
        )
    
    def get_task_analytics(
        self,
        time_range: str = "24h",
        website: Optional[str] = None,
        use_case: Optional[str] = None,
        agent_run_id: Optional[str] = None
    ) -> TaskAnalytics:
        """Get analytics data for tasks"""
        # In production, this would query the database with time range filters
        all_tasks = list(self.mock_tasks.values())
        
        # Apply filters
        if website:
            all_tasks = [t for t in all_tasks if t["website"] == website]
        if use_case:
            all_tasks = [t for t in all_tasks if t["useCase"] == use_case]
        if agent_run_id:
            all_tasks = [t for t in all_tasks if t["agentRunId"] == agent_run_id]
        
        total_tasks = len(all_tasks)
        completed_tasks = len([t for t in all_tasks if t["status"] == "completed"])
        failed_tasks = len([t for t in all_tasks if t["status"] == "failed"])
        
        average_score = sum(t["score"] for t in all_tasks) / total_tasks if total_tasks > 0 else 0.0
        average_duration = sum(t["duration"] for t in all_tasks) / total_tasks if total_tasks > 0 else 0.0
        success_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0.0
        
        # Performance by website
        website_stats = {}
        for task in all_tasks:
            website = task["website"]
            if website not in website_stats:
                website_stats[website] = {"tasks": 0, "successful": 0, "failed": 0, "totalScore": 0.0, "totalDuration": 0}
            
            website_stats[website]["tasks"] += 1
            website_stats[website]["totalScore"] += task["score"]
            website_stats[website]["totalDuration"] += task["duration"]
            
            if task["status"] == "completed":
                website_stats[website]["successful"] += 1
            elif task["status"] == "failed":
                website_stats[website]["failed"] += 1
        
        performance_by_website = [
            WebsitePerformance(
                website=website,
                tasks=stats["tasks"],
                successful=stats["successful"],
                failed=stats["failed"],
                averageScore=stats["totalScore"] / stats["tasks"],
                averageDuration=stats["totalDuration"] / stats["tasks"]
            )
            for website, stats in website_stats.items()
        ]
        
        # Performance by use case
        use_case_stats = {}
        for task in all_tasks:
            use_case = task["useCase"]
            if use_case not in use_case_stats:
                use_case_stats[use_case] = {"tasks": 0, "successful": 0, "failed": 0, "totalScore": 0.0, "totalDuration": 0}
            
            use_case_stats[use_case]["tasks"] += 1
            use_case_stats[use_case]["totalScore"] += task["score"]
            use_case_stats[use_case]["totalDuration"] += task["duration"]
            
            if task["status"] == "completed":
                use_case_stats[use_case]["successful"] += 1
            elif task["status"] == "failed":
                use_case_stats[use_case]["failed"] += 1
        
        performance_by_use_case = [
            UseCasePerformance(
                useCase=use_case,
                tasks=stats["tasks"],
                successful=stats["successful"],
                failed=stats["failed"],
                averageScore=stats["totalScore"] / stats["tasks"],
                averageDuration=stats["totalDuration"] / stats["tasks"]
            )
            for use_case, stats in use_case_stats.items()
        ]
        
        # Performance over time (mock data)
        performance_over_time = [
            PerformanceOverTime(
                timestamp="2024-01-15T10:00:00Z",
                tasks=5,
                averageScore=0.78,
                successRate=80.0
            )
        ]
        
        return TaskAnalytics(
            totalTasks=total_tasks,
            completedTasks=completed_tasks,
            failedTasks=failed_tasks,
            averageScore=average_score,
            averageDuration=average_duration,
            successRate=success_rate,
            performanceByWebsite=performance_by_website,
            performanceByUseCase=performance_by_use_case,
            performanceOverTime=performance_over_time
        )
