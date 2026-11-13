"""Work trees: created under a configurable root from the remote's default branch, removed only when safe.

Real git runs against a throwaway repository whose origin's default branch is not
``master``. Processes come from a fake table or a fake /proc directory. Every git
argument list the tool runs is recorded; none may force, discard or prune.
"""

import contextlib
import json
import shutil
import signal
import sys
from pathlib import Path

import pytest

from tests.hexagonal.contracts import devconfig as settings_contract
from tests.hexagonal.contracts import worktrees as contract
from tests.hexagonal.support.devconfig import names_someone
from tests.hexagonal.support.git_repositories import DEFAULT_BRANCH, REPOSITORY_NAME, commit, git
from tests.hexagonal.support.source_scan import ROOT, imports, namespace_uses, tracked
from tests.hexagonal.support.worktrees import Workspace, fake_proc, server_argv, tool


@pytest.fixture
def ws(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path, monkeypatch)
    yield workspace
    workspace.close()
    if workspace._git is not None:
        assert contract.forced_git_calls(workspace.git.calls) == [], "the tool forced, discarded or pruned"


def refs(ws):
    return git(ws.main, "for-each-ref", "--format=%(refname) %(objectname)")


def trees(ws):
    return git(ws.main, "worktree", "list", "--porcelain")


def codes(result):
    return contract.codes_in(result.text, contract.TREE_REFUSALS | contract.SERENA_CODES)


# new ----------------------------------------------------------------------------------------------

def test_new_creates_a_nested_tree_from_the_remotes_default_branch_without_upstream(ws):
    result = ws.run("new", "feat/nested/name")
    path = ws.tree("feat/nested/name")
    assert result.code == 0, result.text
    assert result.out.strip().splitlines()[-1] == str(path)
    assert git(path, "rev-parse", "--abbrev-ref", "HEAD") == "feat/nested/name"
    assert git(path, "rev-parse", "HEAD") == git(ws.main, "rev-parse", f"origin/{DEFAULT_BRANCH}")
    assert git(path, "rev-parse", "--abbrev-ref", "feat/nested/name@{upstream}", check=False).returncode != 0
    assert git(path, "status", "--porcelain", "--ignored") == "", "the tool wrote into the new tree"
    assert ["fetch", "origin"] in [call[:2] for call in result.git_calls]


def test_new_starts_from_an_explicit_base(ws):
    side = commit(ws.main, "side.txt", "side\n")
    git(ws.main, "push", "-q", "origin", "HEAD:refs/heads/side")
    result = ws.run("new", "from-side", "--base", "origin/side")
    assert result.code == 0, result.text
    assert git(ws.tree("from-side"), "rev-parse", "HEAD") == side


def test_without_a_resolvable_remote_head_new_refuses_and_says_how_to_set_the_base(ws):
    git(ws.main, "remote", "set-head", "origin", "--delete")
    before = refs(ws), trees(ws)
    result = ws.run("new", "orphan")
    assert result.code == 1 and codes(result) == {"BASE_UNRESOLVED"}, result.text
    assert "XORCHESTRATE_DEV_BASE" in result.text and "--base" in result.text
    assert (refs(ws), trees(ws)) == before
    assert [call for call in result.git_calls if call[:1] == ["fetch"]] == [], "a refused new still fetched"
    assert ws.run("new", "orphan", "--base", f"origin/{DEFAULT_BRANCH}").code == 0


def test_the_base_setting_replaces_the_remote_default(ws):
    tip = commit(ws.main, "other.txt", "x\n")
    git(ws.main, "push", "-q", "origin", "HEAD:refs/heads/other")
    result = ws.run("new", "configured", env=ws.env(XORCHESTRATE_DEV_BASE="origin/other"))
    assert result.code == 0, result.text
    assert git(ws.tree("configured"), "rev-parse", "HEAD") == tip


def _tree_exists(ws):
    ws.tree("taken").mkdir(parents=True)
    (ws.tree("taken") / "keep.txt").write_text("mine\n")
    return "taken"


