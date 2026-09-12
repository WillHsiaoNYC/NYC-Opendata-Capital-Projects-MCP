# tests/test_socrata.py
import csv
import io

import httpx
import pytest

from od_cpd import socrata


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_metadata_parses_rows_updated_and_columns():
    def handler(request):
        assert "/api/views/fb86-vt7u.json" in str(request.url)
        return httpx.Response(200, json={
            "rowsUpdatedAt": 1738000000,
            "columns": [{"fieldName": "reporting_period"}, {"fieldName": "pid"}],
        })

    with _client(handler) as c:
        meta = socrata.fetch_metadata("fb86-vt7u", client=c)
    assert meta.rows_updated_at == 1738000000
    assert meta.columns == ["reporting_period", "pid"]


def test_download_csv_paginates_until_short_page(tmp_path):
    pages = [
        "a,b\n1,2\n3,4\n",     # offset 0 (full page of 2 given page_size=2)
        "a,b\n5,6\n",          # offset 2 (short page -> stop)
    ]
    calls = {"n": 0}

    def handler(request):
        body = pages[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, text=body)

    out = tmp_path / "fb86.csv"
    with _client(handler) as c:
        rows = socrata.download_csv("fb86-vt7u", out, page_size=2, client=c)
    assert rows == 3
    text = out.read_text()
    assert text.count("\n") == 4          # header + 3 data rows
    assert text.startswith("a,b\n")       # header written exactly once
    assert "5,6" in text


def test_download_csv_counts_records_not_physical_lines(tmp_path):
    # Page 0 has 2 data RECORDS, but the first record's quoted field spans an
    # embedded newline -> 3 physical body lines. Physical-line counting would
    # inflate the total (and never terminate on the "full page" check).
    pages = [
        'a,b\n1,"hello\nworld"\n3,4\n',   # offset 0: 2 records (3 physical lines)
        "a,b\n5,6\n",                      # offset 2: 1 record (short page -> stop)
    ]
    calls = {"n": 0}

    def handler(request):
        body = pages[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, text=body)

    out = tmp_path / "fb86.csv"
    with _client(handler) as c:
        rows = socrata.download_csv("fb86-vt7u", out, page_size=2, client=c)

    # True record count across both pages is 3 (2 + 1), not the 4 physical
    # body lines a line-counting implementation would report.
    assert rows == 3

    # Download content must be byte-identical: header once, then every page's
    # body concatenated verbatim (embedded newline preserved).
    expected = 'a,b\n1,"hello\nworld"\n3,4\n5,6\n'
    assert out.read_text() == expected
    # And the file parses to exactly 3 data records via a real CSV reader.
    with out.open(newline="") as fh:
        parsed = list(csv.reader(fh))
    assert parsed[0] == ["a", "b"]        # header
    assert len(parsed) - 1 == 3           # 3 data records


@pytest.mark.parametrize("second", ["b,a\n3,4\n", "a,c\n3,4\n", "", "a,b\n3,4,5\n"])
def test_download_rejects_later_page_before_appending(tmp_path, second):
    pages = iter(["a,b\n1,2\n", second])
    out = tmp_path / "source.csv"
    with _client(lambda request: httpx.Response(200, text=next(pages))) as client:
        with pytest.raises(ValueError):
            socrata.download_csv("fb86-vt7u", out, page_size=1, client=client,
                                 expected_header=["a", "b"])
    assert out.read_text() == "a,b\n1,2\n"


def test_download_checks_first_header_against_expected_columns(tmp_path):
    out = tmp_path / "source.csv"
    with _client(lambda request: httpx.Response(200, text='"b","a"\n1,2\n')) as client:
        with pytest.raises(ValueError, match="header changed"):
            socrata.download_csv("fb86-vt7u", out, client=client, expected_header=["a", "b"])
    assert out.read_bytes() == b""


def test_fetch_count_and_snapshot_periods():
    def handler(request):
        if request.url.params.get("$group"):
            assert request.url.params["$where"] == "spend_to_date IS NOT NULL"
            return httpx.Response(200, json=[{"year_month_reported": "202601", "row_count": "4"}])
        assert request.url.params["$select"] == "count(*) AS row_count"
        return httpx.Response(200, json=[{"row_count": "5"}])
    with _client(handler) as client:
        assert socrata.fetch_row_count("qj5n-h5qp", client=client) == 5
        assert socrata.fetch_period_counts("qj5n-h5qp", client=client) == {"202601": 4}


def test_download_keeps_page_records_separate_without_final_newline(tmp_path):
    pages = iter(["a,b\n1,2", "a,b\n3,4", "a,b\n"])
    out = tmp_path / "source.csv"
    with _client(lambda request: httpx.Response(200, text=next(pages))) as client:
        assert socrata.download_csv("fb86-vt7u", out, page_size=1, client=client) == 2
    with out.open(newline="") as stream:
        assert list(csv.reader(stream)) == [["a", "b"], ["1", "2"], ["3", "4"]]


def _export_fixture(monkeypatch):
    monkeypatch.setitem(socrata.schema.RAW_COLUMNS, "raw_project_detail", ["reporting_period", "pid"])
    return socrata.Metadata(123, ["reporting_period", "pid"],
                            {"Reporting Period": "reporting_period", "Project ID": "pid"})


def test_export_normalizes_order_and_preserves_original_values(tmp_path, monkeypatch):
    metadata = _export_fixture(monkeypatch)
    body = b'\xef\xbb\xbfProject ID,Reporting Period\r\n"001, abc\n def",202609\r\n'
    def handler(request):
        assert request.url.path == "/api/v3/views/fb86-vt7u/export.csv"
        assert request.url.params["query"] == "SELECT reporting_period::text AS reporting_period, pid::text AS pid"
        return httpx.Response(200, content=body)
    output = tmp_path / "data.csv"
    with _client(handler) as client:
        assert socrata.download_export_csv("fb86-vt7u", output, metadata=metadata, client=client) == 1
    assert output.with_suffix('.original.csv').read_bytes() == body
    with output.open(newline="") as stream:
        assert list(csv.reader(stream)) == [["reporting_period", "pid"], ["202609", "001, abc\n def"]]


