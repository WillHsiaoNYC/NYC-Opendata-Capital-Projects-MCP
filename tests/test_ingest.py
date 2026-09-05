# tests/test_ingest.py
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from od_cpd import dbio, ingest, schema


def _write_csv(path, header, rows):
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def test_load_raw_csv_into_table(tmp_path):
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    csv = tmp_path / "sched.csv"
    _write_csv(
        csv,
        "reporting_period,managing_agency,pid,agency_project_name,current_phase,"
        "completion_date,completion_date_type,variance_day,"
        "reason_for_forecast_completion_change,data_date",
        ["202601,DDC,101,Park,Construction,,Forecast,45,Late,2026-01-01"],
    )
    n = ingest.load_raw_csv(con, "raw_schedule_history", csv)
    assert n == 1
    got = con.execute(
        "SELECT managing_agency, variance_day FROM raw_schedule_history"
    ).fetchone()
    assert got == ("DDC", "45")   # stored as VARCHAR


def test_build_agency_dim_cpd_active_never_null():
    # Regression: for dictionary-only agencies (cpdw_acronym IS NULL), the buggy
    # `NULL IN (<non-empty set>)` evaluated to NULL rather than FALSE, leaving
    # cpd_active NULL. An acronym-less agency is definitively absent from CPD data,
    # so it must be FALSE, and no agency_dim row may carry a NULL flag.
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    # raw_project_detail must be non-empty to reproduce the bug: `x IN (empty set)`
    # is FALSE for any x, so only a populated edge table exposes the three-valued NULL.
    con.execute("INSERT INTO raw_project_detail (managing_agency) VALUES ('DDC')")
    ingest.build_agency_dim(con)

    null_flags = con.execute(
        "SELECT count(*) FROM agency_dim WHERE cpd_active IS NULL"
    ).fetchone()[0]
    assert null_flags == 0

    # Every acronym-less agency is present (guards a meaningful test) and all FALSE.
    acronym_less = con.execute(
        "SELECT count(*), bool_and(cpd_active = FALSE) "
        "FROM agency_dim WHERE cpdw_acronym IS NULL"
    ).fetchone()
    assert acronym_less[0] > 0
    assert acronym_less[1] is True

    # The TRUE path still works: DDC appears in the edge table.
    ddc_active = con.execute(
        "SELECT cpd_active FROM agency_dim WHERE cpdw_acronym = 'DDC'"
    ).fetchone()[0]
    assert ddc_active is True


def _write_db(path, label):
    with duckdb.connect(str(path)) as con:
        con.execute(
            "CREATE TABLE records AS SELECT i, ? AS label FROM range(4096) t(i)",
            [label],
        )


def _read_db(path):
    with dbio.connect_readonly(path) as con:
        return con.execute("SELECT min(label), count(*), sum(i) FROM records").fetchone()


def _read_db_in_new_process(path):
    # DuckDB shares the database instance for a path while any connection remains
    # open in a process. An independent process observes the actual published file.
    result = subprocess.run(
        [sys.executable, "-c", (
            "import duckdb, json, sys; "
            "con = duckdb.connect(sys.argv[1], read_only=True); "
            "print(json.dumps(con.execute("
            "'SELECT min(label), count(*), sum(i) FROM records').fetchone()))"
        ), str(path)],
        check=True, capture_output=True, text=True, timeout=20,
    )
    return tuple(json.loads(result.stdout))


def test_atomic_swap_initial_publish(tmp_path):
    final = tmp_path / "new_directory" / "cpd.duckdb"
    shadow = tmp_path / "cpd_shadow.duckdb"
    _write_db(shadow, "NEW")
    ingest.atomic_swap(shadow, final)
    assert _read_db(final) == ("NEW", 4096, 8386560)
    assert not shadow.exists()
    assert not final.with_suffix(".duckdb.bak").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids replacing an open file")
