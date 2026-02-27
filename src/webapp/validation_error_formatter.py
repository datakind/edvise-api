"""Formats validation errors into human-readable messages.

This module converts technical validation errors (with canonical column names,
row indices, and check types) into user-friendly error messages that include:
- User-friendly column names (raw headers from the file)
- Row numbers (1-indexed for users)
- Clear explanations of what's wrong
- Guidance on how to fix issues
"""

import logging
import math
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .validation import HardValidationError

# Logger for formatter errors
_logger = logging.getLogger(__name__)

# Optional import for normalize_col (used in _format_extra_columns)
# Imported at module level with try/except to avoid breaking environments without it
# This is safe because:
# 1. We handle ImportError/AttributeError gracefully
# 2. We fall back to function-level import if module-level fails
# 3. We don't cache stale functions (each call to
#    _get_normalize_col_function checks availability)
try:
    from .validation import normalize_col as _normalize_col_func

    HAS_NORMALIZE_COL = True
except (ImportError, AttributeError):
    # Safe fallback - function will try to import again if needed
    HAS_NORMALIZE_COL = False
    _normalize_col_func = None  # type: ignore

# PII indicators - high-risk indicators that should always trigger masking
# These use substring matching because they're unambiguous
PII_HIGH_RISK_INDICATORS = {
    "email",
    "ssn",
    "social_security",
    "phone",
    "telephone",
    "date_of_birth",
    "dob",
    "birthdate",
    "birth_date",
    "passport",
    "driver_license",
    "license_number",
    "credit_card",
    "bank_account",
    "account_number",
    "ip_address",
    "mac_address",
}

# PII indicators - medium-risk indicators that require exact or token matching
# These are common words that appear in non-PII contexts (e.g., "course_name")
# Note: "name" alone is excluded to avoid false positives, but patterns like
# "*_name" (student_name, employee_name, etc.) are caught via token matching
PII_MEDIUM_RISK_INDICATORS = {
    "first_name",
    "last_name",
    "middle_name",
    "full_name",
    "address",
    # Note: student_id is excluded; it is a standard de-identified identifier for all institution types.
    # Note: Patterns like "student_name", "employee_name", "guardian_name" will
    # be caught because they contain "name" as a token, but we check
    # false positives first
}

# Common non-PII column name patterns that should NOT be flagged
# These are compound names where a PII indicator appears but isn't actually PII
PII_FALSE_POSITIVE_PATTERNS = {
    "student_id",  # Standard de-identified identifier for all institution types
    "course_name",
    "school_name",
    "district_name",
    "institution_name",
    "class_name",
    "section_name",
    "program_name",
    "department_name",
    "file_name",
    "column_name",
    "table_name",
    "field_name",
}

# Limits for error message generation
MAX_VALUE_LENGTH = 200  # Maximum length for non-PII values in error messages
MAX_MESSAGE_LENGTH = 10000  # Maximum total error message length
MAX_ERROR_EXAMPLES = 10  # Maximum examples per column

# Human-readable messages for PDP schema check names (edvise repo schemas).
# Keeps messaging consistent without changing the edvise package.
PDP_EDVISE_CHECK_MESSAGES: Dict[str, str] = {
    "check_num_institutions": "All rows must have the same institution ID.",
    "num_credits_attempted_ge_earned": "Credits attempted must be greater than or equal to credits earned for each course row.",
    "unique": "Duplicate rows are not allowed; each row must be unique (e.g. unique student_id for cohort, or unique combination of student_id, term, and course for course files).",
    "column_in_dataframe": "This column is required but is missing from the file.",
}


def _sanitize_string(value: str, max_length: int = MAX_VALUE_LENGTH) -> str:
    """
    Sanitize string values to prevent injection and limit length.

    Args:
        value: String to sanitize
        max_length: Maximum length before truncation

    Returns:
        Sanitized string
    """
    if not isinstance(value, str):
        value = str(value)

    # Truncate if too long
    if len(value) > max_length:
        value = value[:max_length] + "..."

    # Replace newlines and control characters to prevent formatting issues
    value = value.replace("\n", " ").replace("\r", " ")
    # Remove other control characters
    value = "".join(char for char in value if ord(char) >= 32 or char in "\t")

    return value


def _format_missing_required(error: "HardValidationError") -> Optional[str]:
    """Format missing required columns error message."""
    if not error.missing_required:
        return None

    # Get mappings
    canon_to_raw = _get_canon_to_raw_mapping(error)
    merged_specs = getattr(error, "merged_specs", {}) or {}

    missing_display = []
    for canon in error.missing_required:
        # Skip invalid entries
        if not canon or not isinstance(canon, str):
            continue

        # Get raw column name (user-friendly)
        if isinstance(canon_to_raw, dict):
            raw = canon_to_raw.get(canon, canon)
        else:
            raw = str(canon)
        spec = merged_specs.get(canon, {}) if isinstance(merged_specs, dict) else {}
        desc = spec.get("description", "") if isinstance(spec, dict) else ""

        # Sanitize column names and descriptions
        raw = _sanitize_string(str(raw), max_length=100)
        desc = _sanitize_string(str(desc), max_length=200) if desc else ""

        # Format: 'Column Name' (description if available)
        if desc:
            missing_display.append(f"'{raw}' ({desc})")
        else:
            missing_display.append(f"'{raw}'")

    # Handle case where all entries were invalid (missing_display is empty)
    if not missing_display:
        return (
            "Missing required columns detected. "
            "Please check your file and ensure all required columns are present."
        )

    return (
        f"Missing required columns: {', '.join(missing_display)}. "
        f"These columns must be present in your file."
    )


