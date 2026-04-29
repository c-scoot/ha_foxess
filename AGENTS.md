# AGENTS.md

## Purpose

This repository is a Home Assistant custom integration for FoxESS Cloud.
Optimize for reliability, safe API usage, stable entities, and clear user-facing behavior over feature volume.

## Default Balance

Use this default effort split unless the task clearly needs a different ratio:

- Design: 20%. Understand the affected behavior, API contract, entity impact, and rate-limit implications before editing.
- Build: 45%. Implement the smallest coherent change in the correct layer.
- Document: 15%. Update user-facing and maintainer-facing documentation when behavior changes.
- Test: 20%. Verify every change with the strongest checks available.

Do not skip documentation or verification just because the repo is small.

## Workflow For Every Task

1. Design first. Identify which layer is changing, what the user-visible effect is, and whether the FoxESS API call budget or refresh timing is affected.
2. Build second. Keep the change scoped and place logic in the correct module.
3. Document third. Update docs, service descriptions, or UI strings when behavior or terminology changes.
4. Test last. Run the best available automated checks, then summarize any manual verification and any remaining gaps.

For non-trivial changes, leave a short plan in the work log, commit message, or PR description before making broad edits.

## Repository Map

- `custom_components/foxess_cloud/api.py`: FoxESS request signing, endpoint access, normalization, fallback behavior, and rate-limit handling.
- `custom_components/foxess_cloud/coordinator.py`: refresh orchestration, caching, device state assembly, and write-followed-by-refresh behavior.
- `custom_components/foxess_cloud/sensor.py`: curated and dynamic sensors, derived sensor logic, naming, and statistics semantics.
- `custom_components/foxess_cloud/number.py`: writable numeric controls.
- `custom_components/foxess_cloud/select.py`: work mode and related selects.
- `custom_components/foxess_cloud/config_flow.py`: setup, authentication, and config-entry UX.
- `custom_components/foxess_cloud/__init__.py`: integration setup, unload, and service registration.
- `custom_components/foxess_cloud/services.yaml`: Home Assistant service contract and field descriptions.
- `custom_components/foxess_cloud/strings.json` and `translations/en.json`: config-flow and UI text.
- `README.md`: installation, setup, behavior, entity semantics, polling, and dashboard guidance.

## Design Rules

- Preserve stable entity meaning and avoid unnecessary entity churn.
- Prefer curated sensors over exposing raw duplicate FoxESS keys when both represent the same concept.
- Treat rate-limit and daily call-budget impact as part of the design, not an afterthought.
- Keep long-term statistics semantics correct. Daily counters, total counters, and instantaneous power values must not be blurred together.
- Make write paths safe and predictable. Successful writes should refresh or invalidate related cached state promptly.
- Preserve graceful behavior when FoxESS omits fields, changes naming, or returns partial data.
- Do not broaden scope with opportunistic refactors unless they directly reduce risk in the current task.

## Build Rules

- Put API quirks and payload normalization in `api.py`, not in entity classes unless the data is purely presentation-specific.
- Put refresh cadence, merge logic, and post-write refresh behavior in `coordinator.py`.
- Keep entity files focused on Home Assistant entity behavior, naming, attributes, and derived values.
- Prefer small helpers over deeply inlined logic when handling FoxESS naming variants or scheduler/work-mode fallbacks.
- Preserve backward compatibility for entity IDs, service names, and dashboard-facing behavior whenever practical.
- Add brief comments only for non-obvious FoxESS quirks, derived calculations, or fallback reasoning.

## Documentation Rules

- Update `README.md` when changing setup flow, polling behavior, entity naming, entity semantics, Energy dashboard guidance, writable controls, or known API caveats.
- Update `services.yaml` when changing service parameters, meanings, or examples.
- Update `strings.json` and `translations/en.json` when adding or renaming config-flow or UI text.
- If a change is internal-only, document the reasoning with clear commit notes or concise inline comments where the code would otherwise be hard to follow.

## Testing Rules

- Prefer automated tests for normalization logic, rate-limit handling, scheduler/work-mode behavior, derived sensor math, and coordinator refresh decisions.
- If there is no existing test harness for the affected area, it is acceptable to add a small targeted one rather than relying only on manual checks.
- At minimum, run a syntax or import-level validation for touched Python files.
- For behavior changes, record a manual verification checklist covering the exact user-visible path that changed.
- If testing is partial, say exactly what was verified and what was not.

## Definition Of Done

A task is not done until all of the following are true:

- The change is implemented in the correct layer with scope kept under control.
- User-facing documentation is updated, or a clear note explains why no doc change was needed.
- Validation was run and the results were recorded.
- Assumptions, tradeoffs, and remaining risks were stated plainly.
