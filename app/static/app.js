/* Air780 短信桥 前端逻辑 */
(function () {
  const API = "/api";
  /* 双语胶水：前端统一走本文件 i18n 兼容层 ——
    T() : i18n.t() 取当前语言文案（动态文案）；
    PO() : i18n.pair() —— 后端返回的 {zh,en} 对象取当前语言；
    POO(): i18n.pairOpt() —— 可缺省返回值；
    esc  : HTML 转义（委托 i18n.esc，若 i18n 未加载则回退本文件实现）。 */
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
  let token = localStorage.getItem("air780_token") || "";
  let curDir = "in";
  let curMsgFilter = "";
  let statusTimer = null;
  let notifyTypes = null;

  const $ = (id) => document.getElementById(id);

  // ---------- 工具 ----------
  function toast(msg, isErr) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.toggle("error", !!isErr);
    el.classList.remove("hidden");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.add("hidden"), 3200);
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
      throw new Error("登录已过期，请重新登录");
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText || String(r.status));
    return data;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---------- 登录 / 首次部署 / 退出 ----------
  function showApp() {
    $("login-view").classList.add("hidden");
    $("app-view").classList.remove("hidden");
    loadAll();
  }
  function logout() {
    token = "";
    localStorage.removeItem("air780_token");
    $("app-view").classList.add("hidden");
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
    } catch (e) { /* 保持默认登录表单 */ }
    $("login-form").classList.remove("hidden");
    $("setup-form").classList.add("hidden");
  }
  async function login(ev) {
    ev.preventDefault();
    $("login-error").classList.add("hidden");
    try {
      const res = await call("POST", "/auth/login", {
        username: $("login-username").value.trim(),
        password: $("login-password").value,
      });
      token = res.token;
      localStorage.setItem("air780_token", token);
      showApp();
    } catch (e) {
      $("login-error").textContent = e.message;
      $("login-error").classList.remove("hidden");
    }
  }
  async function setup(ev) {
    ev.preventDefault();
    $("setup-error").classList.add("hidden");
    const p = $("setup-password").value;
    if (p !== $("setup-password2").value) {
      $("setup-error").textContent = "两次输入的密码不一致";
      $("setup-error").classList.remove("hidden");
      return;
    }
    try {
      const res = await call("POST", "/auth/setup", {
        username: $("setup-username").value.trim(),
        password: p,
      });
      token = res.token;
      localStorage.setItem("air780_token", token);
      showApp();
    } catch (e) {
      $("setup-error").textContent = e.message;
      $("setup-error").classList.remove("hidden");
    }
  }

  // ---------- 各视图加载 ----------
  async function loadAll() {
    if (statusTimer) clearInterval(statusTimer);
    switchView(currentView());
    setInterval(async () => {
      if (token && !$("app-view").classList.contains("hidden")) {
        try { await Promise.all([loadStatus(true), loadMessages(true)]); } catch (e) {}
      }
    }, 6000);
  }

  function currentView() {
    return document.querySelector(".nav-btn.active").dataset.view;
  }
  function switchView(name) {
    ["status", "messages", "notify", "keys", "logs"].forEach((v) => {
      $("view-" + v).classList.toggle("hidden", v !== name);
    });
    document.querySelectorAll(".nav-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === name);
    });
    ({ status: loadStatus,
       messages: loadMessages,
       notify: loadNotifyConfigs,
       keys: loadKeys,
       logs: loadLogs })[name]();
  }

  // 设备状态
  async function loadStatus(silent) {
    try {
      const s = await call("GET", "/device/status");
      const grid = $("status-grid");
      const csq = s.csq_rssi;
      const bars = csq == null || csq >= 99 ? 0 : Math.max(1, Math.round(csq / 31 * 5));
      const reg = { "0": T("status.reg.0"), "1": T("status.reg.1"), "2": T("status.reg.2"), "3": T("status.reg.3"), "4": T("status.reg.4"), "5": T("status.reg.5") }[s.reg_state] || (s.reg_state ? T("status.reg.none") : "—");
      grid.innerHTML = `
        <div class="card"><span>${T("status.card.conn")}</span><b>${s.connected ? T("status.connected") : T("status.disconnected")}${s.connected ? `<br><span class="tag ok">${T("status.online")}</span>` : `<br><span class="tag warn">${T("status.offline")}</span>`}</b></div>
        <div class="card"><span>${T("status.card.port")}</span><b>${esc(s.port || "—")}</b></div>
        <div class="card"><span>${T("status.card.model")}</span><b>${esc(s.model || "—")}</b></div>
        <div class="card"><span>${T("status.card.imei")}</span><b>${esc(s.imei || "—")}</b></div>
        <div class="card"><span>${T("status.card.operator")}</span><b>${esc(s.operator || "—")}</b></div>
        <div class="card"><span>${T("status.card.reg")}</span><b>${reg}</b></div>
        <div class="card"><span>${T("status.card.signal")}</span><b>${csq == null || csq >= 99 ? T("status.signal.none") : csq + ` <small>(${T("status.csq.rssi", s.rssi_dbm ?? "?")})</small>`}</b></div>
        <div class="card"><span>${T("status.card.checked")}</span><b>${esc(s.checked_at || "—")}</b></div>
        <div class="card"><span>${T("status.card.total")}</span><b>${s.messages_total || 0} / ${s.messages_in || 0} / ${s.messages_out_pending || 0}</b></div>`;
      $("signal-bar").innerHTML = bars ? `<div style="width:${bars * 20}%"></div>` : `<div title="${T("status.signal.none")}"></div>`;
      if (!silent) toast(T("status.refreshed"));
    } catch (e) { if (!silent) toast(e.message, true); }
  }

  // 短信
  async function loadMessages(silent) {
    try {
      const f = curMsgFilter ? "&q=" + encodeURIComponent(curMsgFilter) : "";
      const d = await call("GET", `/messages?direction=${curDir}&page_size=100${f}`);
      const rows = d.items.map((m) => {
        const num = esc(m.direction === "in" ? m.sender : m.receiver);
        const tag = m.direction === "in"
          ? m.status === "stored" ? `<span class="tag ok">${T("msg.status.stored")}</span>` : `<span class="tag info">${esc(m.status)}</span>`
          : m.status === "sent" ? `<span class="tag ok">${T("msg.status.sent")}</span>`
          : m.status === "failed" ? `<span class="tag bad">${T("msg.status.failed")}</span>`
          : `<span class="tag warn">${esc(m.status)}</span>`;
        return `<tr><td>${m.id}</td><td>${m.direction === "in" ? T("msg.dir.in") : T("msg.dir.out")}</td><td>${num}</td><td>${esc(m.content)}</td><td>${tag}</td><td>${esc(m.created_at)}</td></tr>`;
      }).join("");
      const tb = $("msg-table").querySelector("tbody");
      tb.innerHTML = rows || `<tr><td colspan="6" class="hint">${T("msg.table.empty")}</td></tr>`;
      $("msg-filter").textContent = T("msg.total", d.total) + (curMsgFilter ? `（${T("msg.filtered")}）` : "");
    } catch (e) { if (!silent) toast(e.message, true); }
  }

  async function sendSms(ev) {
    ev.preventDefault();
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const res = await call("POST", "/messages/send", { to: $("send-to").value.trim(), content: $("send-content").value });
      toast(POO2("msg.enqueued", "#" + res.id));
      $("send-content").value = "";
      await loadMessages();
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; }
  }

  // 通知设置
  async function loadNotifyTypes() {
    if (notifyTypes) return notifyTypes;
    notifyTypes = await call("GET", "/notify-configs/types");
    return notifyTypes;
  }
  function notifyFieldHtml(f) {
    const req = f.required ? " required" : "";
    const attrs = `name="np_${f.key}" placeholder="${esc(POO(f.placeholder))}"`;
    if (f.type === "select") {
      const opts = (f.options || []).map((o) => `<option value="${esc(o.value)}">${esc(POO(o.label))}</option>`).join("");
      return `<label>${esc(POO(f.label))}<select ${attrs}${req}>${opts}</select></label>`;
    }
    const type = f.type === "password" ? "password" : "text";
    return `<label>${esc(POO(f.label))}<input type="${type}" ${attrs}${req}></label>`;
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
          <td>${r.id}</td>
          <td>${esc(r.name)}</td>
          <td>${esc(POO(r.type_label))}</td>
          <td>${esc([r.match_from, r.match_contains].filter(Boolean).join(" / ") || "—")}</td>
          <td><code>${esc(r.target || r.type)}</code></td>
          <td>${r.enabled ? `<span class="tag ok">${T("notify.enabled.tag")}</span>` : `<span class="tag warn">${T("notify.disabled.tag")}</span>`}</td>
          <td>
            <button class="btn small" data-edit-notify="${r.id}">${T("notify.edit")}</button>
            <button class="btn small" data-test-notify="${r.id}">${T("notify.test")}</button>
            <button class="btn small danger" data-del-notify="${r.id}">删除</button>
          </td>
        </tr>`).join("") || `<tr><td colspan="7" class="hint">暂无通知配置：收到短信后默认仅入库，不推送</td></tr>`;
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
    try {
      if (cid) {
        await call("PUT", "/notify-configs/" + cid, body);
        toast("通知配置已更新");
      } else {
        await call("POST", "/notify-configs", body);
        toast("通知配置已添加");
      }
      resetNotifyForm();
      loadNotifyConfigs();
    } catch (e) { toast(e.message, true); }
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

  // API 密钥
  async function loadKeys() {
    try {
      const rows = await call("GET", "/keys");
      $("key-table").querySelector("tbody").innerHTML = rows.map((k) => `
        <tr>
          <td>${k.id}</td>
          <td>${esc(k.name)}</td>
          <td><code>${esc(k.key_prefix)}…</code></td>
          <td>${k.active ? '<span class="tag ok">' + T("keys.enabled.tag") + "</span>" : '<span class="tag bad">' + T("keys.disabled.tag") + '</span>'}</td>
          <td>${esc(k.last_used || T("keys.last.never"))}</td>
          <td>${k.active ? `<button class="btn small danger" data-del-key="${k.id}">${T("keys.revoke")}</button>` : `<button class="btn small" data-enable-key="${k.id}">${T("keys.enable")}</button>`}</td>
        </tr>`).join("") || `<tr><td colspan="7" class="hint">暂无密钥</td></tr>`;
      $("api-base-url").textContent = location.origin + "/api";
      $("api-example").textContent =
`# 发送短信
curl -X POST ${location.origin}/api/messages/send \\
  -H "Authorization: Bearer <你的密钥>" \\
  -H "Content-Type: application/json" \\
  -d '{"to":"10086","content":"你好"}'

# 查询短信列表（可 ?direction=in|out&q=关键词）
curl "${location.origin}/api/messages?direction=out&page_size=10" \\
  -H "Authorization: Bearer <你的密钥>"

# 设备状态
curl ${location.origin}/api/device/status -H "Authorization: Bearer <你的密钥>"`;
    } catch (e) { toast(e.message, true); }
  }

  async function createKey(ev) {
    ev.preventDefault();
    try {
      const k = await call("POST", "/keys", { name: $("key-name").value.trim() });
      $("new-key").textContent = k.key;
      $("new-key-box").classList.remove("hidden");
      $("key-name").value = "";
      loadKeys();
    } catch (e) { toast(e.message, true); }
  }

  // 转发日志
  async function loadLogs() {
    try {
      const d = await call("GET", "/forward-logs?limit=100");
      const labels = { dingtalk: "钉钉", wecom: "企业微信", feishu: "飞书", email: "Email", webhook: "Webhook" };
      $("log-table").querySelector("tbody").innerHTML = d.items.map((l) => `
        <tr>
          <td>${l.id}</td>
          <td>${l.message_id ?? "—"}</td>
          <td>${esc(l.sender || "—")}</td>
          <td>${esc(l.content || "—")}</td>
          <td>${esc(labels[l.channel] || l.channel || "—")}</td>
          <td>${esc(l.rule_name || "—")}</td>
          <td><code>${esc(l.webhook_url || "—")}</code></td>
          <td>${l.success ? `<span class="tag ok">${T("logs.ok")}</span>` : `<span class="tag bad">${T("logs.fail")} ${esc(l.status_code || "")}</span>`}</td>
          <td title="${esc(l.error || "")}">${esc(l.error ? l.error.slice(0, 40) : "—")}</td>
          <td>${esc(l.created_at)}</td>
        </tr>`).join("") || `<tr><td colspan="10" class="hint">暂无日志</td></tr>`;
    } catch (e) { toast(e.message, true); }
  }

  // 事件绑定
  function bind() {
    $("setup-form").addEventListener("submit", setup);
    $("login-form").addEventListener("submit", login);
    $("logout").addEventListener("click", logout);

    document.querySelectorAll(".nav-btn").forEach((b) =>
      b.addEventListener("click", () => switchView(b.dataset.view)));
    $("refresh-status").addEventListener("click", () => loadStatus());
    $("refresh-msgs").addEventListener("click", () => loadMessages());
    $("refresh-logs").addEventListener("click", () => loadLogs());

    document.querySelectorAll(".msg-tab").forEach((t) =>
      t.addEventListener("click", () => {
        document.querySelectorAll(".msg-tab").forEach((x) => x.classList.remove("active"));
        t.classList.add("active");
        curDir = t.dataset.dir;
        loadMessages();
      }));

    $("send-form").addEventListener("submit", sendSms);
    $("notify-form").addEventListener("submit", addNotifyConfig);
    $("notify-type").addEventListener("change", renderNotifyFields);
    $("notify-cancel-edit").addEventListener("click", resetNotifyForm);
    $("key-form").addEventListener("submit", createKey);

    document.addEventListener("click", (e) => {
      const dn = e.target.closest("[data-del-notify]");
      const tn = e.target.closest("[data-test-notify]");
      const en = e.target.closest("[data-edit-notify]");
      const dk = e.target.closest("[data-del-key]");
      const ek = e.target.closest("[data-enable-key]");
      if (dn) {
        if (!confirm("确认删除该通知配置？")) return;
        call("DELETE", "/notify-configs/" + dn.dataset.delNotify).then(loadNotifyConfigs).catch((er) => toast(er.message, true));
      } else if (tn) {
        call("POST", "/notify-configs/" + tn.dataset.testNotify + "/test").then((r) => {
          toast(`测试${r.ok ? "成功" : "失败"}` + (r.error ? "：" + r.error : ""), !r.ok);
        }).catch((er) => toast(er.message, true));
      } else if (en) {
        editNotifyConfig(Number(en.dataset.editNotify));
      } else if (dk) {
        if (!confirm("确认吊销该密钥？")) return;
        call("DELETE", "/keys/" + dk.dataset.delKey).then(loadKeys).catch((er) => toast(er.message, true));
      } else if (ek) {
        call("POST", "/keys/" + ek.dataset.enableKey + "/enable").then(loadKeys).catch((er) => toast(er.message, true));
      }
    });

    $("copy-key").addEventListener("click", () => {
      navigator.clipboard.writeText($("new-key").textContent).then(() => toast("已复制"));
    });

    $("login-password").addEventListener("keydown", (e) => { if (e.key === "Enter") $("login-form").requestSubmit(); });
    $("setup-password2").addEventListener("keydown", (e) => { if (e.key === "Enter") $("setup-form").requestSubmit(); });
  }

  bind();
  if (token) showApp(); else checkSetup();
})();