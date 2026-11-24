/* ==========================================================================
   PSYCHO-C.A.R.L.A. — HUD controller
   Drives the holographic console: WebSocket state, agent dispatch, tier
   switching, memory inspection, cron management, telemetry and the config
   editor.
   ========================================================================== */
'use strict';

/* What C.A.R.L.A. calls the user ([persona] user_name; default "Psycho").
   Refreshed from /api/status on boot. */
let USER_NAME = 'Psycho';

const state = {
  ws: null,
  agents: [],
  tiers: [],
  activeTier: '',
  activeAgent: 'orchestrator',
  sessionId: 'default',
  busy: false,
  lastSpeech: '',
  lastDisplay: '',
  micOn: false,
  reconnectDelay: 900,
  connected: false,
};

// --------------------------------------------------------------------------- //
// Utilities
// --------------------------------------------------------------------------- //
const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function fmtMs(ms) {
  if (!ms && ms !== 0) return '—';
  if (ms >= 1000) return (ms / 1000).toFixed(2) + 's';
  return Math.round(ms) + 'ms';
}

function fmtNum(n) {
  if (n === null || n === undefined) return '—';
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(Math.round(n * 100) / 100);
}

function toast(msg, kind) {
  const t = el('div', 'toast' + (kind ? ' ' + kind : ''), msg);
  $('toasts').appendChild(t);
  setTimeout(() => {
    t.style.opacity = '0';
    t.style.transition = 'opacity .3s';
    setTimeout(() => t.remove(), 320);
  }, 2600);
}

function escapeHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function api(path, options) {
  const res = await fetch(path, Object.assign({
    headers: { 'Content-Type': 'application/json' },
  }, options || {}));
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* noop */ }
    throw new Error(detail);
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.text();
}

/* --------------------------------------------------------------------------
   Markdown renderer — dependency-free, handles code fences, tables, lists.
   ------------------------------------------------------------------------ */
const CODE_TOKEN = '\u0000CODEBLOCK\u0000';

