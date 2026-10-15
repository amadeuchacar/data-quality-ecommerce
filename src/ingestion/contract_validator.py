"""
Validates a DataFrame against the data contract declared in contracts/<table_name>.contract.yaml
BEFORE the data moves to staging.

The contract validates declared expectations set a priori (schema, nullability, uniqueness, primary
key, accepted values, business ranges).

Acts as a simple circuit breaker: if any "blocking" rule is violated, the contract returns passed=False
and the table must not advance to staging.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml

from src.ingestion.type_casting import TYPE_CASTERS

Severity = Literal["blocking", "warning"]


@dataclass
class Violation:
    """
    Represents a data contract violation encountered during validation.

    Attributes:
        rule: the name of the validation rule that failed.
        column: the target column name, or None if table-level.
        detail: human-readable description of the violation.
        severity: impact level, either 'blocking' (fails pipeline) or 'warning'
    """
    rule: str
    column: str | None
    detail: str
    severity: Severity = "blocking"


@dataclass
class ContractValidationResult:
    """
    Contains the overall validation results for a given dataset contract.

    Attributes:
        table: name of the table being validated.
        passed: True if no blocking violations were found, False otherwise.
        violations: list of all detected Violation objects.
    """
    table: str
    passed: bool
    violations: list[Violation] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serializes the validation result into a standard dictionary format."""
        return asdict(self)


