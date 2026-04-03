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
let currentOperatingMode = "SAFE";
let runtimeTimer = null;
let desktopSettings = null;

const PRODUCT_CAPS = {
  free_actions: 7,
  pro_actions: "42+",
  free_mcp_tools: 29,
  pro_mcp_tools: 48,
};

const DEFAULT_DESKTOP_SETTINGS = {
  autonomy: "confirm",
  operating_mode: "SAFE",
  monitor: 0,
  capture_interval_ms: 500,
  fast_loop_hz: 10.0,
  deep_loop_hz: 1.0,
  vision_profile: "core_ram",
  api_port: 8420,
  vlm_enabled: false,
  vlm_backend: "smol",
  vlm_model: "HuggingFaceTB/SmolVLM2-500M-Instruct",
  vlm_int8: true,
  vlm_device: "auto",
  vlm_image_size: 384,
  vlm_max_tokens: 64,
  vlm_min_interval_ms: 900,
  vlm_keepalive_ms: 7000,
  vlm_priority_threshold: 0.55,
  vlm_secondary_heartbeat_s: 8.0,
};

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

function clampNumber(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

function applyDesktopSettingsToUI(settings) {
  const s = { ...DEFAULT_DESKTOP_SETTINGS, ...(settings || {}) };
  const set = (id, value) => {
    const el = $(id);
    if (!el) return;
    if (el.type === "checkbox") {
      el.checked = !!value;
    } else {
      el.value = value;
    }
  };

  set("#set-autonomy", s.autonomy);
  set("#set-operating-mode", s.operating_mode);
  set("#set-interval", s.capture_interval_ms);
  set("#set-port", s.api_port);
  set("#set-fast-loop-hz", s.fast_loop_hz);
  set("#set-deep-loop-hz", s.deep_loop_hz);
  set("#set-vision-profile", s.vision_profile);
  set("#set-vlm-enabled", s.vlm_enabled);
  set("#set-vlm-backend", s.vlm_backend);
  set("#set-vlm-model", s.vlm_model);
  set("#set-vlm-int8", s.vlm_int8);
  set("#set-vlm-device", s.vlm_device);
  set("#set-vlm-image-size", s.vlm_image_size);
  set("#set-vlm-max-tokens", s.vlm_max_tokens);
  currentOperatingMode = String(s.operating_mode || "SAFE").toUpperCase();
}

function readDesktopSettingsFromUI() {
  return {
    autonomy: $("#set-autonomy")?.value || "confirm",
    operating_mode: ($("#set-operating-mode")?.value || "SAFE").toUpperCase(),
    monitor: 0,
    capture_interval_ms: Math.round(clampNumber($("#set-interval")?.value, 100, 10000, 500)),
    fast_loop_hz: clampNumber($("#set-fast-loop-hz")?.value, 2.0, 20.0, 10.0),
    deep_loop_hz: clampNumber($("#set-deep-loop-hz")?.value, 0.5, 4.0, 1.0),
    vision_profile: $("#set-vision-profile")?.value || "core_ram",
    api_port: Math.round(clampNumber($("#set-port")?.value, 1024, 65535, 8420)),
    vlm_enabled: !!$("#set-vlm-enabled")?.checked,
    vlm_backend: $("#set-vlm-backend")?.value || "smol",
    vlm_model: ($("#set-vlm-model")?.value || DEFAULT_DESKTOP_SETTINGS.vlm_model).trim(),
    vlm_int8: !!$("#set-vlm-int8")?.checked,
    vlm_device: $("#set-vlm-device")?.value || "auto",
    vlm_image_size: Math.round(clampNumber($("#set-vlm-image-size")?.value, 224, 768, 384)),
    vlm_max_tokens: Math.round(clampNumber($("#set-vlm-max-tokens")?.value, 16, 256, 64)),
    vlm_min_interval_ms: 900,
    vlm_keepalive_ms: 7000,
    vlm_priority_threshold: 0.55,
    vlm_secondary_heartbeat_s: 8.0,
  };
}

async function loadDesktopSettings() {
  if (!isTauri || !invoke) {
    desktopSettings = { ...DEFAULT_DESKTOP_SETTINGS };
    applyDesktopSettingsToUI(desktopSettings);
    return desktopSettings;
  }
  try {
    const settings = await invoke("get_desktop_settings");
    desktopSettings = { ...DEFAULT_DESKTOP_SETTINGS, ...(settings || {}) };
    applyDesktopSettingsToUI(desktopSettings);
    return desktopSettings;
  } catch (e) {
    desktopSettings = { ...DEFAULT_DESKTOP_SETTINGS };
    applyDesktopSettingsToUI(desktopSettings);
    addLog("runtime", `Settings load fallback: ${e?.message || e}`, "warn");
    return desktopSettings;
  }
}

function setRuntimeStatusText(text, color = "var(--text-dim)") {
  const el = $("#runtime-status");
  if (!el) return;
  el.textContent = text || "--";
  el.style.color = color;
}

function setVlmDownloadStatusText(text, color = "var(--text-dim)") {
  const el = $("#vlm-download-status");
  if (!el) return;
  el.textContent = text || "idle";
  el.style.color = color;
}

async function refreshRuntimeStatus() {
  if (!isTauri || !invoke) return;
  try {
    const status = await invoke("get_runtime_status");
    if (status?.ready) {
      const label = `ready | ${status.python?.split("\\").pop() || "python"}`;
      setRuntimeStatusText(label, "var(--green)");
    } else {
      const miss = Array.isArray(status?.missing) && status.missing.length
        ? `missing: ${status.missing.join(", ")}`
        : (status?.message || "not ready");
      setRuntimeStatusText(miss, "var(--yellow)");
    }
  } catch (e) {
    setRuntimeStatusText(`error: ${e?.message || e}`, "var(--red)");
  }
}

function startRuntimePolling() {
  if (runtimeTimer) return;
  runtimeTimer = setInterval(async () => {
    await refreshRuntimeStatus();
    if (!isTauri || !invoke) return;
    try {
      const status = await invoke("get_vlm_download_status");
      if (!status) return;
      if (status.status === "running") setVlmDownloadStatusText(`downloading ${status.model}...`, "var(--yellow)");
      else if (status.status === "done") setVlmDownloadStatusText("model cached", "var(--green)");
      else if (status.status === "error") setVlmDownloadStatusText(status.message || "download error", "var(--red)");
      else setVlmDownloadStatusText(status.message || "idle", "var(--text-dim)");
    } catch {}
  }, 4000);
}

function stopRuntimePolling() {
  if (!runtimeTimer) return;
  clearInterval(runtimeTimer);
  runtimeTimer = null;
}

async function bootstrapRuntime(installVlm = false) {
  if (!isTauri || !invoke) return null;
  setRuntimeStatusText("bootstrapping runtime...", "var(--yellow)");
  try {
    const result = await invoke("bootstrap_runtime", { installVlm });
    if (result?.ready) {
      setRuntimeStatusText("runtime ready", "var(--green)");
      addLog("runtime", "Runtime bootstrap complete", "ok");
    } else {
      setRuntimeStatusText(result?.message || "runtime missing modules", "var(--yellow)");
      addLog("runtime", result?.message || "Runtime incomplete", "warn");
    }
    return result;
  } catch (e) {
    setRuntimeStatusText(`bootstrap failed: ${e?.message || e}`, "var(--red)");
    addLog("runtime", `Bootstrap failed: ${e?.message || e}`, "fail");
    return null;
  }
}

async function startVlmDownload() {
  if (!isTauri || !invoke) return;
  const model = ($("#set-vlm-model")?.value || DEFAULT_DESKTOP_SETTINGS.vlm_model).trim();
  if (!model) {
    addLog("vlm", "Model id required", "fail");
    return;
  }
  setVlmDownloadStatusText("starting download...", "var(--yellow)");
  try {
    const status = await invoke("start_vlm_download", { modelId: model, force: false });
    if (status?.status === "running") {
      setVlmDownloadStatusText(`downloading ${model}...`, "var(--yellow)");
      addLog("vlm", `Downloading ${model} in background`, "ok");
    } else {
      setVlmDownloadStatusText(status?.message || "idle");
    }
  } catch (e) {
    setVlmDownloadStatusText(`error: ${e?.message || e}`, "var(--red)");
    addLog("vlm", `Download start failed: ${e?.message || e}`, "fail");
  }
}

// ─── Auth / Onboarding ───
const AUTH_API = "https://api.iluminaty.dev";
// const AUTH_API = "http://localhost:8787"; // dev

function getSession() {
  try { return JSON.parse(localStorage.getItem("iluminaty_session")); } catch { return null; }
}
function setSession(data) {
  localStorage.setItem("iluminaty_session", JSON.stringify(data));
}
function clearSession() {
  localStorage.removeItem("iluminaty_session");
}

async function logout() {
  // Stop server first
  if (isTauri && invoke) {
    try { await invoke("stop_server"); } catch {}
  }
  serverOnline = false;
  updateServerUI();
  stopPolling();
  stopVisionPolling();
  stopTokenPolling();
  stopRuntimePolling();
  clearSession();
  // Show login screen
  $("#onboarding").classList.remove("hidden");
  $("#sidebar-user").style.display = "none";
  const upgradeBtn = $("#btn-upgrade");
  if (upgradeBtn) upgradeBtn.classList.remove("hidden");
  addLog("system", "Signed out", "ok");
}

// ─── API Key masking ───
let keyRevealed = false;
function maskKey(key) {
  if (!key) return "No key";
  return key.substring(0, 9) + "****-****-" + key.slice(-4);
}

function showSidebarUser(session) {
  if (!session || !session.user) return;
  const el = $("#sidebar-user");
  if (!el) return;
  el.style.display = "flex";
  const u = session.user;
  const isPro = u.plan === "pro" || u.plan === "enterprise";
  $("#sidebar-avatar").textContent = (u.name || u.email || "U")[0].toUpperCase();
  $("#sidebar-name").textContent = u.name || u.email.split("@")[0];
  $("#sidebar-plan").textContent = (isPro ? "Pro" : "Free") + " Plan";

  // Show/hide upgrade button
  const upgradeBtn = $("#btn-upgrade");
  if (upgradeBtn) {
    if (isPro) {
      upgradeBtn.classList.add("hidden");
    } else {
      upgradeBtn.classList.remove("hidden");
    }
  }
}

function openUpgradeUrl() {
  const url = "https://iluminaty.dev/#pricing";
  if (TAURI?.shell?.open) {
    TAURI.shell.open(url);
  } else {
    window.open(url, "_blank");
  }
}

function updatePlanBanner(session) {
  const banner = $("#plan-banner");
  if (!banner) return;
  const isPro = session?.user?.plan === "pro" || session?.user?.plan === "enterprise";
  if (isPro) {
    const badge = $("#plan-banner-badge");
    if (badge) { badge.textContent = "PRO"; badge.classList.add("pro"); }
    const text = $(".plan-banner-text");
    if (text) {
      text.textContent = `${PRODUCT_CAPS.pro_actions} actions \u00b7 ${PRODUCT_CAPS.pro_mcp_tools} MCP tools \u00b7 Full access`;
    }
    const btn = $("#plan-banner-upgrade");
    if (btn) btn.classList.add("hidden");
  }
}

// Wire upgrade buttons on DOMContentLoaded
document.addEventListener("DOMContentLoaded", () => {
  const upgradeBtn = $("#btn-upgrade");
  if (upgradeBtn) upgradeBtn.addEventListener("click", openUpgradeUrl);
  const bannerBtn = $("#plan-banner-upgrade");
  if (bannerBtn) bannerBtn.addEventListener("click", openUpgradeUrl);

  // Update plan banner with session
  const session = getSession();
  if (session) updatePlanBanner(session);
});

function initOnboarding() {
  const session = getSession();
  if (session && session.api_key) {
    // Already logged in — skip onboarding, update all UI
    $("#onboarding").classList.add("hidden");
    showSidebarUser(session);
    updateApiKeyCard(session);
    initSplash();
    return;
  }

  // Show onboarding
  let isSignUp = false;
  const form = $("#onboard-form");
  const error = $("#onboard-error");
  const submit = $("#onboard-submit");
  const toggleLink = $("#onboard-toggle-link");

  toggleLink.addEventListener("click", (e) => {
    e.preventDefault();
    isSignUp = !isSignUp;
    $("#onboard-name-group").style.display = isSignUp ? "block" : "none";
    submit.textContent = isSignUp ? "Create Account" : "Sign In";
    $("#onboard-toggle-text").textContent = isSignUp ? "Already have an account?" : "Don't have an account?";
    toggleLink.textContent = isSignUp ? "Sign in" : "Sign up";
    error.textContent = "";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    const email = $("#onboard-email").value.trim();
    const password = $("#onboard-password").value;
    const name = $("#onboard-name").value.trim();

    if (!email || !password) { error.textContent = "Email and password required"; return; }
    if (isSignUp && !name) { error.textContent = "Name required"; return; }

    submit.disabled = true;
    submit.textContent = "Connecting...";

    try {
      const endpoint = isSignUp ? "/auth/register" : "/auth/login";
      const body = isSignUp ? { email, password, name } : { email, password };
      const resp = await fetch(AUTH_API + endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!resp.ok) {
        error.textContent = data.error || "Something went wrong";
        return;
      }
      setSession(data);
      showSidebarUser(data);
      updatePlanBanner(data);
      updateApiKeyCard(data);
      $("#onboarding").classList.add("hidden");
      initSplash();
      startServerAfterLogin();
    } catch (err) {
      // Auth server unreachable — allow dev bypass
      const DEV_ACCOUNTS = { "dev@iluminaty.dev": "iluminaty2026" };
      if (DEV_ACCOUNTS[email] && DEV_ACCOUNTS[email] === password) {
        const devSession = {
          user: { name: "Developer", email, plan: "pro" },
          api_key: "ILUM-dev-godo-master-key-2026",
        };
        setSession(devSession);
        showSidebarUser(devSession);
        updatePlanBanner(devSession);
        updateApiKeyCard(devSession);
        $("#onboarding").classList.add("hidden");
        initSplash();
        startServerAfterLogin();
      } else {
        error.textContent = "Cannot reach auth server. Please try again later.";
      }
    } finally {
      submit.disabled = false;
      submit.textContent = isSignUp ? "Create Account" : "Sign In";
    }
  });

  // Google button
  $("#onboard-google").addEventListener("click", () => {
    error.textContent = "Google login coming soon. Use email for now.";
  });
}

// ─── API Key Card ───
function updateApiKeyCard(session) {
  if (!session || !session.api_key) return;
  const key = session.api_key;
  const plan = session.user?.plan || "free";
  const isPro = plan === "pro" || plan === "enterprise";

  // Key display
  const keyEl = $("#apikey-value");
  if (keyEl) {
    keyEl.textContent = keyRevealed ? key : maskKey(key);
    keyEl.classList.toggle("masked", !keyRevealed);
  }

  // CLI hint
  const cliVal = $("#apikey-cli-val");
  if (cliVal) cliVal.textContent = key;

  // Plan badge
  const badge = $("#plan-badge");
  if (badge) {
    badge.textContent = plan.toUpperCase();
    badge.classList.toggle("pro", isPro);
  }

  // Plan features
  const info = $("#apikey-plan-info");
  if (info) {
    if (isPro) {
      info.innerHTML = `
        <div class="plan-feature"><span class="plan-check">&#10003;</span> ${PRODUCT_CAPS.pro_actions} actions</div>
        <div class="plan-feature"><span class="plan-check">&#10003;</span> ${PRODUCT_CAPS.pro_mcp_tools} MCP tools</div>
        <div class="plan-feature"><span class="plan-check">&#10003;</span> Vision + OCR</div>
        <div class="plan-feature"><span class="plan-check">&#10003;</span> Browser + Terminal</div>
        <div class="plan-feature"><span class="plan-check">&#10003;</span> File System</div>
        <div class="plan-feature"><span class="plan-check">&#10003;</span> Full Autonomy</div>
      `;
    }
  }

  // Upgrade button
  const upgradeCard = $("#btn-upgrade-card");
  if (upgradeCard) {
    if (isPro) {
      upgradeCard.classList.add("hidden");
    } else {
      upgradeCard.classList.remove("hidden");
    }
  }
}

function initApiKeyCard() {
  // Reveal toggle
  const revealBtn = $("#apikey-reveal");
  if (revealBtn) {
    revealBtn.addEventListener("click", () => {
      keyRevealed = !keyRevealed;
      revealBtn.textContent = keyRevealed ? "Hide" : "Reveal";
      const session = getSession();
      if (session) updateApiKeyCard(session);
    });
  }

  // Copy button
  const copyBtn = $("#apikey-copy");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      const session = getSession();
      if (session?.api_key) {
        navigator.clipboard.writeText(session.api_key).then(() => {
          copyBtn.textContent = "Copied!";
          setTimeout(() => { copyBtn.textContent = "Copy"; }, 2000);
        });
      }
    });
  }

  // Upgrade button in card
  const upgradeBtn = $("#btn-upgrade-card");
  if (upgradeBtn) upgradeBtn.addEventListener("click", openUpgradeUrl);
}

