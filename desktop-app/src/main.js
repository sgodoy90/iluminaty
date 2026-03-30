/**
 * ILUMINATY Desktop App — Main Controller
 * ========================================
 * Navigation, API client, real-time polling, splash screen.
 */

// ─── Tauri IPC (available when running inside Tauri) ───
const TAURI = window.__TAURI__;
const invoke = TAURI?.core?.invoke;
const isTauri = !!invoke;

// ─── Config ───
const API = "http://127.0.0.1:8420";
let serverOnline = false;
let pollTimer = null;
let visionTimer = null;
let logs = [];

// ─── DOM Cache ───
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ─── XSS Helper (J1/J2) ───
function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ─── Splash Screen ───
function initSplash() {
  const bar = $("#splash-bar");
  let progress = 0;
  const steps = [20, 45, 70, 90, 100];
  let step = 0;

  const interval = setInterval(() => {
    if (step < steps.length) {
      progress = steps[step++];
      bar.style.width = progress + "%";
    }
    if (progress >= 100) {
      clearInterval(interval);
      setTimeout(() => {
        $("#splash").classList.add("hidden");
      }, 400);
    }
  }, 350);
}

// ─── Navigation ───
function initNav() {
  $$(".nav-item").forEach((item) => {
    item.addEventListener("click", () => {
      const view = item.dataset.view;
      if (!view) return;

      // Update nav
      $$(".nav-item").forEach((n) => n.classList.remove("active"));
      item.classList.add("active");

      // Update view
      $$(".view").forEach((v) => v.classList.remove("active"));
      const target = $(`#view-${view}`);
      if (target) target.classList.add("active");

      // Update topbar title
      const titles = {
        dashboard: "DASHBOARD",
        actions: "ACTIONS",
        layers: "7 LAYERS",
        logs: "ACTION LOG",
        settings: "SETTINGS",
      };
      $("#topbar-title").textContent = titles[view] || view.toUpperCase();
    });
  });
}

// ─── API Client ───
async function apiGet(endpoint) {
  try {
    if (isTauri) {
      const result = await invoke("api_get", { endpoint });
      return JSON.parse(result);
    } else {
      const resp = await fetch(API + endpoint);
      return await resp.json();
    }
  } catch {
    return null;
  }
}

async function apiPost(endpoint, body = {}) {
  try {
    if (isTauri) {
      const result = await invoke("api_post", {
        endpoint,
        body: JSON.stringify(body),
      });
      return JSON.parse(result);
    } else {
      const resp = await fetch(API + endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return await resp.json();
    }
  } catch {
    return null;
  }
}

// ─── Server Control ───
async function checkServer() {
  const data = await apiGet("/health");
  const online = !!data;

  if (online !== serverOnline) {
    serverOnline = online;
    updateServerUI();
    if (online) {
      startPolling();
      startVisionPolling();
    } else {
      stopPolling();
      stopVisionPolling();
    }
  }
}

function updateServerUI() {
  const dot = $("#status-dot");
  const text = $("#status-text");
  const btn = $("#server-toggle");

  if (serverOnline) {
    dot.classList.add("online");
    text.textContent = "Server online";
    btn.textContent = "Stop Server";
  } else {
    dot.classList.remove("online");
    text.textContent = "Server offline";
    btn.textContent = "Start Server";
  }
}

async function toggleServer() {
  if (!isTauri) {
    addLog("system", "Use terminal: python main.py start --actions", "warn");
    return;
  }

  try {
    if (serverOnline) {
      await invoke("stop_server");
      serverOnline = false;
      updateServerUI();
      stopPolling();
      stopVisionPolling();
      addLog("system", "Server stopped", "ok");
    } else {
      const result = await invoke("start_server");
      addLog("system", result, "ok");
      // Give it a moment to start
      setTimeout(checkServer, 2000);
    }
  } catch (err) {
    addLog("system", "Server error: " + (err.message || err), "fail");
  }
}

// ─── Polling ───
function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollData, 3000);
  pollData(); // immediate first call
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollData() {
  if (!serverOnline) return;

  // Stats
  const stats = await apiGet("/stats");
  if (stats) {
    if (stats.captures !== undefined) {
      $("#perc-changes").textContent = stats.captures;
    }
    if (stats.uptime !== undefined) {
      const mins = Math.floor(stats.uptime / 60);
      const secs = Math.floor(stats.uptime % 60);
      $("#stat-uptime").textContent = `${mins}m ${secs}s`;
    }
  }

  // Perception
  const perc = await apiGet("/vision/stats");
  if (perc) {
    $("#perc-ocr").textContent = perc.ocr_enabled ? "Yes" : "No";
    $("#perc-interval").textContent = (perc.interval_ms || "--") + "ms";
    $("#perc-buffer").textContent = (perc.buffer_size || "--") + " frames";
    $("#perc-status").textContent = perc.capturing ? "Capturing" : "Idle";
    if (perc.fps) {
      $("#vision-fps").textContent = perc.fps.toFixed(1) + " FPS";
    }
  }

  // Recent actions from audit
  const audit = await apiGet("/audit/recent?limit=5");
  if (audit && Array.isArray(audit)) {
    const container = $("#recent-actions");
    container.innerHTML = "";
    if (audit.length === 0) {
      container.innerHTML =
        '<div class="log-entry"><span class="log-detail" style="color:var(--text-muted)">No actions yet</span></div>';
    } else {
      audit.forEach((a) => {
        const entry = document.createElement("div");
        entry.className = "log-entry";
        const time = a.timestamp
          ? new Date(a.timestamp * 1000).toLocaleTimeString()
          : "--:--";
        entry.innerHTML = `
          <span class="log-time">${escapeHtml(time)}</span>
          <span class="log-action">${escapeHtml(a.action || "?")}</span>
          <span class="log-status ${a.success ? "ok" : "fail"}">${a.success ? "OK" : "FAIL"}</span>
        `;
        container.appendChild(entry);
      });
    }
  }
}

