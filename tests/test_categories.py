import duckdb
from od_cpd import schema, materialize, categories
from od_cpd.tools import lookup, ranking


def _con():
    """Fixture exercising every precedence tier + the reassignment case."""
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    # reporting_period, managing_agency, sponsor_agency, pid, fms_id, ten_year_plan_category, total_budget, current_phase
    pd_rows = [
        ["202601", "DDC", "DDC", "1", "LB99NLOH", None, "100", "Construction"],                 # Library: fms prefix
        ["202601", "EDC", "EDC", "2", "X1", "ESSENTIAL RECONSTRUCTION OF FACILITIES", "200", "Design"],  # Library: ever-managed beats 'facilities'
        ["202509", "NYPL", "NYPL", "2", "X1", "ESSENTIAL RECONSTRUCTION OF FACILITIES", "200", "Design"],  # ...prior period under NYPL
        ["202601", "DDC", "DOT", "3", "P1", "Park Pedestrian Bridges", "300", "Design"],          # Bridges (structurally a bridge)
        ["202601", "DOT", "DOT", "4", "B1", "EAST RIVER BRIDGES", "400", "Construction"],          # Bridges
        ["202601", "DDC", "DEP", "5", "R1", "ROUTINE RECONSTRUCTION", "500", "Design"],            # Sewer via sponsor fallback
        ["202601", "DDC", "FDNY", "6", "F1", "NEW FACILITIES AND RENOVATIONS", "600", "Design"],   # Fire: sponsor beats generic 'facilities'
        ["202601", "DDC", None, "7", "M1", "MISCELLANEOUS", "700", "Design"],                      # Other
        ["202601", "DDC", "DEP", "8", "GI1", "GREEN INFRASTRUCTURE PROGRAM", "800", "Design"],     # Sewer: DEP green-infra via sponsor (keyword dropped)
        ["202601", "DDC", "DPR", "9", "TP1", "Land Acquisition, Tree Planting and Green Infrastructure", "900", "Design"],  # Parks: DPR via sponsor
        ["202601", "DDC", "DCLA", "10", "DC1", "MISCELLANEOUS ENERGY EFFICIENCY AND SUSTAINABILITY", "1000", "Design"],     # Cultural: DCLA owner-authoritative beats Energy
    ]
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency, pid,"
        " fms_id, ten_year_plan_category, total_budget, current_phase) VALUES (?,?,?,?,?,?,?,?)",
        pd_rows,
    )
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) VALUES (?,?,?,?,?,?)",
        [["DDC", f, "202601", tb, "0", "0"] for f, tb in
         [("LB99NLOH", "100"), ("X1", "200"), ("P1", "300"), ("B1", "400"), ("R1", "500"),
          ("F1", "600"), ("M1", "700"), ("GI1", "800"), ("TP1", "900"), ("DC1", "1000")]],
    )
    return con


def test_classification_precedence():
    con = _con()
    categories.build_category_dim(con)
    cat = dict(con.execute("SELECT fms_id, category FROM category_dim").fetchall())
    assert cat["LB99NLOH"] == "Library"            # tier-1 fms prefix
    assert cat["X1"] == "Library"                  # tier-1 ever-managed (NYPL hist) beats facilities keyword
    assert cat["P1"] == "Bridges"                  # 'Park Pedestrian Bridges' route to Bridges, not Parks
    assert cat["B1"] == "Bridges"
    assert cat["R1"] == "Sewer & Water"            # generic ten-year, routed by sponsor DEP
    assert cat["F1"] == "Fire & EMS"               # sponsor beats generic 'facilities' (tier 2 < tier 3)
    assert cat["M1"] == "Other / Uncategorized"
    assert cat["GI1"] == "Sewer & Water"           # DEP green-infra: keyword dropped, sponsor decides
    assert cat["TP1"] == "Parks & Recreation"      # DPR tree-planting: same shared label, sponsor decides
    assert cat["DC1"] == "Cultural Institutions"   # DCLA owner-authoritative beats the 'energy' keyword


def test_loader_and_names():
    rules = categories.load_category_rules()
    names = categories.category_names(rules)
    assert "Library" in names and "Fire & EMS" in names and "Sewer & Water" in names
    assert names[-1] == rules["other_label"]
    ever = [a for c in rules["categories"] for a in c.get("ever_managed_by") or []]
    assert ever == ["BPL", "NYPL", "QPL", "NYRL", "DCLA"]   # owner-authoritative declarers


def test_list_categories_tool():
    con = _con()
    materialize.materialize_all(con)
    out = lookup.list_categories_from(con)
    cats = {r["category"]: r for r in out["categories"]}
    assert "Library" in cats and "Bridges" in cats
    assert cats["Library"]["n_budget_lines"] == 2           # LB99NLOH + X1
    assert abs(sum(r["pct_budget"] for r in out["categories"]) - 100.0) < 1.0


def test_rank_projects_category_filter():
    con = _con()
    materialize.materialize_all(con)
    out = ranking.rank_projects_from(con, "budget", "total_budget", category="Library")
    assert {r["fms_id"] for r in out["rows"]} == {"LB99NLOH", "X1"}
    assert out["provenance"]["scope"]["filters"]["category"] == "Library"
