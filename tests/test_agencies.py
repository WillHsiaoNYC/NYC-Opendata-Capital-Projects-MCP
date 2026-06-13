# tests/test_agencies.py
from od_cpd import agencies


def test_executor_set_is_the_thirteen():
    assert agencies.SCHEDULE_EXECUTORS == {
        "DDC", "EDC", "DEP", "DOT", "DPR", "DOC", "CUNY",
        "NYPD", "FDNY", "DCAS", "DHS", "DOHMH", "DSNY",
    }


def test_load_agency_rows_from_yaml(tmp_path):
    # `display:` is the real key used by data/agencies.yaml — the fixture must match it,
    # or a reader bug (e.g. reading a `name:` key) is masked and every display_name
    # silently falls back to the slug.
    yaml_text = (
        "ddc:\n"
        "  display: Department of Design and Construction\n"
        "  aliases: [DDC]\n"
        "  cpdw_acronym: DDC\n"
        "council:\n"
        "  display: City Council\n"
        "  aliases: [City Council]\n"  # no cpdw_acronym -> dictionary-only
    )
    p = tmp_path / "agencies.yaml"
    p.write_text(yaml_text)
    rows = agencies.load_agency_rows(yaml_path=p)
    by_slug = {r["slug"]: r for r in rows}
    assert by_slug["ddc"]["display_name"] == "Department of Design and Construction"
    assert by_slug["ddc"]["cpdw_acronym"] == "DDC"
    assert by_slug["ddc"]["is_schedule_executor"] is True
    assert by_slug["council"]["cpdw_acronym"] is None
    assert by_slug["council"]["is_schedule_executor"] is False


def test_real_yaml_display_names_are_read():
    # Guards the actual data/agencies.yaml key contract: if the reader and the YAML
    # disagree on the display-name key, every agency's display_name == slug.
    by_slug = {r["slug"]: r for r in agencies.load_agency_rows()}
    assert by_slug["acs"]["display_name"] == "Administration for Children's Services"
    assert all(r["display_name"] != r["slug"] for r in by_slug.values())


def test_real_yaml_manager_defaults():
    # Guards the actual data/agencies.yaml (not a synthetic fixture): a typo or deleted
    # role_default on a manager agency would silently flip its default lens to sponsor.
    by_slug = {r["slug"]: r for r in agencies.load_agency_rows()}
    assert by_slug["ddc"]["role_default"] == "managing"
    assert by_slug["dcas"]["role_default"] == "managing"
    assert by_slug["edc"]["role_default"] == "managing"
    assert by_slug["doc"]["role_default"] == "sponsor"
    assert by_slug["dep"]["role_default"] == "sponsor"


def test_load_agency_rows_role_default(tmp_path):
    yaml_text = (
        "ddc:\n"
        "  display: Department of Design and Construction\n"
        "  aliases: [DDC]\n"
        "  cpdw_acronym: DDC\n"
        "  role_default: managing\n"
        "dpr:\n"
        "  display: Department of Parks and Recreation\n"
        "  aliases: [DPR]\n"
        "  cpdw_acronym: DPR\n"  # no role_default -> defaults to sponsor
    )
    p = tmp_path / "agencies.yaml"
    p.write_text(yaml_text)
    by_slug = {r["slug"]: r for r in agencies.load_agency_rows(yaml_path=p)}
    assert by_slug["ddc"]["role_default"] == "managing"
    assert by_slug["dpr"]["role_default"] == "sponsor"
