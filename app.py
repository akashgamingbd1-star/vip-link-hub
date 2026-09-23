import os, sys, signal, sqlite3, subprocess, threading, time, shutil, re
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory
import psutil

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
BOTS = DATA / "bots"
DB = DATA / "hosting.db"
LOGS = DATA / "logs"
MAX_BOTS = 16
MAX_UPLOAD_MB = 10
ADMIN_PASSWORD = os.environ.get("HOSTING_ADMIN_PASSWORD", "ChangeMe123!")

DATA.mkdir(exist_ok=True)
BOTS.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("HOSTING_SECRET_KEY", "change-this-secret-key")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

processes = {}
process_lock = threading.Lock()

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.execute("""CREATE TABLE IF NOT EXISTS bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        folder TEXT NOT NULL UNIQUE,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.commit()
    con.close()

def safe_name(name):
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip())
    return name[:48].strip("-_.") or "my-bot"

def bot_row(bot_id):
    con = db()
    row = con.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
    con.close()
    return row

def bot_path(row):
    return BOTS / row["folder"]

def log_path(row):
    return LOGS / f'{row["folder"]}.log'

def is_running(bot_id):
    with process_lock:
        p = processes.get(bot_id)
        return bool(p and p.poll() is None)

def stop_process(bot_id):
    with process_lock:
        p = processes.get(bot_id)
        if not p or p.poll() is not None:
            processes.pop(bot_id, None)
            return False
        try:
            parent = psutil.Process(p.pid)
            children = parent.children(recursive=True)
            for c in children:
                try: c.terminate()
                except Exception: pass
            try: parent.terminate()
            except Exception: pass
            gone, alive = psutil.wait_procs([parent] + children, timeout=4)
            for c in alive:
                try: c.kill()
                except Exception: pass
        except Exception:
            try: p.kill()
            except Exception: pass
        processes.pop(bot_id, None)
        return True

def reader_thread(bot_id, pipe, path):
    try:
        with open(path, "a", encoding="utf-8", errors="ignore") as out:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                out.write(line)
                out.flush()
    except Exception:
        pass

def run_process(row):
    folder = bot_path(row)
    bot_file = folder / "bot.py"
    req_file = folder / "requirements.txt"
    if not bot_file.exists():
        raise RuntimeError("bot.py is missing.")

    # Optional per-bot virtual environment.
    venv = folder / ".venv"
    python_bin = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if req_file.exists() and req_file.read_text(encoding="utf-8", errors="ignore").strip():
        if not python_bin.exists():
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=180)
            subprocess.run([str(python_bin), "-m", "pip", "install", "-r", str(req_file)],
                           check=True, timeout=600)
    if not python_bin.exists():
        python_bin = sys.executable

    lp = log_path(row)
    lp.parent.mkdir(exist_ok=True)
    with open(lp, "a", encoding="utf-8") as log:
        log.write(f"\n--- START {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log.flush()

    kwargs = dict(
        cwd=str(folder),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    p = subprocess.Popen([str(python_bin), str(bot_file)], **kwargs)
    with process_lock:
        processes[row["id"]] = p
    threading.Thread(target=reader_thread, args=(row["id"], p.stdout, lp), daemon=True).start()
    return p.pid

def bot_stats(row):
    running = is_running(row["id"])
    cpu = 0.0
    mem = 0
    pid = None
    if running:
        with process_lock:
            p = processes.get(row["id"])
        if p and p.poll() is None:
            pid = p.pid
            try:
                proc = psutil.Process(pid)
                cpu = proc.cpu_percent(interval=0.05)
                mem = proc.memory_info().rss / 1024 / 1024
            except Exception:
                pass
    folder = bot_path(row)
    total = 0
    for f in folder.rglob("*"):
        if f.is_file() and ".venv" not in f.parts:
            try: total += f.stat().st_size
            except OSError: pass
    return {"running": running, "cpu": round(cpu,1), "memory": round(mem,1), "pid": pid,
            "storage": round(total/1024/1024,2)}

@app.context_processor
def inject():
    return {"max_bots": MAX_BOTS}

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("dashboard"))
        flash("Wrong admin password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.before_request
def guard():
    if request.endpoint not in ("login", "static") and not session.get("admin"):
        return redirect(url_for("login"))

@app.route("/")
def dashboard():
    con = db()
    rows = con.execute("SELECT * FROM bots ORDER BY id DESC").fetchall()
    con.close()
    bots = [{"row": dict(r), "stats": bot_stats(r)} for r in rows]
    return render_template("dashboard.html", bots=bots, bot_count=len(bots))

@app.route("/bot/create", methods=["POST"])
def create_bot():
    name = safe_name(request.form.get("name",""))
    con = db()
    count = con.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
    if count >= MAX_BOTS:
        con.close()
        flash(f"Maximum {MAX_BOTS} bot slots are available.", "error")
        return redirect(url_for("dashboard"))
    if not name:
        con.close()
        flash("Enter a bot name.", "error")
        return redirect(url_for("dashboard"))
    folder = f"{int(time.time())}_{name.lower()}"
    try:
        con.execute("INSERT INTO bots(name,folder) VALUES(?,?)", (name, folder))
        con.commit()
        row = con.execute("SELECT * FROM bots WHERE folder=?", (folder,)).fetchone()
        con.close()
        bp = bot_path(row); bp.mkdir(parents=True, exist_ok=True)
        (bp/"bot.py").write_text("# Paste your Telegram bot code here.\n", encoding="utf-8")
        (bp/"requirements.txt").write_text("# Example: python-telegram-bot==21.6\n", encoding="utf-8")
        flash("Bot slot created. Upload or edit its two files.", "success")
    except sqlite3.IntegrityError:
        con.close()
        flash("That bot name already exists.", "error")
    return redirect(url_for("dashboard"))

@app.route("/bot/<int:bot_id>/files", methods=["POST"])
def upload_files(bot_id):
    row = bot_row(bot_id)
    if not row: return "Not found", 404
    bp = bot_path(row); bp.mkdir(parents=True, exist_ok=True)
    for field, expected in (("bot_file","bot.py"), ("req_file","requirements.txt")):
        f = request.files.get(field)
        if f and f.filename:
            # Only the two supported runtime files are accepted.
            if expected == "bot.py" and f.filename.lower() != "bot.py":
                flash("The first file must be named bot.py.", "error"); return redirect(url_for("edit_bot", bot_id=bot_id))
            if expected == "requirements.txt" and f.filename.lower() != "requirements.txt":
                flash("The second file must be named requirements.txt.", "error"); return redirect(url_for("edit_bot", bot_id=bot_id))
            f.save(bp/expected)
    flash("Files uploaded successfully.", "success")
    return redirect(url_for("edit_bot", bot_id=bot_id))

@app.route("/bot/<int:bot_id>/edit", methods=["GET","POST"])
def edit_bot(bot_id):
    row = bot_row(bot_id)
    if not row: return "Not found", 404
    bp = bot_path(row); bp.mkdir(parents=True, exist_ok=True)
    if request.method == "POST":
        new_name = safe_name(request.form.get("name", row["name"]))
        code = request.form.get("bot_code","")
        req = request.form.get("requirements","")
        if is_running(bot_id):
            flash("Stop the bot before editing runtime files.", "error")
            return redirect(url_for("edit_bot", bot_id=bot_id))
        con = db()
        try:
            con.execute("UPDATE bots SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_name,bot_id))
            con.commit()
        finally:
            con.close()
        (bp/"bot.py").write_text(code, encoding="utf-8")
        (bp/"requirements.txt").write_text(req, encoding="utf-8")
        flash("Changes saved.", "success")
        return redirect(url_for("edit_bot", bot_id=bot_id))
    code = (bp/"bot.py").read_text(encoding="utf-8", errors="ignore") if (bp/"bot.py").exists() else ""
    req = (bp/"requirements.txt").read_text(encoding="utf-8", errors="ignore") if (bp/"requirements.txt").exists() else ""
    return render_template("editor.html", bot=dict(row), code=code, requirements=req, stats=bot_stats(row))

@app.post("/bot/<int:bot_id>/run")
def run_bot(bot_id):
    row = bot_row(bot_id)
    if not row: return "Not found", 404
    if is_running(bot_id):
        flash("Bot is already running.", "info")
        return redirect(url_for("dashboard"))
    try:
        pid = run_process(row)
        flash(f"{row['name']} started (PID {pid}).", "success")
    except Exception as e:
        flash(f"Start failed: {e}", "error")
    return redirect(url_for("dashboard"))

@app.post("/bot/<int:bot_id>/stop")
def stop_bot(bot_id):
    row = bot_row(bot_id)
    if row: stop_process(bot_id)
    flash("Bot stopped.", "success")
    return redirect(url_for("dashboard"))

@app.post("/bot/<int:bot_id>/delete")
def delete_bot(bot_id):
    row = bot_row(bot_id)
    if not row: return redirect(url_for("dashboard"))
    stop_process(bot_id)
    shutil.rmtree(bot_path(row), ignore_errors=True)
    try: log_path(row).unlink()
    except FileNotFoundError: pass
    con = db(); con.execute("DELETE FROM bots WHERE id=?", (bot_id,)); con.commit(); con.close()
    flash("Bot deleted.", "success")
    return redirect(url_for("dashboard"))

@app.get("/api/bot/<int:bot_id>/logs")
def api_logs(bot_id):
    row = bot_row(bot_id)
    if not row: return jsonify({"error":"not found"}),404
    p = log_path(row)
    if not p.exists(): return jsonify({"logs":"No logs yet."})
    data = p.read_text(encoding="utf-8", errors="ignore")
    return jsonify({"logs": data[-30000:]})

@app.get("/api/status")
def api_status():
    con = db(); rows = con.execute("SELECT * FROM bots ORDER BY id DESC").fetchall(); con.close()
    return jsonify({"bots":[{"id":r["id"],"name":r["name"],**bot_stats(r)} for r in rows]})

@app.errorhandler(413)
def too_large(_):
    flash(f"File too large. Maximum upload size is {MAX_UPLOAD_MB} MB.", "error")
    return redirect(url_for("dashboard"))

init_db()

if __name__ == "__main__":
    print("Premium Bot Hosting Panel running at http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","5000")), debug=False)
