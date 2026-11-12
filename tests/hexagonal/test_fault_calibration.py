"""One calibration service runs any registered fault inventory with one verdict policy.

The service is judged with scripted trials; the loader with throwaway registry directories; the real
registry against the source tree it mutates; and the migrated native inventory by a real run whose
verdicts must equal today's.
"""

import ast
import json
import shutil
import subprocess
import sys

import pytest

from scripts.architecture.python import check as architecture
from scripts.verification.calibration import classify
from tests.hexagonal.support.fault_calibration import (
    CANDIDATE,
    INVENTORIES,
    OTHER,
    OUTCOMES,
    ROOT,
    TARGET,
    Recorder,
    case,
    inventory_directory,
    loader,
    module_form,
    raw_case,
    record,
    scripted,
    service,
)
from tests.hexagonal.support.verification_evidence import repository as git_repository

NATIVE = ("grok-native-observation", "requested-selection", "claude-native-hook", "external-detach",
          "crash-uncertainty", "autonomous-callback", "delivery-uncertainty")
"""The seven cases of scripts/verification/faults.json at 66390e7, in order; all were detected there."""
NATIVE_CASES_SHA256 = "dc12f9a24147b6473c508238547c188abd01a7afbc4bc5ce1768aa811d190fec"
"""sha256 of the canonical JSON (sorted keys, compact) of that file's case list."""


def run(cases, records, *, name="probe", candidate=CANDIDATE):
    module = service()
    sink = Recorder()
    trial = scripted(records)
    report = module.calibrate_inventory(module.Inventory(name=name, cases=tuple(cases)), trial=trial, sink=sink,
                                        candidate_sha256=candidate)
    return json.loads(json.dumps(report)), sink.events, trial.calls


# The service ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("verdict", sorted(OUTCOMES))
def test_each_verdict_is_calibration_classify_unchanged_and_only_detection_passes(verdict):
    report, _, _ = run([case("one")], {"one": OUTCOMES[verdict]})
    raw = OUTCOMES[verdict]
    expected = classify(raw["control_cases"], raw["mutant_cases"], TARGET, "PLANTED_FAULT_MARKER", [OTHER],
                        mutation_applied=raw["mutation_applied"], control_exit=raw["control_exit"],
                        mutant_exit=raw["mutant_exit"])["classification"]
    assert expected == verdict, "the scripted record must mean what its name says"
    (row,) = report["results"]
    assert row["classification"] == verdict
    assert row["passed"] is (verdict == "detected"), row
    assert report["passed"] is (verdict == "detected"), report


def test_a_row_carries_the_agreed_fields_only():
    report, _, _ = run([case("one")], {"one": OUTCOMES["detected"]})
    (row,) = report["results"]
    assert set(row) == {"name", "expect", "classification", "passed", "candidate_sha256"}, sorted(row)
    assert row == {"name": "one", "expect": "detected", "classification": "detected", "passed": True,
                   "candidate_sha256": CANDIDATE}


@pytest.mark.parametrize("verdict,passes", [("unrelated_failure", True), ("detected", False),
                                            ("escaped_fault", False), ("setup_error", False)])
def test_a_negative_control_passes_only_when_its_fault_is_unrelated(verdict, passes):
    report, _, _ = run([case("negative", expect="unrelated_failure")], {"negative": OUTCOMES[verdict]})
    (row,) = report["results"]
    assert row["expect"] == "unrelated_failure"
    assert row["classification"] == verdict
    assert row["passed"] is passes and report["passed"] is passes, report


def test_an_anchor_that_does_not_match_exactly_once_is_a_setup_error_from_the_real_trial(tmp_path):
    """The real trial adapter refuses an ambiguous or absent anchor before any pytest run."""
    trials = __import__("scripts.verification.__main__", fromlist=["run_trial"])
    repository = git_repository(tmp_path / "repo")
    (repository / "module.py").write_text("VALUE = 1\nOTHER = 'VALUE = 1'\n", encoding="utf-8")
    (repository / "test_probe.py").write_text("from module import VALUE\ndef test_target():\n    assert VALUE == 1\n")
    module = service()
    ambiguous = case("ambiguous")
    absent = module.FaultCase(name="absent", mutation=module.Mutation(path="module.py", old="VALUE = 3", new="X"),
                              selectors=ambiguous.selectors, target=TARGET, diagnostic="PLANTED_FAULT_MARKER",
                              unaffected=(OTHER,))
    sink = Recorder()
    report = module.calibrate_inventory(module.Inventory(name="anchors", cases=(ambiguous, absent)),
                                        trial=trials.run_trial(repository, tmp_path / "out"), sink=sink,
                                        candidate_sha256=CANDIDATE)
    report = json.loads(json.dumps(report))
    assert [(row["name"], row["classification"]) for row in report["results"]] == [
        ("ambiguous", "setup_error"), ("absent", "setup_error")], report["results"]
    assert report["passed"] is False
    assert (repository / "module.py").read_text() == "VALUE = 1\nOTHER = 'VALUE = 1'\n", "the candidate is untouched"


