/**
 * QuestLog — Friends & Co-op module (friends.js)
 * Loaded by both quest-log.html and quest-log-fantasy.html.
 *
 * Live updates: polls /api/poll every 5s, diffs against local state,
 * and surgically updates only changed DOM elements (XP bars, co-op cards).
 *
 * Expects the host page to expose:
 *   SERVER_BASE, authToken, authUsername, authHeaders(),
 *   xp, awardXp(), save(), render(), notify()
 *
 * Renders into:
 *   #friends-panel-body   — friend list + invite UI
 *   #coop-list            — co-op quest list
 *   #coop-form-area       — co-op member builder
 */

// ── State ─────────────────────────────────────────────────────────────────────
let friends    = [];
let coopQuests = [];

// ── Poll config ───────────────────────────────────────────────────────────────
const POLL_FAST = 5000;
const POLL_SLOW = 15000;
let _pollTimer   = null;
let _pollRunning = false;

// ── API helper ────────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const res = await fetch(`${SERVER_BASE}${path}`, {
    ...opts,
    headers: { ...(opts.headers || {}), ...authHeaders() }
  });
  if (res.status === 401) { logout(); return null; }
  return res;
}

// ── Visibility-aware polling ──────────────────────────────────────────────────
// Pause when tab is hidden (saves battery/resources), resume immediately on return
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    clearTimeout(_pollTimer);
  } else {
    _poll(); // catch up immediately when tab becomes visible again
  }
});

// ── Initial full load ─────────────────────────────────────────────────────────
async function loadFriendsData() {
  try {
    const res = await apiFetch('/api/poll');
    if (!res || !res.ok) return;
    const data = await res.json();
    friends    = data.friends || [];
    coopQuests = data.coop    || [];
  } catch(e) { console.warn('Friends load error:', e); }
  renderFriends();
  renderCoopQuests();
  renderCoopMemberBuilder();
  _schedulePoll(POLL_FAST);
}

// ── Live poll loop ────────────────────────────────────────────────────────────
function _schedulePoll(delay) {
  clearTimeout(_pollTimer);
  _pollTimer = setTimeout(_poll, delay);
}

async function _poll() {
  if (_pollRunning) { _schedulePoll(POLL_FAST); return; }
  _pollRunning = true;
  try {
    const res = await apiFetch('/api/poll');
    if (!res)      { _schedulePoll(POLL_SLOW); return; }
    if (!res.ok)   { _schedulePoll(POLL_SLOW); return; }
    const data = await res.json();
    _diffFriends(data.friends || []);
    _diffCoop(data.coop       || []);
    _schedulePoll(POLL_FAST);
  } catch(e) {
    _schedulePoll(POLL_SLOW);
  } finally {
    _pollRunning = false;
  }
}

// ── Diff: friend XP bars ──────────────────────────────────────────────────────
function _diffFriends(newFriends) {
  const oldNames = friends.map(f => f.username).sort().join(',');
  const newNames = newFriends.map(f => f.username).sort().join(',');
  if (oldNames !== newNames) {
    friends = newFriends;
    renderFriends();
    renderCoopMemberBuilder();
    return;
  }

  for (const nf of newFriends) {
    const old = friends.find(f => f.username === nf.username);
    if (!old) continue;
    if (old.currentXp === nf.currentXp && old.totalXp === nf.totalXp && old.level === nf.level) continue;

    old.currentXp = nf.currentXp;
    old.totalXp   = nf.totalXp;
    old.level     = nf.level;

    // Patch the specific friend row in the DOM
    const rows = document.querySelectorAll('.friend-row');
    for (const row of rows) {
      const nameEl = row.querySelector('.friend-name');
      if (!nameEl || nameEl.textContent !== nf.username) continue;
      const fill = row.querySelector('.friend-xp-fill');
      const num  = row.querySelector('.friend-xp-num');
      const lvl  = row.querySelector('.friend-level');
      if (fill) fill.style.width  = Math.min((nf.currentXp / 100) * 100, 100) + '%';
      if (num)  num.textContent   = nf.totalXp + ' XP';
      if (lvl)  lvl.textContent   = 'Lv.' + nf.level;
      if (fill) {
        fill.classList.add('xp-updated');
        setTimeout(() => fill.classList.remove('xp-updated'), 1200);
      }
      break;
    }
  }
}

