"""
Tasks API endpoints for AutoPPIA Bittensor Dashboard
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Path, Body
from pydantic import BaseModel
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

# Pydantic models for request/response
class TaskAction(BaseModel):
    id: str
    type: str
    selector: Optional[str] = None
    value: Optional[str] = None
    timestamp: datetime
    duration: float
    success: bool
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    screenshot: Optional[str] = None

class TaskScreenshot(BaseModel):
    id: str
    url: str
    timestamp: datetime
    actionId: Optional[str] = None
    description: Optional[str] = None

class TaskLog(BaseModel):
    timestamp: datetime
    level: str
    message: str
    metadata: Optional[Dict[str, Any]] = None

class TaskMetadata(BaseModel):
    environment: str
    browser: str
    viewport: Dict[str, int]
    userAgent: str
    resources: Optional[Dict[str, Any]] = None

class TaskPerformance(BaseModel):
    totalActions: int
    successfulActions: int
    failedActions: int
    averageActionDuration: float
    totalWaitTime: float
    totalNavigationTime: float

class Task(BaseModel):
    taskId: str
    agentRunId: str
    website: str
    useCase: str
    prompt: str
    status: str
    score: float
    successRate: int
    duration: int
    startTime: datetime
    endTime: datetime
    createdAt: datetime
    updatedAt: datetime
    actions: Optional[List[TaskAction]] = None
    screenshots: Optional[List[str]] = None
    logs: Optional[List[str]] = None
    metadata: Optional[TaskMetadata] = None

class TaskDetails(Task):
    performance: Optional[TaskPerformance] = None

class TaskResults(BaseModel):
    taskId: str
    status: str
    score: float
    duration: int
    actions: List[TaskAction]
    screenshots: List[TaskScreenshot]
    logs: List[TaskLog]
    summary: Dict[str, Any]
    timeline: List[Dict[str, Any]]

class TaskStatistics(BaseModel):
    totalTasks: int
    completedTasks: int
    failedTasks: int
    runningTasks: int
    averageScore: float
    averageDuration: float
    successRate: float
    performanceByWebsite: List[Dict[str, Any]]
    performanceByUseCase: List[Dict[str, Any]]
    recentActivity: List[Dict[str, Any]]

class TaskMetrics(BaseModel):
    duration: int
    actionsPerSecond: float
    averageActionDuration: float
    totalWaitTime: float
    totalNavigationTime: float
    memoryUsage: List[Dict[str, Any]]
    cpuUsage: List[Dict[str, Any]]

class TaskTimeline(BaseModel):
    timestamp: datetime
    action: str
    duration: float
    success: bool
    metadata: Optional[Dict[str, Any]] = None

class CompareTasksRequest(BaseModel):
    taskIds: List[str]

class CompareTasksResponse(BaseModel):
    tasks: List[Task]
    comparison: Dict[str, str]

class TaskAnalytics(BaseModel):
    totalTasks: int
    completedTasks: int
    failedTasks: int
    averageScore: float
    averageDuration: float
    successRate: float
    performanceByWebsite: List[Dict[str, Any]]
    performanceByUseCase: List[Dict[str, Any]]
    performanceOverTime: List[Dict[str, Any]]

class PersonasData(BaseModel):
    round: Dict[str, Any]
    validator: Dict[str, Any]
    agent: Dict[str, Any]
    task: Dict[str, Any]

# Mock data - in production this would come from database
MOCK_TASKS = {
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
                "error": None,
                "metadata": {
                    "url": "http://localhost:8000/",
                    "statusCode": 200
                },
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
                "error": None,
                "metadata": {
                    "element": "product-link",
                    "position": {"x": 100, "y": 200}
                },
                "screenshot": "screenshot-2.png"
            },
            {
                "id": "action-3",
                "type": "wait",
                "selector": None,
                "value": "5",
                "timestamp": "2024-01-15T10:30:10Z",
                "duration": 5.0,
                "success": True,
                "error": None,
                "metadata": {
                    "waitTime": 5.0
                },
                "screenshot": None
            }
        ],
        "screenshots": ["screenshot-1.png", "screenshot-2.png"],
        "logs": ["Task started", "Navigation successful", "Task completed"],
        "metadata": {
            "environment": "production",
            "browser": "chrome",
            "viewport": {"width": 1920, "height": 1080},
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
                "error": None,
                "metadata": {
                    "url": "http://autodining.com/",
                    "statusCode": 200
                },
                "screenshot": "screenshot-1.png"
            }
        ],
        "screenshots": ["screenshot-1.png"],
        "logs": ["Task started", "Navigation successful", "Reservation booked", "Task completed"],
        "metadata": {
            "environment": "production",
            "browser": "chrome",
            "viewport": {"width": 1920, "height": 1080},
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
    }
}

@router.get("/search")
async def search_tasks(
    query: Optional[str] = Query(None, description="Search query"),
    website: Optional[str] = Query(None, description="Filter by website"),
    useCase: Optional[str] = Query(None, description="Filter by use case"),
    status: Optional[str] = Query(None, description="Filter by status"),
    agentRunId: Optional[str] = Query(None, description="Filter by agent run ID"),
    minScore: Optional[float] = Query(None, description="Minimum score filter"),
    maxScore: Optional[float] = Query(None, description="Maximum score filter"),
    startDate: Optional[str] = Query(None, description="Filter by start date"),
    endDate: Optional[str] = Query(None, description="Filter by end date"),
    page: int = Query(1, description="Page number"),
    limit: int = Query(20, description="Items per page"),
    sortBy: Optional[str] = Query("startTime", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort order")
):
    """Advanced search for tasks with multiple filters."""
    tasks = list(MOCK_TASKS.values())
    
    # Apply filters
    if website:
        tasks = [t for t in tasks if t["website"] == website]
    if useCase:
        tasks = [t for t in tasks if t["useCase"] == useCase]
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    if agentRunId:
        tasks = [t for t in tasks if t["agentRunId"] == agentRunId]
    if minScore is not None:
        tasks = [t for t in tasks if t["score"] >= minScore]
    if maxScore is not None:
        tasks = [t for t in tasks if t["score"] <= maxScore]
    
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
    facets = {
        "websites": [{"name": "Autozone", "count": 1}],
        "useCases": [{"name": "buy_product", "count": 1}],
        "statuses": [{"name": "completed", "count": 1}],
        "scoreRanges": [{"range": "0.8-1.0", "count": 1}]
    }
    
    return {
        "data": {
            "tasks": paginated_tasks,
            "total": len(tasks),
            "page": page,
            "limit": limit,
            "facets": facets
        }
    }

@router.get("/analytics")
async def get_task_analytics(
    timeRange: Optional[str] = Query("24h", description="Time range"),
    website: Optional[str] = Query(None, description="Filter by website"),
    useCase: Optional[str] = Query(None, description="Filter by use case"),
    agentRunId: Optional[str] = Query(None, description="Filter by agent run ID")
):
    """Get analytics data for tasks."""
    # In production, this would query the database with time range filters
    analytics = {
        "totalTasks": 150,
        "completedTasks": 120,
        "failedTasks": 30,
        "averageScore": 0.75,
        "averageDuration": 52.3,
        "successRate": 80,
        "performanceByWebsite": [
            {"website": "Autozone", "tasks": 45, "averageScore": 0.78},
            {"website": "AutoDining", "tasks": 32, "averageScore": 0.72}
        ],
        "performanceByUseCase": [
            {"useCase": "buy_product", "tasks": 28, "averageScore": 0.80},
            {"useCase": "book_reservation", "tasks": 15, "averageScore": 0.70}
        ],
        "performanceOverTime": [
            {
                "timestamp": "2024-01-15T10:00:00Z",
                "tasks": 5,
                "averageScore": 0.78,
                "successRate": 80
            }
        ]
    }
    
    return {"data": {"analytics": analytics}}

@router.get("/{taskId}")
async def get_task_details(
    taskId: str = Path(..., description="The unique identifier of the task"),
    includeActions: bool = Query(False, description="Include task actions"),
    includeScreenshots: bool = Query(False, description="Include screenshots"),
    includeLogs: bool = Query(False, description="Include logs"),
    includeMetadata: bool = Query(False, description="Include metadata")
):
    """Get comprehensive details for a specific task."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId].copy()
    
    # Filter data based on query parameters
    if not includeActions:
        task.pop("actions", None)
    if not includeScreenshots:
        task.pop("screenshots", None)
    if not includeLogs:
        task.pop("logs", None)
    if not includeMetadata:
        task.pop("metadata", None)
    
    return {"data": {"task": task}}

