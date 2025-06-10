# Autoppia Bittensor Leaderboard API

A Django REST API for tracking and aggregating Bittensor miner tasks and performance metrics. This leaderboard system collects task execution data from miners and provides real-time performance analytics.

## 🚀 Features

- **Task Logging**: Record miner task executions with scores, durations, and success rates
- **Real-time Metrics**: Automatically calculated aggregated performance metrics per miner
- **Flexible Filtering**: Filter tasks by time period (day/week/month) and websites
- **Validator Tracking**: Track performance across different validators
- **MongoDB Integration**: Scalable storage with MongoDB Atlas

## 📋 API Endpoints

### Tasks API (`/api/tasks/`)

- `GET /api/tasks/` - List all task logs
- `POST /api/tasks/` - Submit new task execution data
- `GET /api/tasks/filtered/` - Get filtered tasks by period and websites

### Metrics API (`/api/metrics/`)

- `GET /api/metrics/` - List all miner performance metrics
- `GET /api/metrics/{miner_uid}/` - Get specific miner metrics

## 🛠️ Installation & Setup

### Prerequisites

- Python 3.8+
- MongoDB Atlas account
- PM2 (for production deployment)

### 1. Clone the repository

```bash
git clone https://github.com/autoppia/bittensor-leaderboard.git
cd bittensor-leaderboard
```

### 2. Create virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Environment configuration

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

```env
SECRET_KEY=YOUR_SECRET_KEY
MONGO_CONNECTION_URI=YOUR_MONGO_CONNECTION_URI
MONGO_DB_NAME=YOUR_DB_NAME
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

## 🔧 Environment Variables

| Variable               | Description                   | Example                                        |
| ---------------------- | ----------------------------- | ---------------------------------------------- |
| `SECRET_KEY`           | Django secret key             | `django-insecure-your-secret-key`              |
| `MONGO_CONNECTION_URI` | MongoDB connection string     | `mongodb+srv://user:pass@cluster.mongodb.net/` |
| `MONGO_DB_NAME`        | MongoDB database name         | `bittensor_leaderboard`                        |
| `DEBUG`                | Debug mode (True/False)       | `False`                                        |
| `ALLOWED_HOSTS`        | Comma-separated allowed hosts | `api-leaderboard.autoppia.com,localhost`       |

### 5. Configure MongoDB Atlas

1. Create a MongoDB Atlas cluster
2. Create database user with read/write permissions
3. Add your server IP to IP whitelist
4. Get connection string and update `MONGO_CONNECTION_URI` in `.env`

### 6. Run the development server

```bash
source venv/bin/activate
python manage.py runserver 0.0.0.0:8000
```

The API will be available at `http://localhost:8000`

## 🚀 Production Deployment

### Install system dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y nginx nodejs npm