// ── Diff: co-op cards ─────────────────────────────────────────────────────────
function _diffCoop(newCoop) {
  const oldIds = coopQuests.map(q => q.id).sort().join(',');
  const newIds = newCoop.map(q => q.id).sort().join(',');
  if (oldIds !== newIds) {
    coopQuests = newCoop;
    renderCoopQuests();
    return;
  }

  let anyChanged = false;
  for (const nq of newCoop) {
    const oq = coopQuests.find(q => q.id === nq.id);
    if (!oq) continue;
    const oldSig = oq.members.map(m => `${m.username}:${m.done}`).join('|');
    const newSig = nq.members.map(m => `${m.username}:${m.done}`).join('|');
    if (oldSig === newSig) continue;

    anyChanged = true;

    // Notify about teammate completions
    for (const nm of nq.members) {
      const om = oq.members.find(m => m.username === nm.username);
      if (om && !om.done && nm.done && nm.username !== authUsername) {
        notify(`${nm.username} completed "${nq.title}"!`, 'sync');
      }
    }

    oq.members = nq.members;

    // Patch card DOM in place
    const card = document.getElementById(`coop-${nq.id}`);
    if (!card) continue;

    const totalDone = nq.members.filter(m => m.done).length;
    const pct       = Math.round((totalDone / nq.members.length) * 100);
    const allDone   = totalDone === nq.members.length;

    const fill  = card.querySelector('.coop-progress-fill');
    const label = card.querySelector('.coop-progress-label');
    if (fill)  fill.style.width  = pct + '%';
    if (label) label.textContent = `${totalDone}/${nq.members.length} done`;
    card.classList.toggle('all-done', allDone);

    const membersEl = card.querySelector('.coop-members');
    if (membersEl) {
      membersEl.innerHTML = nq.members.map(m => `
        <div class="coop-member-status ${m.done ? 'done' : ''}">
          <span class="coop-dot ${m.done ? 'done' : ''}"></span>
          <span>${esc(m.username)}</span>
          <span class="coop-member-xp-tag">${m.xp} XP</span>
        </div>`).join('');
    }

    const myMember = nq.members.find(m => m.username === authUsername);
    if (myMember?.done || allDone) {
      const doneBtn = card.querySelector('.coop-done-btn');
      if (doneBtn) doneBtn.remove();
    }

    card.classList.add('coop-updated');
    setTimeout(() => card.classList.remove('coop-updated'), 1500);
  }

  if (anyChanged) coopQuests = newCoop;
}

// ── INVITE ────────────────────────────────────────────────────────────────────
async function generateInvite() {
  const btn = document.getElementById('invite-btn');
  const out = document.getElementById('invite-output');
  if (btn) btn.disabled = true;
  try {
    const res  = await apiFetch('/api/invite', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
    if (!res || !res.ok) { notify('Failed to generate invite', 'err'); return; }
    const data = await res.json();
    if (out) { out.textContent = data.code; out.dataset.code = data.code; }
    notify(`Invite code: ${data.code} (24h)`, 'sync');
  } catch(e) {
    notify('Could not generate invite', 'err');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function copyInviteCode() {
  const out = document.getElementById('invite-output');
  if (!out || !out.dataset.code) return;
  await navigator.clipboard.writeText(out.dataset.code);
  notify('Invite code copied!', 'sync');
}

async function acceptInvite() {
  const inp  = document.getElementById('invite-input');
  const code = (inp?.value || '').trim().toUpperCase();
  if (!code) { notify('Enter a code first', 'err'); return; }
  try {
    const res  = await apiFetch('/api/invite/accept', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code })
    });
    if (!res) return;
    const data = await res.json();
    if (!res.ok) { notify(data.error || 'Invalid code', 'err'); return; }
    if (inp) inp.value = '';
    notify(`Now friends with ${data.newFriend}!`, 'sync');
    await loadFriendsData();
  } catch(e) { notify('Could not accept invite', 'err'); }
}

async function removeFriend(username) {
  if (!confirm(`Remove ${username} from your friends?`)) return;
  try {
    const res = await apiFetch(`/api/friends/${encodeURIComponent(username)}`, { method: 'DELETE' });
    if (!res || !res.ok) { notify('Could not remove friend', 'err'); return; }
    notify(`${username} removed from friends`, 'sync');
    await loadFriendsData();
  } catch(e) { notify('Error removing friend', 'err'); }
}

// ── CO-OP form ────────────────────────────────────────────────────────────────
let coopMembers = [];

function refreshCoopMemberOptions() {
  const sel = document.getElementById('coop-member-select');
  if (!sel) return;
  const used = new Set(coopMembers.map(m => m.username));
  const opts = [authUsername, ...friends.map(f => f.username)].filter(u => !used.has(u));
  sel.innerHTML = opts.map(u =>
    `<option value="${u}">${u}${u === authUsername ? ' (you)' : ''}</option>`
  ).join('');
}

