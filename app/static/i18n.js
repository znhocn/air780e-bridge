/* Air780 短信桥 前端双语层
   - 语言判定：浏览器语言以 zh 开头 -> 中文；否则默认英文(en)
   - i18n.t(key[, vars])        : 取当前语言文案，支持 {0} {1} 占位
   - i18n.pair(v)               : 取后端返回双语对象 {zh,en} 中当前语言的值（v 也可为普通字符串/空）
   - i18n.pairOpt(o)            : 取后端选项/表单字段双语对象 {zh,en} 的值（同 pair，便于语义区分）
   - i18n.esc(s)                : HTML 转义
   - i18n.applyI18n()           : 应用静态 DOM（data-i18n / data-i18n-ph / data-i18n-title / 页面标题）
*/
(function () {
  const navLangs = [navigator.language, ...(navigator.languages || [])]
    .map((s) => String(s || "").toLowerCase());
  const lang = navLangs.some((l) => l.startsWith("zh")) ? "zh" : "en";

  const DICTS = {
    en: {
      "brand.zh": "Air780 SMS Bridge",
      "brand.sub": "SMS Forwarding Bridge",
      "status.title": "Device Status",
      "status.refresh": "Refresh",
      "status.connected": "Connected",
      "status.disconnected": "Not Connected",
      "status.online": "Online",
      "status.offline": "Offline",
      "status.reg.state": "Network registration",
      "status.not.checked": "Not checked yet",
      "status.signal": "Signal",
      "status.signal.none": "No signal",
      "status.csq.rssi": "approx. {0} dBm",
      "status.total": "Total / Incoming / Pending Out",
      "status.checked": "Checked at",
      "status.reg.0": "Not registered",
      "status.reg.1": "Home network registered",
      "status.reg.2": "Searching...",
      "status.reg.3": "Registration denied",
      "status.reg.4": "Unknown",
      "status.reg.5": "Roaming registered",
      "status.reg.none": "Unknown",
      "status.card.conn": "Connection",
      "status.card.port": "Serial Port",
      "status.card.model": "Module Model",
      "status.card.imei": "IMEI",
      "status.card.operator": "Operator",
      "status.card.reg": "Network Registration",
      "status.card.signal": "Signal (CSQ)",
      "status.card.checked": "Last Checked",
      "status.card.total": "Total / In / Out(Pending)",
      "status.refreshed": "Device status refreshed",

      "msg.send": "Send SMS",
      "msg.to": "Recipient number",
      "msg.to.ph": "e.g. 10086",
      "msg.content": "Content",
      "msg.content.ph": "SMS content",
      "msg.send.submit": "Send",
      "msg.queue.ok": "SMS queued (id #{0})",
      "msg.dir.in": "Inbox",
      "msg.dir.out": "Outbox",
      "msg.refresh": "Refresh",
      "msg.dir.in.short": "IN",
      "msg.dir.out.short": "OUT",
      "msg.total": "{0} total",
      "msg.filtered": " (filtered by content)",
      "msg.status.stored": "Received",
      "msg.status.sent": "Sent",
      "msg.status.failed": "Failed",
      "msg.table.empty": "No SMS yet",
      "msg.each.empty": "No SMS",
      "msg.send.hint": "Test: default sends to 10086 (SIM card required)",
      "msg.table.id": "ID",
      "msg.table.dir": "Dir",
      "msg.table.num": "Number",
      "msg.table.content": "Content",
      "msg.table.status": "Status",
      "msg.table.time": "Time",

      "notify.title": "Notification Settings",
      "notify.name": "Name",
      "notify.channel": "Channel",
      "notify.match_from": "From number contains",
      "notify.match_contains": "Content contains",
      "notify.match_contains.ph": "leave empty for any",
      "notify.enabled": "Enabled",
      "notify.save": "Save config",
      "notify.cancel": "Cancel edit",
      "notify.updated": "Notification config updated",
      "notify.added": "Notification config added",
      "notify.delete.confirm": "Delete this notification config?",
      "notify.test.ok": "Test sent successfully",
      "notify.test.fail": "Test failed",
      "notify.table.id": "ID",
      "notify.table.name": "Name",
      "notify.table.channel": "Channel",
      "notify.table.match": "Match rule",
      "notify.table.target": "Target",
      "notify.table.status": "Status",
      "notify.table.ops": "Actions",
      "notify.enabled.tag": "Enabled",
      "notify.edit": "Edit",
      "notify.test": "Test",
      "msg.enqueued": "SMS enqueued (#{0})",
      "keys.last.never": "never used",
      "notify.disabled.tag": "Disabled",
      "notify.empty": "No notify config: inbound SMS is only stored, not pushed",
      "notify.ops.edit": "Edit",
      "notify.ops.test": "Test",
      "notify.ops.delete": "Delete",

      "keys.title": "API Keys",
      "keys.last.never": "never used",
      "notify.test": "Test",
      "notify.edit": "Edit",
      "msg.enqueued": "SMS enqueued (#{0})",
      "keys.info": "API Info",
      "keys.base": "Base URL",
      "keys.auth": "Authentication: send header Authorization: Bearer <your key>",
      "keys.col.id": "ID",
      "keys.col.name": "Name",
      "keys.col.prefix": "Key prefix",
      "keys.col.status": "Status",
      "keys.col.created": "Created at",
      "keys.col.last": "Last used",
      "keys.col.ops": "Actions",
      "keys.enabled.tag": "Active",
      "keys.disabled.tag": "Revoked",
      "keys.empty": "No API keys yet",
      "keys.revoke": "Revoke",
      "keys.enable": "Restore",
      "keys.delete.confirm": "Revoke this API key?",
      "keys.copy": "Copied",
      "keys.never": "never used",

      "logs.title": "Forward Logs",
      "logs.refresh": "Refresh",
      "logs.col.id": "ID",
      "logs.col.msg": "Message ID",
      "logs.col.sender": "Sender",
      "logs.col.content": "Content",
      "logs.col.channel": "Channel",
      "logs.col.rule": "Rule",
      "logs.col.target": "Target",
      "logs.col.result": "Result",
      "logs.col.error": "Error",
      "logs.col.time": "Time",
      "logs.ok": "OK",
      "logs.fail": "Fail",
      "logs.empty": "No logs yet",
      "logs.ch.dingtalk": "DingTalk",
      "logs.ch.wecom": "WeCom",
      "logs.ch.feishu": "Feishu",
      "logs.ch.email": "Email",
      "logs.ch.webhook": "Webhook",

      "nav.status": "Device Status",
      "nav.messages": "SMS",
      "nav.notify": "Notifications",
      "nav.keys": "API Keys",
      "nav.logs": "Logs",
      "logout": "Logout",
      "login.button": "Login",
      "login.username": "Username",
      "login.password": "Password",
      "login.expired": "Session expired, please login again",
      "setup.username": "Username",
      "setup.password": "Password (min 8 chars)",
      "setup.password2": "Confirm password",
      "setup.mismatch": "Passwords do not match",
    },
    zh: {
      "brand.zh": "Air780 短信桥",
      "brand.sub": "短信转发桥",
      "status.title": "设备状态",
      "status.refresh": "刷新",
      "status.connected": "已连接",
      "status.disconnected": "未连接",
      "status.online": "在线",
      "status.offline": "离线",
      "status.reg.state": "网络注册",
      "status.not.checked": "尚未检测",
      "status.signal": "信号",
      "status.signal.none": "无信号",
      "status.csq.rssi": "约 {0} dBm",
      "status.total": "短信总数 / 收 / 待发",
      "status.checked": "设备检测时间",
      "status.reg.0": "未注册",
      "status.reg.1": "已注册本地",
      "status.reg.2": "搜索中",
      "status.reg.3": "注册被拒",
      "status.reg.4": "未知",
      "status.reg.5": "已注册漫游",
      "status.reg.none": "未知",
      "status.refreshed": "设备状态已刷新",

      "msg.send": "发送短信",
      "msg.to": "接收号码",
      "msg.to.ph": "如 10086",
      "msg.content": "内容",
      "msg.content.ph": "短信内容",
      "msg.send.submit": "发送",
      "msg.queue.ok": "短信已入队（#{0}）",
      "msg.dir.in": "收件箱",
      "msg.dir.out": "发件箱",
      "msg.refresh": "刷新",
      "msg.dir.in.short": "收",
      "msg.dir.out.short": "发",
      "msg.total": "共 {0} 条",
      "msg.filtered": "（已按内容过滤）",
      "msg.status.stored": "已收",
      "msg.status.sent": "已发",
      "msg.status.failed": "失败",
      "msg.table.empty": "暂无短信",
      "msg.each.empty": "暂无短信",
      "msg.send.hint": "测试：默认发送到 10086（需插入 SIM 卡）",
      "msg.table.id": "ID",
      "msg.table.dir": "方向",
      "msg.table.num": "号码",
      "msg.table.content": "内容",
      "msg.table.status": "状态",
      "msg.table.time": "时间",

      "notify.title": "通知设置",
      "notify.name": "名称",
      "notify.channel": "渠道",
      "notify.match_from": "来自号码包含",
      "notify.match_contains": "内容包含",
      "notify.match_contains.ph": "留空则不限制",
      "notify.enabled": "启用",
      "notify.save": "保存配置",
      "notify.cancel": "取消编辑",
      "notify.updated": "通知配置已更新",
      "notify.added": "通知配置已添加",
      "notify.delete.confirm": "确认删除该通知配置？",
      "notify.test.ok": "测试发送成功",
      "notify.test.fail": "测试发送失败",
      "notify.table.id": "ID",
      "notify.table.name": "名称",
      "notify.table.channel": "渠道",
      "notify.table.match": "筛选规则",
      "notify.table.target": "目标",
      "notify.table.status": "状态",
      "notify.table.ops": "操作",
      "notify.enabled.tag": "启用",
      "notify.disabled.tag": "停用",
      "notify.empty": "暂无通知配置：收到短信后默认仅入库，不推送",
      "notify.ops.edit": "编辑",
      "notify.ops.test": "测试",
      "notify.ops.delete": "删除",

      "keys.title": "API 密钥",
      "keys.last.never": "从未使用",
      "notify.test": "测试",
      "notify.edit": "编辑",
      "msg.enqueued": "短信已入队（#{0}）",
      "keys.info": "API 信息",
      "keys.base": "基础地址",
      "keys.auth": "认证方式：请求头携带 Authorization: Bearer <你的密钥>",
      "keys.col.id": "ID",
      "keys.col.name": "名称",
      "keys.col.prefix": "密钥前缀",
      "keys.col.status": "状态",
      "keys.col.created": "创建时间",
      "keys.col.last": "最近使用",
      "keys.col.ops": "操作",
      "keys.enabled.tag": "启用",
      "keys.disabled.tag": "已吊销",
      "keys.empty": "暂无密钥",
      "keys.revoke": "吊销",
      "keys.enable": "恢复",
      "keys.delete.confirm": "确认吊销该密钥？",
      "keys.copy": "已复制",
      "keys.never": "从未使用",
      "keys.last.never": "从未使用",


      "logs.title": "转发日志",
      "logs.refresh": "刷新",
      "logs.col.id": "ID",
      "logs.col.msg": "短信ID",
      "logs.col.sender": "发送号码",
      "logs.col.content": "内容",
      "logs.col.channel": "渠道",
      "logs.col.rule": "配置",
      "logs.col.target": "目标",
      "logs.col.result": "结果",
      "logs.col.error": "错误",
      "logs.col.time": "时间",
      "logs.ok": "成功",
      "logs.fail": "失败",
      "logs.empty": "暂无日志",
      "logs.ch.dingtalk": "钉钉",
      "logs.ch.wecom": "企业微信",
      "logs.ch.feishu": "飞书",
      "logs.ch.email": "Email",
      "logs.ch.webhook": "Webhook",

      "nav.status": "设备状态",
      "nav.messages": "短信收发",
      "nav.notify": "通知设置",
      "nav.keys": "API 密钥",
      "nav.logs": "转发日志",
      "logout": "退出登录",
      "login.button": "登录",
      "login.username": "用户名",
      "login.password": "密码",
      "login.expired": "登录已过期，请重新登录",
      "setup.username": "用户名",
      "setup.password": "密码（至少 8 位）",
      "setup.password2": "确认密码",
      "setup.mismatch": "两次输入的密码不一致",
    },
  };

  function dict() { return DICTS[lang] || DICTS.en; }

  function applyStatic() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n-title]").forEach((el) => {
      const v = dict()[el.getAttribute("data-i18n-title")];
      if (v != null) document.title = v;
    });
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const v = dict()[el.getAttribute("data-i18n")];
      if (v == null) return;
      // 保留内部 input/select/textarea，只替换 label 文本节点
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
        acceptNode: (n) => (n.parentElement === el ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
      });
      let n;
      if ((n = walker.nextNode())) n.textContent = v;
      else el.textContent = v;
    });
    document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
      const v = dict()[el.getAttribute("data-i18n-ph")];
      if (v != null) el.setAttribute("placeholder", v);
    });
  }

  window.i18n = {
    lang,
    t(key) {
      const d = dict();
      let s = key in d ? d[key] : key;
      for (let i = 0; i < arguments.length - 1; i++) {
        s = s.replace(new RegExp("\\{" + i + "\\}", "g"), arguments[i + 1]);
      }
      return s;
    },
    pair(v) {
      if (v == null) return "";
      if (typeof v === "string") return v;
      if (lang === "zh" && v.zh) return v.zh;
      return v.en || v.zh || "";
    },
    pairOpt(v) { return window.i18n.pair(v); },
    esc(s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    },
    applyI18n: applyStatic,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyStatic);
  } else {
    applyStatic();
  }
})();
