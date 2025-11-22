module.exports = {
  apps: [
    {
      name: 'api-leaderboard.autoppia.com',
      script: 'venv/bin/python3',
      args: 'venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 2 --limit-concurrency 100',
      cwd: '/root/autoppia_bittensor_dashboard_backend',
      interpreter: 'none',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      env: {
        NODE_ENV: 'production',
      },
      error_file: '/root/.pm2/logs/api-leaderboard.autoppia.com-error.log',
      out_file: '/root/.pm2/logs/api-leaderboard.autoppia.com-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    },
    {
      name: 'background-updater.autoppia.com',
      script: 'background_updater.py',
      interpreter: 'venv/bin/python3',
      cwd: '/root/autoppia_bittensor_dashboard_backend',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      watch: false,
      max_memory_restart: '500M',
      env: {
        NODE_ENV: 'production',
      },
      error_file: '/root/.pm2/logs/background-updater.autoppia.com-error.log',
      out_file: '/root/.pm2/logs/background-updater.autoppia.com-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    },
  ],
};