class TypedTrialError(RuntimeError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class UntypedSentinelFailure(RuntimeError):
    pass


SENTINEL = "sentinel-text-7f3a91"


@pytest.mark.parametrize("error,reason", [
    (TypedTrialError("candidate capture failed " + SENTINEL, "candidate_changed"), "candidate_changed"),
    (UntypedSentinelFailure("broke with " + SENTINEL), "trial_failed"),
    (ValueError("Mutation must replace exactly one known source occurrence " + SENTINEL), "trial_failed"),
])
def test_a_raising_trial_is_a_setup_error_with_a_typed_reason_and_no_message_or_class_name(error, reason):
    report, events, _ = run([case("raises"), case("after")], {"raises": error, "after": OUTCOMES["detected"]})
    first, second = report["results"]
    assert first["classification"] == "setup_error" and first["passed"] is False
    assert first.get("reason") == reason, first
    assert second["classification"] == "detected", "one raising trial does not stop the rest"
    carried = json.dumps([report, events])
    assert SENTINEL not in carried, "the error message must not be recorded"
    assert type(error).__name__ not in carried, "the error class name must not be recorded"


def test_every_case_must_run_on_the_candidate_the_report_names():
    report, _, _ = run([case("same"), case("other")],
                       {"same": OUTCOMES["detected"], "other": record(candidate="d" * 64)})
    assert all(row["passed"] for row in report["results"]), "each verdict alone is detection"
    assert [row["candidate_sha256"] for row in report["results"]] == [CANDIDATE, "d" * 64]
    assert report["same_candidate"] is False
    assert report["passed"] is False, "a case judged on another candidate cannot pass the report"


def test_a_report_on_one_candidate_with_every_case_detected_passes():
    report, _, calls = run([case("a"), case("b")], {"a": OUTCOMES["detected"], "b": OUTCOMES["detected"]},
                           name="pair")
    assert report["inventory"] == "pair"
    assert report["candidate_sha256"] == CANDIDATE
    assert report["same_candidate"] is True and report["passed"] is True
    assert [name for name, _ in calls] == ["a", "b"], "each case runs once, in inventory order"
    assert len({case_id for _, case_id in calls}) == 2, "each trial has its own case id"


def test_an_empty_inventory_is_valid_but_never_passes():
    report, events, calls = run([], {})
    assert report["results"] == [] and calls == []
    assert report["passed"] is False
    assert events == [{"event": "report", **report}]


def test_the_sink_hears_each_case_as_it_finishes_then_the_report():
    report, events, _ = run([case("a"), case("b", expect="unrelated_failure")],
                            {"a": OUTCOMES["detected"], "b": OUTCOMES["escaped_fault"]})
    assert [event["event"] for event in events] == ["case", "case", "report"]
    assert events[0] == {"event": "case", **report["results"][0]}
    assert events[1] == {"event": "case", **report["results"][1]}
    assert events[2] == {"event": "report", **report}


# Role isolation -------------------------------------------------------------------------------------

PROFILE_PATH = ROOT / "contracts/hexagonal.json"


@pytest.fixture
def subject(tmp_path):
    profile = json.loads(PROFILE_PATH.read_text())
    for name in profile["source_roots"]:
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path, profile


def test_the_new_modules_have_their_declared_roles():
    profile = json.loads(PROFILE_PATH.read_text())
    report = architecture(ROOT, profile)
    roles = {entry["module"]: entry["role"] for entry in report["files"]}
    assert roles.get("scripts.verification.fault_calibration") == "verification_calibration", roles.get(
        "scripts.verification.fault_calibration")
    assert roles.get("scripts.verification.inventory_files") == "verification_inventory", roles.get(
        "scripts.verification.inventory_files")
    assert report["status"] == "pass", report["findings"]


@pytest.mark.parametrize("dependency", ["scripts.verification.inventory_files", "scripts.verification.calibrate_suite",
                                        "scripts.testkit.composition"])
def test_the_core_service_role_cannot_reach_the_loader_cli_or_composition(subject, dependency):
    root, profile = subject
    path = root / "scripts/verification/fault_calibration.py"
    path.write_text(path.read_text() + "\nimport " + dependency + "\n")
    report = architecture(root, profile)
    assert report["status"] == "violation"
    assert any(item["rule_id"] == "dependencies.allowed" and item.get("module") == "scripts.verification.fault_calibration"
               and item.get("target") == dependency for item in report["findings"]), report["findings"]


# The loader -----------------------------------------------------------------------------------------

def load(directory, name="probe"):
    files = loader()
    files.load_registry(directory)
    return files.load_inventory(directory, name)


def error_code(directory, name="probe"):
    files = loader()
    with pytest.raises(files.InventoryError) as raised:
        load(directory, name)
    assert isinstance(raised.value, ValueError)
    return raised.value.code


def test_a_valid_inventory_loads_as_frozen_cases_without_touching_the_tree(tmp_path):
    directory = inventory_directory(tmp_path / "inventories", inventories={"probe.json": {"schema_version": 1, "cases": [
        raw_case("first", mutation={"path": "nowhere/absent.py", "old": "x", "new": "y"}),
        raw_case("second", expect="unrelated_failure")]}})
    module = service()
    inventory = load(directory)
    assert isinstance(inventory, module.Inventory) and inventory.name == "probe"
    first, second = inventory.cases
    assert isinstance(first, module.FaultCase) and isinstance(first.mutation, module.Mutation)
    assert first.mutation == module.Mutation(path="nowhere/absent.py", old="x", new="y")
    assert isinstance(first.selectors, tuple) and isinstance(first.unaffected, tuple)
    assert (first.expect, second.expect) == ("detected", "unrelated_failure")
    assert loader().load_registry(directory) == {"probe": ("ci",)}


def test_an_unknown_inventory_name_is_refused(tmp_path):
    assert error_code(inventory_directory(tmp_path / "inventories"), "absent") == "unknown_inventory"


def test_an_unregistered_inventory_file_is_refused(tmp_path):
    directory = inventory_directory(tmp_path / "inventories", extra={"forgotten.json": {"schema_version": 1, "cases": []}})
    assert error_code(directory) == "unregistered_inventory_file"


REGISTRY_FAULTS = {
    "schema-version": {"schema_version": 2, "inventories": {"probe": {"file": "probe.json", "stages": ["ci"]}}},
    "uppercase-name": {"schema_version": 1, "inventories": {"Probe": {"file": "probe.json", "stages": ["ci"]}}},
    "unknown-stage": {"schema_version": 1, "inventories": {"probe": {"file": "probe.json", "stages": ["nightly"]}}},
    "no-stages": {"schema_version": 1, "inventories": {"probe": {"file": "probe.json", "stages": []}}},
    "file-outside": {"schema_version": 1, "inventories": {"probe": {"file": "../probe.json", "stages": ["ci"]}}},
    "file-not-json": {"schema_version": 1, "inventories": {"probe": {"file": "probe.txt", "stages": ["ci"]}}},
    "file-missing": {"schema_version": 1, "inventories": {"probe": {"file": "missing.json", "stages": ["ci"]},
                                                          "other": {"file": "probe.json", "stages": ["ci"]}}},
    "extra-key": {"schema_version": 1, "inventories": {"probe": {"file": "probe.json", "stages": ["ci"],
                                                                 "owner": "x"}}},
}


@pytest.mark.parametrize("fault", sorted(REGISTRY_FAULTS))
def test_a_malformed_registry_is_refused(tmp_path, fault):
    directory = inventory_directory(tmp_path / "inventories", registry=REGISTRY_FAULTS[fault])
    files = loader()
    with pytest.raises(files.InventoryError) as raised:
        files.load_registry(directory)
    assert raised.value.code == "invalid_registry", (fault, raised.value.code)


def _without(key):
    value = raw_case()
    del value[key]
    return value


CASE_FAULTS = {
    "extra-key": [raw_case(owner="x")],
    "missing-diagnostic": [_without("diagnostic")],
    "missing-unaffected": [_without("unaffected")],
    "absolute-path": [raw_case(mutation={"path": "/etc/module.py", "old": "a", "new": "b"})],
    "parent-path": [raw_case(mutation={"path": "scripts/../../module.py", "old": "a", "new": "b"})],
    "empty-anchor": [raw_case(mutation={"path": "module.py", "old": "", "new": "b"})],
    "mutation-extra-key": [raw_case(mutation={"path": "module.py", "old": "a", "new": "b", "count": 2})],
    "no-selectors": [raw_case(selectors=[])],
    "repeated-selector": [raw_case(selectors=["tests/hexagonal/test_probe.py::test_target"] * 2, unaffected=[])],
    "unknown-expectation": [raw_case(expect="skipped")],
    "repeated-name": [raw_case("same"), raw_case("same")],
    "bad-name": [raw_case("Has Spaces")],
}


@pytest.mark.parametrize("fault", sorted(CASE_FAULTS))
def test_a_malformed_case_is_refused(tmp_path, fault):
    directory = inventory_directory(tmp_path / "inventories",
                                    inventories={"probe.json": {"schema_version": 1, "cases": CASE_FAULTS[fault]}})
    assert error_code(directory) == "invalid_inventory", fault


@pytest.mark.parametrize("change", [
    {"target": "tests.hexagonal.test_probe::test_elsewhere"},
    {"unaffected": ["tests.hexagonal.test_probe::test_elsewhere"]},
    {"target": "tests/hexagonal/test_probe.py::test_target"},
])
def test_a_target_or_unaffected_test_outside_the_selectors_is_refused(tmp_path, change):
    directory = inventory_directory(tmp_path / "inventories",
                                    inventories={"probe.json": {"schema_version": 1, "cases": [raw_case(**change)]}})
    assert error_code(directory) == "target_not_selected", change


def test_select_defaults_to_ci_and_follows_stage_or_the_names_given():
    files = loader()
    registry = {"a": ("ci",), "b": ("manual",), "c": ("pre-draft", "ci")}
    assert files.select(registry) == ("a", "c")
    assert files.select(registry, stage="pre-draft") == ("c",)
    assert files.select(registry, stage="manual") == ("b",)
    assert files.select(registry, names=("b", "a")) == ("b", "a")
    with pytest.raises(files.InventoryError) as raised:
        files.select(registry, names=("a", "zzz"))
    assert raised.value.code == "unknown_inventory"


# The real registry ----------------------------------------------------------------------------------

def _real():
    files = loader()
    registry = files.load_registry(INVENTORIES)
    return registry, {name: files.load_inventory(INVENTORIES, name) for name in registry}


def test_the_real_registry_holds_the_native_inventory_at_ci_and_faults_json_is_gone():
    registry, inventories = _real()
    assert registry["native"] == ("ci",)
    assert tuple(case.name for case in inventories["native"].cases) == NATIVE
    assert not (ROOT / "scripts/verification/faults.json").exists(), "one source of cases: the registry"


def test_the_native_inventory_is_todays_faults_json_verbatim():
    import hashlib
    cases = json.loads((INVENTORIES / "native.json").read_text())["cases"]
    canonical = json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == NATIVE_CASES_SHA256


def test_every_registered_case_plants_one_exact_edit_in_the_current_tree():
    _, inventories = _real()
    for name, inventory in inventories.items():
        for fault in inventory.cases:
            path = ROOT / fault.mutation.path
            assert path.is_file(), (name, fault.name, fault.mutation.path)
            assert path.read_text(encoding="utf-8").count(fault.mutation.old) == 1, (name, fault.name)


def _test_source(target):
    module, _, test = target.partition("::")
    function = test.split("[", 1)[0]
    path = ROOT / (module.replace(".", "/") + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function]
    assert len(nodes) == 1, (target, len(nodes))
    return ast.get_source_segment(path.read_text(encoding="utf-8"), nodes[0])


def test_every_registered_target_test_carries_its_diagnostic_text():
    _, inventories = _real()
    for name, inventory in inventories.items():
        for fault in inventory.cases:
            assert fault.target in {module_form(selector) for selector in fault.selectors}, (name, fault.name)
            assert fault.diagnostic in _test_source(fault.target), (name, fault.name, fault.diagnostic)


def test_the_migrated_native_inventory_gives_todays_verdicts_on_this_candidate(tmp_path):
    """Parity: a real run of the native inventory through the service detects every case, as faults.json did."""
    trials = __import__("scripts.verification.__main__", fromlist=["run_trial"])
    from scripts.testkit.adapters.evidence import candidate_identity
    _, inventories = _real()
    candidate = candidate_identity(ROOT)["content_sha256"]
    sink = Recorder()
    report = service().calibrate_inventory(inventories["native"], trial=trials.run_trial(ROOT, tmp_path / "out"),
                                           sink=sink, candidate_sha256=candidate)
    assert [(row["name"], row["classification"]) for row in report["results"]] == [
        (name, "detected") for name in NATIVE], report["results"]
    assert report["same_candidate"] is True and report["passed"] is True


# The command line and the composed check ------------------------------------------------------------

def cli(*arguments, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "scripts.verification.calibrate_suite", *arguments], cwd=cwd,
                          capture_output=True, text=True, timeout=120)