function renderMarkdown(src) {
  if (!src) return '';

  // 1. Pull fenced code blocks out first so nothing else touches them.
  const blocks = [];
  let text = src.replace(/```([\w+\-.]*)\r?\n([\s\S]*?)```/g, (m, lang, code) => {
    blocks.push(
      '<pre><code class="lang-' + (lang || 'text').toLowerCase() + '">' +
      code.replace(/\r/g, '').replace(/\n$/, '') + '</code></pre>'
    );
    return CODE_TOKEN + (blocks.length - 1) + CODE_TOKEN;
  });

  // 2. Inline formatting.
  text = escapeHtml(text)
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/^######\s+(.*)$/gm, '<h6>$1</h6>')
    .replace(/^#####\s+(.*)$/gm, '<h5>$1</h5>')
    .replace(/^####\s+(.*)$/gm, '<h4>$1</h4>')
    .replace(/^###\s+(.*)$/gm, '<h3>$1</h3>')
    .replace(/^##\s+(.*)$/gm, '<h3>$1</h3>')
    .replace(/^#\s+(.*)$/gm, '<h3>$1</h3>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/^&gt;\s?(.*)$/gm, '<blockquote>$1</blockquote>');

  // 3. Tables.
  text = text.replace(
    /(\|[^\n]*\|[ \t]*\r?\n\|[\s:\-|]+\|[ \t]*\r?\n(?:\|[^\n]*\|[ \t]*(?:\r?\n|$))*)/g,
    (block) => {
      const rows = block.trim().split('\n').filter(Boolean);
      if (rows.length < 2) return block;
      const head = rows[0].split('|').slice(1, -1).map(c => c.trim());
      const body = rows.slice(2)
        .map(r => r.split('|').slice(1, -1).map(c => c.trim()))
        .filter(cells => cells.length && cells.some(c => c !== ''));
      let html = '<table><thead><tr>' +
        head.map(h => '<th>' + h + '</th>').join('') +
        '</tr></thead><tbody>';
      body.forEach(r => {
        html += '<tr>' + r.map(c => '<td>' + c + '</td>').join('') + '</tr>';
      });
      return html + '</tbody></table>';
    }
  );

  // 4. Bullet and numbered lists.
  text = text.replace(/(?:^[ \t]*[-*+][ \t]+.*\r?\n?)+/gm, (block) => {
    const items = block.trim().split('\n')
      .map(l => '<li>' + l.replace(/^[ \t]*[-*+][ \t]+/, '') + '</li>');
    return '<ul>' + items.join('') + '</ul>\n';
  });
  text = text.replace(/(?:^[ \t]*\d+[.)][ \t]+.*\r?\n?)+/gm, (block) => {
    const items = block.trim().split('\n')
      .map(l => '<li>' + l.replace(/^[ \t]*\d+[.)][ \t]+/, '') + '</li>');
    return '<ol>' + items.join('') + '</ol>\n';
  });

  // 5. Paragraphs.
  text = text.split(/\r?\n{2,}/).map(p => {
    const t = p.trim();
    if (!t) return '';
    if (/^<(pre|ul|ol|table|h[1-6]|blockquote)/.test(t)) return t;
    return '<p>' + t.replace(/\r?\n/g, '<br>') + '</p>';
  }).join('\n');

  // 6. Restore code blocks.
  text = text.replace(new RegExp(CODE_TOKEN + '(\\d+)' + CODE_TOKEN, 'g'),
    (m, i) => blocks[parseInt(i, 10)]);

  return text;
}

/* --------------------------------------------------------------------------
   Voice Core — the animated waveform ring
   ------------------------------------------------------------------------ */
const core = (() => {
  const paths = Array.from(document.querySelectorAll('.core-wave .wave-path'));
  const baseR = { 0: 76, 1: 66, 2: 56 };
  let raf = null;
  let phase = 0;
  let mode = 'idle';   // idle | listening | speaking
  let amp = 0.2;
  let target = 0.2;

  function pointOnCircle(r, angle) {
    return [100 + r * Math.cos(angle), 100 + r * Math.sin(angle)];
  }

  function buildPath(r, harmonics, time) {
    const steps = 128;
    let d = '';
    for (let i = 0; i <= steps; i++) {
      const angle = (i / steps) * Math.PI * 2;
      let displacement = 0;
      for (let h = 0; h < harmonics; h++) {
        displacement +=
          Math.sin(angle * (h + 2) + time * (1.4 + h * 0.55) + h * 1.7) / (h + 1.6);
      }
      const rr = r + displacement * amp * (mode === 'idle' ? 0.7 : 3.4);
      const p = pointOnCircle(rr, angle);
      d += (i === 0 ? 'M' : 'L') + p[0].toFixed(2) + ' ' + p[1].toFixed(2) + ' ';
    }
    return d + 'Z';
  }

  function tick() {
    phase += 0.016;
    amp += (target - amp) * 0.12;
    paths.forEach((p, idx) => {
      const r = baseR[idx] || 60;
      p.setAttribute('d', buildPath(r, idx === 0 ? 4 : (idx === 1 ? 3 : 2), phase));
    });
    raf = requestAnimationFrame(tick);
  }

  function setMode(m) {
    mode = m;
    const elCore = $('voiceCore');
    elCore.classList.remove('listening', 'speaking');
    if (m === 'listening') elCore.classList.add('listening');
    if (m === 'speaking') elCore.classList.add('speaking');
    target = (m === 'idle') ? 0.2 : 1.0;
  }

  function setState(label, cls) {
    const s = $('coreState');
    s.textContent = label;
    s.className = 'core-state' + (cls ? ' ' + cls : '');
  }

  function start() { if (!raf) raf = requestAnimationFrame(tick); }

  function pulse() {
    target = 1.0;
    setTimeout(() => { target = (mode === 'idle') ? 0.2 : 1.0; }, 420);
  }

  return { setMode, setState, start, pulse };
})();

/* --------------------------------------------------------------------------
   WebSocket
   ------------------------------------------------------------------------ */
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(proto + '://' + location.host + '/ws');
  state.ws = ws;

  ws.onopen = () => {
    state.connected = true;
    state.reconnectDelay = 900;
    $('hdrLink').textContent = 'ONLINE';
    $('hdrLink').className = 'hstat-value';
    bootLine('Link established');
  };

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    handleEvent(msg.type || '', msg.data || {});
  };

  ws.onclose = () => {
    state.connected = false;
    $('hdrLink').textContent = 'OFFLINE';
    $('hdrLink').className = 'hstat-value bad';
    setTimeout(connectWS, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 12000);
  };

  ws.onerror = () => { /* handled by onclose */ };
}

function bootLine(text) {
  const b = $('bootLine');
  if (b) b.textContent = text;
}

function handleEvent(type, data) {
  switch (type) {
    case 'snapshot':
      applySnapshot(data);
      break;
    case 'telemetry':
      applyTelemetry(data);
      break;
    case 'chat_user':
      appendUser(data.prompt);
      break;
    case 'chat_response':
    case 'agent_response':
      appendAssistant(data);
      break;
    case 'voice_state':
      applyVoice(data);
      break;
    case 'wake_word':
      toast('Wake word detected', 'ok');
      core.pulse();
      break;
    case 'voice_command':
      $('prompt').value = data.text || '';
      sendPrompt();
      break;
    case 'tier_changed':
      $('hdrTier').textContent = data.tier;
      loadTiers();
      break;
    case 'agent_status':
      setAgentBusy(data.agent, data.state === 'working');
      break;
    case 'job_added':
    case 'job_removed':
    case 'job_completed':
    case 'job_failed':
      loadJobs();
      break;
    case 'memory_changed':
      loadMemory();
      break;
    case 'config_reloaded':
      toast('Configuration reloaded', 'ok');
      break;
    default:
      break;
  }
}

/* --------------------------------------------------------------------------
   Rendering
   ------------------------------------------------------------------------ */
function applySnapshot(snap) {
  if (!snap) return;
  if (snap.user_name) USER_NAME = snap.user_name;
  state.agents = snap.agents || [];
  state.activeTier = snap.active_tier || '';
  $('hdrTier').textContent = state.activeTier || '—';
  renderAgents();
  renderTiers(snap.tiers || []);
  if (snap.tools) renderTools(snap.tools);
  if (snap.jobs) renderJobs(snap.jobs);
  if (snap.memory) $('memBadge').textContent = snap.memory.memories + ' stored';
  if (snap.voice) applyVoice(snap.voice);
  if (snap.hardware) {
    const hw = snap.hardware;
    $('brandSub').textContent =
      'Personal Intelligence // ' + hw.cpu_count + ' cores // ' +
      (hw.ram_total_mb / 1024).toFixed(1) + ' GB RAM';
  }
  bootLine('All systems nominal');
  setTimeout(() => {
    const b = $('boot');
    if (b) b.classList.add('hide');
  }, 420);
}

function applyTelemetry(t) {
  if (!t) return;
  const s = t.summary || t;
  const res = s.resources || {};

  $('mLatency').innerHTML = fmtMs(s.avg_latency_ms);
  $('mTps').textContent = (s.avg_tokens_per_second || 0).toFixed(1);
  $('mRequests').textContent = fmtNum(s.requests);
  $('mTokens').textContent = fmtNum(s.total_tokens);

  $('hdrLatency').textContent = fmtMs(s.avg_latency_ms);
  $('hdrTps').textContent = (s.avg_tokens_per_second || 0).toFixed(1);

  setBar('Cpu', res.cpu_percent || 0);
  setBar('Ram', res.ram_percent || 0);
  setBar('Vram', res.vram_percent || 0);

  if (res.ram_total_mb) {
    $('hdrRam').textContent = (res.ram_used_mb / 1024).toFixed(1) + 'G';
  }
  if (res.vram_total_mb) {
    $('hdrVram').textContent = (res.vram_used_mb / 1024).toFixed(1) + 'G';
  }

  applyEnergy(t.energy || s.energy);
}

/* Energy and FLOPs. Honest about absence: an unknown power draw is
   reported as unavailable, never as zero. */
function applyEnergy(e) {
  const joules = $('mEnergy');
  const watts = $('mWatts');
  const flops = $('mFlops');
  if (!joules) return;
  if (!e) {
    joules.innerHTML = '—';
    watts.innerHTML = '—';
    flops.textContent = '—';
    return;
  }
  // The summary reports no energy block when no samples exist. That is
  // an absence, not a zero — report it honestly.
  const total = e.total_joules;
  joules.innerHTML = total == null
    ? '—'
    : (total >= 1000 ? (total / 1000).toFixed(2) : total.toFixed(1))
      + '<span class="metric-unit">' + (total >= 1000 ? 'kJ' : 'J') + '</span>';
  watts.innerHTML = e.avg_watts == null
    ? '—'
    : e.avg_watts.toFixed(0) + '<span class="metric-unit">W</span>';
  flops.textContent = e.total_flops == null ? '—' : fmtFlops(e.total_flops);
}

function fmtFlops(n) {
  if (n == null) return '—';
  if (n >= 1e15) return (n / 1e15).toFixed(2) + ' PF';
  if (n >= 1e12) return (n / 1e12).toFixed(2) + ' TF';
  if (n >= 1e9) return (n / 1e9).toFixed(2) + ' GF';
  return n.toFixed(0);
}

/* The installed skills catalog. */
function applySkills(data) {
  const box = $('skillList');
  const badge = $('skillBadge');
  if (!box) return;
  box.innerHTML = '';
  if (!data || !data.enabled) {
    if (badge) badge.textContent = 'off';
    const off = el('div', 'mem-text', 'Skills are disabled, ' + USER_NAME + '.');
    off.style.color = 'var(--carla-text-faint)';
    box.appendChild(off);
    return;
  }
  const skills = data.skills || [];
  if (badge) badge.textContent = String(skills.length);
  if (!skills.length) {
    const empty = el('div', 'mem-text', 'No skills installed yet.');
    empty.style.color = 'var(--carla-text-faint)';
    box.appendChild(empty);
    return;
  }
  skills.slice(0, 10).forEach(function (s) {
    const name = s.skill ? s.skill.name : s.name;
    const desc = s.skill ? s.skill.description : (s.description || '');
    const item = el('div', 'mem-item');
    item.appendChild(el('div', 'mem-text', name));
    const meta = el('div', 'mem-meta', (desc || '').slice(0, 92));
    item.appendChild(meta);
    box.appendChild(item);
  });
}

/* Which learning surfaces are switched on. */
function applyLearning(data) {
  const box = $('learnList');
  const badge = $('learnBadge');
  if (!box) return;
  box.innerHTML = '';
  if (!data) {
    if (badge) badge.textContent = '—';
    const off = el('div', 'mem-text', 'Learning state unavailable.');
    off.style.color = 'var(--carla-text-faint)';
    box.appendChild(off);
    return;
  }
  const surfaces = data.surfaces || {};
  if (badge) badge.textContent = data.enabled
    ? (data.optimizer || 'heuristic')
    : 'off';
  // Even when the learning loop is off globally, the per-surface switches
  // are still worth showing — they are what you edit to turn it on.
  if (!Object.keys(surfaces).length) {
    const off = el('div', 'mem-text', 'Learning is disabled, ' + USER_NAME + '.');
    off.style.color = 'var(--carla-text-faint)';
    box.appendChild(off);
    return;
  }
  Object.keys(surfaces).forEach(function (name) {
    const s = surfaces[name];
    const item = el('div', 'mem-item');
    item.appendChild(el('div', 'mem-text', name));
    const label = s.enabled ? 'enabled' : 'disabled';
    const meta = el('div', 'mem-meta',
      (s.policy || 'none') + ' · ' + label);
    meta.style.color = s.enabled
      ? 'var(--carla-cyan)' : 'var(--carla-text-faint)';
    item.appendChild(meta);
    box.appendChild(item);
  });
}

function setBar(name, pct) {
  const bar = $('bar' + name);
  const val = $('bar' + name + 'Val');
  if (!bar) return;
  const p = Math.max(0, Math.min(100, pct));
  bar.style.width = p + '%';
  bar.className = 'bar-fill' + (p > 85 ? ' bad' : (p > 65 ? ' warn' : ''));
  if (val) val.textContent = p.toFixed(0) + '%';
}

function applyVoice(v) {
  if (!v) return;
  if (v.speaking) {
    core.setMode('speaking');
    core.setState('Speaking', 'speaking');
  } else if (v.listening) {
    core.setMode('listening');
    core.setState('Listening', 'listening');
  } else if (v.waiting_for_wake) {
    core.setMode('idle');
    core.setState('Standby · Wake armed', '');
  } else {
    core.setMode('idle');
    core.setState('Standby', '');
  }
  if (v.last_transcript) $('coreTranscript').textContent = v.last_transcript;
  if (v.last_spoken) state.lastSpeech = v.last_spoken;

  const wakeBtn = $('btnWake');
  if (wakeBtn) {
    wakeBtn.classList.toggle('primary', !!v.wake_enabled);
    wakeBtn.textContent = v.wake_enabled ? '👂 Wake ON' : '👂 Wake OFF';
  }
}

function renderAgents() {
  const box = $('agentList');
  box.innerHTML = '';
  $('agentCount').textContent = state.agents.length;
  state.agents.forEach(a => {
    const item = el('div', 'agent-item' + (a.name === state.activeAgent ? ' active' : ''));
    item.dataset.agent = a.name;

    item.appendChild(el('span', 'agent-dot'));
    const meta = el('div', 'agent-meta');
    meta.appendChild(el('div', 'agent-name', a.name.replace(/_/g, ' ')));
    meta.appendChild(el('div', 'agent-desc', a.description || ''));
    item.appendChild(meta);
    item.appendChild(el('span', 'agent-mode', a.mode || 'on_demand'));

    item.addEventListener('click', () => {
      state.activeAgent = a.name;
      renderAgents();
      toast('Agent → ' + a.name.replace(/_/g, ' '));
    });
    box.appendChild(item);
  });
}

function setAgentBusy(agent, busy) {
  const item = document.querySelector('.agent-item[data-agent="' + agent + '"]');
  if (item) item.classList.toggle('busy', !!busy);
}

function renderTiers(tiers) {
  const box = $('tierList');
  box.innerHTML = '';
  $('tierBadge').textContent = state.activeTier || '—';
  tiers.forEach(t => {
    const item = el('div', 'tier-item' + (t.alias === state.activeTier ? ' active' : ''));
    item.appendChild(el('span', 'tier-radio'));
    const meta = el('div', 'tier-meta');
    meta.appendChild(el('div', 'tier-alias', t.alias));
    meta.appendChild(el('div', 'tier-model', (t.provider || '') + ' · ' + (t.model || '—')));
    item.appendChild(meta);
    item.addEventListener('click', async () => {
      try {
        await api('/api/tiers/active', {
          method: 'POST', body: JSON.stringify({ tier: t.alias }),
        });
        state.activeTier = t.alias;
        $('hdrTier').textContent = t.alias;
        renderTiers(tiers);
        toast('Tier → ' + t.alias, 'ok');
      } catch (e) { toast('Tier switch failed: ' + e.message, 'err'); }
    });
    box.appendChild(item);
  });
}

function renderTools(tools) {
  const box = $('toolList');
  box.innerHTML = '';
  const byNs = {};
  tools.forEach(t => { (byNs[t.namespace] = byNs[t.namespace] || []).push(t); });
  $('toolCount').textContent = tools.length;

  Object.keys(byNs).sort().forEach(ns => {
    const head = el('div', 'metric-label');
    head.style.margin = '9px 0 5px';
    head.textContent = ns;
    box.appendChild(head);
    byNs[ns].forEach(t => {
      const row = el('div', 'mem-item');
      const txt = el('div', 'mem-text', t.name);
      txt.style.marginBottom = '2px';
      txt.style.fontFamily = 'var(--carla-font-mono)';
      txt.style.fontSize = '10.5px';
      row.appendChild(txt);
      const desc = el('div', 'mem-meta', (t.description || '').slice(0, 96));
      desc.style.lineHeight = '1.4';
      row.appendChild(desc);
      if (t.dangerous) {
        const warn = el('span', 'mem-kind', 'elevated');
        warn.style.color = 'var(--carla-amber)';
        desc.appendChild(warn);
      }
      box.appendChild(row);
    });
  });
}

function renderJobs(jobs) {
  const box = $('jobList');
  box.innerHTML = '';
  $('jobBadge').textContent = jobs.length;
  if (!jobs.length) {
    const empty = el('div', 'mem-text', 'No jobs scheduled, ' + USER_NAME + '.');
    empty.style.color = 'var(--carla-text-faint)';
    box.appendChild(empty);
    return;
  }
  jobs.forEach(j => {
    const item = el('div', 'job-item');
    item.appendChild(el('span', 'job-sched', j.schedule));
    item.appendChild(el('div', 'job-name', j.name));
    item.appendChild(el('span', 'job-agent', j.agent));

    const run = el('button', 'btn sm ghost', '▶');
    run.title = 'Run now';
    run.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        await api('/api/jobs/' + j.id + '/run', { method: 'POST' });
        toast('Job dispatched', 'ok');
      } catch (err) { toast(err.message, 'err'); }
    });
    item.appendChild(run);

    const del = el('button', 'btn sm ghost', '✕');
    del.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        await api('/api/jobs/' + j.id, { method: 'DELETE' });
        loadJobs();
        toast('Job removed');
      } catch (err) { toast(err.message, 'err'); }
    });
    item.appendChild(del);
    box.appendChild(item);
  });
}

