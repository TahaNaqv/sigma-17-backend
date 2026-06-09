"""IFRS 17 movement-analysis disclosure (SAMA template).

Schema-as-code single source of truth for the per-(reserving_class, UWY) Gross + RI
roll-forward disclosure. See docs: IFRS17_MOVEMENT_PLAN.md.

Public surface:
    from module2_engine.movement import SCHEMA, SCHEMA_VERSION, get_sheet, validate_schema
"""

from .schema import (  # noqa: F401
    SCHEMA,
    SCHEMA_VERSION,
    Line,
    Sheet,
    get_sheet,
    iter_lines,
    validate_schema,
)