def _local_branch(ws):
    git(ws.main, "branch", "taken")
    return "taken"


def _remote_branch(ws):
    git(ws.main, "push", "-q", "origin", f"{DEFAULT_BRANCH}:refs/heads/taken")
    git(ws.main, "fetch", "-q", "origin")
    return "taken"


NEW_REFUSALS = {
    "TREE_EXISTS": (_tree_exists, ()),
    "BRANCH_EXISTS-local": (_local_branch, ()),
    "BRANCH_EXISTS-remote": (_remote_branch, ()),
    "BAD_BRANCH": (lambda ws: "bad..name", ()),
    "BASE_MISSING": (lambda ws: "fine", ("--base", "origin/no-such-branch")),
}


@pytest.mark.parametrize("case", sorted(NEW_REFUSALS))
def test_new_refuses_without_changing_anything(ws, case):
    setup, extra = NEW_REFUSALS[case]
    branch = setup(ws)
    before = refs(ws), trees(ws)
    result = ws.run("new", branch, *extra)
    assert result.code == 1, result.text
    assert codes(result) == {case.split("-")[0]}, result.text
    assert (refs(ws), trees(ws)) == before
    if case == "TREE_EXISTS":
        assert (ws.tree("taken") / "keep.txt").read_text() == "mine\n"


def test_new_refuses_when_the_fetch_fails(ws):
    git(ws.main, "remote", "set-url", "origin", str(ws.base / "missing.git"))
    before = refs(ws), trees(ws)
    result = ws.run("new", "offline")
    assert result.code == 1 and codes(result) == {"FETCH_FAILED"}, result.text
    assert (refs(ws), trees(ws)) == before


def test_a_repository_path_inside_a_linked_tree_means_its_main_checkout(ws):
    first = ws.new("first")
    env = ws.env(XDG_DATA_HOME=ws.base / "data")
    result = ws.run("--repo", str(first / "pkg"), "new", "second", env=env, repo=False, locations=False)
    assert result.code == 0, result.text
    expected = settings_contract.resolve_default(settings_contract.SLICE_A["XORCHESTRATE_DEV_WORKTREE_ROOT"][1],
                                                 env, REPOSITORY_NAME)
    assert result.out.strip().splitlines()[-1] == f"{expected}/second"
    assert git(Path(expected) / "second", "rev-parse", "--git-common-dir") == str(ws.main / ".git")


def test_the_root_and_state_flags_override_every_other_source(ws):
    env = ws.env(XORCHESTRATE_DEV_WORKTREE_ROOT=ws.base / "env-root")
    (ws.main / ".devtools.env").write_text(f"XORCHESTRATE_DEV_WORKTREE_ROOT={ws.base / 'file-root'}\n")
    assert ws.run("new", "flagged", env=env).out.strip().splitlines()[-1] == str(ws.tree("flagged"))
    assert ws.run("new", "from-env", env=env, locations=False).out.strip().splitlines()[-1] == str(
        ws.base / "env-root" / "from-env")
    assert ws.run("new", "from-file", locations=False).out.strip().splitlines()[-1] == str(
        ws.base / "file-root" / "from-file")


# remove -------------------------------------------------------------------------------------------

def test_remove_deletes_a_clean_merged_tree_and_its_branch(ws):
    path = ws.new("done")
    tip = git(path, "rev-parse", "HEAD")
    result = ws.run("remove", "done")
    assert result.code == 0, result.text
    assert not path.exists()
    assert git(ws.main, "branch", "--list", "done") == ""
    deletes = [call for call in ws.git.calls if call[:1] == ["update-ref"]]
    assert deletes and deletes[-1] == ["update-ref", "-d", "refs/heads/done", tip], deletes


