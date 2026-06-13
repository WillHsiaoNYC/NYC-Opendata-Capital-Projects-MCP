# tests/smoke_test_socrata.py
"""Run manually: uv run pytest tests/smoke_test_socrata.py -v -m smoke
Hits real Socrata. Confirms column lists still match the schema."""
import pytest

from od_cpd import schema, socrata

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("dataset_id,table", list(schema.TABLE_FOR_DATASET.items()))
def test_live_columns_match_schema(dataset_id, table):
    live = set(socrata.fetch_metadata(dataset_id).columns)
    expected = set(schema.RAW_COLUMNS[table])
    assert expected <= live, f"{dataset_id}: missing {expected - live}"