# Install PM2 globally
sudo npm install -g pm2
```

### Configure Nginx

```bash
# Edit Nginx configuration
sudo nano /etc/nginx/sites-available/default
```

Replace all content with:

```nginx
server {
    listen 80;
    server_name api-leaderboard.autoppia.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name leaderboard.autoppia.com;

    location / {
        proxy_pass http://localhost:7000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
# Test and restart Nginx
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl enable nginx
```

### Start application with PM2

```bash
cd ~/autoppia_bittensor_dashboard_backend
source venv/bin/activate

# Start Django API
pm2 start "/home/admin/autoppia_bittensor_dashboard_backend/venv/bin/python3 manage.py runserver 0.0.0.0:8000" --name leaderboard_backend

# Save PM2 configuration
pm2 save
pm2 startup

deactivate
```

### Configure DNS (Cloudflare)

1. Go to your Cloudflare dashboard
2. Update DNS records:
   - `api-leaderboard` A record → Your VPS IP (195.179.228.132)
   - `leaderboard` A record → Your VPS IP (195.179.228.132)
3. Set Proxy status to "Proxied" for both records

## 📊 Data Structure

### Task Submission Format

```json
{
  "validator_uid": "validator_123",
  "miner_uid": "miner_456",
  "miner_hotkey": "5F3sa2TJAWMqDhXG6jhV4N8ko9SxwGy8TpaNS1repo5EYjQX",
  "task_id": "task_789",
  "success": true,
  "score": 0.85,
  "duration": 120,
  "website": "example.com"
}
```

### Metric Response Format

```json
{
  "miner_uid": "miner_456",
  "miner_hotkey": "5F3sa2TJAWMqDhXG6jhV4N8ko9SxwGy8TpaNS1repo5EYjQX",
  "tasks_per_validator": {
    "validator_123": 10
  },
  "scores_per_validator": {
    "validator_123": 0.82
  },
  "durations_per_validator": {
    "validator_123": 115
  },
  "successful_tasks": 85,
  "total_tasks": 100,
  "success_rate": 0.85,
  "score_avg": 0.82,
  "duration_avg": 115
}
```

## 🔧 Environment Variables

| Variable               | Description                   | Example                                        |
| ---------------------- | ----------------------------- | ---------------------------------------------- |
| `SECRET_KEY`           | Django secret key             | `django-insecure-your-secret-key`              |
| `MONGO_CONNECTION_URI` | MongoDB connection string     | `mongodb+srv://user:pass@cluster.mongodb.net/` |
| `MONGO_DB_NAME`        | MongoDB database name         | `bittensor_leaderboard`                        |
| `DEBUG`                | Debug mode (True/False)       | `False`                                        |
| `ALLOWED_HOSTS`        | Comma-separated allowed hosts | `api-leaderboard.autoppia.com,localhost`       |

## 🔧 Environment Variables

| Variable               | Description                   | Example                                        |
| ---------------------- | ----------------------------- | ---------------------------------------------- |
| `SECRET_KEY`           | Django secret key             | `django-insecure-your-secret-key`              |
| `MONGO_CONNECTION_URI` | MongoDB connection string     | `mongodb+srv://user:pass@cluster.mongodb.net/` |
| `MONGO_DB_NAME`        | MongoDB database name         | `bittensor_leaderboard`                        |
| `DEBUG`                | Debug mode (True/False)       | `False`                                        |
| `ALLOWED_HOSTS`        | Comma-separated allowed hosts | `api-leaderboard.autoppia.com,localhost`       |

## 📈 Usage Examples

### Submit a task

```bash
curl -X POST http://api-leaderboard.autoppia.com/tasks/ \
  -H "Content-Type: application/json" \
  -d '{
    "validator_uid": "validator_123",
    "miner_uid": "miner_456",
    "miner_hotkey": "5F3sa2TJAWMqDhXG6jhV4N8ko9SxwGy8TpaNS1repo5EYjQX",
    "task_id": "task_789",
    "success": true,
    "score": 0.85,
    "duration": 120,
    "website": "example.com"
  }'
```

### Get filtered tasks

```bash
# Tasks from last week for specific websites
curl "http://api-leaderboard.autoppia.com/tasks/filtered/?period=Week&websites=example.com,test.com"
```

### Get miner metrics

```bash
# All metrics
curl http://api-leaderboard.autoppia.com/metrics/

# Specific miner
curl http://api-leaderboard.autoppia.com/metrics/miner_456/
```

## 🐛 Troubleshooting

### Common Issues

1. **MongoDB Connection Error**

   - Verify connection string in `.env`
   - Check IP whitelist in MongoDB Atlas
   - Ensure database user has proper permissions

2. **PM2 Process Not Starting**

   - Check if virtual environment is activated
   - Verify Python path: `which python`
   - Check PM2 logs: `pm2 logs leaderboard_backend`

3. **Import Errors**
   - Ensure all dependencies are installed: `pip install -r requirements.txt`
   - Check virtual environment is activated

### Useful Commands

```bash
# Check PM2 status
pm2 status

# View application logs
pm2 logs leaderboard_backend

# Restart application
pm2 restart leaderboard_backend

# Check Nginx status
sudo systemctl status nginx

# Test Nginx configuration
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx

# Test MongoDB connection
python manage.py shell
>>> from apps.database.mongo_service import MongoService
>>> MongoService.db('test_db').list_collection_names()

# Check Django configuration
python manage.py check
```

---

**Contact**: Discord - Riiveer or Daryxx  
**Built with ❤️ by Autoppia for the Bittensor ecosystem**
