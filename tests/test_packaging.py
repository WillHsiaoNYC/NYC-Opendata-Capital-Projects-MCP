"""Exercise the installed wheel without importing code or resources from the checkout."""
import os
import shutil
import site
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

from od_cpd import config


_RESOURCES = {
    "agencies.yaml", "categories.yaml", "data_dictionary.yaml", "tables.yaml",
    "fms_agency_dim.tsv",
}

_WHEEL_PROBE = r'''
import csv
import io
import sys
from pathlib import Path

import duckdb
import od_cpd
from od_cpd import agencies, categories, config, data_dictionary, ingest, materialize, schema
from od_cpd.table_catalog import load_table_catalog
from od_cpd.tools.lookup import describe_table_from

assert Path(od_cpd.__file__).is_relative_to(Path(sys.prefix)), od_cpd.__file__
resources = config.data_dir()
assert agencies.load_agency_rows()
assert categories.load_category_rules()['categories']
assert data_dictionary.load_dictionary()
assert load_table_catalog()
agency_source = csv.DictReader(io.StringIO((resources / 'fms_agency_dim.tsv').read_text()), delimiter='\t')
assert agency_source.fieldnames == ['name', 'shortName', 'CPDW Acronym']
assert list(agency_source)

with duckdb.connect('synthetic.duckdb') as con:
    schema.apply_schema(con)
    con.execute("""INSERT INTO raw_project_detail
        (reporting_period, managing_agency, sponsor_agency, pid, fms_id, total_budget,
         spend_to_date, current_phase, borough, agency_project_name)
        VALUES ('202601', 'DDC', 'DPR', 'SYN-1', 'SYN-LINE', '100', '10',
                'Construction', 'Brooklyn', 'Synthetic project')""")
    con.execute("""INSERT INTO raw_schedule_history
        (reporting_period, managing_agency, pid, current_phase, completion_date,
         completion_date_type, variance_day)
        VALUES ('202601', 'DDC', 'SYN-1', 'Construction', '2027-01-01', 'Forecast', '45')""")
    con.execute("""INSERT INTO raw_budget_history
        (managing_agency, fms_id, year_month_reported, total_budget, spend_to_date, budget_variance)
        VALUES ('DDC', 'SYN-LINE', '202601', '100', '10', '0')""")
    con.execute("""INSERT INTO raw_budget_fy
        (reporting_period, managing_agency, fms_id, fiscal_year, total_budget_city_non_city,
         city, non_city, spend)
        VALUES ('202601', 'DDC', 'SYN-LINE', '2026', '100', '80', '20', '10')""")
    for dataset, table in schema.TABLE_FOR_DATASET.items():
        ingest.write_meta(con, dataset, table, 0, schema.RAW_COLUMNS[table])
    ingest.build_agency_dim(con)
    materialize.materialize_all(con)
    assert con.execute('SELECT count(*) FROM latest_project_state').fetchone() == (1,)
    assert con.execute("SELECT category FROM category_dim WHERE fms_id='SYN-LINE'").fetchone() == ('Parks & Recreation',)
    assert con.execute('SELECT sum(total_budget) FROM budget_history').fetchone() == (100.0,)
    detail = describe_table_from(con, 'budget_history')
    assert {'managing_agency', 'fms_id', 'total_budget'} <= {c['name'] for c in detail['columns']}
print('installed wheel resources, synthetic materialization and catalog verified')
'''


def _run(args, *, cwd, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_source_checkout_keeps_curated_files_editable(tmp_path, monkeypatch):
    package = tmp_path / "src" / "od_cpd"
    package.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'od-cpd'\n")
    data = tmp_path / "data"
    data.mkdir()
    dictionary = data / "categories.yaml"
    dictionary.write_text("categories: []\n")
    monkeypatch.setattr(config, "__file__", str(package / "config.py"))
    assert config.data_dir() == data
    dictionary.write_text("categories: [{name: Revised}]\n")
    assert (config.data_dir() / "categories.yaml").read_text() == "categories: [{name: Revised}]\n"


def test_installed_wheel_contains_and_loads_curated_resources(tmp_path):
    uv = shutil.which("uv")
    assert uv, "The isolated wheel regression requires uv (the repository's test runner)."
    checkout = Path(__file__).resolve().parents[1]
    dist = tmp_path / "dist"
    _run([uv, "build", "--wheel", "--out-dir", str(dist)], cwd=checkout)
    wheel, = dist.glob("*.whl")
    with ZipFile(wheel) as archive:
        assert {Path(n).name for n in archive.namelist() if n.startswith("od_cpd/data/")} == _RESOURCES
        for name in _RESOURCES:
            assert archive.read(f"od_cpd/data/{name}") == (checkout / "data" / name).read_bytes()

    isolated = tmp_path / "wheel-env"
    _run([uv, "venv", "--python", sys.executable, str(isolated)], cwd=tmp_path)
    python = isolated / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    _run([uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)], cwd=outside, env=env)
    wheel_site = Path(_run(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        cwd=outside, env=env,
    ).strip())
    # Reuse the locked test environment's dependencies without processing its
    # editable-install .pth files. The installed wheel has import precedence.
    (wheel_site / "test_dependencies.pth").write_text("\n".join(site.getsitepackages()) + "\n")
    assert "installed wheel resources" in _run(
        [str(python), "-I", "-c", _WHEEL_PROBE], cwd=outside, env=env,
    )
