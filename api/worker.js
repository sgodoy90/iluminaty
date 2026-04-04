/**
 * ILUMINATY Auth API — Cloudflare Worker
 * ========================================
 * Handles user registration, login, API key validation,
 * and Lemon Squeezy billing webhooks.
 *
 * Deploy: wrangler deploy
 * Binds to: api.iluminaty.dev
 *
 * D1 Database tables (run schema.sql first):
 *   users     — id, email, name, password_hash, plan, created_at
 *   api_keys  — id, user_id, key, plan, created_at
 *   usage     — user_id, date, action_count, mcp_count
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

function error(msg, status = 400) {
  return json({ error: msg }, status);
}

// Simple password hashing using Web Crypto (not bcrypt but fine for v1)
async function hashPassword(password) {
  const encoder = new TextEncoder();
  const data = encoder.encode(password + "iluminaty-salt-v1");
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function generateApiKey(plan) {
  // Format: ILUM-{plan}-{8chars}-{8chars}-{8chars}
  // licensing.py checks for "ILUM-" prefix when server unreachable (gives PRO benefit of doubt)
  const prefix = `ILUM-${plan}`;
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  const seg = () => Array.from({ length: 8 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
  return `${prefix}-${seg()}-${seg()}-${seg()}`;
}

// ─── Route handlers ───

async function handleRegister(request, env) {
  const { email, password, name } = await request.json();

  if (!email || !password) return error("Email and password required");
  if (password.length < 6) return error("Password must be at least 6 characters");

  const existing = await env.DB.prepare("SELECT id FROM users WHERE email = ?")
    .bind(email.toLowerCase())
    .first();
  if (existing) return error("Email already registered", 409);

  const passwordHash = await hashPassword(password);
  const userId = crypto.randomUUID();
  const apiKey = generateApiKey("free");

  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO users (id, email, name, password_hash, plan, created_at) VALUES (?, ?, ?, ?, ?, ?)"
    ).bind(userId, email.toLowerCase(), name || email.split("@")[0], passwordHash, "free", new Date().toISOString()),
    env.DB.prepare(
      "INSERT INTO api_keys (id, user_id, key, plan, created_at) VALUES (?, ?, ?, ?, ?)"
    ).bind(crypto.randomUUID(), userId, apiKey, "free", new Date().toISOString()),
  ]);

  return json({
    user: { id: userId, email: email.toLowerCase(), name: name || email.split("@")[0], plan: "free" },
    api_key: apiKey,
  });
}

async function handleLogin(request, env) {
  const { email, password } = await request.json();
  if (!email || !password) return error("Email and password required");

  const passwordHash = await hashPassword(password);
  const user = await env.DB.prepare(
    "SELECT id, email, name, plan FROM users WHERE email = ? AND password_hash = ?"
  )
    .bind(email.toLowerCase(), passwordHash)
    .first();

  if (!user) return error("Invalid email or password", 401);

  const keyRow = await env.DB.prepare(
    "SELECT key, plan FROM api_keys WHERE user_id = ? ORDER BY created_at DESC LIMIT 1"
  )
    .bind(user.id)
    .first();

  return json({
    user: { id: user.id, email: user.email, name: user.name, plan: user.plan },
    api_key: keyRow?.key || null,
  });
}

async function handleGoogleAuth(request, env) {
  const { id_token } = await request.json();
  if (!id_token) return error("id_token required");

  // Verify Google token
  const googleResp = await fetch(
    `https://oauth2.googleapis.com/tokeninfo?id_token=${id_token}`
  );
  if (!googleResp.ok) return error("Invalid Google token", 401);

  const google = await googleResp.json();
  const email = google.email;
  const name = google.name || email.split("@")[0];

  // Check if user exists
  let user = await env.DB.prepare("SELECT id, email, name, plan FROM users WHERE email = ?")
    .bind(email.toLowerCase())
    .first();

  if (!user) {
    // Auto-register
    const userId = crypto.randomUUID();
    const apiKey = generateApiKey("free");
    await env.DB.batch([
      env.DB.prepare(
        "INSERT INTO users (id, email, name, password_hash, plan, created_at) VALUES (?, ?, ?, ?, ?, ?)"
      ).bind(userId, email.toLowerCase(), name, "google-oauth", "free", new Date().toISOString()),
      env.DB.prepare(
        "INSERT INTO api_keys (id, user_id, key, plan, created_at) VALUES (?, ?, ?, ?, ?)"
      ).bind(crypto.randomUUID(), userId, apiKey, "free", new Date().toISOString()),
    ]);
    user = { id: userId, email: email.toLowerCase(), name, plan: "free" };
  }

  const keyRow = await env.DB.prepare(
    "SELECT key, plan FROM api_keys WHERE user_id = ? ORDER BY created_at DESC LIMIT 1"
  )
    .bind(user.id)
    .first();

  return json({
    user: { id: user.id, email: user.email, name: user.name, plan: user.plan },
    api_key: keyRow?.key || null,
  });
}

async function handleValidate(request, env) {
  const authHeader = request.headers.get("Authorization");
  const apiKey = authHeader?.replace("Bearer ", "").trim();
  if (!apiKey) return error("API key required (Authorization: Bearer ILUM-xxx)", 401);

  const row = await env.DB.prepare(
    `SELECT ak.key, ak.plan, u.id as user_id, u.email, u.name, u.plan as user_plan
     FROM api_keys ak JOIN users u ON ak.user_id = u.id
     WHERE ak.key = ?`
  )
    .bind(apiKey)
    .first();

  if (!row) return error("Invalid API key", 401);

  // Use the user's current plan (may have been upgraded)
  const plan = row.user_plan;

  // Track usage
  const today = new Date().toISOString().slice(0, 10);
  await env.DB.prepare(
    `INSERT INTO usage (user_id, date, action_count, mcp_count)
     VALUES (?, ?, 0, 0)
     ON CONFLICT (user_id, date) DO NOTHING`
  )
    .bind(row.user_id, today)
    .run();

  const limits = {
    custom: { act_actions: 7, mcp_tools: 64, rate_limit: null },
    pro:    { act_actions: 7, mcp_tools: 64, rate_limit: null },
    free:   { act_actions: 7, mcp_tools: 24, rate_limit: null },
  }[plan] || { act_actions: 0, mcp_tools: 10, rate_limit: "demo" };

  return json({
    valid: true,
    plan,
    user: { id: row.user_id, email: row.email, name: row.name },
    limits,
  });
}

async function handleUsage(request, env) {
  const authHeader = request.headers.get("Authorization");
  const apiKey = authHeader?.replace("Bearer ", "").trim();
  if (!apiKey) return error("API key required", 401);

  const keyRow = await env.DB.prepare(
    "SELECT user_id FROM api_keys WHERE key = ?"
  )
    .bind(apiKey)
    .first();
  if (!keyRow) return error("Invalid API key", 401);

  const rows = await env.DB.prepare(
    "SELECT date, action_count, mcp_count FROM usage WHERE user_id = ? ORDER BY date DESC LIMIT 30"
  )
    .bind(keyRow.user_id)
    .all();

  return json({ usage: rows.results || [] });
}

async function handleUpgrade(request, env) {
  // Free upgrade from free → pro (no payment required)
  const authHeader = request.headers.get("Authorization");
  const apiKey = authHeader?.replace("Bearer ", "").trim();
  if (!apiKey) return error("API key required", 401);

  const row = await env.DB.prepare(
    "SELECT user_id FROM api_keys WHERE key = ?"
  ).bind(apiKey).first();
  if (!row) return error("Invalid API key", 401);

  await env.DB.batch([
    env.DB.prepare("UPDATE users SET plan = 'pro' WHERE id = ?").bind(row.user_id).run(),
    env.DB.prepare("UPDATE api_keys SET plan = 'pro' WHERE user_id = ?").bind(row.user_id).run(),
  ]);

  return json({ ok: true, plan: "pro", message: "Upgraded to Pro. All 64 MCP tools unlocked." });
}

async function handleLemonWebhook(request, env) {
  // Lemon Squeezy sends webhook on subscription events
  const body = await request.json();
  const event = body.meta?.event_name;
  const email = body.data?.attributes?.user_email;

  if (!email) return error("No email in webhook");

  if (event === "subscription_created" || event === "subscription_resumed") {
    // Upgrade to pro
    await env.DB.prepare("UPDATE users SET plan = 'pro' WHERE email = ?")
      .bind(email.toLowerCase())
      .run();
    await env.DB.prepare(
      "UPDATE api_keys SET plan = 'pro' WHERE user_id = (SELECT id FROM users WHERE email = ?)"
    )
      .bind(email.toLowerCase())
      .run();
    return json({ ok: true, action: "upgraded_to_pro" });
  }

  if (event === "subscription_cancelled" || event === "subscription_expired") {
    // Downgrade to free
    await env.DB.prepare("UPDATE users SET plan = 'free' WHERE email = ?")
      .bind(email.toLowerCase())
      .run();
    await env.DB.prepare(
      "UPDATE api_keys SET plan = 'free' WHERE user_id = (SELECT id FROM users WHERE email = ?)"
    )
      .bind(email.toLowerCase())
      .run();
    return json({ ok: true, action: "downgraded_to_free" });
  }

  return json({ ok: true, action: "ignored", event });
}

async function handleMe(request, env) {
  const authHeader = request.headers.get("Authorization");
  const apiKey = authHeader?.replace("Bearer ", "").trim();
  if (!apiKey) return error("API key required", 401);

  const row = await env.DB.prepare(
    `SELECT u.id, u.email, u.name, u.plan, u.created_at,
            ak.key as api_key
     FROM api_keys ak JOIN users u ON ak.user_id = u.id
     WHERE ak.key = ?`
  )
    .bind(apiKey)
    .first();

  if (!row) return error("Invalid API key", 401);

  const today = new Date().toISOString().slice(0, 10);
  const usage = await env.DB.prepare(
    "SELECT action_count, mcp_count FROM usage WHERE user_id = ? AND date = ?"
  )
    .bind(row.id, today)
    .first();

  return json({
    user: {
      id: row.id,
      email: row.email,
      name: row.name,
      plan: row.plan,
      created_at: row.created_at,
    },
    api_key: row.api_key,
    today_usage: {
      actions: usage?.action_count || 0,
      mcp_calls: usage?.mcp_count || 0,
    },
    limits: {
      custom: { act_actions: 7, mcp_tools: 64, rate_limit: null },
      pro:    { act_actions: 7, mcp_tools: 64, rate_limit: null },
      free:   { act_actions: 7, mcp_tools: 24, rate_limit: null },
    }[row.plan] || { act_actions: 0, mcp_tools: 10, rate_limit: "demo" },
  });
}

// ─── Router ───

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    try {
      // Auth routes
      if (path === "/auth/register" && request.method === "POST") return handleRegister(request, env);
      if (path === "/auth/login" && request.method === "POST") return handleLogin(request, env);
      if (path === "/auth/google" && request.method === "POST") return handleGoogleAuth(request, env);
      if (path === "/auth/validate" && request.method === "GET") return handleValidate(request, env);
      if (path === "/auth/me" && request.method === "GET") return handleMe(request, env);
      if (path === "/auth/usage" && request.method === "GET") return handleUsage(request, env);
      if (path === "/auth/upgrade" && request.method === "POST") return handleUpgrade(request, env);

      // Billing webhook
      if (path === "/billing/webhook" && request.method === "POST") return handleLemonWebhook(request, env);

      // Health check
      if (path === "/health") return json({ status: "ok", service: "iluminaty-auth", version: "1.0.0" });

      return error("Not found", 404);
    } catch (err) {
      return error(err.message || "Internal error", 500);
    }
  },
};
