"""Independent expectations for complete, grounded diagnostic source units."""


def changed_unit_is_complete(context, *, source, start_line):
    units = [item for item in context["sources"] if item["path"] == "pkg/work.py" and item["symbol"] == "changed"]
    assert len(units) == 1, "changed function must be selected, including late-file changes"
    unit = units[0]
    assert unit["source"] == source, "changed function must be supplied whole, with its decorator"
    assert unit["start_line"] == start_line
    assert unit["end_line"] == start_line + 3
    assert unit["truncated"] is False
    assert "UNCHANGED =" not in unit["source"]
    assert context["source_scope"]["truncated"] == []


def related_units_are_supplied(context):
    symbols = {(item["path"], item["symbol"]) for item in context["sources"]}
    assert ("pkg/ports.py", "Store") in symbols, "declared port must accompany its changed consumer"
    assert ("pkg/work.py", "invoke") in symbols, "caller of changed function must be inspectable"
    assert ("tests/test_work.py", "test_changed") in symbols, "behavioral test source must accompany consumer"


def tasks_are_grounded(tasks, context):
    assert len(tasks) == 3
    assert {task["check_id"] for task in tasks} == {"semantic_boundaries", "port_abstraction", "evidence_coverage"}
    assert len({task["task_id"] for task in tasks}) == len(tasks)
    assert context["candidates"], "classification requires a concrete candidate"
    for task in tasks:
        candidate = next(item for item in context["candidates"] if item["candidate_id"] == task["candidate_id"])
        assert task["path"] == candidate["path"] == "pkg/work.py"
        assert (task["start_line"], task["end_line"]) == (candidate["start_line"], candidate["end_line"])
        assert task["contract"] == candidate["contract"]
        assert task["question"] and task["claim"]
        assert str(candidate["start_line"]) in task["question"]
        assert "pkg/work.py" in task["question"]
