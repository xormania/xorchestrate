"""Developer settings: one declared table, a strict KEY=VALUE format and a fixed precedence.

The layer is judged through its loader and command line against throwaway repositories
and an isolated HOME, and statically against this repository: the committed example
equals the rendered table, the local file is ignored, and nothing outside the declared
consumers reads developer settings.
"""

import ast
import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from tests.hexagonal.contracts import devconfig as contract
from tests.hexagonal.support.devconfig import (
    ROOT,
    SettingsWorld,
    devconfig,
    load_by_files,
    names_someone,
    no_processes,
    resolved,
)
from tests.hexagonal.support.git_repositories import REPOSITORY_NAME, git


@pytest.fixture
def world(tmp_path, monkeypatch):
    return SettingsWorld(tmp_path, monkeypatch)


def settings():
    return devconfig("keys").SETTINGS


def placeholders(template):
    return set(re.findall(r"{([^{}]*)}", template))


# The declared table -------------------------------------------------------------------------------

def test_every_declared_setting_is_namespaced_unique_typed_and_neutral():
    table = settings()
    keys = [setting.key for setting in table]
    assert len(keys) == len(set(keys)), keys
    for setting in table:
        assert setting.key.startswith(contract.PREFIX) and re.fullmatch(r"[A-Z][A-Z0-9_]*", setting.key), setting.key
        assert setting.kind in contract.KINDS, (setting.key, setting.kind)
        assert setting.description.strip(), setting.key
        if setting.default is None:
            continue
        allowed = contract.COMMAND_PLACEHOLDERS if setting.kind == "command" else contract.PATH_PLACEHOLDERS
        assert placeholders(setting.default) <= allowed, (setting.key, setting.default)
        assert "~" not in setting.default and "$" not in setting.default, (setting.key, setting.default)
        assert not names_someone(setting.default, contract.HOME_PATHS), setting.key


def test_the_table_declares_slice_a_settings_with_their_kinds_defaults_and_sections():
    declared = {setting.key: (setting.kind, setting.default, setting.section) for setting in settings()}
    assert {key: declared.get(key) for key in contract.SLICE_A} == contract.SLICE_A


def test_the_table_declares_the_test_kit_settings_and_marks_the_evidence_ones():
    declared = {setting.key: setting for setting in settings()}
    assert {key: (declared[key].kind, declared[key].default, declared[key].section) if key in declared else None
            for key in contract.SLICE_C} == contract.SLICE_C
    assert {setting.key for setting in settings() if setting.evidence} == contract.EVIDENCE_KEYS


# Defaults -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("xdg", [False, True], ids=["home", "xdg"])
def test_defaults_come_from_home_or_xdg_and_the_main_checkout_name_only(world, xdg):
    extra = {"XDG_DATA_HOME": world.base / "data", "XDG_STATE_HOME": world.base / "state"} if xdg else {}
    env = world.env(**extra)
    for repo in (world.main, world.tree):
        config = world.load(repo, env=env)
        for key in ("XORCHESTRATE_DEV_WORKTREE_ROOT", "XORCHESTRATE_DEV_STATE_DIR"):
            expected = contract.resolve_default(contract.SLICE_A[key][1], env, REPOSITORY_NAME)
            assert config.get(key) == Path(expected), (repo, key, config.get(key))
            assert config.source(key).kind == "default"


def test_scalar_defaults_are_typed_and_unset_defaults_are_none(world):
    config = world.load()
    assert config.get("XORCHESTRATE_DEV_SERENA_VERSION") == "2.1.0"
    assert config.get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "claude-code"
    timeout = config.get("XORCHESTRATE_DEV_SERENA_TIMEOUT")
    assert isinstance(timeout, float) and timeout == 180.0
    assert config.get("XORCHESTRATE_DEV_SERENA_COMMAND") == shlex.split(contract.SERENA_COMMAND)
    for key in ("XORCHESTRATE_DEV_BASE", "XORCHESTRATE_DEV_PROBE_TOOL", "XORCHESTRATE_DEV_PROBE_ARGS"):
        assert config.get(key) is None, key
    assert list(config.warnings) == []
    assert config.evidence() == {key: {"value": "", "source": "default"} for key in contract.EVIDENCE_KEYS}


# Precedence ---------------------------------------------------------------------------------------

