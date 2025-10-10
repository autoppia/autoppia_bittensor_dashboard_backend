# Models package

# Core models - essential business logic
from .schemas import (
    # Core entities
    ValidatorInfo,
    MinerInfo,
    Task,
    Round,
    AgentEvaluationRun,
    
    # Utilities
    now_ts,
    
    # Validator classes
    Action,
    TaskSolution,
    BaseTaskTest,
    CheckUrlTest,
    FindInHtmlTest,
    CheckEventTest,
    JudgeBaseOnHTML,
    JudgeBaseOnScreenshot,
    TestUnion,
    
    # Evaluation classes
    TestResult,
    Feedback,
    EvaluationStats,
    EvaluationResult,
)

# UI models - dashboard and presentation
from .ui import (
    # Overview dashboard
    ChartDataPoint,
    ValidatorCard,
    LiveEvent,
    OverviewMetrics,
    OverviewResponse,
    
    # Leaderboard
    LeaderboardQuery,
    RoundSummary,
    MinerPerformance,
    MinerLeaderboardEntry,
    ValidatorLeaderboardEntry,
    RoundLeaderboardEntry,
    LeaderboardData,
    LeaderboardResponse,
    
    # Agents
    AgentInfo,
    AgentsListData,
    AgentsListResponse,
    MinerDetails,
    MinerValidatorCard,
    MinerDetailsData,
    MinerDetailsResponse,
    
    # Agent runs
    AgentRunInfo,
    UIValidatorInfo,
    UIMinerInfo,
    OverallMetrics,
    WebsiteScore,
    TaskSummary,
    TaskPagination,
    UITaskExecution,
    TasksData,
    AgentRunDetailsData,
    AgentRunDetailsResponse,
    
    # Tasks
    TaskInfo,
    RoundInfo,
    TaskAction,
    GeneratedGif,
    TaskDetailsData,
    TaskDetailsResponse,
    
    # Analytics
    ScoreDistributionPoint,
    PerformanceAnalytics,
    ParticipationPoint,
    ParticipationAnalytics,
    TrendsAnalytics,
    AnalyticsData,
    AnalyticsResponse,
)

# Legacy imports for backward compatibility
from .schemas import *