@router.get("/{taskId}/personas")
async def get_task_personas(taskId: str = Path(..., description="The unique identifier of the task")):
    """Get personas data (round, validator, agent, task information) for a task."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId]
    
    personas = {
        "round": {
            "id": 11,
            "name": "Round 11",
            "status": "active",
            "startTime": "2024-01-15T00:00:00Z",
            "endTime": "2024-01-15T23:59:59Z"
        },
        "validator": {
            "id": "autoppia",
            "name": "AutoPPIA",
            "image": "/validators/Autoppia.png",
            "description": "AutoPPIA Validator",
            "website": "https://autoppia.com",
            "github": "https://github.com/autoppia"
        },
        "agent": {
            "id": "agent-42",
            "name": "AutoPPIA Agent",
            "type": "autoppia",
            "image": "/agents/autoppia.png",
            "description": "AutoPPIA's main agent"
        },
        "task": {
            "id": taskId,
            "website": task["website"],
            "useCase": task["useCase"],
            "status": task["status"],
            "score": task["score"]
        }
    }
    
    return {"data": {"personas": personas}}

@router.get("/{taskId}/details")
async def get_task_details_extended(taskId: str = Path(..., description="The unique identifier of the task")):
    """Get detailed information for a specific task including performance metrics."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId].copy()
    
    # Add performance metrics
    task["performance"] = {
        "totalActions": 15,
        "successfulActions": 12,
        "failedActions": 3,
        "averageActionDuration": 3.0,
        "totalWaitTime": 10.5,
        "totalNavigationTime": 5.2
    }
    
    # Add resources to metadata
    if "metadata" in task:
        task["metadata"]["resources"] = {
            "cpu": 2.5,
            "memory": 512,
            "network": 1024
        }
    
    return {"data": {"details": task}}