def test_a_branch_that_moved_during_removal_is_kept_and_the_removal_still_succeeds(ws):
    path = ws.new("moving")
    moved = commit(ws.main, "elsewhere.txt", "x\n")
    git(ws.main, "reset", "-q", "--hard", "HEAD~1")
    ws.git.after[("worktree", "remove")] = lambda: git(ws.main, "update-ref", "refs/heads/moving", moved)
    try:
        result = ws.run("remove", "moving")
    except Exception as error:  # noqa: BLE001 - the defect being pinned
        pytest.fail(f"remove raised {error!r}")
    assert result.code == 0, result.text
    assert not path.exists()
    assert git(ws.main, "rev-parse", "refs/heads/moving") == moved
    assert "kept" in result.text.lower(), result.text


def test_remove_keeps_a_pushed_but_unmerged_branch_and_says_so(ws):
    path = ws.new("pushed")
    tip = commit(path, "work.txt", "work\n")
    git(path, "push", "-q", "origin", "pushed")
    result = ws.run("remove", "pushed")
    assert result.code == 0, result.text
    assert not path.exists()
    assert git(ws.main, "rev-parse", "refs/heads/pushed") == tip
    assert "pushed" in result.text and "kept" in result.text.lower(), result.text


def test_remove_with_keep_branch_keeps_a_merged_branch(ws):
    path = ws.new("kept")
    assert ws.run("remove", "kept", "--keep-branch").code == 0
    assert not path.exists() and git(ws.main, "branch", "--list", "kept").strip().endswith("kept")


def test_ignored_files_do_not_make_a_tree_dirty(ws):
    path = ws.new("ignored")
    (path / "build.log").write_text("noise\n")
    result = ws.run("remove", "ignored")
    assert result.code == 0, result.text
    assert not path.exists()


def test_remove_stops_the_trees_own_server_first_and_it_does_not_count_as_in_use(ws):
    path = ws.new("served")
    state = ws.seed_server(path, pid=7_100)
    ws.processes.add(7_101, ppid=7_100, pgid=7_100, cwd=path / "pkg", argv=["node", "pyright"])
    ws.processes.add(7_102, ppid=7_101, pgid=7_102, cwd=ws.base, argv=["python", "worker"],
                     open_paths=[path / "README.md"])
    result = ws.run("remove", "served")
    assert result.code == 0, result.text
    assert not path.exists()
    signalled = [index for index, event in enumerate(ws.events) if event[0] == "signal"]
    removed = [index for index, event in enumerate(ws.events) if event[:3] == ("git", "worktree", "remove")]
    assert signalled and removed and max(signalled) < min(removed), ws.events
    assert (state.pgid, signal.SIGTERM) in ws.processes.signals
    assert ws.saved(path) is None


def _dirty_tracked(ws, path):
    (path / "README.md").write_text("changed\n")


def _dirty_untracked(ws, path):
    (path / "new.txt").write_text("new\n")


def _locked(ws, path):
    git(ws.main, "worktree", "lock", path)


def _stash(ws, path):
    (path / "README.md").write_text("stashed\n")
    git(path, "stash", "push", "-q")


def _in_use_cwd(ws, path):
    ws.processes.add(8_001, cwd=path / "pkg", argv=["bash"])


def _in_use_open_file(ws, path):
    ws.processes.add(8_002, cwd=ws.base, argv=["editor"], open_paths=[path / "README.md"])


def _unpushed(ws, path):
    commit(path, "local.txt", "local\n")


REMOVE_REFUSALS = {
    "DIRTY-tracked": _dirty_tracked,
    "DIRTY-untracked": _dirty_untracked,
    "LOCKED": _locked,
    "STASH": _stash,
    "IN_USE-cwd": _in_use_cwd,
    "IN_USE-open-file": _in_use_open_file,
    "UNPUSHED": _unpushed,
}


@pytest.mark.parametrize("case", sorted(REMOVE_REFUSALS))
def test_remove_refuses_and_changes_nothing_while_the_server_keeps_running(ws, case):
    path = ws.new("guarded")
    REMOVE_REFUSALS[case](ws, path)
    state = ws.seed_server(path, pid=7_200)
    before = refs(ws), trees(ws), git(path, "status", "--porcelain")
    result = ws.run("remove", "guarded")
    assert result.code == 1, result.text
    assert codes(result) >= {case.split("-")[0]}, result.text
    assert path.is_dir()
    assert (refs(ws), trees(ws), git(path, "status", "--porcelain")) == before
    assert ws.processes.signals == [] and ws.processes.alive(state.pid)
    assert ws.saved(path) == state


