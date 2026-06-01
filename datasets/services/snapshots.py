"""Snapshot helpers — freeze a dataset's rows at job-run time.

Called by the processing layer when a Module1Job is created from a
Dataset. The resulting `DatasetSnapshot` is the only thing the engine
adapter reads from; the live dataset can be edited freely after, and the
historical job still reproduces.
"""

from __future__ import annotations

from django.db import transaction

from ..models import ROW_MODEL_FOR_KIND, Dataset, DatasetSnapshot
from ..serializers import ROW_SERIALIZER_FOR_KIND


@transaction.atomic
def create_snapshot(*, dataset: Dataset, consumer_job=None) -> DatasetSnapshot:
    """Materialize `dataset`'s current rows into a new DatasetSnapshot.

    Locking the dataset is a side effect — once a job has consumed a
    dataset, we lock it so the user has an explicit signal that further
    edits should fork into a new dataset.
    """
    model = ROW_MODEL_FOR_KIND[dataset.kind]
    serializer_cls = ROW_SERIALIZER_FOR_KIND[dataset.kind]
    rows_qs = model.objects.filter(dataset=dataset).order_by("row_index", "id")
    # Serialize the whole queryset in one pass. The row serializers are flat
    # ModelSerializers (no per-row lookups / SerializerMethodFields), so this is
    # identical output to per-row `.data` but avoids instantiating a serializer
    # per row — materially faster on large datasets.
    payload = list(serializer_cls(rows_qs, many=True).data)

    snap = DatasetSnapshot.objects.create(
        dataset=dataset,
        organization=dataset.organization,
        consumer_job=consumer_job,
        kind=dataset.kind,
        row_count=len(payload),
        rows_payload=payload,
    )

    if not dataset.is_locked:
        dataset.lock(by_user=consumer_job.user if consumer_job is not None else None)

    return snap


def snapshot_to_dataframe(snapshot: DatasetSnapshot):
    """Convert a snapshot's stored rows into a pandas DataFrame.

    Columns are returned in snake_case (matching the row tables). The
    engine adapter is responsible for renaming to the mixed-case columns
    the existing pipeline expects (POLICYSTARTDATE etc.). We keep the
    pandas import lazy so the import graph stays clean for tests that
    don't touch the engine.
    """
    import pandas as pd

    return pd.DataFrame(snapshot.rows_payload)
