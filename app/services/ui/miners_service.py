from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import math
import random
from app.utils.score_formatter import format_score_as_percentage_float
from app.models.ui.miners import (
    Miner, MinerRun, MinerActivity,
    MinerListQuery, MinerPerformanceQuery, MinerRunsQuery,
    MinerStatus, RunStatus, TaskStatus, ActivityType,
    TimeRange, Granularity, PerformanceTrend
)


class MinersService:
    """Service class for managing miner-related operations."""
    
    def __init__(self):
        """Initialize the miners service."""
        self._mock_miners = self._generate_mock_miners()
        self._mock_runs = self._generate_mock_runs()
        self._mock_activities = self._generate_mock_activities()
    
    def get_miners(self, query: MinerListQuery) -> Tuple[List[Miner], int]:
        """Get paginated list of miners with filtering and sorting."""
        miners = self._mock_miners.copy()
        
        # Apply filters
        if query.isSota is not None:
            miners = [m for m in miners if m.isSota == query.isSota]
        
        if query.status:
            miners = [m for m in miners if m.status == query.status]
        
        if query.search:
            search_lower = query.search.lower()
            miners = [m for m in miners if 
                     search_lower in m.name.lower() or 
                     search_lower in str(m.uid) or
                     search_lower in m.hotkey.lower()]
        
        # Apply sorting
        reverse = query.sortOrder == "desc"
        if query.sortBy == "name":
            miners.sort(key=lambda x: x.name, reverse=reverse)
        elif query.sortBy == "uid":
            miners.sort(key=lambda x: x.uid, reverse=reverse)
        elif query.sortBy == "averageScore":
            miners.sort(key=lambda x: x.averageScore, reverse=reverse)
        elif query.sortBy == "successRate":
            miners.sort(key=lambda x: x.successRate, reverse=reverse)
        elif query.sortBy == "totalRuns":
            miners.sort(key=lambda x: x.totalRuns, reverse=reverse)
        elif query.sortBy == "lastSeen":
            miners.sort(key=lambda x: x.lastSeen, reverse=reverse)
        
        # Apply pagination
        total = len(miners)
        start = (query.page - 1) * query.limit
        end = start + query.limit
        miners = miners[start:end]
        
        return miners, total
    
    def get_miner_by_uid(self, uid: int) -> Optional[Miner]:
        """Get miner by UID."""
        return next((m for m in self._mock_miners if m.uid == uid), None)
    
    def get_miner_performance(self, uid: int, query: MinerPerformanceQuery) -> List[PerformanceTrend]:
        """Get miner performance trends."""
        miner = self.get_miner_by_uid(uid)
        if not miner:
            return []
        
        # Calculate time range
        end_date = datetime.now()
        if query.endDate:
            end_date = query.endDate
        elif query.timeRange == TimeRange.SEVEN_DAYS:
            start_date = end_date - timedelta(days=7)
        elif query.timeRange == TimeRange.THIRTY_DAYS:
            start_date = end_date - timedelta(days=30)
        elif query.timeRange == TimeRange.NINETY_DAYS:
            start_date = end_date - timedelta(days=90)
        else:  # Default to 7 days
            start_date = end_date - timedelta(days=7)
        
        if query.startDate:
            start_date = query.startDate
        
        # Filter runs for this miner in the time range
        miner_runs = [r for r in self._mock_runs 
                     if r.agentId == str(uid)]
        
        if not miner_runs:
            return []
        
        # Generate performance trend
        performance_trend = self._generate_performance_trend(
            miner_runs, start_date, end_date, query.granularity
        )
        
        return performance_trend
    
    def get_miner_runs(self, uid: int, query: MinerRunsQuery) -> Tuple[List[MinerRun], int]:
        """Get paginated list of miner runs."""
        runs = [r for r in self._mock_runs if r.agentId == str(uid)]
        
        # Apply filters
        if query.roundId:
            runs = [r for r in runs if r.roundId == query.roundId]
        
        if query.validatorId:
            runs = [r for r in runs if r.validatorId == query.validatorId]
        
        if query.status:
            runs = [r for r in runs if r.status == query.status]
        
        if query.startDate:
            runs = [r for r in runs if datetime.fromisoformat(r.startTime.replace('Z', '+00:00')) >= query.startDate]
        
        if query.endDate:
            runs = [r for r in runs if datetime.fromisoformat(r.startTime.replace('Z', '+00:00')) <= query.endDate]
        
        # Apply sorting
        reverse = query.sortOrder == "desc"
        if query.sortBy == "startTime":
            runs.sort(key=lambda x: x.startTime, reverse=reverse)
        elif query.sortBy == "score":
            runs.sort(key=lambda x: x.score, reverse=reverse)
        elif query.sortBy == "duration":
            runs.sort(key=lambda x: x.duration, reverse=reverse)
        elif query.sortBy == "ranking":
            runs.sort(key=lambda x: x.ranking or 0, reverse=reverse)
        
        # Apply pagination
        total = len(runs)
        start = (query.page - 1) * query.limit
        end = start + query.limit
        runs = runs[start:end]
        
        return runs, total
    
    def get_miner_run_by_id(self, uid: int, run_id: str) -> Optional[MinerRun]:
        """Get miner run by ID."""
        return next((r for r in self._mock_runs 
                   if r.agentId == str(uid) and r.runId == run_id), None)
    
    
    def _generate_mock_miners(self) -> List[Miner]:
        """Generate mock miner data."""
        miners = [
            Miner(
                id="123",
                uid=123,
                name="Autoppia Bittensor",
                hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                imageUrl="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iOCIgZmlsbD0iIzRGNjBFNSIvPgo8dGV4dCB4PSIzMiIgeT0iMzgiIGZvbnQtZmFtaWx5PSJBcmlhbCwgc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IndoaXRlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5BSSB8PC90ZXh0Pgo8L3N2Zz4K",
                githubUrl="https://github.com/autoppia/bittensor-agent",
                taostatsUrl="https://taostats.io/miner/123",
                isSota=False,
                status=MinerStatus.ACTIVE,
                description="Autoppia's native Bittensor agent for web automation tasks",
                totalRuns=1247,
                successfulRuns=1089,
                averageScore=87.0,
                bestScore=95.0,
                successRate=87.3,
                averageDuration=32.5,
                totalTasks=6235,
                completedTasks=5445,
                lastSeen=(datetime.now() - timedelta(minutes=5)).isoformat(),
                createdAt=datetime(2023, 6, 1, 0, 0, 0).isoformat(),
                updatedAt=datetime.now().isoformat()
            ),
            Miner(
                id="456",
                uid=456,
                name="OpenAI CUA",
                hotkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                imageUrl="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iOCIgZmlsbD0iIzRGNjBFNSIvPgo8dGV4dCB4PSIzMiIgeT0iMzgiIGZvbnQtZmFtaWx5PSJBcmlhbCwgc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IndoaXRlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5BSSB8PC90ZXh0Pgo8L3N2Zz4K",
                githubUrl="https://github.com/openai/computer-use-agent",
                taostatsUrl="https://taostats.io/miner/456",
                isSota=True,
                status=MinerStatus.ACTIVE,
                description="OpenAI's Computer Use Agent for web automation",
                totalRuns=892,
                successfulRuns=756,
                averageScore=82.0,
                bestScore=91.0,
                successRate=84.8,
                averageDuration=28.3,
                totalTasks=4460,
                completedTasks=3780,
                lastSeen=(datetime.now() - timedelta(minutes=12)).isoformat(),
                createdAt=datetime(2024, 1, 10, 8, 0, 0).isoformat(),
                updatedAt=datetime.now().isoformat()
            ),
            Miner(
                id="789",
                uid=789,
                name="Anthropic CUA",
                hotkey="5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy",
                imageUrl="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iOCIgZmlsbD0iIzRGNjBFNSIvPgo8dGV4dCB4PSIzMiIgeT0iMzgiIGZvbnQtZmFtaWx5PSJBcmlhbCwgc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IndoaXRlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5BSSB8PC90ZXh0Pgo8L3N2Zz4K",
                githubUrl="https://github.com/anthropics/computer-use-agent",
                taostatsUrl="https://taostats.io/miner/789",
                isSota=True,
                status=MinerStatus.ACTIVE,
                description="Anthropic's Computer Use Agent",
                totalRuns=654,
                successfulRuns=567,
                averageScore=79.0,
                bestScore=88.0,
                successRate=86.7,
                averageDuration=35.2,
                totalTasks=3270,
                completedTasks=2835,
                lastSeen=(datetime.now() - timedelta(minutes=8)).isoformat(),
                createdAt=datetime(2024, 1, 12, 14, 0, 0).isoformat(),
                updatedAt=datetime.now().isoformat()
            ),
            Miner(
                id="101",
                uid=101,
                name="Browser Use Agent",
                hotkey="5HGjWAeFDfFCWPsjFQdVV2Msvz2XtMktvgocEcsVqKvQYqQ",
                imageUrl="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iOCIgZmlsbD0iIzRGNjBFNSIvPgo8dGV4dCB4PSIzMiIgeT0iMzgiIGZvbnQtZmFtaWx5PSJBcmlhbCwgc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IndoaXRlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5BSSB8PC90ZXh0Pgo8L3N2Zz4K",
                githubUrl="https://github.com/browser-use/browser-use",
                taostatsUrl="https://taostats.io/miner/101",
                isSota=True,
                status=MinerStatus.ACTIVE,
                description="Browser Use framework agent for web automation",
                totalRuns=423,
                successfulRuns=345,
                averageScore=74.0,
                bestScore=85.0,
                successRate=81.6,
                averageDuration=42.1,
                totalTasks=2115,
                completedTasks=1725,
                lastSeen=(datetime.now() - timedelta(minutes=15)).isoformat(),
                createdAt=datetime(2024, 1, 8, 16, 0, 0).isoformat(),
                updatedAt=datetime.now().isoformat()
            ),
            Miner(
                id="202",
                uid=202,
                name="Custom Agent Alpha",
                hotkey="5CiPPseXPECbkjWCa6MnjNokrgYjMqmKndv2rSnekmSK2Dj",
                imageUrl="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iOCIgZmlsbD0iIzRGNjBFNSIvPgo8dGV4dCB4PSIzMiIgeT0iMzgiIGZvbnQtZmFtaWx5PSJBcmlhbCwgc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IndoaXRlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5BSSB8PC90ZXh0Pgo8L3N2Zz4K",
                githubUrl="https://github.com/custom/alpha-agent",
                taostatsUrl="https://taostats.io/miner/202",
                isSota=False,
                status=MinerStatus.MAINTENANCE,
                description="Custom implementation for specialized tasks",
                totalRuns=234,
                successfulRuns=198,
                averageScore=71.0,
                bestScore=82.0,
                successRate=84.6,
                averageDuration=38.7,
                totalTasks=1170,
                completedTasks=990,
                lastSeen=(datetime.now() - timedelta(hours=2)).isoformat(),
                createdAt=datetime(2024, 1, 5, 12, 0, 0).isoformat(),
                updatedAt=datetime.now().isoformat()
            )
        ]
        
        # Add additional SOTA company agents with unique UIDs
        additional_sota_agents = [
            {
                "id": "500",
                "uid": 500,
                "name": "GPT-4 Vision Agent",
                "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQ500",
                "description": "OpenAI's GPT-4 Vision model for visual web automation",
                "githubUrl": "https://github.com/openai/gpt-4-vision-agent",
                "isSota": True,
                "totalRuns": 234,
                "successfulRuns": 198,
                "averageScore": 71.0,
                "bestScore": 82.0,
                "successRate": 84.6,
                "averageDuration": 38.7,
                "totalTasks": 1170,
                "completedTasks": 990,
                "lastSeen": (datetime.now() - timedelta(hours=2)).isoformat(),
                "createdAt": datetime(2024, 1, 5, 12, 0, 0).isoformat(),
            }
        ]
        
        # Add the additional SOTA agents
        for agent_data in additional_sota_agents:
            miners.append(Miner(
                id=agent_data["id"],
                uid=agent_data["uid"],
                name=agent_data["name"],
                hotkey=agent_data["hotkey"],
                imageUrl="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iOCIgZmlsbD0iIzRGNjBFNSIvPgo8dGV4dCB4PSIzMiIgeT0iMzgiIGZvbnQtZmFtaWx5PSJBcmlhbCwgc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IndoaXRlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5BSSB8PC90ZXh0Pgo8L3N2Zz4K",
                githubUrl=agent_data["githubUrl"],
                taostatsUrl=f"https://taostats.io/miner/{agent_data['uid']}",
                isSota=agent_data["isSota"],
                status=MinerStatus.ACTIVE,
                description=agent_data["description"],
                totalRuns=agent_data["totalRuns"],
                successfulRuns=agent_data["successfulRuns"],
                averageScore=agent_data["averageScore"],
                bestScore=agent_data["bestScore"],
                successRate=agent_data["successRate"],
                averageDuration=agent_data["averageDuration"],
                totalTasks=agent_data["totalTasks"],
                completedTasks=agent_data["completedTasks"],
                lastSeen=agent_data["lastSeen"],
                createdAt=agent_data["createdAt"],
                updatedAt=datetime.now().isoformat()
            ))
        
        # Generate regular miners (not SOTA) to reach 50 total
        for i in range(8, 52):
            uid = 300 + i
            miners.append(Miner(
                id=str(uid),
                uid=uid,
                name=f"Miner {i}",
                hotkey=f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQ{i:02d}",
                imageUrl="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iOCIgZmlsbD0iIzRGNjBFNSIvPgo8dGV4dCB4PSIzMiIgeT0iMzgiIGZvbnQtZmFtaWx5PSJBcmlhbCwgc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IndoaXRlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5BSSB8PC90ZXh0Pgo8L3N2Zz4K",
                githubUrl=f"https://github.com/miner{i}/agent",
                taostatsUrl=f"https://taostats.io/miner/{uid}",
                isSota=False,  # These are regular miners, not SOTA agents
                status=random.choice([MinerStatus.ACTIVE, MinerStatus.ACTIVE, MinerStatus.ACTIVE, MinerStatus.INACTIVE]),
                description=f"Custom miner implementation {i}",
                totalRuns=random.randint(50, 1000),
                successfulRuns=random.randint(40, 900),
                averageScore=format_score_as_percentage_float(random.uniform(0.5, 0.95)),
                bestScore=format_score_as_percentage_float(random.uniform(0.8, 1.0)),
                successRate=round(random.uniform(70, 95), 1),
                averageDuration=round(random.uniform(20, 60), 1),
                totalTasks=random.randint(250, 5000),
                completedTasks=random.randint(200, 4500),
                lastSeen=(datetime.now() - timedelta(minutes=random.randint(1, 1440))).isoformat(),
                createdAt=(datetime(2024, 1, 1) + timedelta(days=random.randint(0, 30))).isoformat(),
                updatedAt=datetime.now().isoformat()
            ))
        
        return miners
    
    def _generate_mock_runs(self) -> List[MinerRun]:
        """Generate mock miner run data."""
        runs = []
        miner_uids = [m.uid for m in self._mock_miners]
        websites = ["Autozone", "Amazon", "Google", "GitHub", "Stack Overflow", "Reddit", "Wikipedia", "YouTube"]
        use_cases = ["Login", "Search", "Purchase", "Navigation", "Form Fill", "Data Extraction", "API Call", "File Upload"]
        
        for i in range(2000):
            uid = random.choice(miner_uids)
            start_time = datetime.now() - timedelta(days=random.randint(0, 30))
            duration = random.randint(300, 3600)  # 5 minutes to 1 hour
            end_time = start_time + timedelta(seconds=duration)
            
            # Generate tasks
            num_tasks = random.randint(5, 15)
            tasks = []
            completed_tasks = 0
            
            for j in range(num_tasks):
                task_duration = random.randint(30, 300)
                task_start = start_time + timedelta(seconds=j * 60)
                task_end = task_start + timedelta(seconds=task_duration)
                
                task_status = random.choice([TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.FAILED])
                if task_status == TaskStatus.COMPLETED:
                    completed_tasks += 1
                
                from app.models.ui.miners import Task
                task = Task(
                    taskId=f"task_{i}_{j}",
                    website=random.choice(websites),
                    useCase=random.choice(use_cases),
                    status=task_status,
                    score=format_score_as_percentage_float(random.uniform(0.6, 1.0)) if task_status == TaskStatus.COMPLETED else 0.0,
                    duration=task_duration,
                    startTime=task_start,
                    endTime=task_end if task_status == TaskStatus.COMPLETED else None,
                    error=f"Task failed: {random.choice(['Timeout', 'Network error', 'Element not found'])}" if task_status == TaskStatus.FAILED else None
                )
                tasks.append(task)
            
            # Calculate run score and status
            if completed_tasks == num_tasks:
                status = RunStatus.COMPLETED
                score = format_score_as_percentage_float(random.uniform(0.8, 1.0))
            elif completed_tasks > num_tasks // 2:
                status = RunStatus.COMPLETED
                score = format_score_as_percentage_float(random.uniform(0.6, 0.8))
            else:
                status = random.choice([RunStatus.FAILED, RunStatus.TIMEOUT])
                score = format_score_as_percentage_float(random.uniform(0.0, 0.5))
            
            run = MinerRun(
                runId=f"run_id_{uid}_{int(start_time.timestamp())}_{i}",
                agentId=str(uid),
                validatorId=f"validator_{random.choice(['autoppia', 'kraken', 'roundtable21', 'yuma', 'tao5'])}",
                roundId=random.randint(1, 25),
                score=score,
                ranking=random.randint(1, 50) if status == RunStatus.COMPLETED else 0,
                status=status,
                duration=duration,
                completedTasks=completed_tasks,
                totalTasks=num_tasks,
                startTime=start_time.isoformat(),
                endTime=end_time.isoformat() if status == RunStatus.COMPLETED else None,
                createdAt=start_time.isoformat()
            )
            runs.append(run)
        
        return runs
    
    def _generate_mock_activities(self) -> List[MinerActivity]:
        """Generate mock miner activity data."""
        activities = []
        miner_uids = [m.uid for m in self._mock_miners]
        activity_types = [ActivityType.RUN_STARTED, ActivityType.RUN_COMPLETED, ActivityType.RUN_FAILED]
        
        for i in range(1000):
            uid = random.choice(miner_uids)
            miner = next((m for m in self._mock_miners if m.uid == uid), None)
            miner_name = miner.name if miner else f"Miner {uid}"
            activity_type = random.choice(activity_types)
            timestamp = datetime.now() - timedelta(hours=random.randint(0, 72))
            
            if activity_type == ActivityType.RUN_STARTED:
                message = f"Miner {miner_name} started a new run"
                metadata = {
                    "runId": f"run_miner_{uid}_{i}",
                    "roundId": random.randint(1, 25),
                    "validatorId": random.choice(["autoppia", "kraken", "roundtable21"])
                }
            elif activity_type == ActivityType.RUN_COMPLETED:
                score = random.uniform(0.6, 1.0)
                duration = random.randint(300, 3600)
                message = f"Miner {miner_name} completed run with score {score:.2f}"
                metadata = {
                    "runId": f"run_miner_{uid}_{i}",
                    "roundId": random.randint(1, 25),
                    "validatorId": random.choice(["autoppia", "kraken", "roundtable21"]),
                    "score": score,
                    "duration": duration
                }
            else:  # RUN_FAILED
                message = f"Miner {miner_name} run failed"
                metadata = {
                    "runId": f"run_miner_{uid}_{i}",
                    "roundId": random.randint(1, 25),
                    "validatorId": random.choice(["autoppia", "kraken", "roundtable21"]),
                    "error": random.choice(["Timeout", "Network error", "Validation failed"])
                }
            
            activity = MinerActivity(
                id=f"activity_miner_{uid}_{i}",
                type=activity_type,
                uid=uid,
                minerName=miner_name,
                message=message,
                timestamp=timestamp,
                metadata=metadata
            )
            activities.append(activity)
        
        return activities
    
    def _generate_performance_trend(self, runs: List[MinerRun], start_date: datetime, 
                                  end_date: datetime, granularity: Granularity) -> List[PerformanceTrend]:
        """Generate performance trend data."""
        trend = []
        
        if granularity == Granularity.DAY:
            current = start_date
            while current <= end_date:
                next_day = current + timedelta(days=1)
                period_runs = [r for r in runs if current <= datetime.fromisoformat(r.startTime.replace('Z', '+00:00')) < next_day]
                
                if period_runs:
                    scores = [r.score for r in period_runs if r.score > 0]
                    successful = len([r for r in period_runs if r.status == RunStatus.COMPLETED])
                    durations = [r.duration for r in period_runs if r.duration > 0]
                    
                    trend.append(PerformanceTrend(
                        period=current.strftime("%Y-%m-%d"),
                        score=sum(scores) / len(scores) if scores else 0.0,
                        successRate=(successful / len(period_runs) * 100) if period_runs else 0.0,
                        duration=sum(durations) / len(durations) if durations else 0.0
                    ))
                
                current = next_day
        
        return trend
