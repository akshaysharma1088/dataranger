import json

import pytest

from dataranger import mcp_server as M


def test_demo_summary_and_incident(tmp_path):
    out = tmp_path / "demo"
    s = M.run_demo(str(out))
    assert s["incidents"] == s["auto_fixed"] + s["no_action_needed"] + s["escalate"]
    assert (out / "dashboard.html").exists() and (out / "audit.jsonl").exists()
    assert all(i["decision"] == "escalate" for i in s["needs_a_person"])
    json.dumps(s)  # an MCP result must be JSON serializable
    part = next(i["partition"] for i in s["all_incidents"] if i["partition"] != "table-level")
    rec = M.get_incident(str(out), part)
    assert rec["partition"] == part and "context" in rec


def test_patrol_files_matches_demo_data_and_leaves_input_alone(tmp_path):
    from datetime import date, timedelta
    from dataranger.bench import generate, inject

    case = inject(generate(7, "small", start=date.today() - timedelta(days=60)))
    f, r, m = tmp_path / "facts.csv", tmp_path / "customers.csv", tmp_path / "totals.csv"
    case.warehouse["transactions"].to_csv(f, index=False)
    case.warehouse["customers"].to_csv(r, index=False)
    case.manifest.to_csv(m, index=False)
    before = f.read_bytes()
    s = M.patrol_files(str(f), str(r), str(m), out_dir=str(tmp_path / "out"))
    assert s["table"] == "facts" and s["incidents"] > 0
    assert f.read_bytes() == before


def test_bad_mapping_is_a_clear_error(tmp_path):
    from datetime import date, timedelta
    from dataranger.bench import generate, inject

    case = inject(generate(7, "small", start=date.today() - timedelta(days=60)))
    f, r, m = tmp_path / "facts.csv", tmp_path / "customers.csv", tmp_path / "totals.csv"
    case.warehouse["transactions"].to_csv(f, index=False)
    case.warehouse["customers"].to_csv(r, index=False)
    case.manifest.to_csv(m, index=False)
    with pytest.raises(ValueError, match="wrong_id"):
        M.patrol_files(str(f), str(r), str(m), key="wrong_id", out_dir=str(tmp_path / "out"))


def test_get_incident_without_patrol(tmp_path):
    with pytest.raises(ValueError, match="run a patrol tool first"):
        M.get_incident(str(tmp_path), "2026-01-01")


def test_server_lists_tools():
    pytest.importorskip("mcp")
    import asyncio

    server = M.build_server()
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"patrol_duckdb", "patrol_files", "get_incident", "run_demo"}


def test_tool_definitions_are_documented():
    pytest.importorskip("mcp")
    import asyncio

    tools = {t.name: t.model_dump(by_alias=True) for t in asyncio.run(M.build_server().list_tools())}
    for name, tool in tools.items():
        props = tool["inputSchema"]["properties"]
        assert all(p.get("description") for p in props.values()), f"{name} has an undocumented parameter"
        assert "Behavior:" in tool["description"] and "Returns" in tool["description"]
        assert tool["annotations"]["destructiveHint"] is False
    assert tools["get_incident"]["annotations"]["readOnlyHint"] is True
    assert tools["patrol_files"]["annotations"]["readOnlyHint"] is False  # it writes report files
    assert tools["patrol_duckdb"]["outputSchema"]["properties"]["needs_a_person"]