def test_atomic_swap_keeps_readers_valid_at_every_boundary(tmp_path, monkeypatch):
    final = tmp_path / "cpd.duckdb"
    shadow = tmp_path / "cpd_shadow.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    for path, label in ((final, "OLD"), (shadow, "NEW"), (backup, "PREVIOUS")):
        _write_db(path, label)
    old = ("OLD", 4096, 8386560)
    new = ("NEW", 4096, 8386560)
    observations = []
    replace = ingest.os.replace
    copy = ingest.shutil.copyfileobj

    def observed_copy(src, dst):
        observations.append(_read_db_in_new_process(final))
        result = copy(src, dst)
        observations.append(_read_db_in_new_process(final))
        return result

    def observed_replace(src, dst):
        observations.append(_read_db_in_new_process(final))
        replace(src, dst)
        observations.append(_read_db_in_new_process(final))

    monkeypatch.setattr(ingest.shutil, "copyfileobj", observed_copy)
    monkeypatch.setattr(ingest.os, "replace", observed_replace)
    with dbio.connect_readonly(final) as reader:
        reader.execute("BEGIN")
        assert reader.execute("SELECT min(label) FROM records").fetchone() == ("OLD",)
        ingest.atomic_swap(shadow, final)
        assert observations == [old, old, old, old, old, new]
        assert reader.execute(
            "SELECT min(label), count(*), sum(i) FROM records"
        ).fetchone() == old
        reader.execute("COMMIT")
        # A new same-process connection may share the old DuckDB instance, but it
        # must remain readable; the independent readers above see the new inode.
        assert _read_db(final) in (old, new)
    assert _read_db(final) == new
    assert _read_db(backup) == old
    assert not shadow.exists()
    assert not list(tmp_path.glob(".cpd.duckdb.backup-*"))


@pytest.mark.parametrize("failure_stage", ["copy", "backup", "publish"])
def test_atomic_swap_failure_preserves_live_and_retryable_shadow(
    tmp_path, monkeypatch, failure_stage,
):
    final = tmp_path / "cpd.duckdb"
    shadow = tmp_path / "cpd_shadow.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    for path, label in ((final, "OLD"), (shadow, "NEW"), (backup, "PREVIOUS")):
        _write_db(path, label)
    replace = ingest.os.replace
    copy = ingest.shutil.copyfileobj

    def failing_copy(src, dst):
        if failure_stage == "copy":
            dst.write(b"incomplete backup")
            raise OSError("injected copy failure")
        return copy(src, dst)

    def failing_replace(src, dst):
        if ((failure_stage == "backup" and dst == backup)
                or (failure_stage == "publish" and dst == final)):
            raise OSError("injected replacement failure")
        return replace(src, dst)

    monkeypatch.setattr(ingest.shutil, "copyfileobj", failing_copy)
    monkeypatch.setattr(ingest.os, "replace", failing_replace)
    with pytest.raises(OSError, match="injected"):
        ingest.atomic_swap(shadow, final)
    assert _read_db(final) == ("OLD", 4096, 8386560)
    assert _read_db(shadow) == ("NEW", 4096, 8386560)
    expected_backup = "OLD" if failure_stage == "publish" else "PREVIOUS"
    assert _read_db(backup) == (expected_backup, 4096, 8386560)
    assert not list(tmp_path.glob(".cpd.duckdb.backup-*"))

    # Failure releases the publication lock, so retry can use the completed shadow.
    monkeypatch.setattr(ingest.shutil, "copyfileobj", copy)
    monkeypatch.setattr(ingest.os, "replace", replace)
    ingest.atomic_swap(shadow, final)
    assert _read_db(final) == ("NEW", 4096, 8386560)
    assert _read_db(backup) == ("OLD", 4096, 8386560)


