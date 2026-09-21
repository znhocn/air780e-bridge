/* Air780E SMS Bridge - frontend logic */
(function () {
  const API = "/api";
  /* Bilingual glue: everything goes through the i18n layer in this file —
    T() : i18n.t() - translation for the current locale (dynamic text);
    PO() : i18n.pair() - pick current-locale value from a {zh,en} object;
    POO() : i18n.pairOpt() - like pair but with a fallback default;
    esc  : HTML escaping (via i18n.esc, or this local fallback if i18n is missing). */
  const I18N = window.i18n || null;
  const T = (k, ...a) => (I18N ? I18N.t(k, ...a) : k);
  const PO = (v, fb) => (I18N ? I18N.pair(v, fb) : (v && typeof v === "object" && !Array.isArray(v) ? (v.zh || v.en || fb || "") : (v == null ? (fb || "") : v)));
  const POO = (v, fb) => (I18N ? I18N.pairOpt(v, fb) : PO(v, fb));
  function esc(s) {
    if (I18N && I18N.esc) return I18N.esc(s);
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  let token = localStorage.getItem("air780e_token") || "";
  let curMsgFilter = "";
  let curPeer = null;
  let convMessages = [];
  let convOldestId = null;
  let convNoMore = false;
  let contacts = [];
  let contactsLoaded = false;
  let statusTimer = null;
  let notifyTypes = null;
  let searchTimer = null;
  let appVersion = "";

  const $ = (id) => document.getElementById(id);

  // ---------- utilities ----------
  function busy(btn, fn) {
    btn.disabled = true;
    btn.classList.add("loading");
    return Promise.resolve()
      .then(fn)
      .finally(() => {
        btn.disabled = false;
        btn.classList.remove("loading");
      });
  }
  function toast(msg, isErr) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.toggle("error", !!isErr);
    el.classList.remove("hidden");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.add("hidden"), 3200);
  }

  // Copy to clipboard: prefers the Clipboard API (only available on https/localhost),
  // otherwise falls back to execCommand (works over plain http too).
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (e) { /* fall through to the execCommand path */ }
    }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (e) {
      return false;
    }
  }

  async function call(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;
    const r = await fetch(API + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401 && !path.includes("/auth/login")) {
      logout();
      throw new Error(T("login.expired"));
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText || String(r.status));
    return data;
  }

  // ---------- login / first deployment / logout ----------
  function showApp() {
    $("login-view").classList.add("hidden");
    $("app-view").classList.remove("hidden");
    loadAll();
  }
  function logout() {
    token = "";
    localStorage.removeItem("air780e_token");
    $("app-view").classList.add("hidden");
    closePwd();
    $("login-view").classList.remove("hidden");
    $("setup-form").classList.add("hidden");
    $("login-form").classList.remove("hidden");
    $("login-username").value = "";
    $("login-password").value = "";
    $("login-error").classList.add("hidden");
  }
  async function checkSetup() {
    try {
      const s = await call("GET", "/auth/setup-required");
      if (s.setup_required) {
        $("login-form").classList.add("hidden");
        $("setup-form").classList.remove("hidden");
        return;
      }
    } catch (e) { /* keep the default login form */ }
    $("login-form").classList.remove("hidden");
    $("setup-form").classList.add("hidden");
  }
  async function login(ev) {
    ev.preventDefault();
    $("login-error").classList.add("hidden");
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        const res = await call("POST", "/auth/login", {
          username: $("login-username").value.trim(),
          password: $("login-password").value,
        });
        token = res.token;
        localStorage.setItem("air780e_token", token);
        showApp();
      } catch (e) {
        $("login-error").textContent = e.message;
        $("login-error").classList.remove("hidden");
      }
    });
  }
  async function setup(ev) {
    ev.preventDefault();
    $("setup-error").classList.add("hidden");
    const p = $("setup-password").value;
    if (p !== $("setup-password2").value) {
      $("setup-error").textContent = T("setup.mismatch");
      $("setup-error").classList.remove("hidden");
      return;
    }
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        const res = await call("POST", "/auth/setup", {
          username: $("setup-username").value.trim(),
          password: p,
        });
        token = res.token;
        localStorage.setItem("air780e_token", token);
        showApp();
      } catch (e) {
        $("setup-error").textContent = e.message;
        $("setup-error").classList.remove("hidden");
      }
    });
  }

  // ---------- view loaders ----------
  async function loadAll() {
    if (statusTimer) clearInterval(statusTimer);
    switchView(currentView());
    setInterval(async () => {
      if (token && !$("app-view").classList.contains("hidden")) {
        const v = currentView();
        try {
          if (v === "messages") {
            await Promise.all([loadStatus(true), loadMessages(true)]);
          } else if (v === "status") {
            await loadStatus(true);
          } else if (v === "logs") {
            await loadLogs();
          } else {
            // notify / tasks / keys: refreshed on switchView, no periodic polling needed
          }
        } catch (e) {}
      }
    }, 6000);
  }

  function currentView() {
    return document.querySelector(".nav-btn.active").dataset.view;
  }
  function curLang() { return (I18N && I18N.lang) || "en"; }
  function renderVersion() {
    $("app-version").textContent = appVersion ? (appVersion.startsWith("v") ? appVersion : "v" + appVersion) : "";
  }
  async function loadVersion() {
    try {
      const r = await fetch(API + "/version");
      const d = await r.json().catch(() => ({}));
      if (d && d.version) {
        appVersion = d.version;
        renderVersion();
      }
    } catch (e) { /* ignore */ }
  }
  function renderLangToggle() {
    $("lang-toggle").textContent = curLang() === "zh" ? "EN" : "中文";
    $("lang-toggle").title = curLang() === "zh" ? "Switch to English" : "切换到中文";
    renderVersion();
  }
  function toggleLang() {
    const next = curLang() === "zh" ? "en" : "zh";
    if (I18N && I18N.setLang) I18N.setLang(next);
    if (I18N && I18N.applyI18n) I18N.applyI18n();
    renderLangToggle();
    if (!$("app-view").classList.contains("hidden")) switchView(currentView());
  }
  function switchView(name) {
    ["status", "messages", "notify", "tasks", "keys", "logs"].forEach((v) => {
      $("view-" + v).classList.toggle("hidden", v !== name);
    });
    if (name !== "messages") {
      $("contacts-panel").classList.add("hidden");
      resetContactForm();
    } else {
      document.querySelector(".chat-wrap").classList.remove("chat-open");
    }
    document.querySelectorAll(".nav-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === name);
    });
    ({ status: () => loadStatus(true),
       messages: loadMessages,
       notify: () => { loadNotifyConfigs(); renderNotifyFields(); },
       tasks: loadTasks,
       keys: loadKeys,
       logs: loadLogs })[name]();
  }

  // device status
  const ICONS = {
    signal: `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 20h.01"/><path d="M7 20v-4"/><path d="M12 20v-8"/><path d="M17 20V8"/><path d="M22 4v16"/></svg>`,
    chat: `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
    radio: `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.2 19.1 19.1"/></svg>`,
    pulse: `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`,
    cpu: `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v2"/><path d="M15 2v2"/><path d="M9 20v2"/><path d="M15 20v2"/><path d="M2 9h2"/><path d="M2 15h2"/><path d="M20 9h2"/><path d="M20 15h2"/></svg>`,
  };
  function sigDesc(connected, rssi) {
    if (!connected) return T("status.sig.waiting");
    if (rssi == null) return T("status.signal.none");
    if (rssi >= -80) return T("status.sig.excellent");
    if (rssi >= -90) return T("status.sig.good");
    if (rssi >= -100) return T("status.sig.fair");
    if (rssi >= -110) return T("status.sig.poor");
    return T("status.sig.weak");
  }
  function svcRow(label, healthy, extra, stateText, cls) {
    const st = stateText || (healthy ? T("status.service.ok") : T("status.service.wait"));
    const c = cls || (healthy ? "ok" : "wait");
    return `<div class="svc-row"><span>${label}${extra ? ` <b class="svc-badge">${extra}</b>` : ""}</span>
      <span class="svc-state ${c}">
        <i class="dot ${healthy ? "on" : ""}"></i>${st}
      </span></div>`;
  }
  function dlRow(k, v) {
    return `<div class="dl-row"><dt>${k}</dt><dd>${v}</dd></div>`;
  }

  async function loadStatus(silent) {
    try {
      const s = await call("GET", "/device/status");
      const grid = $("status-grid");
      const csq = s.csq_rssi;
      const pct = csq == null || csq >= 99 ? 0 : Math.max(0, Math.min(100, Math.round(csq / 31 * 100)));
      const rssi = s.rssi_dbm;
      const connected = !!s.connected;
      const registered = s.reg_state === "1" || s.reg_state === "5";
      const desc = sigDesc(connected, rssi);
      const total = s.messages_total || 0;
      const inCount = s.messages_in || 0;
      const outTotal = total - inCount;
      const today = {
        in: s.messages_today_in || 0,
        out: s.messages_today_out || 0,
        failed: s.messages_today_failed || 0,
      };
      const dl = (k, v) => dlRow(k, esc(v || "—"));
      const emDash = "—";
      const connLine = connected
        ? `${T("status.online")} · ${esc(s.port || "—")}`
        : T("status.stat.conn.sub");
      const pending = s.messages_out_pending || 0;
      const SIM = {
        ready: ["ok", T("status.sim.ready")],
        locked: ["warn", T("status.sim.locked")],
        absent: ["bad", T("status.sim.absent")],
        fail: ["bad", T("status.sim.fail")],
      };
      const sim = s.sim_state ? SIM[s.sim_state] || ["wait", String(s.sim_state)] : null;
      const simTag = sim ? `<b class="tag ${sim[0]}">${esc(sim[1])}</b>` : `<b class="tag">${emDash}</b>`;
      const regMap = { "0": T("status.reg.0"), "1": T("status.reg.1"), "2": T("status.reg.2"), "3": T("status.reg.3"), "4": T("status.reg.4"), "5": T("status.reg.5") };
      const regLabel = s.reg_state ? (regMap[s.reg_state] || T("status.reg.none")) : emDash;

      grid.innerHTML = `
        <div class="dash-grid">
          <article class="dash-card">
            <div class="dash-head">
              <div class="dash-title">
                <span class="dash-ic">${ICONS.pulse}</span>
                <div><h3>${T("status.stat.services")}</h3><p>${connLine}</p></div>
              </div>
              <span class="chip ${connected ? "ok" : "bad"}">${connected ? T("status.online") : T("status.offline")}</span>
            </div>
            <div class="svc-list">
              ${svcRow(T("status.service.conn"), connected, "", connected ? T("status.online") : T("status.offline"), connected ? "ok" : "bad")}
              ${svcRow(T("status.service.sms"), connected)}
              ${svcRow(T("status.service.reg"), connected && registered)}
              ${svcRow(T("status.service.call"), connected, s.calls_total || 0)}
            </div>
            <div class="dash-foot">${T("status.checked")}: <b>${esc(s.checked_at || "—")}</b></div>
          </article>

          <article class="dash-card">
            <div class="dash-head">
              <div class="dash-title">
                <span class="dash-ic">${ICONS.radio}</span>
                <div><h3>${T("status.stat.network")}</h3><p>${s.operator || T("status.stat.network.sub")}</p></div>
              </div>
              <span class="chip ${registered ? "ok" : "warn"}">${registered ? T("status.stat.reg.ok") : T("status.stat.reg.no")}</span>
            </div>
            <div class="dash-body">
              <div class="sig-hero">
                <div>
                  <p class="sig-k">${T("status.stat.realtime")}</p>
                  <div class="sig-val">${connected ? pct : emDash}<small class="unit">%</small></div>
                </div>
                <p class="sig-desc">${desc}</p>
              </div>
              <div class="sig-bar"><div style="width:${pct}%"></div></div>
              <div class="sig-metrics">
                <div><b>${rssi != null ? rssi : emDash}</b><span>RSSI</span></div>
                <div><b>${csq != null && csq < 99 ? csq : emDash}</b><span>CSQ</span></div>
                <div><b>${s.csq_ber != null ? s.csq_ber : emDash}</b><span>BER</span></div>
              </div>
              <dl class="dash-dl">
                ${dl(T("status.card.reg"), regLabel)}
                <div class="dl-row"><dt>${T("status.card.sim")}</dt><dd>${simTag}</dd></div>
              </dl>
            </div>
          </article>

          <article class="dash-card dev-card">
            <div class="dash-head">
              <div class="dash-title">
                <span class="dash-ic">${ICONS.cpu}</span>
                <div><h3>${T("status.stat.device")}</h3><p>${T("status.stat.device.sub")}</p></div>
              </div>
            </div>
            <div class="dash-body">
              <dl class="dash-dl">
                ${dl(T("status.card.port"), s.port)}
                ${dl(T("status.card.model"), s.model)}
                ${dl(T("status.card.fw"), s.fw_version)}
                <div class="dl-row dl-pair">
                  <div><dt>${T("status.card.imei")}</dt><dd>${esc(s.imei || "—")}</dd></div>
                  <div><dt>${T("status.card.ccid")}</dt><dd>${esc(s.ccid || "—")}</dd></div>
                </div>
              </dl>
            </div>
          </article>

          <article class="dash-card sms-card">
            <div class="dash-head">
              <div class="dash-title">
                <span class="dash-ic">${ICONS.chat}</span>
                <div><h3>${T("status.stat.sms")}</h3><p>${T("status.stat.sms.sub")}</p></div>
              </div>
            </div>
            <div class="dash-body sms-body">
              <div class="stats-block">
                <div class="sms-block-title">${T("status.stat.history")}</div>
                <div class="stats-nums">
                  <div><b>${total}</b><span>${T("status.num.total")}</span></div>
                  <div><b>${inCount}</b><span>${T("status.num.in")}</span></div>
                  <div><b>${outTotal}</b><span>${T("status.num.sent")}</span></div>
                  <div><b>${pending}</b><span>${T("status.num.pending")}</span></div>
                </div>
              </div>
              <div class="stats-block">
                <div class="sms-block-title">${T("status.num.today")}</div>
                <div class="stats-nums">
                  <div><b>${today.in}</b><span>${T("status.num.in")}</span></div>
                  <div><b>${today.out}</b><span>${T("status.num.sent")}</span></div>
                  <div><b>${today.failed}</b><span>${T("status.num.failed")}</span></div>
                </div>
              </div>
            </div>
          </article>
        </div>`;
      if (!silent) toast(T("status.refreshed"));
    } catch (e) { if (!silent) toast(e.message, true); }
  }

  async function reconnectDevice() {
    const btn = $("reconnect-device");
    await busy(btn, async () => {
      try {
        const r = await call("POST", "/device/reconnect");
        toast(r.detail || (r.ok ? "ok" : "failed"), !r.ok);
        loadStatus(true);
        loadLogs();
      } catch (e) { toast(e.message, true); }
    });
  }

  async function checkSmsCapable() {
    const btn = $("check-sms-capable");
    await busy(btn, async () => {
      try {
        const r = await call("GET", "/device/sms-capable");
        toast(r.capable ? T("status.smscapable.ok") : (r.detail || T("status.smscapable.fail")), !r.capable);
      } catch (e) { toast(e.message, true); }
    });
  }

  // ---------- chat: messages ----------
  function fmtTime(s) {
    if (!s) return "";
    const d = new Date(String(s).replace(" ", "T"));
    if (isNaN(d)) return s;
    const pad = (n) => String(n).padStart(2, "0");
    const sameDay = d.toDateString() === new Date().toDateString();
    if (sameDay) return pad(d.getHours()) + ":" + pad(d.getMinutes());
    return `${d.getMonth() + 1}-${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function dayLabel(s) {
    const d = new Date(String(s).replace(" ", "T"));
    if (isNaN(d)) return "";
    const today = new Date();
    const yest = new Date();
    yest.setDate(yest.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return T("msg.today");
    if (d.toDateString() === yest.toDateString()) return T("msg.yesterday");
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function avatarFor(peer, name) {
    const label = ((name || peer || "?").trim().charAt(0) || "?").toUpperCase();
    const hues = [217, 262, 340, 32, 168, 190, 280, 12];
    let h = 0;
    for (const ch of (name || peer || "x")) h = (h + ch.codePointAt(0)) % hues.length;
    return `<span class="avatar" style="background:hsla(${hues[h]},72%,58%,.16);color:hsla(${hues[h]},82%,72%,1)">${esc(label)}</span>`;
  }
  function statusCls(direction, status) {
    if (direction !== "out") return "st-ok";
    return status === "failed" ? "st-bad" : status === "queued" ? "st-warn" : "st-ok";
  }
  function normDigits(s) { return String(s == null ? "" : s).replace(/\D/g, ""); }
  function contactFor(peer) {
    const d = normDigits(peer);
    let c = contacts.find((x) => normDigits(x.phone) === d);
    if (!c && d.length === 13 && d.startsWith("86")) {
      c = contacts.find((x) => normDigits(x.phone) === d.slice(2));
    }
    return c;
  }
  function contactName(peer) {
    const c = contactFor(peer);
    return c ? c.name : "";
  }
  async function ensureContacts() {
    if (contactsLoaded) return contacts;
    contacts = await call("GET", "/contacts");
    contactsLoaded = true;
    return contacts;
  }
  function renderChatEmpty() {
    $("chat-body").innerHTML = `<p class="hint chat-empty">${T("msg.conv.empty")}</p>`;
    $("chat-peer").textContent = "";
    $("chat-contact-sub").textContent = "";
    $("chat-head-avatar").innerHTML = "";
  }
  function renderConversations(convs) {
    const total = convs.length;
    $("msg-filter").textContent = T("msg.conv.count", total) + (curMsgFilter ? `（${T("msg.filtered")}）` : "");
    $("conv-list").innerHTML = convs.map((c) => {
      const name = c.contact_name || contactName(c.peer) || c.peer;
      const prefix = c.last_direction === "in" ? "\u2193" : c.last_direction === "out" ? "\u2191" : "";
      const st = c.last_direction
        ? (c.last_direction === "out"
          ? (c.last_status === "sent" ? T("msg.status.sent") : c.last_status === "failed" ? T("msg.status.failed") : c.last_status ? esc(c.last_status) : T("msg.status.queued"))
          : T("msg.status.stored"))
        : "";
      const countBadge = c.count > 1 ? `<span class="conv-count">${c.count}</span>` : "";
      const stCls = statusCls(c.last_direction, c.last_status);
      return `<div class="conv-item${c.peer === curPeer ? " active" : ""}" data-conv="${esc(c.peer)}">
        ${avatarFor(c.peer, c.contact_name ? c.contact_name : name)}
        <div class="conv-main">
          <div class="conv-line"><span class="conv-name">${esc(name)}</span>
            <span class="conv-side">${countBadge}${c.last_at ? `<span class="conv-time">${esc(fmtTime(c.last_at))}</span>` : ""}</span></div>
          <div class="conv-preview">${prefix ? `<span class="conv-dir ${c.last_direction}">${prefix}</span>` : ""}${esc(c.last_content || "")}</div>${st ? `<div class="conv-meta"><span class="st ${stCls}">${st}</span></div>` : ""}
        </div>
      </div>`;
    }).join("") || `<p class="hint chat-empty">${T("msg.conv.empty")}</p>`;
  }
  function renderBubbles(scrollBottom = true) {
    const body = $("chat-body");
    if (!convMessages.length) {
      body.innerHTML = `<p class="hint chat-empty">${T("msg.conv.empty")}</p>`;
      return;
    }
    const loadMore = (!convNoMore && convOldestId != null)
      ? `<div class="load-more-wrap"><button class="btn small" data-load-more="1">${T("msg.loadmore")}</button></div>` : "";
    let lastDay = "";
    body.innerHTML = loadMore + convMessages.map((m) => {
      const day = dayLabel(m.created_at);
      const sep = day && day !== lastDay ? `<div class="day-sep"><span>${day}</span></div>` : "";
      lastDay = day;
      const isOut = m.direction === "out";
      const st = isOut
        ? (m.status === "sent" ? T("msg.status.sent") : m.status === "failed" ? T("msg.status.failed") : m.status ? esc(m.status) : T("msg.status.queued"))
        : T("msg.status.stored");
      const stCls = statusCls(m.direction, m.status);
      const av = avatarFor(m.sender || curPeer, contactName(m.sender || curPeer));
      return `${sep}<div class="bubble-row ${isOut ? "out" : "in"}">${isOut ? "" : av}
        <div class="bubble-group ${isOut ? "out" : "in"}">
          <div class="bubble ${isOut ? "out" : "in"}">${esc(m.content)}</div>
          <div class="bubble-meta"><span class="st ${stCls}">${st}</span><span class="dot">·</span>${esc(fmtTime(m.created_at))}</div>
        </div></div>`;
    }).join("");
    if (scrollBottom) body.scrollTop = body.scrollHeight;
  }
  function updateChatHeader() {
    const name = contactName(curPeer) || curPeer;
    $("chat-peer").textContent = name;
    $("chat-contact-sub").textContent = contactName(curPeer) ? (curPeer || "") : T("msg.contact.hint");
    $("chat-head-avatar").innerHTML = curPeer ? avatarFor(curPeer, name) : "";
  }
  async function fetchConversation(peer) {
    const d = await call("GET", "/messages?peer=" + encodeURIComponent(peer) + "&page_size=200");
    convMessages = d.items;
    convOldestId = d.items.length ? d.items[0].id : null;
    convNoMore = d.items.length < 200;
    renderBubbles();
    updateChatHeader();
  }
  async function loadOlder() {
    if (!curPeer || convNoMore) return;
    const btn = document.querySelector("[data-load-more]");
    if (!btn) return;
    const body = $("chat-body");
    const firstEl = body.firstElementChild;
    const prevAbs = firstEl ? firstEl.offsetTop - body.scrollTop : 0;
    await busy(btn, async () => {
      const d = await call("GET", "/messages?peer=" + encodeURIComponent(curPeer)
        + "&before_id=" + convOldestId + "&page_size=100");
      const older = d.items;
      if (!older.length) {
        convNoMore = true;
      } else {
        convMessages = older.concat(convMessages);
        convOldestId = older[0].id;
        if (older.length < 100) convNoMore = true;
      }
    }).catch((e) => toast(e.message, true));
    renderBubbles(false);
    const el = body.firstElementChild;
    body.scrollTop = (el ? el.offsetTop : 0) - prevAbs;
  }
  async function openConversation(peer) {
    curPeer = peer;
    document.querySelector(".chat-wrap").classList.add("chat-open");
    document.querySelectorAll(".conv-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.conv === peer);
    });
    await fetchConversation(peer);
  }
  async function loadMessages(silent) {
    try {
      await ensureContacts();
      const q = curMsgFilter ? "?q=" + encodeURIComponent(curMsgFilter) : "";
      const convs = await call("GET", "/messages/conversations" + q);
      renderConversations(convs);
      if (curPeer && convs.some((c) => c.peer === curPeer)) {
        await fetchConversation(curPeer);
      } else if (curPeer) {
        curPeer = null;
        renderChatEmpty();
      } else {
        renderChatEmpty();
      }
    } catch (e) { if (!silent) toast(e.message, true); }
  }
  function handleSearch() {
    const v = $("msg-search").value.trim();
    if (v === curMsgFilter) return;
    curMsgFilter = v;
    loadMessages();
  }

  async function sendSms(ev) {
    ev.preventDefault();
    if (!curPeer) { toast(T("msg.conv.pick"), true); return; }
    const txt = $("send-content").value.trim();
    if (!txt) return;
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        const res = await call("POST", "/messages/send", { to: curPeer, content: txt });
        toast(T("msg.enqueued"));
        $("send-content").value = "";
        await loadMessages();
      } catch (e) { toast(e.message, true); }
    });
  }

  // ---------- new SMS ----------
  function openCompose() {
    $("compose-modal").classList.remove("hidden");
    $("new-sms-to").focus();
  }
  function closeCompose() {
    $("compose-modal").classList.add("hidden");
  }
  function updateNewSmsChar() {
    $("new-sms-char").textContent = T("msg.compose.char", $("new-sms-content").value.length);
  }
  async function sendNewSms(ev) {
    ev.preventDefault();
    const to = $("new-sms-to").value.trim();
    const content = $("new-sms-content").value.trim();
    if (!to || !content) { toast(T("msg.compose.err"), true); return; }
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        await call("POST", "/messages/send", { to, content });
        toast(T("msg.enqueued"));
        closeCompose();
        $("new-sms-to").value = "";
        $("new-sms-content").value = "";
        updateNewSmsChar();
        await loadMessages();
        await openConversation(to);
      } catch (e) { toast(e.message, true); }
    });
  }

  // ---------- change password ----------
  function openPwd() {
    $("pwd-form").reset();
    $("pwd-error").classList.add("hidden");
    $("pwd-modal").classList.remove("hidden");
    $("pwd-current").focus();
  }
  function closePwd() {
    $("pwd-modal").classList.add("hidden");
  }
  async function changePassword(ev) {
    ev.preventDefault();
    $("pwd-error").classList.add("hidden");
    const np = $("pwd-new").value;
    if (np !== $("pwd-confirm").value) {
      $("pwd-error").textContent = T("pwd.mismatch");
      $("pwd-error").classList.remove("hidden");
      return;
    }
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        await call("POST", "/auth/change-password", {
          current_password: $("pwd-current").value,
          new_password: np,
        });
        toast(T("pwd.ok"));
        closePwd();
      } catch (e) {
        $("pwd-error").textContent = e.message;
        $("pwd-error").classList.remove("hidden");
      }
    });
  }

  // ---------- contacts ----------
  function toggleContacts(open) {
    const p = $("contacts-panel");
    const shouldOpen = open === undefined ? p.classList.contains("hidden") : open;
    p.classList.toggle("hidden", !shouldOpen);
    if (shouldOpen) renderContacts();
  }
  async function renderContacts() {
    try {
      contacts = await call("GET", "/contacts");
      contactsLoaded = true;
      $("contact-list").innerHTML = contacts.map((c) => `
        <div class="contact-item" data-open-contact="${esc(c.phone)}">
          ${avatarFor(c.phone, c.name)}
          <div class="contact-info">
            <div class="contact-line"><b class="contact-name">${esc(c.name)}</b><code>${esc(c.phone)}</code></div>
            ${c.note ? `<div class="contact-note">${esc(c.note)}</div>` : ""}
          </div>
          <div class="contact-ops">
            <button class="btn small" data-edit-contact="${c.id}">${T("contacts.ops.edit")}</button>
            <button class="btn small danger" data-del-contact="${c.id}">${T("contacts.ops.delete")}</button>
          </div>
        </div>`).join("") || `<p class="hint chat-empty">${T("contacts.empty")}</p>`;
    } catch (e) { toast(e.message, true); }
  }
  function resetContactForm() {
    $("contact-form").reset();
    $("contact-id").value = "";
    $("contact-cancel").classList.add("hidden");
  }
  async function saveContact(ev) {
    ev.preventDefault();
    const id = $("contact-id").value;
    const body = {
      name: $("contact-name").value.trim(),
      phone: $("contact-phone").value.trim(),
      note: $("contact-note").value.trim(),
    };
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        if (id) {
          await call("PUT", "/contacts/" + id, body);
          toast(T("contacts.updated"));
        } else {
          await call("POST", "/contacts", body);
          toast(T("contacts.added"));
        }
        resetContactForm();
        await renderContacts();
        await loadMessages();
      } catch (e) { toast(e.message, true); }
    });
  }
  async function editContact(id) {
    const c = contacts.find((x) => x.id === id);
    if (!c) return;
    $("contact-id").value = c.id;
    $("contact-name").value = c.name;
    $("contact-phone").value = c.phone;
    $("contact-note").value = c.note || "";
    $("contact-cancel").classList.remove("hidden");
    $("contact-form").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // notify settings
  async function loadNotifyTypes() {
    if (notifyTypes) return notifyTypes;
    notifyTypes = await call("GET", "/notify-configs/types");
    return notifyTypes;
  }
  function notifyFieldHtml(f) {
    const req = f.required ? " required" : "";
    const attrs = `name="np_${f.key}" placeholder="${esc(POO(f.placeholders))}" autocomplete="off"`;
    if (f.type === "select") {
      const opts = (f.options || []).map((o) => `<option value="${esc(o.value)}">${esc(POO(o.labels))}</option>`).join("");
      return `<label>${esc(POO(f.labels))}<select ${attrs}${req}>${opts}</select></label>`;
    }
    const type = f.type === "password" ? "password" : "text";
    return `<label>${esc(POO(f.labels))}<input type="${type}" ${attrs}${req}></label>`;
  }
  async function renderNotifyFields() {
    const type = $("notify-type").value;
    const types = await loadNotifyTypes();
    $("notify-params").innerHTML = types[type]
      ? types[type].fields.map(notifyFieldHtml).join("") : "";
  }
  async function loadNotifyConfigs() {
    try {
      const rows = await call("GET", "/notify-configs");
      $("notify-table").querySelector("tbody").innerHTML = rows.map((r) => `
        <tr>
          <td>${esc(r.name)}</td>
          <td>${esc(POO(r.type_label))}</td>
          <td>${esc([r.match_from, r.match_contains].filter(Boolean).join(" / ") || "—")}</td>
          <td><code>${esc(r.target || r.type)}</code></td>
          <td>${r.enabled ? `<span class="tag ok">${T("notify.enabled.tag")}</span>` : `<span class="tag warn">${T("notify.disabled.tag")}</span>`}</td>
          <td>
            <button class="btn small" data-edit-notify="${r.id}">${T("notify.edit")}</button>
            <button class="btn small" data-test-notify="${r.id}">${T("notify.test")}</button>
            <button class="btn small danger" data-del-notify="${r.id}">${T("notify.ops.delete")}</button>
          </td>
        </tr>`).join("") || `<tr><td colspan="6" class="hint">${T("notify.empty")}</td></tr>`;
    } catch (e) { toast(e.message, true); }
  }
  async function addNotifyConfig(ev) {
    ev.preventDefault();
    const cid = $("notify-id").value;
    const params = {};
    document.querySelectorAll("#notify-params [name^=np_]").forEach((el) => {
      params[el.name.slice(3)] = el.value.trim();
    });
    const body = {
      name: $("notify-name").value.trim(),
      type: $("notify-type").value,
      enabled: $("notify-enabled").checked,
      match_from: $("notify-from").value.trim(),
      match_contains: $("notify-contains").value.trim(),
      params,
    };
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        if (cid) {
          await call("PUT", "/notify-configs/" + cid, body);
          toast(T("notify.updated"));
        } else {
          await call("POST", "/notify-configs", body);
          toast(T("notify.added"));
        }
        resetNotifyForm();
        loadNotifyConfigs();
      } catch (e) { toast(e.message, true); }
    });
  }
  function resetNotifyForm() {
    $("notify-form").reset();
    $("notify-id").value = "";
    $("notify-enabled").checked = true;
    $("notify-cancel-edit").classList.add("hidden");
    renderNotifyFields();
  }
  async function editNotifyConfig(id) {
    try {
      const rows = await call("GET", "/notify-configs");
      const r = rows.find((x) => x.id === id);
      if (!r) return;
      $("notify-id").value = r.id;
      $("notify-name").value = r.name;
      $("notify-type").value = r.type;
      $("notify-from").value = r.match_from;
      $("notify-contains").value = r.match_contains;
      $("notify-enabled").checked = !!r.enabled;
      await renderNotifyFields();
      Object.entries(r.params || {}).forEach(([k, v]) => {
        const el = document.querySelector(`#notify-params [name=np_${k}]`);
        if (el) el.value = v;
      });
      $("notify-cancel-edit").classList.remove("hidden");
      $("notify-form").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { toast(e.message, true); }
  }

  // scheduled tasks
  const taskStatus = {
    never: `<span class="tag">${T("tasks.status.never")}</span>`,
    running: `<span class="tag info">${T("tasks.status.running")}</span>`,
    success: `<span class="tag ok">${T("tasks.status.success")}</span>`,
    failed: `<span class="tag bad">${T("tasks.status.failed")}</span>`,
  };
  async function loadTasks() {
    try {
      const rows = await call("GET", "/tasks");
      $("task-table").querySelector("tbody").innerHTML = rows.map((t) => `
        <tr>
          <td>${esc(t.name)}</td>
          <td>${esc(t.phone)}</td>
          <td>${esc(t.content)}</td>
          <td>${T("tasks.interval.days", t.interval_days)}</td>
          <td>${taskStatus[t.last_status] || esc(t.last_status)}</td>
          <td>${esc(t.last_run_at || T("tasks.status.never"))}</td>
          <td>
            <button class="btn small" data-run-task="${t.id}">${T("tasks.ops.run")}</button>
            <button class="btn small" data-edit-task="${t.id}">${T("tasks.ops.edit")}</button>
            <button class="btn small danger" data-del-task="${t.id}">${T("tasks.ops.delete")}</button>
          </td>
        </tr>`).join("") || `<tr><td colspan="7" class="hint">${T("tasks.empty")}</td></tr>`;
    } catch (e) { toast(e.message, true); }
  }
  async function saveTask(ev) {
    ev.preventDefault();
    const id = $("task-id").value;
    const body = {
      name: $("task-name").value.trim(),
      phone: $("task-phone").value.trim(),
      interval_days: Math.max(1, parseInt($("task-interval").value, 10) || 7),
      content: $("task-content").value.trim(),
      enabled: $("task-enabled").checked,
    };
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        if (id) {
          await call("PUT", "/tasks/" + id, body);
          toast(T("tasks.updated"));
        } else {
          await call("POST", "/tasks", body);
          toast(T("tasks.added"));
        }
        resetTaskForm();
        loadTasks();
      } catch (e) { toast(e.message, true); }
    });
  }
  function resetTaskForm() {
    $("task-form").reset();
    $("task-id").value = "";
    $("task-enabled").checked = true;
    $("task-interval").value = "7";
    $("task-cancel-edit").classList.add("hidden");
  }
  async function editTask(id) {
    try {
      const rows = await call("GET", "/tasks");
      const t = rows.find((x) => x.id === id);
      if (!t) return;
      $("task-id").value = t.id;
      $("task-name").value = t.name;
      $("task-phone").value = t.phone;
      $("task-content").value = t.content;
      $("task-interval").value = t.interval_days;
      $("task-enabled").checked = !!t.enabled;
      $("task-cancel-edit").classList.remove("hidden");
      $("task-form").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { toast(e.message, true); }
  }
  async function runTaskNow(id) {
    try {
      await call("POST", "/tasks/" + id + "/trigger");
      toast(T("msg.triggered"));
      loadTasks();
    } catch (e) { toast(e.message, true); }
  }

  // API keys
  async function loadKeys() {
    try {
      const rows = await call("GET", "/keys");
      $("key-table").querySelector("tbody").innerHTML = rows.map((k) => `
        <tr>
          <td>${esc(k.name)}</td>
          <td><code>${esc(k.key_prefix)}…</code></td>
          <td>${k.active ? '<span class="tag ok">' + T("keys.enabled.tag") + "</span>" : '<span class="tag bad">' + T("keys.disabled.tag") + '</span>'}</td>
          <td>${esc(k.created_at || "—")}</td>
          <td>${esc(k.last_used || T("keys.last.never"))}</td>
          <td>${k.active ? `<button class="btn small danger" data-revoke-key="${k.id}">${T("keys.revoke")}</button>` : `<button class="btn small" data-enable-key="${k.id}">${T("keys.enable")}</button>`} <button class="btn small danger" data-del-key="${k.id}">${T("keys.delete")}</button></td>
        </tr>`).join("") || `<tr><td colspan="6" class="hint">${T("keys.empty")}</td></tr>`;
      $("api-base-url").textContent = location.origin + "/api";
      $("api-example").textContent =
`# Send an SMS via API key
curl -X POST ${location.origin}/api/messages/send \\
  -H "Authorization: Bearer <your-key>" \\
  -H "Content-Type: application/json" \\
  -d '{"to":"10086","content":"hello"}'

# List messages (optional ?direction=in|out&q=keyword)
curl "${location.origin}/api/messages?direction=out&page_size=10" \\
  -H "Authorization: Bearer <your-key>"

# Device status
curl ${location.origin}/api/device/status -H "Authorization: Bearer <your-key>"`;
    } catch (e) { toast(e.message, true); }
  }

  async function createKey(ev) {
    ev.preventDefault();
    const btn = ev.target.querySelector("button[type=submit]");
    await busy(btn, async () => {
      try {
        const k = await call("POST", "/keys", { name: $("key-name").value.trim() });
        $("new-key").textContent = k.key;
        $("new-key-box").classList.remove("hidden");
        $("key-name").value = "";
        loadKeys();
      } catch (e) { toast(e.message, true); }
    });
  }

  // forward logs
  async function loadLogs() {
    try {
      const d = await call("GET", "/forward-logs?limit=100");
      const labels = { dingtalk: T("logs.ch.dingtalk"), wecom: T("logs.ch.wecom"), feishu: T("logs.ch.feishu"), telegram: T("logs.ch.telegram"), email: T("logs.ch.email"), webhook: T("logs.ch.webhook"), apprise: T("logs.ch.apprise"), call: T("logs.ch.call") };
      $("log-table").querySelector("tbody").innerHTML = d.items.map((l) => `
        <tr>
          <td>${l.id}</td>
          <td>${esc(l.sender || "—")}</td>
          <td>${esc(l.content || "—")}</td>
          <td>${esc(labels[l.channel] || l.channel || "—")}</td>
          <td>${esc(l.rule_name || "—")}</td>
          <td><code>${esc(l.webhook_url || "—")}</code></td>
          <td>${l.success ? `<span class="tag ok">${T("logs.ok")}</span>` : `<span class="tag bad">${T("logs.fail")} ${esc(l.status_code || "")}</span>`}</td>
          <td title="${esc(l.error || "")}">${esc(l.error ? l.error.slice(0, 40) : "—")}</td>
          <td>${esc(l.created_at)}</td>
        </tr>`).join("") || `<tr><td colspan="9" class="hint">${T("logs.empty")}</td></tr>`;
    } catch (e) { toast(e.message, true); }
  }

  // event binding
  function bind() {
    $("setup-form").addEventListener("submit", setup);
    $("login-form").addEventListener("submit", login);
    $("logout").addEventListener("click", logout);
    $("change-password-btn").addEventListener("click", openPwd);
    $("pwd-close").addEventListener("click", closePwd);
    $("pwd-form").addEventListener("submit", changePassword);

    document.querySelectorAll(".nav-btn").forEach((b) =>
      b.addEventListener("click", () => switchView(b.dataset.view)));
    $("refresh-status").addEventListener("click", () => loadStatus());
    $("reconnect-device").addEventListener("click", reconnectDevice);
    $("check-sms-capable").addEventListener("click", checkSmsCapable);
    $("chat-back").addEventListener("click", () => document.querySelector(".chat-wrap").classList.remove("chat-open"));
    $("refresh-msgs").addEventListener("click", () => loadMessages());
    $("refresh-logs").addEventListener("click", () => loadLogs());
    $("msg-search").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(handleSearch, 300);
    });
    $("lang-toggle").addEventListener("click", toggleLang);

    $("send-form").addEventListener("submit", sendSms);
    $("compose-btn").addEventListener("click", openCompose);
    $("compose-close").addEventListener("click", closeCompose);
    $("new-sms-form").addEventListener("submit", sendNewSms);
    $("new-sms-content").addEventListener("input", updateNewSmsChar);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeCompose();
        closePwd();
      }
    });
    $("contacts-toggle").addEventListener("click", () => toggleContacts());
    $("contacts-close").addEventListener("click", () => toggleContacts(false));
    $("contact-form").addEventListener("submit", saveContact);
    $("contact-cancel").addEventListener("click", resetContactForm);
    $("contact-list").addEventListener("click", async (e) => {
      const ed = e.target.closest("[data-edit-contact]");
      const dl = e.target.closest("[data-del-contact]");
      const oc = e.target.closest("[data-open-contact]");
      if (ed) { editContact(Number(ed.dataset.editContact)); }
      else if (dl) {
        if (!confirm(T("contacts.delete.confirm"))) return;
        const id = Number(dl.dataset.delContact);
        call("DELETE", "/contacts/" + id).then(() => {
          toast(T("contacts.deleted"));
          renderContacts();
          loadMessages();
        }).catch((er) => toast(er.message, true));
      } else if (oc) {
        toggleContacts(false);
        const ocPhone = oc.dataset.openContact;
        const mainEl = document.querySelector("main");
        if (mainEl) mainEl.scrollTo({ top: 0, behavior: "smooth" });
        window.scrollTo({ top: 0, behavior: "smooth" });
        await openConversation(ocPhone);
      }
    });
    $("notify-form").addEventListener("submit", addNotifyConfig);
    $("notify-type").addEventListener("change", renderNotifyFields);
    $("notify-cancel-edit").addEventListener("click", resetNotifyForm);
    $("task-form").addEventListener("submit", saveTask);
    $("task-cancel-edit").addEventListener("click", resetTaskForm);
    $("key-form").addEventListener("submit", createKey);

    document.addEventListener("click", (e) => {
      const cp = e.target.closest("[data-copy]");
      const dn = e.target.closest("[data-del-notify]");
      const tn = e.target.closest("[data-test-notify]");
      const en = e.target.closest("[data-edit-notify]");
      const dk = e.target.closest("[data-del-key]");
      const ek = e.target.closest("[data-enable-key]");
      const rk = e.target.closest("[data-revoke-key]");
      const rn = e.target.closest("[data-run-task]");
      const en2 = e.target.closest("[data-edit-task]");
      const dt = e.target.closest("[data-del-task]");
      const cv = e.target.closest(".conv-item");
      const lm = e.target.closest("[data-load-more]");
      if (cp) {
        const src = document.getElementById(cp.dataset.copy);
        if (src) copyText(src.textContent.trim()).then((ok) => toast(ok ? T("keys.copy.ok") : T("keys.copy.fail"), !ok));
      } else if (dn) {
        if (!confirm(T("notify.delete.confirm"))) return;
        call("DELETE", "/notify-configs/" + dn.dataset.delNotify).then(loadNotifyConfigs).catch((er) => toast(er.message, true));
      } else if (tn) {
        busy(tn, () =>
          call("POST", "/notify-configs/" + tn.dataset.testNotify + "/test").then((r) => {
            toast(T(r.ok ? "notify.test.ok" : "notify.test.fail") + (r.error ? ": " + r.error : ""), !r.ok);
          }).catch((er) => toast(er.message, true)));
      } else if (en) {
        editNotifyConfig(Number(en.dataset.editNotify));
      } else if (rk) {
        if (!confirm(T("keys.revoke.confirm"))) return;
        call("POST", "/keys/" + rk.dataset.revokeKey + "/revoke").then(loadKeys).catch((er) => toast(er.message, true));
      } else if (dk) {
        if (!confirm(T("keys.delete.confirm"))) return;
        call("DELETE", "/keys/" + dk.dataset.delKey).then(loadKeys).catch((er) => toast(er.message, true));
      } else if (ek) {
        call("POST", "/keys/" + ek.dataset.enableKey + "/enable").then(loadKeys).catch((er) => toast(er.message, true));
      } else if (dt) {
        if (!confirm(T("tasks.delete.confirm"))) return;
        call("DELETE", "/tasks/" + dt.dataset.delTask).then(loadTasks).catch((er) => toast(er.message, true));
      } else if (rn) {
        runTaskNow(Number(rn.dataset.runTask));
      } else if (en2) {
        editTask(Number(en2.dataset.editTask));
      } else if (cv) {
        openConversation(cv.dataset.conv);
      } else if (lm) {
        loadOlder();
      }
    });

    $("copy-key").addEventListener("click", async () => {
      copyText($("new-key").textContent).then((ok) => {
        toast(ok ? T("keys.copy.ok") : T("keys.copy.fail"), !ok);
      });
    });

    $("login-password").addEventListener("keydown", (e) => { if (e.key === "Enter") $("login-form").requestSubmit(); });
    $("setup-password2").addEventListener("keydown", (e) => { if (e.key === "Enter") $("setup-form").requestSubmit(); });
  }

  bind();
  renderLangToggle();
  updateNewSmsChar();
  loadVersion();
  if (token) showApp(); else checkSetup();
})();