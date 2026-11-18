"""Snowflake and dbt sources. Snowflake is stood in for by DuckDB behind the connector's cursor interface,
with upper-case column names and DECIMAL amounts, as Snowflake returns them."""
import json
import sys
import types
from datetime import date, timedelta

import duckdb
import pytest

from dataranger.cli import main
from dataranger.connectors import dbt as dbt_source
from dataranger.connectors import snowflake as sf
from dataranger.connectors import sql
from dataranger.patrol import Mapping, patrol

MAPPING = Mapping(category="category")


@pytest.fixture(scope="module")
def case():
    from dataranger.bench import generate, inject

    return inject(generate(7, "small", start=date.today() - timedelta(days=60)))


@pytest.fixture(scope="module")
def expected(case):
    res = patrol(case.warehouse["transactions"], case.warehouse["customers"], case.manifest, MAPPING)
    return sorted((str(r["partition"]), r["decision"]) for r in res.incidents)


class FakeCursor:
    def __init__(self, con, log, pandas):
        self.con, self.log, self.result = con, log, None
        if pandas:
            self.fetch_pandas_all = lambda: self.result.df()

    def execute(self, query):
        self.log.append(query)
        self.result = self.con.execute(query)
        self.description = self.result.description

    def fetchall(self):
        return self.result.fetchall()

    def close(self):
        pass


class FakeSnowflake:
    """A warehouse with GOLD.PAYMENTS, GOLD.CUSTOMERS and OPS.CONTROL_TOTALS."""

    def __init__(self, case, pandas=False):
        self.con, self.queries, self.pandas, self.closed = duckdb.connect(), [], pandas, False
        t, c, m = case.warehouse["transactions"], case.warehouse["customers"], case.manifest  # noqa: F841
        self.con.execute("create schema GOLD; create schema OPS")
        self.con.execute('create table GOLD.PAYMENTS as select txn_id as "TXN_ID", customer_id as "CUSTOMER_ID", '
                         'txn_date as "TXN_DATE", channel as "CHANNEL", category as "CATEGORY", '
                         'amount::decimal(18,2) as "AMOUNT", payment_channel as "PAYMENT_CHANNEL" from t')
        self.con.execute('create table GOLD.CUSTOMERS as select customer_id as "CUSTOMER_ID", segment as "SEGMENT" from c')
        self.con.execute('create table OPS.CONTROL_TOTALS as select txn_date as "TXN_DATE", channel as "CHANNEL", '
                         'n as "N", amount::decimal(18,2) as "AMOUNT" from m')

    def cursor(self):
        return FakeCursor(self.con, self.queries, self.pandas)

    def close(self):
        self.closed = True


def test_table_and_column_names_are_checked():
    assert sql.relation('ANALYTICS.GOLD."odd name"') == 'ANALYTICS.GOLD."odd name"'
    for bad in ["a; drop table x", "x--y", "a..b", "", 'a."b"c"', "a b"]:
        with pytest.raises(ValueError):
            sql.relation(bad)
    with pytest.raises(ValueError):
        sql.column("amount; delete from t")


def test_queries_are_selects_with_the_window():
    facts, refs, totals = sql.build_queries("GOLD.PAYMENTS", "GOLD.CUSTOMERS", "OPS.CONTROL_TOTALS", MAPPING,
                                            days=30, today=date(2026, 11, 12))
    assert facts == ("select txn_id, customer_id, txn_date, channel, amount, category from GOLD.PAYMENTS "
                     "where txn_date >= '2026-10-14'::date")
    assert refs == "select distinct customer_id from GOLD.CUSTOMERS"
    assert totals.endswith("from OPS.CONTROL_TOTALS where txn_date >= '2026-10-14'::date")
    with pytest.raises(ValueError):
        sql.build_queries("t", "r", "m", MAPPING, days=0)


