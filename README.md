# QuestLog — Raspberry Pi 5

A dual-theme quest log app with user accounts, XP tracking, AI-powered sub-quest breakdown, friends, co-op quests, and a live REST API. Built to run locally on a Raspberry Pi 5.

---

## Themes

| Theme | URL | Style |
|---|---|---|
| Sci-fi HUD | `http://localhost:8080/` | Orbitron font, cyan/orange, scanlines |
| Fantasy parchment | `http://localhost:8080/fantasy` | Cinzel font, gold/crimson, aged paper |

Both themes share the same account, XP, and quest data. Log in once and both work.

---

## Quick Start

```bash
# Install the one required dependency
pip install bcrypt --break-system-packages

# Start the server
cd questlog/
python3 server.py
```

Then open **http://localhost:8080** in your browser, register an account, and start adding quests.

---

## File Structure

```
questlog/
├── server.py                  ← Python HTTP server + REST API
├── quest-log.html             ← Sci-fi HUD theme
├── quest-log-fantasy.html     ← Fantasy parchment theme
├── friends.js                 ← Friends & co-op module (shared by both themes)
├── start.sh                   ← Convenience launcher
├── setup_hailo.sh             ← One-time Hailo AI HAT+ setup
├── README.md                  ← This file
└── data/
    ├── users.json             ← Registered accounts (bcrypt-hashed passwords)
    ├── sessions.json          ← Active session tokens
    ├── invites.json           ← Pending friend invite codes
    ├── friends.json           ← Friendship graph
    ├── users/<username>/
    │   ├── xp.json            ← Per-user XP state
    │   └── quests.json        ← Per-user quest list
    └── coop/
        └── <id>.json          ← Each co-op quest
```

---

## Features

### Accounts & Auth
- Register and log in with a username and password
- Passwords are bcrypt-hashed (cost 12) — never stored in plain text
- Sessions persist across server restarts via `data/sessions.json`
- Log in once and both themes share the session

### Quests
- Add quests with a title, description, category, priority, XP reward, and optional location
- **Categories:** MAIN, SIDE, DAILY, WEEKLY, URGENT, RECON, BUILD
- **Daily quests** reset to incomplete every midnight — XP already earned is kept
- **Weekly quests** reset every Monday at midnight — XP already earned is kept
- Filter by category, sort by newest / priority / XP
- Complete or delete quests; completing a quest banks XP into your level

### Sub-quests
- **Manual:** click `+` on any quest card to add sub-steps with a title and difficulty — no AI required
- **AI-generated:** click the AI button to have Claude or Ollama break the quest into 3–5 actionable steps automatically
- Sub-quests split the parent quest's XP equally; the parent auto-completes when all steps are done
- Individual sub-quests can be deleted (XP is clawed back if the step was already completed)

### XP & Levels
- Every 100 XP earns a level
- XP and level are stored server-side and synced on every change
- Sci-fi displays level as a zero-padded number (`01`, `02`…); fantasy uses Roman numerals (I, II…)
- XP is labelled "Glory" in the fantasy theme

### Location
- Quests and co-op quests support an optional location field
- Locations render as a clickable link that opens Google Maps pre-searched for that place

### Friends
- Generate a one-time invite code (valid 24 hours) and share it however you like
- The recipient pastes the code to add you as a friend — no account-linking required
- Friends panel shows each friend's level and XP bar, updated live every 5 seconds
- Remove a friend at any time

### Co-op Quests
- Toggle co-op mode when creating a quest to invite friends
- The quest creator sets exactly how much XP each member earns (no forced equal split)
- Each member independently marks their share done and receives their XP immediately
- A progress bar shows how many members have completed their part
- Co-op quests appear for all members simultaneously and update live

### Live Updates (multi-user)
- Friend XP bars and co-op quest progress poll the server every 5 seconds
- Only changed data triggers DOM updates — no full re-renders on every poll
- A notification fires when a teammate completes their share of a co-op quest
- Polling pauses automatically when the browser tab is hidden and resumes on return

### AI Sub-quest Breakdown
The server tries backends in order:

1. **Claude** (Anthropic API) — set `ANTHROPIC_API_KEY` in `server.py`
2. **Ollama** (local LLM, e.g. phi3:mini) — runs on the Pi via Hailo HAT+
3. Returns a 503 error if neither is available