def test_each_level_supplies_its_key_and_explain_names_the_source(world):
    repo_file = world.repo_file("# repo\n\nXORCHESTRATE_DEV_SERENA_VERSION=9.9.repo\n")
    user_file = world.user_file("XORCHESTRATE_DEV_SERENA_CONTEXT=user-context\n")
    env = world.env(XORCHESTRATE_DEV_STATE_DIR=world.base / "from-env")
    config = world.load(env=env, flags={"XORCHESTRATE_DEV_WORKTREE_ROOT": str(world.base / "from-flag"),
                                         "XORCHESTRATE_DEV_STATE_DIR": None})
    assert config.get("XORCHESTRATE_DEV_WORKTREE_ROOT") == world.base / "from-flag"
    assert config.get("XORCHESTRATE_DEV_STATE_DIR") == world.base / "from-env", "a None flag is not given"
    assert config.get("XORCHESTRATE_DEV_SERENA_VERSION") == "9.9.repo"
    assert config.get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "user-context"
    expected = {"XORCHESTRATE_DEV_WORKTREE_ROOT": ("flag", None), "XORCHESTRATE_DEV_STATE_DIR": ("env", None),
                "XORCHESTRATE_DEV_SERENA_VERSION": ("repo_file", (repo_file, 3)),
                "XORCHESTRATE_DEV_SERENA_CONTEXT": ("user_file", (user_file, 1)),
                "XORCHESTRATE_DEV_SERENA_TIMEOUT": ("default", None)}
    lines = config.explain()
    assert [line.split("=", 1)[0] for line in lines] == [setting.key for setting in settings()]
    for key, (kind, location) in expected.items():
        source = config.source(key)
        assert source.kind == kind, (key, source)
        line = next(line for line in lines if line.startswith(key + "="))
        assert re.search(rf"#\s*{kind}\b", line), line
        if location:
            assert (Path(source.path), source.line) == (location[0], location[1]), source
            assert f"{location[0]}:{location[1]}" in line, line
        else:
            assert source.line is None


LEVELS = ("flag", "env", "repo_file", "user_file")


@pytest.mark.parametrize("highest", LEVELS + ("default",))
def test_the_highest_present_level_wins_for_one_key(world, highest):
    key = "XORCHESTRATE_DEV_SERENA_CONTEXT"
    present = LEVELS[LEVELS.index(highest):] if highest != "default" else ()
    env = world.env(**({key: "from-env"} if "env" in present else {}))
    if "repo_file" in present:
        world.repo_file(f"{key}=from-repo_file\n")
    if "user_file" in present:
        world.user_file(f"{key}=from-user_file\n")
    flags = {key: "from-flag"} if "flag" in present else {}
    config = world.load(env=env, flags=flags)
    assert config.source(key).kind == highest
    assert config.get(key) == ("claude-code" if highest == "default" else f"from-{highest}")


def test_the_user_file_lives_under_xdg_config_home_else_home_config(world):
    key = "XORCHESTRATE_DEV_SERENA_CONTEXT"
    world.user_file(f"{key}=home-config\n")
    assert world.load().get(key) == "home-config"
    env = world.env(XDG_CONFIG_HOME=world.base / "config")
    path = world.user_file(f"{key}=xdg-config\n", env=env)
    assert str(path) == contract.user_file(env)
    assert world.load(env=env).get(key) == "xdg-config"


def test_a_linked_tree_reads_the_main_checkouts_file_never_its_own(world):
    world.repo_file("XORCHESTRATE_DEV_SERENA_CONTEXT=from-main\n")
    world.repo_file("XORCHESTRATE_DEV_SERENA_CONTEXT=from-tree\n", where=world.tree)
    assert devconfig("loader").find_main_checkout(world.tree) == world.main.resolve()
    config = world.load(world.tree)
    assert config.get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "from-main"
    assert Path(config.source("XORCHESTRATE_DEV_SERENA_CONTEXT").path) == world.main / ".devtools.env"


def test_only_the_given_environment_is_read(world, monkeypatch):
    monkeypatch.setenv("XORCHESTRATE_DEV_SERENA_CONTEXT", "process-environment")
    assert world.load(env=world.env()).get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "claude-code"


# Path values --------------------------------------------------------------------------------------

def test_path_values_expand_home_and_resolve_relative_to_their_source(world, monkeypatch):
    world.repo_file("XORCHESTRATE_DEV_WORKTREE_ROOT=relative/trees\nXORCHESTRATE_DEV_STATE_DIR=~/state-here\n")
    config = world.load()
    assert config.get("XORCHESTRATE_DEV_WORKTREE_ROOT") == world.main / "relative" / "trees"
    assert config.get("XORCHESTRATE_DEV_STATE_DIR") == world.home / "state-here"
    monkeypatch.chdir(world.base)
    config = world.load(env=world.env(XORCHESTRATE_DEV_STATE_DIR="from/cwd"),
                        flags={"XORCHESTRATE_DEV_WORKTREE_ROOT": "flag/cwd"})
    assert config.get("XORCHESTRATE_DEV_STATE_DIR") == world.base / "from" / "cwd"
    assert config.get("XORCHESTRATE_DEV_WORKTREE_ROOT") == world.base / "flag" / "cwd"
    assert config.get("XORCHESTRATE_DEV_WORKTREE_ROOT").is_absolute()