function addCoopMember() {
  const sel  = document.getElementById('coop-member-select');
  const xpIn = document.getElementById('coop-member-xp');
  if (!sel || !xpIn) return;
  const username = sel.value;
  const xpVal    = parseInt(xpIn.value) || 0;
  if (!username)  { notify('Select a member', 'err'); return; }
  if (xpVal <= 0) { notify('XP must be > 0', 'err'); return; }
  if (coopMembers.find(m => m.username === username)) { notify('Already added', 'err'); return; }
  coopMembers.push({ username, xp: xpVal });
  renderCoopMemberBuilder();
}

function removeCoopMember(username) {
  coopMembers = coopMembers.filter(m => m.username !== username);
  renderCoopMemberBuilder();
}

function renderCoopMemberBuilder() {
  const area = document.getElementById('coop-form-area');
  if (!area) return;
  refreshCoopMemberOptions();
  const list = coopMembers.map(m => `
    <div class="coop-member-row">
      <span class="coop-member-name">${esc(m.username)}${m.username === authUsername ? ' ★' : ''}</span>
      <span class="coop-member-xp">${m.xp} XP</span>
      <button class="coop-remove-btn" onclick="removeCoopMember('${esc(m.username)}')">✕</button>
    </div>`).join('');
  area.innerHTML = list + `
    <div class="coop-add-row">
      <select id="coop-member-select" class="form-select coop-sel"></select>
      <input  id="coop-member-xp"     class="form-input coop-xp-in" type="number" min="1" max="500" value="50" placeholder="XP">
      <button class="coop-add-btn" onclick="addCoopMember()">+ Add</button>
    </div>`;
  refreshCoopMemberOptions();
}

async function createCoopQuest() {
  const title    = document.getElementById('quest-title')?.value.trim();
  const desc     = document.getElementById('quest-desc')?.value.trim()     || '';
  const location = document.getElementById('quest-location')?.value.trim() || '';
  const cat      = document.getElementById('quest-cat')?.value  || 'MAIN';
  const diff     = document.getElementById('quest-diff')?.value || 'MEDIUM';
  if (!title)                   { notify('Quest title required', 'err'); return; }
  if (coopMembers.length === 0) { notify('Add at least one member', 'err'); return; }
  try {
    const res  = await apiFetch('/api/coop', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, desc, location, category: cat, difficulty: diff, members: coopMembers })
    });
    if (!res) return;
    const data = await res.json();
    if (!res.ok) { notify(data.error || 'Could not create co-op quest', 'err'); return; }
    document.getElementById('quest-title').value    = '';
    document.getElementById('quest-desc').value     = '';
    const locEl = document.getElementById('quest-location');
    if (locEl) locEl.value = '';
    coopMembers = [];
    renderCoopMemberBuilder();
    await loadFriendsData();
    notify('Co-op quest posted!', 'sync');
  } catch(e) { notify('Error creating co-op quest', 'err'); }
}

