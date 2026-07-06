---
name: _template-overview
description: Scaffold for a new dataset domain — copy this folder to add one.
applies_to: []
---

# <Dataset name> (dataset <slug>)

> This is a scaffold, not a live domain. To add a dataset (demographics, stock
> prices, …): copy `domains/_template/` to `domains/<your-domain>/`, fill this in,
> and add domain-specific pages beside it. The retrieval tools pick it up
> automatically once the frontmatter `applies_to` terms match real questions.

## Primary building block
- Table `marts.<table>` — one row per <grain>. Key columns:
  - `<col>` (<type>) — <meaning>

## The measure(s)
- How to compute the headline metric from the raw columns (sum/count/median → rate).
- Which analysis skills apply (`growth_rate`, `rolling_average`, `latest_value`).

## Segmenter for profiles
- What dimension composition profiles should break down by for THIS domain
  (fed to the `profile_chart` skill).

## Gotchas
- Grain traps, non-additive columns, missing dimensions, case sensitivity,
  access/RLS notes.