// ─── Vision Polling ───
function startVisionPolling() {
  if (visionTimer) return;
  visionTimer = setInterval(pollVision, 1000);
  pollVision();
}

function stopVisionPolling() {
  if (visionTimer) {
    clearInterval(visionTimer);
    visionTimer = null;
  }
}

async function pollVision() {
  if (!serverOnline) return;

  try {
    const data = await apiGet("/vision/snapshot");
    if (data && data.image) {
      const img = $("#vision-img");
      const placeholder = $("#vision-placeholder");
      const overlay = $("#vision-overlay");

      img.src = "data:image/jpeg;base64," + data.image;
      img.style.display = "block";
      placeholder.style.display = "none";
      overlay.style.display = "flex";

      if (data.width && data.height) {
        $("#vision-res").textContent = `${data.width}x${data.height}`;
      }
    }
  } catch {
    // Vision not available
  }
}

// ─── Agent Execute ───
async function executeAgent() {
  const input = $("#agent-input");
  const result = $("#agent-result");
  const instruction = input.value.trim();
  if (!instruction) return;

  result.style.display = "block";
  result.textContent = "Executing...";
  result.style.color = "var(--yellow)";

  const data = await apiPost("/agent/do", { instruction });

  if (data) {
    result.style.color = data.success ? "var(--green)" : "var(--red)";
    result.textContent = data.message || JSON.stringify(data);
    addLog(data.action || "agent", instruction, data.success ? "ok" : "fail");
  } else {
    result.style.color = "var(--red)";
    result.textContent = "Failed — server may be offline";
    addLog("agent", instruction, "fail");
  }

  input.value = "";
}

// ─── Take Snapshot ───
async function takeSnapshot() {
  if (!serverOnline) {
    addLog("snapshot", "Server offline", "fail");
    return;
  }
  await pollVision();
  addLog("snapshot", "Captured", "ok");
}

// ─── Logs ───
function addLog(action, detail, status) {
  const now = new Date().toLocaleTimeString();
  logs.unshift({ time: now, action, detail, status });
  if (logs.length > 200) logs.pop();
  renderLogs();
}

