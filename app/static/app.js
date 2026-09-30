/* 剧场无线话筒频点排演 前端逻辑（无框架，原生 JS） */
"use strict";

let STATE = null;
let editingMic = null;   // 正在编辑的话筒 id；null 表示新增
let editingRoom = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, method = "GET", body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch (_) { /* ignore */ }
  if (!res.ok || (data && data.ok === false)) {
    const err = new Error((data && data.error) || `请求失败 (${res.status})`);
    err.devices = (data && data.devices) || [];
    throw err;
  }
  return data;
}

// ------------------------------------------------------------------
// 工具
// ------------------------------------------------------------------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
function fmtTime(t) {
  // 整数时间按"小时:分钟"友好展示（能整除 60 时），否则显示原值
  if (Number.isInteger(t) && t >= 0) {
    const h = Math.floor(t / 60), m = t % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }
  return esc(t);
}
function showBanner(kind, html) {
  const el = $(`#${kind}-banner`);
  el.innerHTML = html;
  el.classList.remove("hidden");
}
function clearBanners() {
  $$(".banner").forEach((b) => b.classList.add("hidden"));
}
function flashRows(deviceNames) {
  if (!deviceNames || !deviceNames.length) return;
  const names = new Set(deviceNames);
  $$("#mic-table tbody tr").forEach((tr) => {
    if (names.has(tr.dataset.name)) tr.classList.add("row-error");
  });
}
function clearRowErrors() {
  $$("#mic-table tbody tr.row-error").forEach((tr) => tr.classList.remove("row-error"));
}

// ------------------------------------------------------------------
// 渲染
// ------------------------------------------------------------------
function render() {
  renderRooms();
  renderInterferences();
  renderMics();
  renderResult();
  renderHistory();
}

function renderRooms() {
  const box = $("#room-list");
  if (!STATE.rooms.length) {
    box.innerHTML = `<div class="empty">还没有房间，请先新增。</div>`;
    return;
  }
  box.innerHTML = STATE.rooms.map((r) => `
    <span class="chip">
      ${esc(r.name)}
      <button class="edit" data-room-edit="${r.id}" title="编辑">✎</button>
      <button data-room-del="${r.id}" title="删除">✕</button>
    </span>`).join("");
}

function renderInterferences() {
  const box = $("#inter-list");
  if (!STATE.interferences.length) {
    box.innerHTML = `<div class="empty">暂无互扰关系。</div>`;
    return;
  }
  box.innerHTML = STATE.interferences.map((it) => `
    <div class="pair">
      <span><b>${esc(it.room_a_name)}</b><span class="link">⇄</span><b>${esc(it.room_b_name)}</b></span>
      <button class="danger mini" data-inter-del="${it.id}">删除</button>
    </div>`).join("");
}

function renderMics() {
  const tbody = $("#mic-table tbody");
  if (!STATE.microphones.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">还没有话筒，请新增。</td></tr>`;
    return;
  }
  tbody.innerHTML = STATE.microphones.map((m) => {
    const priCls = m.priority === "high" ? "high" : "normal";
    const priTxt = m.priority === "high" ? "高" : "普通";
    const lock = m.locked_freq != null
      ? `<span class="lock-mark" title="锁定频点">🔒${m.locked_freq}</span>` : "";
    return `
    <tr data-id="${m.id}" data-name="${esc(m.name)}">
      <td>${esc(m.name)}${lock}</td>
      <td>${esc(m.room_name)}</td>
      <td><span class="tag ${priCls}">${priTxt}</span></td>
      <td class="freq-cell">${fmtTime(m.start_time)} – ${fmtTime(m.end_time)}</td>
      <td class="freq-cell">${m.guard_khz}</td>
      <td class="freq-cell">${m.freqs.join(", ")}</td>
      <td>${m.locked_freq != null ? "是" : "—"}</td>
      <td><span class="row-actions">
        <button class="mini ghost" data-mic-edit="${m.id}">编辑</button>
        <button class="mini danger" data-mic-del="${m.id}">删除</button>
      </span></td>
    </tr>`;
  }).join("");
}

