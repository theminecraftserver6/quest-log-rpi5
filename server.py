#!/usr/bin/env python3
"""
QuestLog Server — Raspberry Pi 5  (v5 — friends + co-op)

Auth:
  POST /api/register                → {username, password}
  POST /api/login                   → {username, password}  → token
  POST /api/logout                  → (token)
  GET  /api/me                      → {username, joined}

Personal data (token required):
  GET/POST /api/xp
  GET/POST /api/quests
  POST     /api/analyze

Friends (token required):
  POST /api/invite                  → generate invite code  → {code, expires}
  POST /api/invite/accept           → {code}  → add friendship both ways
  GET  /api/friends                 → [{username, level, totalXp, currentXp}]
  DELETE /api/friends/<username>    → remove friendship
  GET  /api/poll                    → {friends, coop} combined live-update feed

Co-op quests (token required):
  POST /api/coop                    → create co-op quest
                                       {title, desc, category, difficulty,
                                        members: [{username, xp}]}
  GET  /api/coop                    → all co-op quests you're part of
  POST /api/coop/<id>/done          → mark your share done, awards your XP
  DELETE /api/coop/<id>             → creator can delete

Requires:  pip install bcrypt --break-system-packages
Usage:     python3 server.py [--port 8080]
"""

import json, os, argparse, secrets, re
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib import request as urlrequest, error as urlerror

try:
    import bcrypt
except ImportError:
    print("\n  ✗  bcrypt not installed.")
    print("     Run:  pip install bcrypt --break-system-packages\n")
    raise SystemExit(1)

# ── Config ─────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = "YOUR_API_KEY_HERE"
OLLAMA_BASE_URL   = "http://localhost:11434"
OLLAMA_MODEL      = "phi3:mini"
INVITE_TTL_HOURS  = 24      # invite codes expire after this long
INVITE_CODE_LEN   = 8       # length of invite code

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
USERS_DIR     = DATA_DIR / "users"
COOP_DIR      = DATA_DIR / "coop"
USERS_FILE    = DATA_DIR / "users.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"
INVITES_FILE  = DATA_DIR / "invites.json"    # {code: {owner, expires}}
FRIENDS_FILE  = DATA_DIR / "friends.json"    # {username: [friend, ...]}

for d in (DATA_DIR, USERS_DIR, COOP_DIR):
    d.mkdir(exist_ok=True)

HTML_SCIFI   = BASE_DIR / "quest-log.html"
HTML_FANTASY = BASE_DIR / "quest-log-fantasy.html"

# ── Sessions ───────────────────────────────────────────────────────────────────
_sessions: dict = {}

def _load_sessions():
    if SESSIONS_FILE.exists():
        try: _sessions.update(json.loads(SESSIONS_FILE.read_text()))
        except Exception: pass

def _save_sessions():
    try: SESSIONS_FILE.write_text(json.dumps(_sessions, indent=2))
    except Exception: pass

_load_sessions()

# ── Generic JSON store helpers ─────────────────────────────────────────────────
def _rj(path: Path, default):
    if path.exists():
        try: return json.loads(path.read_text())
        except Exception: pass
    return default

def _wj(path: Path, data):
    path.write_text(json.dumps(data, indent=2))

# ── Users ──────────────────────────────────────────────────────────────────────
def _load_users(): return _rj(USERS_FILE, {})
def _save_users(u): _wj(USERS_FILE, u)

# ── Per-user paths ─────────────────────────────────────────────────────────────
def _safe(u): return re.sub(r"[^a-zA-Z0-9_\-]", "_", u)
def _udir(u):
    d = USERS_DIR / _safe(u); d.mkdir(exist_ok=True); return d
def _xp_path(u):     return _udir(u) / "xp.json"
def _q_path(u):      return _udir(u) / "quests.json"

DEFAULT_XP = {"level":1,"currentXp":0,"totalXp":0,"xpToNextLevel":100,"completedQuests":0}

def _rxp(u):
    p = _xp_path(u)
    if p.exists():
        try: return json.loads(p.read_text())
        except Exception: pass
    return dict(DEFAULT_XP)

