"""The Explore manifest — a typed, per-dataset registry of the dimensions and
metrics a user can profile / chart / extract, plus an optional geo binding.

This is the single source of truth the whole feature is built on:

* the aggregate service (service.py) turns a manifest-checked request into
  parameterized SQL — only names that appear here can reach the database, so the
  request contract is allow-listed by construction (never string-interpolated
  free text);
* the profile engine (engine.py) enumerates a dataset's predictor dimensions;
* the /explore/datasets endpoint ships the manifest to the frontend so the UI
  renders controls and the data dictionary from it;
* the data-agent folds the same descriptions into its schema grounding.

Every dimension / metric carries a SQL `expr` (over the mart alias ``m`` and, for
geo rollups, the joined ``g`` = marts.dim_postcode_geo) and the physical columns
it `depends_on`. `validate_manifest` checks those columns against the live schema
at startup, the same "tested capability can't drift from declared capability"
guarantee the dbt tests give the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

# Heavy imports (sqlalchemy) are kept out of module scope so the manifest, the
# engine and the NL interpreter stay importable in the dependency-light root test
# venv — the same pattern as agent/provider.py. Only the async validation helpers
# need sqlalchemy, and they import it locally.
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

GEO_TABLE = "marts.dim_postcode_geo"
GEO_ALIAS = "g"
MART_ALIAS = "m"


class ManifestError(RuntimeError):
    """Raised when the manifest declares a column that an existing table lacks."""


@dataclass(frozen=True)
class Dimension:
    """A groupable / filterable attribute of a dataset."""

    name: str
    label: str
    # categorical | ordinal | time | geo. `ordinal` dims (bedroom_band, year_fy,
    # month) keep their natural order in charts instead of being sorted by value.
    kind: str
    expr: str  # SQL expression over MART_ALIAS / GEO_ALIAS
    depends_on: tuple[str, ...]  # physical columns required (for validation)
    source: str = "mart"  # mart | geo | computed
    unit: str | None = None  # optional value formatting hint for the UI
    # How an EQUALITY filter on this dim is compiled. "eq" -> `expr = value`.
    # "month_year"/"month_fy" translate a year value into a sargable half-open
    # range on the underlying month column, so filtering by calendar/financial
    # year uses the month index instead of a full-scan extract().
    filter_kind: str = "eq"
    filter_col: str | None = None

    @property
    def is_ordinal(self) -> bool:
        return self.kind in ("ordinal", "time")

    @property
    def needs_geo_join(self) -> bool:
        return self.source == "geo"


@dataclass(frozen=True)
class Metric:
    """An aggregate measure. Additive metrics sum a column; derived metrics are a
    ratio expression over summed components, so they re-aggregate correctly at any
    grain (a ratio-of-sums, never an average-of-ratios)."""

    name: str
    label: str
    fmt: str  # currency | number | percent
    kind: str  # additive | derived
    expr: str  # aggregate SQL over mart columns (references MART_ALIAS)
    depends_on: tuple[str, ...]  # physical mart columns the expr sums


@dataclass(frozen=True)
class GeoBinding:
    """Ties a dataset's mappable dimension to a topojson layer. Present only when
    the dataset has a column that can be drawn on a map; absent → no map."""

    dimension: str  # dimension name carrying the key (e.g. "postcode")
    layer: str  # frontend topojson layer id (e.g. "poa_nsw")


@dataclass(frozen=True)
class Dataset:
    slug: str
    name: str
    table: str  # schema-qualified physical table
    time_dim: str  # dimension name used as the trend x-axis / cohort time window
    default_metric: str
    dimensions: tuple[Dimension, ...]
    metrics: tuple[Metric, ...]
    geo: GeoBinding | None = None
    _dim_index: dict[str, Dimension] = field(default_factory=dict, compare=False)
    _metric_index: dict[str, Metric] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        self._dim_index.update({d.name: d for d in self.dimensions})
        self._metric_index.update({mtr.name: mtr for mtr in self.metrics})

    def dimension(self, name: str) -> Dimension | None:
        return self._dim_index.get(name)

    def metric(self, name: str) -> Metric | None:
        return self._metric_index.get(name)

    @property
    def predictor_dimensions(self) -> tuple[Dimension, ...]:
        """Dimensions a profile breaks the cohorts down by — everything except the
        raw time axis (kept as the trend x, not a categorical predictor)."""
        return tuple(d for d in self.dimensions if d.name != self.time_dim)

    def to_public(self) -> dict[str, Any]:
        """The shape /explore/datasets ships to the frontend and the agent."""
        return {
            "slug": self.slug,
            "name": self.name,
            "time_dim": self.time_dim,
            "default_metric": self.default_metric,
            "geo": (
                {"dimension": self.geo.dimension, "layer": self.geo.layer} if self.geo else None
            ),
            "dimensions": [
                {
                    "name": d.name,
                    "label": d.label,
                    "kind": d.kind,
                    "source": d.source,
                    "ordinal": d.is_ordinal,
                    "unit": d.unit,
                }
                for d in self.dimensions
            ],
            "metrics": [
                {"name": mtr.name, "label": mtr.label, "format": mtr.fmt, "kind": mtr.kind}
                for mtr in self.metrics
            ],
        }


# ---------------------------------------------------------------------------
# Shared dimension builders (the geo rollups and calendar dims repeat per dataset)
# ---------------------------------------------------------------------------
def _geo_dims() -> list[Dimension]:
    """SA2/SA3/SA4/Greater-Capital rollups, resolved by joining dim_postcode_geo
    on postcode. depends_on names the geo table columns so validation checks them."""
    specs = [
        ("sa2_region", "SA2 region", "sa2_name"),
        ("sa3_region", "SA3 region", "sa3_name"),
        ("sa4_region", "SA4 region", "sa4_name"),
        ("gcc_region", "Greater region", "gcc_name"),
    ]
    return [
        Dimension(
            name=name,
            label=label,
            kind="geo",
            expr=f"{GEO_ALIAS}.{col}",
            depends_on=(col,),
            source="geo",
        )
        for name, label, col in specs
    ]


def _calendar_dims_from_month() -> list[Dimension]:
    """Calendar-year and Australian financial-year dims derived from a month-grain
    `month` column. FY ending June: months Jul-2021..Jun-2022 -> FY 2022."""
    return [
        Dimension(
            name="year",
            label="Calendar year",
            kind="ordinal",
            expr=f"extract(year from {MART_ALIAS}.month)::int",
            depends_on=("month",),
            source="computed",
            filter_kind="month_year",
            filter_col="month",
        ),
        Dimension(
            name="year_fy",
            label="Financial year",
            kind="ordinal",
            expr=f"extract(year from ({MART_ALIAS}.month + interval '6 months'))::int",
            depends_on=("month",),
            source="computed",
            filter_kind="month_fy",
            filter_col="month",
        ),
    ]


def _mart_dim(name: str, label: str, kind: str, column: str | None = None) -> Dimension:
    col = column or name
    return Dimension(
        name=name,
        label=label,
        kind=kind,
        expr=f"{MART_ALIAS}.{col}",
        depends_on=(col,),
        source="mart",
    )


def _additive(name: str, label: str, fmt: str, column: str | None = None) -> Metric:
    col = column or name
    return Metric(
        name=name,
        label=label,
        fmt=fmt,
        kind="additive",
        expr=f"sum({MART_ALIAS}.{col})",
        depends_on=(col,),
    )


def _ratio(name: str, label: str, fmt: str, num: str, den: str, scale: float = 1.0) -> Metric:
    scale_prefix = "" if scale == 1.0 else f"{scale} * "
    expr = f"{scale_prefix}sum({MART_ALIAS}.{num}) / nullif(sum({MART_ALIAS}.{den}), 0)"
    return Metric(name=name, label=label, fmt=fmt, kind="derived", expr=expr, depends_on=(num, den))


# ---------------------------------------------------------------------------
# The datasets
# ---------------------------------------------------------------------------
_SALES = Dataset(
    slug="nsw_sales",
    name="NSW property sales",
    table="marts.property_sales",
    time_dim="month",
    default_metric="avg_sale_price",
    geo=GeoBinding(dimension="postcode", layer="poa_nsw"),
    dimensions=(
        _mart_dim("property_type", "Property type", "categorical"),
        _mart_dim("postcode", "Postcode", "categorical"),
        _mart_dim("suburb", "Suburb", "categorical"),
        _mart_dim("area_band", "Land size band", "ordinal"),
        _mart_dim("zoning", "Zoning", "categorical"),
        _mart_dim("month", "Month", "time"),
        *_calendar_dims_from_month(),
        *_geo_dims(),
    ),
    metrics=(
        _additive("n_sold", "Sales", "number"),
        _additive("total_sale_value", "Total sale value", "currency"),
        _ratio("avg_sale_price", "Avg sale price", "currency", "total_sale_value", "n_sold"),
    ),
)

_RENT = Dataset(
    slug="nsw_rent",
    name="NSW rental bonds",
    table="marts.property_rent",
    time_dim="month",
    default_metric="avg_weekly_rent",
    geo=GeoBinding(dimension="postcode", layer="poa_nsw"),
    dimensions=(
        _mart_dim("property_type", "Property type", "categorical"),
        _mart_dim("postcode", "Postcode", "categorical"),
        _mart_dim("bedroom_band", "Bedrooms", "ordinal"),
        _mart_dim("month", "Month", "time"),
        *_calendar_dims_from_month(),
        *_geo_dims(),
    ),
    metrics=(
        _additive("n_rented", "Bonds", "number"),
        _additive("total_weekly_rent", "Total weekly rent", "currency"),
        _ratio("avg_weekly_rent", "Avg weekly rent", "currency", "total_weekly_rent", "n_rented"),
    ),
)

_YIELD = Dataset(
    slug="nsw_yield",
    name="NSW rental yield",
    table="marts.property_yield",
    time_dim="year",
    default_metric="gross_yield_pct",
    geo=GeoBinding(dimension="postcode", layer="poa_nsw"),
    dimensions=(
        _mart_dim("property_type", "Property type", "categorical"),
        _mart_dim("postcode", "Postcode", "categorical"),
        _mart_dim("year", "Year", "time"),
        *_geo_dims(),
    ),
    metrics=(
        _additive("n_sold", "Sales", "number"),
        _additive("n_rented", "Bonds", "number"),
        _additive("total_sale_value", "Total sale value", "currency"),
        _additive("total_weekly_rent", "Total weekly rent", "currency"),
        _ratio("avg_sale_price", "Avg sale price", "currency", "total_sale_value", "n_sold"),
        _ratio("avg_weekly_rent", "Avg weekly rent", "currency", "total_weekly_rent", "n_rented"),
        # Gross yield = 52 * avg_weekly_rent / avg_sale_price * 100. A ratio of the
        # two average legs, each a ratio-of-sums, so it stays correct at any rollup.
        Metric(
            name="gross_yield_pct",
            label="Gross yield %",
            fmt="percent",
            kind="derived",
            expr=(
                f"5200 * (sum({MART_ALIAS}.total_weekly_rent) / "
                f"nullif(sum({MART_ALIAS}.n_rented), 0)) / "
                f"nullif(sum({MART_ALIAS}.total_sale_value) / "
                f"nullif(sum({MART_ALIAS}.n_sold), 0), 0)"
            ),
            depends_on=("total_weekly_rent", "n_rented", "total_sale_value", "n_sold"),
        ),
    ),
)

MANIFEST: dict[str, Dataset] = {ds.slug: ds for ds in (_SALES, _RENT, _YIELD)}


def get_dataset(slug: str) -> Dataset | None:
    return MANIFEST.get(slug)


# ---------------------------------------------------------------------------
# Startup validation — declared columns must exist in the live schema
# ---------------------------------------------------------------------------
async def _table_columns(conn: AsyncConnection, schema: str, table: str) -> set[str] | None:
    """Column names of a table, or None if the table doesn't exist yet."""
    from sqlalchemy import text

    exists = (
        await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": schema, "t": table},
        )
    ).first()
    if exists is None:
        return None
    rows = (
        await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": schema, "t": table},
        )
    ).all()
    return {r[0] for r in rows}