def test_an_unknown_inventory_exits_2_with_its_code_before_any_case_runs(tmp_path):
    out = tmp_path / "out"
    result = cli("--candidate", str(ROOT), "--out", str(out), "--inventory", "no-such-inventory")
    assert result.returncode == 2, (result.returncode, result.stdout[-500:], result.stderr[-500:])
    assert "unknown_inventory" in result.stdout + result.stderr
    assert not out.exists() or not any(path.is_dir() for path in out.iterdir()), "no case ran"


def test_an_unregistered_inventory_file_exits_2_with_its_code(tmp_path):
    directory = inventory_directory(tmp_path / "inventories", extra={"forgotten.json": {"schema_version": 1, "cases": []}})
    out = tmp_path / "out"
    result = cli("--candidate", str(ROOT), "--out", str(out), "--registry-dir", str(directory))
    assert result.returncode == 2, (result.returncode, result.stdout[-500:], result.stderr[-500:])
    assert "unregistered_inventory_file" in result.stdout + result.stderr
    assert not out.exists() or not any(path.is_dir() for path in out.iterdir()), "no case ran"


def test_inventory_and_stage_are_mutually_exclusive(tmp_path):
    result = cli("--candidate", str(ROOT), "--out", str(tmp_path / "out"), "--inventory", "native", "--stage", "ci")
    assert result.returncode == 2, (result.returncode, result.stderr[-500:])
    assert "not allowed with argument" in result.stderr, result.stderr[-500:]


