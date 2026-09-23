const express = require("express");
const session = require("express-session");
const Database = require("better-sqlite3");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;
const db = new Database(path.join(__dirname, "data", "app.db"));

db.exec(`
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  email TEXT DEFAULT '',
  balance REAL DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  referred_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS packages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  price REAL NOT NULL,
  ram TEXT NOT NULL,
  cpu TEXT NOT NULL,
  storage TEXT NOT NULL,
  bots INTEGER NOT NULL DEFAULT 1,
  description TEXT DEFAULT '',
  active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS referrals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  referrer_uid TEXT NOT NULL,
  referred_uid TEXT NOT NULL,
  reward REAL DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT NOT NULL,
  package_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  status TEXT DEFAULT 'pending',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT DEFAULT 'stopped',
  package_id INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
`);

const count = db.prepare("SELECT COUNT(*) c FROM packages").get().c;
if (!count) {
  const add = db.prepare(`INSERT INTO packages
    (name, price, ram, cpu, storage, bots, description) VALUES (?,?,?,?,?,?,?)`);
  [
    ["Starter", 99, "512 MB", "1 vCPU", "2 GB SSD", 1, "For a small personal bot"],
    ["Pro", 249, "1 GB", "1 vCPU", "5 GB SSD", 3, "Balanced plan for growing bots"],
    ["Premium", 499, "2 GB", "2 vCPU", "10 GB SSD", 8, "High-performance bot hosting"]
  ].forEach(p => add.run(...p));
}

app.use(express.json());
app.use(express.urlencoded({extended:true}));
app.use(session({
  secret: process.env.SESSION_SECRET || "dev-secret-change-me",
  resave: false, saveUninitialized: false,
  cookie: {httpOnly:true, sameSite:"lax"}
}));
app.use(express.static(path.join(__dirname, "public")));

function uid() {
  return "U" + Date.now().toString(36).toUpperCase() + Math.random().toString(36).slice(2,6).toUpperCase();
}

function currentUser(req) {
  return req.session.uid ? db.prepare("SELECT * FROM users WHERE uid=?").get(req.session.uid) : null;
}

app.get("/api/packages", (req,res) => {
  res.json(db.prepare("SELECT * FROM packages WHERE active=1 ORDER BY price").all());
});

app.post("/api/register", (req,res) => {
  const name = String(req.body.name || "").trim();
  const email = String(req.body.email || "").trim();
  const ref = String(req.body.ref || "").trim();
  if (!name) return res.status(400).json({error:"Name is required"});
  const newUid = uid();
  try {
    db.prepare("INSERT INTO users(uid,name,email,referred_by) VALUES(?,?,?,?)").run(newUid,name,email,ref);
    if (ref && db.prepare("SELECT uid FROM users WHERE uid=?").get(ref)) {
      db.prepare("INSERT INTO referrals(referrer_uid,referred_uid,reward) VALUES(?,?,?)").run(ref,newUid,0);
    }
    req.session.uid = newUid;
    res.json({ok:true,user:currentUser(req)});
  } catch(e) {
    res.status(500).json({error:e.message});
  }
});

app.post("/api/login", (req,res) => {
  const u = db.prepare("SELECT * FROM users WHERE uid=?").get(String(req.body.uid||"").trim());
  if (!u) return res.status(404).json({error:"UID not found"});
  req.session.uid = u.uid;
  res.json({ok:true,user:u});
});

app.post("/api/logout", (req,res) => {
  req.session.destroy(()=>res.json({ok:true}));
});

app.get("/api/me", (req,res) => {
  const u=currentUser(req);
  if (!u) return res.status(401).json({error:"Not logged in"});
  const referrals=db.prepare(`
    SELECT r.*, u.name FROM referrals r JOIN users u ON u.uid=r.referred_uid
    WHERE r.referrer_uid=? ORDER BY r.id DESC
  `).all(u.uid);
  const bots=db.prepare("SELECT * FROM bots WHERE uid=? ORDER BY id DESC").all(u.uid);
  res.json({user:u, referrals, bots});
});

