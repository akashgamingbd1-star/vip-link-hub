import os
import sys
import sqlite3
import subprocess
import time
import shutil
import zipfile
import threading
import signal
import json
from io import BytesIO
from pathlib import Path

from flask import Flask, render_template_string, request, redirect, url_for, send_file, flash

app = Flask(__name__)
app.secret_key = os.environ.get("PANEL_SECRET", "multi_bot_hosting_panel_2026")

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "user_bots"
DATABASE = BASE_DIR / "platform.db"
LOG_FOLDER = BASE_DIR / "bot_logs"
VENV_FOLDER = BASE_DIR / "bot_venvs"

for folder in (UPLOAD_FOLDER, LOG_FOLDER, VENV_FOLDER):
    folder.mkdir(parents=True, exist_ok=True)

# Runtime registry:
# {bot_id: {"process": Popen, "start_time": float, "stop_requested": bool,
#           "restart_count": int, "log_handle": file}}
active_processes = {}
process_lock = threading.RLock()
watchdog_started = False

MAX_AUTO_RESTARTS = 5
RESTART_DELAY = 3
STOP_TIMEOUT = 8


def get_db():
    conn = sqlite3.connect(DATABASE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            main_file TEXT NOT NULL,
            status TEXT DEFAULT 'Stopped',
            logs TEXT DEFAULT 'Ready to run...',
            start_timestamp REAL DEFAULT 0,
            restart_count INTEGER DEFAULT 0,
            venv_path TEXT DEFAULT ''
        )
    """)
    # Safe migration for databases created by the older panel.
    columns = {row["name"] for row in cursor.execute("PRAGMA table_info(bots)").fetchall()}
    if "restart_count" not in columns:
        cursor.execute("ALTER TABLE bots ADD COLUMN restart_count INTEGER DEFAULT 0")
    if "venv_path" not in columns:
        cursor.execute("ALTER TABLE bots ADD COLUMN venv_path TEXT DEFAULT ''")
    conn.commit()
    conn.close()


init_db()


def db_update(bot_id, **fields):
    if not fields:
        return
    conn = get_db()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [bot_id]
    conn.execute(f"UPDATE bots SET {assignments} WHERE id = ?", values)
    conn.commit()
    conn.close()


def safe_name(value):
    value = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value.strip())
    return value[:80] or "bot"


def format_uptime(start_timestamp):
    if not start_timestamp:
        return ""
    diff = max(0, int(time.time() - start_timestamp))
    days = diff // 86400
    hours = (diff % 86400) // 3600
    minutes = (diff % 3600) // 60
    seconds = diff % 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m {seconds}s")
    return " ".join(parts)


def read_last_log(bot_id, fallback="Ready to run..."):
    path = LOG_FOLDER / f"{bot_id}.log"
    try:
        if not path.exists():
            return fallback
        with path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 12000))
            data = f.read()
        return data[-6000:] if data else fallback
    except Exception:
        return fallback


def write_log(bot_id, message):
    path = LOG_FOLDER / f"{bot_id}.log"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def process_alive(proc):
    return proc is not None and proc.poll() is None


def stop_process_tree(proc):
    """Stop a bot and its child processes when supported by the OS."""
    if not proc or proc.poll() is not None:
        return

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.terminate()
            proc.wait(timeout=STOP_TIMEOUT)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def python_for_bot(bot):
    venv_path = bot["venv_path"] if "venv_path" in bot.keys() else ""
    if venv_path:
        if os.name == "nt":
            candidate = Path(venv_path) / "Scripts" / "python.exe"
        else:
            candidate = Path(venv_path) / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def install_requirements(bot_dir, venv_path, bot_id):
    req = bot_dir / "requirements.txt"
    if not req.exists():
        return True, "No requirements.txt supplied."

    try:
        if not Path(venv_path).exists():
            write_log(bot_id, "Creating isolated Python environment...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_path)],
                capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                write_log(bot_id, "Virtual environment creation failed; using system Python.")
                return True, "Venv unavailable; system Python fallback enabled."

        py = python_for_bot({"venv_path": str(venv_path)})
        write_log(bot_id, "Installing bot requirements...")
        result = subprocess.run(
            [py, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "pip install failed")[-3000:]
            write_log(bot_id, "Requirement installation failed: " + error)
            return False, "Requirements installation failed. Check the bot log."
        write_log(bot_id, "Requirements installed successfully.")
        return True, "Requirements installed."
    except subprocess.TimeoutExpired:
        write_log(bot_id, "Requirement installation timed out.")
        return False, "Requirement installation timed out."
    except Exception as e:
        write_log(bot_id, f"Requirement error: {e}")
        return False, str(e)


def start_process(bot):
    bot_id = bot["id"]
    bot_dir = Path(bot["folder_path"])
    main_file = bot["main_file"]
    main_path = bot_dir / main_file

    if not main_path.exists():
        raise FileNotFoundError(f"Main file not found: {main_file}")

    py = python_for_bot(bot)
    log_path = LOG_FOLDER / f"{bot_id}.log"
    log_handle = log_path.open("a", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["BOT_HOSTING_ID"] = str(bot_id)

    kwargs = {
        "cwd": str(bot_dir),
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }

    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [py, "-u", str(main_path.name)],
        **kwargs
    )

    # Give the process a short startup window so immediate crashes are reported.
    time.sleep(0.8)
    if proc.poll() is not None:
        log_handle.close()
        raise RuntimeError("Bot exited immediately. Check the bot log for the traceback.")

    return proc, log_handle


def launch_bot(bot_id, manual=True):
    conn = get_db()
    bot = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    conn.close()

    if not bot:
        return False, "Bot not found."

    with process_lock:
        existing = active_processes.get(bot_id)
        if existing and process_alive(existing["process"]):
            return True, "Bot is already running."

        try:
            write_log(bot_id, "Starting bot...")
            proc, log_handle = start_process(bot)
            now = time.time()
            active_processes[bot_id] = {
                "process": proc,
                "start_time": now,
                "stop_requested": False,
                "restart_count": int(bot["restart_count"] or 0),
                "log_handle": log_handle,
            }
            db_update(
                bot_id,
                status="Running",
                logs="Bot is running live...",
                start_timestamp=now,
            )
            write_log(bot_id, "Bot started successfully.")
            return True, "Bot started."
        except Exception as e:
            db_update(bot_id, status="Stopped", logs=f"Start error: {e}", start_timestamp=0)
            write_log(bot_id, f"START ERROR: {e}")
            return False, str(e)


def stop_bot_internal(bot_id, user_requested=True):
    with process_lock:
        info = active_processes.pop(bot_id, None)

    if info:
        if user_requested:
            info["stop_requested"] = True
        try:
            stop_process_tree(info["process"])
        finally:
            try:
                info["log_handle"].close()
            except Exception:
                pass

    db_update(
        bot_id,
        status="Stopped",
        logs="Bot stopped by user." if user_requested else "Bot stopped.",
        start_timestamp=0,
    )
    if user_requested:
        write_log(bot_id, "Bot stopped by user.")
    return True


def watchdog_loop():
    global watchdog_started
    watchdog_started = True

    while True:
        time.sleep(3)
        with process_lock:
            snapshot = list(active_processes.items())

        for bot_id, info in snapshot:
            proc = info["process"]
            code = proc.poll()
            if code is None:
                continue

            try:
                info["log_handle"].close()
            except Exception:
                pass

            with process_lock:
                active_processes.pop(bot_id, None)

            if info.get("stop_requested"):
                continue

            conn = get_db()
            bot = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
            conn.close()
            if not bot:
                continue

            restart_count = int(bot["restart_count"] or 0) + 1
            write_log(bot_id, f"Bot exited with code {code}.")

            if restart_count <= MAX_AUTO_RESTARTS:
                db_update(
                    bot_id,
                    status="Restarting",
                    restart_count=restart_count,
                    logs=f"Process stopped unexpectedly. Auto-restart {restart_count}/{MAX_AUTO_RESTARTS}...",
                    start_timestamp=0,
                )
                time.sleep(RESTART_DELAY)
                ok, _ = launch_bot(bot_id, manual=False)
                if not ok:
                    db_update(bot_id, status="Stopped", logs="Auto-restart failed. Check logs.", start_timestamp=0)
            else:
                db_update(
                    bot_id,
                    status="Stopped",
                    logs=f"Bot crashed repeatedly. Auto-restart limit ({MAX_AUTO_RESTARTS}) reached.",
                    start_timestamp=0,
                )
                write_log(bot_id, "Auto-restart limit reached; manual restart required.")


def start_watchdog():
    global watchdog_started
    if watchdog_started:
        return
    thread = threading.Thread(target=watchdog_loop, daemon=True, name="bot-watchdog")
    thread.start()


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Premium Telegram Bot Hosting</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0;font-family:Inter,Segoe UI,sans-serif}
body{background:radial-gradient(circle at top left,#24115c,#07111f 52%,#020617);color:#f8fafc;min-height:100vh;padding:22px 14px}
.container{max-width:620px;margin:auto}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
.logo-icon{width:50px;height:50px;border-radius:15px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#8b5cf6,#06b6d4);box-shadow:0 8px 30px #0008;font-size:22px}
.card{background:#0f172acc;backdrop-filter:blur(18px);border:1px solid #ffffff14;border-radius:20px;padding:19px;margin-bottom:18px;box-shadow:0 12px 35px #0007}
.banner{padding:22px;text-align:center;background:linear-gradient(135deg,#6d28d9aa,#0891b2aa);border-radius:20px;border:1px solid #ffffff22;margin-bottom:18px}
.banner h2{font-size:20px;margin-bottom:7px}.banner p{font-size:12px;color:#dbeafe}
.title{font-size:16px;font-weight:800;margin-bottom:15px;display:flex;gap:9px;align-items:center}
.input{width:100%;background:#020617cc;border:1px solid #ffffff18;border-radius:11px;padding:12px;color:white;margin-bottom:11px;outline:none}
.input:focus{border-color:#8b5cf6;box-shadow:0 0 0 3px #8b5cf622}
.file{display:flex;justify-content:center;align-items:center;padding:12px;border:1px dashed #ffffff30;border-radius:11px;color:#cbd5e1;margin-bottom:10px;cursor:pointer}
.btn{border:0;border-radius:11px;padding:11px 14px;color:white;font-weight:800;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:6px}
.deploy{width:100%;background:linear-gradient(135deg,#6366f1,#a855f7);box-shadow:0 8px 25px #7c3aed44}
.bot{background:#020617cc;border:1px solid #ffffff10;border-radius:16px;padding:16px;margin-bottom:12px}
.bothead{display:flex;justify-content:space-between;gap:10px;align-items:center}.botname{font-weight:800}
.badge{font-size:10px;padding:4px 9px;border-radius:999px;font-weight:900}.run{color:#34d399;background:#10b9811c;border:1px solid #10b98155}.stop{color:#fb7185;background:#ef44441c;border:1px solid #ef444455}.restart{color:#fbbf24;background:#f59e0b1c;border:1px solid #f59e0b55}
.meta{font-size:11px;color:#94a3b8;margin-top:7px}.console{background:#000b12;color:#86efac;border:1px solid #ffffff0d;border-radius:10px;padding:10px;margin-top:10px;font:11px/1.45 "Fira Code",monospace;white-space:pre-wrap;max-height:105px;overflow:auto}
.actions{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:11px}.a{font-size:10px;padding:9px 3px;border-radius:9px;color:white;text-decoration:none;text-align:center;font-weight:800}.a1{background:#10b981}.a2{background:#f59e0b}.a3{background:#8b5cf6}.a4{background:#06b6d4}.a5{background:#ef4444}
.small{font-size:10px;color:#64748b}.restore{margin-top:12px;padding:12px;border-radius:12px;background:#ffffff05;border:1px solid #ffffff0d}
@media(max-width:450px){.actions{grid-template-columns:repeat(3,1fr)}}
</style>
</head>
<body>
<div class="container">
<div class="header"><div class="logo-icon"><i class="fa-solid fa-paper-plane"></i></div><a href="/" style="color:#94a3b8"><i class="fa-solid fa-rotate-right"></i></a></div>
<div class="banner"><h2><i class="fa-solid fa-shield-halved"></i> PREMIUM BOT HOSTING</h2><p>Watchdog • Auto Restart • Isolated Environment • Live Code Editor</p></div>

<div class="card">
<div class="title"><i class="fa-solid fa-cloud-arrow-up" style="color:#a855f7"></i> Upload New Bot</div>
<form action="/upload" method="POST" enctype="multipart/form-data">
<input class="input" name="bot_name" placeholder="বটের নাম" required>
<label class="file"><i class="fa-solid fa-code"></i>&nbsp; <span id="botLabel">Choose main.py</span>
<input type="file" name="bot_file" accept=".py" required hidden onchange="pick(this,'botLabel')"></label>
<label class="file"><i class="fa-solid fa-list-check"></i>&nbsp; <span id="reqLabel">requirements.txt (Optional)</span>
<input type="file" name="req_file" accept=".txt" hidden onchange="pick(this,'reqLabel')"></label>
<button class="btn deploy" type="submit"><i class="fa-solid fa-rocket"></i> Save & Deploy</button>
</form>
<div class="restore">
<div style="font-size:12px;font-weight:800;margin-bottom:8px;color:#38bdf8">Restore Backup (.zip)</div>
<form action="/restore" method="POST" enctype="multipart/form-data" style="display:flex;gap:7px">
<input type="file" name="backup_zip" accept=".zip" required style="width:70%;font-size:10px">
<button class="btn" style="background:#0284c7;font-size:10px" type="submit">Restore</button>
</form>
</div>
</div>

<div class="card">
<div class="title"><i class="fa-solid fa-server" style="color:#38bdf8"></i> Managed Bots</div>
{% if bots %}
{% for bot in bots %}
<div class="bot">
<div class="bothead"><div class="botname"><i class="fa-solid fa-robot" style="color:#818cf8"></i> {{ bot['bot_name'] }}</div>
{% if bot['status']=='Running' %}<span class="badge run">● RUNNING</span>
{% elif bot['status']=='Restarting' %}<span class="badge restart">↻ RESTARTING</span>
{% else %}<span class="badge stop">○ STOPPED</span>{% endif %}</div>
<div class="meta">Main: {{ bot['main_file'] }}{% if bot['status']=='Running' and bot['uptime_str'] %} • Uptime: {{ bot['uptime_str'] }}{% endif %}</div>
<div class="console">{{ bot['logs'] }}</div>
<div class="actions">
{% if bot['status']=='Running' %}<a class="a a2" href="/stop/{{bot['id']}}"><i class="fa-solid fa-stop"></i><br>Stop</a>
{% else %}<a class="a a1" href="/start/{{bot['id']}}"><i class="fa-solid fa-play"></i><br>Run</a>{% endif %}
<a class="a a3" href="/edit_code/{{bot['id']}}"><i class="fa-solid fa-code"></i><br>Code</a>
<a class="a a4" href="/user_data/{{bot['id']}}"><i class="fa-solid fa-database"></i><br>Data</a>
<a class="a" style="background:#3b82f6" href="/backup/{{bot['id']}}"><i class="fa-solid fa-download"></i><br>Backup</a>
</div>
<div style="display:grid;grid-template-columns:1fr;margin-top:6px">
<a class="a a5" href="/delete/{{bot['id']}}" onclick="return confirm('এই বটটি সম্পূর্ণ ডিলিট করতে চান?')"><i class="fa-solid fa-trash"></i> Delete Bot</a>
</div>
</div>
{% endfor %}
{% else %}<div style="text-align:center;color:#64748b;padding:20px;font-size:12px">কোনো বট নেই। উপরের ফর্ম থেকে যোগ করুন।</div>{% endif %}
</div>
</div>
<script>
function pick(input,id){if(input.files.length)document.getElementById(id).textContent=input.files[0].name}
setTimeout(()=>location.reload(),15000);
</script>
</body>
</html>
"""

