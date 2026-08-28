import json
from typing import Any
from pydantic import ValidationError
from src.ingestion.models import (
    CanonicalExecutionInput,
    MalformedFileError,
    RowError,
)


class JsonParser:
    """
    Parser robusto para ingestão de operações via JSON (Array ou JSON Lines)
    usando as mesmas regras e contratos do pipeline canônico.
    """
    @staticmethod
    def parse_text(json_content: str) -> tuple[list[CanonicalExecutionInput], list[RowError]]:
        if not json_content.strip():
            raise MalformedFileError("Conteúdo JSON vazio.")

        try:
            parsed = json.loads(json_content)
        except json.JSONDecodeError as e:
            # Tenta parsing como JSON Lines caso não seja um único array
            lines = [l.strip() for l in json_content.strip().splitlines() if l.strip()]
            if lines and lines[0].startswith("{"):
                try:
                    parsed = [json.loads(line) for line in lines]
                except Exception:
                    raise MalformedFileError(f"Formato JSON inválido: {str(e)}")
            else:
                raise MalformedFileError(f"JSON malformado: {str(e)}")

        if not isinstance(parsed, list):
            if isinstance(parsed, dict):
                parsed = [parsed]
            else:
                raise MalformedFileError("JSON raiz deve ser uma lista de objetos ou um único objeto.")

        valid_records: list[CanonicalExecutionInput] = []
        errors: list[RowError] = []

        for idx, item in enumerate(parsed, start=1):
            if not isinstance(item, dict):
                errors.append(
                    RowError(
                        row_number=idx,
                        field="root",
                        reason=f"Elemento esperado dict, recebido {type(item).__name__}",
                        raw_data={"value": item}
                    )
                )
                continue

            try:
                rec = CanonicalExecutionInput(**item)
                valid_records.append(rec)
            except ValidationError as e:
                for err in e.errors():
                    field_name = str(err["loc"][0]) if err["loc"] else "general"
                    reason = err["msg"]
                    errors.append(
                        RowError(
                            row_number=idx,
                            field=field_name,
                            reason=reason,
                            raw_data=item
                        )
                    )
            except Exception as e:
                errors.append(
                    RowError(
                        row_number=idx,
                        field="unknown",
                        reason=str(e),
                        raw_data=item
                    )
                )

        return valid_records, errors
