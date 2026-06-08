"""Stage 2 (LOAD): append-only COPY of raw CSVs into the ``raw_yfinance`` schema.

See ``ai_specs/s1_data_pipeline.md`` §2. The load layer is a pure, immutable
bronze mirror of the landing zone — it never updates, deletes, dedups, or upserts.
"""