CODE_EDIT_TEMPLATE = """
<!doctype html><html lang="bn"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code Editor - {{ bot['bot_name'] }}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/dracula.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/python/python.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/closebrackets.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/matchbrackets.min.js"></script>
<style>
*{box-sizing:border-box}body{margin:0;background:#080b12;color:#fff;font-family:Inter,Segoe UI,sans-serif;height:100vh;display:flex;flex-direction:column}
.top{height:58px;background:#111827;border-bottom:1px solid #ffffff14;display:flex;align-items:center;justify-content:space-between;padding:0 12px;gap:8px}
.title{font-size:13px;font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.buttons{display:flex;gap:7px}
button,a{border:0;border-radius:8px;padding:8px 12px;color:#fff;text-decoration:none;font-weight:800;font-size:11px;cursor:pointer}.save{background:#10b981}.back{background:#374151}
.editor{flex:1;margin:10px;border:1px solid #ffffff12;border-radius:12px;overflow:hidden;box-shadow:0 15px 45px #0008}
.CodeMirror{height:100%;font:13px/1.55 "Fira Code",Consolas,monospace}
@media(max-width:500px){.title{max-width:45vw}.top{height:55px}.buttons button,.buttons a{padding:7px 8px;font-size:10px}}
</style></head>
<body>
<form method="POST" style="display:flex;flex:1;flex-direction:column">
<div class="top"><div class="title"><i class="fa-solid fa-code"></i> {{ filename }}</div>
<div class="buttons"><button class="save" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save</button><a class="back" href="/">Back</a></div></div>
<div class="editor"><textarea name="bot_code" id="code">{{ code_content }}</textarea></div>
</form>
<script>
const cm=CodeMirror.fromTextArea(document.getElementById("code"),{
mode:"python",theme:"dracula",lineNumbers:true,matchBrackets:true,autoCloseBrackets:true,
indentUnit:4,tabSize:4,lineWrapping:false,viewportMargin:Infinity
});
</script></body></html>
"""

