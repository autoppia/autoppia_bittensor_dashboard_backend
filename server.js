const express = require('express');
const cors = require('cors');

const app = express();
const PORT = 8000;

// Middleware
app.use(cors({
  origin: ['http://localhost:3000', 'http://localhost:3002'],
  credentials: true
}));
app.use(express.json());

// Routes
app.get('/', (req, res) => {
  res.json({ message: 'AutoPPIA Bittensor Dashboard API' });
});

app.get('/health', (req, res) => {
  res.json({ status: 'healthy' });
});

// Rounds API Routes
app.get('/api/v1/rounds/current', (req, res) => {
  res.json({
    success: true,
    data: {
      round: {
        id: 20,
        startBlock: 6526001,
        endBlock: 6527000,
        current: true,
        startTime: "2024-01-15T08:00:00Z",
        endTime: null,
        status: "active",
        totalTasks: 1000,
        completedTasks: 750,
        averageScore: 0.85,
        topScore: 0.95,
        currentBlock: 6526300,
        blocksRemaining: 700,
        progress: 0.75
      }
    },
    error: null,
    code: null
  });
});

app.get('/api/v1/rounds/:id', (req, res) => {
  const roundId = parseInt(req.params.id);
  res.json({
    success: true,
    data: {
      round: {
        id: roundId,
        startBlock: 6525001,
        endBlock: 6526000,
        current: false,
        startTime: "2024-01-14T08:00:00Z",
        endTime: "2024-01-15T08:00:00Z",
        status: "completed",
        totalTasks: 1000,
        completedTasks: 1000,
        averageScore: 0.82,
        topScore: 0.94,
        currentBlock: 6526000,
        blocksRemaining: 0,
        progress: 1.0
      }
    },
    error: null,
    code: null
  });
});

app.get('/api/v1/rounds/:id/statistics', (req, res) => {
  const roundId = parseInt(req.params.id);
  res.json({
    success: true,
    data: {
      statistics: {
        roundId: roundId,
        totalMiners: 156,
        activeMiners: 142,
        totalTasks: 1000,
        completedTasks: 1000,
        averageScore: 0.82,
        topScore: 0.94,
        successRate: 0.95,
        averageDuration: 28.5,
        totalStake: 5200000,
        totalEmission: 260000,
        lastUpdated: "2024-01-15T08:00:00Z"
      }
    },
    error: null,
    code: null
  });
});

app.get('/api/v1/rounds/:id/miners', (req, res) => {
  const roundId = parseInt(req.params.id);
  const limit = parseInt(req.query.limit) || 25;
  const page = parseInt(req.query.page) || 1;
  
  res.json({
    success: true,
    data: {
      miners: [
        {
          uid: 25,
          hotkey: "5G1NjW9YhXLadMWajvTkfcJy6up3yH2q1YzMXDTi6ijanChe",
          score: 0.94,
          ranking: 1,
          duration: 25.3,
          tasksCompleted: 100,
          successRate: 0.98
        },
        {
          uid: 84,
          hotkey: "5G1NjW9YhXLadMWajvTkfcJy6up3yH2q1YzMXDTi6ijanChe",
          score: 0.92,
          ranking: 2,
          duration: 28.1,
          tasksCompleted: 98,
          successRate: 0.96
        },
        {
          uid: 36,
          hotkey: "5G1NjW9YhXLadMWajvTkfcJy6up3yH2q1YzMXDTi6ijanChe",
          score: 0.90,
          ranking: 3,
          duration: 30.2,
          tasksCompleted: 95,
          successRate: 0.94
        }
      ],
      total: 156,
      page: page,
      limit: limit
    },
    error: null,
    code: null
  });
});

app.get('/api/v1/rounds/:id/miners/top', (req, res) => {
  const roundId = parseInt(req.params.id);
  const limit = parseInt(req.query.limit) || 10;
  
  res.json({
    success: true,
    data: {
      miners: [
        {
          uid: 25,
          hotkey: "5G1NjW9YhXLadMWajvTkfcJy6up3yH2q1YzMXDTi6ijanChe",
          score: 0.94,
          ranking: 1,
          duration: 25.3,
          tasksCompleted: 100,
          successRate: 0.98
        },
        {
          uid: 84,
          hotkey: "5G1NjW9YhXLadMWajvTkfcJy6up3yH2q1YzMXDTi6ijanChe",
          score: 0.92,
          ranking: 2,
          duration: 28.1,
          tasksCompleted: 98,
          successRate: 0.96
        },
        {
          uid: 36,
          hotkey: "5G1NjW9YhXLadMWajvTkfcJy6up3yH2q1YzMXDTi6ijanChe",
          score: 0.90,
          ranking: 3,
          duration: 30.2,
          tasksCompleted: 95,
          successRate: 0.94
        }
      ]
    },
    error: null,
    code: null
  });
});