def _wxp(u, d): _xp_path(u).write_text(json.dumps(d, indent=2))
def _rq(u):     return _rj(_q_path(u), [])
def _wq(u, d):  _q_path(u).write_text(json.dumps(d, indent=2))

# ── Friends ────────────────────────────────────────────────────────────────────
def _load_friends(): return _rj(FRIENDS_FILE, {})
def _save_friends(f): _wj(FRIENDS_FILE, f)

def _friends_of(username):
    return _load_friends().get(username, [])

def _are_friends(a, b):
    return b in _friends_of(a)

def _add_friendship(a, b):
    f = _load_friends()
    f.setdefault(a, [])
    f.setdefault(b, [])
    if b not in f[a]: f[a].append(b)
    if a not in f[b]: f[b].append(a)
    _save_friends(f)

def _remove_friendship(a, b):
    f = _load_friends()
    f[a] = [x for x in f.get(a, []) if x != b]
    f[b] = [x for x in f.get(b, []) if x != a]
    _save_friends(f)

# ── Invites ────────────────────────────────────────────────────────────────────
def _load_invites(): return _rj(INVITES_FILE, {})
def _save_invites(i): _wj(INVITES_FILE, i)

def _purge_expired_invites():
    now = datetime.now(timezone.utc).isoformat()
    inv = {k: v for k, v in _load_invites().items() if v["expires"] > now}
    _save_invites(inv)
    return inv

def _new_invite(owner):
    _purge_expired_invites()
    code    = secrets.token_urlsafe(INVITE_CODE_LEN)[:INVITE_CODE_LEN].upper()
    expires = (datetime.now(timezone.utc) + timedelta(hours=INVITE_TTL_HOURS)).isoformat()
    inv = _load_invites()
    inv[code] = {"owner": owner, "expires": expires}
    _save_invites(inv)
    return code, expires

# ── Co-op quests ───────────────────────────────────────────────────────────────
def _coop_path(cid): return COOP_DIR / f"{cid}.json"

def _load_coop(cid):
    p = _coop_path(cid)
    if p.exists():
        try: return json.loads(p.read_text())
        except Exception: pass
    return None

def _save_coop(quest): _coop_path(quest["id"]).write_text(json.dumps(quest, indent=2))

def _user_coops(username):
    quests = []
    for f in COOP_DIR.glob("*.json"):
        try:
            q = json.loads(f.read_text())
            if any(m["username"] == username for m in q.get("members", [])):
                quests.append(q)
        except Exception:
            pass
    return quests

# ── Auth helpers ───────────────────────────────────────────────────────────────
def _hash_pw(pw):     return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode()
def _check_pw(pw, h): 
    try: return bcrypt.checkpw(pw.encode(), h.encode())
    except: return False
def _new_token():     return secrets.token_hex(32)
def _token_user(t):   return _sessions.get(t)

def _val_user(u):
    if not u:           return "Username required"
    if len(u) < 2:      return "Username must be ≥ 2 characters"
    if len(u) > 32:     return "Username too long (max 32)"
    if not re.match(r"^[a-zA-Z0-9_\-]+$", u):
                        return "Letters, numbers, _ and - only"
    return None

def _val_pw(pw):
    if not pw:          return "Password required"
    if len(pw) < 8:     return "Password must be ≥ 8 characters"
    if len(pw) > 128:   return "Password too long"
    return None

# ── AI helpers ─────────────────────────────────────────────────────────────────
def _prompt(title, desc, cat, diff):
    return (
        f'Break down this quest into 3-5 actionable sub-quests.\n'
        f'Quest: "{title}"\nDescription: "{desc}"\nCategory: {cat} | Priority: {diff}\n\n'
        f'Respond ONLY with a JSON array, no markdown.\n'
        f'Example: [{{"title":"Do X","difficulty":"EASY"}}]\n'
        f'Rules: 3-5 items, each with "title"(≤60 chars) and "difficulty"(EASY/MEDIUM/HARD/CRITICAL).\n'
        f'Start with [ and end with ]'
    )