def _get_normalize_col_function() -> Optional[Any]:
    """Get normalize_col function if available."""
    if HAS_NORMALIZE_COL and _normalize_col_func:
        return _normalize_col_func
    try:
        from .validation import normalize_col

        return normalize_col
    except (ImportError, AttributeError):
        return None


def _find_raw_names_in_mapping(
    norm_col: str, raw_to_canon: Dict[str, str]
) -> List[str]:
    """Find all raw names that map to norm_col in raw_to_canon."""
    raw_names = []
    for raw, canon in raw_to_canon.items():
        if canon == norm_col:
            raw_names.append(raw)
    return raw_names


def _find_raw_names_via_normalize(
    norm_col: str, raw_to_canon: Dict[str, str], normalize_col: Any
) -> List[str]:
    """Find raw names by normalizing each raw name and comparing."""
    raw_names = []
    for raw in raw_to_canon.keys():
        if normalize_col(raw) == norm_col:
            raw_names.append(raw)
    return raw_names


def _find_raw_names_for_normalized(
    norm_col: str,
    raw_to_canon: Dict[str, str],
    normalize_col: Optional[Any],
) -> List[str]:
    """Find all raw names that normalize to the given normalized column name."""
    if not isinstance(raw_to_canon, dict):
        return []

    # Find all raw names that map to this normalized column in raw_to_canon
    # This uses the existing mapping rather than re-normalizing (more accurate)
    raw_names = _find_raw_names_in_mapping(norm_col, raw_to_canon)

    # If no matches found in raw_to_canon, try reverse lookup with normalize_col
    if not raw_names and normalize_col:
        raw_names = _find_raw_names_via_normalize(norm_col, raw_to_canon, normalize_col)

    return raw_names


def _choose_display_name_for_extra_column(norm_col: str, raw_names: List[str]) -> str:
    """Choose display name for extra column using deterministic rules."""
    if not raw_names:
        return str(norm_col)

    # Check for exact match first
    exact_match = next((raw for raw in raw_names if raw == norm_col), None)
    if exact_match:
        return exact_match

    if len(raw_names) == 1:
        return raw_names[0]

    # Multiple matches: show first one (deterministic) with note if > 1
    if len(raw_names) > 2:
        return f"{raw_names[0]} (and {len(raw_names) - 1} similar)"

    # Exactly 2 matches: show both
    return f"{raw_names[0]} or {raw_names[1]}"


def _format_extra_columns(error: "HardValidationError") -> Optional[str]:
    """Format extra columns error message."""
    if not error.extra_columns:
        return None

    raw_to_canon = getattr(error, "raw_to_canon", {}) or {}
    normalize_col = _get_normalize_col_function()
    extra_display = []

    for norm_col in error.extra_columns:
        if norm_col is None:
            continue

        raw_names = _find_raw_names_for_normalized(
            norm_col, raw_to_canon, normalize_col
        )
        display_name = _choose_display_name_for_extra_column(norm_col, raw_names)
        extra_display.append(f"'{_sanitize_string(display_name, max_length=100)}'")

    return (
        f"Unexpected columns found: {', '.join(extra_display)}. "
        f"Please remove these columns or rename them to match the expected schema."
    )


def _try_to_dict_records(failure_cases: Any) -> Optional[List[dict]]:
    """Try to convert failure_cases using to_dict(orient='records')."""
    if not hasattr(failure_cases, "to_dict"):
        return None

    try:
        result = failure_cases.to_dict(orient="records")
        if isinstance(result, list) and all(isinstance(item, dict) for item in result):
            return result
    except (AttributeError, ValueError, TypeError) as e:
        _logger.debug("Failed to convert with to_dict(orient='records'): %s", e)

    return None


def _convert_dict_of_dicts_to_list(result: dict) -> Optional[List[dict]]:
    """Convert {col: {row: val}} format to list of dicts."""
    if not isinstance(result, dict) or not result:
        return None

    first_key = next(iter(result))
    if not isinstance(result[first_key], dict) or not result[first_key]:
        return None

    # Get max row index from all columns (defensive: handle variable-length columns)
    max_row_idx = max(
        (max(inner_dict.keys()) if isinstance(inner_dict, dict) and inner_dict else -1)
        for inner_dict in result.values()
        if isinstance(inner_dict, dict)
    )

    if max_row_idx < 0:
        return None

    rows = []
    for row_idx in range(max_row_idx + 1):
        row_dict = {
            col: (
                result[col].get(row_idx, None)
                if isinstance(result[col], dict)
                else None
            )
            for col in result
        }
        rows.append(row_dict)

    return rows


def _try_to_dict_fallback(failure_cases: Any) -> Optional[List[dict]]:
    """Try fallback conversion using to_dict() without orient."""
    if not hasattr(failure_cases, "to_dict"):
        return None

    try:
        result = failure_cases.to_dict()
        # Try dict of dicts conversion
        converted = _convert_dict_of_dicts_to_list(result)
        if converted:
            return converted
        # If single dict, wrap in list
        if isinstance(result, dict):
            return [result]
    except (AttributeError, TypeError, ValueError, KeyError) as fallback_err:
        _logger.debug("Fallback to_dict() conversion also failed: %s", fallback_err)

    return None


