# AppFlowy Gateway V0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a runnable Linux Flutter V0.1 by adding an AI Gateway Matrix
entry point to a complete AppFlowy 0.11.4 working copy.

**Architecture:** Preserve the original AppFlowy source and entry point. Add a
gateway-specific Flutter entry point and focused `gateway_matrix` modules that
reuse AppFlowy UI/theme packages and bind to the existing local backend through
a repository and process-control boundary.

**Tech Stack:** Flutter 3.27.4, Dart 3.6.2, AppFlowy 0.11.4 UI packages,
Rust 1.85.1, HTTP/SSE, FastAPI, Docker Compose, Debian packaging.

## Global Constraints

- Keep `/home/chenkai/下载/AppFlowy-main` unchanged.
- Preserve all pre-existing uncommitted API repository changes.
- Do not delete AppFlowy pages or states in V0.1.
- Use only `127.0.0.1:4000` for the application-facing backend.
- Route every key and persistent operation through the backend and jiyi.
- Never store plaintext API keys in Flutter preferences.
- Do not commit, push, or publish without separate user authorization.
- Target Linux x86_64 and produce a `.deb`.

---

### Task 1: Import the complete AppFlowy working copy

**Files:**
- Create: `app/appflowy_gateway/**`
- Preserve: `/home/chenkai/下载/AppFlowy-main/**`

**Interfaces:**
- Consumes: AppFlowy 0.11.4 source.
- Produces: an in-repository working copy with original entry point intact.

- [ ] Copy the complete source tree to `app/appflowy_gateway`.
- [ ] Record upstream version and license in
      `app/appflowy_gateway/GATEWAY_MIGRATION.md`.
- [ ] Verify the reference and working-copy file counts and checksums before
      gateway-specific edits.

### Task 2: Add gateway domain and HTTP boundaries

**Files:**
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/domain/gateway_models.dart`
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/data/gateway_api_client.dart`
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/data/gateway_repository.dart`
- Test: `app/appflowy_gateway/frontend/appflowy_flutter/test/gateway_matrix/gateway_repository_test.dart`

**Interfaces:**
- Consumes: classic `/api/*` and professional `/api/v1/*` JSON.
- Produces: `GatewaySnapshot`, `GatewayChannel`, `GatewayClientKey`,
  `GatewayRequest`, `GatewayTask`, and `GatewayHealth`.

- [ ] Write parsing tests for missing, null, numeric-string, and normal fields.
- [ ] Implement immutable domain models and defensive JSON parsing.
- [ ] Implement a base-URL constrained client for `127.0.0.1:4000`.
- [ ] Implement repository methods for overview, channels, keys, requests,
      ledger, tasks, pricing, audit, health, and jiyi status.
- [ ] Run focused Dart tests.

### Task 3: Add backend process control

**Files:**
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/services/backend_controller.dart`
- Test: `app/appflowy_gateway/frontend/appflowy_flutter/test/gateway_matrix/backend_controller_test.dart`

**Interfaces:**
- Consumes: installed `ai-gateway-matrix` or repository `run.sh`.
- Produces: `BackendState` and `start()`, `stop()`, `restart()`, `refresh()`.

- [ ] Write state-transition tests with an injected process runner.
- [ ] Resolve the installed launcher first and source launcher second.
- [ ] Poll `/healthz` with a bounded timeout and expose actionable failures.
- [ ] Run the controller tests.

### Task 4: Build the AppFlowy gateway shell

**Files:**
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/main_gateway.dart`
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/gateway_app.dart`
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/presentation/gateway_shell.dart`
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/presentation/navigation/gateway_destination.dart`
- Test: `app/appflowy_gateway/frontend/appflowy_flutter/test/gateway_matrix/gateway_shell_test.dart`

**Interfaces:**
- Consumes: `GatewayRepository`, `BackendController`, AppFlowy UI/theme.
- Produces: native application entry point and all navigation destinations.

- [ ] Write widget tests for every destination and sidebar selection.
- [ ] Initialize AppFlowy light/dark themes and Linux window constraints.
- [ ] Implement collapsible grouped sidebar, header, content pane, search affordance,
      theme switch, hover, selected, focus, and context-menu states.
- [ ] Run shell widget tests.

### Task 5: Implement all V0.1 pages and states

**Files:**
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/presentation/pages/*.dart`
- Create: `app/appflowy_gateway/frontend/appflowy_flutter/lib/gateway_matrix/presentation/widgets/*.dart`
- Test: `app/appflowy_gateway/frontend/appflowy_flutter/test/gateway_matrix/pages_test.dart`

**Interfaces:**
- Consumes: repository and backend controller.
- Produces: all 13 routes in the design specification.

- [ ] Write widget tests for loading, populated, empty, error, offline, disabled,
      dialog, menu, and retry states.
- [ ] Implement overview and cumulative token cards.
- [ ] Implement API connection, channel routing, and API key workflows.
- [ ] Implement requests, ledger, tasks/detail, pricing, audit, and health pages.
- [ ] Implement backend control and jiyi settings pages.
- [ ] Run all gateway widget tests.

### Task 6: Complete backend endpoints needed by the native client

**Files:**
- Modify: `app/dashboard/backend.py`
- Modify: `app/dashboard/app/modules/system/router.py`
- Modify: `app/scripts/jiyi_store.py`
- Test: `app/tests/test_dashboard_auth.py`
- Test: `app/tests/test_jiyi_store.py`
- Create: `app/tests/test_native_app_api.py`

**Interfaces:**
- Consumes: native Flutter REST requests.
- Produces: authenticated jiyi status/save/load endpoints and consistent token totals.

- [ ] Write failing tests for jiyi status/save and cumulative token responses.
- [ ] Add narrowly scoped native-client endpoints without exposing secret contents.
- [ ] Ensure key/channel mutations trigger or are observed by jiyi synchronization.
- [ ] Run focused Python tests and existing jiyi/usage tests.

### Task 7: Build and verify Linux release

**Files:**
- Modify: `app/appflowy_gateway/frontend/Makefile.toml`
- Modify: `app/packaging/build-deb.sh`
- Modify: `app/packaging/deb/ai-gateway-matrix.wrapper`
- Modify: `app/desktop/ai-gateway-matrix.desktop.in`
- Test: `app/tests/verify_deb_package.sh`

**Interfaces:**
- Consumes: gateway Flutter entry point and existing backend payload.
- Produces: Linux release bundle and installable Debian package.

- [ ] Add a production build target for `lib/main_gateway.dart`.
- [ ] Build with Flutter 3.27.4 and Rust 1.85.1.
- [ ] Package the Flutter bundle with the existing backend and persistent-data rules.
- [ ] Run package verification and inspect dynamic library dependencies.
- [ ] Launch against the local backend and verify every navigation destination.
- [ ] Capture fixed-viewport screenshots and record remaining visual differences.
