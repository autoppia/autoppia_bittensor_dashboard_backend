# Backend Integration Guide

This document captures the data the frontend expects from the backend for the dashboard sections **Overview**, **Rounds**, **Agents**, **Agent Runs Search**, **Agent Run Detail**, **Tasks Search**, and **Task Detail**. The application uses `NEXT_PUBLIC_API_BASE_URL` (falling back to `NEXT_PUBLIC_API_URL` or `http://localhost:8080`) as the base URL. All requests expect JSON responses with `Content-Type: application/json`. Where responses include a top-level `{ success: boolean }`, `success: false` should be paired with `message`/`error`/`code`.

---

## Overview

Frontend entry point: `apps/isomorphic/src/app/overview/*`, fetching through `useOverviewData` (`apps/isomorphic/src/services/hooks/useOverview.ts`), which aggregates the calls below.

- `GET /api/v1/overview/metrics`
  - Purpose: populate KPI cards (top score, totals, current round, subnet version).
  - Response: either `{ data: { metrics: OverviewMetrics } }` or `{ metrics: OverviewMetrics }`.
  - `OverviewMetrics`: `{ topScore: number; totalWebsites: number; totalValidators: number; totalMiners: number; currentRound: number; subnetVersion: string; lastUpdated: string }`.

- `GET /api/v1/overview/validators`
  - Purpose: fill the “Top Validators” table.
  - Query params (`ValidatorsQueryParams`): `page`, `limit`, `status`, `sortBy` (`weight|trust|totalTasks|name`), `sortOrder` (`asc|desc`).
  - Response: `{ data: { validators: ValidatorData[]; total: number; page: number; limit: number } }`.
  - `ValidatorData`: `{ id: string; name: string; hotkey: string; icon: string; currentTask: string; status: 'Sending Tasks'|'Evaluating'|'Waiting'|'Offline'; totalTasks: number; weight: number; trust: number; version: number; lastSeen: string; uptime: number; stake: number; emission: number }`.

- `GET /api/v1/overview/validators/{validatorId}`
  - Purpose: drill-down modal when a validator is clicked.
  - Response: `{ validator: ValidatorData }` or `{ data: { validator: ValidatorData } }`.

- `GET /api/v1/overview/validators/filter`
  - Purpose: supply validator dropdowns (IDs + names only).
  - Response: `{ data: { validators: { id: string; name: string; hotkey?: string | null; icon?: string | null; status?: string | null }[] } }`. Return `404` to indicate “not ready” if necessary; UI will treat it as empty.

- `GET /api/v1/overview/rounds/current`
  - Purpose: show the currently running round.
  - Response: `{ data: { round: OverviewRoundData } }` or `{ round: OverviewRoundData }`.

- `GET /api/v1/overview/rounds`
  - Purpose: feed the rounds timeline widget.
  - Query params (`RoundsQueryParams`): `page`, `limit`, `status` (`active|completed|pending`), `includeCurrent`.
  - Response: `{ rounds: OverviewRoundData[]; currentRound: OverviewRoundData; total: number }` or wrapped in `data`.
  - `OverviewRoundData`: `{ id: number; startBlock: number; endBlock: number; current: boolean; startTime: string; endTime?: string; status: 'active'|'completed'|'pending'; totalTasks: number; completedTasks: number; averageScore: number; topScore: number }`.

- `GET /api/v1/overview/rounds/{roundId}`
  - Purpose: fetch details for a specific round within the overview context (fallbacks expect `data.round` or `round`).

- `GET /api/v1/overview/leaderboard`
  - Purpose: render comparative charts (Subnet 36 vs benchmarks).
  - Query params (`LeaderboardQueryParams`): `timeRange` (`7D|15D|30D|all`), `limit`, `offset`.
  - Response: `{ data: { leaderboard: LeaderboardData[]; total: number; timeRange: { start: string; end: string } } }`.
  - `LeaderboardData`: `{ round: number; subnet36: number; openai_cua: number; anthropic_cua: number; browser_use: number; timestamp: string }`.