// ─── Token Economy ───
let tokenTimer = null;

function startTokenPolling() {
  if (tokenTimer) return;
  tokenTimer = setInterval(pollTokens, 3000);
  pollTokens();
}

function stopTokenPolling() {
  if (tokenTimer) { clearInterval(tokenTimer); tokenTimer = null; }
}

async function pollTokens() {
  if (!serverOnline) return;
  const data = await apiGet("/tokens/status");
  if (!data) return;

  // Update stats
  const usedEl = $("#token-used");
  if (usedEl) usedEl.textContent = data.used.toLocaleString();

  const budgetEl = $("#token-budget-val");
  if (budgetEl) budgetEl.textContent = data.budget === 0 ? "\u221e" : data.budget.toLocaleString();

  const remainEl = $("#token-remaining");
  if (remainEl) {
    if (data.remaining === -1) {
      remainEl.textContent = "\u221e";
      remainEl.style.color = "";
    } else {
      remainEl.textContent = data.remaining.toLocaleString();
      remainEl.style.color = data.remaining < 5000 ? "var(--red)" : "";
    }
  }

  // Update mode badge
  const modeBadge = $("#token-mode-badge");
  if (modeBadge) modeBadge.textContent = data.mode;

  // Update mode buttons
  $$(".token-mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === data.mode);
  });

  // Update history
  const histEl = $("#token-history");
  if (histEl && data.last_5 && data.last_5.length > 0) {
    histEl.innerHTML = data.last_5.slice().reverse().map(e => {
      const action = escapeHtml(e.action.replace("vision/smart ", "").replace(/[()]/g, ""));
      return `<div class="token-history-entry"><span>${action}</span><span class="th-tokens">${e.tokens}</span></div>`;
    }).join("");
  }
}