async def validate_manifest(conn: AsyncConnection) -> list[str]:
    """Check every declared column against the live schema.

    Raises ManifestError if an EXISTING table is missing a declared column (a real
    drift bug). Tables that don't exist yet (pipeline still building on first boot)
    are reported as warnings and returned, not raised — so the API can start before
    the one-shot pipeline job finishes without masking genuine drift.
    """
    geo_schema, geo_table = GEO_TABLE.split(".")
    geo_cols = await _table_columns(conn, geo_schema, geo_table)
    warnings: list[str] = []
    problems: list[str] = []

    for ds in MANIFEST.values():
        schema, table = ds.table.split(".")
        cols = await _table_columns(conn, schema, table)
        if cols is None:
            warnings.append(f"{ds.slug}: table {ds.table} not present yet")
            continue
        for dim in ds.dimensions:
            required = geo_cols if dim.needs_geo_join else cols
            if dim.needs_geo_join and required is None:
                warnings.append(f"{ds.slug}.{dim.name}: {GEO_TABLE} not present yet")
                continue
            missing = [c for c in dim.depends_on if c not in (required or set())]
            if missing:
                where = GEO_TABLE if dim.needs_geo_join else ds.table
                problems.append(f"{ds.slug}.{dim.name}: {where} missing column(s) {missing}")
        for mtr in ds.metrics:
            missing = [c for c in mtr.depends_on if c not in cols]
            if missing:
                problems.append(f"{ds.slug}.{mtr.name}: {ds.table} missing column(s) {missing}")
        if ds.geo and ds.dimension(ds.geo.dimension) is None:
            problems.append(
                f"{ds.slug}: geo binding references unknown dimension {ds.geo.dimension}"
            )
        if ds.dimension(ds.time_dim) is None:
            problems.append(f"{ds.slug}: time_dim {ds.time_dim} is not a declared dimension")
        if ds.metric(ds.default_metric) is None:
            problems.append(
                f"{ds.slug}: default_metric {ds.default_metric} is not a declared metric"
            )

    if problems:
        raise ManifestError("Explore manifest drifted from the schema: " + "; ".join(problems))
    return warnings