def _parse_subs(text):
    text = text.strip().replace("```json","").replace("```","").strip()
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1: raise ValueError("No array found")
    subs = json.loads(text[s:e+1])
    if not isinstance(subs, list) or not subs: raise ValueError("Empty array")
    valid = {"EASY","MEDIUM","HARD","CRITICAL"}
    return [{"title": str(x.get("title","Task"))[:60],
             "difficulty": x.get("difficulty","EASY") if x.get("difficulty") in valid else "EASY"}
            for x in subs[:5]]

def has_claude_key():
    return bool(ANTHROPIC_API_KEY) and ANTHROPIC_API_KEY != "YOUR_API_KEY_HERE"

def _claude(title, desc, cat, diff):
    body = json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":1000,
                       "messages":[{"role":"user","content":_prompt(title,desc,cat,diff)}]}).encode()
    req = urlrequest.Request("https://api.anthropic.com/v1/messages", data=body,
          headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_API_KEY,
                   "anthropic-version":"2023-06-01"}, method="POST")
    with urlrequest.urlopen(req, timeout=30) as r:
        res = json.loads(r.read().decode())
    return _parse_subs("".join(c.get("text","") for c in res.get("content",[]))), "claude"

def _check_ollama():
    try:
        with urlrequest.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=3) as r: return r.status==200
    except: return False

def _ollama(title, desc, cat, diff):
    body = json.dumps({"model":OLLAMA_MODEL,"prompt":_prompt(title,desc,cat,diff),
                       "stream":False,"options":{"temperature":0.3,"num_predict":512}}).encode()
    req = urlrequest.Request(f"{OLLAMA_BASE_URL}/api/generate", data=body,
          headers={"Content-Type":"application/json"}, method="POST")
    with urlrequest.urlopen(req, timeout=90) as r:
        res = json.loads(r.read().decode())
    return _parse_subs(res.get("response","")), f"ollama/{OLLAMA_MODEL}"

# ── CORS ───────────────────────────────────────────────────────────────────────
def _cors():
    return {"Access-Control-Allow-Origin":"*",
            "Access-Control-Allow-Methods":"GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers":"Content-Type, Authorization",
            "Cache-Control":"no-cache, no-store, must-revalidate"}