- `GET /api/v1/overview/statistics`
  - Purpose: network-wide summary (stake, emission, uptime, etc.).
  - Response: `{ statistics: SubnetStatistics }` or `{ data: { statistics: SubnetStatistics } }`.
  - `SubnetStatistics`: `{ totalStake: number; totalEmission: number; averageTrust: number; networkUptime: number; activeValidators: number; registeredMiners: number; totalTasksCompleted: number; averageTaskScore: number; lastUpdated: string }`.

- `GET /api/v1/overview/network-status`
  - Purpose: status pill for network health.
  - Response: `{ data?: { status: 'healthy'|'degraded'|'down'; message: string; lastChecked: string; activeValidators: number; networkLatency: number } }` (client also accepts flat payload).

- `GET /api/v1/overview/recent-activity`
  - Purpose: “Recent Activity” feed (default `limit=10`).
  - Query params: `limit`.
  - Response: `{ activities: { id: string; type: 'task_completed'|'validator_joined'|'round_started'|'round_ended'|'miner_registered'; message: string; timestamp: string; metadata?: Record<string, any> }[]; total: number }`.

- `GET /api/v1/overview/performance-trends`
  - Purpose: render historical performance charts (default `days=7`).
  - Query params: `days`.
  - Response: `{ trends: { date: string; averageScore: number; totalTasks: number; activeValidators: number; networkUptime: number }[]; period: string }`.

---

## Rounds

Frontend entry point: `apps/isomorphic/src/app/rounds/*`, data via `roundsService` (`apps/isomorphic/src/services/api/rounds.service.ts`).

- `GET /api/v1/rounds`
  - Query params (`RoundsListQueryParams`): `page`, `limit`, `status`, `sortBy` (`id|startTime|endTime|totalTasks|averageScore`), `sortOrder` (`asc|desc`).
  - Response: `{ data: { rounds: RoundData[]; total: number; page: number; limit: number } }`. Service normalizes snake_case and deduplicates by `id`.
  - `RoundData`: `{ id: number; roundKey: string; startBlock: number; endBlock: number; current: boolean; startTime: string; endTime?: string; status: 'active'|'completed'|'pending'; totalTasks: number; completedTasks: number; averageScore: number; topScore: number; currentBlock?: number; blocksRemaining?: number; progress?: number; validatorUid?: number; validatorHotkey?: string; validatorName?: string }`.

- `GET /api/v1/rounds/current`
  - Response: same structure as `RoundData` (wrapped in `data.round` acceptable).

- `GET /api/v1/rounds/{roundId}`
  - Response candidates inspected in order: `{ data: { round } }`, `{ round }`, `{ data: { rounds: [...] } }`, `{ rounds: [...] }`, or flat.

- `GET /api/v1/rounds/{roundId}/statistics`
  - Response: `{ data: { statistics: RoundStatistics } }`.
  - `RoundStatistics`: `{ roundId: number; totalMiners: number; activeMiners: number; totalTasks: number; completedTasks: number; averageScore: number; topScore: number; successRate: number; averageDuration: number; totalStake: number; totalEmission: number; lastUpdated: string }`.

- `GET /api/v1/rounds/{roundId}/miners`
  - Query params (`RoundMinersQueryParams`): `page`, `limit`, `sortBy` (`score|duration|ranking|uid`), `sortOrder`, `success`, `minScore`, `maxScore`.
  - Response: `{ data: { miners: MinerPerformance[]; benchmarks?: BenchmarkPerformance[]; total: number; page: number; limit: number } }`.
  - `MinerPerformance`: `{ uid: number; name?: string; hotkey?: string | null; success: boolean; score: number; duration: number; ranking: number; tasksCompleted: number; tasksTotal: number; stake: number; emission: number; lastSeen: string; validatorId?: string; isSota?: boolean; provider?: string; imageUrl?: string }`.