@pytest.mark.parametrize("body", [
    "Reporting Period,Project ID,Extra\n202609,1,2\n",
    "Reporting Period\n202609\n",
    "Reporting Period,Reporting Period\n202609,1\n",
    "Reporting Period,pid,Project ID\n202609,1,2\n",
    "Reporting Period,Project ID\n202609,1,2\n",
    'Reporting Period,Project ID\n202609,"unterminated',
    "",
])
def test_export_rejects_invalid_headers_and_rows(tmp_path, monkeypatch, body):
    metadata = _export_fixture(monkeypatch)
    with _client(lambda request: httpx.Response(200, text=body)) as client:
        with pytest.raises((ValueError, csv.Error)):
            socrata.download_export_csv("fb86-vt7u", tmp_path / "data.csv", metadata=metadata, client=client)


def test_export_accepts_native_headers(tmp_path, monkeypatch):
    metadata = _export_fixture(monkeypatch)
    with _client(lambda request: httpx.Response(200, text="pid,reporting_period\n001,202609\n")) as client:
        assert socrata.download_export_csv("fb86-vt7u", tmp_path / "data.csv", metadata=metadata, client=client) == 1


def test_export_rejects_ambiguous_display_and_native_name(tmp_path, monkeypatch):
    _export_fixture(monkeypatch)
    metadata = socrata.Metadata(123, ["reporting_period", "pid"], {"pid": "reporting_period"})
    with _client(lambda request: httpx.Response(200, text="pid,reporting_period\n001,202609\n")) as client:
        with pytest.raises(ValueError, match="ambiguous"):
            socrata.download_export_csv("fb86-vt7u", tmp_path / "data.csv", metadata=metadata, client=client)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_metadata_retries_transient_errors(monkeypatch, status):
    monkeypatch.setattr(socrata.time, "sleep", lambda seconds: None)
    responses = iter([status, status, 200])
    with _client(lambda request: httpx.Response(next(responses), json={"columns": []})) as client:
        assert socrata.fetch_metadata("fb86-vt7u", client=client).columns == []


def test_export_retries_interrupted_transfer_from_beginning(tmp_path, monkeypatch):
    metadata = _export_fixture(monkeypatch)
    monkeypatch.setattr(socrata.time, "sleep", lambda seconds: None)
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b"PARTIAL DOWNLOAD"
            raise httpx.ReadError("interrupted")
    responses = iter([httpx.Response(200, stream=Interrupted()),
                      httpx.Response(200, text="pid,reporting_period\n001,202609\n")])
    output = tmp_path / "data.csv"
    with _client(lambda request: next(responses)) as client:
        assert socrata.download_export_csv("fb86-vt7u", output, metadata=metadata, client=client) == 1
    assert output.with_suffix('.original.csv').read_text() == "pid,reporting_period\n001,202609\n"


@pytest.mark.parametrize("status,expected_calls", [(503, 3), (403, 1), (404, 1)])
def test_export_retry_is_bounded(tmp_path, monkeypatch, status, expected_calls):
    metadata = _export_fixture(monkeypatch)
    monkeypatch.setattr(socrata.time, "sleep", lambda seconds: None)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status)
    with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            socrata.download_export_csv("fb86-vt7u", tmp_path / "data.csv", metadata=metadata, client=client)
    assert len(calls) == expected_calls


def test_metadata_maps_display_headers_excluding_system_columns():
    data = {"columns": [{"fieldName": ":id", "name": "ID"},
                        {"fieldName": "pid", "name": "Project ID"}]}
    with _client(lambda request: httpx.Response(200, json=data)) as client:
        meta = socrata.fetch_metadata("fb86-vt7u", client=client)
    assert meta.columns == [":id", "pid"]
    assert meta.export_headers == {"Project ID": "pid"}


def test_metadata_rejects_duplicate_display_labels():
    data = {"columns": [{"fieldName": "pid", "name": "ID"},
                        {"fieldName": "fms_id", "name": "ID"}]}
    with _client(lambda request: httpx.Response(200, json=data)) as client:
        with pytest.raises(ValueError, match="duplicate export column label"):
            socrata.fetch_metadata("fb86-vt7u", client=client)


def test_full_export_query_preserves_precision_dates_and_empty_values(tmp_path, monkeypatch):
    fields = ["total_budget", "spend_to_date_1", "fms_data_date", "agency_project_name"]
    monkeypatch.setitem(socrata.schema.RAW_COLUMNS, "raw_project_detail", fields)
    metadata = socrata.Metadata(123, fields)
    body = ("total_budget,spend_to_date_1,fms_data_date,agency_project_name\n"
            '173058.01,0.4221,2023-05-11T14:15:16.123,""\n'
            "-0.000000001,1.23456789,,literal text\n")
    def handler(request):
        assert request.method == "GET"
        assert request.url.params["query"] == "SELECT " + ", ".join(
            f"{field}::text AS {field}" for field in fields)
        assert "LIMIT" not in request.url.params["query"]
        return httpx.Response(200, text=body)
    output = tmp_path / "data.csv"
    with _client(handler) as client:
        assert socrata.download_export_csv("fb86-vt7u", output, metadata=metadata, client=client) == 2
    with output.open(newline="") as stream:
        assert list(csv.reader(stream)) == list(csv.reader(io.StringIO(body)))
