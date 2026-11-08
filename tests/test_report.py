"""Report statuses, exit codes and serialisation."""
import json

import numpy as np
import pytest

from agentcad.report import Check, Report, Status, json_value


def check(status: Status, id: str = "c", **kwargs) -> Check:
    return Check(id, kwargs.pop("part", "p"), status, kwargs.pop("summary", "s"), **kwargs)


def test_status_and_exit_code_matrix():
    assert (Report("t").status(), Report("t").exit_code()) == (Status.PASS, 0)
    assert Report("t", checks=[check(Status.PASS), check(Status.SKIP)]).exit_code() == 0
    warn = Report("t", checks=[check(Status.PASS), check(Status.WARN)])
    assert (warn.status(), warn.exit_code()) == (Status.WARN, 0)
    fail = Report("t", checks=[check(Status.WARN), check(Status.FAIL)])
    assert (fail.status(), fail.exit_code()) == (Status.FAIL, 1)
    error = Report("t", checks=[check(Status.PASS), check(Status.ERROR)])
    assert (error.status(), error.exit_code()) == (Status.FAIL, 3)
    assert Report("t", checks=[check(Status.ERROR), check(Status.FAIL)]).exit_code() == 1


def test_json_schema_rounding_and_numpy():
    report = Report("check", "proj", [check(Status.PASS, measured={"v": np.float64(1.23456789), "a": np.array([1.00004, 2]),
                                                                    "n": np.int64(3)}, where=[{"z_mm": (0.123456, 1)}])])
    data = json.loads(report.to_json())
    assert data["schema"] == "agentcad.report/1" and data["tool"] == "check" and data["project"] == "proj"
    assert data["status"] == "PASS" and data["exit_code"] == 0 and data["counts"]["pass"] == 1
    entry = data["checks"][0]
    assert entry["measured"] == {"v": 1.2346, "a": [1.0, 2], "n": 3}
    assert entry["where"] == [{"z_mm": [0.1235, 1]}]
    assert set(entry) == {"id", "part", "status", "summary", "measured", "limit", "where"}


def test_non_finite_numbers_become_null():
    assert json_value({"a": float("nan"), "b": float("inf")}) == {"a": None, "b": None}
    json.loads(Report("t", checks=[check(Status.PASS, measured={"x": float("nan")})]).to_json())


def test_table_has_one_line_per_check_and_a_total():
    report = Report("t", checks=[check(Status.PASS, "a", summary="ok"), check(Status.FAIL, "bb", part="x+y", summary="bad"),
                                 check(Status.ERROR, "e", part=None, summary="boom")])
    lines = report.to_table().splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("PASS") and "bad" in lines[1] and "x+y" in lines[1] and " - " in lines[2]
    assert lines[-1] == "1 pass, 0 warn, 1 fail, 1 error"


# -- brief view -------------------------------------------------------------------------------------------------

def mixed_report() -> Report:
    return Report("check", "proj", [
        check(Status.PASS, "valid", part="a"), check(Status.PASS, "valid", part="b"),
        check(Status.PASS, "wide", part=None),
        check(Status.FAIL, "bed_fit", part="a", summary="too big", measured={"size_mm": [1, 2, 3]}, hint="rotate it"),
        check(Status.WARN, "single_solid", part="b", summary="two"),
        check(Status.SKIP, "min_wall", part=None, summary="off"),
    ], {"elapsed_s": 1.5, "build": "/x/build", "printer": "generic-220", "cadquery": "2.8"})


def test_brief_json_keeps_only_what_needs_attention():
    text = mixed_report().to_json(view="brief")
    data = json.loads(text)
    assert ": " not in text and ", " not in text  # compact separators
    assert data["schema"] == "agentcad.report/1" and data["view"] == "brief" and data["tool"] == "check"
    assert data["status"] == "FAIL" and data["exit_code"] == 1 and data["counts"]["pass"] == 3
    assert [c["id"] for c in data["checks"]] == ["bed_fit", "single_solid", "min_wall"]
    assert data["checks"][0]["hint"] == "rotate it" and data["checks"][0]["measured"] == {"size_mm": [1, 2, 3]}
    # Empty measured, limit and where are dropped; a check with no hint carries no hint key.
    assert set(data["checks"][1]) == {"id", "part", "status", "summary"}
    # Built-in passes fold into ids; project requirement checks stay quotable as compact entries.
    assert data["passed"] == {"valid": ["a", "b"]}
    assert data["passed_checks"] == [{"id": "wide", "part": None, "summary": "s"}]
    assert data["meta"] == {"elapsed_s": 1.5, "build": "/x/build"}


def test_full_view_is_unchanged_and_has_no_new_keys():
    report = mixed_report()
    data = json.loads(report.to_json())
    assert report.to_json() == report.to_json(view="full")
    assert "view" not in data and "passed" not in data
    assert set(data) == {"schema", "tool", "project", "status", "exit_code", "counts", "checks", "meta"}
    assert set(data["checks"][0]) == {"id", "part", "status", "summary", "measured", "limit", "where"}
    assert "hint" in data["checks"][3] and data["meta"]["printer"] == "generic-220"


def test_unknown_view_is_rejected():
    with pytest.raises(ValueError, match="brief"):
        Report("t").to_json(view="tiny")


def test_brief_table_lists_attention_rows_hints_then_passed_and_totals():
    lines = mixed_report().to_brief_table().splitlines()
    assert lines[0].startswith("FAIL") and "bed_fit" in lines[0]
    assert lines[1] == "  hint: rotate it"
    assert lines[2].startswith("WARN") and lines[3].startswith("SKIP")
    assert lines[4] == "3 passed: valid x2, wide x1"
    assert lines[5] == "3 pass, 1 warn, 1 fail, 1 skip"
    assert len(lines) == 6


def test_brief_table_all_pass_is_two_lines():
    report = Report("t", checks=[check(Status.PASS, "valid"), check(Status.PASS, "valid", part="q")])
    assert report.to_brief_table().splitlines() == ["2 passed: valid x2", "2 pass, 0 warn, 0 fail"]
    assert Report("t").to_brief_table() == "0 pass, 0 warn, 0 fail"


# -- brief view on real projects ---------------------------------------------------------------------------------

def test_brief_json_budgets_on_broken_and_bracket(cli, copy_example):
    broken, bracket = copy_example("broken"), copy_example("bracket")
    code, full, _ = cli("check", broken, "--json")
    assert code == 1
    code, brief, _ = cli("check", broken, "--json", "--brief")
    assert code == 1 and len(brief.encode()) <= 0.25 * len(full.encode())
    data = json.loads(brief)
    assert data["view"] == "brief" and {c["id"] for c in data["checks"]} >= {"bed_fit", "overhang", "interference"}
    assert all(c["status"] != "PASS" for c in data["checks"])
    code, ok, _ = cli("check", bracket, "--json", "--brief")
    assert code == 0 and len(ok.encode()) < 1000
    assert json.loads(ok)["checks"] == [] and "valid" in json.loads(ok)["passed"]


def test_brief_table_through_the_cli(cli, copy_example):
    code, out, _ = cli("check", copy_example("broken"), "--brief")
    assert code == 1
    assert "  hint: " in out and out.splitlines()[-2].split(":")[0].endswith("passed")
    assert not [line for line in out.splitlines() if line.startswith("PASS")]
    code, out, _ = cli("check", copy_example("bracket"), "--brief")
    assert code == 0 and len(out.splitlines()) == 2 and " passed: " in out