USER_DATA_TEMPLATE = """
<!doctype html><html lang="bn"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Files - {{bot['bot_name']}}</title>
<style>
body{margin:0;background:#07101d;color:#fff;font-family:Inter,Segoe UI,sans-serif;padding:18px}.box{max-width:700px;margin:auto}
.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}.back{background:#475569;color:#fff;padding:8px 12px;border-radius:9px;text-decoration:none;font-size:11px;font-weight:800}
.list{background:#0f172a;border:1px solid #ffffff10;border-radius:14px;overflow:hidden}.item{display:flex;justify-content:space-between;gap:10px;padding:13px 15px;border-bottom:1px solid #ffffff08;align-items:center}.item:last-child{border:0}
.name{font-size:12px;word-break:break-all}.view{background:#06b6d4;color:#fff;text-decoration:none;border-radius:8px;padding:7px 9px;font-size:10px;font-weight:800;white-space:nowrap}
</style></head><body><div class="box"><div class="head"><h3 style="font-size:15px">📁 {{bot['bot_name']}} Files</h3><a class="back" href="/">Back</a></div>
<div class="list">{% for file in files %}<div class="item"><div class="name">📄 {{file['name']}} <small style="color:#64748b">({{file['size']}} KB)</small></div><a class="view" href="/edit_file/{{bot['id']}}?filename={{file['name']}}">Edit / View</a></div>{% else %}<div style="padding:20px;text-align:center;color:#64748b">No files found.</div>{% endfor %}</div>
</div></body></html>
"""


