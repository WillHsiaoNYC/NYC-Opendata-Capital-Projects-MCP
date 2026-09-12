import csv
import json

import httpx
import pytest

from od_cpd import schema, snapshots, socrata
from od_cpd.config import DATASETS


@pytest.fixture
def bundle(tmp_path):
    staging = tmp_path / "working"
    staging.mkdir()
    downloads = {}
    for ds, dataset in DATASETS.items():
        columns = schema.RAW_COLUMNS[schema.TABLE_FOR_DATASET[ds]]
        mapping = {field.replace("_", " ").title(): field for field in columns}
        normalized = staging / f"{ds}.csv"
        original = normalized.with_suffix(".original.csv")
        rows = [{column: "" for column in columns} for _ in range(2)]
        for row in rows:
            row[dataset.period_column] = "202605"
            if "spend_to_date" in row:
                row["spend_to_date"] = "42"
        if ds == "qj5n-h5qp":
            rows[1]["spend_to_date"] = ""
        with original.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(reversed(mapping))
            for row in rows:
                writer.writerow([row[field] for field in reversed(columns)])
        meta = socrata.Metadata(1780000000, columns, mapping)
        count = socrata.normalize_export_csv(ds, original, normalized, metadata=meta)
        downloads[ds] = (meta, normalized, count)
    path = snapshots.write_manifest(tmp_path, downloads)
    manifest = json.loads(path.read_text())
    manifest["release_tag"] = "data-test"
    manifest["release_assets"] = {name: index for index, name in enumerate(["manifest.json", *(f"{ds}.csv" for ds in DATASETS)], 1)}
    return tmp_path, manifest, downloads


def test_round_trip_preserves_originals_and_maps_reordered_headers(bundle, tmp_path):
    directory, manifest, downloads = bundle
    requests = []
    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=(directory / next(name for name, asset_id in manifest["release_assets"].items() if str(asset_id) == request.url.path.split("/")[-1])).read_bytes())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        for ds in DATASETS:
            meta, normalized, count = snapshots.download_dataset(ds, tmp_path / "installed", manifest, client=client)
            assert count == 2
            assert normalized.read_bytes() == downloads[ds][1].read_bytes()
            assert meta == downloads[ds][0]
            assert (normalized.parent / f"{ds}.csv").read_bytes() == (directory / f"{ds}.csv").read_bytes()
    assert all(url.startswith(f"https://api.github.com/repos/{snapshots.REPOSITORY}/releases/assets/") for url in requests)
    assert manifest["datasets"]["qj5n-h5qp"]["period_counts"] == {"202605": 1}


@pytest.mark.parametrize("change", [
    lambda m: m.update(schema_version=2),
    lambda m: m.pop("export_format"),
    lambda m: m.update(downloaded_at="2026-09-11"),
    lambda m: m["datasets"].pop("95tx-snak"),
    lambda m: m["datasets"]["fb86-vt7u"].update(filename="../evil.csv"),
    lambda m: m["datasets"]["fb86-vt7u"].update(sha256="bad"),
    lambda m: m["datasets"]["fb86-vt7u"].update(size_bytes=True),
    lambda m: m["datasets"]["fb86-vt7u"].update(row_count=0),
    lambda m: m["datasets"]["fb86-vt7u"].update(export_headers={}),
    lambda m: m["datasets"]["fb86-vt7u"].update(columns=["unknown"]),
    lambda m: m["datasets"]["fb86-vt7u"].update(period_counts={"202606": 1}),
    lambda m: m["datasets"]["fb86-vt7u"].update(source_url="https://example.com"),
])
def test_invalid_manifest_rejected_before_network(bundle, change):
    _, manifest, _ = bundle
    change(manifest)
    with pytest.raises(ValueError):
        snapshots.validate_manifest(manifest)


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("row_count", 3),
                                          ("size_bytes", 10000000), ("period_counts", {"202601": 2})])
def test_asset_reconciliation_rejects_wrong_bytes_counts_and_periods(bundle, field, value):
    directory, manifest, _ = bundle
    ds = "fb86-vt7u"
    manifest["datasets"][ds][field] = value
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, content=(directory / f"{ds}.csv").read_bytes()))) as client:
        with pytest.raises(ValueError, match="mismatch"):
            snapshots.download_dataset(ds, directory / "invalid", manifest, client=client)
    assert not (directory / "invalid" / f"{ds}.csv").exists()
    assert not (directory / "invalid" / f"{ds}.csv.part").exists()


def test_resolve_requires_all_release_assets(bundle):
    _, manifest, _ = bundle
    def handle(request):
        return httpx.Response(200, json={"tag_name": "data-test", "assets": [{"name": "manifest.json"}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="missing required assets"):
            snapshots.resolve_release("data-test", client=client)


def test_resolve_downloads_manifest_from_fixed_repository(bundle):
    _, manifest, _ = bundle
    def handle(request):
        if "/releases/tags/" in request.url.path:
            return httpx.Response(200, json={"tag_name": "data-test", "assets": [
                {"name": name, "id": manifest["release_assets"][name], "browser_download_url": "https://evil.example/ignored"}
                for name in ["manifest.json", *(f"{ds}.csv" for ds in DATASETS)]]})
        assert str(request.url) == snapshots._release_asset_url(manifest["release_assets"]["manifest.json"])
        return httpx.Response(200, json=manifest)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert snapshots.resolve_release("data-test", client=client) == manifest


def test_download_retries_service_failure(bundle, monkeypatch):
    directory, manifest, _ = bundle
    calls = []
    monkeypatch.setattr(snapshots.time, "sleep", lambda seconds: None)
    def handle(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(
            200, content=(directory / "fb86-vt7u.csv").read_bytes())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert snapshots.download_dataset("fb86-vt7u", directory / "retry", manifest, client=client)[2] == 2
    assert len(calls) == 3


def test_invalid_tag_cannot_traverse_release_path():
    with pytest.raises(ValueError, match="tag"):
        snapshots.resolve_release("../../other")


def test_missing_release_has_actionable_error():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        with pytest.raises(ValueError, match="use --source opendata"):
            snapshots.resolve_release(client=client)


def test_creation_checks_count_and_never_writes_manifest_on_failure(bundle, tmp_path):
    _, _, downloads = bundle
    output = tmp_path / "bad-count"
    output.mkdir()
    ds = "fb86-vt7u"
    meta, path, count = downloads[ds]
    downloads[ds] = (meta, path, count + 1)
    with pytest.raises(ValueError, match="row count"):
        snapshots.write_manifest(output, downloads)
    assert not (output / "manifest.json").exists()


def test_creation_preserves_existing_artifacts(bundle):
    directory, _, downloads = bundle
    original = (directory / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="already contains"):
        snapshots.write_manifest(directory, downloads)
    assert (directory / "manifest.json").read_bytes() == original


def test_download_requires_pinned_asset_identity(bundle):
    directory, manifest, _ = bundle
    manifest.pop("release_assets")
    with pytest.raises(ValueError, match="asset ID"):
        snapshots.download_dataset("fb86-vt7u", directory / "unpinned", manifest)


def test_creation_checks_original_row_count(bundle, tmp_path):
    _, _, downloads = bundle
    output = tmp_path / "bad-original"
    output.mkdir()
    original = downloads["fb86-vt7u"][1].with_suffix(".original.csv")
    with original.open(newline="") as stream:
        rows = list(csv.reader(stream))
    with original.open("w", newline="") as stream:
        csv.writer(stream).writerows(rows[:-1])
    with pytest.raises(ValueError, match="row count"):
        snapshots.write_manifest(output, downloads)
    assert not (output / "manifest.json").exists()
