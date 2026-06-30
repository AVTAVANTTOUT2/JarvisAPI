/**
 * JARVIS TV War Room v2 — Data Engine
 *
 * Horizonte pure, globe canvas, 11 widgets, overlay vocal SSE.
 * Zéro framework — vanilla JS + fetch vers /api/* du serveur TV (port 5174).
 */

(function () {
  "use strict";

  const $  = (s,d) => (d||document).querySelector(s);
  const $$ = (s,d) => [...(d||document).querySelectorAll(s)];

  // ── Fetch helper ─────────────────────────────────────────
  async function fetchJSON(path) {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 6000);
      const r = await fetch(path, { signal: ctl.signal });
      clearTimeout(t);
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } catch (e) {
      console.warn("[tv-v2]", path, e.message);
      return null;
    }
  }

  function esc(s) { return String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

  function timeAgo(ts) {
    const d = new Date(ts); if (isNaN(d)) return "";
    const m = Math.floor((Date.now() - d) / 60000);
    if (m < 1) return "à l'instant";
    if (m < 60) return `il y a ${m}min`;
    const h = Math.floor(m / 60);
    if (h < 24) return `il y a ${h}h`;
    if (h < 48) return "hier";
    return `il y a ${Math.floor(h/24)}j`;
  }

  // ═══════════════════════════════════════════════════════
  // CLOCK
  // ═══════════════════════════════════════════════════════
  function clockTick() {
    const n = new Date();
    $("#clock-hours").textContent   = String(n.getHours()).padStart(2,"0");
    $("#clock-minutes").textContent = String(n.getMinutes()).padStart(2,"0");
    $("#clock-seconds").textContent = String(n.getSeconds()).padStart(2,"0");
    $("#clock-colon").style.opacity = n.getSeconds() % 2 ? "0" : "1";
    const days = ["Dimanche","Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi"];
    const mos  = ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"];
    $("#clock-date").textContent = `${days[n.getDay()]} ${n.getDate()} ${mos[n.getMonth()]}`;
  }

  // ═══════════════════════════════════════════════════════
  // GLOBE CANVAS
  // ═══════════════════════════════════════════════════════
  let globeId = null;
  function initGlobe() {
    const c = $("#globe-canvas"); if (!c) return setTimeout(initGlobe, 200);
    const ctx = c.getContext("2d");
    const [W, H, cx, cy, R] = [400, 400, 200, 200, 85];
    const arcs = Array.from({length: 25}, () => ({
      theta: Math.random()*Math.PI*2, phi: Math.random()*Math.PI,
      speed: .001+Math.random()*.003, alpha: .1+Math.random()*.3
    }));
    let rot = 0;
    function draw() {
      ctx.clearRect(0,0,W,H); rot += .003;
      ctx.strokeStyle="rgba(0,212,255,.06)"; ctx.lineWidth=.5;
      // méridiens
      for (let i=0; i<=18; i++) {
        ctx.beginPath(); let ph = (i/18)*Math.PI*2+rot, first=true;
        for (let j=0; j<=10; j++) {
          const th = (j/10)*Math.PI;
          const x = cx+R*Math.sin(th)*Math.cos(ph), y = cy+R*Math.cos(th);
          first ? ctx.moveTo(x,y) : ctx.lineTo(x,y); first=false;
        }
        ctx.stroke();
      }
      // parallèles
      for (let j=1; j<10; j++) { ctx.beginPath(); ctx.arc(cx, cy+R*Math.cos((j/10)*Math.PI), R*Math.sin((j/10)*Math.PI), 0, Math.PI*2); ctx.stroke(); }
      // arcs
      arcs.forEach(a => {
        a.phi+=a.speed; if(a.phi>Math.PI*2) a.phi-=Math.PI*2;
        const sx=cx+R*1.05*Math.sin(a.theta)*Math.cos(a.phi), sy=cy+R*1.05*Math.sin(a.theta)*Math.sin(a.phi);
        ctx.beginPath(); ctx.arc(sx,sy,1.5,0,Math.PI*2); ctx.fillStyle=`rgba(0,212,255,${a.alpha})`; ctx.fill();
      });
      // particules
      const t = Date.now()*.001;
      for (let i=0; i<4; i++) {
        const px=cx+R*.9*Math.cos(t*(1.2+i*.3)+i*1.5), py=cy+R*.6*Math.sin(t*.7*(1.2+i*.3));
        ctx.beginPath(); ctx.arc(px,py,1.8,0,Math.PI*2); ctx.fillStyle="rgba(0,212,255,.5)"; ctx.fill();
      }
      globeId = requestAnimationFrame(draw);
    }
    draw();
  }

  // ═══════════════════════════════════════════════════════
  // WEATHER
  // ═══════════════════════════════════════════════════════
  async function renderWeather() {
    const d = await fetchJSON("/api/weather");
    if (!d || !d.ok) return;
    const c = d.current;
    $("#weather-temp").textContent     = `${Math.round(c.temperature)}°C`;
    $("#weather-desc").textContent     = (c.description||"").replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]/gu,"").trim()||"Inconnu";
    $("#weather-city").textContent     = d.city||"Lille";
    $("#weather-wind").textContent     = `Vent ${Math.round(c.wind_speed)} km/h`;
    $("#weather-humidity").textContent = `Humidité ${c.humidity??"?"}%`;
  }

  // ═══════════════════════════════════════════════════════
  // SERVER
  // ═══════════════════════════════════════════════════════
  async function renderServer() {
    const d = await fetchJSON("/api/stats");
    if (!d || !d.ok) return;
    const cpu = d.cpu?.percent ?? 0, ram = d.ram?.percent ?? 0, disk = d.disk?.percent ?? 0;

    const svcMap = [
      { name:"Ollama",  on:d.ollama },
      { name:"Backend", on:d.backend },
      { name:"Audio",   on:!!(d.backend_data?.audio_daemon?.state) },
      { name:"Email",   on:!!(d.backend_data?.email_watcher?.running) }
    ];
    const svcs = svcMap.map((s,i) =>
      `<div class="service-item"><div class="service-dot ${s.on?'on':'off'}" style="animation-delay:${(i*.4).toFixed(1)}s"></div><span class="service-name${s.on?'':' off'}">${s.name}</span></div>`
    ).join("");

    const bar = (pct, color, cls) => `<div class="stat-bar"><div class="stat-bar-fill" style="width:${Math.min(pct,100)}%;background:${color}"></div></div>`;
    const row = (label, pct, color) =>
      `<div class="stat-row"><span class="stat-label">${label}</span>${bar(pct,color)}<span class="stat-val">${Math.round(pct)}%</span></div>`;

    $("#widget-server").innerHTML =
      `<div class="card-title-row"><div class="card-title">Serveur</div><div class="card-sub" id="server-uptime">${d.uptime||"—"}</div></div>` +
      row("CPU", cpu, "#00d4ff") +
      row("RAM", ram, ram>80?"#ff3b30":"#30d158") +
      row("DSK", disk, "#f59e0b") +
      `<div class="service-list">${svcs}</div>`;
  }

  // ═══════════════════════════════════════════════════════
  // AGENDA
  // ═══════════════════════════════════════════════════════
  async function renderCalendar() {
    const d = await fetchJSON("/api/calendar");
    const el = $("#widget-agenda"); if (!el) return;
    if (!d || d.length===0) {
      el.innerHTML=`<div class="card-title" style="margin-bottom:14px">Agenda</div><div class="empty-state">Aucun événement aujourd'hui</div>`; return;
    }
    const now = new Date();
    const items = d.slice(0,6).map(ev => {
      const s = ev.start ? new Date(ev.start) : null;
      const e = ev.end ? new Date(ev.end) : null;
      const hh = s ? `${String(s.getHours()).padStart(2,"0")}:${String(s.getMinutes()).padStart(2,"0")}` : "--:--";
      let op=1, bg="", bl="", dot="rgba(0,212,255,.3)", fw="";
      if (e && e<now) { op=.3; dot="rgba(0,212,255,.4)"; }
      else if (s && s<=now && (!e||e>=now)) { bl="border-left:2px solid #00d4ff;"; bg="background:rgba(0,212,255,.06);"; dot="#00d4ff"; fw="font-weight:500;"; }
      return `<div class="agenda-item" style="opacity:${op};${bg}${bl}"><span class="agenda-time">${hh}</span><div class="agenda-dot" style="background:${dot}"></div><span class="agenda-title" style="${fw}">${esc(ev.title||ev.summary||"Sans titre")}</span></div>`;
    }).join("");
    el.innerHTML=`<div class="card-title" style="margin-bottom:14px">Agenda</div><div id="agenda-list"><div class="agenda-line"></div>${items}</div>`;
  }

  // ═══════════════════════════════════════════════════════
  // TASKS
  // ═══════════════════════════════════════════════════════
  async function renderTasks() {
    const d = await fetchJSON("/api/tasks");
    const el = $("#widget-tasks"); if (!el) return;
    const tasks = d||[];
    const doing = tasks.filter(t=>t.status==="doing").length;
    const todo  = tasks.filter(t=>t.status==="todo").length;
    const items = tasks.slice(0,7).map(t => {
      const done=t.status==="done", high=t.priority==="high"&&!done;
      const col=done?"rgba(255,255,255,.3)":high?"#ff3b30":"rgba(255,255,255,.8)";
      const tc=done?"rgba(255,255,255,.4)":high?"#ff3b30":"rgba(255,255,255,.85)";
      return `<div class="task-item"><span class="task-icon" style="color:${col}">${done?"✓":"●"}</span><span class="task-text" style="color:${tc}">${esc(t.title)}</span></div>`;
    }).join("");
    el.innerHTML=`<div class="card-title-row" style="margin-bottom:14px"><div class="card-title">Tâches</div><div class="card-sub">${doing} en cours · ${todo} à faire</div></div><div style="display:flex;flex-direction:column;gap:6px;flex:1">${items||'<div class="empty-state">Aucune tâche</div>'}</div>`;
  }

  // ═══════════════════════════════════════════════════════
  // MESSAGES
  // ═══════════════════════════════════════════════════════
  async function renderMessages() {
    const d = await fetchJSON("/api/messages");
    const el = $("#widget-messages"); if (!el) return;
    const msgs = d||[];
    const items = msgs.slice(0,5).map((m,i) => {
      const unread = i===0, dc = unread?"#00d4ff":"transparent";
      const nc = unread?"rgba(255,255,255,.9)":"rgba(255,255,255,.7)";
      const tc = unread?"rgba(255,255,255,.45)":"rgba(255,255,255,.35)";
      const fw = unread?"font-weight:600;":"";
      return `<div class="msg-item"><div class="msg-dot" style="background:${dc}"></div><div class="msg-body"><div class="msg-header"><span class="msg-name" style="color:${nc};${fw}">${esc(m.display_name||"?")}</span><span class="msg-time">${timeAgo(m.timestamp)}</span></div><div class="msg-text" style="color:${tc}">${esc(m.text||"")}</div></div></div>`;
    }).join("");
    el.innerHTML=`<div class="card-title" style="margin-bottom:14px">Messages</div><div style="display:flex;flex-direction:column;gap:4px;flex:1">${items||'<div class="empty-state">Aucun message</div>'}</div>`;
  }

  // ═══════════════════════════════════════════════════════
  // EMAILS
  // ═══════════════════════════════════════════════════════
  async function renderEmails() {
    const d = await fetchJSON("/api/emails");
    const el = $("#widget-emails"); if (!el) return;
    const emails = d||[];
    const unread = emails.length, urgent = emails.filter(e=>e.priority==="urgent"||e.priority==="high").length;
    const items = emails.slice(0,5).map(e => {
      const urg = e.priority==="urgent"||e.priority==="high";
      return `<div class="email-item${urg?' urgent':''}"><div style="flex:1;min-width:0;display:flex;gap:12px;align-items:baseline"><span class="email-sender${urg?' urgent':''}">${esc(e.sender||"?")}</span><span class="email-subject${urg?' urgent':''}">${esc(e.subject||"")}</span></div></div>`;
    }).join("");
    el.innerHTML=`<div class="card-title-row" style="margin-bottom:10px"><div class="card-title">Emails</div></div><div class="email-counters"><div><span class="email-counter-val">${unread}</span> <span class="email-counter-label">non lus</span></div><div><span class="email-counter-val" style="color:${urgent?'#ff3b30':'rgba(255,255,255,.6)'}">${urgent}</span> <span class="email-counter-label">urgent</span></div></div><div style="display:flex;flex-direction:column;gap:2px;flex:1">${items||'<div class="empty-state">Aucun email</div>'}</div>`;
  }

  // ═══════════════════════════════════════════════════════
  // ACTIONS IA
  // ═══════════════════════════════════════════════════════
  async function renderActions() {
    const d = await fetchJSON("/api/automations");
    const actions = d||[];
    const errors = actions.filter(a=>a.status==="error").length;
    const last = actions[0];
    $("#actions-total").textContent = actions.length;
    $("#actions-errors").textContent = `${errors} erreur${errors>1?'s':''}`;
    $("#actions-errors").style.color = errors>0?"#ff3b30":"rgba(255,255,255,.3)";
    $("#actions-last").textContent = `${(last?.action_type||"—").replace(/_/g," ")} · ${last?.time||"—"}`;
  }

  // ═══════════════════════════════════════════════════════
  // MACHINES
  // ═══════════════════════════════════════════════════════
  async function renderMachines() {
    const d = await fetchJSON("/api/devices");
    const devs = d?.devices||[];
    const items = devs.map((d,i) => {
      const on = d.is_active||d.status==="online";
      return `<div style="display:flex;align-items:center;gap:8px"><div style="width:7px;height:7px;border-radius:50%;background:${on?'#30d158':'rgba(255,255,255,.15)'};${on?`animation:pulse 2.5s infinite ${(i*.5).toFixed(1)}s`:''}"></div><span style="font-size:16px;color:${on?'rgba(255,255,255,.7)':'rgba(255,255,255,.3)'}">${esc(d.device_name||d.device_id)}</span><span style="font-size:14px;color:${on?'#30d158':'rgba(255,255,255,.2)'};margin-left:auto;text-transform:uppercase;letter-spacing:1px">${on?'Actif':(d.idle_text||'Off')}</span></div>`;
    }).join("");
    $("#widget-machines").innerHTML=`<div class="card-title">Machines</div><div style="display:flex;flex-direction:column;gap:6px;margin-top:2px">${items||'<span style="color:rgba(255,255,255,.25)">Aucune machine</span>'}</div>`;
  }

  // ═══════════════════════════════════════════════════════
  // MOOD
  // ═══════════════════════════════════════════════════════
  async function renderMood() {
    const d = await fetchJSON("/api/mood");
    if (!d||!d.ok||!d.mood_score) {
      $("#mood-score").textContent="--"; $("#mood-label").textContent="---"; return;
    }
    const sc=d.mood_score, lab=sc>=7?"excellent":sc>=5?"bien":sc>=3?"moyen":"bas";
    $("#mood-score").textContent=sc; $("#mood-label").textContent=lab;
    const bars=[55,75,45,65,85,55,sc*10];
    $("#mood-sparkline").innerHTML= bars.map((h,i)=>`<div class="mood-bar" style="height:${h}%;background:${i===6?'#00d4ff':'rgba(0,212,255,.2)'}"></div>`).join("");
  }

  // ═══════════════════════════════════════════════════════
  // API COST
  // ═══════════════════════════════════════════════════════
  async function renderCost() {
    const d = await fetchJSON("/api/status");
    const t = d?.data?.today;
    if (!t) { $("#cost-value").textContent="$--"; return; }
    const c = t.total_cost??0, ti=t.total_in??0, to=t.total_out??0;
    const fmt = n => n>=1000?(n/1000).toFixed(1)+"k":String(n);
    $("#cost-value").textContent=`$${Number(c).toFixed(2)}`;
    $("#cost-tokens").textContent=`${fmt(ti)} in · ${fmt(to)} out`;
    $("#cost-model").textContent = d?.data?.models?.main||"---";
  }

  // ═══════════════════════════════════════════════════════
  // VOICE OVERLAY (SSE)
  // ═══════════════════════════════════════════════════════
  const STATES = {
    idle:           { css:"", orbBg:"#52525b", orbShadow:"0 0 12px rgba(82,82,91,.3)", stateColor:"#52525b", stateText:"IDLE", label:"VEILLE", labelColor:"#52525b", user:"", jarvis:"" },
    wake_listening: { css:"visible", orbBg:"#00d4ff", orbShadow:"0 0 20px rgba(0,212,255,.5)", stateColor:"#00d4ff", stateText:"LISTEN", label:"ÉCOUTE", labelColor:"#00d4ff", user:"Je vous écoute, Monsieur...", jarvis:"" },
    listening:      { css:"visible", orbBg:"#00d4ff", orbShadow:"0 0 20px rgba(0,212,255,.5)", stateColor:"#00d4ff", stateText:"LISTEN", label:"ÉCOUTE", labelColor:"#00d4ff", user:"Je vous écoute...", jarvis:"" },
    processing:     { css:"visible", orbBg:"#a855f7", orbShadow:"0 0 20px rgba(168,85,247,.5)", stateColor:"#a855f7", stateText:"PROC", label:"TRAITEMENT", labelColor:"#a855f7", user:"Analyse en cours...", jarvis:"" },
    speaking:       { css:"visible", orbBg:"#f59e0b", orbShadow:"0 0 20px rgba(245,158,11,.5)", stateColor:"#f59e0b", stateText:"SPEAK", label:"JARVIS PARLE", labelColor:"#f59e0b", user:"", jarvis:"" },
    error:          { css:"visible", orbBg:"#ef4444", orbShadow:"0 0 12px rgba(239,68,68,.4)", stateColor:"#ef4444", stateText:"ERR", label:"ERREUR", labelColor:"#ef4444", user:"Une erreur est survenue", jarvis:"" }
  };
  let hideTimer = null;

  function applyVoiceState(st, userTxt, jarvisTxt) {
    const s = STATES[st] || STATES.idle;
    const ov = $("#voice-overlay");
    ov.className = s.css;
    const orb = $("#voice-orb"); orb.style.background=s.orbBg; orb.style.boxShadow=s.orbShadow;
    const vs = $("#voice-state"); vs.style.color=s.stateColor; vs.textContent=s.stateText;
    const vl = $("#voice-label"); vl.style.color=s.labelColor; vl.textContent=s.label;
    $("#voice-user").textContent   = userTxt||s.user||"";
    $("#voice-jarvis").textContent = jarvisTxt||s.jarvis||"";
    if (st==="speaking"||st==="processing"||st==="listening") {
      $("#dashboard").style.opacity=".7";
    } else {
      $("#dashboard").style.opacity="1";
    }
    if (hideTimer) clearTimeout(hideTimer);
    if (st==="idle") hideTimer = setTimeout(() => { ov.className=""; $("#dashboard").style.opacity="1"; }, 3000);
  }

  function connectSSE() {
    const es = new EventSource("/api/events");
    es.onmessage = e => {
      try {
        const d = JSON.parse(e.data);
        let st = "idle";
        if (d.type?.startsWith("audio_daemon_")) st = d.state||d.type.replace("audio_daemon_","");
        applyVoiceState(st, d.user_text, d.jarvis_text);
      } catch {}
    };
    es.onerror = () => { es.close(); setTimeout(connectSSE, 5000); };
  }

  // ═══════════════════════════════════════════════════════
  // POLLING
  // ═══════════════════════════════════════════════════════
  const poll = (fn, ms) => { fn(); setInterval(fn, ms); };

  function init() {
    console.log("[tv-v2] Init War Room");

    // Auto fullscreen
    try {
      const el = document.documentElement;
      if (el.requestFullscreen) { el.requestFullscreen().catch(() => {}); }
      else if (el.webkitRequestFullscreen) { el.webkitRequestFullscreen(); }
    } catch (_) {}

    clockTick(); setInterval(clockTick, 1000);
    initGlobe();
    poll(renderWeather,  900_000);
    poll(renderServer,    10_000);
    poll(renderCalendar, 300_000);
    poll(renderTasks,    120_000);
    poll(renderMessages,  30_000);
    poll(renderEmails,   300_000);
    poll(renderActions,   30_000);
    poll(renderMachines,  60_000);
    poll(renderMood,     300_000);
    poll(renderCost,      60_000);
    connectSSE();
  }

  if (document.readyState==="loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