app.get('/api/v1/rounds/:id/validators', (req, res) => {
  const roundId = parseInt(req.params.id);
  
  // Generate diverse validator data based on round ID
  const generateValidatorData = (uid, name, baseStake, baseVtrust, version, performanceTier) => {
    // Use round ID and validator UID as seed for consistent but unique data
    const seed = `${uid}_${roundId}`;
    const hash = require('crypto').createHash('md5').update(seed).digest('hex');
    const hashInt = parseInt(hash.substring(0, 8), 16);
    
    // Generate validator-specific performance variations
    const baseCompletionRate = 0.85 + (hashInt % 15) / 100; // 85-99% completion
    const baseUptime = 95.0 + (hashInt % 5); // 95-99% uptime
    const baseScoreVariance = (hashInt % 20) / 100; // 0-19% score variance
    
    // Calculate validator-specific metrics
    const totalTasks = 8 + (roundId % 7); // 8-14 tasks based on round
    const completedTasks = Math.floor(totalTasks * baseCompletionRate);
    const averageScore = 0.8 + baseScoreVariance; // Base score + variance
    const clampedScore = Math.min(1.0, Math.max(0.0, averageScore));
    
    // Generate different statuses based on performance
    let status = "active";
    if (baseCompletionRate >= 0.95) {
      status = "active";
    } else if (baseCompletionRate >= 0.85) {
      status = "syncing";
    } else {
      status = "lagging";
    }
    
    return {
      id: `validator_${uid}`,
      name: name,
      hotkey: `5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY${uid}`,
      version: parseInt(version.split('.')[0]),
      stake: Math.floor(baseStake * (1.0 + (roundId - 1) * 0.02)),
      emission: Math.floor(baseStake * 0.05),
      performance: parseFloat(clampedScore.toFixed(3)),
      uptime: parseFloat((baseUptime / 100).toFixed(2)),
      status: status,
      totalTasks: totalTasks,
      completedTasks: completedTasks,
      averageScore: parseFloat(clampedScore.toFixed(3)),
      weight: Math.floor(baseStake),
      trust: parseFloat(baseVtrust.toFixed(3)),
      lastSeen: new Date().toISOString()
    };
  };
  
  const validators = [
    generateValidatorData(124, "Autoppia", 2000.0, 0.95, "7.0.0", "high"),
    generateValidatorData(129, "tao5", 1000.0, 0.71, "6.8.2", "medium"),
    generateValidatorData(133, "RoundTable21", 1500.0, 0.83, "7.0.3", "high"),
    generateValidatorData(135, "Kraken", 1200.0, 0.75, "6.9.1", "medium"),
    generateValidatorData(137, "Yuma", 2000.0, 0.83, "6.7.4", "medium")
  ];
  
  res.json({
    success: true,
    data: {
      validators: validators,
      total: validators.length
    },
    error: null,
    code: null
  });
});

app.get('/api/v1/rounds/:id/activity', (req, res) => {
  const roundId = parseInt(req.params.id);
  const limit = parseInt(req.query.limit) || 10;
  
  res.json({
    success: true,
    data: {
      activities: [
        {
          id: "act_001",
          timestamp: "2024-01-15T07:45:00Z",
          type: "miner_joined",
          minerUid: 25,
          message: "Miner 25 joined the round",
          block: 6525995
        },
        {
          id: "act_002",
          timestamp: "2024-01-15T07:30:00Z",
          type: "task_completed",
          minerUid: 84,
          message: "Miner 84 completed task batch",
          block: 6525990
        },
        {
          id: "act_003",
          timestamp: "2024-01-15T07:15:00Z",
          type: "validator_update",
          minerUid: null,
          message: "Validator Autoppia updated consensus",
          block: 6525985
        }
      ]
    },
    error: null,
    code: null
  });
});

app.get('/api/v1/rounds/:id/progress', (req, res) => {
  const roundId = parseInt(req.params.id);
  
  res.json({
    success: true,
    data: {
      progress: {
        currentBlock: 6526300,
        totalBlocks: 6527000,
        progress: 0.75,
        blocksRemaining: 700,
        estimatedTimeRemaining: {
          days: 0,
          hours: 2,
          minutes: 30,
          seconds: 45
        },
        lastUpdated: "2024-01-15T10:30:00Z"
      }
    },
    error: null,
    code: null
  });
});

// Start server
app.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 AutoPPIA Bittensor Dashboard API running on http://0.0.0.0:${PORT}`);
  console.log(`📊 Health check: http://0.0.0.0:${PORT}/health`);
  console.log(`🎯 Current round: http://0.0.0.0:${PORT}/api/v1/rounds/current`);
});

module.exports = app;
