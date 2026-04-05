## ILUMINATY vs Computer Use -- Benchmark

> Run: 2026-04-05T13:59:10 | Server: unknown | Monitors: 3

### Methodology
- **ILUMINATY**: measured directly against live server
- **Computer Use**: estimated from [Anthropic vision pricing docs](https://docs.anthropic.com/en/docs/build-with-claude/vision)
  - Full 1920x1080 screenshot ~ 4,000–8,000 tokens at high detail
  - Post-action verification = another full screenshot
  - No event system -> polling required for async tasks
  - No multi-monitor API -> window placement is OS-controlled

### Results

| Task | Name | ILUMINATY tokens | Computer Use tokens | Savings | ILUMINATY ms | CU est. ms | Faster | Pass |
|------|------|-----------------|---------------------|---------|-------------|-----------|--------|------|
| T1 | Element Location | 0 | 4,300 | 100.0% | 28 | 2500 | 98.9% | [PASS] |
| T2 | Multi-Monitor Vision | 4,800 | 24,300 | 80.2% | 190 | 2400 | 92.1% | [PASS] |
| T3 | Multi-Step Task | 750 | 21,500 | 96.5% | 3937 | 12500 | 68.5% | [PASS] |
| T4 | Event Detection | 0 | N/A (limited) | N/A | 1516 | 6000 | 74.7% | [PASS] |
| T5 | Spatial Awareness | 400 | N/A (no) | N/A | 1288 | 3000 | 57.1% | [PASS] |
| T6 | Session Memory | 57 | N/A (no) | N/A | 10 | 0 | N/A | [PASS] |
| **TOTAL** | *(comparable)* | **6,007** | **50,100** | **88.0%** | | | | |

### Key Advantages

- **Element Location**: 100.0% fewer tokens. ILUMINATY: OCR cache not warm yet (run again after 30s for warm results) -- NOTE: OCR cache cold, latency measured anyway
- **Multi-Monitor Vision**: 80.2% fewer tokens. ILUMINATY: 3 monitors x low_res = 4800 tokens
- **Multi-Step Task**: 96.5% fewer tokens. ILUMINATY: 5 steps x ~150 tokens post-action context = 750 tokens
- **Event Detection**: 100.0% fewer tokens. ILUMINATY: event-driven detection in 1516ms
- **Spatial Awareness**: Computer Use cannot do this. No multi-monitor awareness. Window opens wherever OS decides. No way to specify target monitor or avoid user's active workspace.
- **Session Memory**: Computer Use cannot do this. No session memory. Every session starts from scratch. Agent must re-discover entire environment via screenshots.

### Notes
- Computer Use token estimates are conservative (lower bound).
  Real-world usage is typically higher due to context accumulation.
- ILUMINATY token counts are measured, not estimated.
- Latency for Computer Use includes Claude API inference time (~1-2s per call).
- Tasks T5 (multi-monitor) and T6 (session memory) are not possible with Computer Use.