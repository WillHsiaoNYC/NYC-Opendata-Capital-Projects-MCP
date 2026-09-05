import duckdb
import pytest

from od_cpd import build_info
from od_cpd.dbio import connect_readonly
from tests.test_ingest_safety import seed_database


def test_completed_build_identity_is_stable_and_tracks_source_revision(tmp_path):
    path = tmp_path / "build.duckdb"
    seed_database(path)
    with duckdb.connect(str(path)) as con:
        before = build_info.read_build_info(con)
        again = build_info.write_build_info(con, expected_fingerprint=build_info.current_fingerprint())
        assert before["build_id"] == again["build_id"]
        con.execute("UPDATE meta SET rows_updated_at=rows_updated_at+1 WHERE dataset_id='95tx-snak'")
        changed = build_info.write_build_info(con, expected_fingerprint=build_info.current_fingerprint())
        assert changed["build_id"] != before["build_id"]
        assert changed["source_revisions"]["95tx-snak"]["rows_updated_at"] == 101
        assert con.execute("SELECT count(*) FROM data_build").fetchone() == (1,)
    with connect_readonly(path) as con:
        assert build_info.read_build_info(con) == changed


def test_resource_fingerprint_includes_yaml_and_tsv_without_filesystem_only_api(tmp_path, monkeypatch):
    (tmp_path / "dictionary.yaml").write_text("a: one\n")
    (tmp_path / "categories.tsv").write_text("a\tone\n")
    class Resource:
        def __init__(self, path):
            self.path, self.name = path, path.name
        def iterdir(self):
            return (Resource(path) for path in self.path.iterdir())
        def read_bytes(self):
            return self.path.read_bytes()
    monkeypatch.setattr(build_info, "data_dir", lambda: Resource(tmp_path))
    before = build_info.current_fingerprint()
    (tmp_path / "dictionary.yaml").write_text("a: two\n")
    changed_yaml = build_info.current_fingerprint()
    assert changed_yaml != before
    (tmp_path / "categories.tsv").write_text("a\ttwo\n")
    assert build_info.current_fingerprint() != changed_yaml


def test_fingerprint_mismatch_never_creates_completed_marker():
    with duckdb.connect(":memory:") as con:
        with pytest.raises(ValueError, match="rules changed during the build"):
            build_info.write_build_info(con, expected_fingerprint="before rules changed")
        assert con.execute("SHOW TABLES").fetchall() == []
