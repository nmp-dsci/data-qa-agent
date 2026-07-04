---
name: rolling-averages
description: Smooth every trend with a 6-month rolling average — the one window for both the chart line and growth.
applies_to: [rolling average, moving average, smoothing, window, trailing]
---

# Rolling averages

Monthly property buckets are volatile (few sales/bonds per suburb per month), so
almost every trend figure should be smoothed. Use the `rolling_average` tool (a
trailing mean over the ordered monthly series) rather than writing window SQL by
hand.

## Window choice — one window: 6 months

Use a **6-month** rolling average everywhere:
- the **chart** overlay line (the bold, solid line drawn over the faint actuals),
  and
- the **base** for growth rates and "latest value" headlines.

Six months is long enough to wash out single-month spikes and thin months, and
short enough to stay responsive to a real turn in the trend. The `compute_trend`
tool already uses a 6-month window; don't override it.

## Rules
- The rolling series is computed in Python from the query rows, not asserted by the
  model. Same rows in → same series out.
- **Keep every month.** Never drop thin months from a series with a
  `WHERE n_sold >= N` / `n_rented >= N` filter — the 6-month average, not a row
  filter, is what stabilises the noise ([[analysis/latest-month]],
  [[domains/property-sales/overview]]).
- A rolling average needs enough history; if the series is shorter than 6 months,
  fall back to the raw series and say the trend is provisional.
