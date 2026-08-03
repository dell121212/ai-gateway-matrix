# UI implementation contract

## Frozen business contracts

- `UI_FEATURE_PARITY.md` is the functional acceptance matrix for the primary
  Appica console. A link to the classic dashboard is optional convenience, not a
  substitute for implementing an action in `/console`.
- Preserve `/console` routes, `/api/v1` requests, authentication behavior, React Query
  cache keys, forms, permissions, and backend-provided values.
- Preserve the Flutter gateway channel models and actions. UI work must not replace
  real services with demo data.
- Preserve the user-added token totals on the overview page.

## Component mapping

| Product need | React implementation | Flutter equivalent | Product composite |
| --- | --- | --- | --- |
| Application shell | Appica `Navigation`, `Breadcrumb`, `BackgroundPattern` | AppFlowy sidebar | grouped route shell |
| Status | Appica `Badge` | AppFlowy themed status chip | `ServiceStatus` / channel status |
| Quota ratio | Appica `Progress` | themed linear progress | `QuotaPanel` / channel quota card |
| Explanatory help | Appica `Tooltip` | Flutter tooltip | label help trigger |
| Loading | Appica `Skeleton` | themed loading placeholder | page and quota loading states |
| Actions | Appica `Button` | AppFlowy button | route or mutation action |
| Text entry | Appica `Input` | AppFlowy input | account/key fields |
| Choice | Appica `Select` | AppFlowy select/menu | routing mode selector |
| Records | Appica `Table` | AppFlowy data table | requests, tasks, keys, pricing, audit |
| Copy secret | Appica `CopyButton` | Flutter clipboard action | one-time client key |
| Surface/card | project CSS (not in Appica 1.0.0) | existing AppFlowy card | `QuotaPanel`, stat and system cards |

## Locked Web stack

- `@appica/ui-react`: 1.0.0
- React / React DOM: 19.2.8
- Tailwind CSS / Vite plugin: 4.3.3
- Appica styles are imported once in `src/styles.css`; theme, reduced-motion, and
  tooltip providers are mounted once in `src/main.tsx`.

## Responsive acceptance

| Available content width | Quota/stat columns |
| --- | --- |
| Greater than 1100 px | 3 |
| 621–1100 px | 2 |
| 620 px or less | 1 |

The compatibility Flutter channel grid uses its own measured content width and
follows the same 3/2/1 behavior.

## Verification

- Web: `npm test`, `npx tsc --noEmit`, `npm run build`, then browser checks at wide,
  compact, and narrow viewports in light and dark themes.
- Flutter: format, analyze, gateway widget tests, and a wide/narrow layout check.
- A change to the primary experience requires fresh Web results. Changes that also
  touch the Flutter compatibility surface require fresh results from both stacks.
