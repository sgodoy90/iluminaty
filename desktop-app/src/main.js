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
          <span class="log-time">${time}</span>
          <span class="log-action">${a.action || "?"}</span>
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
    const resp = await fetch(API + "/vision/snapshot");
    if (resp.ok) {
      const data = await resp.json();
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

  const data = await apiPost(
    `/agent/do?instruction=${encodeURIComponent(instruction)}`
  );

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
      <span class="log-time">${l.time}</span>
      <span class="log-action">${l.action}</span>
      <span class="log-detail">${l.detail}</span>
      <span class="log-status ${l.status}">${l.status.toUpperCase()}</span>
    </div>
  `
    )
    .join("");
}

// ─── Action Buttons ───
function initActionButtons() {
  $$(".action-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      if (!serverOnline) {
        addLog(action, "Server offline", "fail");
        return;
      }

      // For simple demo actions, just call the endpoint
      const data = await apiPost(`/action/${action}`);
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