- `GET /api/v1/rounds/{roundId}/validators`
  - Response: `{ data: { validators: ValidatorPerformance[]; total: number } }`.
  - `ValidatorPerformance`: `{ id: string; name: string; hotkey: string; icon: string; status: 'active'|'inactive'|'offline'; totalTasks: number; completedTasks: number; averageScore: number; weight: number; trust: number; version: number; stake: number; emission: number; lastSeen: string; uptime: number }`.

- `GET /api/v1/rounds/{roundId}/activity`
  - Query params (`RoundActivityQueryParams`): `limit`, `offset`, `type`, `since`.
  - Response: `{ data: { activities: RoundActivity[]; total: number } }`.
  - `RoundActivity`: `{ id: string; type: 'task_completed'|'miner_joined'|'miner_left'|'validator_joined'|'validator_left'|'round_started'|'round_ended'; message: string; timestamp: string; metadata: { minerUid?: number; validatorId?: string; taskId?: string; score?: number; duration?: number } }`.

- `GET /api/v1/rounds/{roundId}/progress`
  - Response: `{ data: { progress: RoundProgress } }`.
  - `RoundProgress`: `{ roundId: number; currentBlock: number; startBlock: number; endBlock: number; blocksRemaining: number; progress: number; estimatedTimeRemaining: { days: number; hours: number; minutes: number; seconds: number }; lastUpdated: string }`.

- `GET /api/v1/rounds/{roundId}/miners/top`
  - Query params: `limit` (defaults to 10). Response `{ data: { miners: MinerPerformance[]; ... } }`; frontend expects array in `data.miners`.

- `GET /api/v1/rounds/{roundId}/miners/{uid}`
  - Response: `{ data: { miner: MinerPerformance } }`.

- `GET /api/v1/rounds/{roundId}/validators/{validatorId}`
  - Response: `{ data: { validator: ValidatorPerformance } }`.

- `POST /api/v1/rounds/compare`
  - Body: `{ roundIds: number[] }`.
  - Response: `{ data: { rounds: { roundId: number; statistics: RoundStatistics; topMiners: MinerPerformance[] }[] } }`.

- `GET /api/v1/rounds/{roundId}/timeline`
  - Response: `{ data: { timeline: { timestamp: string; block: number; completedTasks: number; averageScore: number; activeMiners: number }[] } }`.

- `GET /api/v1/rounds/{roundId}/summary`
  - Response: `{ data: { roundId: number; status: string; progress: number; totalMiners: number; averageScore: number; topScore: number; timeRemaining?: string } }`.

---

## Agents

Frontend entry point: `apps/isomorphic/src/app/agents/*`, data via `agentsService` (`apps/isomorphic/src/services/api/agents.service.ts`) and `useAgents` hooks.

- `GET /api/v1/miner-list`
  - Purpose: lightweight sidebar list.
  - Query params (`MinimalAgentsListQueryParams`): `page`, `limit`, `isSota`, `search`.
  - Response: `{ miners: MinimalAgentData[]; total: number; page: number; limit: number }`.
  - `MinimalAgentData`: `{ uid: number; name: string; ranking: number; score: number; isSota: boolean; imageUrl: string; slug?: string; provider?: string }`.

- `GET /api/v1/miners/{uid}/performance`
  - Purpose: optimized endpoint for miner history charts.
  - Query params: `timeRange` (`7d|30d|90d`), `granularity` (`hour|day`).
  - Expected response: `{ success: boolean; data: { performanceTrend: { period: string; score: number; successRate: number; duration: number }[] } }`. The frontend derives averages/top/worst scores from the trend data; missing or empty arrays trigger a local fallback.

