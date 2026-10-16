'use strict';
const $ = (s) => document.querySelector(s);
let config = { targetApp: '', buttons: [] };
let editing = false;
let editIndex = -1;

// ---------- server calls
async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Deck': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) { location.reload(); throw new Error('not paired'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(msg, ms = 2500) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

const buzz = (p) => navigator.vibrate && navigator.vibrate(p);

// ---------- icons (SF Symbols-style line icons, 24x24, drawn here)
const ICONS = {
  check: ['Checkmark', 'M5 12.5l4.5 4.5L19 7.5'],
  'check-all': ['Double check', 'M1.5 12.5l4.5 4.5L16 7M11.5 16.5l.5.5L22 7'],
  xmark: ['Xmark', 'M6.5 6.5l11 11M17.5 6.5l-11 11'],
  play: ['Play', 'M8 5.5v13l10.5-6.5z'],
  cycle: ['Cycle', 'M4.5 11a7.5 7.5 0 0 1 13.4-4.2M19 3.5V7h-3.5M19.5 13a7.5 7.5 0 0 1-13.4 4.2M5 20.5V17h3.5'],
  stop: ['Stop', 'M7 7h10v10H7z'],
  sparkles: ['Sparkles', 'M10 3.5l1.6 4.4L16 9.5l-4.4 1.6L10 15.5l-1.6-4.4L4 9.5l4.4-1.6zM18 14l.8 2.2 2.2.8-2.2.8L18 20l-.8-2.2-2.2-.8 2.2-.8z'],
  sparkle: ['Sparkle', 'M12 3.5l2 6.5 6.5 2-6.5 2-2 6.5-2-6.5-6.5-2 6.5-2z'],
  bolt: ['Bolt', 'M13.5 2.5L5 13.5h6.5l-1 8 8.5-11h-6.5z'],
  compress: ['Compress', 'M4 14h6v6M20 10h-6V4M14 10l6.5-6.5M3.5 20.5L10 14'],
  trash: ['Trash', 'M4 6.5h16M9.5 6.5V4h5v2.5M6 6.5l1 14h10l1-14M10 10.5v6M14 10.5v6'],
  up: ['Chevron up', 'M6 15l6-6 6 6'],
  down: ['Chevron down', 'M6 9l6 6 6-6'],
  left: ['Chevron left', 'M15 6l-6 6 6 6'],
  right: ['Chevron right', 'M9 6l6 6-6 6'],
  return: ['Return', 'M19.5 5v6.5a3 3 0 0 1-3 3H5M9 10.5l-4 4 4 4'],
  terminal: ['Terminal', 'M5 7l5 5-5 5M12.5 17.5h7'],
  text: ['Text', 'M5 6.5h14M12 6.5v12'],
  mic: ['Mic', 'M12 3.5a2.5 2.5 0 0 1 2.5 2.5v5a2.5 2.5 0 0 1-5 0V6A2.5 2.5 0 0 1 12 3.5zM6.5 11a5.5 5.5 0 0 0 11 0M12 16.5v4'],
  question: ['Question', 'M9 9a3 3 0 1 1 4.5 2.6c-.9.5-1.5 1.2-1.5 2.2v.7M12 18.5v.5'],
  star: ['Star', 'M12 3.5l2.6 5.5 6 .7-4.5 4.1 1.2 5.9L12 16.8l-5.3 2.9 1.2-5.9-4.5-4.1 6-.7z'],
  undo: ['Undo', 'M9 14.5l-4.5-4.5L9 5.5M4.5 10H15a4.5 4.5 0 0 1 0 9h-3'],
  book: ['Book', 'M5 4.5h11a2 2 0 0 1 2 2v13H7a2 2 0 0 1-2-2zM5 17.5a2 2 0 0 1 2-2h11'],
  agents: ['Agents', 'M9.5 6a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M3.5 18a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M15.5 18a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M10.8 8.3l-3.6 7.4M13.2 8.3l3.6 7.4M8.5 18h7'],
  layers: ['Layers', 'M12 4l8.5 4.5L12 13 3.5 8.5zM3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5'],
  gauge: ['Gauge', 'M4 16.5a8 8 0 1 1 16 0M12 16.5l4-5'],
};
const FILLED = new Set(['play', 'stop']);
const SVG_NS = 'http://www.w3.org/2000/svg';

function iconEl(name) {
  const def = ICONS[name];
  if (!def) { const dot = document.createElement('span'); dot.className = 'dot'; return dot; }
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', def[1]);
  if (FILLED.has(name)) path.setAttribute('class', 'filled');
  svg.appendChild(path);
  return svg;
}

// fill the icon picker once
for (const [name, [title]] of Object.entries(ICONS)) {
  const opt = document.createElement('option');
  opt.value = name; opt.textContent = title;
  $('#f-icon').appendChild(opt);
}

// ---------- grid
function render() {
  const grid = $('#grid');
  grid.textContent = '';
  config.buttons.forEach((b, i) => {
    const el = document.createElement('button');
    el.className = 'key';
    const label = document.createElement('span');
    label.textContent = b.label;
    el.append(iconEl(b.icon), label);
    el.dataset.color = b.color || 'gray';
    el.addEventListener('click', () => (editing ? openEditor(i) : press(b, el)));
    grid.appendChild(el);
  });
}

