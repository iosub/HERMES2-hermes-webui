# Instructions

- Always use English for comments and code.
- Touch only the minimum code required to implement the new functionality.
- This folder and this document exist only for this branch.
- Do not include this folder in the final PR to `main`.


# Goal

Make the currently active Hermes profile visible in the key places of the portal UI, so the user can always understand which profile the portal is using.


# Current Situation

The portal already supports selecting a runtime profile from Settings.

What is missing is visibility. After the user selects a profile, the active profile is not shown consistently across the screens that depend on it. This creates confusion because the selected profile changes:

- Hermes home
- config and env sources
- gateway actions and status
- API server target
- provider/model resolution


# UX Principle

The active profile should be visible anywhere the profile has direct operational impact.

The UI should not spam the same badge everywhere. Instead, it should surface the profile in the places where the user is making decisions or reading runtime state.


# Recommended UI Surfaces

## 1. Global header or persistent status area

Add a small, always-visible profile indicator in a stable global location.

Recommended location:

- top bar actions area, or
- sidebar footer near gateway status

Recommended content:

- label: `Profile`
- value: active profile name

Why:

- gives immediate context from any screen
- removes ambiguity when navigating away from Settings


## 2. Dashboard

Show the active profile in the runtime summary area.

Recommended places:

- System Info card
- or a dedicated small stat card near Gateway Status

Recommended content:

- Active Profile
- Hermes Home
- API Server

Why:

- Dashboard is the first place users inspect runtime state
- profile affects all those values directly


## 3. Service screen

Show the active profile next to the gateway runtime information.

Recommended content:

- Active Profile
- Hermes Home
- Hermes Binary

Why:

- Start, stop, restart, and diagnostics are profile-sensitive operations
- users need to know which profile's gateway they are controlling


## 4. Settings screen

Keep the existing profile selector card and strengthen it as the canonical source of truth.

Recommended improvements:

- keep selected profile clearly labeled
- show a short explanation that the selected profile affects gateway, env, config, and CLI chat execution
- make sure the card appears above the settings tabs

Why:

- this is the screen where the user changes profile
- this should remain the authoritative control surface


## 5. Providers / Models area

Show the active profile where provider and API target data are displayed.

Recommended places:

- Providers screen summary cards
- Model roles screen or modal header

Recommended content:

- Active Profile
- current API server URL when relevant

Why:

- users may assume provider configuration is global
- this area is one of the most likely places for confusion when switching profiles


## 6. Chat screen

Show the active profile in the chat runtime area, but keep it lightweight.

Recommended places:

- session status strip
- chat settings panel
- a small runtime badge near transport or capability state

Why:

- the selected profile changes the effective environment behind chat
- this is especially important when comparing behavior across profiles


# Minimal Implementation Plan

## Phase 1: Reuse existing runtime profile API

Do not add a new backend feature unless strictly needed.

Use the existing runtime profile data already exposed by:

- `GET /api/runtime/profiles`
- `GET /api/health`
- `GET /api/chat/status`

Primary implementation direction:

- rely on `profile` from runtime/status endpoints where already present
- only add fields if a specific screen cannot render the profile with current data


## Phase 2: Add one reusable frontend renderer

Create one small shared helper in `static/app.js` to render profile UI consistently.

Examples:

- compact profile badge
- profile info row
- profile summary card snippet

Why:

- keeps changes small
- prevents inconsistent labels across screens
- makes later profile-related improvements cheaper


## Phase 3: Add profile visibility to the highest-value screens first

Implement in this order:

1. global persistent indicator
2. Dashboard
3. Service
4. Providers / Models
5. Chat

Reason for this order:

- highest runtime clarity first
- smallest user confusion reduction per edit
- minimal code spread early


# Current Implementation Progress

The branch now includes the following implemented surfaces:

- persistent active profile indicators in the top bar and sidebar footer
- active profile shown in Dashboard runtime information
- active profile shown in Service runtime information
- active profile shown in the chat session banner
- active profile shown in the chat thinking state
- chat history now distinguishes between the active portal profile and the profile used by each saved chat session

Important clarification already implemented in Chat:

- `Portal: <profile>` means the profile currently selected for the web UI runtime
- `Session: <profile>` means the profile that specific chat session used when it was created or last persisted

This distinction is necessary because a user can switch the active portal profile after creating older chats, and those older chats should still show their own session profile instead of silently changing labels.


# Current Validation Status

The branch has already been validated for these scenarios:

- switching the active profile updates the global profile indicator
- Dashboard and Service show the selected runtime profile
- new chats show the active profile in the chat banner
- chat history keeps the session profile visible even after switching the active portal profile

Additional branch-only testing helper currently present:

- the web UI can bootstrap the token from `?token=...` in the URL for easier testing in simple embedded browsers

This helper is meant to reduce friction during testing. A proper login screen is still planned later and is outside the current profile-visibility scope.


## Phase 4: Keep scope tight

Do not redesign the full UI.

Do not add profile display to every card or table.

Only show the profile where it improves operational clarity.


# Concrete Acceptance Criteria

The implementation should be considered complete when:

- the active profile is visible from any screen without going back to Settings
- Dashboard clearly shows which profile is active
- Service clearly shows which profile's gateway is being controlled
- Settings continues to provide profile switching and remains the canonical edit point
- Providers or Models clearly indicate which active profile the shown runtime data belongs to
- Chat exposes the active profile in a lightweight but visible way


# Suggested Validation

After implementation, validate these scenarios manually:

1. Switch from `default` to `leire` in Settings.
2. Confirm the visible profile indicator updates immediately.
3. Confirm Dashboard shows the new profile and matching Hermes home.
4. Confirm Service shows the same profile and corresponding gateway status.
5. Confirm Providers / Models reflect the expected API target for that profile.
6. Confirm Chat shows the same selected profile.
7. Switch back to `default` and verify all visible profile indicators update consistently.


# Implementation Notes

- Prefer existing runtime payloads over adding new backend logic.
- Avoid duplicating profile lookup logic in multiple frontend screens.
- Keep labels simple and explicit: `Profile`, `Active Profile`, `Hermes Home`, `API Server`.
- Do not modify unrelated screens or styling beyond what is needed for this feature.