function renderLogs() {
  const container = $("#log-list");
  if (logs.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>No logs yet</h3>
        <p>Actions will appear here as the Eye operates</p>
      </div>
    `;
    return;
  }

  container.innerHTML = logs
    .map(
      (l) => `
    <div class="log-entry">
      <span class="log-time">${escapeHtml(l.time)}</span>
      <span class="log-action">${escapeHtml(l.action)}</span>
      <span class="log-detail">${escapeHtml(l.detail)}</span>
      <span class="log-status ${escapeHtml(l.status)}">${escapeHtml(l.status).toUpperCase()}</span>
    </div>
  `
    )
    .join("");
}

// ─── Action Buttons ───
// D1: Actions that need parameters before calling the API
const ACTION_PARAMS = {
  click: { prompt: "Enter coordinates (x, y):", param: "coordinates" },
  double_click: { prompt: "Enter coordinates (x, y):", param: "coordinates" },
  right_click: { prompt: "Enter coordinates (x, y):", param: "coordinates" },
  drag_drop: { prompt: "Enter start and end (x1,y1,x2,y2):", param: "coordinates" },
  move_mouse: { prompt: "Enter coordinates (x, y):", param: "coordinates" },
  scroll: { prompt: "Enter scroll amount (positive=down, negative=up):", param: "amount" },
  typewrite: { prompt: "Enter text to type:", param: "text" },
  press_key: { prompt: "Enter key name (e.g. enter, tab, escape):", param: "key" },
  hotkey: { prompt: "Enter hotkey combo (e.g. ctrl+s):", param: "keys" },
  set_clipboard: { prompt: "Enter text for clipboard:", param: "text" },
  click_element: { prompt: "Enter element name or selector:", param: "selector" },
  type_in_field: { prompt: "Enter field name and text (field: text):", param: "input" },
  select_option: { prompt: "Enter selector and option (selector: option):", param: "input" },
  find_element: { prompt: "Enter element name or text to find:", param: "query" },
  vscode_open: { prompt: "Enter file path to open:", param: "path" },
  terminal_run: { prompt: "Enter command to run:", param: "command" },
  git_commit: { prompt: "Enter commit message:", param: "message" },
  browser_navigate: { prompt: "Enter URL to navigate to:", param: "url" },
  browser_click: { prompt: "Enter CSS selector to click:", param: "selector" },
  browser_fill: { prompt: "Enter selector and value (selector: value):", param: "input" },
  browser_eval: { prompt: "Enter JavaScript to evaluate:", param: "code" },
  file_read: { prompt: "Enter file path:", param: "path" },
  file_write: { prompt: "Enter file path:", param: "path" },
  file_list: { prompt: "Enter directory path:", param: "path" },
  file_search: { prompt: "Enter search pattern:", param: "pattern" },
  file_copy: { prompt: "Enter source and destination (src: dst):", param: "paths" },
};

function initActionButtons() {
  $$(".action-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      if (!serverOnline) {
        addLog(action, "Server offline", "fail");
        return;
      }

      let body = {};
      const paramInfo = ACTION_PARAMS[action];
      if (paramInfo) {
        const input = prompt(paramInfo.prompt);
        if (input === null) return; // cancelled
        body[paramInfo.param] = input;
      }

      const data = await apiPost(`/action/${action}`, body);
      if (data) {
        addLog(action, data.message || "Done", data.success ? "ok" : "warn");
      } else {
        addLog(action, "No response", "fail");
      }
    });
  });
}

// ─── Init ───
function init() {
  initSplash();
  initNav();
  initActionButtons();

  // Server toggle
  $("#server-toggle").addEventListener("click", toggleServer);

  // Topbar buttons
  $("#btn-refresh").addEventListener("click", () => {
    checkServer();
    if (serverOnline) pollData();
  });
  $("#btn-snapshot").addEventListener("click", takeSnapshot);

  // Agent execute
  $("#agent-exec").addEventListener("click", executeAgent);
  $("#agent-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") executeAgent();
  });

  // Clear logs
  $("#btn-clear-logs").addEventListener("click", () => {
    logs = [];
    renderLogs();
  });

  // J11: Settings save
  const saveSettings = async () => {
    const settings = {
      autonomy: $("#set-autonomy").value,
      capture_interval: parseInt($("#set-interval").value, 10),
      ocr_enabled: $("#set-ocr").checked,
      buffer_size: parseInt($("#set-buffer").value, 10),
      port: parseInt($("#set-port").value, 10),
      browser_port: parseInt($("#set-browser-port").value, 10),
      rate_limit: parseInt($("#set-rate").value, 10),
      kill_zones: $("#set-killzones").checked,
      audit_trail: $("#set-audit").checked,
      mcp_enabled: $("#set-mcp").checked,
    };
    const result = await apiPost("/settings", settings);
    if (result) {
      addLog("settings", "Settings saved (apply on next server restart)", "ok");
    } else {
      addLog("settings", "Failed to save settings", "fail");
    }
  };
  const saveBtn = $("#btn-save-settings");
  if (saveBtn) saveBtn.addEventListener("click", saveSettings);

  // J6: Pause/resume polling when window is hidden
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopPolling();
      stopVisionPolling();
    } else if (serverOnline) {
      startPolling();
      startVisionPolling();
    }
  });

  // Check server status periodically
  checkServer();
  setInterval(checkServer, 5000);
}

// Start
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