@pytest.mark.parametrize("pandas", [False, True])
def test_snowflake_source_gives_the_same_patrol(case, expected, pandas):
    fake = FakeSnowflake(case, pandas)
    facts, refs, totals = sf.from_snowflake("GOLD.PAYMENTS", "GOLD.CUSTOMERS", "OPS.CONTROL_TOTALS", MAPPING,
                                            opener=lambda connection, params: fake)
    assert fake.closed
    assert len(fake.queries) == 3 and all(q.lower().startswith("select ") for q in fake.queries)
    assert list(facts.columns) == ["txn_id", "customer_id", "txn_date", "channel", "amount", "category"]
    assert facts["amount"].dtype == float and totals["amount"].dtype == float
    res = patrol(facts, refs, totals, MAPPING)
    assert sorted((str(r["partition"]), r["decision"]) for r in res.incidents) == expected


def test_snowflake_days_limits_what_is_read(case):
    fake = FakeSnowflake(case)
    facts, _, totals = sf.from_snowflake("GOLD.PAYMENTS", "GOLD.CUSTOMERS", "OPS.CONTROL_TOTALS", MAPPING, days=10,
                                         opener=lambda connection, params: fake)
    first = date.today() - timedelta(days=9)
    assert min(facts["txn_date"]) >= first and min(totals["txn_date"]) >= first
    assert 0 < len(facts) < len(case.warehouse["transactions"])


def test_snowflake_errors_are_one_readable_line(case):
    def refuse(connection, params):
        raise RuntimeError("250001: Incorrect username or password was specified.\nstack...")

    with pytest.raises(ValueError, match="could not connect to Snowflake: 250001: Incorrect username"):
        sf.from_snowflake("GOLD.PAYMENTS", "GOLD.CUSTOMERS", "OPS.CONTROL_TOTALS", MAPPING, opener=refuse)
    fake = FakeSnowflake(case)
    with pytest.raises(ValueError, match="Snowflake query failed"):
        sf.from_snowflake("GOLD.NOPE", "GOLD.CUSTOMERS", "OPS.CONTROL_TOTALS", MAPPING, opener=lambda c, p: fake)
    assert fake.closed
    with pytest.raises(ValueError, match="not valid"):  # refused before any connection is opened
        sf.from_snowflake("GOLD.PAYMENTS; drop table x", "r", "m", MAPPING, opener=refuse)


def test_snowflake_connection_settings():
    assert sf.connection_params("prod") == {"connection_name": "prod"}
    env = {"SNOWFLAKE_ACCOUNT": "ab12345", "SNOWFLAKE_USER": "ranger", "SNOWFLAKE_PRIVATE_KEY_PATH": "/k.p8",
           "SNOWFLAKE_ROLE": "READER", "SNOWFLAKE_WAREHOUSE": "XS", "UNRELATED": "x"}
    assert sf.connection_params(env=env) == {"account": "ab12345", "user": "ranger", "private_key_file": "/k.p8",
                                             "role": "READER", "warehouse": "XS"}
    mfa = {"SNOWFLAKE_ACCOUNT": "ab12345", "SNOWFLAKE_USER": "ranger", "SNOWFLAKE_PASSWORD": "pw",
           "SNOWFLAKE_AUTHENTICATOR": "username_password_mfa", "SNOWFLAKE_PASSCODE": "123456"}
    assert sf.connection_params(env=mfa) == {"account": "ab12345", "user": "ranger", "password": "pw",
                                             "authenticator": "username_password_mfa", "passcode": "123456"}
    assert sf.connection_params(env={}) == {}  # the connector's default connection
    with pytest.raises(ValueError, match="SNOWFLAKE_ACCOUNT is not set"):
        sf.connection_params(env={"SNOWFLAKE_USER": "ranger"})


