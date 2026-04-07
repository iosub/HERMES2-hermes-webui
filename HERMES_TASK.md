# Hermes WebUI - Production Readiness Project

## Project

**Repo:** hermes-webui  
**Goal:** Apply production-readiness audit in small, safe, minimal patches

## Working Rules

- Work only inside this repo
- Keep patches minimal and localized
- Do not rewrite architecture
- Do not add unrelated cleanup
- One fix batch at a time
- After each batch, always report:
  1. Files changed
  2. Exact code changes
  3. Why the change fixes the issue
  4. Assumptions or remaining risks
  5. Manual test checklist
  6. Unified diff

## Completed

### Phase 1: Security & Infrastructure
- [x] **Fix 1:** Create requirements.txt
- [x] **Fix 2:** Authentication (token-based, all API routes)
- [x] **Fix 3:** XSS hardening in chat rendering
- [x] **Fix 4:** Path traversal hardening for uploads

### Phase 2: Stability
- [x] **Fix 5:** Production server / startup hardening (gunicorn)
- [x] **Fix 6:** Basic structured logging
- [x] **Fix 7:** Simple in-memory rate limiting

### Phase 3: UX/Feature Improvements
- [x] **Fix 8:** Remove non-functional Plugins menu item
- [x] **Fix 9:** Remove non-functional model selector from chat
- [x] **Fix 10:** Remove non-functional global search bar
- [x] **Fix 11:** Remove non-functional chat mode tabs
- [x] **Fix 12:** Frontend authentication - token handling in browser
- [x] **Fix 13:** Update Hermes executable and sessions paths to current layout
- [x] **Fix 14:** Fix chat history header layout (prevent button cutoff)
- [x] **Fix 15:** Preserve existing provider/config secrets when masked values are resubmitted
- [x] **Fix 16:** Show honest gateway stopped-state and transport fallback messaging in the UI
- [x] **Fix 17:** Extend smoke coverage for chat continuity, service controls, uploads, and broken UI literals
- [x] **Fix 18:** Replace leaked Unicode codepoint literals that rendered raw `U000...` text in the UI

## Status

**Production Readiness: Deployable With Configuration Checks**

Core repo-side hardening, honest UI behavior, and deployment docs are in place. Image chat works through the Hermes vision/API path when a real provider, API URL, model, and key are configured correctly, so the remaining risk is mostly environment configuration and external service readiness rather than missing repo plumbing.

## Remaining Follow-Ups

- Verify real deployment provider settings before treating screenshot paste as ready in a given environment
- Expand browser automation for full chat/send/upload flows when a stable seeded local app state is available
- Keep local runtime artifacts (`run/`, `.codex`) ignored so future publish steps stay clean

---

## Exact Fix 1 Requirement

Create `requirements.txt` with:

```
flask>=3.0,<4.0
flask-cors>=4.0,<5.0
python-dotenv>=1.0,<2.0
pyyaml>=6.0,<7.0
gunicorn>=21.0,<23.0
```

Also include any directly imported dependency already used in code if missing.

**Do not change any other file for Fix 1.**

---

## Session Start Instruction

Read this file first. Then implement only the next unchecked item.
