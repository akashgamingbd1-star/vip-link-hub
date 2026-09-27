import os, time, sqlite3, secrets, shutil, signal, subprocess, sys
from pathlib import Path
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

BASE=Path(__file__).resolve().parent; DATA=BASE/'data'; BOTS=DATA/'bots'; DB=DATA/'platform.db'
DATA.mkdir(exist_ok=True); BOTS.mkdir(exist_ok=True)
app=Flask(__name__); app.secret_key=os.environ.get('SECRET_KEY',secrets.token_hex(32)); app.config['MAX_CONTENT_LENGTH']=20*1024*1024
PROCS={}

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,email TEXT UNIQUE,pw TEXT,ref TEXT UNIQUE,referred_by INTEGER,balance REAL DEFAULT 0,coins REAL DEFAULT 0,status TEXT DEFAULT 'active',created REAL);
    CREATE TABLE IF NOT EXISTS packages(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,months INTEGER,bots INTEGER,price REAL,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS subs(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,package_id INTEGER,expires REAL,bot_limit INTEGER,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,method TEXT,amount REAL,txn TEXT,status TEXT DEFAULT 'pending',created REAL);
    CREATE TABLE IF NOT EXISTS bots(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,name TEXT,folder TEXT,status TEXT DEFAULT 'Stopped',created REAL);
    ''')
    defaults={'site':'BotCloud','support':'https://t.me/your_support','bkash':'01XXXXXXXXX','nagad':'01XXXXXXXXX','min_deposit':'100','commission':'2'}
    for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)',(k,v))
    if not c.execute("SELECT id FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users(username,email,pw,ref,status,created) VALUES(?,?,?,?,?,?)",('admin','admin@example.com',generate_password_hash('admin123'),'ADMIN','active',time.time()))
    if c.execute('SELECT count(*) n FROM packages').fetchone()['n']==0:
        c.executemany('INSERT INTO packages(name,months,bots,price) VALUES(?,?,?,?)',[('Starter',1,2,199),('Pro',3,3,499),('Business',12,10,1499)])
    c.commit(); c.close()
init()

def S(k):
    c=db(); r=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone(); c.close(); return r['v'] if r else ''
def user():
    if not session.get('uid'): return None
    c=db(); r=c.execute('SELECT * FROM users WHERE id=?',(session['uid'],)).fetchone(); c.close(); return r
def loginreq(f):
    @wraps(f)
    def w(*a,**kw):
        u=user()
        if not u or u['status']!='active': session.clear(); return redirect(url_for('login'))
        return f(*a,**kw)
    return w
def adminreq(f):
    @wraps(f)
    def w(*a,**kw):
        if not user() or user()['username']!='admin': abort(403)
        return f(*a,**kw)
    return w
def sub(uid):
    c=db(); r=c.execute('''SELECT s.*,p.name FROM subs s JOIN packages p ON p.id=s.package_id WHERE s.user_id=? AND s.active=1 AND s.expires>? ORDER BY s.expires DESC LIMIT 1''',(uid,time.time())).fetchone()
    if not r: c.execute('UPDATE subs SET active=0 WHERE user_id=? AND expires<=?',(uid,time.time())); c.commit()
    c.close(); return r
def bot(uid,bid):
    c=db(); r=c.execute('SELECT * FROM bots WHERE id=? AND user_id=?',(bid,uid)).fetchone(); c.close()
    if not r: abort(404)
    return r
def root(b):
    p=Path(b['folder']).resolve()
    if not str(p).startswith(str(BOTS.resolve())): abort(400)
    return p
def stop(bid):
    x=PROCS.pop(bid,None)
    if x:
        p=x['p']
        try:
            if p.poll() is None:
                if os.name=='nt': p.terminate()
                else:
                    os.killpg(os.getpgid(p.pid),signal.SIGTERM)
                    try:p.wait(4)
                    except subprocess.TimeoutExpired: os.killpg(os.getpgid(p.pid),signal.SIGKILL)
        except Exception: pass
    c=db(); c.execute("UPDATE bots SET status='Stopped' WHERE id=?",(bid,)); c.commit(); c.close()

def page(body,title='BotCloud'):
    u=user(); return render_template_string(BASE+body,site=S('site'),support=S('support'),u=u,title=title)
BASE='''<!doctype html><html lang="bn"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{site}}</title><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css"><style>
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top,#26205c,#080c15 42%,#04060a);color:#f8fafc;font-family:Inter,system-ui,sans-serif}a{text-decoration:none;color:inherit}button,input{font:inherit}.wrap{max-width:560px;margin:auto;min-height:100vh;background:#07101acc;box-shadow:0 0 90px #0008}.top{height:70px;display:flex;justify-content:space-between;align-items:center;padding:12px 15px;border-bottom:1px solid #ffffff12;background:#080d17ed;position:sticky;top:0;z-index:10}.brand{display:flex;gap:10px;align-items:center}.logo{width:43px;height:43px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(135deg,#7c3aed,#06b6d4);font-size:22px}.brand small{display:block;color:#7f8da6;font-size:8px}.brand b{font-size:14px}.menu{position:fixed;right:max(calc((100vw - 560px)/2),0px);top:70px;width:min(340px,90vw);background:#0b1220f7;border:1px solid #ffffff15;padding:14px;z-index:9;transform:translateX(110%);transition:.2s}.open .menu{transform:none}.menu a{display:block;padding:12px;border-radius:11px;background:#ffffff06;margin:5px 0;font-size:11px}.content{padding:15px}.hero{padding:20px;border-radius:22px;background:linear-gradient(135deg,#6d28d9aa,#0284c7aa);border:1px solid #ffffff15;margin-bottom:12px}.hero h1{margin:6px 0;font-size:22px}.hero p{font-size:10px;color:#dbeafe;line-height:1.6}.card{background:#0c1422e8;border:1px solid #ffffff10;border-radius:18px;padding:14px;margin:12px 0}.title{font-weight:800;font-size:13px;margin-bottom:11px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.stat{padding:11px;border-radius:13px;background:#ffffff06;border:1px solid #ffffff0d}.stat small{display:block;color:#7f8da6;font-size:7px}.stat b{display:block;margin-top:5px;font-size:12px}.field,input{width:100%;background:#070b13;color:#fff;border:1px solid #ffffff12;border-radius:10px;padding:11px;margin:4px 0;outline:0}.btn{display:inline-block;border:0;border-radius:10px;padding:9px 11px;color:#fff;font-size:9px;font-weight:800;cursor:pointer}.primary{background:linear-gradient(135deg,#7c3aed,#06b6d4)}.green{background:#16a34a}.orange{background:#d97706}.red{background:#dc2626}.cyan{background:#0891b2}.purple{background:#7c3aed}.full{width:100%}.lock{display:flex;gap:12px;align-items:center;padding:16px;border:1px dashed #ffffff22;border-radius:14px}.lock i{font-size:25px;color:#fb7185}.muted{font-size:9px;color:#7f8da6}.bot{padding:13px;border-radius:15px;background:#070d18;border:1px solid #ffffff0e;margin-top:10px}.bothead{display:flex;justify-content:space-between}.bothead small{display:block;color:#7f8da6;font-size:8px;margin-top:4px}.actions{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}.pkg,.row{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:11px;border-radius:12px;background:#ffffff05;margin:7px 0}.pkg small{display:block;color:#7f8da6;font-size:8px;margin-top:3px}.paytabs{display:grid;grid-template-columns:1fr 1fr;gap:8px}.pay{padding:16px;border:1px solid #ffffff12;background:#0c1422;color:#fff;border-radius:15px;text-align:left}.pay.active{border-color:#a78bfa;background:#7c3aed22}.wallet{padding:16px;background:#fff;color:#111827;border-radius:15px;display:grid;gap:7px}.wallet strong{color:#2563eb;font-size:17px}.toast{position:fixed;top:78px;left:50%;transform:translateX(-50%);background:#111827;border:1px solid #ffffff16;padding:10px 13px;border-radius:10px;font-size:9px;z-index:30}.auth{min-height:calc(100vh - 70px);display:grid;place-items:center;padding:16px}.authbox{width:100%;padding:25px;border-radius:23px;background:#0c1422;border:1px solid #ffffff12;text-align:center}.editorbar{display:flex;justify-content:space-between;align-items:center;padding:10px;margin-bottom:8px;background:#0c1422;border-radius:12px}.adminrow{display:flex;align-items:center;gap:7px;padding:11px 0;border-bottom:1px solid #ffffff0c;font-size:9px}.adminrow>div{flex:1}.adminrow small{display:block;color:#7f8da6;margin-top:3px}.cols{display:grid;grid-template-columns:1fr 1fr;gap:7px}.hidden{display:none}.file{padding:9px;background:#ffffff06;border-radius:9px;margin:5px 0;font-size:9px}
</style></head><body><div class="wrap"><header class="top"><a class="brand" href="{{url_for('home')}}"><span class="logo"><i class="fa-brands fa-telegram"></i></span><span><b>{{site}}</b><small>PREMIUM BOT HOSTING</small></span></a>{% if u %}<button class="btn" onclick="document.body.classList.toggle('open')">☰</button>{% endif %}</header>{% if u %}<nav class="menu"><a href="{{url_for('home')}}">Dashboard</a><a href="{{url_for('deposit')}}">Advance / Deposit</a><a href="{{url_for('home')}}#packages">Packages</a><a href="{{url_for('home')}}#affiliate">Affiliate</a><a href="{{support}}" target="_blank">Support</a>{% if u['username']=='admin' %}<a href="{{url_for('admin')}}">Admin Panel</a>{% endif %}<a href="{{url_for('logout')}}">Logout</a></nav>{% endif %}{% with ms=get_flashed_messages() %}{% for m in ms %}<div class="toast">{{m}}</div>{% endfor %}{% endwith %}{{body|safe}}</div></body></html>'''

def render(body,**ctx): return render_template_string(BASE,body=render_template_string(body,**ctx),site=S('site'),support=S('support'),u=user())

HOME='''<main class="content"><section class="hero"><b>PREMIUM BOT HOSTING</b><h1>আপনার Bot Workspace</h1><p>Package অনুযায়ী দুইটি প্রয়োজনীয় file upload করে bot Run করুন। Run হওয়ার পর নিচে bot card থাকবে—Stop, আবার Run, Edit এবং Delete সব থাকবে।</p></section><div class="grid"><div class="stat"><small>BALANCE</small><b>৳{{'%.2f'|format(u['balance'])}}</b></div><div class="stat"><small>PLAN</small><b>{{s['name'] if s else 'Locked'}}</b></div><div class="stat"><small>BOT LIMIT</small><b>{{s['bots'] if s else 0}}</b></div></div><section class="card"><div class="title">My Bots</div>{% if s and bots|length<s['bots'] %}<form method="post" action="{{url_for('upload')}}" enctype="multipart/form-data"><input name="name" placeholder="Bot name" required><div class="cols"><label class="file">bot.py<input type="file" name="bot" accept=".py" required></label><label class="file">requirements.txt<input type="file" name="req" accept=".txt" required></label></div><button class="btn primary full">Save Files</button></form>{% else %}<div class="lock"><i class="fa-solid fa-lock"></i><div><b>{{'Package limit reached' if s else 'Hosting Locked'}}</b><div class="muted">{{'এই package-এর limit পূর্ণ।' if s else 'Package কিনলে upload unlock হবে।'}}</div></div></div>{% endif %}{% for b in bots %}<article class="bot"><div class="bothead"><div><b>{{b['name']}}</b><small>bot.py + requirements.txt</small></div><span>{{b['status']}}</span></div><div class="actions">{% if b['status']=='Running' %}<form method="post" action="{{url_for('stopbot',bid=b['id'])}}"><button class="btn orange">Stop</button></form>{% else %}<form method="post" action="{{url_for('runbot',bid=b['id'])}}"><button class="btn green">Run</button></form>{% endif %}<a class="btn purple" href="{{url_for('edit',bid=b['id'],fn='bot.py')}}">Edit bot.py</a><a class="btn purple" href="{{url_for('edit',bid=b['id'],fn='requirements.txt')}}">Edit requirements</a><a class="btn cyan" href="{{url_for('data',bid=b['id'])}}">Data</a><form method="post" action="{{url_for('delbot',bid=b['id'])}}"><button class="btn red">Delete</button></form></div></article>{% endfor %}</section><section class="card" id="packages"><div class="title">Hosting Packages</div>{% for p in packages %}<div class="pkg"><div><b>{{p['name']}}</b><small>{{p['months']}} month • {{p['bots']}} bots</small></div><b>৳{{p['price']}}</b><form method="post" action="{{url_for('buy',pid=p['id'])}}"><button class="btn primary">Buy</button></form></div>{% endfor %}</section><section class="card" id="affiliate"><div class="title">Affiliate</div><div class="row"><b>{{u['ref']}}</b><span>Coins: {{u['coins']}}</span></div><div class="muted">Approved referred deposit-এ প্রতি request ৳{{commission}} commission যোগ হবে।</div></section></main>'''

@app.route('/')
@loginreq
def home():
    u=user(); c=db(); bots=c.execute('SELECT * FROM bots WHERE user_id=? ORDER BY id DESC',(u['id'],)).fetchall(); packages=c.execute('SELECT * FROM packages WHERE active=1').fetchall(); c.close(); return render(HOME,bots=bots,packages=packages,s=sub(u['id']),commission=S('commission'))

AUTH='''<main class="auth"><div class="authbox"><div class="logo" style="margin:auto"><i class="fa-brands fa-telegram"></i></div><h2>{{'Register' if mode=='reg' else 'Login'}}</h2><form method="post">{% if mode=='reg' %}<input name="username" placeholder="Username" required><input name="email" type="email" placeholder="Gmail" required>{% else %}<input name="identity" placeholder="Username or Gmail" required>{% endif %}<input name="password" type="password" placeholder="Password" required>{% if mode=='reg' %}<input name="ref" placeholder="Referral code (optional)">{% endif %}<button class="btn primary full">{{'Create Account' if mode=='reg' else 'Login'}}</button></form><p class="muted"><a href="{{url_for('login' if mode=='reg' else 'register')}}">{{'Already have account? Login' if mode=='reg' else 'Create account'}}</a></p></div></main>'''
@app.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        un=request.form['username'].strip(); em=request.form['email'].strip().lower(); pw=request.form['password']; ref=request.form.get('ref','').strip().upper(); c=db()
        if c.execute('SELECT 1 FROM users WHERE username=? OR email=?',(un,em)).fetchone(): c.close(); flash('Username/Gmail already exists'); return redirect(url_for('register'))
        rr=c.execute('SELECT id FROM users WHERE ref=?',(ref,)).fetchone() if ref else None; code='BC-'+secrets.token_hex(4).upper(); c.execute('INSERT INTO users(username,email,pw,ref,referred_by,created) VALUES(?,?,?,?,?,?)',(un,em,generate_password_hash(pw),code,rr['id'] if rr else None,time.time())); uid=c.lastrowid; c.commit(); c.close(); session['uid']=uid; return redirect(url_for('home'))
    return render(AUTH,mode='reg')
@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        x=request.form['identity']; c=db(); u=c.execute('SELECT * FROM users WHERE username=? OR email=?',(x,x.lower())).fetchone(); c.close()
        if u and u['status']=='active' and check_password_hash(u['pw'],request.form['password']): session['uid']=u['id']; return redirect(url_for('home'))
        flash('Login failed or account banned')
    return render(AUTH,mode='login')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

DEP='''<main class="content"><div class="title">Advance / Deposit <span class="muted">Minimum ৳{{minimum}}</span></div><div class="paytabs"><button id="bk" class="pay active" onclick="sel('bkash')"><b>bKash</b><small>Personal • Send Money</small></button><button id="ng" class="pay" onclick="sel('nagad')"><b>Nagad</b><small>Personal • Send Money</small></button></div><section class="card"><form method="post"><input type="hidden" name="method" id="method" value="bkash"><input id="amount" name="amount" type="number" min="{{minimum}}" placeholder="Deposit amount" required><button type="button" class="btn primary full" onclick="nextpay()">ডিপোজিট</button><div id="paybox" class="hidden" style="margin-top:12px"><div class="wallet"><span>Wallet</span><b id="wn">bKash</b><span>Number</span><strong id="num">{{bkash}}</strong><button type="button" class="btn cyan" onclick="copyNum()">Copy Number</button><span>Amount</span><strong id="amt">৳0</strong></div><p class="muted">উপরের নম্বরে Send Money করে Transaction ID দিন।</p><input name="txn" placeholder="Transaction ID" required><button class="btn primary full">জমা দিন</button></div></form></section></main><script>let D={bkash:['bKash','{{bkash}}'],nagad:['Nagad','{{nagad}}']};function sel(x){method.value=x;bk.classList.toggle('active',x=='bkash');ng.classList.toggle('active',x=='nagad');wn.textContent=D[x][0];num.textContent=D[x][1]}function nextpay(){let a=+amount.value;if(a<{{minimum}}){alert('Minimum deposit ৳{{minimum}}');return}amt.textContent='৳'+a;paybox.classList.remove('hidden')}function copyNum(){navigator.clipboard.writeText(num.textContent);alert('Number copied')}</script>'''
@app.route('/deposit',methods=['GET','POST'])
@loginreq
def deposit():
    if request.method=='POST':
        a=float(request.form['amount']); method=request.form['method']; txn=request.form['txn'].strip()
        if a<float(S('min_deposit')) or method not in ('bkash','nagad') or not txn: flash('Invalid deposit details'); return redirect(url_for('deposit'))
        c=db(); c.execute('INSERT INTO deposits(user_id,method,amount,txn,created) VALUES(?,?,?,?,?)',(user()['id'],method,a,txn,time.time())); c.commit(); c.close(); flash('Deposit request sent to admin'); return redirect(url_for('deposit'))
    return render(DEP,minimum=S('min_deposit'),bkash=S('bkash'),nagad=S('nagad'))

@app.route('/upload',methods=['POST'])
@loginreq
def upload():
    u=user(); s=sub(u['id']); c=db(); n=c.execute('SELECT count(*) n FROM bots WHERE user_id=?',(u['id'],)).fetchone()['n']; c.close()
    if not s or n>=s['bots']: flash('Package limit reached'); return redirect(url_for('home'))
    f=request.files.get('bot'); r=request.files.get('req')
    if not f or not r or not f.filename.endswith('.py') or r.filename!='requirements.txt': flash('bot.py and requirements.txt are required'); return redirect(url_for('home'))
    folder=BOTS/f'u{u["id"]}_{secrets.token_hex(5)}'; folder.mkdir(); (folder/'bot.py').write_bytes(f.read()); (folder/'requirements.txt').write_bytes(r.read()); c=db(); c.execute('INSERT INTO bots(user_id,name,folder,created) VALUES(?,?,?,?)',(u['id'],request.form['name'],str(folder),time.time())); c.commit(); c.close(); flash('Files saved. এখন Run চাপুন।'); return redirect(url_for('home'))
@app.route('/bot/<int:bid>/run',methods=['POST'])
@loginreq
def runbot(bid):
    b=bot(user()['id'],bid); s=sub(user()['id']);
    if not s: abort(403)
    rt=root(b); v=rt/'.venv'; py=v/'Scripts/python.exe' if os.name=='nt' else v/'bin/python'
    if not v.exists(): subprocess.run([sys.executable,'-m','venv',str(v)],check=True)
    if not (rt/'.deps').exists(): subprocess.run([str(py),'-m','pip','install','-r','requirements.txt'],cwd=rt,check=False,timeout=600); (rt/'.deps').write_text('ok')
    if bid in PROCS and PROCS[bid]['p'].poll() is None: return redirect(url_for('home'))
    log=open(rt/'runtime.log','a',encoding='utf8'); kw={'cwd':rt,'stdout':log,'stderr':subprocess.STDOUT,'stdin':subprocess.DEVNULL}
    if os.name!='nt': kw['start_new_session']=True
    p=subprocess.Popen([str(py),'bot.py'],**kw); PROCS[bid]={'p':p}; c=db(); c.execute("UPDATE bots SET status='Running' WHERE id=?",(bid,)); c.commit(); c.close(); return redirect(url_for('home'))
@app.route('/bot/<int:bid>/stop',methods=['POST'])
@loginreq
def stopbot(bid): bot(user()['id'],bid); stop(bid); return redirect(url_for('home'))
@app.route('/bot/<int:bid>/delete',methods=['POST'])
@loginreq
def delbot(bid): b=bot(user()['id'],bid); stop(bid); shutil.rmtree(root(b),ignore_errors=True); c=db(); c.execute('DELETE FROM bots WHERE id=?',(bid,)); c.commit(); c.close(); return redirect(url_for('home'))

EDIT='''<main class="content"><div class="editorbar"><b>{{fn}}</b><button class="btn green" onclick="document.getElementById('form').submit()">Save</button></div><form id="form" method="post"><textarea id="code" name="code">{{code}}</textarea></form></main><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/material-palenight.min.css"><script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script><script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/python/python.min.js"></script><script>let cm=CodeMirror.fromTextArea(document.getElementById('code'),{mode:'python',theme:'material-palenight',lineNumbers:true,matchBrackets:true,autoCloseBrackets:true,indentUnit:4,tabSize:4});cm.setSize('100%','calc(100vh - 150px)')</script>'''
@app.route('/bot/<int:bid>/edit',methods=['GET','POST'])
@loginreq
def edit(bid):
    b=bot(user()['id'],bid); fn=request.args.get('file','bot.py'); p=(root(b)/fn).resolve()
    if not str(p).startswith(str(root(b))) or fn not in ('bot.py','requirements.txt'): abort(400)
    if request.method=='POST': p.write_text(request.form['code'],encoding='utf8'); flash(fn+' saved'); return redirect(url_for('edit',bid=bid,file=fn))
    return render(EDIT,fn=fn,code=p.read_text(encoding='utf8',errors='replace'))
@app.route('/bot/<int:bid>/data')
@loginreq
def data(bid):
    b=bot(user()['id'],bid); fs=[p.name for p in root(b).iterdir() if p.is_file() and p.name not in ('runtime.log','.deps','.venv')]; return render('<main class="content"><section class="card"><div class="title">Live Data / Files</div>{% for f in fs %}<div class="row"><span>{{f}}</span><a class="btn cyan" href="{{url_for("edit",bid=bid,file=f)}}">Edit</a></div>{% endfor %}</section></main>',fs=fs,bid=bid)

ADMIN='''<main class="content"><section class="hero"><b>ADMIN CONTROL CENTER</b><h1>Business Management</h1></section><section class="card"><div class="title">Payment / Affiliate Settings</div><form class="cols" method="post" action="{{url_for('settings')}}"><input name="site" value="{{site}}" placeholder="Site name"><input name="min_deposit" value="{{settings.min_deposit}}" placeholder="Min deposit"><input name="bkash" value="{{settings.bkash}}" placeholder="bKash number"><input name="nagad" value="{{settings.nagad}}" placeholder="Nagad number"><input name="commission" value="{{settings.commission}}" placeholder="Referral commission"><input name="support" value="{{settings.support}}" placeholder="Support link"><button class="btn primary">Save Settings</button></form></section><section class="card"><div class="title">Deposit Requests</div>{% for d in deposits %}<div class="adminrow"><div><b>{{d.username}}</b><small>{{d.method}} • ৳{{d.amount}} • {{d.txn}}</small></div><span>{{d.status}}</span>{% if d.status=='pending' %}<form method="post" action="{{url_for('review',did=d.id,a='approve')}}"><button class="btn green">Approve</button></form><form method="post" action="{{url_for('review',did=d.id,a='reject')}}"><button class="btn red">Reject</button></form>{% endif %}</div>{% endfor %}</section><section class="card"><div class="title">Users</div>{% for x in users %}<div class="adminrow"><div><b>{{x.username}}</b><small>{{x.email}} • ৳{{x.balance}}</small></div><span>{{x.status}}</span>{% if x.status=='active' %}<form method="post" action="{{url_for('ban',uid=x.id)}}"><button class="btn red">Ban</button></form>{% else %}<form method="post" action="{{url_for('unban',uid=x.id)}}"><button class="btn green">Unban</button></form>{% endif %}</div>{% endfor %}</section><section class="card"><div class="title">Packages</div>{% for p in packages %}<form class="pkg" method="post" action="{{url_for('pkgedit',pid=p.id)}}"><input name="name" value="{{p.name}}"><input name="months" value="{{p.months}}" type="number"><input name="bots" value="{{p.bots}}" type="number"><input name="price" value="{{p.price}}" type="number"><button class="btn primary">Save</button></form>{% endfor %}<form class="pkg" method="post" action="{{url_for('pkgadd')}}"><input name="name" placeholder="New package"><input name="months" value="1" type="number"><input name="bots" value="2" type="number"><input name="price" value="199" type="number"><button class="btn primary">Add</button></form></section></main>'''
@app.route('/admin')
@adminreq
def admin():
    c=db(); users=c.execute("SELECT * FROM users WHERE username!='admin' ORDER BY id DESC").fetchall(); deposits=c.execute('SELECT d.*,u.username FROM deposits d JOIN users u ON u.id=d.user_id ORDER BY d.id DESC').fetchall(); packages=c.execute('SELECT * FROM packages WHERE active=1').fetchall(); c.close(); settings=type('Obj',(),{k:S(k) for k in ['min_deposit','bkash','nagad','commission','support']}); return render(ADMIN,users=users,deposits=deposits,packages=packages,settings=settings)
@app.route('/admin/settings',methods=['POST'])
@adminreq
def settings():
    for k in ['site','min_deposit','bkash','nagad','commission','support']:
        c=db(); c.execute('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,request.form[k])); c.commit(); c.close()
    return redirect(url_for('admin'))
@app.route('/admin/deposit/<int:did>/<a>',methods=['POST'])
@adminreq
def review(did,a):
    c=db(); d=c.execute('SELECT * FROM deposits WHERE id=?',(did,)).fetchone()
    if d and d['status']=='pending':
        if a=='approve':
            c.execute("UPDATE deposits SET status='approved' WHERE id=?",(did,)); c.execute('UPDATE users SET balance=balance+? WHERE id=?',(d['amount'],d['user_id'])); r=c.execute('SELECT referred_by FROM users WHERE id=?',(d['user_id'],)).fetchone();
            if r and r['referred_by']:
                cm=float(S('commission')); c.execute('UPDATE users SET balance=balance+?,coins=coins+? WHERE id=?',(cm,cm,r['referred_by']))
        else: c.execute("UPDATE deposits SET status='rejected' WHERE id=?",(did,))
        c.commit()
    c.close(); return redirect(url_for('admin'))
@app.route('/admin/user/<int:uid>/<a>',methods=['POST'])
@adminreq
def ban(uid,a):
    c=db(); c.execute("UPDATE users SET status=? WHERE id=?",('active' if a=='unban' else 'banned',uid)); c.commit(); c.close(); return redirect(url_for('admin'))
@app.route('/admin/pkg/add',methods=['POST'])
@adminreq
def pkgadd():
    c=db(); c.execute('INSERT INTO packages(name,months,bots,price) VALUES(?,?,?,?)',(request.form['name'],int(request.form['months']),int(request.form['bots']),float(request.form['price']))); c.commit(); c.close(); return redirect(url_for('admin'))
@app.route('/admin/pkg/<int:pid>/edit',methods=['POST'])
@adminreq
def pkgedit(pid):
    c=db(); c.execute('UPDATE packages SET name=?,months=?,bots=?,price=? WHERE id=?',(request.form['name'],int(request.form['months']),int(request.form['bots']),float(request.form['price']),pid)); c.commit(); c.close(); return redirect(url_for('admin'))

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))