def _normalize_failure_cases(failure_cases: Any) -> List[dict]:
    """
    Normalize failure_cases to a list of dicts.

    Handles multiple formats:
    - List of dicts (already normalized)
    - pandas DataFrame or DataFrame-like objects (converts with to_dict("records"))
    - Other iterables (converts to list, filters dicts)

    Uses behavior-based detection (try to_dict("records")) rather than type-shape checks
    to handle various tabular types (pandas, polars, etc.).
    """
    if failure_cases is None:
        return []

    # Try behavior-based detection: to_dict("records")
    result = _try_to_dict_records(failure_cases)
    if result is not None:
        return result

    # Try fallback: to_dict() without orient
    result = _try_to_dict_fallback(failure_cases)
    if result is not None:
        return result

    # Check for empty (safe for lists, dicts, etc.)
    try:
        if not failure_cases:
            return []
    except (ValueError, TypeError):
        # DataFrame/array-like objects can raise on truthiness check
        pass

    # Handle list of dicts
    if isinstance(failure_cases, list):
        return [case for case in failure_cases if isinstance(case, dict)]

    # Try to convert other iterables to list
    try:
        converted = list(failure_cases)
        return [case for case in converted if isinstance(case, dict)]
    except (TypeError, ValueError):
        return []


def _check_and_handle_nan(row_idx: Any) -> Optional[Any]:
    """Check if row_idx is NaN and return None if so."""
    try:
        if isinstance(row_idx, float) and math.isnan(row_idx):
            return None
    except (TypeError, AttributeError):
        pass
    return row_idx


def _convert_numpy_integer(row_idx: Any) -> Any:
    """Convert numpy integer types to Python int."""
    try:
        import numpy as np

        if isinstance(row_idx, (np.integer, np.int64, np.int32)):
            return int(row_idx)
    except (ImportError, ValueError, OverflowError):
        pass
    return row_idx


def _normalize_integer_index(row_idx: int) -> Optional[int]:
    """Normalize integer row index to 1-indexed."""
    try:
        if row_idx >= 0:
            return row_idx + 1  # Convert 0-indexed to 1-indexed
    except (ValueError, OverflowError):
        pass
    return None


def _normalize_float_index(row_idx: float) -> Optional[Any]:
    """Normalize float row index (only whole numbers)."""
    try:
        if row_idx.is_integer():
            idx_int = int(row_idx)
            if idx_int >= 0:
                return idx_int + 1  # Convert 0-indexed to 1-indexed
        # Non-integer float - return sanitized string instead of misleading conversion
        return _sanitize_string(str(row_idx), max_length=50)
    except (ValueError, OverflowError, AttributeError):
        return None


def _normalize_row_index(row_idx: Any) -> Optional[Any]:
    """
    Normalize row index to a displayable format.

    Handles:
    - int/float: Convert to 1-indexed (Pandera uses 0-indexed)
    - numpy.integer: Convert to int
    - NaN/None: Return None
    - Other types: Return sanitized string representation
    """
    if row_idx is None:
        return None

    # Handle NaN
    checked = _check_and_handle_nan(row_idx)
    if checked is None:
        return None

    # Handle numpy integer types
    row_idx = _convert_numpy_integer(checked)

    # Handle int
    if isinstance(row_idx, int):
        return _normalize_integer_index(row_idx)

    # Handle float
    if isinstance(row_idx, float):
        return _normalize_float_index(row_idx)

    # For other types (e.g., string indices, MultiIndex), return sanitized string
    return _sanitize_string(str(row_idx), max_length=50)


def _group_failure_cases_by_column(failure_cases: List[dict]) -> Dict[str, List[dict]]:
    """
    Group failure cases by column name.

    Schema-level checks (without a column) are grouped under "_schema_level".
    """
    by_column: Dict[str, List[dict]] = {}
    for case in failure_cases:
        if not isinstance(case, dict):
            continue

        # Handle column name - use "_schema_level" for schema-level checks
        canon_col = case.get("column")
        if not canon_col or not isinstance(canon_col, str):
            canon_col = "_schema_level"
        else:
            canon_col = str(canon_col)

        # Normalize row index
        row_idx = case.get("index", -1)
        row_num = _normalize_row_index(row_idx)

        check = case.get("check", "validation")
        value = case.get("failure_case", "N/A")

        if canon_col not in by_column:
            by_column[canon_col] = []
        by_column[canon_col].append(
            {
                "row": row_num,
                "check": str(check) if check else "validation",
                "value": value,
            }
        )
    return by_column