def test_the_composed_native_calibration_check_runs_the_native_inventory(tmp_path):
    from scripts.testkit import composition
    (check,) = [item for item in composition.plan(directory=tmp_path, suite="calibration", mode="protocol-fixture")
                if item.name == "native-calibration"]
    assert check.argv == (sys.executable, "-m", "scripts.verification.calibrate_suite", "--candidate",
                          str(composition.ROOT), "--out", str(tmp_path / "native-calibration"),
                          "--inventory", "native"), check.argv


def test_the_suite_report_holds_each_inventory_report(tmp_path):
    """A real CLI run on a throwaway registry with one detected and one escaped case."""
    repository = git_repository(tmp_path / "repo")
    (repository / "module.py").write_text("VALUE = 1\nSPARE = 5\n", encoding="utf-8")
    (repository / "test_probe.py").write_text(
        "from module import VALUE, SPARE\n"
        "def test_target():\n    assert VALUE == 1, 'PLANTED_FAULT_MARKER'\n"
        "def test_other():\n    assert True\n", encoding="utf-8")
    selectors = ["test_probe.py::test_target", "test_probe.py::test_other"]
    cases = [{"name": "caught", "mutation": {"path": "module.py", "old": "VALUE = 1", "new": "VALUE = 2"},
              "selectors": selectors, "target": "test_probe::test_target", "diagnostic": "PLANTED_FAULT_MARKER",
              "unaffected": ["test_probe::test_other"]},
             {"name": "escapes", "mutation": {"path": "module.py", "old": "SPARE = 5", "new": "SPARE = 6"},
              "selectors": selectors, "target": "test_probe::test_target", "diagnostic": "PLANTED_FAULT_MARKER",
              "unaffected": ["test_probe::test_other"]}]
    directory = inventory_directory(tmp_path / "inventories",
                                    registry={"schema_version": 1, "inventories": {
                                        "probe": {"file": "probe.json", "stages": ["manual"]}}},
                                    inventories={"probe.json": {"schema_version": 1, "cases": cases}})
    out = tmp_path / "out"
    result = cli("--candidate", str(repository), "--out", str(out), "--registry-dir", str(directory),
                 "--stage", "manual")
    assert result.returncode == 1, (result.returncode, result.stdout[-800:], result.stderr[-800:])
    suite = json.loads((out / "suite.json").read_text())
    assert set(suite) >= {"candidate", "candidate_integrity", "inventories", "passed"}, sorted(suite)
    assert list(suite["inventories"]) == ["probe"]
    report = suite["inventories"]["probe"]
    assert [(row["name"], row["classification"]) for row in report["results"]] == [
        ("caught", "detected"), ("escapes", "escaped_fault")], report["results"]
    assert report["passed"] is False and suite["passed"] is False
    assert (out / "probe").is_dir(), "each inventory's cases are kept under its own name"
