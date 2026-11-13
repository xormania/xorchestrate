"""Local native setup honors selected capture and reports real prerequisites."""


def default_capture_contract(subject):
    configuration = subject.setup_native()
    capture = configuration.get("telemetry")
    assert isinstance(capture, dict) and capture.get("adapter") == "local", \
        "LOCAL_NATIVE_SETUP_MUST_CONFIGURE_DURABLE_CAPTURE_BY_DEFAULT"
    assert capture.get("enabled", True) is True
    assert capture.get("options", {}).get("sources") is None, \
        "DEFAULT_CAPTURE_MUST_NOT_SILENTLY_FILTER_ALLOWED_SOURCES"


def explicit_disabled_capture_contract(subject):
    before, after = subject.setup_existing_disabled()
    assert after == before, "SETUP_MUST_PRESERVE_EXPLICIT_DISABLED_CAPTURE_AND_OPERATOR_SETTINGS"


def selected_capture_sqlite_contract(subject, capture):
    report, unchanged = subject.doctor_with_incompatible_sqlite(capture)
    sqlite = [item for item in report["readiness"] if item["name"] == "telemetry_sqlite"]
    if capture == "sqlite":
        assert sqlite and sqlite[0].get("required") is True and sqlite[0]["passed"] is False, \
            "SELECTED_SQLITE_CORPUS_MUST_REPORT_INCOMPATIBLE_SQLITE_AS_REQUIRED"
        assert report["status"] == "setup_error"
    elif capture == "malformed":
        assert any(item["name"] == "telemetry_configuration" and item.get("required") and not item["passed"]
                   for item in report["readiness"]), "MALFORMED_CAPTURE_SELECTION_MUST_BE_AN_EXPLICIT_FAILURE"
        assert report["status"] == "setup_error"
    else:
        assert not any(item.get("required", True) for item in sqlite), \
            "DISABLED_OR_NON_SQLITE_CAPTURE_MUST_NOT_REQUIRE_THE_CORPUS_SQLITE_RUNTIME"
        assert report["status"] == "passed"
    assert report["authentication"] == "not probed", "READINESS_MUST_NOT_CLAIM_NATIVE_AUTHENTICATED_EXECUTION"
    assert unchanged, "DOCTOR_MUST_NOT_CHANGE_OPERATOR_CONFIGURATION_OR_ALLOCATE_A_RUN"


def project_language_contract(subject, language, missing=None):
    required = {"rust": {"rustc", "cargo"}, "python": {"python3"}, "php": {"php", "composer"}}[language]
    report, looked_up, project, unchanged = subject.project_readiness(language, missing=missing)
    assert report["profile"]["project"]["path"] == project, "PROJECT_PATH_MUST_RESOLVE_FROM_PROFILE_DIRECTORY"
    assert required <= set(looked_up), "DOCTOR_MUST_CHECK_SELECTED_PROJECT_LANGUAGE_PREREQUISITES"
    unrelated = {"rustc", "cargo", "python3", "php", "composer"} - required
    assert unrelated.isdisjoint(looked_up), "DOCTOR_MUST_NOT_REQUIRE_UNSELECTED_PROJECT_LANGUAGES"
    assert not ({"unselected-claude", "unselected-grok"} & set(looked_up)), \
        "DOCTOR_MUST_NOT_REQUIRE_UNSELECTED_NATIVE_CLIENTS"
    if missing:
        assert report["status"] == "setup_error"
        assert any(missing in str(item) for item in report["readiness"] if not item["passed"]), \
            "MISSING_SELECTED_PROJECT_PREREQUISITE_MUST_HAVE_AN_EXPLICIT_FAILED_OBSERVATION"
    else:
        assert report["status"] == "passed"
    assert report["authentication"] == "not probed"
    assert unchanged


def isolated_capture_contract(subject, configured):
    original, left, right, expected, unchanged, descriptors = subject.derive_capture(configured)
    if configured in {"inline", "disabled", "custom"}:
        assert left["telemetry"] == right["telemetry"] == original["telemetry"], \
            "MANAGED_CAPTURE_MUST_PRESERVE_EXPLICIT_OPERATOR_POLICY"
    else:
        assert left.get("telemetry", {}).get("adapter") == right.get("telemetry", {}).get("adapter") == "local", \
            "NATIVE_RUN_MUST_HAVE_LOCAL_DURABLE_CAPTURE_WHEN_UNCONFIGURED"
        options = left["telemetry"]["options"], right["telemetry"]["options"]
        assert [item["runtime_root"] for item in options] == expected, \
            "NATIVE_RUN_CAPTURE_MUST_USE_ITS_OWN_RUN_DIRECTORY"
        for identity in ("dataset_id", "collector_id"):
            assert all(isinstance(item.get(identity), str) and item[identity] for item in options)
            assert options[0][identity] != options[1][identity], "FRESH_NATIVE_RUN_MUST_HAVE_FRESH_CAPTURE_IDENTITIES"
    assert unchanged, "DERIVING_NATIVE_CAPTURE_MUST_NOT_MUTATE_OPERATOR_INPUT"
    expected_status = "disabled" if configured == "disabled" else (
        "external" if configured in {"inline", "custom"} else "configured")
    assert all(item and item["status"] == expected_status for item in descriptors), \
        "DERIVED_CAPTURE_MUST_WRITE_AN_EXPLICIT_RUNTIME_SOURCE_DESCRIPTOR"
    assert all(item.get("source_completion") == "unknown" for item in descriptors)
    if expected_status == "configured":
        assert [item["runtime_root"] for item in descriptors] == expected
    else:
        assert all(item.get("reason") for item in descriptors), "WITHHELD_OR_EXTERNAL_CAPTURE_MUST_HAVE_AN_EXPLICIT_GAP"