### Mobile Support
- Sidebar collapses into a slide-in drawer on small screens, toggled by a floating ☰ button
- Quest cards have enlarged tap targets (28×28px checkboxes, 36×36px action buttons)
- Auth box and forms scale to fit any phone screen

---

## API Endpoints

All data endpoints require an `Authorization: Bearer <token>` header. Get a token by logging in via `POST /api/login`.

### Auth

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/register` | `{username, password}` | `{token, username}` |
| POST | `/api/login` | `{username, password}` | `{token, username}` |
| POST | `/api/logout` | — | `{ok}` |
| GET | `/api/me` | — | `{username, joined}` |

### Personal Data

| Method | Path | Description |
|---|---|---|
| GET | `/api/xp` | Your current XP state |
| POST | `/api/xp` | Update your XP state |
| GET | `/api/quests` | Your quest list |
| POST | `/api/quests` | Replace your quest list |
| POST | `/api/analyze` | AI sub-quest breakdown |
| GET | `/api/status` | Which AI backend is active |

### Friends

| Method | Path | Description |
|---|---|---|
| GET | `/api/friends` | Your friends list with level + XP |
| POST | `/api/invite` | Generate a 24h invite code |
| POST | `/api/invite/accept` | Accept a code, add friendship |
| DELETE | `/api/friends/<username>` | Remove a friend |

### Co-op Quests

| Method | Path | Description |
|---|---|---|
| GET | `/api/coop` | All co-op quests you're part of |
| POST | `/api/coop` | Create a co-op quest |
| POST | `/api/coop/<id>/done` | Mark your share complete, awards XP |
| DELETE | `/api/coop/<id>` | Delete (creator only) |

### Live Poll

| Method | Path | Description |
|---|---|---|
| GET | `/api/poll` | Friends XP + co-op quests in one call (used by the 5s live update loop) |

---

## Configuration

Edit the top of `server.py`:

```python
ANTHROPIC_API_KEY = "YOUR_API_KEY_HERE"   # Claude API key (optional)
OLLAMA_BASE_URL   = "http://localhost:11434"
OLLAMA_MODEL      = "phi3:mini"
INVITE_TTL_HOURS  = 24                    # How long invite codes stay valid
```

---

## Consuming the XP API from Another App

```javascript
// Must include a valid auth token
const res = await fetch('http://<pi-ip>:8080/api/xp', {
  headers: { 'Authorization': 'Bearer <token>' }
});
const xp = await res.json();
// { level, currentXp, totalXp, xpToNextLevel, completedQuests, lastUpdated }
console.log(`Level ${xp.level} — ${xp.totalXp} total XP`);
```

CORS is enabled for all origins so any browser app on your network can call it.

---

## Hailo AI HAT+ Setup

```bash
bash setup_hailo.sh
sudo reboot
hailortcli fw-control identify   # verify the HAT is detected
```

---

## Auto-start on Boot

**Crontab** (`crontab -e`):
```
@reboot cd /home/pi/questlog && python3 server.py >> questlog.log 2>&1 &
```

**Systemd** (recommended):
```ini
# /etc/systemd/system/questlog.service
[Unit]
Description=QuestLog Server
After=network.target

[Service]
WorkingDirectory=/home/pi/questlog
ExecStart=/usr/bin/python3 server.py --port 8080
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable questlog
sudo systemctl start questlog
```

---

## Custom Port

```bash
python3 server.py --port 9000
# or
bash start.sh 9000
```

---

## Running on Windows

The app runs on Windows with no code changes required.

**Starting the server:**
```bat
python server.py
```
Or double-click `start.bat` (accepts an optional port number as an argument, defaults to 8080).

**Installing bcrypt on Windows:**
```bat
pip install bcrypt
```
The `--break-system-packages` flag is not needed on Windows.

**Auto-start on boot (Task Scheduler):**

1. Open Task Scheduler and click **Create Basic Task**
2. Set the trigger to **When the computer starts**
3. Set the action to **Start a program**
4. Program: `python`
5. Arguments: `server.py --port 8080`
6. Start in: `C:\path\to\questlog`
7. Check **Run whether user is logged on or not**

Or use a startup folder shortcut — press `Win+R`, type `shell:startup`, and drop a shortcut to `start.bat` in that folder.

**Notes:**
- Use `python` instead of `python3` on Windows (unless you've aliased it)
- `start.sh` and `setup_hailo.sh` are not used on Windows — ignore them
- Everything else (the HTML themes, API, friends, co-op, data files) works identically
