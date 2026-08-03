# Private API UI design system

## Direction

The primary desktop experience is the React `/console` application, opened by
`run.sh app` inside the native WebView shell. It uses a calm, information-first system: quiet surfaces,
strong typography hierarchy, restrained semantic color, and dense operational data
without dashboard decoration. OpenDesign owns this contract, Appica UI supplies the
React interaction primitives. The AppFlowy-based Flutter application remains an
explicit compatibility surface available through `run.sh flutter`.

Avoid gradients, glass panels, oversized KPI decoration, and page-specific button,
input, dialog, or tooltip implementations.

## Tokens

- Color: Appica semantic tokens (`background`, `foreground`, `primary`, `success`,
  `warning`, `error`, `info`) are the source for the React console. Flutter maps the
  same meanings through the current AppFlowy theme.
- Type: system UI stack, tabular numerals for metrics, three clear levels for page,
  section, and supporting text.
- Space: 4 px base rhythm; primary page gaps are 24 px; panel gaps are 12–14 px.
- Shape: medium controls, large panels, pill status badges. Generic cards remain a
  product composite because Appica 1.0.0 does not export a Card component.
- Motion: short state transitions only; all animation respects reduced motion.

## Layout

- Content maximum: 1480 px in the Web console and 1280 px in the Flutter channel page.
- Quota panels: three columns on wide desktop, two on compact desktop/tablet, one on
  narrow screens.
- A quota panel always presents label/help, status, primary value, and a meaningful
  progress indicator. Missing API values display an em dash and are never invented.
- Navigation is grouped by workbench, assets, and operations. It collapses into an
  off-canvas Appica navigation on narrow screens.

## States

Every data surface accounts for loading, empty, error, disabled, focus, selected,
light/dark, and reduced-motion states. Health and quota state use semantic labels in
addition to color.
