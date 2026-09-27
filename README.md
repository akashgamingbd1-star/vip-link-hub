# Telegram Bot Hosting Business Panel

Run:
- `pip install -r requirements.txt`
- `python app.py`
- open `http://127.0.0.1:5000`

Admin demo: `admin` / `admin123` (change immediately for real use).

Features: registration/login, referral code, packages and bot limits, bKash/Nagad deposit flow with copy-number screen, admin approval/rejection, referral commission, admin payment settings, user ban/unban, two-file bot upload, per-bot Run/Stop/Delete, live bot.py and requirements.txt editor with CodeMirror syntax highlighting, runtime log, per-bot virtualenv.

For public production use, add HTTPS, CSRF protection, rate limits, strong secrets, backups and container/VM isolation. Uploaded Python is arbitrary code and must not run in the same trust boundary as the control panel.