@router.get("/{taskId}/results")
async def get_task_results(taskId: str = Path(..., description="The unique identifier of the task")):
    """Get results and execution details for a specific task."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId]
    
    results = {
        "taskId": taskId,
        "status": task["status"],
        "score": task["score"],
        "duration": task["duration"],
        "actions": task.get("actions", []),
        "screenshots": [
            {
                "id": "screenshot-1",
                "url": f"/screenshots/{taskId}/screenshot-1.png",
                "timestamp": "2024-01-15T10:30:00Z",
                "actionId": "action-1",
                "description": "Initial page load"
            }
        ],
        "logs": [
            {
                "timestamp": "2024-01-15T10:30:00Z",
                "level": "info",
                "message": "Task started",
                "metadata": {"taskId": taskId}
            }
        ],
        "summary": {
            "totalActions": 15,
            "successfulActions": 12,
            "failedActions": 3,
            "actionTypes": {
                "navigate": 1,
                "click": 8,
                "type": 3,
                "wait": 2,
                "scroll": 1,
                "screenshot": 0,
                "other": 0
            }
        },
        "timeline": [
            {
                "timestamp": "2024-01-15T10:30:00Z",
                "action": "NavigateAction",
                "duration": 2.1,
                "success": True
            }
        ]
    }
    
    return {"data": {"results": results}}

@router.get("/{taskId}/statistics")
async def get_task_statistics(taskId: str = Path(..., description="The unique identifier of the task")):
    """Get statistics for a specific task."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId]
    
    statistics = {
        "totalTasks": 1,
        "completedTasks": 1,
        "failedTasks": 0,
        "runningTasks": 0,
        "averageScore": task["score"],
        "averageDuration": task["duration"],
        "successRate": 100,
        "performanceByWebsite": [
            {
                "website": task["website"],
                "tasks": 1,
                "successful": 1,
                "failed": 0,
                "averageScore": task["score"],
                "averageDuration": task["duration"]
            }
        ],
        "performanceByUseCase": [
            {
                "useCase": task["useCase"],
                "tasks": 1,
                "successful": 1,
                "failed": 0,
                "averageScore": task["score"],
                "averageDuration": task["duration"]
            }
        ],
        "recentActivity": [
            {
                "timestamp": task["endTime"],
                "action": "task_completed",
                "details": f"Task completed successfully with score {task['score']}"
            }
        ]
    }
    
    return {"data": {"statistics": statistics}}

