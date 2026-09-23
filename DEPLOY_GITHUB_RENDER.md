# GitHub + Render deployment

## 1. GitHub
Create a new GitHub repository and upload the contents of this project.

## 2. Render
Create a new Web Service from that GitHub repository.

Use:
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn --workers 1 --threads 4 --timeout 120 app:app`

Environment variables:
- `HOSTING_ADMIN_PASSWORD` = your private admin password
- `HOSTING_SECRET_KEY` = a long random secret

## Important
GitHub stores the source code; it does not execute the bot hosting panel.
Render (or another server/VPS) is the machine that runs the Flask dashboard and bot processes.

For a real always-on bot service, use an always-on server/background-worker setup. Free web-service plans can sleep or have resource/runtime limits, so they are not suitable for guaranteed 24/7 Telegram bot uptime.