def test_path_values_are_normalized_but_never_follow_symlinks(world, tmp_path):
    target = tmp_path / "real" / "python3.12"
    target.parent.mkdir()
    target.write_text("")
    link = world.main / "env" / "bin" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    config = world.load(env=world.env(XORCHESTRATE_DEV_STATE_DIR=link))
    assert config.get("XORCHESTRATE_DEV_STATE_DIR") == link
    world.repo_file("XORCHESTRATE_DEV_STATE_DIR=env/lib/../bin/python\n")
    config = world.load()
    assert config.get("XORCHESTRATE_DEV_STATE_DIR") == link, "normalized against the file, link kept"


def test_get_prints_one_typed_value_or_nothing_and_refuses_an_unknown_key(world):
    code, output = world.cli("get", "XORCHESTRATE_DEV_SERENA_VERSION")
    assert (code, output) == (0, "2.1.0\n")
    world.repo_file("XORCHESTRATE_DEV_PYCACHE_PREFIX=~/cache-here\n")
    code, output = world.cli("get", "XORCHESTRATE_DEV_PYCACHE_PREFIX")
    assert (code, output) == (0, f"{world.home / 'cache-here'}\n")
    code, output = world.cli("get", "XORCHESTRATE_DEV_TESTKIT_PROFILE")
    assert (code, output) == (0, "\n")
    code, output = world.cli("get", "XORCHESTRATE_DEV_NO_SUCH_SETTING")
    assert code == 2 and "UNKNOWN_SETTING" in output, output


# The file format ----------------------------------------------------------------------------------

def test_values_are_literal_text_and_never_executed_or_interpolated(world):
    witness = world.base / "pwned"
    world.repo_file(
        "# a comment\n"
        "\n"
        f"XORCHESTRATE_DEV_SERENA_CONTEXT=$(touch {witness})\n"
        "XORCHESTRATE_DEV_SERENA_VERSION=  `touch x` ${HOME} $HOME  \n"
        "XORCHESTRATE_DEV_BASE=\"origin/with space\"\n"
        "XORCHESTRATE_DEV_PROBE_TOOL='quoted=equals'\n")
    config = world.load()
    assert not witness.exists(), "a value was executed"
    assert config.get("XORCHESTRATE_DEV_SERENA_CONTEXT") == f"$(touch {witness})"
    assert config.get("XORCHESTRATE_DEV_SERENA_VERSION") == "`touch x` ${HOME} $HOME"
    assert config.get("XORCHESTRATE_DEV_BASE") == "origin/with space"
    assert config.get("XORCHESTRATE_DEV_PROBE_TOOL") == "quoted=equals"


def test_only_one_matching_pair_of_quotes_is_stripped():
    parse = devconfig("parser").parse_env_file
    values, warnings = parse("XORCHESTRATE_DEV_BASE=\"'inner'\"\nXORCHESTRATE_DEV_PROBE_TOOL=\"unbalanced'\n", "f")
    assert values["XORCHESTRATE_DEV_BASE"] == ("'inner'", 1)
    assert values["XORCHESTRATE_DEV_PROBE_TOOL"] == ("\"unbalanced'", 2)
    assert list(warnings) == []


@pytest.mark.parametrize("line", ["export XORCHESTRATE_DEV_BASE=x", "XORCHESTRATE_DEV_BASE", "lower_case=1",
                                  "1STARTS_WITH_DIGIT=1", "XORCHESTRATE-DEV-BASE=x", "=value"])
def test_a_line_that_is_not_a_comment_blank_or_key_value_is_refused_with_file_and_line(world, line):
    path = world.repo_file(f"# fine\nXORCHESTRATE_DEV_SERENA_CONTEXT=ok\n{line}\n")
    error = world.error()
    assert error.code == "ENV_FILE_INVALID"
    assert str(path) in str(error.detail) and "3" in str(error.detail), error.detail
    code, output = world.cli("--repo", str(world.main), "show")
    assert code == 2 and "ENV_FILE_INVALID" in output, (code, output)