- `GET /api/v1/agents`
  - Purpose: full agent list and filterable tables.
  - Query params (`AgentsListQueryParams`): `page`, `limit`, `isSota`, `status`, `sortBy` (`name|uid|currentScore|currentTopScore|currentRank|totalRuns|lastSeen`), `sortOrder`, `search`.
  - Response: `{ data: { agents: AgentData[]; total: number; page: number; limit: number } }`.
  - `AgentData`: `{ id: string; uid: number; name: string; slug?: string; hotkey?: string | null; type: string; imageUrl: string; githubUrl?: string; taostatsUrl?: string | null; provider?: string; category?: string; isSota: boolean; status: 'active'|'inactive'|'maintenance'; description?: string; version?: string; totalRuns: number; successfulRuns: number; currentScore: number; currentTopScore: number; currentRank: number; bestRankEver: number; roundsParticipated: number; alphaWonInPrizes: number; averageDuration: number; totalTasks: number; completedTasks: number; lastSeen: string; createdAt: string; updatedAt: string }`.

- `GET /api/v1/agents/{agentId}`
  - Purpose: agent detail header.
  - Response: `{ data: { agent: AgentData; scoreRoundData: ScoreRoundDataPoint[] } }`.
  - `ScoreRoundDataPoint`: `{ round_id: number; score: number; rank: number | null; reward: number; timestamp: string; topScore?: number; benchmarks?: { name: string; score: number; provider?: string }[] }`.

- `GET /api/v1/agents/{agentId}/performance`
  - Query params (`AgentPerformanceQueryParams`): `timeRange`, `startDate`, `endDate`, `granularity`.
  - Response: `{ data: { metrics: AgentPerformanceMetrics } }`.
  - `AgentPerformanceMetrics`: includes totals, success/failure counts, currentScore/currentTopScore/worstScore, `scoreDistribution`, `performanceTrend` (`{ round: number; score: number }[]`), plus `timeRange`, `averageDuration`, `taskCompletionRate`.

- `GET /api/v1/agent-runs/agents/{agentId}/runs`
  - Query params (`AgentRunsQueryParams`): `page`, `limit`, `roundId`, `validatorId`, `status`, `sortBy` (`startTime|score|duration|ranking`), `sortOrder`, `startDate`, `endDate`.
  - Response: `{ data: { runs: AgentRunData[]; total: number; page: number; limit: number } }`.
  - `AgentRunData` shape defined under “Agent Run Detail” below.

- `GET /api/v1/agent-runs/{runId}`
  - Purpose: quick link from agent detail run tables.
  - Response: `{ data: { run: AgentRunData } }`.

- `GET /api/v1/agents/{agentId}/activity`
  - Query params (`AgentActivityQueryParams`): `limit`, `offset`, `type`, `since`.
  - Response: `{ data: { activities: AgentActivity[]; total: number } }`.
  - `AgentActivity`: `{ id: string; type: 'run_started'|'run_completed'|'run_failed'|'agent_created'|'agent_updated'|'agent_deactivated'; agentId: string; agentName: string; message: string; timestamp: string; metadata: { runId?: string; roundId?: number; validatorId?: string; score?: number; duration?: number; error?: string } }`.

- `GET /api/v1/agents/activity`
  - Same payload as above but aggregated across all agents (used for dashboards).

- `POST /api/v1/agents/compare`
  - Body (`AgentComparisonQueryParams`): `{ agentIds: string[]; timeRange?: ...; startDate?: string; endDate?: string; metrics?: ('score'|'successRate'|'duration'|'runs')[] }`.
  - Response: `{ data: { comparison: AgentComparison } }`, where `AgentComparison` contains per-agent metric summary and `comparisonMetrics` strings.

- `GET /api/v1/agents/statistics`
  - Response: `{ data: { statistics: AgentStatistics } }` summarizing totals, top performers, distribution buckets, `lastUpdated`.

---

## Agent Runs Search

Frontend entry point: `apps/isomorphic/src/app/agent-run/agent-run-search.tsx`, using `useAgentRunsList` (`apps/isomorphic/src/services/hooks/useAgentRun.ts`).