async function press(b, el) {
  buzz(15);
  el.classList.add('pressed');
  try {
    await api('/api/press', { id: b.id });
  } catch (e) {
    el.classList.add('err'); buzz([60, 60, 60]); toast(e.message, 5000);
    setTimeout(() => el.classList.remove('err'), 1500);
  } finally {
    setTimeout(() => el.classList.remove('pressed'), 120);
  }
}

// ---------- edit mode
function setEditing(on) {
  editing = on;
  document.body.classList.toggle('editing', on);
  $('#edit-bar').hidden = !on;
  $('#dictate').hidden = on;
  $('#edit-toggle').textContent = on ? 'Done' : 'Edit';
  $('#target-app').value = config.targetApp || '';
}

function openEditor(i) {
  editIndex = i;
  const b = config.buttons[i];
  $('#f-label').value = b.label;
  $('#f-macro').value = b.macro;
  $('#f-color').value = b.color || 'gray';
  $('#f-icon').value = b.icon || '';
  $('#btn-dialog').showModal();
}

$('#btn-dialog').addEventListener('close', () => {
  const action = $('#btn-dialog').returnValue;
  const list = config.buttons;
  const b = list[editIndex];
  if (!b) return;
  if (action === 'delete') list.splice(editIndex, 1);
  else {
    b.label = $('#f-label').value.trim() || b.label;
    b.macro = $('#f-macro').value || b.macro;
    b.color = $('#f-color').value;
    b.icon = $('#f-icon').value;
    const j = editIndex + (action === 'left' ? -1 : action === 'right' ? 1 : 0);
    if (j !== editIndex && j >= 0 && j < list.length) [list[editIndex], list[j]] = [list[j], list[editIndex]];
  }
  render();
});

$('#edit-toggle').addEventListener('click', async () => {
  if (editing) { config = await api('/api/config'); render(); } // discard unsaved edits
  setEditing(!editing);
});

$('#add-btn').addEventListener('click', () => {
  config.buttons.push({ id: 'b' + Date.now().toString(36), label: 'New', macro: '{enter}', color: 'gray', icon: '' });
  render();
  openEditor(config.buttons.length - 1);
});

$('#save-btn').addEventListener('click', async () => {
  config.targetApp = $('#target-app').value.trim();
  try {
    await api('/api/config', config);
    toast('Saved'); setEditing(false);
  } catch (e) { toast('Not saved: ' + e.message, 5000); }
});

$('#reset-btn').addEventListener('click', async () => {
  if (!confirm('Restore the default buttons?')) return;
  const def = await fetch('/defaults.json').then((r) => r.json()).catch(() => null);
  if (def) { config = def; render(); toast('Defaults loaded — tap Save to keep'); }
});

// ---------- typing / dictation
const textEl = $('#text');
textEl.addEventListener('input', () => {
  textEl.style.height = 'auto';
  textEl.style.height = textEl.scrollHeight + 'px';
});

async function sendText() {
  const text = textEl.value.trim();
  if (!text) return;
  try {
    await api('/api/type', { text, enter: true });
    textEl.value = ''; textEl.style.height = ''; buzz(15);
  } catch (e) { toast(e.message, 5000); }
}
$('#send').addEventListener('click', sendText);

const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
$('#mic').addEventListener('click', () => {
  if (!Speech) {
    // Plain-HTTP pages can't use the speech API; the keyboard's own mic works everywhere.
    textEl.focus();
    toast('Use the mic on your keyboard to dictate');
    return;
  }
  if (rec) { rec.stop(); return; }
  rec = new Speech();
  rec.interimResults = true;
  rec.continuous = true;
  const base = textEl.value ? textEl.value.trim() + ' ' : '';
  rec.onresult = (ev) => {
    let said = '';
    for (const r of ev.results) said += r[0].transcript;
    textEl.value = base + said;
  };
  rec.onend = () => { rec = null; $('#mic').classList.remove('listening'); };
  rec.onerror = (ev) => toast('Mic: ' + ev.error);
  rec.start();
  $('#mic').classList.add('listening');
  buzz(20);
});

// ---------- live status from Claude Code hooks
function showEvent(ev) {
  $('#status-msg').textContent = ev.message;
  const when = new Date(ev.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  $('#status-sub').textContent = `${ev.project}, ${when}`;
  document.body.dataset.kind = ev.kind;
  if (ev.kind === 'permission') buzz([200, 100, 200]);
  else if (ev.kind === 'done') buzz(120);
}

function connect() {
  const es = new EventSource('/api/events');
  es.onopen = () => $('#conn').classList.add('on');
  es.onmessage = (m) => { try { showEvent(JSON.parse(m.data)); } catch {} };
  es.onerror = () => { $('#conn').classList.remove('on'); }; // EventSource retries by itself
}

// ---------- keep the phone screen on
async function keepAwake() {
  try { if ('wakeLock' in navigator) await navigator.wakeLock.request('screen'); } catch {}
}
document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && keepAwake());

(async function start() {
  try { config = await api('/api/config'); } catch (e) { toast(e.message, 5000); }
  render();
  connect();
  keepAwake();
})();