/* --------------------------------------------------------------------------
   Chat
   ------------------------------------------------------------------------ */
function scrollChat() {
  const log = $('chatLog');
  log.scrollTop = log.scrollHeight;
}

function appendUser(text) {
  const msg = el('div', 'msg user');
  msg.appendChild(el('div', 'msg-avatar', USER_NAME.charAt(0).toUpperCase() || 'P'));
  const body = el('div', 'msg-body');
  body.appendChild(el('div', 'msg-role', USER_NAME));
  const t = el('div', 'msg-text');
  t.innerHTML = '<p>' + escapeHtml(text) + '</p>';
  body.appendChild(t);
  msg.appendChild(body);
  $('chatLog').appendChild(msg);
  scrollChat();
}

function appendAssistant(data) {
  const msg = el('div', 'msg assistant');
  msg.appendChild(el('div', 'msg-avatar', 'C'));
  const body = el('div', 'msg-body');
  body.appendChild(el('div', 'msg-role', 'C.A.R.L.A.'));

  const display = data.display || data.text || '';
  const speech = data.speech || '';

  const t = el('div', 'msg-text');
  t.innerHTML = renderMarkdown(display);
  body.appendChild(t);

  // The speech channel — what C.A.R.L.A. actually vocalised.
  if (speech && speech !== display) {
    const chip = el('div', 'speech-chip');
    chip.appendChild(el('span', 'sc-icon', '🔊'));
    chip.appendChild(el('span', '', speech));
    body.appendChild(chip);
  }

  if (data.tool_calls && data.tool_calls.length) {
    const strip = el('div', 'tool-strip');
    data.tool_calls.forEach(tc => {
      strip.appendChild(el('span', 'tool-pill' + (tc.ok ? ' ok' : ' fail'), tc.name));
    });
    body.appendChild(strip);
  }

  if (data.latency_ms) {
    const meta = el('div', 'mem-meta');
    meta.style.marginTop = '7px';
    meta.textContent =
      (data.agent || 'agent') + ' · ' + (data.tier || '') + ' · ' +
      fmtMs(data.latency_ms) +
      (data.usage && data.usage.total_tokens ? ' · ' + data.usage.total_tokens + ' tok' : '');
    body.appendChild(meta);
  }

  msg.appendChild(body);
  $('chatLog').appendChild(msg);
  scrollChat();

  state.lastSpeech = speech;
  state.lastDisplay = display;
  state.busy = false;
  $('btnSend').disabled = false;
  $('btnSend').textContent = 'Send ▸';
  $('consoleBadge').textContent = 'ready';
  setAgentBusy(data.agent, false);
}

