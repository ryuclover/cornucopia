import csv
import io
from typing import TextIO
from pydantic import ValidationError
from src.ingestion.models import (
    CanonicalExecutionInput,
    MissingColumnError,
    RowError,
)

REQUIRED_COLUMNS = {"execution_id", "trader_id", "symbol", "timestamp", "side", "quantity", "price"}


class CsvParser:
    """
    Parser robusto para arquivos CSV de execuções com validação de cabeçalho
    e tratamento granular de erros por linha.
    """
    @staticmethod
    def parse_text(csv_content: str) -> tuple[list[CanonicalExecutionInput], list[RowError]]:
        return CsvParser.parse_stream(io.StringIO(csv_content))

    @staticmethod
    def parse_stream(stream: TextIO) -> tuple[list[CanonicalExecutionInput], list[RowError]]:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise MissingColumnError("Arquivo CSV vazio ou sem cabeçalho.")

        actual_fields = {f.strip() for f in reader.fieldnames if f}
        missing = REQUIRED_COLUMNS - actual_fields
        if missing:
            raise MissingColumnError(
                f"Colunas obrigatórias ausentes no CSV: {', '.join(sorted(missing))}. "
                f"Colunas encontradas: {', '.join(sorted(actual_fields))}"
            )

        valid_records: list[CanonicalExecutionInput] = []
        errors: list[RowError] = []

        # Linha 1 é o header, dados começam na linha 2
        for row_idx, row in enumerate(reader, start=2):
            cleaned_row = {k.strip(): v.strip() for k, v in row.items() if k is not None and v is not None}
            try:
                item = CanonicalExecutionInput(**cleaned_row)
                valid_records.append(item)
            except ValidationError as e:
                for err in e.errors():
                    field_name = str(err["loc"][0]) if err["loc"] else "general"
                    reason = err["msg"]
                    errors.append(
                        RowError(
                            row_number=row_idx,
                            field=field_name,
                            reason=reason,
                            raw_data=cleaned_row
                        )
                    )
            except Exception as e:
                errors.append(
                    RowError(
                        row_number=row_idx,
                        field="unknown",
                        reason=str(e),
                        raw_data=cleaned_row
                    )
                )

        return valid_records, errors
