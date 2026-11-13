"""One calibration service runs any registered fault inventory with one verdict policy.

The service is judged with scripted trials; the loader with throwaway registry directories; the real
registry against the source tree it mutates; and the migrated native, work and state inventories by real
runs whose verdicts must equal those of the entry points they replace.
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
    MARKER,
    OTHER,
    OTHER_SELECTOR,
    OUTCOMES,
    ROOT,
    TARGET,
    TARGET_SELECTOR,
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
NATIVE_CASES_SHA256 = "25013556c9d11fc3c59947d641a96521b95686e767b4154970175df058a89a44"
"""sha256 of the canonical JSON (sorted keys, compact) of that file's case list, with the three lowercase
diagnostics renamed to UPPER_SNAKE under the calibrated-check standard and nothing else changed."""


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
    "contract-without-function": [raw_case(contract="tests.hexagonal.contracts.probe")],
    "contract-two-functions": [raw_case(contract="tests.hexagonal.contracts.probe:check:other")],
    "contract-bad-module": [raw_case(contract="1probe:check")],
    "contract-empty": [raw_case(contract="")],
    "contract-not-text": [raw_case(contract=["probe", "check"])],
    "contract-empty-module-part": [raw_case(contract="tests..probe:check")],
    "contract-trailing-dot": [raw_case(contract="tests.probe.:check")],
    "contract-leading-dot": [raw_case(contract=".probe:check")],
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


# Checks declared in a contract function -------------------------------------------------------------

CONTRACT_SOURCE = (
    "def check(value):\n"
    f"    assert value == 1, {MARKER!r}\n"
    "def quiet(value):\n"
    f"    # {MARKER}\n"
    f"    return {MARKER!r}\n"
    "def nested(value):\n"
    "    def inner():\n"
    f"        assert value == 1, {MARKER!r}\n"
    "    return inner\n"
    "def scoped(value):\n"
    "    class Local:\n"
    "        def method(self):\n"
    f"            assert value == 1, {MARKER!r}\n"
    f"    check = lambda: {MARKER!r}\n"
    "    return Local, check\n"
    "class Holder:\n"
    "    def method(self, value):\n"
    f"        assert value == 1, {MARKER!r}\n"
)


def declared(contract, *, diagnostic=MARKER):
    module = service()
    return module.Inventory(name="probe", cases=(module.FaultCase(
        name="declared", mutation=module.Mutation(path="module.py", old="VALUE = 1", new="VALUE = 2"),
        selectors=(TARGET_SELECTOR, OTHER_SELECTOR), target=TARGET, diagnostic=diagnostic, unaffected=(OTHER,),
        contract=contract),))


def contract_root(tmp_path):
    root = tmp_path / "candidate"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "contract_mod.py").write_text(CONTRACT_SOURCE)
    return root


def test_a_case_may_declare_the_contract_function_holding_its_check(tmp_path):
    directory = inventory_directory(tmp_path / "inventories", inventories={"probe.json": {"schema_version": 1, "cases": [
        raw_case("declared", contract="tests.hexagonal.contracts.probe:check"), raw_case("plain")]}})
    first, second = load(directory).cases
    assert first.contract == "tests.hexagonal.contracts.probe:check"
    assert second.contract is None


def test_the_tree_check_accepts_a_declared_function_holding_the_exact_assertion(tmp_path):
    assert loader().check_inventory_tree(declared("pkg.contract_mod:check"), contract_root(tmp_path)) is None


@pytest.mark.parametrize("contract,code", [
    ("pkg.absent_mod:check", "contract_not_found"),
    ("pkg.contract_mod:absent", "contract_not_found"),
    ("pkg.contract_mod:method", "contract_not_found"),
    ("pkg.contract_mod:quiet", "diagnostic_not_in_contract"),
    ("pkg.contract_mod:nested", "diagnostic_not_in_contract"),
    ("pkg.contract_mod:scoped", "diagnostic_not_in_contract"),
])
def test_the_tree_check_refuses_a_declared_contract_it_cannot_find_or_that_lacks_the_assertion(tmp_path, contract,
                                                                                                 code):
    files = loader()
    with pytest.raises(files.InventoryError) as raised:
        files.check_inventory_tree(declared(contract), contract_root(tmp_path))
    assert raised.value.code == code, (contract, raised.value.code)


def test_the_tree_check_needs_the_exact_identifier_as_the_assertion_message(tmp_path):
    files = loader()
    with pytest.raises(files.InventoryError) as raised:
        files.check_inventory_tree(declared("pkg.contract_mod:check", diagnostic="PLANTED_FAULT"), contract_root(tmp_path))
    assert raised.value.code == "diagnostic_not_in_contract"


def test_the_tree_check_leaves_cases_without_a_contract_to_the_standard_test(tmp_path):
    module = service()
    inventory = module.Inventory(name="probe", cases=(case("plain"),))
    assert loader().check_inventory_tree(inventory, tmp_path) is None


def _contract_repository(tmp_path):
    repository = git_repository(tmp_path / "repo")
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repository / "contracts_probe.py").write_text(f"def check(value):\n    assert value == 1, {MARKER!r}\n",
                                                   encoding="utf-8")
    (repository / "test_probe.py").write_text(
        "from module import VALUE\nfrom contracts_probe import check\n"
        "def test_target():\n    check(VALUE)\n"
        "def test_other():\n    assert True\n", encoding="utf-8")
    return repository


def _contract_cases(**contracts):
    selectors = ["test_probe.py::test_target", "test_probe.py::test_other"]
    cases = []
    for name, (contract, expect) in contracts.items():
        value = {"name": name, "mutation": {"path": "module.py", "old": "VALUE = 1", "new": "VALUE = 2"},
                 "selectors": selectors, "target": "test_probe::test_target", "diagnostic": MARKER,
                 "unaffected": ["test_probe::test_other"], "expect": expect}
        if contract:
            value["contract"] = contract
        cases.append(value)
    return cases


def _probe_directory(tmp_path, cases):
    return inventory_directory(tmp_path / "inventories", registry={"schema_version": 1, "inventories": {
        "probe": {"file": "probe.json", "stages": ["manual"]}}},
        inventories={"probe.json": {"schema_version": 1, "cases": cases}})


def test_a_check_in_its_declared_contract_function_is_detected_and_an_undeclared_one_is_not(tmp_path):
    """A real run: the same failing contract assertion is credited only when the case declares that function."""
    repository = _contract_repository(tmp_path)
    directory = _probe_directory(tmp_path, _contract_cases(declared=("contracts_probe:check", "detected"),
                                                           undeclared=(None, "unrelated_failure")))
    out = tmp_path / "out"
    result = cli("--candidate", str(repository), "--out", str(out), "--registry-dir", str(directory),
                 "--stage", "manual")
    assert result.returncode == 0, (result.returncode, result.stdout[-800:], result.stderr[-800:])
    report = json.loads((out / "suite.json").read_text())["inventories"]["probe"]
    assert [(row["name"], row["classification"]) for row in report["results"]] == [
        ("declared", "detected"), ("undeclared", "unrelated_failure")], report["results"]


def test_an_unresolvable_declared_contract_exits_2_before_any_case_runs(tmp_path):
    repository = _contract_repository(tmp_path)
    directory = _probe_directory(tmp_path, _contract_cases(declared=("contracts_probe:absent", "detected")))
    out = tmp_path / "out"
    result = cli("--candidate", str(repository), "--out", str(out), "--registry-dir", str(directory),
                 "--stage", "manual")
    assert result.returncode == 2, (result.returncode, result.stdout[-500:], result.stderr[-500:])
    assert "contract_not_found" in result.stdout + result.stderr
    assert not out.exists() or not any(path.is_dir() for path in out.iterdir()), "no case ran"


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


def test_the_native_inventory_is_todays_faults_json_with_standard_markers():
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


def test_every_registered_case_carries_its_diagnostic_text_where_it_asserts():
    """In the target test, or in the contract function the case declares."""
    _, inventories = _real()
    for name, inventory in inventories.items():
        for fault in inventory.cases:
            assert fault.target in {module_form(selector) for selector in fault.selectors}, (name, fault.name)
            site = fault.contract.replace(":", "::", 1) if fault.contract else fault.target
            assert fault.diagnostic in _test_source(site), (name, fault.name, site, fault.diagnostic)


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


# The state and work inventories ---------------------------------------------------------------------

STATE = ("guard-bypass", "expected-version-bypass", "history-omission", "target-validation-bypass",
         "application-engine-bypass", "unrelated-error-control")
"""The five FAULTS of scripts/verification/calibrate_state.py at f165bb7, then its UNRELATED_ERROR control."""
STATE_CASES_SHA256 = "0421ce0408fb7cb1611b7bf61b8f3f897a850745b1c5b2b1f7c3a806eb94e59d"
"""sha256 of the canonical JSON (sorted keys, compact) of those six cases as dicts, the control carrying
``"expect": "unrelated_failure"`` and no other case an ``expect`` key."""

WORK_IDS = ("b1-cancelled-contribution-erases-owner-outcome", "b2-wrong-observed-candidate",
            "b2-caller-origin-accepted-as-measurement", "b2-uncertain-measurement-hidden-by-later-pass",
            "b2-stale-assessment-configuration")
"""The CASES ids of tests/hexagonal/calibrate_work_acceptance.py at f165bb7, lowercased for the schema."""
WORK = tuple(f"{case_id}-{store}" for case_id in WORK_IDS for store in ("memory", "sqlite"))
WORK_CONTROL = "test_measurement_replay_is_bound_to_request_and_does_not_reexecute"
WORK_CASES_SHA256 = "d2ac7d02284b449211f03928353a4c216e166d6893b0ad50f7ae4611d6590f63"
"""sha256 of the canonical JSON of the ten work cases: per old case and per store (memory, then sqlite),
mutation ``xorchestrate/acceptance.py`` before/after verbatim, selectors the target node then the control
node on that store, the old assertion as diagnostic, the control node as the only unaffected test, and no
``expect`` key. The two value-parametrized tests take ``candidate`` and ``running``."""


def _canonical_sha256(name):
    import hashlib
    cases = json.loads((INVENTORIES / f"{name}.json").read_text())["cases"]
    return hashlib.sha256(json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_the_real_registry_holds_native_work_and_state_at_ci_in_that_order():
    registry, inventories = _real()
    assert list(registry.items())[:3] == [("native", ("ci",)), ("work", ("ci",)), ("state", ("ci",))], registry
    assert tuple(case.name for case in inventories["state"].cases) == STATE
    assert tuple(case.name for case in inventories["work"].cases) == WORK


def test_the_state_inventory_is_calibrate_states_cases_verbatim():
    _, inventories = _real()
    assert [case.expect for case in inventories["state"].cases] == ["detected"] * 5 + ["unrelated_failure"]
    assert _canonical_sha256("state") == STATE_CASES_SHA256


def test_the_work_inventory_is_the_old_work_cases_split_by_store():
    _, inventories = _real()
    for fault in inventories["work"].cases:
        store = fault.name.rsplit("-", 1)[1]
        node = fault.target.split("::", 1)[1]
        assert node.split("[", 1)[1].startswith(store), (fault.name, fault.target)
        assert fault.unaffected == (f"tests.hexagonal.test_work_acceptance::{WORK_CONTROL}[{store}]",), fault.name
        assert {module_form(selector) for selector in fault.selectors} == {fault.target, *fault.unaffected}
        assert fault.mutation.path == "xorchestrate/acceptance.py" and fault.expect == "detected"
    assert _canonical_sha256("work") == WORK_CASES_SHA256


_RECOVERY_TESTS = "tests.hexagonal.test_recovery"
_RECOVERY_E2E = "tests.hexagonal.test_recovery_end_to_end"
_RECOVERY_CONTRACT = "tests.hexagonal.contracts.recovery"
_SERVER, _LIFECYCLE = "xorchestrate/server.py", "xorchestrate/session_lifecycle.py"
_MANUAL = "manual_policy_reconnects_nothing_and_reconciles_what_cannot_reconnect"
_DEFAULT = "default_policy_recovers_from_durable_state_alone"


def _scenario(name):
    return f"{_RECOVERY_TESTS}::test_recovery_meets_the_contract[{name}]"


RECOVERY = (
    # name, mutated file, target, diagnostic, declared contract function, unaffected neighbour
    ("recovery-policy-ignored", _SERVER, _scenario(_MANUAL), "RECOVERY_MANUAL_POLICY_RECONNECTS_NOTHING",
     f"{_RECOVERY_CONTRACT}:{_MANUAL}", _scenario(_DEFAULT)),
    ("manual-leaves-dead-sessions-active", _SERVER, _scenario(_MANUAL), "RECOVERY_MANUAL_RECONCILES_DEAD_SESSIONS",
     f"{_RECOVERY_CONTRACT}:{_MANUAL}", _scenario("pending_effects_are_never_replayed")),
    ("disconnect-reason-not-recorded", _LIFECYCLE, _scenario(_DEFAULT), "RECOVERY_DISCONNECT_REASON_RECORDED",
     f"{_RECOVERY_CONTRACT}:{_DEFAULT}", _scenario("pending_effects_are_never_replayed")),
    ("one-failure-stops-the-rest", _LIFECYCLE, _scenario("one_failure_stops_neither_the_others_nor_the_server"),
     "RECOVERY_ONE_FAILURE_STOPS_NO_OTHER", f"{_RECOVERY_CONTRACT}:one_failure_stops_neither_the_others_nor_the_server",
     _scenario("pending_effects_are_never_replayed")),
    ("recover-ignores-caller-authority", _LIFECYCLE, _scenario("explicit_recover_acts_only_where_its_caller_is_authorised"),
     "RECOVERY_RECOVER_RESPECTS_CALLER_AUTHORITY",
     f"{_RECOVERY_CONTRACT}:explicit_recover_acts_only_where_its_caller_is_authorised",
     _scenario("the_automatic_run_acts_as_the_runtime")),
    ("up-never-starts-recovery", "xorchestrate/cli.py",
     f"{_RECOVERY_E2E}::test_after_total_loss_the_default_policy_brings_the_session_back_without_a_call[detached-process]",
     "RECOVERY_STARTS_ON_UP", None,
     f"{_RECOVERY_E2E}::test_recovery_within_is_true_once_a_report_exists_and_nothing_runs"),
    ("running-terminal-reopened", "xorchestrate/adapters/hosted.py",
     f"{_RECOVERY_E2E}::test_a_server_only_crash_adopts_the_running_terminal_and_keeps_its_bridge_host[detached-process]",
     "RECOVERY_ADOPTS_RUNNING_TERMINAL", None,
     f"{_RECOVERY_E2E}::test_after_total_loss_the_default_policy_brings_the_session_back_without_a_call[detached-process]"),
    ("recover-ignores-work-binding", _LIFECYCLE,
     f"{_RECOVERY_TESTS}::test_an_explicit_recover_weighs_the_sessions_work_binding_for_the_caller",
     "RECOVERY_RECOVER_WEIGHS_WORK_BINDING", None, _scenario("explicit_recover_acts_only_where_its_caller_is_authorised")),
    ("shutdown-does-not-wait-for-recovery", _SERVER,
     f"{_RECOVERY_TESTS}::test_a_server_interrupted_during_recovery_waits_for_the_session_in_hand_before_it_returns",
     "RECOVERY_SERVE_LOOP_WAITS_FOR_RECOVERY", None,
     f"{_RECOVERY_TESTS}::test_recovery_running_belongs_to_the_live_runtime_not_to_durable_state"),
    ("recovery-takes-sessions-while-stopping", _LIFECYCLE,
     f"{_RECOVERY_TESTS}::test_a_server_stopping_during_recovery_waits_for_the_session_in_hand_and_takes_no_more",
     "RECOVERY_STOPPING_TAKES_NO_MORE_SESSIONS", None,
     f"{_RECOVERY_TESTS}::test_recovery_running_belongs_to_the_live_runtime_not_to_durable_state"),
    ("running-outlives-recovery", _LIFECYCLE,
     f"{_RECOVERY_TESTS}::test_recovery_running_belongs_to_the_live_runtime_not_to_durable_state",
     "RECOVERY_RUNNING_BELONGS_TO_LIVE_RUNTIME", None,
     f"{_RECOVERY_TESTS}::test_an_explicit_recover_weighs_the_sessions_work_binding_for_the_caller"),
    ("doctor-opens-unapproved-store", "xorchestrate/instance/doctor.py",
     f"{_RECOVERY_TESTS}::test_doctor_does_not_open_a_built_in_store_of_unapproved_configuration",
     "RECOVERY_DOCTOR_SKIPS_UNAPPROVED_STORE", None,
     f"{_RECOVERY_TESTS}::test_doctor_never_imports_or_constructs_a_store_factory_for_its_recovery_check[approved]"),
    ("doctor-constructs-store-factory", "xorchestrate/instance/doctor.py",
     f"{_RECOVERY_TESTS}::test_doctor_never_imports_or_constructs_a_store_factory_for_its_recovery_check[approved]",
     "RECOVERY_DOCTOR_NEVER_IMPORTS_STORE_FACTORY", None,
     f"{_RECOVERY_TESTS}::test_doctor_never_imports_or_constructs_a_store_factory_for_its_recovery_check[unapproved]"),
)
"""The recovery area's planted faults (old faults 23-25 and 27-36; 26 dropped as an equivalent mutant)."""
RECOVERY_MUTATIONS_SHA256 = "fd440a44eeb1a818a16ccb81f4fe7888dea9fe5459abc913c6999f2f41b92669"
"""sha256 of the canonical JSON of the cases' mutation objects in order: each fault's file, anchor and replacement
exactly as the old recovery fault script planted them."""


