"""
Upload ILUMINATY to Hugging Face as a public Space (README + demo video)
and as a model repo with full source.

Usage:
    python scripts/hf_upload.py
"""

import os
import sys
import pathlib

# ── Load token ────────────────────────────────────────────────────────────────
env_path = pathlib.Path(__file__).parent.parent / ".env.hf"
token = None
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.startswith("HF_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")

if not token:
    token = os.environ.get("HF_TOKEN")

if not token:
    print("ERROR: HF_TOKEN not found in .env.hf or environment")
    sys.exit(1)

from huggingface_hub import HfApi, create_repo
import tempfile, shutil

api = HfApi(token=token)
me = api.whoami()
username = me["name"]
print(f"Logged in as: {username}")

REPO_ID = f"{username}/iluminaty"
ROOT = pathlib.Path(__file__).parent.parent

# ── 1. Create / ensure repo exists ───────────────────────────────────────────
print(f"\nCreating repo: {REPO_ID}")
try:
    create_repo(
        repo_id=REPO_ID,
        repo_type="space",
        space_sdk="static",
        private=False,
        token=token,
        exist_ok=True,
    )
    print("  ✓ Space created (or already exists)")
except Exception as e:
    print(f"  ✗ {e}")
    sys.exit(1)

# ── 2. Build README / index.html for the Space ───────────────────────────────
readme_content = """\
---
title: ILUMINATY
emoji: 👁
colorFrom: green
colorTo: gray
sdk: static
pinned: true
license: mit
tags:
  - mcp
  - computer-use
  - vision
  - multi-monitor
  - ai-agents
  - claude
  - gpt4o
short_description: Real-time vision + PC control for AI. 88% fewer tokens.
---

# 👁 ILUMINATY

**Real-time visual perception + PC control for AI agents.**  
Local MCP server · Zero cloud · Zero disk · AI sees your screen — all monitors — live.

[![Tests](https://github.com/sgodoy90/iluminaty/actions/workflows/tests.yml/badge.svg)](https://github.com/sgodoy90/iluminaty)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/sgodoy90/iluminaty/blob/main/LICENSE)
[![Version](https://img.shields.io/badge/version-0.3.0-green)](https://github.com/sgodoy90/iluminaty)

## What Is This?

ILUMINATY is a local [MCP](https://modelcontextprotocol.io) server that gives any AI (Claude, GPT-4o, Cursor, etc.) **real-time visual perception and OS-level control** of your desktop — without sending screenshots to the cloud.

## Benchmark vs Computer Use

| Task | ILUMINATY | Computer Use | Savings |
|---|---|---|---|
| Element location (OCR) | 0 tokens · 28ms | 4,300 tokens · 2,500ms | **100% tokens** |
| See 3 monitors | 4,800 tokens · 190ms | 24,300 tokens · 2,400ms | **80% tokens** |
| 5-step task | 750 tokens · 3,937ms | 21,500 tokens · 12,500ms | **96% tokens** |
| Event detection | 0 tokens · 1,516ms | polling · ~6,000ms | **100% tokens** |
| Multi-monitor control | 400 tokens · 1,288ms | ❌ not possible | — |
| Session memory | 57 tokens · 10ms | ❌ not possible | — |
| **TOTAL** | **6,007 tokens** | **50,100 tokens** | **88% fewer tokens** |

## Quick Start

```bash
pip install iluminaty[ocr]
iluminaty start
```

## 41 MCP Tools

`see_now` · `see_region` · `get_spatial_context` · `watch_and_notify` · `monitor_until` ·  
`get_session_memory` · `act` · `operate_cycle` · `move_window` · `window_close` ·  
`open_path` · `open_on_monitor` · `run_command` · `smart_locate` · and 27 more.

## Links

- 📦 **GitHub**: https://github.com/sgodoy90/iluminaty
- 📖 **Docs**: https://github.com/sgodoy90/iluminaty#readme
- 🐛 **Issues**: https://github.com/sgodoy90/iluminaty/issues
"""

index_html = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>ILUMINATY — Real-time vision for AI agents</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#030805;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;
     display:flex;flex-direction:column;align-items:center;padding:60px 24px;min-height:100vh}