async function completeCoopQuest(id) {
  try {
    const res  = await apiFetch(`/api/coop/${id}/done`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    if (!res) return;
    const data = await res.json();
    if (!res.ok) { notify(data.error || 'Error', 'err'); return; }
    awardXp(data.xpAwarded);
    notify(`+${data.xpAwarded} XP earned${data.allDone ? ' — quest complete!' : ''}`, 'xp');
    save(); render();
    await loadFriendsData();
  } catch(e) { notify('Error completing co-op quest', 'err'); }
}

async function deleteCoopQuest(id) {
  if (!confirm('Delete this co-op quest?')) return;
  try {
    const res = await apiFetch(`/api/coop/${id}`, { method: 'DELETE' });
    if (!res || !res.ok) { notify('Could not delete', 'err'); return; }
    notify('Co-op quest removed', 'sync');
    await loadFriendsData();
  } catch(e) { notify('Error deleting co-op quest', 'err'); }
}

// ── Full renders ──────────────────────────────────────────────────────────────
function renderFriends() {
  const body = document.getElementById('friends-panel-body');
  if (!body) return;
  const friendList = friends.length
    ? friends.map(f => `
      <div class="friend-row">
        <div class="friend-info">
          <span class="friend-name">${esc(f.username)}</span>
          <span class="friend-level">Lv.${f.level}</span>
        </div>
        <div class="friend-xp-bar-wrap">
          <div class="friend-xp-bar">
            <div class="friend-xp-fill" style="width:${Math.min((f.currentXp/100)*100,100)}%"></div>
          </div>
          <span class="friend-xp-num">${f.totalXp} XP</span>
        </div>
        <button class="friend-remove-btn" onclick="removeFriend('${esc(f.username)}')" title="Remove friend">✕</button>
      </div>`).join('')
    : '<div class="friend-empty">No friends yet — generate an invite!</div>';

  body.innerHTML = `
    <div class="friend-list">${friendList}</div>
    <div class="invite-section">
      <div class="invite-row">
        <button id="invite-btn" class="invite-gen-btn" onclick="generateInvite()">Generate Invite Code</button>
        <div id="invite-output" class="invite-code" onclick="copyInviteCode()" title="Click to copy"></div>
      </div>
      <div class="invite-row">
        <input id="invite-input" class="form-input invite-in" placeholder="Paste a friend's code..." maxlength="20">
        <button class="invite-accept-btn" onclick="acceptInvite()">Accept</button>
      </div>
    </div>`;
}

function renderCoopQuests() {
  const el = document.getElementById('coop-list');
  if (!el) return;
  if (!coopQuests.length) {
    el.innerHTML = '<div class="coop-empty">No co-op quests yet.</div>';
    return;
  }
  el.innerHTML = coopQuests.map(q => {
    const myMember  = q.members.find(m => m.username === authUsername);
    const myDone    = myMember?.done || false;
    const totalDone = q.members.filter(m => m.done).length;
    const pct       = Math.round((totalDone / q.members.length) * 100);
    const isCreator = q.creator === authUsername;
    const allDone   = totalDone === q.members.length;
    const memberRows = q.members.map(m => `
      <div class="coop-member-status ${m.done ? 'done' : ''}">
        <span class="coop-dot ${m.done ? 'done' : ''}"></span>
        <span>${esc(m.username)}</span>
        <span class="coop-member-xp-tag">${m.xp} XP</span>
      </div>`).join('');
    return `
      <div class="coop-card ${allDone ? 'all-done' : ''}" id="coop-${q.id}">
        <div class="coop-card-top">
          <div class="coop-body">
            <div class="coop-title">${esc(q.title)}</div>
            ${q.desc     ? `<div class="coop-desc">${esc(q.desc)}</div>` : ''}
            ${q.location ? `<div class="quest-location"><a href="https://www.google.com/maps/search/${encodeURIComponent(q.location)}" target="_blank" rel="noopener">${esc(q.location)}</a></div>` : ''}
            <div class="coop-meta-tags">
              <span class="tag tag-category">${q.category}</span>
              <span class="tag ${diffClass(q.difficulty)}">${q.difficulty}</span>
              <span class="tag coop-tag">⚔ CO-OP</span>
            </div>
          </div>
          <div class="coop-actions">
            ${!myDone && !allDone ? `<button class="coop-done-btn" onclick="completeCoopQuest('${q.id}')">✓ Done</button>` : ''}
            ${isCreator ? `<button class="icon-btn del" onclick="deleteCoopQuest('${q.id}')" title="Delete">✕</button>` : ''}
          </div>
        </div>
        <div class="coop-progress">
          <div class="coop-progress-bar"><div class="coop-progress-fill" style="width:${pct}%"></div></div>
          <span class="coop-progress-label">${totalDone}/${q.members.length} done</span>
        </div>
        <div class="coop-members">${memberRows}</div>
      </div>`;
  }).join('');
}

// ── Co-op mode toggle ─────────────────────────────────────────────────────────
function toggleCoopMode() {
  const area    = document.getElementById('coop-form-area');
  const wrap    = document.getElementById('coop-form-wrap');
  const btn     = document.getElementById('coop-deploy-btn');
  const soloBtn = document.getElementById('solo-deploy-btn');
  const isOn    = wrap?.classList.toggle('active');
  if (isOn) {
    if (!coopMembers.find(m => m.username === authUsername)) {
      const soloXp = parseInt(document.getElementById('quest-xp')?.value) || 50;
      coopMembers  = [{ username: authUsername, xp: soloXp }];
    }
    renderCoopMemberBuilder();
    if (btn)     btn.style.display     = '';
    if (soloBtn) soloBtn.style.display = 'none';
  } else {
    coopMembers = [];
    if (area)    area.innerHTML        = '';
    if (btn)     btn.style.display     = 'none';
    if (soloBtn) soloBtn.style.display = '';
  }
}

// ── esc helper ────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