async function sendPrompt() {
  const box = $('prompt');
  const prompt = box.value.trim();
  if (!prompt || state.busy) return;

  state.busy = true;
  $('btnSend').disabled = true;
  $('btnSend').textContent = 'Working…';
  $('consoleBadge').textContent = 'processing';
  box.value = '';
  box.style.height = 'auto';
  setAgentBusy(state.activeAgent, true);

  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({
      type: 'chat',
      prompt: prompt,
      agent: state.activeAgent,
      tier: state.activeTier,
      session_id: state.sessionId,
    }));
  } else {
    try {
      const result = await api('/api/chat', {
        method: 'POST',
        body: JSON.stringify({
          prompt: prompt, agent: state.activeAgent,
          tier: state.activeTier, session_id: state.sessionId,
        }),
      });
      appendUser(prompt);
      appendAssistant(result);
    } catch (e) {
      toast('Request failed: ' + e.message, 'err');
      state.busy = false;
      $('btnSend').disabled = false;
      $('btnSend').textContent = 'Send ▸';
    }
  }
}

/* --------------------------------------------------------------------------
   Loaders
   ------------------------------------------------------------------------ */
async function loadTiers() {
  try {
    const data = await api('/api/tiers');
    state.activeTier = data.active;
    state.tiers = data.tiers;
    renderTiers(data.tiers);
  } catch (e) { /* noop */ }
}