- `GET /api/v1/agent-runs`
  - Query params (`AgentRunsListQueryParams`): `query`, `page`, `limit`, `roundId`, `validatorId`, `agentId`, `status` (`pending|running|completed|failed|timeout`), `sortBy` (`startTime|score|duration|ranking`), `sortOrder`, `startDate`, `endDate`.
  - Response: `{ data: { runs: AgentRunListItem[]; total: number; page: number; limit: number; facets?: { validators: { id: string; name?: string; count: number }[]; rounds: { id: number; count: number }[]; agents: { id: string; name?: string; count: number }[]; statuses: { name: string; count: number }[] } } }`.
  - `AgentRunListItem`: `{ runId: string; agentId: string; agentName?: string; roundId: number; validatorId: string; validatorName?: string; validatorImage?: string; status: 'running'|'completed'|'failed'|'timeout'; startTime: string; endTime?: string | null; totalTasks: number; completedTasks?: number; successfulTasks?: number; overallScore: number; successRate?: number | null; ranking?: number }`.
  - Notes: numeric ratios may be delivered as `0-1` fractions; service normalizes to percentages.

- `GET /api/v1/agent-runs/{runId}`
  - Purpose: direct run search by ID (manual lookup path).
  - Response: `{ data: { run: AgentRunData } }`. Used to map into a single `AgentRunListItem`.
  - Error handling: return 404 with `code: 'AGENT_RUN_NOT_FOUND'` to trigger the “not found” copy.

- `GET /api/v1/overview/validators/filter`
  - Purpose: pre-populate validator dropdown; see “Overview” section for response contract.

Pagination controls rely on `total` and `limit` from `/agent-runs`. Missing values default to client-calculated counts.

---

## Agent Run Detail

Frontend entry point: `apps/isomorphic/src/app/agent-run/[id]/*`, data via `agentRunsService` (`apps/isomorphic/src/services/api/agent-runs.service.ts`) and `useAgentRun`.

- `GET /api/v1/agent-runs/{runId}`
  - Optional query params (`AgentRunQueryParams`): `includeTasks`, `includeStats`, `includeSummary`, `includePersonas`, `website`, `useCase`, `status` (`pending|running|completed|failed`), `sortBy` (`startTime|score|duration|ranking`), `sortOrder`.
  - Response: `{ data: { run: AgentRunData } }`.
  - `AgentRunData`: `{ runId: string; agentId: string; roundId: number; validatorId: string; validatorName: string; validatorImage: string; startTime: string; endTime?: string; status: 'running'|'completed'|'failed'|'timeout'; totalTasks: number; completedTasks: number; successfulTasks: number; failedTasks: number; score: number; ranking?: number; duration: number; overallScore: number; websites: { website: string; tasks: number; successful: number; failed: number; score: number }[]; tasks: AgentRunTaskData[]; metadata: { environment: string; version: string; resources: { cpu: number; memory: number; storage: number } } }`.

- `GET /api/v1/agent-runs/{runId}/personas`
  - Response: `{ data: { personas: AgentRunPersonas } }`, where each persona set includes `round`, `validator`, and `agent` descriptors (IDs, names, imagery, metadata).

- `GET /api/v1/agent-runs/{runId}/stats`
  - Response: `{ data: { stats: AgentRunStats } }` (or `{ data: { statistics: ... } }`).
  - `AgentRunStats`: totals, success/fail counts, average durations, score distribution buckets, plus `performanceByWebsite`/`performanceByUseCase` arrays (website/useCase name, task counts, success breakdown, average score/duration).
  - Values may be raw fractions (`0-1`); client scales to percentages.

- `GET /api/v1/agent-runs/{runId}/summary`
  - Response: `{ data: { summary: AgentRunSummary } }`.
  - `AgentRunSummary`: high-level run view containing `overallScore`, counts, `duration`, optional `ranking`, `topPerformingWebsite`, `topPerformingUseCase`, and a `recentActivity` timeline.