def test_the_registry_holds_the_recovery_inventory_at_ci():
    registry, _ = _real()
    assert list(registry)[-1] == "recovery" and registry["recovery"] == ("ci",), registry


def test_the_recovery_inventory_is_the_old_faults_with_standard_checks():
    import hashlib
    _, inventories = _real()
    found = tuple((case.name, case.mutation.path, case.target, case.diagnostic, case.contract, case.unaffected)
                  for case in inventories["recovery"].cases)
    assert found == tuple((name, path, target, diagnostic, contract, (unaffected,))
                          for name, path, target, diagnostic, contract, unaffected in RECOVERY), found
    for fault in inventories["recovery"].cases:
        assert {module_form(selector) for selector in fault.selectors} == {fault.target, *fault.unaffected}, fault.name
        assert fault.expect == "detected", fault.name
    mutations = json.loads((INVENTORIES / "recovery.json").read_text())["cases"]
    canonical = json.dumps([item["mutation"] for item in mutations], sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == RECOVERY_MUTATIONS_SHA256


@pytest.mark.parametrize("name,expected", ids=["state", "work", "recovery"], argvalues=[
    ("state", tuple((name, "detected") for name in STATE[:-1]) + (("unrelated-error-control", "unrelated_failure"),)),
    ("work", tuple((name, "detected") for name in WORK)),
    ("recovery", tuple((row[0], "detected") for row in RECOVERY)),
])
def test_the_migrated_inventory_gives_the_old_verdicts_on_this_candidate(tmp_path, name, expected):
    """Parity: a real run through the service classifies every case as the old entry point did."""
    trials = __import__("scripts.verification.__main__", fromlist=["run_trial"])
    from scripts.testkit.adapters.evidence import candidate_identity
    _, inventories = _real()
    candidate = candidate_identity(ROOT)["content_sha256"]
    report = service().calibrate_inventory(inventories[name], trial=trials.run_trial(ROOT, tmp_path / "out"),
                                           sink=Recorder(), candidate_sha256=candidate)
    assert tuple((row["name"], row["classification"]) for row in report["results"]) == expected, report["results"]
    assert report["same_candidate"] is True and report["passed"] is True


OLD_ENTRY_POINTS = ("scripts/verification/calibrate_state.py", "tests/hexagonal/calibrate_work_acceptance.py")


def test_the_old_state_and_work_entry_points_are_gone_and_unnamed():
    import importlib.util
    for path in OLD_ENTRY_POINTS:
        assert not (ROOT / path).exists(), path
        assert importlib.util.find_spec(path[:-3].replace("/", ".")) is None, path
    names = [path.rsplit("/", 1)[1][:-3] for path in OLD_ENTRY_POINTS]
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    here = "tests/hexagonal/test_fault_calibration.py"
    naming = sorted(
        path for path in tracked.decode().split("\0")
        if path and path != here and not path.startswith("docs/archive/") and (ROOT / path).is_file()
        and any(name in (ROOT / path).read_text(encoding="utf-8", errors="replace") for name in names))
    assert naming == [], "old entry points are still named in: " + ", ".join(naming)


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


def test_composition_builds_one_calibration_check_per_ci_registered_inventory(tmp_path):
    from scripts.testkit import composition
    files = loader()
    registry = files.load_registry(INVENTORIES)
    checks = [item for item in composition.plan(directory=tmp_path, suite="calibration", mode="protocol-fixture")
              if item.name.endswith("-calibration")]
    ci = files.select(registry, stage="ci")
    assert [check.name for check in checks] == [f"{name}-calibration" for name in ci]
    assert [check.name for check in checks] == ["native-calibration", "work-calibration", "state-calibration",
                                                "recovery-calibration"]
    entries = json.loads((INVENTORIES / "registry.json").read_text())["inventories"]
    for name, check in zip(ci, checks):
        assert check.argv == (sys.executable, "-m", "scripts.verification.calibrate_suite", "--candidate",
                              str(composition.ROOT), "--out", str(tmp_path / f"{name}-calibration"),
                              "--inventory", name), check.argv
        assert set(check.required) == {"scripts/verification/calibrate_suite.py",
                                       "scripts/verification/inventories/" + entries[name]["file"]}, check.required


def test_composition_leaves_out_inventories_not_registered_at_ci(tmp_path):
    """Every real inventory is at ci, so only a registry with other stages shows the stage filter."""
    from scripts.testkit import composition
    root = tmp_path / "candidate"
    inventory_directory(root / "scripts/verification/inventories",
                        registry={"schema_version": 1, "inventories": {
                            "early": {"file": "early.json", "stages": ["ci", "manual"]},
                            "later": {"file": "later.json", "stages": ["pre-draft", "manual"]}}},
                        inventories={"early.json": {"schema_version": 1, "cases": []},
                                     "later.json": {"schema_version": 1, "cases": []}})
    checks = [item.name for item in composition.plan(directory=tmp_path / "out", suite="calibration",
                                                     mode="protocol-fixture", root=root)]
    assert checks == ["early-calibration"], checks


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


# Hardening: the edges of the loader, the service and the command line --------------------------------

PROBE_TESTS = (
    "from module import VALUE, GROUPED\n"
    "def test_target():\n    assert VALUE == 1, 'PLANTED_FAULT_MARKER'\n"
    "def test_other():\n    assert True\n"
    "class TestGroup:\n"
    "    def test_grouped(self):\n        assert GROUPED == 1, 'GROUPED_FAULT_MARKER'\n")


def probe_repository(root, *, drift=None):
    """A throwaway git candidate; with ``drift``, its tests write that file into the candidate itself."""
    repository = git_repository(root)
    (repository / "module.py").write_text("VALUE = 1\nGROUPED = 1\n", encoding="utf-8")
    tests = PROBE_TESTS
    if drift is not None:
        tests += f"def test_drift():\n    open({str(repository / drift)!r}, 'w').write('changed')\n"
    (repository / "test_probe.py").write_text(tests, encoding="utf-8")
    return repository


def probe_cases():
    function = ["test_probe.py::test_target", "test_probe.py::test_other"]
    grouped = ["test_probe.py::TestGroup::test_grouped", "test_probe.py::test_other"]
    return [{"name": "caught", "mutation": {"path": "module.py", "old": "VALUE = 1", "new": "VALUE = 2"},
             "selectors": function, "target": "test_probe::test_target", "diagnostic": "PLANTED_FAULT_MARKER",
             "unaffected": ["test_probe::test_other"]},
            {"name": "grouped", "mutation": {"path": "module.py", "old": "GROUPED = 1", "new": "GROUPED = 2"},
             "selectors": grouped, "target": "test_probe.TestGroup::test_grouped",
             "diagnostic": "GROUPED_FAULT_MARKER", "unaffected": ["test_probe::test_other"]}]


def probe_registry(root, cases, stages=("manual",)):
    return inventory_directory(root, registry={"schema_version": 1, "inventories": {
        "probe": {"file": "probe.json", "stages": list(stages)}}},
        inventories={"probe.json": {"schema_version": 1, "cases": cases}})


@pytest.fixture(scope="module")
def probe_run(tmp_path_factory):
    """One real CLI run of a function case and a class case, both detected, kept for several checks."""
    base = tmp_path_factory.mktemp("probe-run")
    repository = probe_repository(base / "repo")
    directory = probe_registry(base / "inventories", probe_cases())
    out = base / "out"
    result = cli("--candidate", str(repository), "--out", str(out), "--registry-dir", str(directory),
                 "--inventory", "probe")
    return result, out


def test_a_test_class_selector_is_judged_by_its_junit_identity(probe_run):
    result, out = probe_run
    suite = json.loads((out / "suite.json").read_text())
    rows = suite["inventories"]["probe"]["results"]
    assert [(row["name"], row["classification"]) for row in rows] == [("caught", "detected"), ("grouped", "detected")], rows
    assert result.returncode == 0 and suite["passed"] is True, (result.returncode, result.stderr[-500:])


def test_a_class_selector_target_must_name_its_class(tmp_path):
    grouped = probe_cases()[1]
    directory = probe_registry(tmp_path / "inventories", [grouped])
    assert load(directory).cases[0].target == "test_probe.TestGroup::test_grouped"
    directory = probe_registry(tmp_path / "other", [{**grouped, "target": "test_probe::test_grouped"}])
    assert error_code(directory) == "target_not_selected"


def test_each_case_keeps_its_raw_trial_record_beside_its_evidence(probe_run):
    _, out = probe_run
    suite = json.loads((out / "suite.json").read_text())
    for index, name in enumerate(("caught", "grouped"), 1):
        path = out / "probe" / f"{index:02d}-{name}" / "trial.json"
        assert path.is_file(), path
        record = json.loads(path.read_text())
        assert {"control_cases", "mutant_cases", "control_exit", "mutant_exit", "mutation_applied",
                "candidate_sha256", "commands"} <= set(record), sorted(record)
        assert record["candidate_sha256"] == suite["inventories"]["probe"]["candidate_sha256"]


def test_the_progress_file_holds_every_event_in_order(probe_run):
    _, out = probe_run
    suite = json.loads((out / "suite.json").read_text())
    report = suite["inventories"]["probe"]
    events = json.loads((out / "probe.progress.json").read_text())
    assert events == [*({"event": "case", **row} for row in report["results"]), {"event": "report", **report}]


def test_a_candidate_that_changes_during_the_run_fails_the_suite(tmp_path):
    """Each case is detected and its report passes, but the candidate changed under the run."""
    repository = probe_repository(tmp_path / "repo", drift="drift.txt")
    cases = [{**probe_cases()[0], "selectors": [*probe_cases()[0]["selectors"], "test_probe.py::test_drift"]}]
    directory = probe_registry(tmp_path / "inventories", cases)
    out = tmp_path / "out"
    result = cli("--candidate", str(repository), "--out", str(out), "--registry-dir", str(directory),
                 "--inventory", "probe")
    suite = json.loads((out / "suite.json").read_text())
    assert suite["inventories"]["probe"]["passed"] is True, suite["inventories"]["probe"]["results"]
    assert (repository / "drift.txt").exists(), "the run changed the candidate"
    assert suite["candidate_integrity"]["passed"] is False
    assert suite["passed"] is False and result.returncode == 1, (result.returncode, result.stdout[-300:])


def test_a_selection_with_no_inventory_exits_2_before_any_run(tmp_path):
    repository = probe_repository(tmp_path / "repo")
    directory = probe_registry(tmp_path / "inventories", probe_cases(), stages=("ci",))
    out = tmp_path / "out"
    result = cli("--candidate", str(repository), "--out", str(out), "--registry-dir", str(directory),
                 "--stage", "manual")
    assert result.returncode == 2, (result.returncode, result.stdout[-300:], result.stderr[-300:])
    assert "no_inventory_selected" in result.stderr
    assert not out.exists(), "nothing was written"


def test_an_output_directory_inside_the_candidate_is_refused(tmp_path):
    repository = probe_repository(tmp_path / "repo")
    directory = probe_registry(tmp_path / "inventories", probe_cases())
    out = repository / "calibration-out"
    result = cli("--candidate", str(repository), "--out", str(out), "--registry-dir", str(directory),
                 "--inventory", "probe")
    assert result.returncode == 2, (result.returncode, result.stdout[-300:], result.stderr[-300:])
    assert "--out" in result.stderr, result.stderr[-300:]
    assert not out.exists(), "the candidate was not written to"


def test_a_target_listed_as_unaffected_is_refused(tmp_path):
    directory = inventory_directory(tmp_path / "inventories", inventories={"probe.json": {
        "schema_version": 1, "cases": [raw_case(unaffected=[TARGET, OTHER])]}})
    assert error_code(directory) == "invalid_inventory"


@pytest.mark.parametrize("selector", ["--co::test_target", "-k::test_target"])
def test_a_selector_that_reads_as_a_pytest_option_is_refused(tmp_path, selector):
    case = raw_case(selectors=[selector, "tests/hexagonal/test_probe.py::test_target",
                               "tests/hexagonal/test_probe.py::test_other"])
    directory = inventory_directory(tmp_path / "inventories",
                                    inventories={"probe.json": {"schema_version": 1, "cases": [case]}})
    assert error_code(directory) == "invalid_inventory", selector


def test_select_names_each_inventory_once():
    assert loader().select({"a": ("ci",), "b": ("ci",)}, names=("a", "b", "a")) == ("a", "b")


@pytest.mark.parametrize("code", ["Not a code " + SENTINEL, "x" * 65, "", "1starts_with_digit", 7, None])
def test_only_a_typed_code_becomes_the_reason(code):
    report, events, _ = run([case("raises")], {"raises": TypedTrialError("failed " + SENTINEL, code)})
    (row,) = report["results"]
    assert row["reason"] == "trial_failed", row
    assert SENTINEL not in json.dumps([report, events])


def test_a_typed_code_at_the_length_limit_is_kept():
    code = "a" + "b" * 63
    report, _, _ = run([case("raises")], {"raises": TypedTrialError("failed", code)})
    assert report["results"][0]["reason"] == code