async function loadJobs() {
  try {
    const data = await api('/api/jobs');
    renderJobs(data.jobs);
    populateAgentSelect();
  } catch (e) { /* noop */ }
}

async function loadMemory(query) {
  try {
    const url = query
      ? '/api/memory?q=' + encodeURIComponent(query) + '&limit=30'
      : '/api/memory?limit=30';
    const data = await api(url);
    const box = $('memList');
    box.innerHTML = '';
    $('memBadge').textContent = ((data.stats && data.stats.memories) || 0) + ' stored';
    if (!data.memories.length) {
      const empty = el('div', 'mem-text', 'Nothing in the vault yet, ' + USER_NAME + '.');
      empty.style.color = 'var(--carla-text-faint)';
      box.appendChild(empty);
      return;
    }
    data.memories.forEach(m => {
      const item = el('div', 'mem-item');
      item.appendChild(el('div', 'mem-text', m.content));
      const meta = el('div', 'mem-meta');
      meta.appendChild(el('span', 'mem-kind', m.kind));
      meta.appendChild(el('span', '', m.score ? 'score ' + m.score.toFixed(2) : ''));
      const del = el('button', 'mem-del', '×');
      del.title = 'Delete';
      del.addEventListener('click', async () => {
        try {
          await api('/api/memory/' + m.id, { method: 'DELETE' });
          loadMemory();
          toast('Memory purged');
        } catch (e) { toast(e.message, 'err'); }
      });
      meta.appendChild(del);
      item.appendChild(meta);
      box.appendChild(item);
    });
  } catch (e) { /* noop */ }
}

function populateAgentSelect() {
  const sel = $('jobAgent');
  if (sel.options.length) return;
  state.agents.forEach(a => {
    const o = document.createElement('option');
    o.value = a.name;
    o.textContent = a.name.replace(/_/g, ' ');
    sel.appendChild(o);
  });
}

/* --------------------------------------------------------------------------
   Settings editor
   ------------------------------------------------------------------------ */
async function openEditor() {
  try {
    const data = await api('/api/config');
    $('editorText').value = data.toml || '# Empty configuration.';
    $('editorPath').textContent = data.path;
    $('editor').classList.add('open');
    $('editorStatus').className = 'editor-status';
    $('editorStatus').textContent =
      'Loaded ' + (data.toml || '').split('\n').length + ' lines.';
  } catch (e) {
    toast('Could not load config: ' + e.message, 'err');
  }
}

async function saveEditor() {
  const status = $('editorStatus');
  status.className = 'editor-status';
  status.textContent = 'Saving…';
  try {
    await api('/api/config', {
      method: 'PUT', body: JSON.stringify({ toml: $('editorText').value }),
    });
    status.className = 'editor-status ok';
    status.textContent = 'Saved. Reloading runtime…';
    await api('/api/config/reload', { method: 'POST' });
    toast('Configuration saved', 'ok');
    setTimeout(() => {
      $('editor').classList.remove('open');
      status.textContent = 'Ready.';
    }, 900);
  } catch (e) {
    status.className = 'editor-status err';
    status.textContent = 'Error: ' + e.message;
  }
}

/* --------------------------------------------------------------------------
   Wiring
   ------------------------------------------------------------------------ */