def _is_pii_column(column_name: str) -> bool:
    """
    Check if a column name indicates PII using a tiered approach.

    Tier 1: High-risk indicators (substring match) - unambiguous
    Tier 2: Medium-risk indicators (exact/token match) - avoid false positives
    Tier 3: False positive patterns (explicit denylist)
    Tier 4: Schema metadata (if available in future)

    Args:
        column_name: Column name to check (can be canonical or raw)

    Returns:
        True if column likely contains PII, False otherwise
    """
    if not column_name or not isinstance(column_name, str):
        return False

    col_lower = column_name.lower()

    # Tier 3: Check false positive patterns first (explicit denylist)
    if col_lower in PII_FALSE_POSITIVE_PATTERNS:
        return False

    # Tier 1: High-risk indicators - substring matching (unambiguous)
    if any(indicator in col_lower for indicator in PII_HIGH_RISK_INDICATORS):
        return True

    # Tier 2: Medium-risk indicators - exact match or token match (avoid false positives)
    # Split on common separators: underscore, hyphen, space
    tokens = set()
    for sep in ["_", "-", " "]:
        if sep in col_lower:
            tokens.update(col_lower.split(sep))
    tokens.add(col_lower)  # Also check full name

    # Check if any token exactly matches a medium-risk indicator
    if any(token in PII_MEDIUM_RISK_INDICATORS for token in tokens):
        return True

    # Also check for "*_name" patterns (student_name, employee_name, etc.)
    # These are likely PII unless in the false positive denylist (already checked above)
    # We check this after medium-risk indicators to catch patterns like "student_name"
    if col_lower.endswith("_name") and col_lower != "name":
        # Already checked false positives above, so if we get here, it's likely PII
        return True

    return False


def _mask_pii_value(value: Any, max_visible_chars: int = 2) -> str:
    """
    Mask PII values to prevent exposure in error messages.

    Args:
        value: The value to mask
        max_visible_chars: Maximum characters to show at start/end

    Returns:
        Masked value string (e.g., "AB***XY" for "ABCDEFGHXY")
    """
    if value is None:
        return "N/A"

    value_str = str(value)

    # Handle empty strings - don't mask as "****" (would be misleading)
    if not value_str or not value_str.strip():
        return "<redacted>"

    # Limit length to prevent DoS
    if len(value_str) > MAX_VALUE_LENGTH:
        value_str = value_str[:MAX_VALUE_LENGTH]

    length = len(value_str)

    # For very short values (length <= 4), show 4 asterisks to avoid leaking length
    # This prevents inference: "**" vs "***" vs "****" would reveal length
    if length <= max_visible_chars * 2:
        return "*" * 4

    # Show first and last few characters with masking in between
    start = value_str[:max_visible_chars]
    end = value_str[-max_visible_chars:] if length > max_visible_chars * 2 else ""
    masked = "*" * min(length - (max_visible_chars * 2), 6)

    return f"{start}{masked}{end}"


def _format_single_error_example(err: dict, is_pii: bool, spec: dict) -> Optional[str]:
    """Format a single error example."""
    if not isinstance(err, dict):
        return None

    row_num = err.get("row")
    # Handle row number display - can be int (1-indexed) or string (for non-int indices)
    if row_num is None:
        row_msg = "Unknown row"
    elif isinstance(row_num, (int, str)):
        row_msg = f"Row {row_num}"
    else:
        # Fallback for unexpected types
        row_msg = f"Row {_sanitize_string(str(row_num), max_length=20)}"

    # Mask PII values before displaying
    raw_value = err.get("value", "N/A")
    if is_pii:
        display_value = _mask_pii_value(raw_value)
        value_msg = f"found '{display_value}' (value masked for privacy)"
    else:
        # Truncate non-PII values
        value_str = str(raw_value)
        if len(value_str) > MAX_VALUE_LENGTH:
            value_str = value_str[:MAX_VALUE_LENGTH] + "..."
        value_msg = f"found '{_sanitize_string(value_str)}'"

    # Human-readable check descriptions
    check_type = err.get("check", "validation")
    check_msg = _format_check_error(check_type, spec, err.get("value"))

    return f"{row_msg}: {check_msg}. Current value: {value_msg}"


def _get_canon_to_raw_mapping(error: "HardValidationError") -> Dict[str, str]:
    """
    Get canon_to_raw mapping, deriving from raw_to_canon if needed.

    Handles non-bijective mappings (multiple raw names map to same canonical):
    - Uses first raw name seen for each canonical
    - Falls back to canonical name if no mapping exists
    """
    # Prefer canon_to_raw if available
    canon_to_raw = getattr(error, "canon_to_raw", {}) or {}
    if isinstance(canon_to_raw, dict) and canon_to_raw:
        return canon_to_raw

    # Derive from raw_to_canon (inverse mapping)
    raw_to_canon = getattr(error, "raw_to_canon", {}) or {}
    if isinstance(raw_to_canon, dict) and raw_to_canon:
        # Build inverse, using first raw name for each canonical (handles non-bijective)
        derived: Dict[str, str] = {}
        for raw, canon in raw_to_canon.items():
            if canon not in derived:  # First occurrence wins
                derived[canon] = raw
        return derived

    return {}


def _format_schema_level_errors(errors: List[dict]) -> List[str]:
    """Format schema-level validation errors (no column)."""
    messages: List[str] = []
    spec: dict = {}  # No column-specific spec for schema-level errors
    is_pii = False  # Schema-level errors don't contain PII values

    error_examples = []
    for err in errors[:MAX_ERROR_EXAMPLES]:
        example = _format_single_error_example(err, is_pii, spec)
        if example:
            error_examples.append(example)

    if error_examples:
        messages.append(
            "File-level validation errors:\n"
            + "\n".join(f"  • {ex}" for ex in error_examples)
        )

    if len(errors) > MAX_ERROR_EXAMPLES:
        messages.append(
            f"File-level: {len(errors) - MAX_ERROR_EXAMPLES} additional errors found. "
            f"Please review your file structure."
        )

    return messages


