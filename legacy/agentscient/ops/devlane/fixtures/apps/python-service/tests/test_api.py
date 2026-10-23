from service import api

GET_LINE = '{"verb": "get", "name": "fixture"}'


def test_boundary_refuses_bad_request(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "parse_request", lambda _line: {"verb": "unknown"})
    response = api.handle("not json", tmp_path)
    assert response["ok"] is False


def test_parser_reads_a_get():
    request = api.parse_request(GET_LINE)
    assert request["verb"] == "get"


def test_missing_get_name_is_a_problem(tmp_path):
    response = api.handle('{"verb": "get"}', tmp_path)
    assert response["ok"] is False
    assert "name" in str(response)
