# tests/test_provenance.py
from od_cpd.provenance import provenance_block, source_descriptor


def test_provenance_block_shape():
    p = provenance_block(
        definition="raw row dump",
        scope={"period": "202601"},
        row_count=42,
        reproduce_sql="SELECT * FROM raw_project_detail",
    )
    assert p["row_count"] == 42
    assert p["reproduce_sql"].startswith("SELECT")
    assert p["scope"] == {"period": "202601"}
    assert "excluded" in p


def test_source_descriptor_sets_null_reproduce_sql():
    p = source_descriptor("agencies.yaml + live intersection")
    assert p["source"] == "agencies.yaml + live intersection"
    assert p["reproduce_sql"] is None
