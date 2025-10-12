# Frontend Changes for Agent Detail Endpoint

## API Endpoint Changes

### Endpoint URL
```
GET /api/v1/agents/{agent_id}
```

### Response Structure
```json
{
  "data": {
    "agent": {
      "id": "123",
      "uid": 123,
      "name": "Autoppia Bittensor",
      "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
      "type": "autoppia",
      "imageUrl": "data:image/svg+xml;base64,...",
      "githubUrl": "https://github.com/autoppia/bittensor-agent",
      "taostatsUrl": "https://taostats.io/miner/123",
      "isSota": false,
      "description": "Autoppia's native Bittensor agent for web automation tasks",
      "version": "1.0.0",
      "status": "active",
      "totalRuns": 1247,
      "successfulRuns": 1089,
      "currentScore": 0.87,           // RENAMED from averageScore
      "currentTopScore": 0.95,        // RENAMED from bestScore
      "currentRank": 10,              // NEW FIELD
      "bestRankEver": 6,              // NEW FIELD
      "roundsParticipated": 1247,     // NEW FIELD
      "alphaWonInPrizes": 174.0,      // NEW FIELD
      "averageDuration": 32.5,
      "totalTasks": 6235,
      "completedTasks": 5445,
      "lastSeen": "2025-10-12T06:01:46.167710",
      "createdAt": "2023-06-01T00:00:00",
      "updatedAt": "2025-10-12T06:06:46.167735"
    },
    "scoreRoundData": [
      {
        "round_id": 19,
        "score": 0.758,
        "rank": 30,
        "reward": 0.0,
        "timestamp": "2025-10-12T06:01:46.167710"
      }
      // ... more data points
    ]
  },
  "success": true,
  "message": "Retrieved agent details with score vs round data for autoppia-bittensor"
}
```

## Field Changes Summary

### Removed Fields
- ❌ `averageScore` (renamed to `currentScore`)
- ❌ `bestScore` (renamed to `currentTopScore`)
- ❌ `successRate` (completely removed)

### Renamed Fields
- `averageScore` → `currentScore`
- `bestScore` → `currentTopScore`

### New Fields
- `currentRank`: Current rank of the agent
- `bestRankEver`: Best rank ever achieved
- `roundsParticipated`: Number of rounds participated
- `alphaWonInPrizes`: Alpha won in prizes

## Frontend Implementation Changes

### 1. Update TypeScript/JavaScript Interfaces

```typescript
// Old interface
interface Agent {
  id: string;
  name: string;
  averageScore: number;  // ❌ Remove this
  bestScore: number;     // ❌ Remove this
  successRate: number;   // ❌ Remove this
  // ... other fields
}

// New interface
interface Agent {
  id: string;
  name: string;
  currentScore: number;        // ✅ New field
  currentTopScore: number;     // ✅ New field
  currentRank: number;         // ✅ New field
  bestRankEver: number;        // ✅ New field
  roundsParticipated: number;  // ✅ New field
  alphaWonInPrizes: number;    // ✅ New field
  // ... other fields
}

// Score vs Round Data interface
interface ScoreRoundDataPoint {
  round_id: number;
  score: number;
  rank: number | null;
  reward: number;
  timestamp: string;
}

interface AgentDetailResponse {
  agent: Agent;
  scoreRoundData: ScoreRoundDataPoint[];
}
```

### 2. Update Component Props

```typescript
// Update your component props
interface AgentDetailProps {
  agent: Agent;
  scoreRoundData: ScoreRoundDataPoint[];
}

// Or if you're using the full response
interface AgentDetailProps {
  data: {
    agent: Agent;
    scoreRoundData: ScoreRoundDataPoint[];
  };
}
```

### 3. Update Display Components