# ── Handler ────────────────────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[{datetime.now():%H:%M:%S}] {fmt % args}")

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(body)))
        for k,v in _cors().items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)

    def _file(self, path):
        if not path.exists(): self._json(404,{"error":f"{path.name} not found"}); return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k,v in _cors().items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length",0))
        if n:
            try: return json.loads(self.rfile.read(n))
            except: pass
        return {}

    def _tok(self):
        a = self.headers.get("Authorization","")
        return a[7:].strip() if a.startswith("Bearer ") else None

    def _auth(self):
        t = self._tok()
        if not t:
            self._json(401,{"error":"Missing token — please log in"}); return None
        u = _token_user(t)
        if not u:
            self._json(401,{"error":"Invalid or expired session"}); return None
        return u

    def do_OPTIONS(self):
        self.send_response(204)
        for k,v in _cors().items(): self.send_header(k,v)
        self.end_headers()

    # ── GET ────────────────────────────────────────────────────────────────────
    def do_GET(self):
        p = self.path.split("?")[0]

        if p in ("/","/index.html"):    self._file(HTML_SCIFI); return
        if p == "/fantasy":             self._file(HTML_FANTASY); return
        if p == "/friends.js":
            js = BASE_DIR / "friends.js"
            if not js.exists(): self._json(404,{"error":"friends.js not found"}); return
            body = js.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type","application/javascript")
            self.send_header("Content-Length", str(len(body)))
            for k,v in _cors().items(): self.send_header(k,v)
            self.end_headers(); self.wfile.write(body); return

        u = self._auth()
        if not u: return

        if p == "/api/me":
            users  = _load_users()
            joined = users.get(u,{}).get("joined","unknown")
            self._json(200,{"username":u,"joined":joined}); return

        if p == "/api/xp":
            self._json(200, _rxp(u)); return

        if p == "/api/quests":
            self._json(200, _rq(u)); return

        if p == "/api/status":
            oll = _check_ollama()
            self._json(200,{"claude":has_claude_key(),"ollama":oll,
                "ollama_model":OLLAMA_MODEL if oll else None,
                "active_backend":"claude" if has_claude_key() else
                                 f"ollama/{OLLAMA_MODEL}" if oll else "none"}); return

        # Friends list — with their XP
        if p == "/api/friends":
            result = []
            for f in _friends_of(u):
                xp = _rxp(f)
                result.append({"username":f,"level":xp.get("level",1),
                               "totalXp":xp.get("totalXp",0),"currentXp":xp.get("currentXp",0)})
            self._json(200, result); return

        # Co-op quests
        if p == "/api/coop":
            self._json(200, _user_coops(u)); return

        # Poll — friends XP + co-op quests in one lightweight call
        if p == "/api/poll":
            friends_data = []
            for f in _friends_of(u):
                fx = _rxp(f)
                friends_data.append({"username":f,"level":fx.get("level",1),
                                     "totalXp":fx.get("totalXp",0),"currentXp":fx.get("currentXp",0)})
            self._json(200, {"friends": friends_data, "coop": _user_coops(u)}); return

        # Combined poll — friends XP + co-op quests in one call
        if p == "/api/poll":
            friends_data = []
            for f in _friends_of(u):
                xp = _rxp(f)
                friends_data.append({"username":f,"level":xp.get("level",1),
                                     "totalXp":xp.get("totalXp",0),"currentXp":xp.get("currentXp",0)})
            self._json(200, {"friends": friends_data, "coop": _user_coops(u)}); return

        self._json(404,{"error":"not found"})

    # ── DELETE ─────────────────────────────────────────────────────────────────
    def do_DELETE(self):
        p = self.path.split("?")[0]
        u = self._auth()
        if not u: return

        # DELETE /api/friends/<username>
        if p.startswith("/api/friends/"):
            target = p[len("/api/friends/"):]
            if not target: self._json(400,{"error":"Username required"}); return
            _remove_friendship(u, target)
            print(f"  ✓ {u} unfriended {target}")
            self._json(200,{"ok":True}); return

        # DELETE /api/coop/<id>
        if p.startswith("/api/coop/") and not p.endswith("/done"):
            cid = p[len("/api/coop/"):]
            q   = _load_coop(cid)
            if not q: self._json(404,{"error":"Quest not found"}); return
            if q.get("creator") != u:
                self._json(403,{"error":"Only the creator can delete this quest"}); return
            _coop_path(cid).unlink(missing_ok=True)
            print(f"  ✓ co-op {cid} deleted by {u}")
            self._json(200,{"ok":True}); return

        self._json(404,{"error":"not found"})

    # ── POST ───────────────────────────────────────────────────────────────────
    def do_POST(self):
        p    = self.path.split("?")[0]
        body = self._body()

        # ── REGISTER ──────────────────────────────────────────────────────────
        if p == "/api/register":
            un = (body.get("username") or "").strip()
            pw = body.get("password") or ""
            e  = _val_user(un) or _val_pw(pw)
            if e: self._json(400,{"error":e}); return
            users = _load_users()
            if un.lower() in {x.lower() for x in users}:
                self._json(409,{"error":"Username already taken"}); return
            users[un] = {"hash":_hash_pw(pw),"joined":datetime.now().isoformat()}
            _save_users(users)
            tok = _new_token(); _sessions[tok] = un; _save_sessions()
            print(f"  ✓ registered: {un}")
            self._json(201,{"ok":True,"token":tok,"username":un}); return

        # ── LOGIN ─────────────────────────────────────────────────────────────
        if p == "/api/login":
            un    = (body.get("username") or "").strip()
            pw    = body.get("password") or ""
            users = _load_users()
            match = next((x for x in users if x.lower() == un.lower()), None)
            if not match or not _check_pw(pw, users[match]["hash"]):
                self._json(401,{"error":"Invalid username or password"}); return
            tok = _new_token(); _sessions[tok] = match; _save_sessions()
            print(f"  ✓ login: {match}")
            self._json(200,{"ok":True,"token":tok,"username":match}); return

        # ── LOGOUT ────────────────────────────────────────────────────────────
        if p == "/api/logout":
            tok = self._tok()
            if tok and tok in _sessions:
                print(f"  ✓ logout: {_sessions[tok]}")
                del _sessions[tok]; _save_sessions()
            self._json(200,{"ok":True}); return

        # Everything below requires auth
        u = self._auth()
        if not u: return

        # ── XP ────────────────────────────────────────────────────────────────
        if p == "/api/xp":
            payload = {"level":body.get("level",1),"currentXp":body.get("currentXp",0),
                       "totalXp":body.get("totalXp",0),"xpToNextLevel":body.get("xpToNextLevel",100),
                       "completedQuests":body.get("completedQuests",0),
                       "lastUpdated":datetime.now().isoformat()}
            _wxp(u, payload)
            self._json(200,{"ok":True,"state":payload}); return

        # ── QUESTS ────────────────────────────────────────────────────────────
        if p == "/api/quests":
            if isinstance(body, list):
                _wq(u, body); self._json(200,{"ok":True,"count":len(body)})
            else:
                self._json(400,{"error":"expected a JSON array"})
            return

        # ── ANALYZE ───────────────────────────────────────────────────────────
        if p == "/api/analyze":
            title,desc,cat,diff = (body.get("title",""),body.get("desc","None"),
                                   body.get("category","MAIN"),body.get("difficulty","MEDIUM"))
            errs = []
            if has_claude_key():
                try:
                    subs,src = _claude(title,desc,cat,diff)
                    print(f"  ✓ analyzed via {src} for {u}")
                    self._json(200,{"ok":True,"subquests":subs,"source":src}); return
                except urlerror.HTTPError as e:
                    msg=f"Claude HTTP {e.code}"; errs.append(msg); print(f"  ✗ {msg}")
                except Exception as e:
                    errs.append(f"Claude: {e}"); print(f"  ✗ Claude: {e}")
            if _check_ollama():
                try:
                    subs,src = _ollama(title,desc,cat,diff)
                    print(f"  ✓ analyzed via {src} for {u}")
                    self._json(200,{"ok":True,"subquests":subs,"source":src}); return
                except Exception as e:
                    errs.append(f"Ollama: {e}"); print(f"  ✗ Ollama: {e}")
            else:
                errs.append("Ollama: not running")
            self._json(503,{"error":"No AI backend available","details":errs}); return

        # ── INVITE — generate ─────────────────────────────────────────────────
        if p == "/api/invite":
            code, expires = _new_invite(u)
            print(f"  ✓ invite {code} created by {u}")
            self._json(200,{"ok":True,"code":code,"expires":expires}); return

        # ── INVITE — accept ───────────────────────────────────────────────────
        if p == "/api/invite/accept":
            code = (body.get("code") or "").strip().upper()
            if not code: self._json(400,{"error":"Code required"}); return
            inv = _purge_expired_invites()
            if code not in inv: self._json(404,{"error":"Invalid or expired code"}); return
            owner = inv[code]["owner"]
            if owner == u: self._json(400,{"error":"You cannot accept your own invite"}); return
            if _are_friends(u, owner): self._json(409,{"error":"Already friends"}); return
            _add_friendship(u, owner)
            del inv[code]; _save_invites(inv)
            print(f"  ✓ {u} accepted invite from {owner} — now friends")
            self._json(200,{"ok":True,"newFriend":owner}); return

        # ── CO-OP CREATE ──────────────────────────────────────────────────────
        if p == "/api/coop":
            title    = (body.get("title") or "").strip()
            desc     = body.get("desc","")
            location = body.get("location","").strip()[:120]
            category = body.get("category","MAIN")
            diff     = body.get("difficulty","MEDIUM")
            members  = body.get("members",[])  # [{username, xp}]

            if not title: self._json(400,{"error":"Title required"}); return
            if not isinstance(members, list) or len(members) < 1:
                self._json(400,{"error":"At least one member required (can include yourself)"}); return

            # validate members: must all be friends (or self) with positive xp
            friends = _friends_of(u)
            valid_names = set(friends) | {u}
            for m in members:
                mn = m.get("username","")
                mx = m.get("xp", 0)
                if mn not in valid_names:
                    self._json(400,{"error":f"{mn} is not your friend"}); return
                if not isinstance(mx, int) or mx <= 0:
                    self._json(400,{"error":f"XP for {mn} must be a positive integer"}); return
                # confirm user exists
                if mn not in _load_users():
                    self._json(400,{"error":f"User {mn} not found"}); return

            # deduplicate by username keeping first occurrence
            seen = set(); deduped = []
            for m in members:
                if m["username"] not in seen:
                    seen.add(m["username"]); deduped.append(m)

            cid = secrets.token_hex(8)
            quest = {"id":cid,"title":title,"desc":desc,"location":location,
                     "category":category,"difficulty":diff,"creator":u,
                     "created":datetime.now().isoformat(),
                     "members":[{"username":m["username"],"xp":m["xp"],"done":False}
                                for m in deduped]}
            _save_coop(quest)
            print(f"  ✓ co-op quest '{title}' ({cid}) created by {u}")
            self._json(201,{"ok":True,"quest":quest}); return

        # ── CO-OP DONE ────────────────────────────────────────────────────────
        if p.startswith("/api/coop/") and p.endswith("/done"):
            cid = p[len("/api/coop/"):-len("/done")]
            q   = _load_coop(cid)
            if not q: self._json(404,{"error":"Quest not found"}); return
            member = next((m for m in q["members"] if m["username"]==u), None)
            if not member: self._json(403,{"error":"You are not part of this quest"}); return
            if member["done"]: self._json(409,{"error":"You already completed this quest"}); return

            # award XP
            xp_award = member["xp"]
            xp_data  = _rxp(u)
            xp_data["totalXp"]  = xp_data.get("totalXp",0)  + xp_award
            xp_data["currentXp"]= xp_data.get("currentXp",0) + xp_award
            lvl_up = False
            while xp_data["currentXp"] >= xp_data.get("xpToNextLevel",100):
                xp_data["currentXp"] -= xp_data.get("xpToNextLevel",100)
                xp_data["level"] = xp_data.get("level",1) + 1
                lvl_up = True
            _wxp(u, xp_data)

            member["done"] = True
            _save_coop(q)

            all_done = all(m["done"] for m in q["members"])
            print(f"  ✓ {u} completed co-op '{q['title']}' (+{xp_award} XP)")
            self._json(200,{"ok":True,"xpAwarded":xp_award,"levelUp":lvl_up,
                            "newXp":xp_data,"allDone":all_done}); return

        self._json(404,{"error":"not found"})


