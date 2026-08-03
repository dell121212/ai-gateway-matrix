# AppFlowy Gateway Desktop Design

## Objective

Transform the complete AppFlowy 0.11.4 source tree into a Linux Flutter
desktop application for the existing AI Gateway Matrix backend. AppFlowy's
layout, navigation behavior, design tokens, widgets, menus, dialogs, hover and
selection states remain the visual baseline. Business copy, data, and actions
are replaced with the gateway's API management functions.

## Confirmed constraints

- Framework: Flutter.
- Reference source: `/home/chenkai/下载/AppFlowy-main`.
- Target backend: the existing project in `/home/chenkai/文档/api`.
- Implementation route: modify a complete AppFlowy working copy, not a WebView
  and not a lightweight visual imitation.
- Scope: merge both the classic channel dashboard and the professional console.
- Backend exposure: one public local endpoint, `127.0.0.1:4000`.
- Desktop behavior: start, stop, restart, and health-check the Docker backend.
- Persistence: settings, API keys, operations, token totals, PostgreSQL and
  Redis snapshots continue through `jiyi`.
- Delivery: Linux x86_64 application and installable Debian package.
- Usage: personal use. Preserve AppFlowy's AGPLv3 license notices.
- Existing uncommitted changes in the API repository must be preserved.

## Replication contract

### Fixed interface shell

- AppFlowy desktop window behavior and responsive minimum window constraints.
- Collapsible workspace sidebar and grouped navigation.
- Typography, colors, spacing, radii, borders, shadows, and icon language.
- Buttons, text fields, cards, tables, badges, popovers, dialogs, tooltips,
  context menus, loading indicators, and notifications.
- Hover, focus, selected, disabled, loading, empty, error, offline, expanded,
  collapsed, and drag-ready states.
- Keyboard focus paths, scroll behavior, theme switching, and window resizing.

### Replaceable business content

- AppFlowy workspace/document copy becomes AI Gateway Matrix copy.
- AppFlowy workspace data becomes channel, key, request, token, ledger, task,
  pricing, audit, health, and jiyi data.
- AppFlowy actions are rebound to the existing local backend.
- Original document and collaboration modules remain in the source working copy
  during the first migration version; they are not part of the new entry point.

## Navigation map

1. Overview
2. API Connection
3. Channels and Routing
4. API Keys
5. Requests and Token Usage
6. Credits and Ledger
7. Realtime Tasks
8. Task Detail
9. Model Pricing
10. Audit Log
11. System Health
12. Backend Control
13. Settings and jiyi

The first version creates every route and state. Core live bindings cover
backend health/control, total tokens, channels, API keys, and jiyi. Existing
`/api/v1` endpoints are used for the remaining pages, with structured mock data
used only when a backend response is unavailable.

## Architecture

The complete AppFlowy tree is copied under `app/appflowy_gateway`. A new Flutter
entry point, `lib/main_gateway.dart`, boots a gateway-specific application
without removing the original AppFlowy entry point. Gateway code lives under
`lib/gateway_matrix` and consumes AppFlowy's `appflowy_ui`, `flowy_svg`, theme,
and desktop infrastructure.

`GatewayApiClient` talks to `http://127.0.0.1:4000`. `GatewayRepository`
normalizes classic `/api/*` and professional `/api/v1/*` responses. A
`BackendController` wraps the existing `run.sh` or installed
`ai-gateway-matrix` command and exposes explicit process states. No secret is
stored in Flutter preferences; key mutations go through the backend and jiyi
remains authoritative.

## Data and state flow

```text
Flutter action
  -> GatewayRepository
  -> GatewayApiClient
  -> 127.0.0.1:4000
  -> existing FastAPI / LiteLLM / PostgreSQL / Redis
  -> jiyi-sync
  -> jiyi.txt
```

Polling provides the first-version overview and health refresh. Existing SSE is
used for task detail events. All API failures produce retryable AppFlowy-style
error states; cached or mock values are visibly labelled and never presented as
live facts.

## Packaging

The Flutter release is built with Flutter 3.27.4 and Rust 1.85.1, matching the
source. The Debian package installs the native Flutter bundle, existing backend
payload, launcher, desktop entry, and icon. User data remains under
`~/.config/ai-gateway-matrix`; package upgrades must not replace it.

## Verification

- Dart unit tests for response parsing, repository fallback, token formatting,
  process state transitions, and navigation.
- Flutter widget tests for sidebar selection, loading/empty/error states,
  dialogs, and key/channel actions.
- Existing Python backend and jiyi tests remain green.
- Linux release build and Debian package verification.
- Fixed-size screenshots of reference and migrated shell for visual comparison.
- Manual checks for hover, focus, menus, scrolling, resizing, backend restart,
  total token display, key persistence, and jiyi synchronization.