```jsx
// Example component update
function AgentDetailCard({ agent, scoreRoundData }) {
  return (
    <div className="agent-detail-card">
      <h2>{agent.name}</h2>
      
      {/* Key metrics in the requested order */}
      <div className="metrics-grid">
        <div className="metric-item current-rank">
          <span className="label">Current Rank</span>
          <span className="value" style={{ color: '#FFD700' }}>
            #{agent.currentRank}
          </span>
        </div>
        
        <div className="metric-item best-rank">
          <span className="label">Best Rank Ever</span>
          <span className="value" style={{ color: '#C0C0C0' }}>
            #{agent.bestRankEver}
          </span>
        </div>
        
        <div className="metric-item current-score">
          <span className="label">Current Score</span>
          <span className="value" style={{ color: '#00FF00' }}>
            {agent.currentScore.toFixed(3)}
          </span>
        </div>
        
        <div className="metric-item rounds-participated">
          <span className="label">Rounds Participated</span>
          <span className="value" style={{ color: '#0080FF' }}>
            {agent.roundsParticipated}
          </span>
        </div>
        
        <div className="metric-item alpha-prizes">
          <span className="label">Alpha Won in Prizes</span>
          <span className="value" style={{ color: '#8000FF' }}>
            {agent.alphaWonInPrizes.toFixed(2)} α
          </span>
        </div>
        
        <div className="metric-item current-top-score">
          <span className="label">Current Top Score</span>
          <span className="value" style={{ color: '#FF8000' }}>
            {agent.currentTopScore.toFixed(3)}
          </span>
        </div>
      </div>
      
      {/* Score vs Round Chart */}
      <div className="score-chart">
        <h3>Score vs Round Performance</h3>
        <ScoreRoundChart data={scoreRoundData} />
      </div>
    </div>
  );
}
```

### 4. Update API Calls

```typescript
// Update your API service
class AgentService {
  async getAgentDetails(agentId: string): Promise<AgentDetailResponse> {
    const response = await fetch(`/api/v1/agents/${agentId}`);
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || 'Failed to fetch agent details');
    }
    
    return data.data; // Returns { agent, scoreRoundData }
  }
}
```

### 5. Update Chart Components

```jsx
// Example chart component for score vs round data
function ScoreRoundChart({ data }: { data: ScoreRoundDataPoint[] }) {
  const chartData = data.map(point => ({
    x: point.round_id,
    y: point.score,
    rank: point.rank,
    timestamp: point.timestamp
  }));
  
  return (
    <div className="chart-container">
      {/* Use your preferred charting library */}
      <LineChart
        data={chartData}
        xKey="x"
        yKey="y"
        xLabel="Round ID"
        yLabel="Score"
        color="#FFD700"
      />
    </div>
  );
}
```

### 6. CSS Styling Suggestions

```css
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin: 1rem 0;
}

.metric-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 1rem;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(10px);
}

.metric-item .label {
  font-size: 0.875rem;
  color: #888;
  margin-bottom: 0.5rem;
}

.metric-item .value {
  font-size: 1.5rem;
  font-weight: bold;
}

/* Color classes for easy theming */
.current-rank { border-left: 4px solid #FFD700; }
.best-rank { border-left: 4px solid #C0C0C0; }
.current-score { border-left: 4px solid #00FF00; }
.rounds-participated { border-left: 4px solid #0080FF; }
.alpha-prizes { border-left: 4px solid #8000FF; }
.current-top-score { border-left: 4px solid #FF8000; }
```

## Migration Checklist

- [ ] Update TypeScript interfaces
- [ ] Update component props and state
- [ ] Replace `averageScore` with `currentScore`
- [ ] Replace `bestScore` with `currentTopScore`
- [ ] Remove `successRate` references
- [ ] Add new fields: `currentRank`, `bestRankEver`, `roundsParticipated`, `alphaWonInPrizes`
- [ ] Update API calls to handle new response structure
- [ ] Add score vs round data visualization
- [ ] Update styling with new color scheme
- [ ] Test with real API endpoint

## Color Scheme

- **Current Rank**: Gold (#FFD700)
- **Best Rank Ever**: Silver (#C0C0C0)
- **Current Score**: Green (#00FF00)
- **Rounds Participated**: Blue (#0080FF)
- **Alpha Won in Prizes**: Purple (#8000FF)
- **Current Top Score**: Orange (#FF8000)