- `GET /api/v1/agent-runs/{runId}/tasks`
  - Query params (`AgentRunTasksQueryParams`): `page`, `limit`, `website`, `useCase`, `status`, `sortBy` (`startTime|score|duration`), `sortOrder`.
  - Response: `{ data: { tasks: AgentRunTaskData[]; total: number; page?: number; limit?: number } }`.
  - `AgentRunTaskData`: `{ taskId: string; website: string; useCase: string; prompt: string; status: 'pending'|'running'|'completed'|'failed'; score: number; duration: number; startTime: string; endTime?: string; error?: string; actions: TaskAction[]; screenshots?: string[]; logs?: string[]; extras?: Record<string, any> }`.

- `POST /api/v1/agent-runs/compare`
  - Body: `{ runIds: string[] }`.
  - Response: `{ data: { runs: AgentRunData[]; comparison: { bestScore: string; fastest: string; mostTasks: string; bestSuccessRate: string } } }`.

- `GET /api/v1/agent-runs/{runId}/timeline`
  - Response: `{ data: { events: { timestamp: string; type: 'task_started'|'task_completed'|'task_failed'|'run_started'|'run_completed'; taskId?: string; message: string; metadata?: Record<string, any> }[] } }`.

- `GET /api/v1/agent-runs/{runId}/logs`
  - Query params: `level` (`info|warn|error|debug`), `limit`, `offset`.
  - Response: `{ data: { logs: { timestamp: string; level: string; message: string; metadata?: Record<string, any> }[]; total: number } }`.

- `GET /api/v1/agent-runs/{runId}/metrics`
  - Response: `{ data: { metrics: { cpu: { timestamp: string; value: number }[]; memory: { timestamp: string; value: number }[]; network: { timestamp: string; value: number }[]; duration: number; peakCpu: number; peakMemory: number; totalNetworkTraffic: number } } }`.

All partial loaders tolerate per-endpoint failures; when a section cannot be provided, return a descriptive non-2xx error so the UI can surface the message.

---

## Tasks Search

Frontend entry point: `apps/isomorphic/src/app/tasks/task-search.tsx`. Calls `tasksService.searchTasks` (`apps/isomorphic/src/services/api/tasks.service.ts`).

- `GET /api/v1/tasks/search`
  - Query params (`TaskSearchParams`): `query`, `website`, `useCase`, `status`, `agentRunId`, `minScore`, `maxScore`, `startDate`, `endDate`, `page`, `limit`, `sortBy`, `sortOrder`.
  - Response: `{ data: { tasks: TaskData[]; total: number; page: number; limit: number; facets: { websites: { name: string; count: number }[]; useCases: { name: string; count: number }[]; statuses: { name: string; count: number }[]; scoreRanges: { range: string; count: number }[] } } }`.
  - A “prefetch” call with `{ limit: 1 }` runs on page load to seed the dropdowns; initial facets may be empty.
  - `TaskData`: `{ taskId: string; agentRunId: string; website: string; useCase: string; prompt: string; status: 'pending'|'running'|'completed'|'failed'; score: number; successRate: number; duration: number; startTime: string; endTime?: string; createdAt: string; updatedAt: string; error?: string; actions: TaskAction[]; screenshots?: string[]; logs?: string[]; metadata: { environment: string; browser: string; viewport: { width: number; height: number }; userAgent: string } }`.

- `GET /api/v1/tasks`
  - Used internally when filtering by agent run, website, or use-case (`getTasks`, `getTasksByAgentRun`, etc.).
  - Query params (`TasksListQueryParams`): `page`, `limit`, `agentRunId`, `website`, `useCase`, `status`, `sortBy` (`startTime|score|duration|createdAt`), `sortOrder`, `startDate`, `endDate`.
  - Response: `{ data: { tasks: TaskData[]; total: number; page: number; limit: number } }`.

---

## Task Detail

Frontend entry point: `apps/isomorphic/src/app/tasks/[id]/*`, using `tasksService` (`apps/isomorphic/src/services/api/tasks.service.ts`) and `useTask`.