function wire() {
  $('btnSend').addEventListener('click', sendPrompt);

  $('prompt').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendPrompt();
    }
  });
  $('prompt').addEventListener('input', (e) => {
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 170) + 'px';
  });

  $('btnClear').addEventListener('click', () => {
    $('chatLog').innerHTML = '';
    toast('Console cleared');
  });

  $('btnMic').addEventListener('click', async () => {
    state.micOn = !state.micOn;
    try {
      const data = await api('/api/voice/listen', { method: 'POST' });
      if (data.text) {
        $('prompt').value = data.text;
        core.setMode('listening');
        core.setState('Listening', 'listening');
        setTimeout(() => { core.setMode('idle'); core.setState('Standby', ''); }, 1400);
        toast('Heard: "' + data.text.slice(0, 48) + '"', 'ok');
      } else {
        toast('Nothing picked up, ' + USER_NAME + '.');
      }
    } catch (e) { toast('Listen failed: ' + e.message, 'err'); }
  });

  $('btnSpeak').addEventListener('click', async () => {
    const text = state.lastSpeech || state.lastDisplay;
    if (!text) { toast('Nothing to say yet, ' + USER_NAME + '.'); return; }
    try {
      await api('/api/voice/speak', { method: 'POST', body: JSON.stringify({ text }) });
      core.setMode('speaking');
      core.setState('Speaking', 'speaking');
      setTimeout(() => { core.setMode('idle'); core.setState('Standby', ''); }, 2600);
    } catch (e) { toast('Speak failed: ' + e.message, 'err'); }
  });

  $('btnStop').addEventListener('click', () => {
    core.setMode('idle');
    core.setState('Standby', '');
    toast('Halted.');
  });

  $('btnWake').addEventListener('click', () => {
    toast('Wake word state is managed in psycho_carla.toml');
  });

  $('memSearch').addEventListener('input', (e) => {
    const v = e.target.value;
    clearTimeout(window._memTimer);
    window._memTimer = setTimeout(() => loadMemory(v.trim()), 320);
  });
  $('btnMemRefresh').addEventListener('click', () => loadMemory());
  $('btnMemAdd').addEventListener('click', async () => {
    const content = prompt('Memory content to store:');
    if (!content) return;
    try {
      await api('/api/memory', { method: 'POST', body: JSON.stringify({ content }) });
      loadMemory();
      toast('Stored in the vault', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });

  $('btnJobAdd').addEventListener('click', async () => {
    const name = $('jobName').value.trim();
    const schedule = $('jobSchedule').value.trim();
    const agent = $('jobAgent').value;
    if (!name || !schedule) { toast('Name and schedule required, ' + USER_NAME + '.', 'err'); return; }
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          name: name, schedule: schedule, agent: agent,
          prompt: 'Run the ' + name + ' scheduled task.',
        }),
      });
      $('jobName').value = '';
      loadJobs();
      toast('Job scheduled', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });

  $('btnSettings').addEventListener('click', openEditor);
  $('btnEditorSave').addEventListener('click', saveEditor);
  $('btnEditorClose').addEventListener('click', () => $('editor').classList.remove('open'));
  $('btnEditorReload').addEventListener('click', openEditor);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $('editor').classList.remove('open');
    if (e.ctrlKey && e.key === '/') { e.preventDefault(); openEditor(); }
  });
}

/* --------------------------------------------------------------------------
   Boot
   ------------------------------------------------------------------------ */
async function boot() {
  core.start();
  wire();
  connectWS();
  try {
    const snap = await api('/api/status');
    applySnapshot(snap);
  } catch (e) {
    bootLine('Runtime unavailable');
    setTimeout(() => {
      const b = $('boot');
      if (b) b.classList.add('hide');
    }, 900);
  }
  loadTiers();
  loadJobs();
  loadMemory();
  loadSkills();
  loadLearning();
  loadModelsLocal();
  loadChannels();
  setInterval(() => {
    fetch('/api/telemetry')
      .then(r => r.json())
      .then(d => applyTelemetry(d))
      .catch(() => {});
  }, 3000);
}

/* The skills catalog and the learning surfaces. Both are slow-moving,
   so they are fetched once at boot rather than polled. */
async function loadSkills() {
  try {
    const d = await api('/api/skills');
    applySkills(d);
  } catch (e) { applySkills(null); }
}

async function loadLearning() {
  try {
    const d = await api('/api/learning/surfaces');
    applyLearning(d);
  } catch (e) { applyLearning(null); }
}

/* The connected messaging channels. */
function applyChannels(data) {
  const box = $('chanList');
  const badge = $('chanBadge');
  if (!box) return;
  box.innerHTML = '';
  const channels = (data && data.channels) || [];
  if (badge) badge.textContent = data && data.status ? data.status : '—';

  if (!channels.length) {
    const empty = el('div', 'mem-text', 'No channels configured.');
    empty.style.color = 'var(--carla-text-faint)';
    box.appendChild(empty);
    return;
  }

  channels.forEach(function (c) {
    const item = el('div', 'mem-item');
    const row = el('div', 'mem-row');
    row.appendChild(el('span', 'mem-text', c.channel_id));

    const status = el('span', 'mem-meta', c.status);
    if (c.status === 'connected') status.style.color = 'var(--carla-cyan)';
    else if (c.status === 'error') status.style.color = 'var(--carla-red, #ff6b6b)';
    row.appendChild(status);

    item.appendChild(row);
    if (c.missing && c.missing.length) {
      const note = el('div', 'mem-meta', 'needs: ' + c.missing.join(', '));
      note.style.color = 'var(--carla-text-faint)';
      item.appendChild(note);
    }
    box.appendChild(item);
  });
}

async function loadChannels() {
  try {
    const d = await api('/api/channels');
    applyChannels(d);
  } catch (e) {
    applyChannels(null);
  }
}

async function loadModelsLocal() {
  try {
    const d = await api('/api/models/local');
    applyModelsLocal(d);
  } catch (e) { applyModelsLocal(null); }
}

/* --------------------------------------------------------------------------
   Model Manager — browse the catalog and download models without a terminal.
   ------------------------------------------------------------------------ */

