"""Módulo de ingestão, parsing e validação de dados de operações."""

from src.ingestion.csv_parser import CsvParser
from src.ingestion.importer import ExecutionImporter
from src.ingestion.json_parser import JsonParser
from src.ingestion.models import (
    CanonicalExecutionInput,
    ImportReport,
    MalformedFileError,
    MissingColumnError,
    RowError,
    StructuralIngestionError,
)
from src.ingestion.validator import IngestionValidator

__all__ = [
    "CanonicalExecutionInput",
    "ImportReport",
    "RowError",
    "StructuralIngestionError",
    "MissingColumnError",
    "MalformedFileError",
    "CsvParser",
    "JsonParser",
    "IngestionValidator",
    "ExecutionImporter",
]