- `GET /api/v1/tasks/{taskId}`
  - Query params (`TaskQueryParams`): `includeActions`, `includeScreenshots`, `includeLogs`, `includeMetadata`.
  - Response: `{ data: { task: TaskData } }`. When `include*` flags are false, omit the heavy arrays.

- `GET /api/v1/tasks/{taskId}/details`
  - Query params (`TaskDetailQueryParams`): `includeActions`, `includeScreenshots`, `includeLogs`, `includePerformance`, `includeMetadata`.
  - Response: `{ data: { details: TaskDetails } }`.
  - `TaskDetails` extends `TaskData` with `performance` (aggregate action stats) and `metadata.resources`.

- `GET /api/v1/tasks/{taskId}/results`
  - Response: `{ data: { results: TaskResults } }` containing action breakdown, summary buckets, and execution timeline.

- `GET /api/v1/tasks/{taskId}/personas`
  - Response: `{ data: { personas: TaskPersonas } }` describing round, validator, agent, and task summary.

- `GET /api/v1/tasks/{taskId}/statistics`
  - Response: `{ data: { statistics: TaskStatistics } }` (totals, averages, per-website/use-case breakdowns, recent activity).

- `GET /api/v1/tasks/{taskId}/actions`
  - Query params: `page`, `limit`, `type`, `sortBy` (`timestamp|duration`), `sortOrder`.
  - Response: `{ data: { actions: any[]; total: number; page: number; limit: number } }`. Each action item should at least match `TaskAction` shape.

- `GET /api/v1/tasks/{taskId}/screenshots`
  - Response: `{ data: { screenshots: { id: string; url: string; timestamp: string; actionId?: string; description?: string }[] } }`.

- `GET /api/v1/tasks/{taskId}/logs`
  - Query params: `level`, `limit`, `offset`.
  - Response: `{ data: { logs: { timestamp: string; level: string; message: string; metadata?: Record<string, any> }[]; total: number } }`.

- `GET /api/v1/tasks/{taskId}/metrics`
  - Response: `{ data: { metrics: { duration: number; actionsPerSecond: number; averageActionDuration: number; totalWaitTime: number; totalNavigationTime: number; memoryUsage: { timestamp: string; value: number }[]; cpuUsage: { timestamp: string; value: number }[] } } }`.

- `GET /api/v1/tasks/{taskId}/timeline`
  - Response: `{ data: { timeline: { timestamp: string; action: string; duration: number; success: boolean; metadata?: Record<string, any> }[] } }`.

- `POST /api/v1/tasks/compare`
  - Body: `{ taskIds: string[] }`.
  - Response: `{ data: { tasks: TaskData[]; comparison: { bestScore: string; fastest: string; mostActions: string; bestSuccessRate: string } } }`.

- `GET /api/v1/tasks/analytics`
  - Query params: `timeRange`, `website`, `useCase`, `agentRunId`.
  - Response: `{ data: { analytics: { totalTasks: number; completedTasks: number; failedTasks: number; averageScore: number; averageDuration: number; successRate: number; performanceByWebsite: any[]; performanceByUseCase: any[]; performanceOverTime: any[] } } }`.

The progressive loader (`useTask`) calls `/personas`, `/details`, `/results`, and `/statistics` in parallel; each should return promptly and may fail independently with a descriptive error message.

---

### Error Handling Expectations

- Non-2xx responses should include `{ message, status, code }`. In particular, `/agent-runs/{runId}` uses `code: 'AGENT_RUN_NOT_FOUND'` for missing runs so the UI can show a tailored message.
- When an endpoint is not yet implemented, prefer returning an empty payload (e.g., `{ data: { validators: [] } }`) or a `404` with a clear `message`. The frontend suppresses stack traces and shows user-friendly copy.
- Numeric metrics are often interpreted as percentages. If the backend returns fractions (`0-1`), the services normalize them; returning already-scaled percentages is acceptable if consistent.

---

### File Location

This document lives at: `/home/usuario1/autoppia/autoppia_bittensor_dashboard_frontend/guide.md`