/* The compact panel in the sidebar. */
function applyModelsLocal(data) {
  const box = $('modelList');
  const badge = $('modelBadge');
  if (!box) return;
  box.innerHTML = '';
  const models = (data && data.models) || [];
  if (badge) badge.textContent = String(models.length);
  if (!models.length) {
    const empty = el('div', 'mem-text', 'No models downloaded yet.');
    empty.style.color = 'var(--carla-text-faint)';
    box.appendChild(empty);
    return;
  }
  models.slice(0, 6).forEach(function (m) {
    const item = el('div', 'mem-item');
    const row = el('div', 'mem-row');
    row.appendChild(el('span', 'mem-text', m.model_id));
    row.appendChild(el('span', 'mem-meta', (m.size_gb || 0).toFixed(1) + ' GB'));
    item.appendChild(row);
    box.appendChild(item);
  });
}

/* How much memory this machine has — used to warn about downloads. */
let mmRam = null;

/* The modal: catalog + installed models. */
function mmRenderCatalog(data, engine) {
  const box = $('mmCatalog');
  if (!box) return;
  box.innerHTML = '';
  const models = (data && data.models) || [];
  if (!models.length) {
    const empty = el('div', 'mem-text', 'No catalogued models for that engine.');
    empty.style.color = 'var(--carla-text-faint)';
    box.appendChild(empty);
    return;
  }
  models.forEach(function (m) {
    const item = el('div', 'mem-item');
    const row = el('div', 'mem-row');
    row.appendChild(el('span', 'mem-text', m.model_id));
    const right = el('span', 'mem-meta');
    const ram = m.ram_gb ? m.ram_gb + ' GB ram' : '';
    const dl = m.size_gb ? m.size_gb + ' GB dl' : '';
    right.textContent = [ram, dl].filter(Boolean).join(' · ');
    row.appendChild(right);
    item.appendChild(row);
    if (m.description) {
      const d = el('div', 'mem-meta', m.description);
      item.appendChild(d);
    }
    if (engine === 'ollama' || engine === 'llamacpp' ||
        engine === 'native_onnx') {
      // Warn before a download that will not fit in memory.
      if (engine === 'native_onnx' && mmRam && m.ram_gb) {
        const need = m.ram_gb;
        const have = mmRam.free_gb || mmRam.ram_gb;
        if (need > have) {
          const warn = el('div', 'mem-meta',
            '⚠ needs about ' + need + ' GB RAM — this machine has ' +
            have + ' GB free');
          warn.style.color = 'var(--carla-amber)';
          item.appendChild(warn);
        }
      }
      const btn = el('button', 'btn sm', 'Download');
      btn.style.marginTop = '6px';
      btn.onclick = function () { mmPull(m.model_id, engine, btn); };
      item.appendChild(btn);
    }
    box.appendChild(item);
  });
}

function mmRenderLocal(data) {
  const box = $('mmLocal');
  if (!box) return;
  box.innerHTML = '';
  const models = (data && data.models) || [];
  if (!models.length) {
    const empty = el('div', 'mem-text', 'Nothing installed yet.');
    empty.style.color = 'var(--carla-text-faint)';
    box.appendChild(empty);
    return;
  }
  models.forEach(function (m) {
    const item = el('div', 'mem-item');
    const row = el('div', 'mem-row');
    row.appendChild(el('span', 'mem-text', m.model_id));
    row.appendChild(el('span', 'mem-meta', m.engine + ' · ' +
      (m.size_gb || 0).toFixed(1) + ' GB'));
    item.appendChild(row);

    // A model that is running right now says so.
    if (m.running) {
      const badge = el('span', 'mem-meta', '● running');
      badge.style.color = 'var(--carla-cyan)';
      badge.style.marginLeft = '8px';
      row.appendChild(badge);
    }

    const actions = el('div', 'mem-actions');
    if (m.engine === 'native_onnx') {
      // Start / Stop loads the model into memory and frees it again.
      if (m.running) {
        const stop = el('button', 'btn sm', 'Stop');
        stop.style.marginTop = '6px';
        stop.onclick = function () { mmStop(m.model_id, stop); };
        actions.appendChild(stop);
      } else {
        const start = el('button', 'btn sm', 'Start');
        start.style.marginTop = '6px';
        start.onclick = function () { mmStart(m.model_id, start); };
        actions.appendChild(start);
      }

      const use = el('button', 'btn sm', 'Use this');
      use.style.marginTop = '6px';
      use.onclick = function () { mmActivate(m.model_id, use); };
      actions.appendChild(use);
    }
    const del = el('button', 'btn sm ghost', 'Delete');
    del.style.marginTop = '6px';
    del.onclick = function () { mmDelete(m.model_id, m.engine, del); };
    actions.appendChild(del);
    item.appendChild(actions);
    box.appendChild(item);
  });

  // Partial downloads — the ones that never finished. They are
  // listed with a button to clear them out.
  if (data && data.partial && data.partial.length) {
    const head = el('div', 'mem-meta',
      'Partial downloads — ' + data.partial.length +
      ' incomplete');
    head.style.marginTop = '18px';
    head.style.color = 'var(--carla-amber)';
    box.appendChild(head);

    data.partial.forEach(function (p) {
      const row = el('div', 'mem-row');
      row.appendChild(el('span', 'mem-text', p.id));
      row.appendChild(el('span', 'mem-meta', p.size_mb + ' MB'));
      const clear = el('button', 'btn sm ghost', 'Clear');
      clear.style.marginLeft = '10px';
      clear.onclick = function () { mmClearPartial(p.id, clear); };
      row.appendChild(clear);
      box.appendChild(row);
    });
  }
}

/* Start a model — it loads into memory and stays resident. */
async function mmStart(modelId, btn) {
  const status = $('mmStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
  if (status) status.textContent = 'Starting ' + modelId + '…';
  try {
    const res = await api('/api/models/start', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId }),
    });
    if (status) status.textContent = res.message || ('started ' + modelId);
    await mmRefreshLocal();
  } catch (e) {
    if (status) status.textContent = e.message;
    if (btn) { btn.disabled = false; btn.textContent = 'Start'; }
  }
}