def test_unknown_keys_warn_with_file_line_and_key_and_are_ignored(world):
    path = world.repo_file("XORCHESTRATE_DEV_NOT_A_SETTING=1\nSOMETHING_ELSE=2\nXORCHESTRATE_DEV_BASE=origin/x\n")
    config = world.load(env=world.env(XORCHESTRATE_DEV_ALSO_UNKNOWN="3", XORCHESTRATE_HOME="/product/home"))
    warnings = {tuple(warning) for warning in config.warnings}
    assert ("UNKNOWN_KEY", str(path), 1, "XORCHESTRATE_DEV_NOT_A_SETTING") in warnings, warnings
    assert ("UNKNOWN_KEY", str(path), 2, "SOMETHING_ELSE") in warnings, warnings
    assert any(warning[0] == "UNKNOWN_KEY" and warning[3] == "XORCHESTRATE_DEV_ALSO_UNKNOWN" for warning in warnings)
    assert not any(warning[3] == "XORCHESTRATE_HOME" for warning in warnings), "only the dev namespace is read"
    assert config.get("XORCHESTRATE_DEV_BASE") == "origin/x"


@pytest.mark.parametrize("key,value", [("XORCHESTRATE_DEV_SERENA_TIMEOUT", "soon"),
                                       ("XORCHESTRATE_DEV_PROBE_ARGS", "[1, 2]"),
                                       ("XORCHESTRATE_DEV_PROBE_ARGS", "{not json")])
def test_a_value_of_the_wrong_kind_is_refused_naming_key_and_source(world, key, value):
    path = world.repo_file(f"{key}={value}\n")
    error = world.error()
    assert error.code == "TYPE_INVALID"
    assert key in str(error.detail) and str(path) in str(error.detail), error.detail


def test_probe_arguments_are_a_json_object(world):
    world.repo_file('XORCHESTRATE_DEV_PROBE_ARGS={"relative_path": "pkg/b_model.py"}\n')
    assert world.load().get("XORCHESTRATE_DEV_PROBE_ARGS") == {"relative_path": "pkg/b_model.py"}


def test_looking_up_an_undeclared_setting_is_a_programming_error(world):
    model = devconfig("model")
    with pytest.raises(model.DevConfigError) as raised:
        world.load().get("XORCHESTRATE_DEV_NEVER_DECLARED")
    assert raised.value.code == "UNKNOWN_SETTING"


# The example file ---------------------------------------------------------------------------------

def _example():
    return devconfig("example").render_example(settings())


def test_the_rendered_example_documents_every_key_commented_out_with_neutral_placeholders():
    text = _example()
    assert text == _example(), "deterministic"
    lines = text.splitlines()
    assert all(not line.strip() or line.startswith("#") for line in lines), "nothing is set by the example"
    header = "\n".join(lines[:6]).lower()
    assert "optional" in header and "secret" in header, header
    documented = [match.group(1) for line in lines if (match := re.match(r"#\s*([A-Z][A-Z0-9_]*)=", line))]
    assert sorted(documented) == sorted(setting.key for setting in settings())
    for line in lines:
        if re.match(r"#\s*[A-Z][A-Z0-9_]*=", line):
            value = line.split("=", 1)[1]
            assert "~" not in value and "$HOME" not in value, line
            assert not re.search(r"(^|\s)/", value), f"absolute path in {line!r}"
    assert not names_someone(text, contract.HOME_PATHS)


def test_the_committed_example_is_the_rendered_table_byte_for_byte():
    committed = ROOT / contract.EXAMPLE_FILE
    assert committed.is_file(), contract.EXAMPLE_FILE
    assert committed.read_text(encoding="utf-8") == _example()


def test_the_local_settings_file_is_ignored_and_the_example_is_tracked():
    ignored = subprocess.run(["git", "check-ignore", "-q", contract.REPO_FILE], cwd=ROOT)
    assert ignored.returncode == 0, f"{contract.REPO_FILE} must be git-ignored"
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", contract.EXAMPLE_FILE], cwd=ROOT,
                             capture_output=True)
    assert tracked.returncode == 0, f"{contract.EXAMPLE_FILE} must be tracked"