function renderResult() {
  const s = STATE.active_scheme;
  const summary = $("#summary");
  const atbody = $("#assigned-table tbody");
  const unbox = $("#unassigned-list");

  if (!s) {
    summary.innerHTML = `<div class="empty" style="grid-column:1/-1">尚未编排。录入房间、互扰与话筒后点击"开始编排"。</div>`;
    atbody.innerHTML = `<tr><td colspan="6" class="empty">—</td></tr>`;
    unbox.innerHTML = `<div class="empty">—</div>`;
    $("#assigned-count").textContent = "";
    $("#unassigned-count").textContent = "";
    return;
  }

  const assignedIds = new Set(s.assignments.map((a) => a.microphone_id));
  const micsById = Object.fromEntries(STATE.microphones.map((m) => [m.id, m]));

  summary.innerHTML = `
    <div class="metric high"><div class="v">${s.high_assigned}/${s.high_count}</div><div class="l">高优先级获配</div></div>
    <div class="metric normal"><div class="v">${s.normal_assigned}/${s.normal_count}</div><div class="l">普通获配</div></div>
    <div class="metric total"><div class="v">${s.assigned}/${s.total_count}</div><div class="l">获配总数</div></div>
    <div class="metric"><div class="v" style="color:var(--bad)">${s.total_count - s.assigned}</div><div class="l">未获配</div></div>`;

  $("#assigned-count").textContent = `${s.assigned} 台`;
  $("#unassigned-count").textContent = `${s.total_count - s.assigned} 台`;

  atbody.innerHTML = s.assignments.map((a) => {
    const m = micsById[a.microphone_id] || {
      name: a.mic_name, room_name: "?", priority: "normal",
      start_time: 0, end_time: 0, guard_khz: 0,
    };
    const cls = m.priority === "high" ? "high" : "normal";
    return `
    <tr class="${a.was_locked ? "locked-row" : ""}">
      <td>${esc(a.mic_name)}${a.was_locked ? ' <span class="lock-mark">🔒</span>' : ""}</td>
      <td>${esc(m.room_name)}</td>
      <td><span class="tag ${cls}">${m.priority === "high" ? "高" : "普通"}</span></td>
      <td class="freq-cell">${fmtTime(m.start_time)} – ${fmtTime(m.end_time)}</td>
      <td class="freq-out">${a.freq_khz}</td>
      <td class="freq-cell">${m.guard_khz}</td>
    </tr>`;
  }).join("");

  const unassigned = STATE.microphones.filter((m) => !assignedIds.has(m.id));
  if (!unassigned.length) {
    unbox.innerHTML = `<div class="empty">全部话筒均已获配 🎉</div>`;
  } else {
    unbox.innerHTML = unassigned.map((m) => `
      <div class="un-item">
        <span><b>${esc(m.name)}</b> · ${esc(m.room_name)}
          · <span class="tag ${m.priority === "high" ? "high" : "normal"}">${m.priority === "high" ? "高" : "普通"}</span>
          ${m.locked_freq != null ? '<span class="lock-mark">🔒</span>' : ""}
        </span>
        <span class="reason">无满足保护间隔的可用频点</span>
      </div>`).join("");
  }
}

function renderHistory() {
  const box = $("#history-list");
  if (!STATE.history.length) {
    box.innerHTML = `<div class="empty">暂无历史方案。</div>`;
    return;
  }
  box.innerHTML = STATE.history.map((h) => `
    <div class="h-item ${h.is_active ? "" : "expired"}">
      <span>
        <span class="stamp">#${h.id} · ${esc(h.created_at)} UTC</span>
        <span class="pill ${h.is_active ? "active" : "expired"}">${h.is_active ? "当前" : "已过期"}</span>
      </span>
      <span>高优 ${h.high_assigned}/${h.high_count} · 总 ${h.assigned}/${h.total_count}</span>
    </div>`).join("");
}

// ------------------------------------------------------------------
// 输入变更后：提示旧方案过期
// ------------------------------------------------------------------
function applyState(next) {
  const hadActive = STATE && STATE.active_scheme;
  const hasActive = next.active_scheme;
  STATE = next;
  render();
  if (hadActive && !hasActive) {
    showBanner("warn", "⚠️ 输入已修改，<b>原方案已标记为过期</b>。确认参数后请重新点击「开始编排」。");
  }
}

