"""Patrol the tables behind dbt models. Read-only.

DataRanger reads dbt's own files and then queries the warehouse directly; dbt itself is not run:
  * target/manifest.json says which table each model was built as (run `dbt build` or `dbt parse` first),
  * profiles.yml says how to connect (DuckDB, Snowflake and Postgres targets are supported),
  * the fact model's `meta: dataranger:` block can hold the column mapping, so it lives with the model.

Example, in the model's YAML file:

    models:
      - name: fct_payments
        meta:
          dataranger:
            ref_model: dim_customers
            manifest_model: control_totals
            key: payment_id
            date: paid_on
            segment: channel
            amount: amount

Without a `key`, `reference` or `ref_key` there (or on the command line), the model's dbt tests fill them in:
a `unique` test on one column gives the key, and a `relationships` test gives the reference column, the
reference model and its key. Two such tests on one model are ambiguous, so nothing is guessed from them.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import sql

MAPPING_KEYS = ("key", "reference", "ref_key", "date", "segment", "category", "amount")
_ENV_VAR = re.compile(r"""\{\{\s*env_var\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]*)['"]\s*)?\)\s*(?:\|[^}]*)?\}\}""")

# dbt-snowflake profile key -> Snowflake connector argument
SNOWFLAKE_KEYS = {
    "account": "account", "user": "user", "password": "password", "role": "role", "warehouse": "warehouse",
    "database": "database", "schema": "schema", "authenticator": "authenticator", "token": "token",
    "private_key_path": "private_key_file", "private_key_passphrase": "private_key_file_pwd",
}


def _yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise SystemExit('The dbt source needs PyYAML: pip install "dataranger[dbt]"') from None
    text = path.read_text(encoding="utf-8")

    def env(match):
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ValueError(f"{path}: environment variable {name} is not set")

    return yaml.safe_load(_ENV_VAR.sub(env, text)) or {}


def load_manifest(project_dir: str | Path, manifest_path: str | Path | None = None) -> dict:
    path = Path(manifest_path) if manifest_path else Path(project_dir) / "target" / "manifest.json"
    if not path.exists():
        raise ValueError(f"{path} not found. Run `dbt build` (or `dbt parse`) in the project first, "
                         "so dbt writes target/manifest.json.")
    return json.loads(path.read_text(encoding="utf-8"))


def find_node(manifest: dict, name: str) -> dict:
    """A model, seed or snapshot by name, or a source written as source_name.table_name.

    A manifest unique_id (model.package.name, source.package.source_name.table_name) also works, for a name two
    packages share; settings_from_tests gives one for a package-qualified ref.
    """
    if name in manifest.get("sources", {}):
        return manifest["sources"][name]
    by_id = manifest.get("nodes", {}).get(name)
    if by_id and by_id.get("resource_type") in ("model", "seed", "snapshot"):
        return by_id
    nodes = [n for n in manifest.get("nodes", {}).values()
             if n.get("name") == name and n.get("resource_type") in ("model", "seed", "snapshot")]
    nodes += [s for s in manifest.get("sources", {}).values() if f"{s.get('source_name')}.{s.get('name')}" == name]
    if not nodes:
        known = sorted(n["name"] for n in manifest.get("nodes", {}).values()
                       if n.get("resource_type") in ("model", "seed", "snapshot"))
        raise ValueError(f"dbt model {name!r} not found in the manifest. Models and seeds: {', '.join(known) or 'none'}")
    if len(nodes) > 1:
        raise ValueError(f"dbt model name {name!r} matches {len(nodes)} nodes; rename one or use a unique name")
    return nodes[0]


def relation_name(node: dict) -> str:
    if node.get("config", {}).get("materialized") == "ephemeral":
        raise ValueError(f"dbt model {node['name']!r} is ephemeral, so there is no table to patrol")
    name = node.get("relation_name")
    if not name:  # older manifests: build it from the parts
        name = ".".join(p for p in (node.get("database"), node.get("schema"), node.get("alias") or node.get("name")) if p)
    return sql.relation(name, f"relation of dbt model {node['name']!r}")


def model_settings(node: dict) -> dict:
    """The model's `meta: dataranger:` block (dbt keeps meta at the top level and under config)."""
    meta = {**(node.get("config", {}).get("meta") or {}), **(node.get("meta") or {})}
    settings = meta.get("dataranger") or {}
    if not isinstance(settings, dict):
        raise ValueError(f"meta.dataranger on dbt model {node['name']!r} must be a mapping of settings")
    unknown = sorted(set(settings) - set(MAPPING_KEYS) - {"ref_model", "manifest_model"})
    if unknown:
        raise ValueError(f"meta.dataranger on dbt model {node['name']!r} has unknown setting(s): {', '.join(unknown)}")
    return settings


_REF = re.compile(r"""^\s*ref\(\s*(?:['"]([^'"]+)['"]\s*,\s*)?['"]([^'"]+)['"]\s*(?:,[^)]*)?\)\s*$""")
_SOURCE = re.compile(r"""^\s*source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*$""")


def _tests_on(manifest: dict, node: dict, test_name: str) -> list[dict]:
    """Generic dbt tests named test_name (unique, relationships) attached to node, one entry per test."""
    uid = node.get("unique_id")
    out = []
    for test in manifest.get("nodes", {}).values():
        meta = test.get("test_metadata") or {}
        if test.get("resource_type") != "test" or meta.get("name") != test_name or meta.get("namespace"):
            continue
        kwargs = meta.get("kwargs") or {}
        attached = test.get("attached_node")
        if attached is None:  # manifests before dbt 1.5: the model is only named in the kwargs
            attached_here = f"ref('{node['name']}')" in str(kwargs.get("model", "")).replace('"', "'")
        else:
            attached_here = attached == uid
        column = kwargs.get("column_name") or test.get("column_name")
        if attached_here and isinstance(column, str) and column:
            # A `where` config limits the test to some rows, so it says nothing about the whole model.
            where = (test.get("config") or {}).get("where") or kwargs.get("where")
            out.append({"column": column, **kwargs, "_filtered": bool(where)})
    return out


def _model_name(manifest: dict, to: str) -> str | None:
    """The model a relationships test points at: ref('dim') -> dim, source('s', 't') -> s.t.

    A package-qualified ref('pkg', 'dim') gives that package's node by unique_id, so a name another package also
    uses stays unambiguous for find_node; None when the manifest has no such node, or `to` is not a plain ref or
    source (a var or other Jinja), so nothing is guessed.
    """
    if m := _REF.match(to or ""):
        package, name = m.group(1), m.group(2)
        if package is None:
            return name
        return next((uid for uid, n in manifest.get("nodes", {}).items()
                     if n.get("package_name") == package and n.get("name") == name
                     and n.get("resource_type") in ("model", "seed", "snapshot")), None)
    if m := _SOURCE.match(to or ""):
        return f"{m.group(1)}.{m.group(2)}"
    return None


def settings_from_tests(manifest: dict, node: dict, notes: dict[str, str] | None = None) -> dict:
    """key, reference, ref_key and ref_model taken from the model's own dbt tests.

    A `unique` test on one column names the key; a `relationships` test names the reference column, the
    model it points to and that model's key. With two tests of a kind, nothing is taken from that kind,
    and notes[field] says so (field is key or reference).
    """
    out = {}
    notes = {} if notes is None else notes
    name = node["name"]
    uniques = _tests_on(manifest, node, "unique")
    unique = sorted({t["column"] for t in uniques if not t["_filtered"]})
    if len(unique) == 1:
        out["key"] = unique[0]
    elif len(unique) > 1:
        notes["key"] = (f"dbt model {name!r} has unique tests on {len(unique)} columns ({', '.join(unique)}), "
                        "so the key is not guessed from them; set key in meta.dataranger or pass --key")
    elif filtered := sorted({t["column"] for t in uniques}):
        notes["key"] = (f"the unique test on {', '.join(filtered)} of dbt model {name!r} has a where filter, so it "
                        "does not make a key for the whole model; set key in meta.dataranger or pass --key")
    advice = "set reference, ref_key and ref_model in meta.dataranger or pass --reference, --ref-key and --dbt-ref-model"
    # Every attached relationships test counts, also one whose target can't be read: two candidates mean no guess.
    rels = {(t["column"], str(t.get("to", "")), t.get("field"), t["_filtered"])
            for t in _tests_on(manifest, node, "relationships")}
    if len(rels) == 1:
        column, to, field, filtered = next(iter(rels))
        ref_model = _model_name(manifest, to)
        if ref_model and isinstance(field, str) and field and not filtered:
            out.update(reference=column, ref_model=ref_model, ref_key=field)
        else:
            why = "has a where filter" if filtered else f"points at {to!r}, which is not a plain ref or source"
            if not filtered and ref_model and not (isinstance(field, str) and field):
                why = "names no field"
            notes["reference"] = (f"the relationships test on {column} of dbt model {name!r} {why}, so the reference "
                                  f"is not taken from it; {advice}")
    elif len(rels) > 1:
        cols = ", ".join(sorted({r[0] for r in rels}))
        notes["reference"] = (f"dbt model {name!r} has {len(rels)} relationships tests ({cols}), "
                              f"so the reference is not guessed from them; {advice}")
    return out


def load_target(project_dir: str | Path, target: str | None = None, profiles_dir: str | Path | None = None) -> dict:
    """The connection details dbt would use: the project's profile and its chosen (or default) target."""
    project_dir = Path(project_dir)
    project_file = project_dir / "dbt_project.yml"
    if not project_file.exists():
        raise ValueError(f"{project_file} not found: point --dbt-project-dir at the folder that holds dbt_project.yml")
    profile_name = _yaml(project_file).get("profile")
    candidates = [Path(p) for p in (profiles_dir, os.environ.get("DBT_PROFILES_DIR"), project_dir, Path.home() / ".dbt") if p]
    profiles_file = next((d / "profiles.yml" for d in candidates if (d / "profiles.yml").exists()), None)
    if profiles_file is None:
        raise ValueError("profiles.yml not found in " + ", ".join(str(c) for c in candidates))
    profile = _yaml(profiles_file).get(profile_name)
    if not profile:
        raise ValueError(f"profile {profile_name!r} (from dbt_project.yml) is not in {profiles_file}")
    target = target or profile.get("target")
    output = (profile.get("outputs") or {}).get(target)
    if not output:
        raise ValueError(f"target {target!r} is not in profile {profile_name!r}; "
                         f"targets: {', '.join(profile.get('outputs') or {}) or 'none'}")
    return {**output, "_project_dir": str(project_dir)}


def _read_duckdb(target: dict, queries):
    import duckdb

    path = target.get("path") or ":memory:"
    if path == ":memory:" or str(path).startswith("md:"):
        raise ValueError("the dbt DuckDB target must be a database file for DataRanger to read it")
    path = Path(path)
    if not path.is_absolute():
        path = Path(target["_project_dir"]) / path
    if not path.exists():
        raise ValueError(f"DuckDB file {path} not found. Run `dbt build` first.")
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            return tuple(con.execute(q).df() for q in queries)
        finally:
            con.close()
    except duckdb.Error as e:
        hint = " Upgrade DuckDB to the version dbt uses: pip install -U duckdb" if "version number" in str(e) else ""
        raise ValueError(f"could not read {path}: {str(e).splitlines()[0]}{hint}") from None


def from_dbt(project_dir: str | Path, model: str, mapping_overrides: dict | None = None, ref_model: str | None = None,
             manifest_model: str | None = None, target: str | None = None, profiles_dir: str | Path | None = None,
             days: int | None = None, manifest_path: str | Path | None = None, snowflake_opener=None,
             postgres_opener=None):
    """Read the tables behind a dbt fact model, its reference model and its control totals.

    Returns (facts, refs, control_totals, mapping, table_name). Column mapping comes from the model's
    `meta: dataranger:` block; values in `mapping_overrides` win over it.
    """
    from ..patrol import Mapping

    manifest = load_manifest(project_dir, manifest_path)
    node = find_node(manifest, model)
    overrides = {k: v for k, v in (mapping_overrides or {}).items() if v is not None}
    # Preference: command line, then meta.dataranger, then the model's dbt tests, then the defaults.
    meta = model_settings(node)
    notes: dict[str, str] = {}
    from_tests = settings_from_tests(manifest, node, notes)
    if {"reference", "ref_key"} & (set(meta) | set(overrides)):
        # The relationships test describes one column; once the reference is set elsewhere it may not be that one.
        for field in ("reference", "ref_key", "ref_model"):
            from_tests.pop(field, None)
    settings = {**from_tests, **meta}
    for field, note in notes.items():
        if field not in meta and field not in overrides:  # only when the default is really what gets used
            print(f"dataranger: {note}.", file=sys.stderr)
    mapping = Mapping(**{"category": None, **{k: settings[k] for k in MAPPING_KEYS if k in settings}, **overrides})
    ref_model = ref_model or settings.get("ref_model")
    manifest_model = manifest_model or settings.get("manifest_model")
    missing = [flag for flag, v in (("ref_model", ref_model), ("manifest_model", manifest_model)) if not v]
    if missing:
        raise ValueError(f"dbt model {model!r}: set {' and '.join(missing)} in its meta.dataranger block, "
                         "or pass --dbt-ref-model / --dbt-manifest-model")
    table, ref_table, man_table = (relation_name(n) for n in
                                   (node, find_node(manifest, ref_model), find_node(manifest, manifest_model)))
    queries = sql.build_queries(table, ref_table, man_table, mapping, days)
    out = load_target(project_dir, target, profiles_dir)
    kind = out.get("type")
    if kind == "duckdb":
        frames = _read_duckdb(out, queries)
    elif kind == "snowflake":
        from . import snowflake

        params = {arg: out[key] for key, arg in SNOWFLAKE_KEYS.items() if out.get(key)}
        frames = snowflake.run_queries(queries, params=params, opener=snowflake_opener)
    elif kind == "postgres":
        from . import postgres

        frames = postgres.run_queries(queries, params=postgres.params_from_profile(out), opener=postgres_opener)
    else:
        raise ValueError(f"dbt target type {kind!r} is not supported yet (DuckDB, Snowflake and Postgres are). "
                         "Export the three tables to Parquet and use --facts-file instead.")
    facts, refs, totals = sql.normalize(*frames, mapping)
    return facts, refs, totals, mapping, node["name"]
