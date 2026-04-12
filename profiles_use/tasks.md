# Profile Visibility Tasks

This document is branch-local working material for `improve-profiles`.

It exists to track the implementation work before the final upstream PR.

Do not include this folder in the final upstream PR.


## Rules

- Keep code comments and code in English.
- Touch only the minimum code required for the feature.
- Update this checklist as work progresses.
- Commit progress to this branch so the work remains traceable.


## Current Goal

Make the active Hermes profile visible in the important runtime areas of the portal UI so users always know which profile they are using.


## Branch Status

- Branch: `improve-profiles`
- Base: `main`
- Scope: branch-only planning and incremental implementation


## Backlog

- [x] Review the current UI surfaces and decide the minimum set of screens that should show the active profile.
- [x] Add one reusable frontend helper for rendering active profile information consistently.
- [x] Add a persistent active profile indicator in a global UI location.
- [x] Show the active profile in Dashboard runtime information.
- [x] Show the active profile in the Service screen next to gateway controls and runtime state.
- [ ] Show the active profile in Providers / Models where runtime target data is shown.
- [x] Show the active profile in Chat in a lightweight but visible way.
- [x] Distinguish the active portal profile from each chat session profile in chat history.
- [x] Keep the chat session banner aligned with the selected profile when creating a new chat after switching profiles.
- [ ] Validate profile switching visually for `default` and `leire`.
- [ ] Keep this tracking folder out of the final upstream PR.


## In Progress

- [ ] Implement active profile visibility in Providers / Models.


## Future Roadmap

### Phase 1: Segmented chat runtime

- [ ] Allow switching profile inside the same chat for subsequent turns.
- [ ] Represent each runtime phase as a visible chat segment (`1`, `2`, `3`, ...).
- [ ] Label each segment with its profile and transport.
- [ ] Keep the chat visually unified while making runtime boundaries explicit.
- [ ] Define how Hermes CLI continuity behaves when crossing profile boundaries.

### Phase 2: Multi-profile comparison mode

- [ ] Add a dedicated compare mode separate from normal chat.
- [ ] Allow sending the same prompt to multiple selected profiles.
- [ ] Render one response column per selected profile.
- [ ] Label each column with profile and effective runtime/model information.
- [ ] Keep compare mode results separate from the normal segmented chat flow unless explicitly promoted.


## Done

- [x] Create the `improve-profiles` branch.
- [x] Create a branch-local task tracker in `profiles_use/tasks.md`.
- [x] Document the high-level implementation plan in `profiles_use/plan.md`.
- [x] Add a reusable profile rendering helper in the frontend.
- [x] Add a persistent active profile indicator to the global UI.
- [x] Show the active profile in Dashboard.
- [x] Show the active profile in Service.
- [x] Show the active profile in Chat and chat history.
- [x] Separate `Portal` and `Session` profile labels in chat history.
- [x] Fix the new-chat banner to use the currently selected profile after a profile switch.
- [x] Add a temporary `?token=...` bootstrap helper for testing in simple embedded browsers.


## Validation Checklist

- [x] Switching the profile updates the persistent profile indicator.
- [x] Dashboard shows the active profile and matching runtime paths.
- [x] Service shows which profile's gateway is being controlled.
- [ ] Providers / Models show which profile the displayed runtime configuration belongs to.
- [x] Chat shows the active profile without adding visual noise.
- [x] The UI remains consistent after switching back and forth between `default` and `leire` for the global indicator, chat banner, and chat history labels.


## Notes

- The portal already supports runtime profile selection.
- This work is about visibility and clarity, not reworking the full profile system.
- A temporary `?token=...` URL bootstrap was added only to make testing easier in simple embedded browsers that do not handle the token prompt correctly.
- Before the final upstream PR, remove or exclude this folder from the published diff.