# Appica console feature parity

The React `/console` is the primary desktop experience. This matrix freezes the
functional surface that must remain available while the presentation is rebuilt
with Appica UI.

| Capability | Existing source | Appica destination | Required actions |
| --- | --- | --- | --- |
| Unified API connection | Flutter `connection` | `/connection` | copy base URL and model modes |
| Channel catalog | classic dashboard + Flutter | `/channels` | search, filter, refresh, inspect quota and health |
| Channel credentials | classic dashboard + Flutter | `/channels` | save Key, probe, refresh balance |
| Channel routing | classic dashboard + Flutter | `/channels` | tier, priority, model, temporary optimal, brain source |
| Provider lifecycle | classic dashboard + Flutter | `/channels` | add account, discover/add custom provider, delete channel |
| Routing classifier | classic dashboard + Flutter | `/channels` | auto/source/dedicated modes and answer verification |
| Professional API keys | professional API | `/keys` | create and revoke |
| Gateway client keys | classic dashboard | `/keys` | create, reveal/copy, probe, raise limits, delete |
| Requests | professional API | `/requests`, `/requests/:id` | list and inspect attempts |
| Tasks | professional API | `/tasks`, `/tasks/:id` | create, inspect, finish |
| Credits | professional API | `/ledger` | inspect and privileged adjustment |
| Pricing | professional API | `/pricing` | inspect and create immutable versions |
| Users and roles | professional API | `/users` | list, create and disable |
| Health | professional API | `/health` | structured service state and raw diagnostics |
| jiyi persistence | Flutter + professional API | `/memory` | status and immediate sync |
| Backend runtime | Flutter desktop controller | `/runtime` | health in Web; start/restart/stop through desktop bridge |

All mutations use the existing backend contracts. Missing permissions, desktop
bridge availability, loading, empty, success, and error states must be explicit;
the UI must not substitute demo values or silently hide unavailable operations.
