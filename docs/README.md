# Autoppia Leaderboard API

A FastAPI backend for the Autoppia Bittensor Leaderboard system. This API provides endpoints for validators to submit round data, task runs, agent runs, and other metrics to a MongoDB database.

## Features

- **FastAPI Framework**: Modern, fast, and async Python web framework
- **MongoDB Integration**: Async MongoDB operations using Motor
- **Authentication**: API key-based authentication
- **Idempotency**: Optional idempotency key support for safe retries
- **Comprehensive Logging**: Request/response logging and error tracking
- **Docker Support**: Ready-to-use Docker and Docker Compose configuration
- **Health Checks**: Built-in health monitoring endpoints

## API Endpoints

### Round Management

- `POST /v1/rounds/start` - Start a new round
- `POST /v1/rounds/{validator_round_id}/events` - Post events for a round
- `POST /v1/rounds/{validator_round_id}/task-runs:batch-upsert` - Batch upsert task runs
- `POST /v1/rounds/{validator_round_id}/agent-runs:upsert` - Upsert agent runs
- `POST /v1/rounds/{validator_round_id}/progress` - Post progress updates
- `PUT /v1/rounds/{validator_round_id}/weights` - Update round weights
- `POST /v1/rounds/{validator_round_id}/finalize` - Finalize a round
- `POST /v1/rounds/{validator_round_id}/round-results` - Post complete round results

### Utility Endpoints

- `GET /health` - Health check
- `GET /v1/rounds/{validator_round_id}/status` - Get round status
- `GET /v1/rounds/{validator_round_id}/weights` - Get round weights
- `GET /debug/idempotency-stats` - Idempotency cache statistics

## Quick Start

### Prerequisites

- Python 3.11+
- MongoDB 4.4+
- Docker (optional)

### Local Development

1. **Clone and setup**:
   ```bash
   cd autoppia_bittensor_dashboard_backend
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure environment**:
   ```bash
   cp env.example .env
   # Edit .env with your settings
   ```

3. **Start MongoDB** (if not using Docker):
   ```bash
   # Using Docker
   docker run -d -p 27017:27017 --name mongodb mongo:7.0
   
   # Or install MongoDB locally
   ```

4. **Run the API**:
   ```bash
   uvicorn app.main:app --reload --port 8080
   ```

5. **Access the API**:
   - API: http://localhost:8080
   - Documentation: http://localhost:8080/docs
   - Health check: http://localhost:8080/health

### Docker Deployment

1. **Using Docker Compose** (recommended):
   ```bash
   docker-compose up -d
   ```

2. **Using Docker directly**:
   ```bash
   # Build the image
   docker build -t leaderboard-api .
   
   # Run with MongoDB
   docker run -d --name mongodb -p 27017:27017 mongo:7.0
   docker run -d --name api -p 8080:8080 --link mongodb leaderboard-api
   ```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | "Autoppia Leaderboard API" | Application name |
| `DEBUG` | false | Debug mode |
| `HOST` | "0.0.0.0" | Server host |
| `PORT` | 8080 | Server port |
| `MONGO_URI` | "mongodb://localhost:27017" | MongoDB connection string |
| `MONGO_DB` | "leaderboard" | MongoDB database name |
| `API_KEYS` | ["dev-token-123"] | List of valid API keys |
| `CORS_ORIGINS` | ["*"] | CORS allowed origins |
| `IDEMPOTENCY_TTL` | 600 | Idempotency cache TTL in seconds |

### API Authentication

All endpoints require authentication via the `Authorization` header:

```bash
Authorization: Bearer your-api-key-here
```

## API Usage Examples

### Start a Round

```bash
curl -X POST "http://localhost:8080/v1/rounds/start" \
  -H "Authorization: Bearer dev-token-123" \
  -H "Content-Type: application/json" \
  -d '{
    "validator_uid": 12,
    "validator_round_id": "12-18172-abc",
    "version": "iwa-1.3.0",
    "max_epochs": 20,
    "max_blocks": 7200,
    "started_at": 1728123456.12,
    "start_block": 18172,
    "n_total_miners": 96,
    "task_set": [],
    "meta": {"netuid": 36}
  }'
```

### Post an Event

```bash
curl -X POST "http://localhost:8080/v1/rounds/12-18172-abc/events" \
  -H "Authorization: Bearer dev-token-123" \
  -H "Content-Type: application/json" \
  -d '{
    "validator_uid": 12,
    "validator_round_id": "12-18172-abc",
    "phase": "sending_tasks",
    "message": "Starting task distribution"
  }'
```

### Batch Upsert Task Runs

```bash
curl -X POST "http://localhost:8080/v1/rounds/12-18172-abc/task-runs:batch-upsert" \
  -H "Authorization: Bearer dev-token-123" \
  -H "Content-Type: application/json" \
  -d '{
    "validator_uid": 12,
    "validator_round_id": "12-18172-abc",
    "task_runs": [
      {
        "validator_uid": 12,
        "validator_round_id": "12-18172-abc",
        "task_id": "books:1",
        "miner_uid": 44,
        "miner_hotkey": "5F...",
        "miner_coldkey": "5G...",
        "eval_score": 0.8,
        "time_score": 0.9,
        "execution_time": 12.4,
        "reward": 0.7,
        "solution": {},
        "test_results": {},
        "evaluation_result": {}
      }
    ]
  }'
```

## Database Schema

### Collections

- **rounds**: Round metadata and configuration
- **events**: Round events and progress updates
- **task_runs**: Individual task execution results
- **agent_runs**: Agent performance summaries
- **weights**: Round weight distributions
- **round_results**: Complete round archival data

### Indexes

All collections have appropriate compound indexes for efficient querying and unique constraints for data integrity.

## Development

### Project Structure

```
app/
├── main.py              # FastAPI application entry point
├── config.py            # Configuration management
├── api/
│   ├── deps.py          # Authentication dependencies
│   └── routes/
│       └── rounds.py    # Round management endpoints
├── db/
│   └── mongo.py         # MongoDB client and indexes
├── models/
│   └── schemas.py       # Pydantic models
├── services/
│   └── idempotency.py   # Idempotency service
└── utils/               # Utility functions
```

### Adding New Endpoints

1. Define Pydantic models in `app/models/schemas.py`
2. Add route handlers in `app/api/routes/rounds.py`
3. Update database operations in `app/db/mongo.py` if needed
4. Add tests and documentation

### Testing

```bash
# Install development dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest
```

## Production Considerations

1. **Security**: Replace default API keys with secure, environment-specific keys
2. **MongoDB**: Use MongoDB Atlas or a properly secured MongoDB instance
3. **Monitoring**: Add application monitoring (Prometheus, Grafana)
4. **Logging**: Configure structured logging for production
5. **Scaling**: Consider horizontal scaling with load balancers
6. **Backup**: Implement MongoDB backup strategies

## License

This project is part of the Autoppia ecosystem for Bittensor validation.

## Support

For issues and questions, please refer to the Autoppia documentation or create an issue in the project repository.