def test_the_example_command_writes_and_checks_the_current_trees_copy(world):
    """The example is tracked, so each branch has its own copy; a linked tree never touches the main checkout's."""
    code, output = world.cli("example", "--check", cwd=world.tree)
    assert code == 1, (code, output)
    code, output = world.cli("example", "--write", cwd=world.tree)
    assert code == 0, output
    written = world.tree / contract.EXAMPLE_FILE
    assert written.read_text(encoding="utf-8") == _example()
    assert not (world.main / contract.EXAMPLE_FILE).exists(), "the main checkout's copy was written"
    assert world.cli("example", "--check", cwd=world.tree)[0] == 0
    assert world.cli("example", "--check", cwd=world.main)[0] == 1, "each tree checks its own copy"
    written.write_text(written.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    assert world.cli("example", "--check", cwd=world.tree)[0] == 1


def test_show_prints_one_line_per_setting_with_its_source(world):
    world.repo_file("XORCHESTRATE_DEV_SERENA_CONTEXT=shown\n")
    code, output = world.cli("show", cwd=world.tree)
    assert code == 0, output
    lines = [line for line in output.splitlines() if re.match(r"[A-Z][A-Z0-9_]*=", line)]
    assert [line.split("=", 1)[0] for line in lines] == [setting.key for setting in settings()]
    assert any(line.startswith("XORCHESTRATE_DEV_SERENA_CONTEXT=shown") and "repo_file" in line for line in lines)


# Neutrality: only declared consumers read developer settings ---------------------------------------

def _tracked(*patterns):
    out = subprocess.run(["git", "ls-files", "-z", "--", *patterns], cwd=ROOT, capture_output=True, check=True).stdout
    return [path for path in out.decode().split("\0") if path]


def test_no_source_outside_the_declared_consumers_mentions_developer_settings():
    naming = [path for path in _tracked("xorchestrate/*.py", "scripts/*.py")
              if not path.startswith(contract.CONSUMERS)
              and re.search(r"\.devtools\.env|XORCHESTRATE_DEV_", (ROOT / path).read_text(encoding="utf-8"))]
    assert naming == [], naming


def _imports(path):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("scripts."):
            names.add(node.value)
    return names


def test_no_module_outside_the_declared_consumers_imports_the_settings_layer():
    importing = [path for path in _tracked("xorchestrate/*.py", "scripts/*.py")
                 if not path.startswith(contract.CONSUMERS)
                 and any(name == "scripts.devconfig" or name.startswith("scripts.devconfig.")
                         for name in _imports(path))]
    assert importing == [], importing


def test_only_consumer_roles_may_depend_on_the_settings_role():
    roles = json.loads((ROOT / "contracts/hexagonal.json").read_text())["roles"]
    assert "scripts.devconfig.*" in roles["devconfig"]["modules"], roles["devconfig"]["modules"]
    dependants = {name for name, role in roles.items() if "devconfig" in role.get("allowed_roles", ())}
    assert dependants <= contract.CONSUMER_ROLES, sorted(dependants - contract.CONSUMER_ROLES)


def test_the_settings_layer_uses_the_standard_library_only_and_names_no_one():
    import sys
    sources = _tracked("scripts/devconfig/*.py")
    assert sources, "scripts/devconfig has no tracked modules"
    for path in sources:
        for name in _imports(path):
            top = name.split(".")[0]
            assert top in sys.stdlib_module_names or name.startswith("scripts.devconfig"), (path, name)
        text = (ROOT / path).read_text(encoding="utf-8")
        assert not names_someone(text, contract.HOME_PATHS), path
        assert _namespace_uses(text) == [], (path, _namespace_uses(text))


def _namespace_uses(text):
    """String constants, other than docstrings, that use the name anywhere but the XDG folder or the key prefix."""
    tree = ast.parse(text)
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    allowed = re.compile(rf"{contract.PREFIX}[A-Z0-9_]*|(?<![A-Za-z]){contract.NAMESPACE}(?=/|$)")
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
            and contract.NAMESPACE in allowed.sub("", node.value).lower()]


# Review findings: repository discovery, robustness and edge values -----------------------------------

def _bare_with_tree(base):
    bare = base / "bare.git"
    git(base, "init", "-q", "--bare", "-b", "trunk", bare)
    seed = base / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "trunk")
    (seed / "f.txt").write_text("x\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", str(bare), "trunk")
    tree = base / "from-bare"
    git(bare, "worktree", "add", "-q", tree, "trunk")
    return tree


def test_a_bare_main_repository_is_not_a_checkout_to_read_settings_from(world):
    tree = _bare_with_tree(world.base)
    error = world.error(repo=tree)
    assert error.code == "NOT_A_REPOSITORY", error.detail


def test_a_submodule_is_its_own_checkout_named_after_itself(world, monkeypatch):
    library = world.base / "library-source"
    library.mkdir()
    git(library, "init", "-q", "-b", "trunk")
    (library / "lib.py").write_text("X = 1\n")
    git(library, "add", "-A")
    git(library, "commit", "-q", "-m", "lib")
    git(world.main, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library), "inner-lib")
    inner = world.main / "inner-lib"
    (inner / ".devtools.env").write_text("XORCHESTRATE_DEV_SERENA_CONTEXT=from-submodule\n")
    assert devconfig("loader").find_main_checkout(inner) == inner.resolve()
    config = world.load(inner)
    assert config.get("XORCHESTRATE_DEV_WORKTREE_ROOT").name == "inner-lib"
    assert config.get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "from-submodule"


