conda activate leaderboard
pm2 start "python manage.py runserver 0.0.0.0:8000" --name leaderboard_backend
conda deactivate