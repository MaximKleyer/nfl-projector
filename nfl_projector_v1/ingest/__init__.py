"""Ingestion: read FPD CSVs and external data into a DuckDB warehouse."""
from .schemas import REPORTS, ReportSchema, RequiredColumn  # noqa: F401
from .readers import read_report  # noqa: F401
from .database import build_warehouse  # noqa: F401