def test_the_main_checkout_is_the_first_registered_tree_whatever_git_dir_says(world, monkeypatch):
    other = world.base / "elsewhere"
    other.mkdir()
    git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_COMMON_DIR", str(other / ".git"))
    assert devconfig("loader").find_main_checkout(world.tree) == world.main.resolve()
    world.repo_file("XORCHESTRATE_DEV_SERENA_CONTEXT=from-main\n")
    assert world.load(world.tree).get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "from-main"


def _not_a_repository(world, tmp):
    plain = tmp / "plain"
    plain.mkdir()
    return plain


def _a_file(world, tmp):
    path = tmp / "file.txt"
    path.write_text("x")
    return path


@pytest.mark.parametrize("where", [lambda world, tmp: tmp / "missing", _a_file, _not_a_repository],
                         ids=["missing", "file", "not-a-repository"])
def test_a_repository_path_that_is_not_a_checkout_is_refused_without_a_traceback(world, tmp_path, where):
    path = where(world, tmp_path)
    assert world.error(repo=path).code == "NOT_A_REPOSITORY"
    code, output = world.cli("--repo", str(path), "show")
    assert "Traceback" not in output, output
    if path.is_dir():
        # show reads files only: a directory outside any repository has settings, just no repository file.
        assert code == 0 and any(line.startswith("REPO_FILE_SKIPPED: ") for line in output.splitlines()), output
    else:
        assert code == 2 and "NOT_A_REPOSITORY" in output, (code, output)


def test_without_git_the_repository_cannot_be_found_and_says_so(world, monkeypatch, tmp_path):
    empty = tmp_path / "no-programs"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert world.error().code == "NOT_A_REPOSITORY"
    world.repo_file(REPO_VALUE)
    code, output = world.cli("--repo", str(world.main), "show")
    assert code == 0 and "Traceback" not in output, output
    assert "XORCHESTRATE_DEV_SERENA_VERSION=9.9.repo" in output, "show finds the repository file without git"


@pytest.mark.parametrize("damage", ["not-utf8", "unreadable"])
def test_an_unreadable_settings_file_is_refused_naming_it(world, damage):
    import os
    path = world.repo_file("XORCHESTRATE_DEV_BASE=x\n")
    if damage == "not-utf8":
        path.write_bytes(b"XORCHESTRATE_DEV_BASE=\xff\xfe\n")
    else:
        if os.geteuid() == 0:
            pytest.skip("permissions do not bind the superuser")
        path.chmod(0)
    try:
        error = world.error()
        assert error.code == "ENV_FILE_INVALID" and str(path) in str(error.detail), (error.code, error.detail)
        code, output = world.cli("show")
        assert code == 2 and "Traceback" not in output, output
    finally:
        path.chmod(0o644)


def test_home_comes_from_the_given_environment_else_the_account_database(world, monkeypatch, tmp_path):
    import pwd
    monkeypatch.setenv("HOME", str(tmp_path / "process-home"))
    key = "XORCHESTRATE_DEV_STATE_DIR"
    given = world.load(env=world.env())
    assert given.get(key) == world.home / ".local/state/xorchestrate/worktrees"
    for env in ({"PATH": world.env()["PATH"]}, {"PATH": world.env()["PATH"], "HOME": ""}):
        account = Path(pwd.getpwuid(__import__("os").getuid()).pw_dir)
        assert world.load(env=env).get(key) == (account / ".local/state/xorchestrate/worktrees").resolve()


def test_a_relative_xdg_directory_counts_as_unset(world):
    config = world.load(env=world.env(XDG_STATE_HOME="relative/state", XDG_CONFIG_HOME="relative/config"))
    assert config.get("XORCHESTRATE_DEV_STATE_DIR") == world.home / ".local/state/xorchestrate/worktrees"
    world.user_file("XORCHESTRATE_DEV_SERENA_CONTEXT=from-home-config\n")
    config = world.load(env=world.env(XDG_CONFIG_HOME="relative/config"))
    assert config.get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "from-home-config"


def test_an_empty_value_means_not_set_at_that_level(world):
    world.repo_file("XORCHESTRATE_DEV_STATE_DIR=/from/file\nXORCHESTRATE_DEV_BASE=\nXORCHESTRATE_DEV_SERENA_TIMEOUT=\n")
    config = world.load(env=world.env(XORCHESTRATE_DEV_STATE_DIR="", XORCHESTRATE_DEV_SERENA_CONTEXT=""))
    assert config.get("XORCHESTRATE_DEV_STATE_DIR") == Path("/from/file")
    assert config.source("XORCHESTRATE_DEV_STATE_DIR").kind == "repo_file"
    assert config.get("XORCHESTRATE_DEV_BASE") is None and config.source("XORCHESTRATE_DEV_BASE").kind == "default"
    assert config.get("XORCHESTRATE_DEV_SERENA_TIMEOUT") == 180.0
    assert config.get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "claude-code"