def project_override_contract(subject):
    report, looked_up, _, unchanged = subject.project_readiness("rust", requires=["project-check"])
    assert report["status"] == "passed"
    assert "project-check" in looked_up
    assert {"rustc", "cargo", "php", "composer", "python3"}.isdisjoint(looked_up), \
        "EXPLICIT_PROJECT_REQUIREMENTS_MUST_REPLACE_LANGUAGE_DEFAULTS"
    assert unchanged


def selected_sqlite_contract(subject, mode):
    report, interpreter = subject.selected_interpreter_readiness(mode)
    expected = "passed" if mode == "compatible" else "setup_error"
    assert report["status"] == expected, "LOCAL_CAPTURE_MUST_REJECT_INCOMPATIBLE_SELECTED_SQLITE_RUNTIME"
    sqlite = [item for item in report["readiness"] if "sqlite" in item["name"].lower()]
    assert sqlite and all(item["passed"] == (mode == "compatible") for item in sqlite), \
        "SQLITE_COMPATIBILITY_MUST_BE_AN_EXPLICIT_SELECTED_INTERPRETER_OBSERVATION"
    assert report["authentication"] == "not probed"
    assert any(item.get("observation_interpreter") == interpreter for item in report["readiness"]), \
        "READINESS_MUST_USE_THE_CONFIGURED_INTERPRETER"


def public_setup_contract(subject):
    code, settings, native, preserved, project = subject.public_project_setup()
    assert code == 0, "PUBLIC_SETUP_MUST_ACCEPT_NATIVE_DRIVER_AND_PROJECT_SELECTION_IN_ONE_COMMAND"
    assert settings["mode"] == "native" and settings["suite"] == "stories"
    assert settings["drivers"] == [subject.expected_driver] and settings["stories"] == ["native-callback"]
    assert settings["project"]["language"] == "python" and settings["project"]["path"] == project
    assert native["telemetry"]["adapter"] == "local"
    assert {item["options"]["cwd"] for item in native["sessions"].values()} == {project}
    assert preserved, "EXPLICIT_SETUP_SELECTION_MUST_PRESERVE_EXISTING_OPERATOR_NATIVE_CONFIGURATION"


def selected_config_install_contract(subject, enabled):
    result, installed, unchanged = subject.setup_selected_config(enabled)
    assert result["status"] == "passed"
    assert installed == [["test"]], "SETUP_MUST_INSTALL_ONE_DEPENDENCY_SET_WHATEVER_THE_CAPTURE_POLICY"
    assert result["python"]["dependencies"]["zizmor"] is True, \
        "SETUP_MUST_RETURN_THE_POST_INSTALL_WORKFLOW_SCANNER_DETAIL"
    assert unchanged, "DEPENDENCY_SETUP_MUST_NOT_REWRITE_EITHER_OPERATOR_CONFIGURATION"


def saved_verify_interpreter_contract(subject):
    code, selected, unchanged = subject.verify_saved_interpreter()
    assert code == 0
    assert selected == "selectedselected", "PUBLIC_VERIFY_MUST_USE_SAVED_RUN_INTERPRETER"
    assert unchanged


def repeat_capture_contract(subject, policy, *, prepare, repeat):
    observed = subject.repeat_capture(policy, prepare=prepare, repeat=repeat)
    assert observed["public_pipeline"] == ["prepare_run", "repeat_settings", "prepare_run", "repeat_settings", "prepare_run"]
    assert observed["selected_checks"] and all(observed["selected_checks"])
    assert observed["cwd_preserved"], "NATIVE_REPEAT_MUST_RETAIN_ORIGINAL_RELATIVE_WORKSPACE_BASE"
    assert observed["original_file_edit_retained"]
    if policy in {"absent", "local-default"}:
        options = observed["capture_options"]
        assert len({item["runtime_root"] for item in options}) == 3, \
            "NATIVE_REPEAT_DEFAULT_CAPTURE_MUST_USE_A_FRESH_RUN_OWNED_ROOT"
        for key in ("dataset_id", "collector_id"):
            assert len({item[key] for item in options}) == 3, "NATIVE_REPEAT_MUST_ALLOCATE_FRESH_CAPTURE_IDENTITIES"
        assert observed["owned_descriptors"] and observed["collection_status"] == ["verified"] * 3, \
            "NATIVE_REPEAT_DEFAULT_CORPUS_MUST_REMAIN_ELIGIBLE_FOR_OWNED_COLLECTION"
        assert observed["first_corpus_unchanged"], "NATIVE_REPEAT_MUST_NOT_APPEND_TO_THE_PREVIOUS_RUN_CORPUS"
        assert observed["original_recipe_reused"], "NATIVE_REPEAT_MUST_REUSE_FROZEN_OPERATOR_INPUT_NOT_PRIOR_EFFECTIVE_CONFIG"
    else:
        assert observed["operator_policy_preserved"], "NATIVE_REPEAT_MUST_PRESERVE_EXPLICIT_OPERATOR_CAPTURE_POLICY"
        assert observed["withholding_preserved"], "NATIVE_REPEAT_MUST_KEEP_EXTERNAL_OR_DISABLED_CAPTURE_GAPS_EXPLICIT"


def repeat_snapshot_integrity_contract(subject, *, prepare, repeat):
    assert subject.repeat_with_changed_snapshot(prepare=prepare, repeat=repeat), \
        "NATIVE_REPEAT_MUST_REJECT_CHANGED_FROZEN_INPUT_WITHOUT_EFFECTIVE_CONFIG_FALLBACK"