@router.get("")
async def get_tasks_list(
    page: int = Query(1, description="Page number"),
    limit: int = Query(20, description="Items per page"),
    agentRunId: Optional[str] = Query(None, description="Filter by agent run ID"),
    website: Optional[str] = Query(None, description="Filter by website"),
    useCase: Optional[str] = Query(None, description="Filter by use case"),
    status: Optional[str] = Query(None, description="Filter by status"),
    sortBy: Optional[str] = Query("startTime", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort order"),
    startDate: Optional[str] = Query(None, description="Filter by start date"),
    endDate: Optional[str] = Query(None, description="Filter by end date")
):
    """Get list of tasks with filtering and pagination."""
    # In production, this would query the database with filters
    tasks = list(MOCK_TASKS.values())
    
    # Apply filters
    if agentRunId:
        tasks = [t for t in tasks if t["agentRunId"] == agentRunId]
    if website:
        tasks = [t for t in tasks if t["website"] == website]
    if useCase:
        tasks = [t for t in tasks if t["useCase"] == useCase]
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    
    # Remove detailed fields for list view
    for task in tasks:
        task.pop("actions", None)
        task.pop("screenshots", None)
        task.pop("logs", None)
        task.pop("metadata", None)
    
    # Pagination
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paginated_tasks = tasks[start_idx:end_idx]
    
    return {
        "data": {
            "tasks": paginated_tasks,
            "total": len(tasks),
            "page": page,
            "limit": limit
        }
    }

@router.get("/{taskId}/actions")
async def get_task_actions(
    taskId: str = Path(..., description="The unique identifier of the task"),
    page: int = Query(1, description="Page number"),
    limit: int = Query(20, description="Items per page"),
    type: Optional[str] = Query(None, description="Filter by action type"),
    sortBy: Optional[str] = Query("timestamp", description="Sort field"),
    sortOrder: Optional[str] = Query("asc", description="Sort order")
):
    """Get actions for a specific task with pagination."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId]
    actions = task.get("actions", [])
    
    # Apply type filter
    if type:
        actions = [a for a in actions if a["type"] == type]
    
    # Pagination
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paginated_actions = actions[start_idx:end_idx]
    
    return {
        "data": {
            "actions": paginated_actions,
            "total": len(actions),
            "page": page,
            "limit": limit
        }
    }

@router.get("/{taskId}/screenshots")
async def get_task_screenshots(taskId: str = Path(..., description="The unique identifier of the task")):
    """Get screenshots for a specific task."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId]
    screenshots = task.get("screenshots", [])
    
    screenshot_data = [
        {
            "id": f"screenshot-{i+1}",
            "url": f"/screenshots/{taskId}/screenshot-{i+1}.png",
            "timestamp": "2024-01-15T10:30:00Z",
            "actionId": f"action-{i+1}",
            "description": f"Screenshot {i+1}"
        }
        for i, screenshot in enumerate(screenshots)
    ]
    
    return {"data": {"screenshots": screenshot_data}}

@router.get("/{taskId}/logs")
async def get_task_logs(
    taskId: str = Path(..., description="The unique identifier of the task"),
    level: Optional[str] = Query(None, description="Log level"),
    limit: int = Query(100, description="Number of logs to return"),
    offset: int = Query(0, description="Number of logs to skip")
):
    """Get logs for a specific task."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId]
    logs = task.get("logs", [])
    
    log_data = [
        {
            "timestamp": "2024-01-15T10:30:00Z",
            "level": "info",
            "message": log,
            "metadata": {"taskId": taskId}
        }
        for log in logs
    ]
    
    # Apply level filter
    if level:
        log_data = [l for l in log_data if l["level"] == level]
    
    # Apply pagination
    paginated_logs = log_data[offset:offset + limit]
    
    return {
        "data": {
            "logs": paginated_logs,
            "total": len(log_data)
        }
    }

@router.get("/{taskId}/metrics")
async def get_task_metrics(taskId: str = Path(..., description="The unique identifier of the task")):
    """Get performance metrics for a specific task."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    task = MOCK_TASKS[taskId]
    
    metrics = {
        "duration": task["duration"],
        "actionsPerSecond": 0.33,
        "averageActionDuration": 3.0,
        "totalWaitTime": 10.5,
        "totalNavigationTime": 5.2,
        "memoryUsage": [
            {
                "timestamp": "2024-01-15T10:30:00Z",
                "value": 256
            }
        ],
        "cpuUsage": [
            {
                "timestamp": "2024-01-15T10:30:00Z",
                "value": 15.5
            }
        ]
    }
    
    return {"data": {"metrics": metrics}}

@router.get("/{taskId}/timeline")
async def get_task_timeline(taskId: str = Path(..., description="The unique identifier of the task")):
    """Get timeline of events for a specific task."""
    if taskId not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
    timeline = [
        {
            "timestamp": "2024-01-15T10:30:00Z",
            "action": "NavigateAction",
            "duration": 2.1,
            "success": True,
            "metadata": {
                "url": "http://localhost:8000/",
                "statusCode": 200
            }
        },
        {
            "timestamp": "2024-01-15T10:30:05Z",
            "action": "WaitAction",
            "duration": 5.0,
            "success": True,
            "metadata": {
                "waitTime": 5.0
            }
        }
    ]
    
    return {"data": {"timeline": timeline}}

@router.post("/compare")
async def compare_tasks(request: CompareTasksRequest):
    """Compare multiple tasks."""
    tasks = []
    for taskId in request.taskIds:
        if taskId in MOCK_TASKS:
            tasks.append(MOCK_TASKS[taskId])
        else:
            raise HTTPException(status_code=404, detail=f"Task with ID '{taskId}' not found")
    
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
    
    return {"data": {"tasks": tasks, "comparison": comparison}}