def test_a_byte_order_mark_and_windows_line_ends_are_accepted():
    parse = devconfig("parser").parse_env_file
    values, _ = parse("\ufeffXORCHESTRATE_DEV_BASE=origin/x\r\nXORCHESTRATE_DEV_PROBE_TOOL=find\r\n", "f")
    assert values["XORCHESTRATE_DEV_BASE"] == ("origin/x", 1)
    assert values["XORCHESTRATE_DEV_PROBE_TOOL"] == ("find", 2)


def test_lines_split_only_on_newline():
    parse = devconfig("parser").parse_env_file
    values, _ = parse("XORCHESTRATE_DEV_BASE=a\fb\u2028c\x85d\nXORCHESTRATE_DEV_PROBE_TOOL=t\n", "f")
    assert values["XORCHESTRATE_DEV_BASE"] == ("a\fb\u2028c\x85d", 1)
    assert values["XORCHESTRATE_DEV_PROBE_TOOL"] == ("t", 2)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
def test_a_number_must_be_finite(world, value):
    world.repo_file(f"XORCHESTRATE_DEV_SERENA_TIMEOUT={value}\n")
    error = world.error()
    assert error.code == "TYPE_INVALID" and "XORCHESTRATE_DEV_SERENA_TIMEOUT" in str(error.detail)


def test_a_settings_file_tracked_by_git_is_refused(world):
    path = world.repo_file("XORCHESTRATE_DEV_SERENA_COMMAND=echo injected {port}\n")
    git(world.main, "add", "-f", ".devtools.env")
    git(world.main, "commit", "-q", "-m", "commit the local settings")
    error = world.error()
    assert error.code == "ENV_FILE_TRACKED" and str(path) in str(error.detail), (error.code, error.detail)
    code, output = world.cli("show")
    assert code == 2 and "ENV_FILE_TRACKED" in output, output


# Discovery by plain file reads: no process, same answers as git ---------------------------------------

SKIPPED = "REPO_FILE_SKIPPED"
REPO_VALUE = "XORCHESTRATE_DEV_SERENA_VERSION=9.9.repo\n"
USER_VALUE = "XORCHESTRATE_DEV_SERENA_CONTEXT=user-context\n"


def by_files(world, monkeypatch, repo, env=None):
    with no_processes(monkeypatch):
        return load_by_files(repo, env=world.env() if env is None else env)


def skipped(config):
    return [warning for warning in config.warnings if warning[0] == SKIPPED]


def submodule(world, tmp_path):
    inner_origin = tmp_path / "inner"
    git(tmp_path, "init", "-q", "-b", "trunk", inner_origin)
    (inner_origin / "mod.py").write_text("X = 1\n")
    git(inner_origin, "add", "-A")
    git(inner_origin, "commit", "-q", "-m", "inner")
    git(world.main, "-c", "protocol.file.allow=always", "submodule", "add", "-q", inner_origin, "sub")
    return world.main / "sub"


@pytest.mark.parametrize("where", ["main", "linked", "inside", "submodule"])
def test_file_discovery_starts_no_process_and_matches_git_discovery(world, monkeypatch, tmp_path, where):
    repo = {"main": world.main, "linked": world.tree, "inside": world.tree / "pkg"}.get(where)
    if where == "submodule":
        repo = submodule(world, tmp_path)
        world.repo_file("XORCHESTRATE_DEV_SERENA_VERSION=9.9.sub\n", where=repo)
    world.repo_file(REPO_VALUE)
    world.user_file(USER_VALUE)
    files = by_files(world, monkeypatch, repo)
    assert resolved(files) == resolved(world.load(repo)), where
    expected = "9.9.sub" if where == "submodule" else "9.9.repo"
    assert files.get("XORCHESTRATE_DEV_SERENA_VERSION") == expected
    assert files.source("XORCHESTRATE_DEV_SERENA_VERSION").kind == "repo_file"


def _bare_tree(world, tmp_path):
    bare = tmp_path / "shared.git"
    git(tmp_path, "clone", "-q", "--bare", world.main, bare)
    tree = tmp_path / "bare-linked"
    git(bare, "worktree", "add", "-q", "-b", "elsewhere", tree)
    return tree


def _plain(world, tmp_path):
    plain = tmp_path / "plain-dir"
    plain.mkdir()
    return plain


