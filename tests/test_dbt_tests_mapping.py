"""--dbt-model: a model's own unique and relationships tests fill in the key and reference (issue #60)."""
from dataranger.connectors import dbt as dbt_source

FCT = {"name": "fct_payments", "unique_id": "model.shop.fct_payments", "resource_type": "model"}


def generic_test(test_name, column, attached="model.shop.fct_payments", namespace=None, **kwargs):
    return {"resource_type": "test", "attached_node": attached, "column_name": column,
            "test_metadata": {"name": test_name, "namespace": namespace,
                              "kwargs": {"column_name": column, "model": "{{ get_where_subquery(ref('fct_payments')) }}",
                                         **kwargs}}}


def manifest(*tests):
    return {"nodes": {"model.shop.fct_payments": FCT, **{f"test.shop.t{i}": t for i, t in enumerate(tests)}}}


def test_unique_test_gives_the_key():
    assert dbt_source.settings_from_tests(manifest(generic_test("unique", "payment_id")), FCT) == {"key": "payment_id"}


def test_relationships_test_gives_reference_ref_key_and_ref_model():
    m = manifest(generic_test("relationships", "customer_id", to="ref('dim_customers')", field="id"))
    assert dbt_source.settings_from_tests(m, FCT) == {"reference": "customer_id", "ref_key": "id", "ref_model": "dim_customers"}
    src = manifest(generic_test("relationships", "customer_id", to="source('crm', 'customers')", field="id"))
    assert dbt_source.settings_from_tests(src, FCT)["ref_model"] == "crm.customers"



def test_a_package_qualified_ref_resolves_to_that_packages_node():
    # Two packages have a dim_customers; ref('core', ...) names one, so the unique_id is kept.
    pkg = manifest(generic_test("relationships", "customer_id", to='ref("core", "dim_customers")', field="id"))
    pkg["nodes"]["model.core.dim_customers"] = {"name": "dim_customers", "package_name": "core",
                                                 "resource_type": "model", "unique_id": "model.core.dim_customers"}
    pkg["nodes"]["model.shop.dim_customers"] = {"name": "dim_customers", "package_name": "shop",
                                                 "resource_type": "model", "unique_id": "model.shop.dim_customers"}
    settings = dbt_source.settings_from_tests(pkg, FCT)
    assert settings["ref_model"] == "model.core.dim_customers"
    assert dbt_source.find_node(pkg, settings["ref_model"])["package_name"] == "core"
    notes = {}
    missing = manifest(generic_test("relationships", "customer_id", to="ref('nowhere', 'dim_customers')", field="id"))
    assert dbt_source.settings_from_tests(missing, FCT, notes) == {}
    assert "not a plain ref or source" in notes["reference"]


def test_a_filtered_unique_test_does_not_give_the_key():
    notes = {}
    test = generic_test("unique", "payment_id")
    test["config"] = {"where": "status = 'settled'"}
    assert dbt_source.settings_from_tests(manifest(test), FCT, notes) == {}
    assert "has a where filter" in notes["key"]
    # An unfiltered unique test next to it still gives the key.
    both = manifest(test, generic_test("unique", "external_ref"))
    assert dbt_source.settings_from_tests(both, FCT) == {"key": "external_ref"}


def test_an_unreadable_relationships_target_still_counts_as_a_candidate():
    notes = {}
    m = manifest(generic_test("relationships", "customer_id", to="ref('dim_customers')", field="id"),
                 generic_test("relationships", "merchant_id", to="ref(var('merchants_model'))", field="id"))
    assert dbt_source.settings_from_tests(m, FCT, notes) == {}
    assert "2 relationships tests (customer_id, merchant_id)" in notes["reference"]


def test_two_unique_tests_mean_no_guess_and_a_note():
    notes = {}
    m = manifest(generic_test("unique", "payment_id"), generic_test("unique", "external_ref"))
    assert dbt_source.settings_from_tests(m, FCT, notes) == {}
    assert "unique tests on 2 columns (external_ref, payment_id)" in notes["key"]


def test_two_relationships_tests_mean_no_guess_and_a_note():
    notes = {}
    m = manifest(generic_test("relationships", "customer_id", to="ref('dim_customers')", field="id"),
                 generic_test("relationships", "merchant_id", to="ref('dim_merchants')", field="id"))
    assert dbt_source.settings_from_tests(m, FCT, notes) == {}
    assert "2 relationships tests (customer_id, merchant_id)" in notes["reference"]


def test_other_tests_and_other_models_are_ignored():
    m = manifest(generic_test("unique", "id", attached="model.shop.dim_customers"),
                 generic_test("not_null", "payment_id"),
                 generic_test("unique", "payment_id", namespace="dbt_utils"),
                 generic_test("unique_combination_of_columns", None, combination_of_columns=["a", "b"]))
    assert dbt_source.settings_from_tests(m, FCT) == {}


def test_older_manifest_without_attached_node():
    test = generic_test("unique", "payment_id")
    del test["attached_node"]
    assert dbt_source.settings_from_tests(manifest(test), FCT) == {"key": "payment_id"}
    other = generic_test("unique", "id", model="{{ get_where_subquery(ref('dim_customers')) }}")
    del other["attached_node"]
    assert dbt_source.settings_from_tests(manifest(other), FCT) == {}