# ── Main ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    server   = HTTPServer((args.host, args.port), H)
    local_ip = os.popen("hostname -I 2>/dev/null").read().strip().split()
    local_ip = local_ip[0] if local_ip else "localhost"

    uc = len(_load_users()); fc = len(_load_friends())
    cs = "SET ✓" if has_claude_key() else "not set (Ollama fallback)"
    os_ = "RUNNING ✓" if _check_ollama() else f"not running (model: {OLLAMA_MODEL})"

    print("=" * 58)
    print("  ◈  QUESTLOG SERVER v5  (auth + friends + co-op)")
    print("=" * 58)
    print(f"  Sci-fi        →  http://localhost:{args.port}/")
    print(f"  Fantasy       →  http://localhost:{args.port}/fantasy")
    print(f"  Network       →  http://{local_ip}:{args.port}/")
    print("─" * 58)
    print(f"  Accounts      →  {uc} registered")
    print(f"  Friendships   →  tracked in data/friends.json")
    print(f"  Co-op quests  →  stored in data/coop/")
    print(f"  Claude key    →  {cs}")
    print(f"  Ollama/Hailo  →  {os_}")
    print("=" * 58)
    print("  Press Ctrl+C to stop\n")

    try: server.serve_forever()
    except KeyboardInterrupt: print("\n  Server stopped.")
