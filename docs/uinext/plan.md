## UI Improvement Plan

This refactor should stay conservative and testable: first stabilize the shared responsive layout, then harden shared components, then improve the chat experience, and finally polish navigation and secondary admin screens. The workflow below must remain the operating contract for every implementation step.

## Refactor Checklist

### Phase 0. Baseline

- [ ] Confirm the validation baseline screens: Chat, Dashboard, Providers / Models, Settings / Env Vars, and Sessions / Logs.
- [ ] Review the current UI on desktop.
- [ ] Review the current UI on tablet.
- [ ] Review the current UI on real mobile.
- [x] Define the expected UX for dual-profile comparison in chat before implementation.
- [x] Identify the first small implementation slice before changing code.

### Phase 1. Shared Responsive Layout

- [ ] Adjust sidebar, topbar, and content area spacing for desktop.
- [ ] Adjust sidebar, topbar, and content area spacing for tablet.
- [ ] Add real mobile layout behavior for sidebar and content.
- [ ] Verify that chat mode and non-chat screens use space consistently.
- [ ] Verify that unexpected horizontal scrolling is removed.

### Phase 2. Shared Components

- [ ] Improve modal height limits and internal scrolling.
- [ ] Make modal footers stable on smaller screens.
- [ ] Improve form focus states.
- [ ] Improve form error states.
- [ ] Improve form disabled states.
- [ ] Improve table overflow behavior.
- [ ] Improve dense table readability on smaller screens.

### Phase 3. Chat Experience

- [ ] Improve chat layout density for transcript and composer.
- [ ] Improve chat history behavior on smaller screens.
- [ ] Improve visual hierarchy between user and assistant messages.
- [x] Add a chat compare mode that can send the same prompt to two different profiles at the same time.
- [x] Add UI controls to choose the primary and secondary profiles for compare mode.
- [x] Render compare-mode answers in two side-by-side columns on desktop.
- [ ] Stack compare-mode answers cleanly on smaller screens.
- [x] Keep each compared profile visually distinguishable.
- [ ] Define how compare mode behaves with attachments, regeneration, and follow-up prompts.
- [ ] Improve attachment area readability.
- [x] Improve progress trace readability without competing with the main response.
- [ ] Verify long-response behavior in the chat transcript.

### Phase 4. Admin Screens

- [ ] Refine Dashboard spacing and card hierarchy.
- [ ] Refine Providers / Models layout and form consistency.
- [ ] Refine Settings / Env Vars layout and form consistency.
- [ ] Refine Sessions / Logs table readability and overflow handling.
- [ ] Verify that shared visual rules stay consistent across admin screens.

### Phase 5. Navigation Polish

- [ ] Improve collapsed sidebar clarity.
- [ ] Strengthen active navigation states.
- [ ] Improve spacing and readability of navigation groups.
- [ ] Verify topbar behavior in chat and non-chat screens.
- [ ] Verify that navigation preserves context across screen changes.

### Phase 6. Cross-Screen Verification

- [ ] Re-check all priority screens on desktop.
- [ ] Re-check all priority screens on tablet.
- [ ] Re-check all priority screens on real mobile.
- [ ] Review dark theme behavior.
- [ ] Review light theme behavior.
- [ ] Review warm theme behavior.
- [ ] Review system theme behavior.
- [x] Update this checklist after each approved mini-step.

## Relevant Files

- templates/index.html: root SPA structure, sidebar, topbar, modal shell, and main layout containers.
- static/app.js: central UI behavior; the main anchors are showModal, navigate, Screens, and chatState.
- static/style.css: layout system, shared components, chat-specific rules, and responsive breakpoints.
- tools/run_playwright_smoke.sh: fast smoke validation after a stable phase.
- tests/test_smoke.py: baseline reference to avoid breaking core assumptions while improving the UI.

## Validation Checklist

- [ ] Before each mini-step, refresh the integrated browser.
- [ ] Before each mini-step, sign in with the admin account.
- [ ] Validate only the surface touched by the current step.
- [ ] After shared layout changes, verify expanded and collapsed sidebar behavior.
- [ ] After shared layout changes, verify mobile overlay behavior.
- [ ] After shared layout changes, verify topbar behavior in chat and non-chat screens.
- [ ] After shared layout changes, verify the absence of unexpected horizontal scrolling.
- [ ] After modal changes, test long modal content and footer behavior.
- [ ] After form changes, test focus, error, and disabled states.
- [ ] After table changes, test dense tables in Sessions / Logs.
- [ ] After chat changes, test transcript layout, history panel, composer, attachments, and progress trace behavior.
- [x] After compare-mode changes, test sending one prompt to two different profiles simultaneously.
- [x] After compare-mode changes, test that both responses appear side by side on desktop.
- [ ] After compare-mode changes, test that the compare layout collapses correctly on tablet and mobile.
- [ ] After compare-mode changes, test mixed outcomes where one profile succeeds and the other fails.
- [ ] After a phase becomes stable, run the smoke flow from tools/run_playwright_smoke.sh.

## Scope Checklist

- [ ] Keep real responsive support down to mobile within scope.
- [ ] Keep chat clarity and density improvements within scope.
- [ ] Keep dual-profile simultaneous comparison in chat within scope.
- [ ] Keep modal, form, table, and navigation normalization within scope.
- [ ] Do not introduce backend changes unless a visual requirement strictly depends on them.
- [ ] Do not turn this phase into a full branding redesign.
- [ ] Do not introduce a framework migration.
- [ ] Do not split static/app.js as part of this refactor.
- [ ] Do not batch multiple unvalidated items into one implementation step.

## Execution Notes

- Start with a small mobile/tablet layout step before touching detailed chat visuals.
- Use chat as the pilot screen to tune density and spacing before propagating shared improvements.
- Treat dual-profile comparison as a dedicated chat sub-feature with its own mini-steps, not as a small cosmetic tweak.
- Reserve the Playwright smoke flow for phase closures rather than every tiny visual adjustment.



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
- Keep `hermes-webui.service` disabled for automatic startup during this refactor so the UI can be stopped and started manually for testing.

Manual validation routine for each test step:

1. Refresh the integrated browser before starting the check.
2. Log in through the UI with the admin account before validating the current item.
3. Use the integrated browser as the default manual validation surface unless the step clearly requires a different tool.
4. Start or stop `hermes-webui.service` manually when the current test step requires it.
5. After the browser-based check is ready, stop and wait for the user to confirm the result.


