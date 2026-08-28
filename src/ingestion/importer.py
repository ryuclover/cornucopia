from pathlib import Path
from typing import Optional, Union
from src.ingestion.csv_parser import CsvParser
from src.ingestion.json_parser import JsonParser
from src.ingestion.models import ImportReport, RowError
from src.ingestion.validator import IngestionValidator
from src.storage.repositories.base import (
    ExecutionRepository,
    InstrumentRepository,
    TraderRepository,
)


class ExecutionImporter:
    """
    Importador unificado de operações com suporte a CSV e JSON,
    validação e relatórios estruturados de auditoria.
    """
    def __init__(
        self,
        execution_repo: ExecutionRepository,
        trader_repo: Optional[TraderRepository] = None,
        instrument_repo: Optional[InstrumentRepository] = None,
    ):
        self.execution_repo = execution_repo
        self.validator = IngestionValidator(trader_repo=trader_repo, instrument_repo=instrument_repo)

    def import_csv(self, content_or_path: Union[str, Path], source_name: Optional[str] = None) -> ImportReport:
        if isinstance(content_or_path, Path) or (isinstance(content_or_path, str) and ("\n" not in content_or_path and Path(content_or_path).exists())):
            path = Path(content_or_path)
            source = source_name or str(path.name)
            csv_text = path.read_text(encoding="utf-8")
        else:
            source = source_name or "csv_string"
            csv_text = str(content_or_path)

        parsed_records, parse_errors = CsvParser.parse_text(csv_text)
        valid_executions, validation_errors = self.validator.validate_and_convert(parsed_records)

        # Inserção idempotente em lote com detecção de conflitos
        inserted, duplicates, conflicts = self.execution_repo.insert_batch(valid_executions)

        conflict_errors = [
            RowError(
                row_number=0,
                field="execution_id",
                reason=f"Conflito de integridade no execution_id '{c.execution_id}': campos divergentes {c.divergent_fields}",
                raw_data={"execution_id": c.execution_id, "divergent_fields": c.divergent_fields}
            )
            for c in conflicts
        ]

        all_errors = parse_errors + validation_errors + conflict_errors
        total_read = len(parsed_records) + len(parse_errors)

        return ImportReport(
            source=source,
            rows_read=total_read,
            inserted=inserted,
            duplicates=duplicates,
            conflicts=len(conflicts),
            rejected=len(all_errors),
            errors=all_errors
        )

    def import_json(self, content_or_path: Union[str, Path], source_name: Optional[str] = None) -> ImportReport:
        if isinstance(content_or_path, Path) or (isinstance(content_or_path, str) and ("\n" not in content_or_path and Path(content_or_path).exists())):
            path = Path(content_or_path)
            source = source_name or str(path.name)
            json_text = path.read_text(encoding="utf-8")
        else:
            source = source_name or "json_string"
            json_text = str(content_or_path)

        parsed_records, parse_errors = JsonParser.parse_text(json_text)
        valid_executions, validation_errors = self.validator.validate_and_convert(parsed_records)

        inserted, duplicates, conflicts = self.execution_repo.insert_batch(valid_executions)

        conflict_errors = [
            RowError(
                row_number=0,
                field="execution_id",
                reason=f"Conflito de integridade no execution_id '{c.execution_id}': campos divergentes {c.divergent_fields}",
                raw_data={"execution_id": c.execution_id, "divergent_fields": c.divergent_fields}
            )
            for c in conflicts
        ]

        all_errors = parse_errors + validation_errors + conflict_errors
        total_read = len(parsed_records) + len(parse_errors)

        return ImportReport(
            source=source,
            rows_read=total_read,
            inserted=inserted,
            duplicates=duplicates,
            conflicts=len(conflicts),
            rejected=len(all_errors),
            errors=all_errors
        )
