# tests/test_socrata.py
import httpx

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