def load_contract(table_name: str, contracts_dir: Path) -> dict[str, Any]:
    """
    Loads a table data contract YAML configuration file.

    Args:
        table_name: name of the target table
        contracts_dir: directory where contract YAML files are stored

    Returns:
        A dictionary containing the parsed contract specification
    """
    contract_path = contracts_dir / f"{table_name}.contract.yaml"
    with open(contract_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _check_primary_key(df: pd.DataFrame, primary_key: str | list[str]) -> list[Violation]:
    """Checks that the (possibly composite) primary key is fully populated and unique."""
    key_columns = [primary_key] if isinstance(primary_key, str) else list(primary_key)
    key_label = ", ".join(key_columns)

    missing = [col for col in key_columns if col not in df.columns]
    if missing:
        return []   # Already reported as missing_column

    violations: list[Violation] = []

    n_null_keys = int(df[key_columns].isna().any(axis=1).sum())
    if n_null_keys > 0:
        violations.append(
            Violation(
                rule="primary_key_nulls",
                column=key_label,
                detail=f"{n_null_keys} row(s) with a null value in the primary key ({key_label}).",
                severity="blocking"
            )
        )

    n_duplicates = int(df.duplicated(subset=key_columns, keep=False).sum())
    if n_duplicates > 0:
        violations.append(
            Violation(
                rule="primary_key_violation",
                column=key_label,
                detail=f"{n_duplicates} row(s) share a duplicated primary key ({key_label}).",
                severity="blocking"
            )
        )

    return violations


def validate_dataframe(df: pd.DataFrame, contract: dict[str, Any]) -> ContractValidationResult:
    """
    Validates a pandas DataFrame against a declared data contract.

    Performs schema validation, type coercion checks, nullability constraints,
    uniqueness checks, primary key checks, business ranges, allowed value sets,
    and expected volume SLAs.

    Args:
        df: the pandas dataframe to validate
        contract: the contract specification dictionary

    Returns:
        A ContractValidationResult object detailing any detected violations.
    """
    table_name = contract["table"]
    violations: list[Violation] = []
    contract_columns = contract.get("columns", {})

    # 1. Check for missing columns expected by the contract
    missing_columns = set(contract_columns) - set(df.columns)
    for col in sorted(missing_columns):
        violations.append(
            Violation(
                rule="missing_column",
                column=col,
                detail=f"Column '{col}' expected by the contract is missing from the dataset.",
                severity="blocking"
            )
        )

    # 2. Check for unexpected new columns not in the contract (schema drift - warning by default)
    extra_columns = set(df.columns) - set(contract_columns)
    for col in sorted(extra_columns):
        violations.append(
            Violation(
                rule="unexpected_column",
                column=col,
                detail=f"Column '{col}' is not declared in the contract (potential schema drift).",
                severity="warning"
            )
        )

    # 3. Apply column-level rules defined in the contract
    for col, rules in contract_columns.items():
        if col not in df.columns:
            continue    # Already reported above as missing_column

        series = df[col]
        expected_type = rules.get("type")

        # 3a. Type / coercibility checks
        if expected_type in TYPE_CASTERS:
            coerced = TYPE_CASTERS[expected_type](series)
            newly_null = coerced.isna() & series.notna()
            n_failed_cast = int(newly_null.sum())

            if n_failed_cast > 0:
                violations.append(
                    Violation(
                        rule="type_mismatch",
                        column=col,
                        detail=(
                            f"{n_failed_cast} value(s) could not be safely converted to "
                            f"type '{expected_type}'."
                        ),
                        severity="blocking"
                    )
                )

            series_for_checks = coerced
        else:
            series_for_checks = series

        # 3b. Nullability check
        n_nulls = int(series.isna().sum())
        if not rules.get("nullable", True) and n_nulls > 0:
            violations.append(
                Violation(
                    rule="unexpected_nulls",
                    column=col,
                    detail=f"{n_nulls} null value(s) found in a column declared as not-nullable.",
                    severity="blocking"
                )
            )

        # 3c. Uniqueness check
        if rules.get("unique", False):
            n_duplicates = int(series.duplicated(keep=False).sum())
            if n_duplicates > 0:
                violations.append(
                    Violation(
                        rule="uniqueness_violation",
                        column=col,
                        detail=f"{n_duplicates} duplicate value(s) found in a column declared as unique.",
                        severity="blocking"
                    )
                )

        # 3d. Accepted values check
        allowed_values = rules.get("allowed_values")
        if allowed_values is not None:
            observed = set(series_for_checks.dropna().unique())
            unexpected = observed - set(allowed_values)

            if unexpected:
                violations.append(
                    Violation(
                        rule="unexpected_value",
                        column=col,
                        detail=f"Values outside the allowed list: {sorted(map(str, unexpected))}",
                        severity="blocking"
                    )
                )

        # 3e. Business ranges check (min_value / max_value)
        min_value = rules.get("min_value")
        max_value = rules.get("max_value")
        if min_value is not None or max_value is not None:
            numeric = pd.to_numeric(series_for_checks, errors="coerce")

            if min_value is not None:
                n_below = int((numeric < min_value).sum())
                if n_below > 0:
                    violations.append(
                        Violation(
                            rule="below_min_value",
                            column=col,
                            detail=f"{n_below} value(s) below the allowed minimum ({min_value}).",
                            severity="blocking"
                        )
                    )

            if max_value is not None:
                n_above = int((numeric > max_value).sum())
                if n_above > 0:
                    violations.append(
                        Violation(
                            rule="above_max_value",
                            column=col,
                            detail=f"{n_above} value(s) above the allowed maximum ({max_value}).",
                            severity="blocking"
                        )
                    )

    # 4. Table-level primary key check (supports composite keys)
    primary_key = contract.get("primary_key")
    if primary_key:
        violations.extend(_check_primary_key(df, primary_key))

    # 5. Minimum expected volume check
    min_expected_volume = contract.get("sla", {}).get("min_expected_volume")
    if min_expected_volume is not None and len(df) < min_expected_volume:
        violations.append(
            Violation(
                rule="volume_below_expected",
                column=None,
                detail=f"Received {len(df)} row(s), but a minimum of {min_expected_volume} was expected.",
                severity="blocking"
            )
        )

    passed = not any(v.severity == "blocking" for v in violations)
    return ContractValidationResult(table=table_name, passed=passed, violations=violations)