def test_cli_snowflake_source(case, expected, tmp_path, monkeypatch, capsys):
    fake = FakeSnowflake(case)
    seen = {}
    monkeypatch.setattr(sf, "connect", lambda connection, params=None: seen.update(connection=connection) or fake)
    args = ["patrol", "--snowflake", "prod", "--table", "GOLD.PAYMENTS", "--ref-table", "GOLD.CUSTOMERS",
            "--manifest-table", "OPS.CONTROL_TOTALS", "--category", "category", "--out", str(tmp_path / "out"), "--json"]
    assert main(args) == 0
    assert seen == {"connection": "prod"}
    assert json.loads(capsys.readouterr().out)["incidents"] == len(expected)
    with pytest.raises(SystemExit):  # --snowflake still needs the three tables
        main(["patrol", "--snowflake", "--table", "GOLD.PAYMENTS"])
    with pytest.raises(SystemExit):  # one source at a time
        main(["patrol", "--snowflake", "--db", "x.duckdb", "--table", "t", "--ref-table", "r", "--manifest-table", "m"])


def _dbt_project(tmp_path, case, meta=None, profile_output=None):
    """A dbt project as dbt leaves it after `dbt build`: project file, profile, manifest and built tables."""
    project = tmp_path / "shop"
    (project / "target").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: shop\nprofile: shop\n")
    output = profile_output or "type: duckdb\n      path: \"{{ env_var('SHOP_DB', 'shop.duckdb') }}\""
    (project / "profiles.yml").write_text(f"shop:\n  target: dev\n  outputs:\n    dev:\n      {output}\n")
    t, c, m = case.warehouse["transactions"], case.warehouse["customers"], case.manifest  # noqa: F841
    with duckdb.connect(str(project / "shop.duckdb")) as con:
        con.execute("create table fct_payments as select txn_id as payment_id, customer_id, txn_date as paid_on, "
                    "channel, category, amount from t")
        con.execute("create table dim_customers as select * from c")
        con.execute("create table control_totals as select txn_date as paid_on, channel, n, amount from m")
    settings = {"ref_model": "dim_customers", "manifest_model": "control_totals", "key": "payment_id",
                "date": "paid_on", "category": "category"} if meta is None else meta

    def node(name, node_meta=None, materialized="table"):
        return {"name": name, "resource_type": "model", "relation_name": f'"shop"."main"."{name}"',
                "database": "shop", "schema": "main", "alias": name, "meta": node_meta or {},
                "config": {"materialized": materialized, "meta": node_meta or {}}}

    manifest = {"metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
                "nodes": {"model.shop.fct_payments": node("fct_payments", {"dataranger": settings}),
                          "model.shop.dim_customers": node("dim_customers"),
                          "model.shop.control_totals": node("control_totals"),
                          "model.shop.stg_tmp": node("stg_tmp", materialized="ephemeral")},
                "sources": {}}
    (project / "target" / "manifest.json").write_text(json.dumps(manifest))
    return project


def test_dbt_source_reads_mapping_from_model_meta(case, expected, tmp_path):
    project = _dbt_project(tmp_path, case)
    facts, refs, totals, mapping, name = dbt_source.from_dbt(project, "fct_payments")
    assert name == "fct_payments"
    assert (mapping.key, mapping.date, mapping.category, mapping.amount) == ("payment_id", "paid_on", "category", "amount")
    res = patrol(facts, refs, totals, mapping)
    assert sorted((str(r["partition"]), r["decision"]) for r in res.incidents) == expected


