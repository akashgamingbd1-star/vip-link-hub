# Premium Telegram Bot Hosting Panel

A personal, self-hosted dashboard for running multiple Python Telegram bots.

## Included
- Premium responsive dashboard
- Up to 16 bot slots by default
- Each bot has exactly two main runtime files: `bot.py` and `requirements.txt`
- Upload both files
- Large-screen code editor for both files
- Run / Stop / Delete per bot
- Live CPU, RAM, PID and storage indicators
- Per-bot console log viewer
- Automatic per-bot virtual environment when requirements.txt contains packages
- SQLite persistence
- Search-friendly dashboard and mobile layout

## Run locally
1. Install Python 3.10+.
2. Open a terminal in this folder.
3. Run:
   `python -m venv .venv`
4. Activate it:
   - Windows: `.venv\Scripts\activate`
   - Linux/macOS: `source .venv/bin/activate`
5. Install dependencies:
   `pip install -r requirements.txt`
6. Start:
   `python app.py`
7. Open `http://127.0.0.1:5000`

Default admin password:
`ChangeMe123!`

IMPORTANT: change the password before exposing the panel to the internet:
- Windows PowerShell: `$env:HOSTING_ADMIN_PASSWORD="your-password"`
- Linux/macOS: `export HOSTING_ADMIN_PASSWORD="your-password"`

Also set `HOSTING_SECRET_KEY` to a long random value.

## Hosting notes
This panel executes the Python files you upload. Use it only on a server you control.
For internet-facing deployment, put it behind HTTPS and a reverse proxy, and preferably run each bot inside a stronger container/VM isolation layer.

The panel itself does not provide a free cloud server. It is the control panel + process runner; the machine/server where you run it supplies the CPU, RAM, network and storage.