/* Stop the running model and free the memory it was using. */
async function mmStop(modelId, btn) {
  const status = $('mmStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'Stopping…'; }
  if (status) status.textContent = 'Stopping ' + modelId + '…';
  try {
    const res = await api('/api/models/stop', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId || '' }),
    });
    if (status) status.textContent = res.message || 'stopped';
    await mmRefreshLocal();
  } catch (e) {
    if (status) status.textContent = e.message;
    if (btn) { btn.disabled = false; btn.textContent = 'Stop'; }
  }
}

/* Delete one partial download — the ones that never finished. */
async function mmClearPartial(modelId, btn) {
  const status = $('mmStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'Clearing…'; }
  if (status) status.textContent = 'Clearing ' + modelId + '…';
  try {
    const res = await api('/api/models/partial/clear', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId }),
    });
    if (status) status.textContent = res.message || 'cleaned up';
    await mmRefreshLocal();
  } catch (e) {
    if (status) status.textContent = e.message;
    if (btn) { btn.disabled = false; btn.textContent = 'Clear'; }
  }
}

/* Point a capability tier at a native model and run it inside C.A.R.L.A. */
async function mmActivate(modelId, btn) {
  const status = $('mmStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'Activating…'; }
  if (status) status.textContent = 'Activating ' + modelId + '…';
  try {
    const res = await api('/api/models/activate', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId, tier: 'fast_tier' }),
    });
    if (status) {
      status.textContent = res.ok ? '✓ ' + (res.message || 'activated')
                                  : '✗ ' + (res.message || res.error || 'failed');
      status.style.color = res.ok ? 'var(--carla-cyan)' : 'var(--carla-amber)';
    }
  } catch (e) {
    if (status) {
      status.textContent = '✗ ' + (e.message || 'failed');
      status.style.color = 'var(--carla-amber)';
    }
  }
  if (btn) { btn.disabled = false; btn.textContent = 'Use this'; }
}

/* Remove a downloaded model from this machine. */
async function mmDelete(modelId, engine, btn) {
  const status = $('mmStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'Deleting…'; }
  if (status) status.textContent = 'Deleting ' + modelId + '…';
  try {
    const res = await api('/api/models/delete', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId, engine: engine }),
    });
    if (status) {
      status.textContent = res.ok ? '✓ ' + (res.message || 'deleted')
                                  : '✗ ' + (res.message || 'failed');
      status.style.color = res.ok ? 'var(--carla-cyan)' : 'var(--carla-amber)';
    }
  } catch (e) {
    if (status) {
      status.textContent = '✗ ' + (e.message || 'failed');
      status.style.color = 'var(--carla-amber)';
    }
  }
  if (btn) { btn.disabled = false; btn.textContent = 'Delete'; }
  mmRefreshLocal();
}

async function mmPull(modelId, engine, btn) {
  const status = $('mmStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'Downloading…'; }
  if (status) status.textContent = 'Pulling ' + modelId + '…';
  try {
    const res = await api('/api/models/pull', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId, engine: engine }),
    });
    if (status) {
      status.textContent = res.ok ? '✓ ' + (res.message || 'done')
                                  : '✗ ' + (res.message || 'failed');
      status.style.color = res.ok ? 'var(--carla-cyan)' : 'var(--carla-amber)';
    }
  } catch (e) {
    if (status) {
      status.textContent = '✗ ' + (e.message || 'failed');
      status.style.color = 'var(--carla-amber)';
    }
  }
  if (btn) { btn.disabled = false; btn.textContent = 'Download'; }
  mmRefreshLocal();
}

async function mmRefreshLocal() {
  try {
    const d = await api('/api/models/local');
    mmRenderLocal(d);
    applyModelsLocal(d);
  } catch (e) { /* noop */ }
}

async function mmRefreshCatalog() {
  const engine = ($('mmEngine') || {}).value || 'ollama';
  const status = $('mmStatus');
  if (status) status.textContent = 'Loading catalog…';
  try {
    const d = await api('/api/models?engine=' + encodeURIComponent(engine));
    mmRenderCatalog(d, engine);
    if (status) { status.textContent = ''; }
  } catch (e) {
    if (status) status.textContent = '✗ ' + (e.message || 'failed');
  }
  try {
    const rec = await api('/api/models/recommend');
    const hw = $('mmHw');
    if (hw) {
      hw.textContent = rec.ram_gb + ' GB RAM · ' +
        rec.disk_free_gb + ' GB free';
    }
  } catch (e) { /* noop */ }
  try {
    mmRam = await api('/api/models/ram');
  } catch (e) { mmRam = null; }
}

function mmOpen() {
  const wrap = $('modelMgr');
  if (!wrap) return;
  wrap.classList.add('open');
  mmRefreshCatalog();
  mmRefreshLocal();
}

function mmClose() {
  const wrap = $('modelMgr');
  if (wrap) wrap.classList.remove('open');
}

document.addEventListener('DOMContentLoaded', function () {
  const btn = $('btnModels');
  if (btn) btn.onclick = mmOpen;
  const close = $('mmClose');
  if (close) close.onclick = mmClose;
  const refresh = $('mmRefresh');
  if (refresh) refresh.onclick = mmRefreshCatalog;
  const engine = $('mmEngine');
  if (engine) engine.onchange = mmRefreshCatalog;
});

document.addEventListener('DOMContentLoaded', boot);

/* --------------------------------------------------------------------------
   Hash routing.

   The native desktop menu navigates by setting window.location.hash, so
   the HUD listens for it and opens the matching panel.
   -------------------------------------------------------------------------- */
function applyRoute() {
  const route = (window.location.hash || '').replace(/^#/, '');
  if (!route) return;
  if (route === 'models') { mmOpen(); return; }
  if (route === 'voice') {
    const btn = $('btnMic');
    if (btn) btn.click();
    return;
  }
  if (route === 'console') {
    const input = $('prompt');
    if (input) input.focus();
  }
}

window.addEventListener('hashchange', applyRoute);
document.addEventListener('DOMContentLoaded', function () {
  setTimeout(applyRoute, 250);
});
