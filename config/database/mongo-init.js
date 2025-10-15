// MongoDB initialization script
// This script runs when the MongoDB container starts for the first time

// Switch to the leaderboard database
db = db.getSiblingDB('leaderboard');

// Create a user for the application
db.createUser({
  user: 'leaderboard_user',
  pwd: 'leaderboard_password',
  roles: [
    {
      role: 'readWrite',
      db: 'leaderboard'
    }
  ]
});

// Create collections and indexes
db.createCollection('rounds');
db.createCollection('events');
db.createCollection('task_runs');
db.createCollection('agent_runs');
db.createCollection('weights');
db.createCollection('round_results');

// Create indexes
db.rounds.createIndex(
  { "validator_uid": 1, "validator_round_id": 1 },
  { unique: true, name: "u_round" }
);

db.events.createIndex(
  { "validator_uid": 1, "validator_round_id": 1, "ts": 1 },
  { name: "e_vr_ts" }
);

db.task_runs.createIndex(
  { "validator_uid": 1, "validator_round_id": 1, "task_id": 1, "miner_uid": 1 },
  { unique: true, name: "u_task_run" }
);

db.agent_runs.createIndex(
  { "validator_uid": 1, "validator_round_id": 1, "miner_uid": 1 },
  { unique: true, name: "u_agent_run" }
);

db.weights.createIndex(
  { "validator_uid": 1, "validator_round_id": 1 },
  { unique: true, name: "u_weights" }
);

db.round_results.createIndex(
  { "validator_uid": 1, "validator_round_id": 1 },
  { unique: true, name: "u_round_results" }
);

print('MongoDB initialization completed successfully');