@pytest.mark.parametrize("make", [_bare_tree, _plain], ids=["bare-common-dir", "no-repository"])
def test_without_a_main_checkout_file_discovery_skips_only_the_repository_file(world, monkeypatch, tmp_path, make):
    repo = make(world, tmp_path)
    (repo / ".devtools.env").write_text(REPO_VALUE)
    world.user_file(USER_VALUE)
    env = world.env(XORCHESTRATE_DEV_BASE="from-env")
    try:
        config = by_files(world, monkeypatch, repo, env)
    except devconfig("model").DevConfigError as error:
        pytest.fail(f"file discovery refused instead of skipping the repository file: {error}")
    assert [(code, line) for code, _, line, _ in skipped(config)] == [(SKIPPED, None)], config.warnings
    assert config.source("XORCHESTRATE_DEV_SERENA_VERSION").kind == "default"
    assert config.get("XORCHESTRATE_DEV_SERENA_CONTEXT") == "user-context"
    assert config.get("XORCHESTRATE_DEV_BASE") == "from-env"
    assert config.get("XORCHESTRATE_DEV_WORKTREE_ROOT").name == repo.name


def test_file_discovery_refuses_a_tracked_repository_file_from_the_index(world, monkeypatch):
    world.repo_file(REPO_VALUE)
    git(world.main, "add", "-f", ".devtools.env")
    with pytest.raises(devconfig("model").DevConfigError) as refused:
        by_files(world, monkeypatch, world.tree)
    assert refused.value.code == "ENV_FILE_TRACKED"


@pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])
def test_file_discovery_reads_index_version_three(world, monkeypatch, tracked):
    world.repo_file(REPO_VALUE)
    if tracked:
        git(world.main, "add", "-f", ".devtools.env")
    git(world.main, "update-index", "--index-version", "3")
    if tracked:
        with pytest.raises(devconfig("model").DevConfigError) as refused:
            by_files(world, monkeypatch, world.main)
        assert refused.value.code == "ENV_FILE_TRACKED"
    else:
        assert by_files(world, monkeypatch, world.main).get("XORCHESTRATE_DEV_SERENA_VERSION") == "9.9.repo"


def _index_v4(world, tmp_path):
    git(world.main, "update-index", "--index-version", "4")
    return world.main


def _damaged(world, tmp_path):
    index = world.main / ".git" / "index"
    data = index.read_bytes()
    index.write_bytes(data[:len(data) // 2])
    return world.main


def _sha256(world, tmp_path):
    repo = tmp_path / "sha256-repo"
    git(tmp_path, "init", "-q", "--object-format=sha256", "-b", "trunk", repo)
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "a")
    return repo


@pytest.mark.parametrize("make", [_index_v4, _damaged, _sha256], ids=["index-v4", "damaged-index", "sha256"])
def test_an_index_file_discovery_cannot_read_skips_the_repository_file(world, monkeypatch, tmp_path, make):
    repo = make(world, tmp_path)
    (repo / ".devtools.env").write_text(REPO_VALUE)
    try:
        config = by_files(world, monkeypatch, repo)
    except devconfig("model").DevConfigError as error:
        pytest.fail(f"an unreadable index refused instead of skipping the repository file: {error}")
    assert [code for code, *_ in skipped(config)] == [SKIPPED], config.warnings
    assert config.source("XORCHESTRATE_DEV_SERENA_VERSION").kind == "default"


def test_a_checkout_without_an_index_has_nothing_tracked(world, monkeypatch, tmp_path):
    repo = tmp_path / "fresh"
    git(tmp_path, "init", "-q", "-b", "trunk", repo)
    assert not (repo / ".git" / "index").exists()
    (repo / ".devtools.env").write_text(REPO_VALUE)
    config = by_files(world, monkeypatch, repo)
    assert config.get("XORCHESTRATE_DEV_SERENA_VERSION") == "9.9.repo" and skipped(config) == []


def test_show_and_get_outside_a_repository_report_the_skipped_file_and_still_read_the_user_file(
        world, monkeypatch, tmp_path):
    plain = tmp_path / "plain-dir"
    plain.mkdir()
    (plain / ".devtools.env").write_text(REPO_VALUE)
    world.user_file(USER_VALUE)
    with no_processes(monkeypatch):
        code, output = world.cli("show", cwd=plain)
    assert code == 0, output
    lines = [line for line in output.splitlines() if line.startswith(f"{SKIPPED}: ")]
    assert len(lines) == 1, output
    assert lines[0].endswith("(no repository); the repository settings file was not read"), lines[0]
    assert any(line.startswith("XORCHESTRATE_DEV_SERENA_CONTEXT=user-context") for line in output.splitlines())
    assert any(line.startswith("XORCHESTRATE_DEV_SERENA_VERSION=2.1.0") for line in output.splitlines())
    with no_processes(monkeypatch):
        assert world.cli("get", "XORCHESTRATE_DEV_SERENA_CONTEXT", cwd=plain) == (0, "user-context\n")