@pytest.mark.parametrize("target,code", [("never-created", "NOT_A_TREE"), (DEFAULT_BRANCH, "MAIN_CHECKOUT")])
def test_remove_refuses_what_is_not_a_removable_tree(ws, target, code):
    before = refs(ws), trees(ws)
    result = ws.run("remove", target)
    assert result.code == 1 and code in codes(result), result.text
    assert (refs(ws), trees(ws)) == before and ws.main.is_dir()


def test_in_use_is_read_from_the_process_filesystem(ws, tmp_path):
    path = ws.new("watched")
    proc = fake_proc(tmp_path / "proc", {
        4242: (1, 4242, "shell", ["bash", "-l"], str(path / "pkg"), []),
        4300: (1, 4300, "other", ["sleep", "100"], str(tmp_path), [str(tmp_path / "elsewhere")]),
    })
    result = ws.run("remove", "watched", ports=ws.ports(processes=tool("adapters.procfs").ProcFs(proc)))
    assert result.code == 1 and "IN_USE" in codes(result), result.text
    assert path.is_dir()


# Reading /proc ------------------------------------------------------------------------------------

def test_procfs_reads_parent_group_command_directory_and_open_files(tmp_path):
    root = fake_proc(tmp_path / "proc", {
        101: (1, 101, "odd (name) here", ["python", "-m", "serena", "start-mcp-server"], str(tmp_path / "a"),
              [str(tmp_path / "a" / "f.txt"), "pipe:[123]"]),
        102: (101, 205, "pyright", ["node", "pyright"], str(tmp_path / "b"), []),
    })
    (root / "self").mkdir()
    (root / "103").mkdir()
    found = {proc.pid: proc for proc in tool("adapters.procfs").ProcFs(root).processes()}
    assert set(found) == {101, 102}, "non-numeric and unreadable entries are skipped"
    first = found[101]
    assert (first.ppid, first.pgid, first.cwd) == (1, 101, str(tmp_path / "a"))
    assert first.argv == ("python", "-m", "serena", "start-mcp-server")
    assert str(tmp_path / "a" / "f.txt") in first.open_paths
    assert (found[102].ppid, found[102].pgid) == (101, 205)


@pytest.mark.parametrize("pgid", [-1, 0, 1])
def test_procfs_never_signals_process_group_one_or_below(monkeypatch, pgid):
    procfs = tool("adapters.procfs")
    sent = []
    monkeypatch.setattr(procfs.os, "killpg", lambda group, sig: sent.append((group, sig)))
    with contextlib.suppress(ValueError):
        procfs.ProcFs().signal_group(pgid, signal.SIGTERM)
    assert sent == []


# Neutrality: optional, standard library only, nothing depends on it --------------------------------

def test_nothing_outside_the_tool_imports_or_requires_it():
    importing = [path for path in tracked("xorchestrate/*.py", "scripts/*.py")
                 if not path.startswith(contract.TOOL_SOURCES)
                 and any(name == "scripts.worktrees" or name.startswith("scripts.worktrees.") for name in imports(path))]
    assert importing == [], importing
    assert "worktrees" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    roles = json.loads((ROOT / "contracts/hexagonal.json").read_text())["roles"]
    assert contract.TOOL_ROLES <= roles.keys(), sorted(contract.TOOL_ROLES - roles.keys())
    dependants = {name for name, role in roles.items() if set(role.get("allowed_roles", ())) & contract.TOOL_ROLES}
    assert dependants <= contract.TOOL_ROLES | contract.TEST_ROLES, sorted(dependants)