def test_atomic_swap_rejects_overlapping_publication(tmp_path, monkeypatch):
    final = tmp_path / "cpd.duckdb"
    first = tmp_path / "first.duckdb"
    second = tmp_path / "second.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    for path, label in ((final, "OLD"), (first, "FIRST"), (second, "SECOND")):
        _write_db(path, label)
    replace = ingest.os.replace
    competing_attempts = []

    def try_competing_publication(src, dst):
        if dst == final and src == first:
            # Force the second publication exactly between backup and live rename.
            # Without the lock it could publish SECOND here, then FIRST would
            # overwrite it with a backup that still contains OLD.
            result = subprocess.run(
                [sys.executable, "-c", (
                    "from pathlib import Path; import sys; "
                    "from od_cpd.ingest import atomic_swap; "
                    "atomic_swap(Path(sys.argv[1]), Path(sys.argv[2]))"
                ), str(second), str(final)],
                capture_output=True, text=True, timeout=20,
            )
            competing_attempts.append(result)
        return replace(src, dst)

    monkeypatch.setattr(ingest.os, "replace", try_competing_publication)
    ingest.atomic_swap(first, final)
    assert len(competing_attempts) == 1
    assert competing_attempts[0].returncode != 0
    assert "publication already in progress" in competing_attempts[0].stderr
    assert _read_db(final) == ("FIRST", 4096, 8386560)
    assert _read_db(backup) == ("OLD", 4096, 8386560)
    assert _read_db(second) == ("SECOND", 4096, 8386560)
    ingest.atomic_swap(second, final)
    assert _read_db(final) == ("SECOND", 4096, 8386560)
    assert _read_db(backup) == ("FIRST", 4096, 8386560)


@pytest.mark.parametrize("shadow_source", ["live", "backup", "live_hardlink"])
def test_atomic_swap_rejects_shadow_aliases_without_changing_images(tmp_path, shadow_source):
    final = tmp_path / "cpd.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    _write_db(final, "OLD")
    _write_db(backup, "PREVIOUS")
    if shadow_source == "live_hardlink":
        shadow = tmp_path / "alias.duckdb"
        shadow.hardlink_to(final)
    else:
        shadow = final if shadow_source == "live" else backup
    with pytest.raises(ValueError, match="distinct"):
        ingest.atomic_swap(shadow, final)
    assert _read_db(final) == ("OLD", 4096, 8386560)
    assert _read_db(backup) == ("PREVIOUS", 4096, 8386560)


def test_atomic_swap_rejects_cross_filesystem_shadow_before_rotating_backup(tmp_path, monkeypatch):
    final = tmp_path / "cpd.duckdb"
    shadow = tmp_path / "cpd_shadow.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    for path, label in ((final, "OLD"), (shadow, "NEW"), (backup, "PREVIOUS")):
        _write_db(path, label)
    stat = Path.stat

    def different_device(path, *args, **kwargs):
        result = stat(path, *args, **kwargs)
        if path == shadow:
            fields = list(result)
            fields[2] += 1  # st_dev; no second filesystem is needed for the guard.
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "stat", different_device)
    with pytest.raises(ValueError, match="same filesystem"):
        ingest.atomic_swap(shadow, final)
    assert _read_db(final) == ("OLD", 4096, 8386560)
    assert _read_db(shadow) == ("NEW", 4096, 8386560)
    assert _read_db(backup) == ("PREVIOUS", 4096, 8386560)


def test_atomic_swap_rejects_unfinished_shadow_without_persistent_changes(tmp_path):
    final = tmp_path / "cpd.duckdb"
    shadow = tmp_path / "cpd_shadow.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    for path, label in ((final, "OLD"), (shadow, "NEW"), (backup, "PREVIOUS")):
        _write_db(path, label)
    with duckdb.connect(str(shadow)) as writer:
        writer.execute("INSERT INTO records VALUES (4096, 'NEW')")
        before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        with pytest.raises(ValueError, match="checkpointed and closed"):
            ingest.atomic_swap(shadow, final)
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert _read_db(final) == ("OLD", 4096, 8386560)
    assert _read_db(backup) == ("PREVIOUS", 4096, 8386560)
    ingest.atomic_swap(shadow, final)
    assert _read_db(final) == ("NEW", 4097, 8390656)