// ------------------------------------------------------------------
// 弹窗填充
// ------------------------------------------------------------------
function fillRoomOptions() {
  const opts = STATE.rooms.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join("");
  $("#m-room").innerHTML = opts;
  $("#inter-a").innerHTML = opts;
  $("#inter-b").innerHTML = opts;
}

function openMicModal(mic) {
  fillRoomOptions();
  editingMic = mic ? mic.id : null;
  $("#mic-modal-title").textContent = mic ? "编辑话筒" : "新增话筒";
  $("#m-name").value = mic ? mic.name : "";
  if (mic) $("#m-room").value = mic.room_id;
  $("#m-priority").value = mic ? mic.priority : "normal";
  $("#m-start").value = mic ? mic.start_time : 1080;
  $("#m-end").value = mic ? mic.end_time : 1320;
  $("#m-guard").value = mic ? mic.guard_khz : 200;
  $("#m-freqs").value = mic ? mic.freqs.join(", ") : "600000, 602000, 604000, 606000";
  $("#m-locked").value = mic && mic.locked_freq != null ? mic.locked_freq : "";
  $("#mic-modal").showModal();
}

function openRoomModal(room) {
  editingRoom = room ? room.id : null;
  $("#room-modal-title").textContent = room ? "编辑房间" : "新增房间";
  $("#room-name").value = room ? room.name : "";
  $("#room-modal").showModal();
}

// ------------------------------------------------------------------
// 事件绑定
// ------------------------------------------------------------------
document.addEventListener("click", async (e) => {
  const t = e.target;

  if (t.dataset.open === "room-modal") { openRoomModal(null); return; }
  if (t.dataset.open === "inter-modal") {
    if (!STATE.rooms.length) { showBanner("error", "请先新增至少两个房间。"); return; }
    fillRoomOptions();
    if (STATE.rooms.length < 2) { showBanner("error", "至少需要两个房间才能建立互扰关系。"); return; }
    $("#inter-b").selectedIndex = Math.min(1, STATE.rooms.length - 1);
    $("#inter-modal").showModal();
    return;
  }
  if (t.dataset.open === "mic-modal") {
    if (!STATE.rooms.length) { showBanner("error", "请先新增房间，再录入话筒。"); return; }
    openMicModal(null);
    return;
  }

  if (t.dataset.roomEdit !== undefined) {
    const r = STATE.rooms.find((x) => x.id == t.dataset.roomEdit);
    if (r) openRoomModal(r);
    return;
  }
  if (t.dataset.roomDel !== undefined) {
    const r = STATE.rooms.find((x) => x.id == t.dataset.roomDel);
    if (!confirm(`确定删除房间「${r.name}」吗？该房间下有话筒时会被拒绝。`)) return;
    try {
      const next = await api(`/api/rooms/${r.id}`, "DELETE");
      clearBanners(); applyState(next);
    } catch (err) { showBanner("error", esc(err.message)); }
    return;
  }
  if (t.dataset.interDel !== undefined) {
    try {
      const next = await api(`/api/interferences/${t.dataset.interDel}`, "DELETE");
      clearBanners(); applyState(next);
    } catch (err) { showBanner("error", esc(err.message)); }
    return;
  }
  if (t.dataset.micEdit !== undefined) {
    const m = STATE.microphones.find((x) => x.id == t.dataset.micEdit);
    if (m) openMicModal(m);
    return;
  }
  if (t.dataset.micDel !== undefined) {
    const m = STATE.microphones.find((x) => x.id == t.dataset.micDel);
    if (!confirm(`确定删除话筒「${m.name}」吗？`)) return;
    try {
      const next = await api(`/api/microphones/${m.id}`, "DELETE");
      clearBanners(); applyState(next);
    } catch (err) { showBanner("error", esc(err.message)); }
    return;
  }
});

$("#room-save").addEventListener("click", async (e) => {
  e.preventDefault();
  const name = $("#room-name").value.trim();
  if (!name) { showBanner("error", "房间名称不能为空。"); return; }
  try {
    const next = editingRoom
      ? await api(`/api/rooms/${editingRoom}`, "PUT", { name })
      : await api("/api/rooms", "POST", { name });
    clearBanners(); $("#room-modal").close(); applyState(next);
  } catch (err) { showBanner("error", esc(err.message)); }
});