def test_cli_dbt_source_and_overrides(case, expected, tmp_path, capsys):
    project = _dbt_project(tmp_path, case, meta={})  # nothing in meta: everything comes from flags
    args = ["patrol", "--dbt-model", "fct_payments", "--dbt-project-dir", str(project), "--dbt-ref-model", "dim_customers",
            "--dbt-manifest-model", "control_totals", "--key", "payment_id", "--date", "paid_on",
            "--category", "category", "--out", str(tmp_path / "out"), "--json"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["incidents"] == len(expected)


def test_dbt_problems_are_explained(case, tmp_path):
    project = _dbt_project(tmp_path, case)
    with pytest.raises(ValueError, match="not found in the manifest. Models and seeds: control_totals"):
        dbt_source.from_dbt(project, "nope")
    with pytest.raises(ValueError, match="set ref_model and manifest_model"):
        dbt_source.from_dbt(project, "dim_customers")
    with pytest.raises(ValueError, match="is ephemeral"):
        dbt_source.from_dbt(project, "stg_tmp", ref_model="dim_customers", manifest_model="control_totals")
    with pytest.raises(ValueError, match="Run `dbt build`"):
        dbt_source.from_dbt(tmp_path / "missing", "fct_payments")
    with pytest.raises(SystemExit, match="patrol: dbt model 'nope' not found"):
        main(["patrol", "--dbt-model", "nope", "--dbt-project-dir", str(project)])
    other = _dbt_project(tmp_path / "pg", case, profile_output="type: postgres\n      host: localhost")
    with pytest.raises(ValueError, match="'postgres' is not supported yet"):
        dbt_source.from_dbt(other, "fct_payments")
    typo = _dbt_project(tmp_path / "typo", case, meta={"ref_model": "dim_customers", "manifest_model": "control_totals",
                                                       "keys": "payment_id"})
    with pytest.raises(ValueError, match=r"unknown setting\(s\): keys"):
        dbt_source.from_dbt(typo, "fct_payments")


def test_dbt_snowflake_target_uses_the_profile(case, tmp_path, monkeypatch):
    monkeypatch.setenv("SNOW_PW", "s3cret")
    output = ("type: snowflake\n      account: ab12345\n      user: ranger\n      password: \"{{ env_var('SNOW_PW') }}\"\n"
              "      role: READER\n      warehouse: XS\n      database: SHOP\n      schema: MAIN\n      threads: 4")
    project = _dbt_project(tmp_path, case, profile_output=output)
    seen = {}

    class Shop(FakeSnowflake):
        def __init__(self):
            self.con, self.queries, self.pandas, self.closed = duckdb.connect(str(project / "shop.duckdb"), read_only=True), [], False, False

    def opener(connection, params):
        seen.update(params)
        return Shop()

    facts, refs, totals, mapping, _ = dbt_source.from_dbt(project, "fct_payments", snowflake_opener=opener)
    assert seen == {"account": "ab12345", "user": "ranger", "password": "s3cret", "role": "READER",
                    "warehouse": "XS", "database": "SHOP", "schema": "MAIN"}
    assert len(facts) == len(case.warehouse["transactions"]) and len(totals) == len(case.manifest)
    monkeypatch.delenv("SNOW_PW")
    with pytest.raises(ValueError, match="environment variable SNOW_PW is not set"):
        dbt_source.from_dbt(project, "fct_payments", snowflake_opener=opener)


def test_bigquery_queries_use_date_literals_and_backticks():
    facts, refs, totals = sql.build_queries("`my-proj`.`gold`.`payments`", "`my-proj`.gold.customers",
                                            "`my-proj`.`ops`.`control_totals`", MAPPING, days=30,
                                            today=date(2026, 11, 12), dialect="bigquery")
    assert facts == ("select txn_id, customer_id, txn_date, channel, amount, category from `my-proj`.`gold`.`payments` "
                     "where txn_date >= DATE '2026-10-14'")
    assert refs == "select distinct customer_id from `my-proj`.gold.customers"
    assert totals.endswith("from `my-proj`.`ops`.`control_totals` where txn_date >= DATE '2026-10-14'")
    for bad in ["`a`b`.t", "`a\\`.t", "``.t", "`a`;drop"]:
        with pytest.raises(ValueError):
            sql.build_queries(bad, "r", "m", MAPPING, dialect="bigquery")
    with pytest.raises(ValueError):  # backticks only for BigQuery
        sql.build_queries("`my-proj`.gold.payments", "r", "m", MAPPING)


class FakeBigQuery:
    """The dbt project's DuckDB file behind the BigQuery client's query().result() interface."""

    def __init__(self, path):
        self.con, self.queries, self.closed = duckdb.connect(str(path), read_only=True), [], False

    def query(self, query, job_config=None):
        self.queries.append(query)
        result = self.con.execute(query.replace("`", '"'))
        names = [d[0] for d in result.description]
        rows = [types.SimpleNamespace(values=lambda r=r: r) for r in result.fetchall()]

        class Rows(list):
            schema = [types.SimpleNamespace(name=n) for n in names]

        return types.SimpleNamespace(result=lambda: Rows(rows))

    def close(self):
        self.closed = True


def _bigquery_project(tmp_path, case, output):
    project = _dbt_project(tmp_path, case, profile_output=output)
    path = project / "target" / "manifest.json"
    manifest = json.loads(path.read_text())
    for node in manifest["nodes"].values():
        node["relation_name"] = node["relation_name"].replace('"', "`")
    path.write_text(json.dumps(manifest))
    return project


def test_dbt_bigquery_target_uses_the_profile(case, expected, tmp_path):
    output = ("type: bigquery\n      method: service-account\n      project: shop-data\n"
              "      execution_project: shop-billing\n      dataset: main\n      keyfile: keys/sa.json\n"
              "      location: EU\n      threads: 4")
    project = _bigquery_project(tmp_path, case, output)
    seen, opened = {}, []

    def opener(params):
        seen.update(params)
        opened.append(FakeBigQuery(project / "shop.duckdb"))
        return opened[-1]

    facts, refs, totals, mapping, _ = dbt_source.from_dbt(project, "fct_payments", days=30, bigquery_opener=opener)
    assert seen == {"method": "service-account", "project": "shop-billing", "location": "EU",
                    "keyfile": str(project / "keys" / "sa.json")}
    assert opened[0].closed and len(opened[0].queries) == 3
    assert all("`shop`.`main`." in q for q in opened[0].queries)
    assert "DATE '" in opened[0].queries[0] and "::" not in opened[0].queries[0]
    full = dbt_source.from_dbt(project, "fct_payments", bigquery_opener=opener)
    res = patrol(*full[:4])
    assert sorted((str(r["partition"]), r["decision"]) for r in res.incidents) == expected
    assert 0 < len(facts) < len(full[0])


def test_dbt_bigquery_profile_methods(tmp_path):
    from dataranger.connectors import bigquery as bq

    assert bq.params_from_profile({"type": "bigquery", "method": "oauth", "project": "p", "dataset": "d"}) == \
        {"method": "oauth", "project": "p"}
    info = {"type": "service_account", "project_id": "p"}
    assert bq.params_from_profile({"method": "service-account-json", "project": "p", "keyfile_json": info}) == \
        {"method": "service-account-json", "project": "p", "keyfile_json": info}
    for output, message in [({"method": "oauth-secrets", "project": "p"}, "'oauth-secrets' is not supported yet"),
                            ({"method": "service-account", "project": "p"}, "needs keyfile"),
                            ({"method": "service-account-json", "project": "p"}, "needs keyfile_json")]:
        with pytest.raises(ValueError, match=message):
            bq.params_from_profile(output)


def test_dbt_bigquery_errors_are_one_line(case, tmp_path, monkeypatch):
    project = _bigquery_project(tmp_path, case, "type: bigquery\n      method: oauth\n      project: shop")

    def refused(params):
        raise RuntimeError("Your default credentials were not found.\nTo set up ADC, see ...")

    with pytest.raises(ValueError, match=r"^could not connect to BigQuery: Your default credentials were not found\.$"):
        dbt_source.from_dbt(project, "fct_payments", bigquery_opener=refused)

    class Broken(FakeBigQuery):
        def query(self, query, job_config=None):
            raise RuntimeError("404 Not found: Table shop:main.fct_payments was not found in location US\n\nLocation: US")

    broken = Broken(project / "shop.duckdb")
    with pytest.raises(ValueError, match=r"^BigQuery query failed: 404 Not found: Table .* location US$"):
        dbt_source.from_dbt(project, "fct_payments", bigquery_opener=lambda params: broken)
    assert broken.closed

    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", None)
    monkeypatch.setitem(sys.modules, "google.cloud", None)
    with pytest.raises(SystemExit, match=r'pip install "dataranger\[bigquery\]"'):
        dbt_source.from_dbt(project, "fct_payments")


def test_days_and_markdown_summary_for_file_sources(case, tmp_path, capsys):
    f, r, m = tmp_path / "facts.csv", tmp_path / "customers.csv", tmp_path / "totals.csv"
    case.warehouse["transactions"].to_csv(f, index=False)
    case.warehouse["customers"].to_csv(r, index=False)
    case.manifest.to_csv(m, index=False)
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n")
    args = ["patrol", "--facts-file", str(f), "--ref-file", str(r), "--manifest-file", str(m),
            "--category", "category", "--days", "30", "--out", str(tmp_path / "out"), "--summary-md", str(summary), "--json"]
    assert main(args) == 0
    counts = json.loads(capsys.readouterr().out)
    text = summary.read_text()
    assert text.startswith("earlier step\n## DataRanger patrol: `facts`")  # appended, not overwritten
    assert f"**{counts['incidents']} incidents** across " in text
    days_patrolled = int(text.split("incidents** across ")[1].split(" days")[0])
    assert 28 <= days_patrolled <= 30  # the window ends today; the data may end yesterday
    assert f"**{counts['escalate']} need a person**" in text and "| Partition | Problems |" in text
    assert "No data was modified" in text
    with pytest.raises(SystemExit, match="no rows in the last 1 days"):
        future = tmp_path / "old.csv"
        case.warehouse["transactions"].assign(txn_date="2020-01-01").to_csv(future, index=False)
        main(["patrol", "--facts-file", str(future), "--ref-file", str(r), "--manifest-file", str(m), "--days", "1",
              "--out", str(tmp_path / "out2")])


def _generic_test(test_name, column, **kwargs):
    """A dbt generic test on fct_payments, as the manifest records it (see tests/test_dbt_tests_mapping.py)."""
    return {"resource_type": "test", "attached_node": "model.shop.fct_payments", "column_name": column,
            "test_metadata": {"name": test_name, "namespace": None, "kwargs": {"column_name": column, **kwargs}}}


def _with_tests(project, *tests):
    path = project / "target" / "manifest.json"
    data = json.loads(path.read_text())
    data["nodes"]["model.shop.fct_payments"]["unique_id"] = "model.shop.fct_payments"
    data["nodes"].update({f"test.shop.t{i}": t for i, t in enumerate(tests)})
    path.write_text(json.dumps(data))


def test_from_dbt_prefers_command_line_then_meta_then_tests(case, tmp_path, capsys):
    meta = {"ref_model": "dim_customers", "manifest_model": "control_totals", "date": "paid_on", "category": "category"}
    project = _dbt_project(tmp_path, case, meta=meta)
    _with_tests(project, _generic_test("unique", "payment_id"))
    *_, mapping, _ = dbt_source.from_dbt(project, "fct_payments")
    assert mapping.key == "payment_id"  # from the test: meta has no key

    meta_key = _dbt_project(tmp_path / "meta", case, meta={**meta, "key": "payment_id"})
    _with_tests(meta_key, _generic_test("unique", "customer_id"))
    *_, mapping, _ = dbt_source.from_dbt(meta_key, "fct_payments")
    assert mapping.key == "payment_id"  # meta wins over the test

    *_, mapping, _ = dbt_source.from_dbt(project, "fct_payments", mapping_overrides={"key": "customer_id"})
    assert mapping.key == "customer_id"  # the command line wins over both
    assert capsys.readouterr().err == ""


def test_from_dbt_keeps_the_default_and_says_so_when_ambiguous(case, tmp_path, capsys):
    meta = {"ref_model": "dim_customers", "manifest_model": "control_totals", "date": "paid_on"}
    project = _dbt_project(tmp_path, case, meta=meta)
    _with_tests(project, _generic_test("unique", "payment_id"), _generic_test("unique", "customer_id"))
    *_, mapping, _ = dbt_source.from_dbt(project, "fct_payments", mapping_overrides={"key": "payment_id"})
    assert capsys.readouterr().err == ""  # --key given: nothing to say
    try:
        dbt_source.from_dbt(project, "fct_payments")
    except Exception:
        pass  # the default txn_id column doesn't exist in this warehouse; only the note matters here
    err = capsys.readouterr().err
    assert "dataranger: dbt model 'fct_payments' has unique tests on 2 columns" in err
    assert "pass --key" in err
