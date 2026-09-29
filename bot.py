import os
import sys
import sqlite3
import subprocess
import time
import shutil
import zipfile
import signal
import threading
import atexit
from io import BytesIO
from flask import Flask, render_template_string, request, redirect, url_for, send_file, flash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-key')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# Per-bot process locks prevent double-start races.
process_locks = {}
process_locks_guard = threading.Lock()

def get_process_lock(bot_id):
    with process_locks_guard:
        return process_locks.setdefault(bot_id, threading.Lock())

def terminate_process(proc, timeout=5):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass

def cleanup_all_processes():
    for item in list(active_processes.values()):
        terminate_process(item.get('process'))

atexit.register(cleanup_all_processes)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'user_bots')
DATABASE = os.path.join(BASE_DIR, 'platform.db')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Active processes dictionary: {bot_id: {'process': proc_obj, 'start_time': timestamp}}
active_processes = {}

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            main_file TEXT NOT NULL,
            status TEXT DEFAULT 'Stopped',
            logs TEXT DEFAULT 'Ready to run...',
            start_timestamp REAL DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Multi-Bot Telegram Hosting Panel</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>

        /* ===== Premium Hosting UI v2 ===== */
        :root{
          --bg:#070a12; --panel:#0d1322; --panel2:#111a2c; --line:rgba(255,255,255,.09);
          --text:#f7f9ff; --muted:#94a3b8; --brand:#7c5cff; --brand2:#25c7ff;
          --ok:#19d39a; --warn:#ffb020; --danger:#ff5571;
        }
        *{box-sizing:border-box}
        html{scroll-behavior:smooth}
        body{
          background:
            radial-gradient(900px 500px at 10% -10%,rgba(124,92,255,.20),transparent 60%),
            radial-gradient(700px 450px at 100% 0%,rgba(37,199,255,.12),transparent 55%),
            linear-gradient(180deg,#070a12,#090d17 60%,#060810);
          color:var(--text); min-height:100vh; padding:24px 14px;
        }
        body:before{
         content:"";position:fixed;inset:0;pointer-events:none;opacity:.18;
         background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),
         linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);
         background-size:32px 32px;
        }
        .container{max-width:920px;margin:auto;position:relative;z-index:1}
        .header{
         display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;
         padding:10px 2px;
        }
        .logo-icon{
         width:48px;height:48px;border-radius:15px;display:flex;align-items:center;justify-content:center;
         background:linear-gradient(135deg,var(--brand),var(--brand2));box-shadow:0 12px 35px rgba(124,92,255,.32);
        }
        .premium-banner{
         position:relative;overflow:hidden;text-align:left;padding:26px;border-radius:24px;margin-bottom:18px;
         background:linear-gradient(135deg,rgba(20,27,47,.92),rgba(13,18,31,.88));
         border:1px solid var(--line);box-shadow:0 25px 70px rgba(0,0,0,.35);
        }
        .premium-banner:after{content:"";position:absolute;width:240px;height:240px;right:-100px;top:-130px;border-radius:50%;
         background:radial-gradient(circle,rgba(37,199,255,.25),transparent 65%)}
        .premium-banner h2{font-size:22px;margin:0 0 7px;font-weight:850}
        .premium-banner p{color:var(--muted);font-size:13px}
        .card{
         background:linear-gradient(180deg,rgba(17,26,44,.92),rgba(10,15,27,.94));
         border:1px solid var(--line);border-radius:22px;padding:20px;margin-bottom:18px;
         box-shadow:0 18px 50px rgba(0,0,0,.25);
        }
        .card-title{font-size:16px;margin-bottom:15px;font-weight:800}
        .input-box{
         width:100%;padding:13px 14px;margin-bottom:11px;border-radius:13px;
         border:1px solid var(--line);background:#080d18;color:#fff;outline:0;
        }
        .input-box:focus{border-color:rgba(124,92,255,.8);box-shadow:0 0 0 4px rgba(124,92,255,.10)}
        .btn-file{
         min-height:50px;border-radius:13px;border:1px dashed rgba(124,92,255,.5);
         background:rgba(124,92,255,.06);color:#dbe4ff;
        }
        .btn-deploy{
         width:100%;padding:13px;border:0;border-radius:13px;color:white;font-weight:850;
         background:linear-gradient(135deg,#6d5cff,#1fbce9);box-shadow:0 10px 28px rgba(88,92,255,.24);
         cursor:pointer;
        }
        .bot-card{
         background:linear-gradient(180deg,#0d1525,#0a101c);border:1px solid var(--line);
         border-radius:18px;padding:17px;margin-bottom:12px;transition:.22s;
        }
        .bot-card:hover{transform:translateY(-2px);border-color:rgba(124,92,255,.32)}
        .status-running{background:rgba(25,211,154,.10);color:#4ff0ba;border-color:rgba(25,211,154,.22)}
        .status-stopped{background:rgba(255,85,113,.10);color:#ff7c91;border-color:rgba(255,85,113,.22)}
        .console-box{
         background:#050810;color:#62e6ad;border:1px solid rgba(255,255,255,.06);
         border-radius:12px;min-height:55px;max-height:130px;padding:11px;
        }
        .btn-act{border:1px solid rgba(255,255,255,.08);border-radius:10px!important}
        .btn-run{background:#0b9f75}.btn-stop-bot{background:#a36a00}
        .btn-code-edit{background:#6346d9}.btn-data{background:#087f9d}.btn-backup{background:#245fba}.btn-del{background:#b62d45}
    </style>
</head>
<body>

<div class="container">
    <div class="header">
        <div class="logo">
            <div class="logo-icon"><i class="fa-solid fa-paper-plane"></i></div>
        </div>
        <i class="fa-solid fa-rotate-right" style="font-size: 20px; cursor: pointer; color: #94a3b8;" onclick="location.reload()"></i>
    </div>

    <div class="premium-banner">
        <h2><i class="fa-solid fa-crown" style="color:#f59e0b;"></i> PREMIUM HOSTING PANEL</h2>
        <p>Manage, Backup & Edit Your Python Bots Live</p>
    </div>

    <!-- Upload Bot -->
    <div class="card">
        <div class="card-title"><i class="fa-solid fa-cloud-arrow-up" style="color:#a855f7;"></i> Upload New Bot</div>
        
        <form action="/upload" method="POST" enctype="multipart/form-data">
            <input type="text" name="bot_name" class="input-box" placeholder="বটের নাম লিখুন (যেমন: Bot One)" required>
            
            <div class="file-input-wrapper">
                <label for="bot_file" class="btn-file" id="botLabel"><i class="fa-solid fa-code"></i> Choose main.py</label>
                <input type="file" id="bot_file" name="bot_file" accept=".py" required style="display:none;" onchange="updateLabel(this, 'botLabel', 'Choose main.py')">
            </div>

            <div class="file-input-wrapper">
                <label for="req_file" class="btn-file" id="reqLabel"><i class="fa-solid fa-list-check"></i> Choose requirements.txt (Optional)</label>
                <input type="file" id="req_file" name="req_file" accept=".txt" style="display:none;" onchange="updateLabel(this, 'reqLabel', 'Choose requirements.txt (Optional)')">
            </div>

            <button type="submit" class="btn-deploy">Save & Deploy Bot</button>
        </form>

        <!-- Restore Section -->
        <div class="restore-box">
            <div style="font-size: 13px; font-weight: 600; margin-bottom: 8px; color: #38bdf8;"><i class="fa-solid fa-rotate-left"></i> Restore Bot from Backup (.zip)</div>
            <form action="/restore" method="POST" enctype="multipart/form-data" style="display: flex; gap: 8px;">
                <input type="file" name="backup_zip" accept=".zip" required style="font-size: 11px; color: #94a3b8; width: 70%;">
                <button type="submit" style="padding: 6px 12px; background: #0284c7; border: none; border-radius: 8px; color: white; font-weight: bold; font-size: 11px; cursor: pointer;">Restore</button>
            </form>
        </div>
    </div>

    <!-- Managed Bots -->
    <div class="card">
        <div class="card-title"><i class="fa-solid fa-server" style="color:#38bdf8;"></i> Managed Bots List</div>
        
        {% if bots %}
            {% for bot in bots %}
            <div class="bot-card">
                <div class="bot-header">
                    <div class="bot-title"><i class="fa-solid fa-robot" style="color:#818cf8;"></i> {{ bot['bot_name'] }}</div>
                    {% if bot['status'] == 'Running' %}
                        <span class="status-badge status-running">● RUNNING</span>
                    {% else %}
                        <span class="status-badge status-stopped">○ STOPPED</span>
                    {% endif %}
                </div>

                <div style="font-size: 12px; color: #94a3b8;">Main File: {{ bot['main_file'] }}</div>
                
                {% if bot['status'] == 'Running' and bot['uptime_str'] %}
                    <div style="font-size: 11px; color: #34d399; margin-top: 4px;"><i class="fa-solid fa-clock"></i> Uptime: {{ bot['uptime_str'] }}</div>
                {% endif %}
                
                <div class="console-box">{{ bot['logs'] }}</div>

                <div class="bot-actions">
                    {% if bot['status'] == 'Running' %}
                        <a href="/stop/{{ bot['id'] }}" class="btn-act btn-stop-bot"><i class="fa-solid fa-pause"></i> Stop</a>
                    {% else %}
                        <a href="/start/{{ bot['id'] }}" class="btn-act btn-run"><i class="fa-solid fa-play"></i> Run</a>
                    {% endif %}
                    <a href="/edit_code/{{ bot['id'] }}" class="btn-act btn-code-edit"><i class="fa-solid fa-code"></i> Code</a>
                    <a href="/user_data/{{ bot['id'] }}" class="btn-act btn-data"><i class="fa-solid fa-database"></i> Data</a>
                    <a href="/backup/{{ bot['id'] }}" class="btn-act btn-backup"><i class="fa-solid fa-download"></i> Backup</a>
                    <a href="/delete/{{ bot['id'] }}" class="btn-act btn-del" onclick="return confirm('এই বটটি সম্পূর্ণ ডিলিট করতে চান?')"><i class="fa-solid fa-trash"></i> Del</a>
                </div>
            </div>
            {% endfor %}
        {% else %}
            <div style="text-align: center; color: #64748b; font-size: 13px; padding: 20px;">
                কোনো বট আপলোড করা হয়নি। ওপরের ফর্ম ব্যবহার করে বট যোগ করুন!
            </div>
        {% endif %}
    </div>
</div>

<script>
function updateLabel(input, labelId, defaultText) {
    const label = document.getElementById(labelId);
    if (input.files.length > 0) {
        label.innerText = "Selected: " + input.files[0].name;
    } else {
        label.innerText = defaultText;
    }
}
</script>

</body>
</html>
"""
CODE_EDIT_TEMPLATE = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Code Editor - {{ bot['bot_name'] }}</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>

        :root{--bg:#070a12;--panel:#0d1322;--line:rgba(255,255,255,.09);--brand:#7c5cff;--text:#eef3ff}
        *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,rgba(124,92,255,.18),transparent 40%),#070a12;color:var(--text);height:100vh;display:flex;flex-direction:column}
        .editor-header{height:62px;display:flex;justify-content:space-between;align-items:center;background:rgba(13,19,34,.96);border-bottom:1px solid var(--line);padding:0 14px;font-family:Inter,system-ui,sans-serif}
        .editor-title{font-size:14px;font-weight:800;display:flex;align-items:center;gap:9px}
        .action-btns{display:flex;gap:8px}.btn-top{padding:9px 13px;border-radius:10px;border:1px solid var(--line);color:white;text-decoration:none;font-size:12px;font-weight:800;cursor:pointer}
        .btn-back{background:#141c2c}.btn-save{background:linear-gradient(135deg,#13b77d,#0d8fca);border:0}
        .editor-container{display:flex;flex:1;margin:12px;border:1px solid var(--line);border-radius:15px;overflow:hidden;background:#060912;min-height:0}
        .line-numbers{background:#090e19;color:#4b5a72;padding:14px 10px;text-align:right;font:13px/1.55 "Fira Code",monospace;user-select:none;border-right:1px solid var(--line);min-width:52px;overflow:hidden}
        .code-area{width:100%;flex:1;background:transparent;border:0;outline:0;padding:14px;color:#9bdcff;font:13px/1.55 "Fira Code",monospace;resize:none;white-space:pre;overflow:auto;tab-size:4}
        .code-area:focus{box-shadow:inset 0 0 0 1px rgba(124,92,255,.25)}
        @media(max-width:600px){.editor-header{height:auto;min-height:62px;gap:8px}.editor-title{max-width:45vw;overflow:hidden;white-space:nowrap}.btn-top{padding:8px 9px}.editor-container{margin:7px}.code-area{font-size:12px}}
    </style>
</head>
<body>
    <form method="POST" style="display: flex; flex-direction: column; flex: 1;">
        <div class="editor-header">
            <div class="editor-title"><i class="fa-solid fa-code" style="color:#a855f7;"></i> Editing: {{ filename }}</div>
            <div class="action-btns">
                <button type="submit" class="btn-top btn-save"><i class="fa-solid fa-floppy-disk"></i> Save Code</button>
                <a href="/" class="btn-top btn-back"><i class="fa-solid fa-arrow-left"></i> Back</a>
            </div>
        </div>

        <div class="editor-container">
            <div id="lineNumbers" class="line-numbers">1</div>
            <textarea name="bot_code" id="codeArea" class="code-area" required spellcheck="false" oninput="updateLines()" onscroll="syncScroll()">{{ code_content }}</textarea>
        </div>
    </form>

    <script>
        const codeArea = document.getElementById('codeArea');
        const lineNumbers = document.getElementById('lineNumbers');

        function updateLines() {
            const lines = codeArea.value.split('\\n').length;
            let numbersStr = '';
            for (let i = 1; i <= lines; i++) { numbersStr += i + '<br>'; }
            lineNumbers.innerHTML = numbersStr;
        }

        function syncScroll() { lineNumbers.scrollTop = codeArea.scrollTop; }
        codeArea.addEventListener('scroll', syncScroll);
        window.onload = updateLines;
        codeArea.addEventListener('keydown', function(e) {
            if (e.key === 'Tab') {
                e.preventDefault();
                const s=this.selectionStart, epos=this.selectionEnd;
                this.value=this.value.slice(0,s)+'    '+this.value.slice(epos);
                this.selectionStart=this.selectionEnd=s+4;
                updateLines();
            }
        });
    </script>
</body>
</html>
"""

USER_DATA_TEMPLATE = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>User Data & File Manager</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
        body { background: #0f172a; color: white; padding: 20px; }
        .container { max-width: 700px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 12px; }
        .file-list { background: #1e293b; border-radius: 12px; overflow: hidden; border: 1px solid rgba(255,255,255,0.08); }
        .file-item { display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .file-item:last-child { border-bottom: none; }
        .file-name { font-size: 14px; font-weight: 500; display: flex; align-items: center; gap: 10px; color: #e2e8f0; }
        .btn-view { padding: 6px 14px; background: #3b82f6; border-radius: 8px; color: white; text-decoration: none; font-size: 12px; font-weight: 600; }
        .btn-back { padding: 8px 16px; background: #475569; border-radius: 8px; color: white; text-decoration: none; font-size: 13px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h3><i class="fa-solid fa-folder-open" style="color:#06b6d4;"></i> Files & User Data ({{ bot['bot_name'] }})</h3>
            <a href="/" class="btn-back"><i class="fa-solid fa-arrow-left"></i> Back</a>
        </div>

        <div class="file-list">
            {% if files %}
                {% for file in files %}
                <div class="file-item">
                    <div class="file-name">
                        <i class="fa-solid fa-file-code" style="color:#a855f7;"></i> {{ file['name'] }}
                        <span style="font-size: 11px; color: #64748b;">({{ file['size'] }} KB)</span>
                    </div>
                    <a href="/edit_file/{{ bot['id'] }}?filename={{ file['name'] }}" class="btn-view">Edit / View Data</a>
                </div>
                {% endfor %}
            {% else %}
                <div style="padding: 20px; text-align: center; color: #64748b;">কোনো ফাইল বা ডাটাবেস খুঁজে পাওয়া যায়নি।</div>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

def format_uptime(start_timestamp):
    if not start_timestamp:
        return ""
    diff = int(time.time() - start_timestamp)
    days = diff // 86400
    hours = (diff % 86400) // 3600
    minutes = (diff % 3600) // 60
    seconds = diff % 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m {seconds}s")
    return " ".join(parts)

@app.route('/')
def index():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bots ORDER BY id DESC')
    db_bots = cursor.fetchall()

    bots = []
    for bot in db_bots:
        bot_dict = dict(bot)
        if bot_dict['status'] == 'Running' and bot_dict['id'] in active_processes:
            if active_processes[bot_dict['id']]['process'].poll() is not None:
                cursor.execute('UPDATE bots SET status = "Stopped", start_timestamp = 0 WHERE id = ?', (bot_dict['id'],))
                conn.commit()
                bot_dict['status'] = 'Stopped'
                bot_dict['uptime_str'] = ''
            else:
                start_ts = active_processes[bot_dict['id']]['start_time']
                bot_dict['uptime_str'] = format_uptime(start_ts)
        else:
            bot_dict['uptime_str'] = ''
        bots.append(bot_dict)

    conn.close()
    return render_template_string(HTML_TEMPLATE, bots=bots)

@app.route('/upload', methods=['POST'])
def upload():
    bot_name = request.form.get('bot_name')
    bot_file = request.files.get('bot_file')
    req_file = request.files.get('req_file')

    if not bot_file or not bot_file.filename:
        return redirect(url_for('index'))

    folder_name = f"bot_{int(time.time())}_{bot_name.replace(' ', '_')}"
    bot_dir = os.path.join(UPLOAD_FOLDER, folder_name)
    os.makedirs(bot_dir, exist_ok=True)

    main_filename = bot_file.filename
    main_path = os.path.join(bot_dir, main_filename)
    bot_file.save(main_path)

    log_msg = "Files uploaded successfully.\n"

    if req_file and req_file.filename:
        req_path = os.path.join(bot_dir, 'requirements.txt')
        req_file.save(req_path)
        try:
            log_msg += "Installing requirements...\n"
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], capture_output=True, text=True)
            log_msg += "Packages installed successfully!\n"
        except Exception as e:
            log_msg += f"Install error: {str(e)}\n"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO bots (bot_name, folder_path, main_file, status, logs, start_timestamp) VALUES (?, ?, ?, ?, ?, ?)',
                   (bot_name, bot_dir, main_filename, 'Stopped', log_msg + "Ready to Run.", 0))
    conn.commit()
    conn.close()

    return redirect(url_for('index'))

@app.route('/start/<int:bot_id>')
def start_bot(bot_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bots WHERE id = ?', (bot_id,))
    bot = cursor.fetchone()

    if bot:
        bot_dir = bot['folder_path']
        main_file = bot['main_file']

        if bot_id not in active_processes or active_processes[bot_id]['process'].poll() is not None:
            proc = subprocess.Popen(
                [sys.executable, "-u", main_file],
                cwd=bot_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True
            )
            current_time = time.time()
            active_processes[bot_id] = {'process': proc, 'start_time': current_time}

            cursor.execute('UPDATE bots SET status = "Running", logs = "Bot is running live!", start_timestamp = ? WHERE id = ?', (current_time, bot_id))
            conn.commit()

    conn.close()
    return redirect(url_for('index'))

@app.route('/stop/<int:bot_id>')
def stop_bot(bot_id):
    if bot_id in active_processes:
        terminate_process(active_processes[bot_id]['process'])
        del active_processes[bot_id]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE bots SET status = "Stopped", logs = "Bot stopped by user.", start_timestamp = 0 WHERE id = ?', (bot_id,))
    conn.commit()
    conn.close()

    return redirect(url_for('index'))

@app.route('/edit_code/<int:bot_id>', methods=['GET', 'POST'])
def edit_code(bot_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bots WHERE id = ?', (bot_id,))
    bot = cursor.fetchone()
    conn.close()

    if not bot:
        return redirect(url_for('index'))

    file_path = os.path.join(bot['folder_path'], bot['main_file'])

    if request.method == 'POST':
        new_code = request.form.get('bot_code')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_code)

        if bot['status'] == 'Running':
            stop_bot(bot_id)

        return redirect(url_for('index'))

    code_content = ""
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            code_content = f.read()

    return render_template_string(CODE_EDIT_TEMPLATE, bot=bot, code_content=code_content, filename=bot['main_file'])

# --- USER DATA, BACKUP & RESTORE ROUTES ---

@app.route('/user_data/<int:bot_id>')
def user_data(bot_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bots WHERE id = ?', (bot_id,))
    bot = cursor.fetchone()
    conn.close()

    if not bot:
        return redirect(url_for('index'))

    bot_dir = bot['folder_path']
    files_list = []
    
    if os.path.exists(bot_dir):
        for root, _, files in os.walk(bot_dir):
            for file in files:
                rel_path = os.path.relpath(os.path.join(root, file), bot_dir)
                full_path = os.path.join(root, file)
                size_kb = round(os.path.getsize(full_path) / 1024, 2)
                files_list.append({'name': rel_path, 'size': size_kb})

    return render_template_string(USER_DATA_TEMPLATE, bot=bot, files=files_list)

@app.route('/edit_file/<int:bot_id>', methods=['GET', 'POST'])
def edit_file(bot_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bots WHERE id = ?', (bot_id,))
    bot = cursor.fetchone()
    conn.close()

    filename = request.args.get('filename', bot['main_file'])
    file_path = os.path.join(bot['folder_path'], filename)

    if request.method == 'POST':
        new_code = request.form.get('bot_code')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_code)
        return redirect(url_for('user_data', bot_id=bot_id))

    code_content = ""
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                code_content = f.read()
        except Exception:
            code_content = "[Binary/Database File - Cannot edit directly as text]"

    return render_template_string(CODE_EDIT_TEMPLATE, bot=bot, code_content=code_content, filename=filename)

@app.route('/backup/<int:bot_id>')
def backup_bot(bot_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bots WHERE id = ?', (bot_id,))
    bot = cursor.fetchone()
    conn.close()

    if not bot or not os.path.exists(bot['folder_path']):
        return redirect(url_for('index'))

    memory_file = BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(bot['folder_path']):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, bot['folder_path'])
                zf.write(full_path, rel_path)
                
    memory_file.seek(0)
    zip_name = f"{bot['bot_name']}_backup.zip".replace(' ', '_')
    return send_file(memory_file, download_name=zip_name, as_attachment=True)

@app.route('/restore', methods=['POST'])
def restore_bot():
    backup_file = request.files.get('backup_zip')
    if not backup_file or not backup_file.filename.endswith('.zip'):
        return redirect(url_for('index'))

    bot_name = os.path.splitext(backup_file.filename)[0].replace('_backup', '')
    folder_name = f"bot_{int(time.time())}_{bot_name}"
    bot_dir = os.path.join(UPLOAD_FOLDER, folder_name)
    os.makedirs(bot_dir, exist_ok=True)

    with zipfile.ZipFile(backup_file, 'r') as zf:
        for member in zf.infolist():
            target = os.path.abspath(os.path.join(bot_dir, member.filename))
            if not target.startswith(os.path.abspath(bot_dir) + os.sep):
                raise ValueError("Unsafe archive path")
        zf.extractall(bot_dir)

    main_file = "main.py"
    files_in_dir = os.listdir(bot_dir)
    py_files = [f for f in files_in_dir if f.endswith('.py')]
    if py_files:
        main_file = py_files[0]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO bots (bot_name, folder_path, main_file, status, logs, start_timestamp) VALUES (?, ?, ?, ?, ?, ?)',
                   (bot_name.replace('_', ' '), bot_dir, main_file, 'Stopped', 'Restored from Backup zip.', 0))
    conn.commit()
    conn.close()

    return redirect(url_for('index'))

@app.route('/delete/<int:bot_id>')
def delete_bot(bot_id):
    stop_bot(bot_id)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT folder_path FROM bots WHERE id = ?', (bot_id,))
    bot = cursor.fetchone()

    if bot:
        bot_dir = bot['folder_path']
        if os.path.exists(bot_dir):
            shutil.rmtree(bot_dir, ignore_errors=True)

        cursor.execute('DELETE FROM bots WHERE id = ?', (bot_id,))
        conn.commit()

    conn.close()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
    