def _format_column_specific_errors(
    canon_col: str,
    errors: List[dict],
    canon_to_raw: Dict[str, str],
    merged_specs: Dict[str, dict],
) -> List[str]:
    """Format validation errors for a specific column."""
    messages = []

    # Validate and normalize column name
    if not canon_col or not isinstance(canon_col, str):
        canon_col = "unknown"

    # Get raw column name (user-friendly)
    raw_col = (
        canon_to_raw.get(canon_col, canon_col)
        if isinstance(canon_to_raw, dict)
        else canon_col
    )
    spec = merged_specs.get(canon_col, {}) if isinstance(merged_specs, dict) else {}

    # Sanitize column name
    raw_col = _sanitize_string(str(raw_col), max_length=100)

    # Check if this column contains PII
    is_pii = _is_pii_column(str(canon_col)) or _is_pii_column(raw_col)

    # Group errors and format (limit to MAX_ERROR_EXAMPLES)
    error_examples = []
    for err in errors[:MAX_ERROR_EXAMPLES]:
        example = _format_single_error_example(err, is_pii, spec)
        if example:
            error_examples.append(example)

    if error_examples:
        messages.append(
            f"Column '{raw_col}' has validation errors:\n"
            + "\n".join(f"  • {ex}" for ex in error_examples)
        )

    if len(errors) > MAX_ERROR_EXAMPLES:
        messages.append(
            f"Column '{raw_col}': {len(errors) - MAX_ERROR_EXAMPLES} additional errors found. "
            f"Please review all rows for this column."
        )

    return messages


def _format_column_validation_errors(
    canon_col: str,
    errors: List[dict],
    error: "HardValidationError",
) -> List[str]:
    """Format validation errors for a single column or schema-level errors."""
    # Get mappings
    canon_to_raw = _get_canon_to_raw_mapping(error)
    merged_specs = getattr(error, "merged_specs", {}) or {}

    # Handle schema-level errors (no column)
    if canon_col == "_schema_level":
        return _format_schema_level_errors(errors)

    # Column-specific errors
    return _format_column_specific_errors(canon_col, errors, canon_to_raw, merged_specs)


def _format_schema_validation_errors(error: "HardValidationError") -> List[str]:
    """Format schema validation errors with row numbers."""
    messages: List[str] = []

    failure_cases = _normalize_failure_cases(error.failure_cases)
    if not failure_cases:
        return messages

    by_column = _group_failure_cases_by_column(failure_cases)

    # Sort columns for deterministic output: schema-level first, then alphabetical
    sorted_columns = sorted(
        by_column.keys(),
        key=lambda x: (x != "_schema_level", x),  # _schema_level comes first
    )

    # Format errors by column
    for canon_col in sorted_columns:
        errors = by_column[canon_col]
        column_messages = _format_column_validation_errors(canon_col, errors, error)
        messages.extend(column_messages)

    return messages


def _add_message_if_fits(
    messages: List[str],
    current_length: int,
    new_msg: Optional[str],
    max_length: int = MAX_MESSAGE_LENGTH,
) -> int:
    """
    Add message to list if it fits within size limit. Returns updated length.

    Uses <= for comparison to allow messages up to max_length.
    Accounts for "\n\n" separator that will be added between messages.
    """
    if not new_msg:
        return current_length

    # Account for separator: "\n\n" (2 chars) if not first message
    separator_len = 2 if messages else 0
    total_len = current_length + len(new_msg) + separator_len

    if total_len <= max_length:
        messages.append(new_msg)
        return total_len

    return current_length


def _format_decode_error(
    error: "HardValidationError", current_length: int
) -> tuple[List[str], int]:
    """Format decode error section. Returns (messages, updated_length)."""
    messages: List[str] = []
    try:
        if hasattr(error, "schema_errors") and error.schema_errors == "decode_error":
            if hasattr(error, "failure_cases") and error.failure_cases:
                decode_msg = (
                    str(error.failure_cases[0])
                    if isinstance(error.failure_cases, list)
                    else str(error.failure_cases)
                )
                decode_msg = _sanitize_string(decode_msg, max_length=200)
                decode_text = (
                    f"File encoding error: {decode_msg}. "
                    f"Please ensure your file is saved as UTF-8 encoding."
                )
                current_length = _add_message_if_fits(
                    messages, current_length, decode_text
                )
    except (AttributeError, TypeError, ValueError, IndexError) as e:
        _logger.debug("Error formatting decode error: %s", e, exc_info=True)
    return messages, current_length


def _format_generic_schema_error(
    error: "HardValidationError", current_length: int
) -> tuple[List[str], int]:
    """Format generic schema error section. Returns (messages, updated_length)."""
    messages: List[str] = []
    try:
        if (
            hasattr(error, "schema_errors")
            and error.schema_errors
            and error.schema_errors != "decode_error"
        ):
            schema_error_text = _sanitize_string(
                str(error.schema_errors), max_length=500
            )
            schema_text = (
                f"Schema validation error: {schema_error_text}. "
                f"Please check your file format and column definitions."
            )
            current_length = _add_message_if_fits(messages, current_length, schema_text)
    except (AttributeError, TypeError, ValueError) as e:
        _logger.debug("Error formatting generic schema error: %s", e, exc_info=True)
    return messages, current_length


