"""Make engine output storable as JSON.

IEEE-754 has three values JSON does not: NaN, Infinity and -Infinity. Python's
``json.dumps`` emits them as the bare tokens ``NaN`` / ``Infinity`` by default,
which is valid JavaScript but *invalid JSON* — so:

* PostgreSQL rejects the write outright::

      psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type json
      DETAIL: Token "NaN" is invalid.

* DRF refuses to render it too (``STRICT_JSON`` is on by default), turning any
  API response carrying one into a 500.

The engines produce these routinely and legitimately: a Combined_Summary whose
``Exp Ratio`` / ``RI %`` columns have not been filled in yields ``float('nan')``
for every one of them, and a ratio over a zero exposure yields an infinity. That
is real actuarial "no value", not corruption — it only becomes a problem at the
moment it crosses into a JSON column.

Non-finite values map to ``None``, never to ``0``. A blank renders as a blank;
a zero is a *number an actuary could act on*, and inventing one to satisfy the
serializer would be the worse failure by far.
"""

from __future__ import annotations

import math
from typing import Any

from django.db import models


def json_safe(value: Any) -> Any:
    """Recursively replace non-finite floats with None.

    Containers are rebuilt rather than mutated, so a caller's own dict is never
    altered underneath it. Tuples become lists because that is what they would
    have become through JSON anyway.
    """
    if isinstance(value, float):
        # numpy.float64 subclasses float, so it lands here too.
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


class SafeJSONField(models.JSONField):
    """A JSONField that cannot be handed a value Postgres will reject.

    Sanitising at each assignment site was the alternative, and it is the one
    that rots: every future field added to a persisted engine payload is one
    more chance to forget, and the failure only shows up against Postgres with
    real actuarial data — never in a unit test with tidy numbers. Doing it in
    ``get_prep_value`` makes it an invariant of the column instead of a habit.

    Nothing is lost by this: a non-finite float cannot be stored in a JSON
    column at all, so the only choice is between ``null`` and a crash.
    """

    def get_prep_value(self, value):
        return super().get_prep_value(json_safe(value))