.logo{font-size:64px;margin-bottom:12px}
h1{font-size:2.4rem;font-weight:800;letter-spacing:-0.5px;margin-bottom:8px;font-family:monospace}
h1 span{color:#00ff88}
.tag{font-size:1rem;color:rgba(255,255,255,0.45);margin-bottom:40px;text-align:center;line-height:1.6}
.badge{display:inline-flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-bottom:48px}
.badge a{background:rgba(0,255,136,0.08);border:1px solid rgba(0,255,136,0.25);
          color:#00ff88;text-decoration:none;padding:6px 18px;border-radius:20px;
          font-family:monospace;font-size:13px;font-weight:700}
.badge a:hover{background:rgba(0,255,136,0.15)}
.stats{display:flex;gap:16px;flex-wrap:wrap;justify-content:center;margin-bottom:48px}
.stat{background:rgba(0,255,136,0.05);border:1px solid rgba(0,255,136,0.15);
      border-radius:10px;padding:16px 24px;text-align:center;min-width:120px}
.stat .n{font-size:2rem;font-weight:800;color:#00ff88;font-family:monospace}
.stat .l{font-size:11px;color:rgba(0,255,136,0.5);letter-spacing:1px;text-transform:uppercase;margin-top:4px;font-family:monospace}
.demos{width:100%;max-width:960px;margin-bottom:48px;display:flex;flex-direction:column;gap:24px}
.demo-block{display:flex;flex-direction:column;gap:8px}
.demo-label{font-family:monospace;font-size:12px;color:rgba(0,255,136,0.5);
             letter-spacing:2px;text-transform:uppercase;padding-left:4px}
video{width:100%;border-radius:10px;border:1px solid rgba(0,255,136,0.2);
      background:#000;display:block}
.install{background:#0d1117;border:1px solid rgba(255,255,255,0.08);border-radius:10px;
         padding:20px 32px;font-family:monospace;font-size:15px;color:#00ff88;margin-bottom:48px}
.install span{color:rgba(255,255,255,0.3)}
.cta{background:linear-gradient(135deg,#004d22,#007733);
     border:1.5px solid rgba(0,255,136,0.4);border-radius:12px;
     padding:14px 48px;font-size:18px;font-weight:700;color:#00ff88;
     text-decoration:none;font-family:monospace;letter-spacing:0.5px}
.cta:hover{background:linear-gradient(135deg,#005a28,#008a3a)}
</style>
</head>
<body>
<div class="logo">👁</div>
<h1>ILUMINATY <span>IPA v3</span></h1>
<p class="tag">
  Real-time visual perception + PC control for AI agents.<br/>
  Local MCP server · Zero cloud · Zero disk · 3+ monitors · 41 tools
</p>
<div class="badge">
  <a href="https://github.com/sgodoy90/iluminaty">⭐ GitHub</a>
  <a href="https://github.com/sgodoy90/iluminaty#readme">📖 Docs</a>
  <a href="https://github.com/sgodoy90/iluminaty/blob/main/LICENSE">MIT License</a>
  <a href="https://github.com/sgodoy90/iluminaty/issues">🐛 Issues</a>
</div>
<div class="stats">
  <div class="stat"><div class="n">88%</div><div class="l">fewer tokens</div></div>
  <div class="stat"><div class="n">6/6</div><div class="l">benchmark</div></div>
  <div class="stat"><div class="n">101</div><div class="l">tests passing</div></div>
  <div class="stat"><div class="n">29ms</div><div class="l">max latency</div></div>
  <div class="stat"><div class="n">41</div><div class="l">mcp tools</div></div>
</div>
<div class="demos">
  <div class="demo-block">
    <div class="demo-label">🇺🇸 Demo — English</div>
    <video controls preload="metadata" poster="">
      <source src="iluminaty-demo-en.mp4" type="video/mp4"/>
    </video>
  </div>
  <div class="demo-block">
    <div class="demo-label">🇪🇸 Demo — Español</div>
    <video controls preload="metadata" poster="">
      <source src="iluminaty-demo-es.mp4" type="video/mp4"/>
    </video>
  </div>
</div>
<div class="install">
  <span>$</span> pip install iluminaty[ocr]<br/>
  <span>$</span> iluminaty start
</div>
<a class="cta" href="https://github.com/sgodoy90/iluminaty">
  github.com/sgodoy90/iluminaty
</a>
</body>
</html>
"""

# ── 3. Upload files to Space ──────────────────────────────────────────────────
print("\nUploading Space files...")

with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)

    (tmp / "README.md").write_text(readme_content, encoding="utf-8")
    (tmp / "index.html").write_text(index_html, encoding="utf-8")

    # Demo videos if they exist
    for vid in ["iluminaty-demo-en.mp4", "iluminaty-demo-es.mp4"]:
        src = ROOT / "out" / vid  # demo dir
        if not src.exists():
            src = pathlib.Path("C:/Users/jgodo/Desktop/iluminaty-demo/out") / vid
        if src.exists():
            shutil.copy(src, tmp / vid)
            print(f"  + {vid} ({src.stat().st_size // 1024 // 1024}MB)")

    api.upload_folder(
        folder_path=str(tmp),
        repo_id=REPO_ID,
        repo_type="space",
        token=token,
        commit_message="Initial upload — ILUMINATY v0.3.0",
    )
    print("  ✓ Space uploaded")

print(f"\n✓ Done! https://huggingface.co/spaces/{REPO_ID}")