def _format_all_error_sections(error: "HardValidationError") -> List[str]:
    """Format all error sections and return messages with length tracking."""
    messages: List[str] = []
    current_length = 0

    # Missing required columns
    try:
        missing_msg = _format_missing_required(error)
        current_length = _add_message_if_fits(messages, current_length, missing_msg)
    except Exception as e:
        _logger.debug("Error formatting missing required columns: %s", e, exc_info=True)

    # Extra columns
    try:
        extra_msg = _format_extra_columns(error)
        current_length = _add_message_if_fits(messages, current_length, extra_msg)
    except Exception as e:
        _logger.debug("Error formatting extra columns: %s", e, exc_info=True)

    # Schema validation errors (with row numbers)
    try:
        schema_msgs = _format_schema_validation_errors(error)
        for msg in schema_msgs:
            separator_len = 2 if messages else 0
            if current_length + len(msg) + separator_len <= MAX_MESSAGE_LENGTH:
                messages.append(msg)
                current_length += len(msg) + separator_len
            else:
                # Add truncation notice
                truncation_msg = "Additional validation errors were truncated due to message size limits."
                current_length = _add_message_if_fits(
                    messages, current_length, truncation_msg
                )
                break
    except Exception as e:
        _logger.debug("Error formatting schema validation errors: %s", e, exc_info=True)

    # Decode errors
    try:
        decode_msgs, current_length = _format_decode_error(error, current_length)
        messages.extend(decode_msgs)
    except Exception as e:
        _logger.debug("Error formatting decode errors: %s", e, exc_info=True)

    # Generic schema errors
    try:
        schema_msgs, current_length = _format_generic_schema_error(
            error, current_length
        )
        messages.extend(schema_msgs)
    except Exception as e:
        _logger.debug("Error formatting generic schema errors: %s", e, exc_info=True)

    return messages


def _get_fallback_message(error: "HardValidationError") -> str:
    """Get fallback error message if formatting fails."""
    try:
        fallback = str(error)
        # If str(error) is empty or just whitespace, use default message
        if not fallback or not fallback.strip():
            return "Validation error occurred. Please check your file format and try again."
        return _sanitize_string(fallback, max_length=MAX_MESSAGE_LENGTH)
    except (AttributeError, TypeError, ValueError) as e:
        _logger.debug("Error getting fallback message: %s", e, exc_info=True)
        return "Validation error occurred. Please check your file format and try again."


def format_validation_error(
    error: "HardValidationError",
) -> str:
    """
    Convert technical validation errors to human-readable messages.

    Args:
        error: HardValidationError instance with validation failure details

    Returns:
        Formatted string with user-friendly error messages

    Raises:
        ValueError: If error is None or invalid
    """
    # Input validation
    if error is None:
        raise ValueError("error cannot be None")

    if not hasattr(error, "missing_required"):
        # Invalid error object - return safe fallback
        return "Validation error occurred. Please check your file format and try again."

    messages = _format_all_error_sections(error)

    if not messages:
        return _get_fallback_message(error)

    result = "\n\n".join(messages)
    # Final safety check - truncate if somehow exceeded
    if len(result) > MAX_MESSAGE_LENGTH:
        result = (
            result[: MAX_MESSAGE_LENGTH - 50]
            + "\n\n[Error message truncated due to size limits.]"
        )

    return result


def _format_str_length_error(check_spec: Optional[dict]) -> str:
    """Format str_length check error message."""
    kwargs = (
        check_spec.get("kwargs", {})
        if check_spec and isinstance(check_spec, dict)
        else {}
    )
    min_val = kwargs.get("min_value") if isinstance(kwargs, dict) else None
    max_val = kwargs.get("max_value") if isinstance(kwargs, dict) else None

    if min_val is not None and max_val is not None:
        return f"Value must be between {min_val} and {max_val} characters long"
    elif min_val is not None:
        return f"Value must be at least {min_val} characters long"
    elif max_val is not None:
        return f"Value must be at most {max_val} characters long"
    return "Value length validation failed"


def _categorize_values_for_sorting(
    allowed_list: List[Any],
) -> tuple[List[tuple[float, str, Any]], List[Any]]:
    """Categorize values into numeric and non-numeric for sorting."""
    numeric_values = []
    non_numeric_values = []

    for val in allowed_list:
        try:
            numeric_val = float(val)
            # Handle NaN and inf - push to non-numeric bucket for predictable ordering
            if math.isnan(numeric_val) or math.isinf(numeric_val):
                non_numeric_values.append(val)
            else:
                # Use (numeric_value, str(original)) as sort key to break ties deterministically
                numeric_values.append((numeric_val, str(val), val))
        except (ValueError, TypeError, OverflowError):
            non_numeric_values.append(val)

    return numeric_values, non_numeric_values


