# app.py Refactor Plan

## Goal

Split the current Flask backend into smaller, responsibility-focused modules without changing external behavior, breaking the smoke tests, or forcing a large one-shot rewrite.

## Progress Checklist

Use this checklist as the working tracker for the refactor.

## Working Agreement

This refactor will follow a strict step-by-step workflow:

1. Complete one checklist item or one tightly related mini-step.
2. Stop and let the user test that specific change.
3. Only after the user confirms the test is good, create the commit.
4. Push immediately after that commit.

Rules:

- Do not batch multiple untested refactor items into a single commit.
- Do not continue to the next implementation step before user validation of the current one.
- Keep each commit scoped to the single item that was just tested.
- Update this checklist after the tested item is confirmed and committed.

Manual validation routine for each test step:

1. Refresh the integrated browser before starting the check.
2. Log in through the UI with the admin account before validating the current item.
3. Use the integrated browser as the default manual validation surface unless the step clearly requires a different tool.
4. After the browser-based check is ready, stop and wait for the user to confirm the result.

### Setup and Tracking

- [x] Create the refactor plan document.
- [x] Create and push the split branch.
- [x] Keep the plan written in English.
- [x] Define the item-by-item workflow: implement, user test, commit, push.
- [x] Define the browser-first validation routine: refresh, login, user validation, commit, push.
- [ ] Keep this checklist updated after each completed refactor step.

### Phase 1: Stabilize the Entry Point

- [x] Keep app.py as the main compatibility entry point.
- [ ] Identify the minimum globals that must remain re-exported for tests.
- [x] Confirm imports from app.py still succeed after the first extraction.

### Phase 2: Extract Low-Risk Infrastructure

- [x] Extract authentication helpers.
- [x] Extract login, logout, and auth-check routes.
- [x] Extract request lifecycle hooks.
- [x] Extract shared request error handlers.
- [x] Extract rate limiting helpers.
- [x] Extract health and system routes.
- [ ] Validate smoke tests after this phase.

### Phase 3: Extract Configuration Domain

- [ ] Extract ConfigManager into a dedicated module.
- [ ] Extract config read and update endpoints.
- [ ] Extract environment variable helpers and endpoints.
- [ ] Extract runtime profile selection helpers where safe.
- [ ] Validate smoke tests after this phase.

### Phase 4: Extract Provider and Agent APIs

- [ ] Extract provider profile helpers and routes.
- [ ] Extract model role routes.
- [ ] Extract agent and personality routes.
- [ ] Extract capability preview and apply routes.
- [ ] Extract skills and starter-pack endpoints.
- [ ] Validate smoke tests after this phase.

### Phase 5: Isolate Chat Persistence First

- [ ] Extract chat session file load and write helpers.
- [ ] Extract folder persistence helpers.
- [ ] Extract request control persistence helpers.
- [ ] Extract attachment metadata helpers.
- [ ] Validate smoke tests after this phase.

### Phase 6: Extract Chat Runtime and Routes

- [ ] Extract chat transport selection logic.
- [ ] Extract CLI and API dispatch helpers.
- [ ] Extract cancellation flow helpers.
- [ ] Extract upload endpoints.
- [ ] Extract chat session and folder routes.
- [ ] Extract chat status route.
- [ ] Validate smoke tests after this phase.

### Completion Criteria

- [ ] Reduce app.py to a thin bootstrap and compatibility layer.
- [ ] Organize routes by concern in dedicated modules.
- [ ] Separate shared services from route registration.
- [ ] Keep smoke tests passing.
- [ ] Avoid unintended user-facing API changes.

## Why This Refactor Is Needed

The current app.py file mixes multiple concerns in a single module:

- Flask bootstrap and request lifecycle hooks
- Authentication and rate limiting
- Configuration and environment management
- Hermes runtime and update logic
- Provider, model, and agent APIs
- Chat sessions, folders, uploads, and transport logic
- Static SPA serving

This makes the file harder to navigate, increases change risk, and slows down testing and maintenance.

## Refactor Constraints

- Preserve all existing HTTP routes and payload shapes.
- Keep the current import surface stable during the first phases.
- Avoid rewriting business logic while extracting structure.
- Keep the smoke tests working with minimal or no changes.
- Validate each extraction step before moving to the next one.

## Current Risk To Respect