app.post("/api/orders", (req,res) => {
  const u=currentUser(req);
  if (!u) return res.status(401).json({error:"Login required"});
  const packageId=Number(req.body.packageId);
  const p=db.prepare("SELECT * FROM packages WHERE id=? AND active=1").get(packageId);
  if (!p) return res.status(404).json({error:"Package not found"});
  const info=db.prepare("INSERT INTO orders(uid,package_id,amount) VALUES(?,?,?)").run(u.uid,p.id,p.price);
  // Payment integration point: replace this demo pending order with your gateway's checkout call.
  res.json({ok:true,orderId:info.lastInsertRowid,message:"Order created. Connect a payment gateway to collect payment."});
});

app.post("/api/bots", (req,res) => {
  const u=currentUser(req);
  if (!u) return res.status(401).json({error:"Login required"});
  const name=String(req.body.name||"").trim();
  const packageId=Number(req.body.packageId);
  if (!name) return res.status(400).json({error:"Bot name required"});
  const p=db.prepare("SELECT * FROM packages WHERE id=?").get(packageId);
  if (!p) return res.status(404).json({error:"Package not found"});
  const existing=db.prepare("SELECT COUNT(*) c FROM bots WHERE uid=?").get(u.uid).c;
  if (existing >= p.bots) return res.status(400).json({error:"Bot limit reached for this package"});
  const info=db.prepare("INSERT INTO bots(uid,name,status,package_id) VALUES(?,?,?,?)").run(u.uid,name,"stopped",packageId);
  res.json({ok:true,id:info.lastInsertRowid,message:"Bot record created. Attach your server-side process manager/Docker worker to start real Telegram bots."});
});

// Admin
function adminRequired(req,res,next){
  if(!req.session.admin) return res.status(401).json({error:"Admin login required"});
  next();
}
app.post("/api/admin/login",(req,res)=>{
  const user=process.env.ADMIN_USERNAME||"admin";
  const pass=process.env.ADMIN_PASSWORD||"change-this-password";
  if(req.body.username===user && req.body.password===pass){req.session.admin=true; return res.json({ok:true});}
  res.status(401).json({error:"Invalid admin credentials"});
});
app.post("/api/admin/logout",(req,res)=>{req.session.admin=false;res.json({ok:true})});
app.get("/api/admin/stats",adminRequired,(req,res)=>{
  res.json({
    users:db.prepare("SELECT COUNT(*) c FROM users").get().c,
    referrals:db.prepare("SELECT COUNT(*) c FROM referrals").get().c,
    orders:db.prepare("SELECT COUNT(*) c FROM orders").get().c,
    bots:db.prepare("SELECT COUNT(*) c FROM bots").get().c,
    revenue:db.prepare("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='paid'").get().s
  });
});
app.get("/api/admin/users",adminRequired,(req,res)=>{
  const q=String(req.query.q||"").trim();
  const users=q ? db.prepare("SELECT * FROM users WHERE uid LIKE ? OR name LIKE ? ORDER BY id DESC").all(`%${q}%`,`%${q}%`)
                : db.prepare("SELECT * FROM users ORDER BY id DESC LIMIT 100").all();
  res.json(users);
});
app.get("/api/admin/orders",adminRequired,(req,res)=>{
  res.json(db.prepare(`SELECT o.*,u.name,p.name package_name FROM orders o
    LEFT JOIN users u ON u.uid=o.uid LEFT JOIN packages p ON p.id=o.package_id
    ORDER BY o.id DESC LIMIT 100`).all());
});
app.patch("/api/admin/orders/:id",adminRequired,(req,res)=>{
  const status=["pending","paid","cancelled"].includes(req.body.status)?req.body.status:"pending";
  db.prepare("UPDATE orders SET status=? WHERE id=?").run(status,req.params.id);
  res.json({ok:true});
});
app.patch("/api/admin/users/:uid",adminRequired,(req,res)=>{
  const name=String(req.body.name||"").trim();
  const balance=Number(req.body.balance||0);
  db.prepare("UPDATE users SET name=?, balance=? WHERE uid=?").run(name,balance,req.params.uid);
  res.json({ok:true});
});

app.get("*",(req,res)=>res.sendFile(path.join(__dirname,"public","index.html")));
app.listen(PORT,()=>console.log(`Premium hosting panel running on http://localhost:${PORT}`));