def _sort_allowed_values(allowed_list: List[Any]) -> List[Any]:
    """Sort allowed values deterministically (numeric first, then non-numeric)."""
    try:
        numeric_values, non_numeric_values = _categorize_values_for_sorting(
            allowed_list
        )
        # Sort numeric values by (numeric, string) tuple, non-numeric by string
        numeric_sorted = [val for _, _, val in sorted(numeric_values)]
        non_numeric_sorted = sorted(non_numeric_values, key=str)
        return numeric_sorted + non_numeric_sorted
    except (TypeError, ValueError, ImportError):
        # If sorting fails (or math not available), use string sort
        try:
            return sorted(allowed_list, key=str)
        except TypeError:
            # If items aren't comparable, use original order
            return allowed_list


def _format_isin_error(check_spec: Optional[dict]) -> str:
    """Format isin/is_in check error message."""
    args = (
        check_spec.get("args", [])
        if check_spec and isinstance(check_spec, dict)
        else []
    )
    if not args or not isinstance(args[0], (list, set, tuple)):
        return "Value must be one of the allowed values"

    allowed_list = list(args[0])[:10]  # Limit to 10 values for readability
    allowed = _sort_allowed_values(allowed_list)

    if len(allowed) <= 5:
        return f"Value must be one of: {', '.join(map(str, allowed))}"

    return f"Value must be one of the allowed values (e.g., {', '.join(map(str, allowed[:5]))}, ...)"


def _format_matches_error(check_spec: Optional[dict]) -> str:
    """Format matches/str_matches check error message."""
    args = (
        check_spec.get("args", [])
        if check_spec and isinstance(check_spec, dict)
        else []
    )
    if args:
        pattern = str(args[0])
        # Try to provide helpful description for common patterns
        if "\\d{4}-\\d{2}" in pattern:
            return "Value must match the format YYYY-YY (e.g., 2025-26)"
        elif "\\d{4}" in pattern:
            return "Value must be a 4-digit year"
        else:
            return "Value must match the required format pattern"
    return "Value must match the required format"


def _format_ge_error(check_spec: Optional[dict]) -> str:
    """Format ge (greater than or equal) check error message."""
    kwargs = (
        check_spec.get("kwargs", {})
        if check_spec and isinstance(check_spec, dict)
        else {}
    )
    if isinstance(kwargs, dict):
        min_val = kwargs.get("ge", kwargs.get("min_value"))
        if min_val is not None:
            return f"Value must be greater than or equal to {min_val}"
    return "Value must be greater than or equal to the minimum"


def _format_le_error(check_spec: Optional[dict]) -> str:
    """Format le (less than or equal) check error message."""
    kwargs = (
        check_spec.get("kwargs", {})
        if check_spec and isinstance(check_spec, dict)
        else {}
    )
    if isinstance(kwargs, dict):
        max_val = kwargs.get("le", kwargs.get("max_value"))
        if max_val is not None:
            return f"Value must be less than or equal to {max_val}"
    return "Value must be less than or equal to the maximum"


def _format_gt_error(check_spec: Optional[dict]) -> str:
    """Format gt (strictly greater than) check error message."""
    kwargs = (
        check_spec.get("kwargs", {})
        if check_spec and isinstance(check_spec, dict)
        else {}
    )
    if isinstance(kwargs, dict):
        min_val = kwargs.get("gt", kwargs.get("min_value"))
        if min_val is not None:
            return f"Value must be greater than {min_val}"
    # Also check args for gt (some specs use args instead of kwargs)
    args = (
        check_spec.get("args", [])
        if check_spec and isinstance(check_spec, dict)
        else []
    )
    if args and len(args) > 0:
        min_val = args[0]
        return f"Value must be greater than {min_val}"
    return "Value must be greater than the minimum"


def _format_lt_error(check_spec: Optional[dict]) -> str:
    """Format lt (strictly less than) check error message."""
    kwargs = (
        check_spec.get("kwargs", {})
        if check_spec and isinstance(check_spec, dict)
        else {}
    )
    if isinstance(kwargs, dict):
        max_val = kwargs.get("lt", kwargs.get("max_value"))
        if max_val is not None:
            return f"Value must be less than {max_val}"
    # Also check args for lt (some specs use args instead of kwargs)
    args = (
        check_spec.get("args", [])
        if check_spec and isinstance(check_spec, dict)
        else []
    )
    if args and len(args) > 0:
        max_val = args[0]
        return f"Value must be less than {max_val}"
    return "Value must be less than the maximum"


def _extract_base_check_type(check_type: str) -> str:
    """
    Extract the base check type from parameterized check types.

    Pandera provides check types with arguments like:
    - "isin(['A', 'B', 'C'])" -> "isin"
    - "str_length(3, None)" -> "str_length"
    - "greater_than(0)" -> "greater_than"
    - "Check.isin(['A'])" -> "isin" (namespaced, extracts final token)
    - "pandera.Check.str_length(3, None)" -> "str_length" (multi-level namespace)
    - "str_matches(re.compile('...'))" -> "str_matches" (complex repr)

    Handles edge cases:
    - Empty/None/non-string: returns safe empty string
    - Already base: "isin" -> "isin"
    - Namespaced: extracts final token after last dot
    - Spaces: "isin (['A'])" -> "isin" (after strip)

    Args:
        check_type: The check type string (may be parameterized)

    Returns:
        The base check type name (without parameters or namespace), stripped of whitespace
    """
    # Handle non-string types safely - return empty string to avoid noisy output
    if not isinstance(check_type, str):
        return ""

    # Handle empty string
    if not check_type:
        return ""

    # Extract base type by taking everything before the first '('
    # This safely handles:
    # - Parameterized: "isin(['A', 'B'])" -> "isin"
    # - Complex repr: "str_matches(re.compile('...'))" -> "str_matches"
    # - Spaces: "isin (['A'])" -> "isin" (after strip)
    if "(" in check_type:
        base = check_type.split("(")[0].strip()
    else:
        base = check_type.strip()

    # Extract final token after last dot to handle namespaced types
    # This ensures "Check.isin(['A'])" -> "isin" (matches spec with type="isin")
    # and "pandera.Check.str_length(3, None)" -> "str_length"
    if "." in base:
        base = base.split(".")[-1].strip()

    return base


