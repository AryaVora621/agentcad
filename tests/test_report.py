"""Report statuses, exit codes and serialisation."""
import json

import numpy as np

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