def test_atomic_swap_rejects_invalid_shadow_without_persistent_changes(tmp_path):
    final = tmp_path / "cpd.duckdb"
    shadow = tmp_path / "cpd_shadow.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    _write_db(final, "OLD")
    _write_db(backup, "PREVIOUS")
    shadow.write_bytes(b"incomplete database")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(ValueError, match="valid and closed"):
        ingest.atomic_swap(shadow, final)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("checkpointed", [False, True])
def test_atomic_swap_rejects_live_writer_without_changing_images(tmp_path, checkpointed):
    final = tmp_path / "cpd.duckdb"
    shadow = tmp_path / "cpd_shadow.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    for path, label in ((final, "OLD"), (shadow, "NEW"), (backup, "PREVIOUS")):
        _write_db(path, label)
    with duckdb.connect(str(final)) as writer:
        writer.execute("INSERT INTO records VALUES (4096, 'OLD')")
        if checkpointed:
            writer.execute("CHECKPOINT")
        # Reading final through another descriptor here would itself release the
        # writer's POSIX lock when that descriptor closes. Inspect its stat only.
        final_stat = final.stat()
        images = (shadow, backup)
        before = {path: path.read_bytes() for path in images}
        with pytest.raises(ValueError, match="Live database must be .*read-only"):
            ingest.atomic_swap(shadow, final)
        assert {path: path.read_bytes() for path in images} == before
        after_stat = final.stat()
        assert (after_stat.st_ino, after_stat.st_size, after_stat.st_mtime_ns) == (
            final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns,
        )
        assert writer.execute("SELECT count(*) FROM records").fetchone() == (4097,)
    assert _read_db(final) == ("OLD", 4097, 8390656)
    assert _read_db(shadow) == ("NEW", 4096, 8386560)
    assert _read_db(backup) == ("PREVIOUS", 4096, 8386560)
    assert not list(tmp_path.glob(".cpd.duckdb.backup-*"))

    # Once the writer closes, its committed row belongs to the complete backup.
    ingest.atomic_swap(shadow, final)
    assert _read_db(final) == ("NEW", 4096, 8386560)
    assert _read_db(backup) == ("OLD", 4097, 8390656)


@pytest.mark.skipif(os.name == "nt", reason="Windows requires closing readers before replacement")
def test_atomic_swap_prevents_live_writes_during_publication(tmp_path, monkeypatch):
    final = tmp_path / "cpd.duckdb"
    shadow = tmp_path / "cpd_shadow.duckdb"
    backup = final.with_suffix(".duckdb.bak")
    _write_db(final, "OLD")
    _write_db(shadow, "NEW")
    replace = ingest.os.replace
    attempts = []

    def try_write_before_replacement(src, dst):
        # Hold publication at both renames and try to mutate live from another
        # process. The read lock must preserve the image throughout the backup.
        result = subprocess.run(
            [sys.executable, "-c", (
                "import duckdb, sys; "
                "con = duckdb.connect(sys.argv[1]); "
                "con.execute(\"INSERT INTO records VALUES (4096, 'UNEXPECTED')\")"
            ), str(final)],
            capture_output=True, text=True, timeout=20,
        )
        attempts.append(result)
        return replace(src, dst)

    monkeypatch.setattr(ingest.os, "replace", try_write_before_replacement)
    ingest.atomic_swap(shadow, final)
    assert len(attempts) == 2
    assert all(result.returncode != 0 for result in attempts)
    assert all("lock" in result.stderr.lower() for result in attempts)
    assert _read_db(final) == ("NEW", 4096, 8386560)
    assert _read_db(backup) == ("OLD", 4096, 8386560)