function initTokenCard() {
  // Mode buttons
  $$(".token-mode-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const mode = btn.dataset.mode;
      await apiPost(`/tokens/mode?mode=${mode}`);
      $$(".token-mode-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const modeBadge = $("#token-mode-badge");
      if (modeBadge) modeBadge.textContent = mode;
      addLog("tokens", `Mode set to ${mode}`, "ok");
    });
  });

  // Budget set
  const budgetBtn = $("#token-budget-set");
  if (budgetBtn) {
    budgetBtn.addEventListener("click", async () => {
      const val = parseInt($("#token-budget-input").value, 10) || 0;
      await apiPost(`/tokens/budget?limit=${val}`);
      addLog("tokens", `Budget set to ${val === 0 ? "unlimited" : val}`, "ok");
      pollTokens();
    });
  }

  // Reset
  const resetBtn = $("#token-reset");
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      await apiPost("/tokens/reset");
      addLog("tokens", "Token counter reset", "ok");
      pollTokens();
    });
  }
}

// ─── Plan Upgrade Check ───
// Periodically checks if user's plan was upgraded (after purchase)
async function checkPlanUpgrade() {
  const session = getSession();
  if (!session || !session.api_key) return;

  try {
    // Check against auth API
    const resp = await fetch(AUTH_API + "/auth/validate", {
      headers: { "Authorization": "Bearer " + session.api_key },
    });
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data.valid) return;

    const currentPlan = session.user?.plan || "free";
    const newPlan = data.plan || data.user?.plan || "free";

    if (newPlan !== currentPlan && (newPlan === "pro" || newPlan === "enterprise")) {
      // Plan was upgraded!
      session.user.plan = newPlan;
      setSession(session);
      showSidebarUser(session);
      updatePlanBanner(session);
      updateApiKeyCard(session);

      // Show upgrade message
      const msg = $("#apikey-refresh-msg");
      if (msg) { msg.style.display = "block"; setTimeout(() => { msg.style.display = "none"; }, 10000); }

      addLog("plan", `Upgraded to ${newPlan.toUpperCase()}!`, "ok");
    }
  } catch {
    // Auth server unreachable — no problem
  }
}

