"""The profile engine — a pure port of the legacy profileCalc.js / profileMetrics.js.

Given two cohorts (a Target and a Comparison), each already aggregated by the
service, it computes:

* the topline per-cohort totals and their deltas across every metric;
* per-predictor **segment uplifts** — for each breakdown dimension, how each
  segment's response metric moved between the cohorts, ranked by |Δ|;
* a per-predictor **signal score** (the largest |Δ| among its segments) so the UI
  can order the predictor charts strongest-signal-first;
* flat positive / negative uplift leaderboards across all predictors.

This module is deliberately DB-free and side-effect-free: it transforms
already-fetched aggregate rows into the profile result, so it is exhaustively
unit-testable without a database. The service (service.py) does the SQL; the
router hands the rows here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .manifest import Dataset, Metric


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    label: str
    fmt: str
    target: float | None
    comparison: float | None
    delta: float | None
    delta_pct: float | None


@dataclass(frozen=True)
class Segment:
    value: str
    target: float | None
    comparison: float | None
    delta: float | None
    delta_pct: float | None
    # volume of the target cohort in this segment (drives the min-volume filter &
    # tie-breaking), so a one-record blip can't top the ranking.
    target_n: float | None


@dataclass(frozen=True)
class PredictorProfile:
    predictor: str
    label: str
    kind: str
    ordinal: bool
    signal: float  # max |Δ| across the predictor's segments (0 if none)
    segments: list[Segment]


@dataclass(frozen=True)
class ProfileResult:
    metric: str
    metric_label: str
    metric_fmt: str
    target_total: float | None
    comparison_total: float | None
    delta: float | None
    delta_pct: float | None
    metric_deltas: list[MetricDelta]
    predictors: list[PredictorProfile]
    positive_uplifts: list[dict[str, Any]]
    negative_uplifts: list[dict[str, Any]]

    def to_public(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "metric_label": self.metric_label,
            "metric_format": self.metric_fmt,
            "target_total": self.target_total,
            "comparison_total": self.comparison_total,
            "delta": self.delta,
            "delta_pct": self.delta_pct,
            "metric_deltas": [d.__dict__ for d in self.metric_deltas],
            "predictors": [
                {
                    "predictor": p.predictor,
                    "label": p.label,
                    "kind": p.kind,
                    "ordinal": p.ordinal,
                    "signal": p.signal,
                    "segments": [s.__dict__ for s in p.segments],
                }
                for p in self.predictors
            ],
            "positive_uplifts": self.positive_uplifts,
            "negative_uplifts": self.negative_uplifts,
        }


def _pct(delta: float | None, base: float | None) -> float | None:
    if delta is None or base is None or base == 0:
        return None
    return round(delta / base * 100, 2)


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def metric_deltas(
    dataset: Dataset,
    target_totals: dict[str, Any],
    comparison_totals: dict[str, Any],
) -> list[MetricDelta]:
    """Topline delta for every metric in the dataset."""
    out: list[MetricDelta] = []
    for mtr in dataset.metrics:
        t = _num(target_totals.get(mtr.name))
        c = _num(comparison_totals.get(mtr.name))
        delta = (t - c) if (t is not None and c is not None) else None
        out.append(
            MetricDelta(
                metric=mtr.name,
                label=mtr.label,
                fmt=mtr.fmt,
                target=t,
                comparison=c,
                delta=None if delta is None else round(delta, 2),
                delta_pct=_pct(delta, c),
            )
        )
    return out


def _segments_for_predictor(
    metric: Metric,
    target_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    ordinal: bool,
    min_volume: int,
) -> list[Segment]:
    """Merge a predictor's target & comparison rows into per-segment deltas.

    Rows are dicts carrying the segment value under key ``segment``, the response
    metric under the metric's name, and a volume count under ``_n``.
    """
    by_target = {str(r["segment"]): r for r in target_rows}
    by_comp = {str(r["segment"]): r for r in comparison_rows}
    values = list(by_target.keys())
    for v in by_comp:
        if v not in by_target:
            values.append(v)

    segments: list[Segment] = []
    for v in values:
        tr = by_target.get(v)
        cr = by_comp.get(v)
        t = _num(tr.get(metric.name)) if tr else None
        c = _num(cr.get(metric.name)) if cr else None
        target_n = _num(tr.get("_n")) if tr else None
        # Skip thin target segments: a segment carried by a couple of records is
        # noise, not signal (the legacy tool ranked on volume-weighted moves).
        if target_n is not None and target_n < min_volume:
            continue
        delta = (t - c) if (t is not None and c is not None) else None
        segments.append(
            Segment(
                value=v,
                target=t,
                comparison=c,
                delta=None if delta is None else round(delta, 2),
                delta_pct=_pct(delta, c),
                target_n=target_n,
            )
        )

    if ordinal:
        # Ordinal dims keep their natural order (bedrooms 0,1,2..; years ascending),
        # annotated with deltas — sorting them by Δ would scramble the axis.
        segments.sort(key=_ordinal_key)
    else:
        # Categorical dims sort by signed Δ so the strongest movers read top-down.
        segments.sort(key=lambda s: (s.delta is None, -(s.delta or 0)))
    return segments


def _ordinal_key(seg: Segment) -> tuple[int, float | str]:
    """Natural sort for ordinal segment labels: numeric where possible, else text."""
    try:
        return (0, float(str(seg.value).rstrip("+")))
    except ValueError:
        return (1, str(seg.value))


def build_profile(
    dataset: Dataset,
    response_metric: str,
    target_totals: dict[str, Any],
    comparison_totals: dict[str, Any],
    target_by_predictor: dict[str, list[dict[str, Any]]],
    comparison_by_predictor: dict[str, list[dict[str, Any]]],
    *,
    min_segment_volume: int = 3,
    max_leaderboard: int = 8,
) -> ProfileResult:
    """Assemble the full profile result from pre-fetched aggregate rows."""
    metric = dataset.metric(response_metric)
    if metric is None:
        raise ValueError(f"unknown response metric {response_metric!r}")

    deltas = metric_deltas(dataset, target_totals, comparison_totals)
    topline = next((d for d in deltas if d.metric == response_metric), None)

    predictors: list[PredictorProfile] = []
    for dim in dataset.predictor_dimensions:
        t_rows = target_by_predictor.get(dim.name, [])
        c_rows = comparison_by_predictor.get(dim.name, [])
        if not t_rows and not c_rows:
            continue
        segments = _segments_for_predictor(
            metric, t_rows, c_rows, dim.is_ordinal, min_segment_volume
        )
        signal = max((abs(s.delta) for s in segments if s.delta is not None), default=0.0)
        predictors.append(
            PredictorProfile(
                predictor=dim.name,
                label=dim.label,
                kind=dim.kind,
                ordinal=dim.is_ordinal,
                signal=round(signal, 2),
                segments=segments,
            )
        )

    # Strongest-signal predictor first (the UI orders charts by this).
    predictors.sort(key=lambda p: -p.signal)

    # Flat leaderboards across every predictor's segments.
    flat: list[dict[str, Any]] = []
    for p in predictors:
        for s in p.segments:
            if s.delta is None:
                continue
            flat.append(
                {
                    "predictor": p.predictor,
                    "label": p.label,
                    "segment": s.value,
                    "delta": s.delta,
                    "delta_pct": s.delta_pct,
                    "target": s.target,
                    "comparison": s.comparison,
                }
            )
    positives = sorted((f for f in flat if f["delta"] > 0), key=lambda f: -f["delta"])
    negatives = sorted((f for f in flat if f["delta"] < 0), key=lambda f: f["delta"])

    return ProfileResult(
        metric=metric.name,
        metric_label=metric.label,
        metric_fmt=metric.fmt,
        target_total=topline.target if topline else None,
        comparison_total=topline.comparison if topline else None,
        delta=topline.delta if topline else None,
        delta_pct=topline.delta_pct if topline else None,
        metric_deltas=deltas,
        predictors=predictors,
        positive_uplifts=positives[:max_leaderboard],
        negative_uplifts=negatives[:max_leaderboard],
    )