def test_the_tool_uses_the_standard_library_and_settings_layer_only_and_names_no_one():
    sources = tracked(contract.TOOL_SOURCES + "*.py")
    assert sources, "scripts/worktrees has no tracked modules"
    for path in sources:
        for name in imports(path):
            assert (name.split(".")[0] in sys.stdlib_module_names or name.startswith("scripts.worktrees")
                    or name.startswith("scripts.devconfig")), (path, name)
        text = (ROOT / path).read_text(encoding="utf-8")
        assert not names_someone(text, settings_contract.HOME_PATHS), path
        assert "master" not in text, f"{path} hardwires a branch name"
        assert namespace_uses(text, settings_contract.NAMESPACE, settings_contract.PREFIX) == [], path


def test_a_stash_made_on_another_branch_does_not_block_removal(ws):
    path = ws.new("unrelated-stash")
    (ws.main / "README.md").write_text("main checkout work\n")
    git(ws.main, "stash", "push", "-q")
    result = ws.run("remove", "unrelated-stash")
    assert result.code == 0, result.text
    assert not path.exists()
    assert git(ws.main, "stash", "list") != "", "the other branch's stash is untouched"


def test_a_recorded_server_pid_now_held_by_an_unrelated_process_in_the_tree_counts_as_in_use(ws):
    path = ws.new("guarded")
    state = ws.make_state(path, pid=2_345, pgid=2_345, port=1, head=git(path, "rev-parse", "HEAD"),
                          version=contract.DEFAULT_SERENA_VERSION, started=0.0, last_call=None,
                          argv=tuple(server_argv(path, 1)), start_time=None)
    ws.store().save(ws.key(path), state)
    ws.processes.add(2_345, cwd=path, argv=["tmux", "new-session", "-d"])
    ws.processes.add(2_346, ppid=2_345, pgid=2_345, cwd=path / "pkg", argv=["bash"])
    before = refs(ws), trees(ws)
    result = ws.run("remove", "guarded")
    assert result.code == 1 and "IN_USE" in codes(result), result.text
    assert "2345" in result.text and "2346" in result.text, result.text
    assert ws.processes.signals == [] and path.is_dir()
    assert (refs(ws), trees(ws)) == before


def _deleted(ws, path):
    shutil.rmtree(path)


def _moved(ws, path):
    path.rename(ws.base / "moved-away")


@pytest.mark.parametrize("change", [_deleted, _moved], ids=["deleted", "moved"])
def test_remove_refuses_a_tree_whose_directory_is_gone(ws, change):
    path = ws.new("vanished")
    change(ws, path)
    before = refs(ws), trees(ws)
    try:
        result = ws.run("remove", "vanished")
    except Exception as error:  # noqa: BLE001 - the defect being pinned
        pytest.fail(f"remove raised {error!r}")
    assert result.code == 1, result.text
    assert "NOT_A_TREE" in codes(result), result.text
    assert (refs(ws), trees(ws)) == before


@pytest.mark.parametrize("prefix,code", [(("status",), "DIRTY"), (("stash", "list"), "STASH")], ids=["status", "stash"])
def test_a_safety_check_that_errors_refuses_removal(ws, prefix, code):
    path = ws.new("unchecked")
    ws.git.fail[prefix] = "fatal: planted failure"
    before = refs(ws), trees(ws)
    result = ws.run("remove", "unchecked")
    assert result.code == 1, result.text
    assert code in codes(result), result.text
    assert path.is_dir() and (refs(ws), trees(ws)) == before


def test_a_serena_server_in_the_tree_whose_state_records_no_command_counts_as_in_use(ws):
    path = ws.new("guarded")
    state = ws.make_state(path, pid=2_400, pgid=2_400, port=1, head=git(path, "rev-parse", "HEAD"),
                          version=contract.DEFAULT_SERENA_VERSION, started=0.0, last_call=None, argv=(), start_time=None)
    ws.store().save(ws.key(path), state)
    ws.processes.add(2_400, cwd=path, argv=server_argv(path, 1))
    before = refs(ws), trees(ws)
    result = ws.run("remove", "guarded")
    assert result.code == 1 and "IN_USE" in codes(result), result.text
    assert "2400" in result.text and ws.processes.signals == [] and path.is_dir()
    assert (refs(ws), trees(ws)) == before
