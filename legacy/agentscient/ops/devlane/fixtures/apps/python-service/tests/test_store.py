from service.store import checked_record_path, read_records, record_path, write_records


def test_checked_path_stays_beneath_root(tmp_path):
    assert checked_record_path(tmp_path, "row.jsonl") == tmp_path / "row.jsonl"
    assert record_path(tmp_path, "row.jsonl") == tmp_path / "row.jsonl"


def test_records_round_trip(tmp_path):
    path = tmp_path / "records.jsonl"
    write_records(path, [{"count": 2}])
    assert read_records(path) == [{"count": 2}]