$("#inter-save").addEventListener("click", async (e) => {
  e.preventDefault();
  const a = parseInt($("#inter-a").value, 10);
  const b = parseInt($("#inter-b").value, 10);
  if (a === b) { showBanner("error", "请选择两个<b>不同</b>的房间。"); return; }
  try {
    const next = await api("/api/interferences", "POST",
      { room_a_id: a, room_b_id: b });
    clearBanners(); $("#inter-modal").close(); applyState(next);
  } catch (err) { showBanner("error", esc(err.message)); }
});

$("#mic-save").addEventListener("click", async (e) => {
  e.preventDefault();
  const name = $("#m-name").value.trim();
  const payload = {
    name,
    room_id: parseInt($("#m-room").value, 10),
    priority: $("#m-priority").value,
    start_time: parseInt($("#m-start").value, 10),
    end_time: parseInt($("#m-end").value, 10),
    guard_khz: parseInt($("#m-guard").value, 10),
    freqs: $("#m-freqs").value.split(/[\s,，、]+/).filter(Boolean).map((x) => parseInt(x, 10)),
    locked_freq: null,
  };
  const lockRaw = $("#m-locked").value.trim();
  if (lockRaw !== "") payload.locked_freq = parseInt(lockRaw, 10);

  try {
    const next = editingMic
      ? await api(`/api/microphones/${editingMic}`, "PUT", payload)
      : await api("/api/microphones", "POST", payload);
    clearBanners(); clearRowErrors(); $("#mic-modal").close(); applyState(next);
  } catch (err) {
    showBanner("error", esc(err.message));
    flashRows(err.devices && err.devices.length ? err.devices : (name ? [name] : []));
  }
});

$("#btn-solve").addEventListener("click", async () => {
  if (!STATE.microphones.length) {
    showBanner("error", "还没有任何话筒，请先录入后再编排。");
    return;
  }
  const btn = $("#btn-solve");
  btn.disabled = true; btn.textContent = "编排中…";
  try {
    const next = await api("/api/solve", "POST", {});
    clearBanners();
    applyState(next);
    const s = next.active_scheme;
    const highTxt = `高优先级获配 <b>${s.high_assigned}/${s.high_count}</b>`;
    const totalTxt = `获配总数 <b>${s.assigned}/${s.total_count}</b>`;
    if (s.assigned === s.total_count) {
      showBanner("info", `✅ 编排完成：${highTxt}，${totalTxt}，所有话筒均已获配。`);
    } else {
      showBanner("info", `✅ 已给出最大可行方案：${highTxt}，${totalTxt}。` +
        (next.truncated ? "（规模较大，方案为当前最优可行近似）" : ""));
    }
    $$("#assigned-table tbody tr, #unassigned-list .un-item").forEach((el) =>
      el.classList.add("row-flash"));
  } catch (err) {
    showBanner("error", `❌ 编排被拒绝：${esc(err.message)}` +
      (err.devices && err.devices.length
        ? `<br>相关设备：<span class="dev">${err.devices.map(esc).join("、")}</span>` : ""));
    flashRows(err.devices || []);
  } finally {
    btn.disabled = false; btn.textContent = "⚡ 开始编排";
  }
});

$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("将清空全部房间、互扰、话筒与方案，并重新写入演示数据，确定吗？")) return;
  try {
    const next = await api("/api/reset", "POST", {});
    clearBanners(); clearRowErrors();
    applyState(next);
    showBanner("info", "已重置为演示数据。");
  } catch (err) { showBanner("error", esc(err.message)); }
});

$("#btn-toggle-history").addEventListener("click", () => {
  const box = $("#history-list");
  const hidden = box.classList.toggle("hidden");
  $("#btn-toggle-history").textContent = hidden ? "展开" : "收起";
});

// ------------------------------------------------------------------
// 启动
// ------------------------------------------------------------------
(async function init() {
  try {
    STATE = await api("/api/state");
    render();
  } catch (err) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      `<div class="banner error">初始化失败：${esc(err.message)}</div>`);
  }
})();
