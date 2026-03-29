# ILUMINATY - Extended Capability Roadmap
## "What AI actually needs to truly see"

---

## E01: Proactive Watchdog Mode
> The AI doesn't wait to be asked — it watches and alerts

```
Instead of:  User: "what's on my screen?"  →  AI: "I see..."
Now:         AI: "Hey, your build just failed. Error on line 42."
```

- [ ] Event detection engine: watch for patterns in screen changes
- [ ] Configurable triggers: "alert me when terminal shows ERROR"
- [ ] Pattern library: build failures, crash dialogs, security warnings
- [ ] Webhook/callback system: push events to any AI or service
- [ ] Quiet mode: only alert on high-priority events
- **Market value**: This is what makes ILUMINATY indispensable.
  The AI becomes a co-pilot that's always watching.

---

## E02: Spatial Layout Map
> The AI knows WHERE things are, not just WHAT things are

```
Current:  "I see some code and a terminal"
With E02: "VS Code is in the top-left (60% of screen), 
           terminal is bottom (30%), browser sidebar right (10%)"
```

- [ ] Region detection: divide screen into semantic zones
- [ ] Zone naming: "editor", "terminal", "sidebar", "browser"
- [ ] Zone tracking: persist layout across frames
- [ ] Natural language positioning: "the error is in the bottom panel"
- [ ] Multi-window spatial awareness
- **Market value**: Makes AI instructions 10x more precise.
  "Fix the error in the bottom terminal" just works.

---

## E03: Action Bridge (Computer Use)
> The AI can SEE and now also ACT

```
Current:  AI sees a bug → tells you how to fix it
With E03: AI sees a bug → clicks the button → fixes it
```

- [ ] Coordinate mapping: frame pixels → screen pixels
- [ ] Click injection: simulate mouse clicks at coordinates
- [ ] Keyboard injection: type text into active window
- [ ] Action safety: require confirmation for destructive actions
- [ ] Action recording: log what the AI did for audit
- [ ] Integration with existing tools: pyautogui, accessibility APIs
- **Market value**: This is the holy grail. See + Act = autonomous agent.
  Every AI company wants this. Few have it working reliably.

---

## E04: User Profile Learning
> The AI remembers your preferences across sessions

```
Session 1: User prefers dark mode, codes in Python, uses VS Code
Session 2: AI already knows this without asking
```

- [ ] Preference detection: themes, tools, languages, workflows
- [ ] Persistent profile (encrypted, local-only)
- [ ] Pattern learning: "user always checks email first thing"
- [ ] Custom vocabulary: project names, team members, tools
- [ ] Privacy controls: user can delete/edit any learned data
- **Market value**: Personalization is what makes AI sticky.
  The AI that knows you is the AI you keep using.

---

## E05: Multi-Modal Fusion
> Combine vision + audio + context into unified perception

```
Current:  Image + OCR text + audio transcript (separate)
With E05: "User is in a Zoom meeting discussing the Q3 budget.
           The spreadsheet on screen shows revenue at $2.3M.
           Sarah just asked about hiring timeline."
```

- [ ] Temporal alignment: sync video frames with audio transcript
- [ ] Cross-modal reasoning: what's said relates to what's shown
- [ ] Meeting intelligence: who said what, about which slide
- [ ] Code review mode: comments relate to visible code
- [ ] Teaching mode: instructor narrates while showing screen
- **Market value**: This is how humans perceive — everything at once.
  First AI tool to fuse all modalities wins.

---

## Vertical Products (built on ILUMINATY core)

### Product 1: ILUMINATY QA
"AI that watches your app and finds visual bugs"
- Monitors web apps in real-time
- Detects layout shifts, broken elements, console errors
- Generates bug reports with annotated screenshots
- Compares against design mockups
- **Price**: $99/month per project

### Product 2: ILUMINATY Meetings
"AI meeting assistant that sees your screen and hears your call"
- Auto-detects when you join a meeting
- Captures screen + audio in RAM (zero disk)
- Generates meeting summary with action items
- Detects who presented what (screen changes + speaker)
- **Price**: $29/user/month

### Product 3: ILUMINATY Dev
"AI pair programmer that sees your IDE in real-time"
- Watches VS Code / terminal / browser
- Detects errors before you see them
- Suggests fixes based on visible code + error messages
- Tracks your coding session: time, focus, productivity
- **Price**: $19/month

### Product 4: ILUMINATY Guard
"Compliance monitor for regulated industries"
- Detects access to sensitive data on screen
- Auto-blurs and logs without storing images
- Generates compliance reports
- Alerts on policy violations
- **Price**: $499/month per team

---

## SaaS API Platform

### Developer API
```
POST https://api.iluminaty.dev/v1/frame/analyze
{
  "image": "<base64>",
  "capabilities": ["ocr", "diff", "context"],
  "provider": "gemini"
}

Response:
{
  "ocr_text": "...",
  "layout": { "zones": [...] },
  "context": { "workflow": "coding", "app": "VS Code" },
  "ai_response": "I see a syntax error on line 42..."
}
```

### Pricing
| Tier | Price | Includes |
|---|---|---|
| Free | $0 | 1,000 frames/month, OCR only |
| Pro | $29/month | 50,000 frames, all capabilities |
| Business | $99/month | 500,000 frames, priority, SLA |
| Enterprise | Custom | Unlimited, on-prem, SOC2 |