@app.route("/")
def index():
    start_watchdog()
    conn = get_db()
    db_bots = conn.execute("SELECT * FROM bots ORDER BY id DESC").fetchall()
    conn.close()

    bots = []
    for bot in db_bots:
        bot_dict = dict(bot)
        with process_lock:
            info = active_processes.get(bot_dict["id"])
        if info and process_alive(info["process"]):
            bot_dict["status"] = "Running"
            bot_dict["uptime_str"] = format_uptime(info["start_time"])
        else:
            bot_dict["uptime_str"] = ""
            if bot_dict["status"] == "Running":
                bot_dict["status"] = "Stopped"
                db_update(bot_dict["id"], status="Stopped", start_timestamp=0)
        bot_dict["logs"] = read_last_log(bot_dict["id"], bot_dict["logs"])
        bots.append(bot_dict)

    return render_template_string(HTML_TEMPLATE, bots=bots)


@app.route("/upload", methods=["POST"])
def upload():
    bot_name = request.form.get("bot_name", "Bot").strip()
    bot_file = request.files.get("bot_file")
    req_file = request.files.get("req_file")

    if not bot_file or not bot_file.filename or not bot_file.filename.lower().endswith(".py"):
        return redirect(url_for("index"))

    folder_name = f"bot_{int(time.time())}_{safe_name(bot_name)}"
    bot_dir = UPLOAD_FOLDER / folder_name
    bot_dir.mkdir(parents=True, exist_ok=True)

    main_filename = Path(bot_file.filename).name
    main_path = bot_dir / main_filename
    bot_file.save(main_path)

    if req_file and req_file.filename:
        req_file.save(bot_dir / "requirements.txt")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO bots
           (bot_name, folder_path, main_file, status, logs, start_timestamp, restart_count, venv_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (bot_name, str(bot_dir), main_filename, "Stopped",
         "Files uploaded. Preparing isolated environment...", 0, 0,
         str(VENV_FOLDER / folder_name))
    )
    bot_id = cursor.lastrowid
    conn.commit()
    conn.close()

    ok, msg = install_requirements(bot_dir, VENV_FOLDER / folder_name, bot_id)
    db_update(bot_id, logs=msg + " Ready to Run.")

    # Basic syntax check before first run.
    try:
        py = python_for_bot({"venv_path": str(VENV_FOLDER / folder_name)})
        result = subprocess.run(
            [py, "-m", "py_compile", str(main_path)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            db_update(bot_id, logs="Python syntax error. Open Code and fix it.")
            write_log(bot_id, result.stderr[-4000:])
        else:
            write_log(bot_id, "Python syntax check passed.")
    except Exception as e:
        write_log(bot_id, f"Syntax check skipped: {e}")

    return redirect(url_for("index"))


@app.route("/start/<int:bot_id>")
def start_bot_route(bot_id):
    ok, _ = launch_bot(bot_id, manual=True)
    return redirect(url_for("index"))


@app.route("/stop/<int:bot_id>")
def stop_bot_route(bot_id):
    stop_bot_internal(bot_id, user_requested=True)
    return redirect(url_for("index"))


@app.route("/edit_code/<int:bot_id>", methods=["GET", "POST"])
def edit_code(bot_id):
    conn = get_db()
    bot = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    conn.close()
    if not bot:
        return redirect(url_for("index"))

    file_path = Path(bot["folder_path"]) / bot["main_file"]

    if request.method == "POST":
        new_code = request.form.get("bot_code", "")
        was_running = False
        with process_lock:
            info = active_processes.get(bot_id)
            was_running = bool(info and process_alive(info["process"]))

        if was_running:
            stop_bot_internal(bot_id, user_requested=False)

        file_path.write_text(new_code, encoding="utf-8")
        write_log(bot_id, "Main code updated from editor.")
        db_update(bot_id, logs="Code saved. Bot is stopped; press Run to apply the new code.")

        return redirect(url_for("index"))

    code_content = file_path.read_text(encoding="utf-8", errors="replace") if file_path.exists() else ""
    return render_template_string(
        CODE_EDIT_TEMPLATE, bot=bot, code_content=code_content, filename=bot["main_file"]
    )


@app.route("/user_data/<int:bot_id>")
def user_data(bot_id):
    conn = get_db()
    bot = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    conn.close()
    if not bot:
        return redirect(url_for("index"))

    bot_dir = Path(bot["folder_path"])
    files_list = []
    if bot_dir.exists():
        for path in bot_dir.rglob("*"):
            if path.is_file():
                rel = path.relative_to(bot_dir).as_posix()
                files_list.append({"name": rel, "size": round(path.stat().st_size / 1024, 2)})
    files_list.sort(key=lambda x: x["name"])
    return render_template_string(USER_DATA_TEMPLATE, bot=bot, files=files_list)


def safe_bot_file(bot_dir, filename):
    root = Path(bot_dir).resolve()
    candidate = (root / filename).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("Invalid file path")
    return candidate


@app.route("/edit_file/<int:bot_id>", methods=["GET", "POST"])
def edit_file(bot_id):
    conn = get_db()
    bot = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    conn.close()
    if not bot:
        return redirect(url_for("index"))

    filename = request.args.get("filename", bot["main_file"])
    try:
        file_path = safe_bot_file(bot["folder_path"], filename)
    except ValueError:
        return redirect(url_for("user_data", bot_id=bot_id))

    if request.method == "POST":
        if file_path.suffix.lower() not in {".py", ".txt", ".json", ".yaml", ".yml", ".md", ".env"}:
            return redirect(url_for("user_data", bot_id=bot_id))
        file_path.write_text(request.form.get("bot_code", ""), encoding="utf-8")
        write_log(bot_id, f"File updated: {filename}")
        return redirect(url_for("user_data", bot_id=bot_id))

    try:
        code_content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        code_content = "[Binary file cannot be edited as text.]"

    return render_template_string(
        CODE_EDIT_TEMPLATE, bot=bot, code_content=code_content, filename=filename
    )


@app.route("/backup/<int:bot_id>")
def backup_bot(bot_id):
    conn = get_db()
    bot = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    conn.close()
    if not bot or not Path(bot["folder_path"]).exists():
        return redirect(url_for("index"))

    memory_file = BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        root = Path(bot["folder_path"])
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
    memory_file.seek(0)
    zip_name = f"{safe_name(bot['bot_name'])}_backup.zip"
    return send_file(memory_file, download_name=zip_name, as_attachment=True)


@app.route("/restore", methods=["POST"])
def restore_bot():
    backup_file = request.files.get("backup_zip")
    if not backup_file or not backup_file.filename.lower().endswith(".zip"):
        return redirect(url_for("index"))

    bot_name = Path(backup_file.filename).stem.replace("_backup", "")
    folder_name = f"bot_{int(time.time())}_{safe_name(bot_name)}"
    bot_dir = UPLOAD_FOLDER / folder_name
    bot_dir.mkdir(parents=True, exist_ok=True)

    # Protect against zip path traversal.
    with zipfile.ZipFile(backup_file, "r") as zf:
        root = bot_dir.resolve()
        for member in zf.infolist():
            target = (root / member.filename).resolve()
            if root != target and root not in target.parents:
                shutil.rmtree(bot_dir, ignore_errors=True)
                return redirect(url_for("index"))
        zf.extractall(bot_dir)

    py_files = [p for p in bot_dir.rglob("*.py") if p.is_file()]
    if not py_files:
        shutil.rmtree(bot_dir, ignore_errors=True)
        return redirect(url_for("index"))

    main_path = next((p for p in py_files if p.name.lower() == "main.py"), py_files[0])
    relative_main = main_path.relative_to(bot_dir).as_posix()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO bots
           (bot_name, folder_path, main_file, status, logs, start_timestamp, restart_count, venv_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (bot_name.replace("_", " "), str(bot_dir), relative_main, "Stopped",
         "Restored from backup. Preparing environment...", 0, 0,
         str(VENV_FOLDER / folder_name))
    )
    bot_id = cursor.lastrowid
    conn.commit()
    conn.close()

    install_requirements(bot_dir, VENV_FOLDER / folder_name, bot_id)
    db_update(bot_id, logs="Restored from backup. Ready to Run.")
    return redirect(url_for("index"))


@app.route("/delete/<int:bot_id>")
def delete_bot(bot_id):
    stop_bot_internal(bot_id, user_requested=True)

    conn = get_db()
    bot = conn.execute("SELECT folder_path, venv_path FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if bot:
        shutil.rmtree(bot["folder_path"], ignore_errors=True)
        if bot["venv_path"]:
            shutil.rmtree(bot["venv_path"], ignore_errors=True)
        try:
            (LOG_FOLDER / f"{bot_id}.log").unlink(missing_ok=True)
        except Exception:
            pass
        conn.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
        conn.commit()
    conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    start_watchdog()
    port = int(os.environ.get("PORT", 5000))
    # Single-process Flask is intentional here because the in-memory process
    # registry/watchdog supervises bot processes. Put a reverse proxy in front
    # in production rather than running multiple Flask workers.
    app.run(host="0.0.0.0", port=port, threaded=True)