# Alias mapping for check type normalization
# Maps Pandera's verbose check names to the short names used in specs
# IMPORTANT: Preserves semantic correctness (strict vs non-strict comparisons)
_CHECK_TYPE_ALIASES = {
    # Strict comparisons (> and <)
    "greater_than": "gt",  # Strict: > x
    "gt": "gt",  # Already canonical
    "less_than": "lt",  # Strict: < x
    "lt": "lt",  # Already canonical
    # Non-strict comparisons (≥ and ≤)
    "greater_than_or_equal_to": "ge",  # Non-strict: ≥ x
    "less_than_or_equal_to": "le",  # Non-strict: ≤ x
    # Other aliases
    "is_in": "isin",  # Handle both spellings (conceptually equivalent)
}


def _normalize_check_type_alias(check_type: str) -> str:
    """
    Normalize check type aliases to match spec keys.

    Pandera may emit verbose check names (e.g., "greater_than(0)") while
    specs use short names (e.g., "ge"). This function maps aliases to their
    canonical forms.

    Args:
        check_type: Base check type (already extracted from parameterized form)

    Returns:
        Normalized check type that matches spec keys
    """
    return _CHECK_TYPE_ALIASES.get(check_type, check_type)


def _find_check_spec(check_type: str, spec: dict) -> Optional[dict]:
    """
    Find the check specification that matches the check type.

    Handles parameterized check types by extracting the base type.
    Also handles aliases (e.g., "greater_than" → "gt", "greater_than_or_equal_to" → "ge").
    Prioritizes base type match first to avoid over-matching.
    """
    if not isinstance(spec, dict):
        return None

    # Extract base check type to handle parameterized checks
    base_check_type = _extract_base_check_type(check_type)
    # Normalize aliases to match spec keys (e.g., "greater_than" → "ge")
    normalized_check_type = _normalize_check_type_alias(base_check_type)

    checks = spec.get("checks", []) if isinstance(spec.get("checks"), list) else []

    for chk in checks:
        if isinstance(chk, dict):
            chk_type = chk.get("type")
            # Try multiple matching strategies:
            # 1. Normalized alias match (e.g., "greater_than" → "ge" matches spec "ge")
            # 2. Base type match (for non-aliased checks)
            # 3. Exact match (for backwards compatibility)
            if (
                chk_type == normalized_check_type
                or chk_type == base_check_type
                or chk_type == check_type
            ):
                return chk

    return None


def _format_check_error(check_type: str, spec: dict, value: Any) -> str:
    """
    Convert technical check names to human-readable descriptions.

    Handles parameterized check types from Pandera (e.g., "isin(['A', 'B', 'C'])").
    Also handles aliases (e.g., "greater_than" → "gt", "greater_than_or_equal_to" → "ge").
    Preserves semantic correctness: strict comparisons (> and <) vs non-strict (≥ and ≤).
    Only formats specific check types if a matching spec is found.
    """
    # Extract base check type for matching (Pandera provides parameterized types)
    base_check_type = _extract_base_check_type(check_type)
    # Normalize aliases to match spec keys (e.g., "greater_than" → "gt")
    normalized_check_type = _normalize_check_type_alias(base_check_type)
    check_spec = _find_check_spec(check_type, spec)

    # Only format specific check types if a matching spec was found
    # This ensures we don't format "greater_than" as "gt" when spec has "ge"
    if check_spec is not None:
        # Format based on normalized check type (spec was found, so safe to format)
        if normalized_check_type == "str_length":
            return _format_str_length_error(check_spec)

        if normalized_check_type in {"isin", "is_in"}:
            return _format_isin_error(check_spec)

        if normalized_check_type == "ge":
            return _format_ge_error(check_spec)

        if normalized_check_type == "le":
            return _format_le_error(check_spec)

        if normalized_check_type == "gt":
            return _format_gt_error(check_spec)

        if normalized_check_type == "lt":
            return _format_lt_error(check_spec)

        if base_check_type in {"matches", "str_matches"}:
            return _format_matches_error(check_spec)

    # Format check types that don't require a spec match
    if base_check_type in {"not_nullable", "not_null"}:
        return "This field cannot be empty"

    if base_check_type == "nullable":
        return "Value validation failed"

    # PDP/Edvise schema check names (same validation as edvise repo)
    if base_check_type in PDP_EDVISE_CHECK_MESSAGES:
        return PDP_EDVISE_CHECK_MESSAGES[base_check_type]

    # Generic fallback - use original check_type for display (may include parameters)
    # This handles cases where check type doesn't match any spec (e.g., "greater_than" with "ge" spec)
    return f"Validation failed for {check_type} check"