The existing tests import the top-level app module directly and patch module-level globals such as chat paths and runtime state. Because of that, the refactor should keep app.py as a compatibility facade at first, even after logic is moved into smaller modules.

## Target Structure

Recommended high-level layout:

- app.py
	Thin compatibility entry point that exposes the Flask app and legacy globals still used by tests.
- webui_app/__init__.py
	App factory or central bootstrap wiring.
- webui_app/extensions.py
	Flask setup, CORS, shared wiring.
- webui_app/auth.py
	Login, logout, cookie session helpers, token validation.
- webui_app/request_hooks.py
	Request ID generation, timing, request logging, request size handlers.
- webui_app/config_manager.py
	ConfigManager and configuration persistence helpers.
- webui_app/runtime.py
	Hermes binary discovery, runtime inspection, update state, gateway status.
- webui_app/chat_store.py
	Chat session persistence, folders, file-backed storage helpers.
- webui_app/chat_service.py
	Chat request orchestration, transport routing, cancellation, attachments.
- webui_app/routes/
	Route modules grouped by concern.

## Recommended Route Split

Suggested route grouping:

- routes/system.py
	health, system info, Hermes update endpoints
- routes/config.py
	config, runtime profiles, env vars
- routes/providers.py
	providers, discovery, model role wiring
- routes/agents.py
	agents, capabilities, skills, starter-pack flows
- routes/operations.py
	channels, sessions config, hooks, logs, cron, tools, service actions, onboarding
- routes/chat.py
	chat, uploads, folders, chat session CRUD, status
- routes/frontend.py
	SPA catch-all

## Execution Strategy

### Phase 1: Stabilize the Entry Point

- Keep app.py as the main import target.
- Move only wiring-safe pieces first.
- Re-export any objects the tests still patch directly.
- Do not change route behavior.

### Phase 2: Extract Low-Risk Infrastructure

Extract the smallest, least coupled slices first:

- authentication helpers and auth routes
- rate limiting
- request lifecycle hooks
- shared error handlers
- health and system routes

Reason:
These areas are relatively self-contained and do not depend on most of the chat/session state.

### Phase 3: Extract Configuration Domain

Move these pieces next:

- ConfigManager
- config endpoints
- env var helpers and endpoints
- runtime profile selection helpers if they can move without breaking shared globals

Reason:
This domain is large but more structured than chat and gives a clear reduction in app.py size.

### Phase 4: Extract Provider and Agent APIs

Move:

- provider profile helpers and routes
- model role routes
- agent and personality routes
- capability preview and apply routes
- skills and starter-pack endpoints

Reason:
These routes are mostly administrative and easier to isolate than the live chat flow.

### Phase 5: Isolate Chat Persistence First

Before moving chat routes, separate storage concerns from request orchestration:

- session file load/write/delete helpers
- folder persistence helpers
- request control file helpers
- attachment metadata helpers

Reason:
This creates a stable base for the most complex slice of the application.

### Phase 6: Extract Chat Runtime and Routes

Move last:

- chat transport selection
- CLI/API dispatch
- cancellation flow
- upload endpoints
- chat session and folder endpoints
- chat status endpoint

Reason:
This is the most stateful and coupled part of the codebase, so it should only move after the lower-level helpers are already isolated.

## Validation Plan

For each phase:

- run the smoke tests
- verify route registration still works
- confirm imports from app.py still succeed
- review the diff for accidental behavior changes

Minimum validation command after each extraction step:

- source venv/bin/activate && python -m unittest tests.test_smoke

## Practical Rules During Refactor

- Prefer moving code without rewriting it in the same step.
- Keep names stable until the module boundaries are proven.
- Avoid mixing structural refactors with feature work.
- If a helper is still heavily tied to module globals, move it later.
- If tests patch a global, preserve that global at the top level until the tests are updated intentionally.

## First Concrete Cut

Best first extraction:

- auth helpers and auth routes
- request hooks and request error handlers
- health and system endpoints

Why this first cut:

- low coupling compared with chat
- visible reduction in app.py size
- low risk of breaking persisted session behavior
- easy to validate quickly

## Definition of Done

The refactor is complete when:

- app.py is reduced to a thin bootstrap and compatibility layer
- routes are organized by concern in dedicated modules
- shared services are separated from route registration
- smoke tests pass
- no user-facing API behavior changes unintentionally
- future features can be added without returning to a monolithic file