// ─── Post-Login Server Start ───
async function startServerAfterLogin() {
  // Start server via Tauri (if inside desktop app)
  if (isTauri && invoke) {
    try {
      await invoke("start_server");
      addLog("system", "Server starting...", "ok");
    } catch {}
  }
  // Start health check polling — detects when server is ready
  checkServer();
  setInterval(checkServer, 5000);
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
function getApiKey() {
  const session = getSession();
  return session?.api_key || "";
}

async function apiGet(endpoint) {
  try {
    const apiKey = getApiKey();
    if (isTauri) {
      const result = await invoke("api_get", { endpoint, apiKey });
      return JSON.parse(result);
    } else {
      const headers = {};
      if (apiKey) headers["x-api-key"] = apiKey;
      const resp = await fetch(API + endpoint, { headers });
      return await resp.json();
    }
  } catch {
    return null;
  }
}

async function apiPost(endpoint, body = {}) {
  try {
    const apiKey = getApiKey();
    if (isTauri) {
      const result = await invoke("api_post", {
        endpoint,
        body: JSON.stringify(body),
        apiKey,
      });
      return JSON.parse(result);
    } else {
      const headers = { "Content-Type": "application/json" };
      if (apiKey) headers["x-api-key"] = apiKey;
      const resp = await fetch(API + endpoint, {
        method: "POST",
        headers,
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
      startTokenPolling();
      checkGpuStatus();
    } else {
      stopPolling();
      stopVisionPolling();
      stopTokenPolling();
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

async function checkGpuStatus() {
  try {
    const port = desktopSettings?.api_port || DEFAULT_DESKTOP_SETTINGS.api_port;
    const resp = await fetch(`http://127.0.0.1:${port}/system/gpu`);
    const data = await resp.json();
    const info = document.getElementById("gpu-info");
    const badge = document.getElementById("gpu-badge");
    if (!info || !badge) return;
    if (data.cuda_available) {
      info.textContent = `${data.gpu_name} \u2014 ${data.vram_total_mb}MB VRAM (${data.cuda_version})`;
      badge.textContent = "CUDA";
      badge.style.color = "#00ff88";
    } else if (data.torch_version) {
      info.textContent = "No CUDA GPU detected \u2014 CPU mode";
      badge.textContent = "CPU";
      badge.style.color = "#ff8800";
    } else {
      info.textContent = "torch not installed \u2014 enable VLM to install";
      badge.textContent = "N/A";
      badge.style.color = "#888";
    }
  } catch {
    const info = document.getElementById("gpu-info");
    if (info) info.textContent = "Server not ready";
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

  // Buffer/Capture Stats
  const stats = await apiGet("/buffer/stats");
  if (stats) {
    if (stats.total_frames_captured !== undefined) {
      $("#perc-changes").textContent = stats.total_frames_captured;
    }
    if (stats.buffer_seconds !== undefined) {
      $("#stat-uptime").textContent = `${Math.round(stats.buffer_seconds)}s buffer`;
    }
    if (stats.current_fps !== undefined && $("#vision-fps")) {
      $("#vision-fps").textContent = Number(stats.current_fps).toFixed(1) + " FPS";
    }
  }

  // Perception state (v2 contract first, legacy fallback)
  const world = await apiGet("/perception/world");
  const readiness = await apiGet("/perception/readiness");
  if (world) {
    $("#perc-ocr").textContent = world.visual_facts && world.visual_facts.length > 0 ? "Yes" : "No";
    $("#perc-interval").textContent = (world.staleness_ms ?? "--") + "ms";
    $("#perc-buffer").textContent = `${(world.evidence || []).length} evidence`;
    $("#perc-status").textContent = world.task_phase || "unknown";
    if (readiness && readiness.uncertainty !== undefined) {
      $("#perc-changes").textContent = `${Math.round((1 - Number(readiness.uncertainty || 0)) * 100)}% ready`;
    }
  } else {
    const perc = await apiGet("/perception/state");
    if (perc) {
      const oldWorld = perc.world || {};
      $("#perc-ocr").textContent = oldWorld.visual_facts && oldWorld.visual_facts.length > 0 ? "Yes" : "No";
      $("#perc-interval").textContent = (oldWorld.staleness_ms ?? "--") + "ms";
      $("#perc-buffer").textContent = (perc.event_count || 0) + " events";
      $("#perc-status").textContent = perc.scene_state || "unknown";
    }
  }

  // Operating mode sync (SAFE / HYBRID / RAW)
  const modeInfo = await apiGet("/operating/mode");
  if (modeInfo && modeInfo.mode) {
    currentOperatingMode = String(modeInfo.mode).toUpperCase();
    const modeSelect = $("#set-operating-mode");
    if (modeSelect && modeSelect.value !== currentOperatingMode) {
      modeSelect.value = currentOperatingMode;
    }
    const visionMode = $("#vision-mode");
    if (visionMode) visionMode.textContent = currentOperatingMode;
  }

  // Recent actions from audit
  const audit = await apiGet("/audit/recent?count=5");
  const entries = audit?.entries || [];
  if (audit && Array.isArray(entries)) {
    const container = $("#recent-actions");
    container.innerHTML = "";
    if (entries.length === 0) {
      container.innerHTML =
        '<div class="log-entry"><span class="log-detail" style="color:var(--text-muted)">No actions yet</span></div>';
    } else {
      entries.forEach((a) => {
        const entry = document.createElement("div");
        entry.className = "log-entry";
        const ts = a.timestamp || a.ts || a.created_at || 0;
        const success = a.status ? a.status === "success" : !!a.success;
        const time = ts
          ? new Date(ts * 1000).toLocaleTimeString()
          : "--:--";
        entry.innerHTML = `
          <span class="log-time">${escapeHtml(time)}</span>
          <span class="log-action">${escapeHtml(a.action || "?")}</span>
          <span class="log-status ${success ? "ok" : "fail"}">${success ? "OK" : "FAIL"}</span>
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
    if (data && data.image_base64) {
      const img = $("#vision-img");
      const placeholder = $("#vision-placeholder");
      const overlay = $("#vision-overlay");

      img.src = "data:image/webp;base64," + data.image_base64;
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

  const data = await apiPost("/action/execute", {
    instruction,
    verify: true,
    mode: getPreferredMode(),
  });

  if (data) {
    const actionResult = data.result || {};
    const ok = !!actionResult.success;
    result.style.color = ok ? "var(--green)" : "var(--red)";
    const mode = data.precheck?.mode || "SAFE";
    result.textContent = `[${mode}] ${actionResult.message || JSON.stringify(data)}`;
    addLog(actionResult.action || "agent", instruction, ok ? "ok" : "fail");
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
  vision_query: { prompt: "Ask a visual question:", param: "question" },
  grounding_resolve: { prompt: "Enter target query (optional: query|role):", param: "target" },
  grounded_click: { prompt: "Enter target query (optional: query|role):", param: "target" },
  grounded_type: { prompt: "Enter target and text (query: text):", param: "input" },
  file_read: { prompt: "Enter file path:", param: "path" },
  file_write: { prompt: "Enter file path:", param: "path" },
  file_list: { prompt: "Enter directory path:", param: "path" },
  file_search: { prompt: "Enter search pattern:", param: "pattern" },
  file_copy: { prompt: "Enter source and destination (src: dst):", param: "paths" },
};

function getPreferredMode() {
  const mode = $("#set-operating-mode")?.value || currentOperatingMode || "SAFE";
  return String(mode).toUpperCase();
}

function parseTargetAndRole(raw) {
  const value = String(raw || "");
  const [queryPart, rolePart] = value.split("|");
  return {
    query: (queryPart || "").trim(),
    role: (rolePart || "").trim() || undefined,
  };
}

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

      let data = null;
      try {
        // Explicit mapping for backend-compatible actions.
        if (action === "click" || action === "double_click" || action === "right_click") {
          const [x, y] = String(body.coordinates || "0,0").split(",").map((v) => parseInt(v.trim(), 10) || 0);
          if (action === "double_click") {
            data = await apiPost(`/action/double_click?x=${x}&y=${y}`);
          } else {
            const button = action === "right_click" ? "right" : "left";
            data = await apiPost(`/action/click?x=${x}&y=${y}&button=${button}`);
          }
        } else if (action === "scroll") {
          const amount = parseInt(body.amount, 10) || 0;
          data = await apiPost(`/action/scroll?amount=${amount}`);
        } else if (action === "typewrite") {
          data = await apiPost(`/action/type?text=${encodeURIComponent(body.text || "")}`);
        } else if (action === "press_key" || action === "hotkey") {
          const keys = encodeURIComponent(body.keys || body.key || "");
          data = await apiPost(`/action/hotkey?keys=${keys}`);
        } else if (action === "set_clipboard") {
          data = await apiPost(`/clipboard/write?text=${encodeURIComponent(body.text || "")}`);
        } else if (action === "click_element") {
          data = await apiPost(`/ui/click?name=${encodeURIComponent(body.selector || "")}`);
        } else if (action === "grounding_resolve") {
          const { query, role } = parseTargetAndRole(body.target);
          data = await apiPost("/grounding/resolve", {
            query,
            role,
            mode: getPreferredMode(),
            top_k: 5,
          });
          if (data && data.success !== undefined) {
            data.message = data.success
              ? `Resolved: ${data.target?.name || query} (${Math.round((data.target?.confidence || 0) * 100)}%)`
              : `Resolve blocked: ${data.reason || "unknown"}`;
          }
        } else if (action === "grounded_click") {
          const { query, role } = parseTargetAndRole(body.target);
          data = await apiPost("/grounding/click", {
            query,
            role,
            mode: getPreferredMode(),
            verify: true,
          });
          if (data && data.success !== undefined) {
            data.message = data.success
              ? "Grounded click executed"
              : `Grounded click blocked: ${data.message || data.grounding?.reason || "unknown"}`;
          }
        } else if (action === "grounded_type") {
          const raw = String(body.input || "");
          const sep = raw.indexOf(":");
          const left = sep >= 0 ? raw.slice(0, sep) : raw;
          const text = sep >= 0 ? raw.slice(sep + 1).trim() : "";
          const { query, role } = parseTargetAndRole(left);
          data = await apiPost("/grounding/type", {
            query,
            text,
            role: role || "textfield",
            mode: getPreferredMode(),
            verify: true,
          });
          if (data && data.success !== undefined) {
            data.message = data.success
              ? "Grounded type executed"
              : `Grounded type blocked: ${data.message || data.grounding?.reason || "unknown"}`;
          }
        } else if (action === "find_element") {
          data = await apiGet(`/ui/find?name=${encodeURIComponent(body.query || "")}`);
          if (data?.element) data.success = true;
        } else if (action === "browser_navigate") {
          data = await apiPost(`/browser/navigate?url=${encodeURIComponent(body.url || "")}`);
        } else if (action === "vision_query") {
          data = await apiPost("/perception/query", {
            question: String(body.question || "").trim(),
            window_seconds: 30,
          });
          if (data?.answer) {
            data.success = true;
            data.message = `${data.answer} (conf: ${Math.round((data.confidence || 0) * 100)}%)`;
          }
        } else if (action === "terminal_run") {
          data = await apiPost(`/terminal/exec?cmd=${encodeURIComponent(body.command || "")}&timeout=30`);
        } else {
          // Generic fallback through intent pipeline
          const instruction = `${action}${Object.keys(body).length ? " " + JSON.stringify(body) : ""}`;
          data = await apiPost("/action/execute", {
            instruction,
            verify: true,
            mode: getPreferredMode(),
          });
          if (data?.result) {
            data.success = !!data.result.success;
            data.message = data.result.message || "";
          }
        }
      } catch {
        data = null;
      }
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
  initOnboarding();
  initNav();
  initActionButtons();
  loadDesktopSettings().then(() => refreshRuntimeStatus());
  startRuntimePolling();

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
    const previousPort = Number(
      desktopSettings?.api_port ?? DEFAULT_DESKTOP_SETTINGS.api_port
    );
    const settings = readDesktopSettingsFromUI();
    let effectiveSettings = { ...settings };

    if (isTauri && invoke) {
      try {
        const persisted = await invoke("save_desktop_settings", { settings });
        desktopSettings = { ...DEFAULT_DESKTOP_SETTINGS, ...(persisted || settings) };
        effectiveSettings = { ...desktopSettings };
        applyDesktopSettingsToUI(desktopSettings);
      } catch (e) {
        addLog("settings", `Desktop save failed: ${e?.message || e}`, "fail");
      }
    } else {
      desktopSettings = { ...DEFAULT_DESKTOP_SETTINGS, ...settings };
      effectiveSettings = { ...desktopSettings };
    }

    const newPort = Number(
      effectiveSettings?.api_port ?? DEFAULT_DESKTOP_SETTINGS.api_port
    );
    const portChanged = previousPort !== newPort;

    if (portChanged && serverOnline && isTauri && invoke) {
      addLog(
        "settings",
        `Port changed (${previousPort} → ${newPort}), restarting server...`,
        "warn"
      );
      try {
        await invoke("stop_server");
        await new Promise((r) => setTimeout(r, 300));
        await invoke("start_server");
        await new Promise((r) => setTimeout(r, 700));
      } catch (e) {
        addLog("settings", `Port restart failed: ${e?.message || e}`, "fail");
      }
    }

    const interval = Math.max(100, effectiveSettings.capture_interval_ms || 1000);
    const fps = +(1000 / interval).toFixed(2);
    const mode = getPreferredMode();

    // Live backend settings (subset supported hot-reload).
    const result = await apiPost(`/config?fps=${fps}`);
    const modeResult = await apiPost(`/operating/mode?mode=${encodeURIComponent(mode)}`);

    if (result && modeResult?.mode) {
      currentOperatingMode = String(modeResult.mode).toUpperCase();
      addLog(
        "settings",
        `Saved (${effectiveSettings.vision_profile}${effectiveSettings.vlm_enabled ? " + VLM" : ""})`,
        "ok"
      );
    } else {
      addLog("settings", "Saved local profile; backend not reachable", "warn");
    }
  };
  const saveBtn = $("#btn-save-settings");
  if (saveBtn) saveBtn.addEventListener("click", saveSettings);

  const runtimeBtn = $("#btn-runtime-bootstrap");
  if (runtimeBtn) {
    runtimeBtn.addEventListener("click", async () => {
      const wantsVlm = !!$("#set-vlm-enabled")?.checked;
      await bootstrapRuntime(wantsVlm);
    });
  }

  const dlBtn = $("#btn-vlm-download");
  if (dlBtn) dlBtn.addEventListener("click", startVlmDownload);

  // Logout button
  const logoutBtn = $("#btn-logout");
  if (logoutBtn) logoutBtn.addEventListener("click", logout);

  // Init new cards
  initApiKeyCard();
  initTokenCard();

  // Load session into API key card on startup
  const startSession = getSession();
  if (startSession) updateApiKeyCard(startSession);

  // J6: Pause/resume polling when window is hidden
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopPolling();
      stopVisionPolling();
      stopTokenPolling();
      stopRuntimePolling();
    } else if (serverOnline) {
      startPolling();
      startVisionPolling();
      startTokenPolling();
      startRuntimePolling();
    } else {
      startRuntimePolling();
    }
  });

  // Server does NOT start until login — see startServerAfterLogin()
  // Only start polling/server if already logged in (session restored)
  const existingSession = getSession();
  if (existingSession && existingSession.api_key) {
    startServerAfterLogin();
  }

  // Check for plan upgrades every 60s (detects payment)
  setInterval(checkPlanUpgrade, 60000);
}

// Start
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
