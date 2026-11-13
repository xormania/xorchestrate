"""Maintenance: a report that judges every tree and scratch directory, a pruner that removes only the safe.

A fleet of trees covers every verdict: merge-commit and squash merges, pushed and
unpushed work, dirty, stashed, locked, in-use, idle, deleted and orphaned trees.
Scratch, run and extra directories are aged against a fake clock that starts at the
real time. GitHub is a fake that answers, misses or fails.
"""

import json
import re
import tarfile
from pathlib import Path

import pytest

from tests.hexagonal.contracts import devconfig as settings_contract
from tests.hexagonal.contracts import worktrees as contract
from tests.hexagonal.support.git_repositories import DEFAULT_BRANCH, commit, git
from tests.hexagonal.support.worktrees import DAY, Fleet, Workspace, push


@pytest.fixture
def ws(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path, monkeypatch)
    yield workspace
    workspace.close()
    if workspace._git is not None:
        assert contract.forced_git_calls(workspace.git.calls) == [], "the tool forced, discarded or pruned"


@pytest.fixture
def fleet(ws):
    return Fleet(ws)


def report(ws, fleet, *args, **env):
    result = ws.run("report", "--json", *args, env=fleet.env(**env))
    assert result.code == 0, result.text
    return json.loads(result.out)


def by_path(rows):
    return {row["path"]: row for row in rows}


def snapshot(ws, fleet):
    files = sorted(str(path) for root in (ws.root, fleet.scratch, fleet.runs, fleet.extra, ws.state)
                   if root.exists() for path in root.rglob("*"))
    return (git(ws.main, "for-each-ref", "--format=%(refname) %(objectname)"),
            git(ws.main, "worktree", "list", "--porcelain"), files)


# Settings -----------------------------------------------------------------------------------------

def test_the_settings_table_declares_the_maintenance_keys():
    import importlib
    declared = {setting.key: (setting.kind, setting.default, setting.section)
                for setting in importlib.import_module("scripts.devconfig.keys").SETTINGS}
    assert {key: declared.get(key) for key in settings_contract.SLICE_B} == settings_contract.SLICE_B


@pytest.mark.parametrize("key", settings_contract.LIST_KEYS)
@pytest.mark.parametrize("value,code", [('["relative/dir"]', "CONFIG_INVALID"), ('["~user/dir"]', "CONFIG_INVALID"),
                                        ('{"not": "a list"}', "TYPE_INVALID")])
def test_directory_lists_must_be_lists_of_absolute_or_home_paths(ws, key, value, code):
    result = ws.run("report", env=ws.env(**{key: value}))
    assert result.code == 2, result.text
    assert code in result.text and key in result.text, result.text


def test_home_relative_directory_entries_expand_with_the_given_home(ws):
    runs = ws.home / "runs"
    (runs / "one").mkdir(parents=True)
    (runs / "one" / "plan.json").write_text("{}")
    result = ws.run("report", "--json", env=ws.env(XORCHESTRATE_DEV_RUN_ROOTS='["~/runs"]'))
    assert result.code == 0, result.text
    assert str(runs / "one") in by_path(json.loads(result.out)["dirs"])


# report -------------------------------------------------------------------------------------------

EXPECTED = {
    "merged": ("REMOVABLE", set()),
    "squash": ("REMOVABLE", set()),
    "keep": ("KEEP", set()),
    "unpushed": ("NEEDS_LOOK", {"UNPUSHED"}),
    "idle": ("NEEDS_LOOK", {"IDLE"}),
    "dirty": ("NEEDS_LOOK", {"DIRTY"}),
    "stash": ("NEEDS_LOOK", {"STASH"}),
    "locked": ("NEEDS_LOOK", {"LOCKED"}),
    "busy": ("NEEDS_LOOK", {"IN_USE_MERGED"}),
    "missing": ("NEEDS_LOOK", {"MISSING_DIR"}),
    "missing-locked": ("NEEDS_LOOK", {"MISSING_DIR"}),
    "orphan": ("NEEDS_LOOK", {"ORPHAN_DIR"}),
}


def test_report_judges_every_tree_by_the_first_matching_verdict(ws, fleet):
    data = report(ws, fleet)
    assert set(data) == {"trees", "dirs", "totals"}
    rows = by_path(data["trees"])
    main = rows[str(ws.main)]
    assert main["verdict"] == "MAIN"
    for name, (verdict, reasons) in EXPECTED.items():
        row = rows[str(fleet.trees[name])]
        assert set(row) == contract.TREE_KEYS, (name, sorted(row))
        assert row["verdict"] == verdict, (name, row)
        assert reasons <= set(row["reasons"]) <= contract.REASONS, (name, row["reasons"])
    assert rows[str(fleet.trees["squash"])]["merged"] is True
    assert rows[str(fleet.trees["keep"])]["merged"] is False
    assert rows[str(fleet.trees["keep"])]["on_origin"] is True and rows[str(fleet.trees["unpushed"])]["on_origin"] is False
    totals = data["totals"]
    assert set(totals) == contract.TOTAL_KEYS
    assert totals["removable"] == 2
    assert totals["needs_look"] == sum(1 for verdict, _ in EXPECTED.values() if verdict == "NEEDS_LOOK")


@pytest.mark.parametrize("mode", ["offline", "github-fails", "github-misses", "github-other-head"])
def test_without_matching_github_evidence_a_squash_merge_is_unmerged(ws, fleet, mode):
    args = ("--offline",) if mode == "offline" else ()
    if mode == "github-fails":
        ws.github.raises = True
    elif mode == "github-misses":
        ws.github.heads.clear()
    elif mode == "github-other-head":
        ws.github.heads["squash"] = "0" * 40
    row = by_path(report(ws, fleet, *args)["trees"])[str(fleet.trees["squash"])]
    assert row["merged"] is False and row["verdict"] == "KEEP", row
    assert by_path(report(ws, fleet, *args)["trees"])[str(fleet.trees["merged"])]["verdict"] == "REMOVABLE"
    if mode == "offline":
        assert ws.github.calls == [], "--offline must not ask GitHub"


def test_a_recent_call_to_the_trees_server_counts_as_use(ws, fleet):
    idle = fleet.trees["idle"]
    state = ws.seed_server(idle, pid=7_900)
    ws.store().save(ws.key(idle), state.__class__(**{**state.__dict__, "last_call": ws.clock.now() - 60}))
    row = by_path(report(ws, fleet)["trees"])[str(idle)]
    assert row["verdict"] == "KEEP" and row["serena"] == "up", row
    assert row["last_use"] >= ws.clock.now() - 61


def test_sizes_are_split_into_serena_virtualenv_and_build_without_following_links(ws, fleet):
    keep = fleet.trees["keep"]
    outside = ws.base / "big.bin"
    outside.write_bytes(b"x" * 100_000)
    for relative, size in ((".serena/cache/blob", 1000), (".venv/lib/a", 2000), ("venv/b", 300), ("build/c", 40),
                           ("dist/d", 5), (".pytest_cache/e", 6), ("pkg/__pycache__/f.pyc", 7)):
        path = keep / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"y" * size)
    (keep / ".venv" / "link").symlink_to(outside)
    size = by_path(report(ws, fleet)["trees"])[str(keep)]["size"]
    assert set(size) == contract.SIZE_KEYS
    assert (size["serena"], size["venv"], size["build"]) == (1000, 2300, 58), size
    assert 3358 <= size["total"] < 100_000, size


def test_report_lists_each_directory_under_the_scratch_run_and_extra_roots(ws, fleet):
    rows = by_path(report(ws, fleet)["dirs"])
    expected = {"old-marked": ("scratch", True, None, True), "new-marked": ("scratch", True, None, False),
                "busy-marked": ("scratch", True, None, False), "unmarked-old": ("scratch", False, None, False),
                "run-old": ("run", None, True, True), "run-new": ("run", None, True, False),
                "notarun-old": ("run", None, False, False), "extra-old": ("extra", None, None, False),
                "extra-new": ("extra", None, None, False)}
    for name, (kind, marked, recognised, expired) in expected.items():
        row = rows[str(fleet.dirs[name])]
        assert set(row) == contract.DIR_KEYS, (name, sorted(row))
        assert row["kind"] == kind and row["expired"] is expired, (name, row)
        if marked is not None:
            assert row["marked"] is marked, (name, row)
        if recognised is not None:
            assert row["recognised"] is recognised, (name, row)
    assert rows[str(fleet.dirs["busy-marked"])]["in_use"] is True
    assert rows[str(fleet.dirs["old-marked"])]["age_s"] >= 2 * DAY - 1
    assert report(ws, fleet)["totals"]["expired"] == 2


def test_report_changes_nothing(ws, fleet):
    before = snapshot(ws, fleet)
    report(ws, fleet)
    assert snapshot(ws, fleet) == before


# report --brief -----------------------------------------------------------------------------------

def test_brief_is_one_line_when_something_needs_attention(ws, fleet):
    result = ws.run("report", "--brief", env=fleet.env())
    assert result.code == 0
    lines = result.out.strip().splitlines()
    assert len(lines) == 1, result.out
    assert re.search(r"\b2 removable\b", lines[0]) and "report" in lines[0], lines[0]


def test_brief_says_nothing_when_all_is_well(ws):
    ws.new("fresh-but-kept")
    commit(ws.tree("fresh-but-kept"), "w.txt", "w\n")
    push(ws.tree("fresh-but-kept"), "fresh-but-kept")
    result = ws.run("report", "--brief", env=ws.env(XORCHESTRATE_DEV_SCRATCH_ROOT=ws.base / "empty-scratch"))
    assert (result.code, result.out.strip()) == (0, ""), result.text


def test_brief_speaks_up_when_the_total_size_passes_the_threshold(ws):
    result = ws.run("report", "--brief", env=ws.env(XORCHESTRATE_DEV_BRIEF_SIZE_GB="0.000000001",
                                                    XORCHESTRATE_DEV_SCRATCH_ROOT=ws.base / "empty-scratch"))
    assert result.code == 0 and len(result.out.strip().splitlines()) == 1, result.text


def test_brief_never_fails_or_prints_a_traceback(ws, tmp_path):
    not_a_repo = tmp_path / "plain-directory"
    not_a_repo.mkdir()
    result = ws.run("--repo", str(not_a_repo), "report", "--brief", repo=False)
    assert result.code == 0
    assert len(result.text.strip().splitlines()) <= 1 and "Traceback" not in result.text, result.text


# prune --------------------------------------------------------------------------------------------

def test_prune_without_apply_prints_the_plan_and_changes_nothing(ws, fleet):
    before = snapshot(ws, fleet)
    result = ws.run("prune", env=fleet.env())
    assert str(fleet.trees["merged"]) in result.text
    assert snapshot(ws, fleet) == before


def test_prune_apply_removes_only_what_is_safe_and_reports_the_rest(ws, fleet):
    dead = ws.new("dead-server")
    ws.seed_server(dead, pid=7_950)
    ws.processes.kill(7_950)
    result = ws.run("prune", "--apply", env=fleet.env())
    assert result.code == 1, result.text
    trees = fleet.trees
    assert not trees["merged"].exists() and git(ws.main, "branch", "--list", "merged") == ""
    assert not trees["squash"].exists()
    assert git(ws.main, "rev-parse", "refs/heads/squash") == fleet.squash_tip, "a squash-merged branch is kept"
    assert "kept" in result.text.lower()
    for name in ("keep", "unpushed", "idle", "dirty", "stash", "locked", "busy", "orphan"):
        assert trees[name].is_dir(), name
    assert (trees["orphan"] / ".git").is_file()
    assert not fleet.missing_admin["missing"].exists(), "an unlocked, pushed missing tree's registration is dropped"
    assert fleet.missing_admin["missing-locked"].is_dir(), "a locked registration is kept"
    backups = list((ws.state / "backups").glob(f"{fleet.missing_admin['missing'].name}-*.tar"))
    assert len(backups) == 1, list((ws.state / "backups").iterdir())
    with tarfile.open(backups[0]) as archive:
        names = archive.getnames()
    assert f"{fleet.missing_admin['missing'].name}/gitdir" in names and f"{fleet.missing_admin['missing'].name}/HEAD" in names
    dirs = fleet.dirs
    assert not dirs["old-marked"].exists() and not dirs["run-old"].exists()
    for name in ("new-marked", "busy-marked", "unmarked-old", "run-new", "notarun-old", "extra-old", "extra-new"):
        assert dirs[name].exists(), name
    assert ws.store().load(ws.key(dead)) is None, "prune reaps dead servers"
    assert not any(call[:2] == ["worktree", "prune"] for call in ws.git.calls)


def test_prune_with_include_extra_also_clears_old_unused_extra_entries(ws, fleet):
    ws.run("prune", "--apply", "--include-extra", env=fleet.env())
    assert not fleet.dirs["extra-old"].exists()
    assert fleet.dirs["extra-new"].exists()


def test_prune_refuses_when_the_fetch_fails_unless_offline(ws, fleet):
    git(ws.main, "remote", "set-url", "origin", str(ws.base / "gone.git"))
    before = snapshot(ws, fleet)
    result = ws.run("prune", "--apply", env=fleet.env())
    assert result.code == 1 and "FETCH_FAILED" in result.text, result.text
    assert snapshot(ws, fleet) == before
    result = ws.run("prune", "--apply", "--offline", env=fleet.env())
    assert not fleet.trees["merged"].exists(), result.text
    assert fleet.trees["squash"].is_dir(), "offline, a squash merge has no evidence"


@pytest.mark.parametrize("change", ["dirty", "unpushed"])
def test_prune_rechecks_each_tree_just_before_removing_it(ws, fleet, change):
    squash = fleet.trees["squash"]

    def turn():
        if change == "dirty":
            (squash / "late.txt").write_text("late\n")
        else:
            commit(squash, "late.txt", "late\n")

    ws.github.on_call["squash"] = turn
    result = ws.run("prune", "--apply", env=fleet.env())
    assert result.code == 1, result.text
    assert squash.is_dir() and (squash / "late.txt").exists(), "a tree that changed after planning was removed"
    assert not fleet.trees["merged"].exists()


# scratch ------------------------------------------------------------------------------------------

def test_scratch_new_makes_a_marked_stamped_directory_under_the_root(ws):
    root = ws.base / "scratch"
    env = ws.env(XORCHESTRATE_DEV_SCRATCH_ROOT=root, XORCHESTRATE_DEV_OWNER="env-owner")
    result = ws.run("scratch", "new", "probe.run-1", env=env)
    assert result.code == 0, result.text
    path = result.out.strip().splitlines()[-1]
    assert re.fullmatch(rf"{re.escape(str(root))}/probe\.run-1-{contract.STAMP}", path), path
    mark = json.loads((root / path.rsplit("/", 1)[1] / contract.SCRATCH_MARK).read_text())
    assert mark == {"owner": "env-owner", "created": ws.clock.now(), "purpose": "probe.run-1"}
    flagged = ws.run("scratch", "new", "other", "--owner", "flag-owner", env=env).out.strip().splitlines()[-1]
    assert json.loads((root / flagged.rsplit("/", 1)[1] / contract.SCRATCH_MARK).read_text())["owner"] == "flag-owner"
    unowned = ws.run("scratch", "new", "third", env=ws.env(XORCHESTRATE_DEV_SCRATCH_ROOT=root)).out.strip()
    assert json.loads((root / unowned.splitlines()[-1].rsplit("/", 1)[1] / contract.SCRATCH_MARK).read_text())[
        "owner"] is None


@pytest.mark.parametrize("label", ["", ".hidden", "a/b", "-dash", "sp ace"])
def test_scratch_new_refuses_a_bad_label(ws, label):
    root = ws.base / "scratch"
    result = ws.run("scratch", "new", label, env=ws.env(XORCHESTRATE_DEV_SCRATCH_ROOT=root))
    assert result.code == 2 and "BAD_LABEL" in result.text, result.text
    assert not root.exists() or not any(root.iterdir())


def test_scratch_rm_deletes_only_an_unused_marked_directory_directly_under_the_root(ws):
    root = ws.base / "scratch"
    env = ws.env(XORCHESTRATE_DEV_SCRATCH_ROOT=root)
    marked = ws.run("scratch", "new", "gone", env=env).out.strip().splitlines()[-1]
    busy = ws.run("scratch", "new", "busy", env=env).out.strip().splitlines()[-1]
    ws.processes.add(8_950, cwd=busy, argv=["bash"])
    unmarked = root / "unmarked"
    unmarked.mkdir()
    nested = root / "unmarked" / "inner"
    nested.mkdir()
    (nested / contract.SCRATCH_MARK).write_text("{}")
    elsewhere = ws.base / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / contract.SCRATCH_MARK).write_text("{}")
    assert ws.run("scratch", "rm", marked, env=env).code == 0
    assert not (root / marked.rsplit("/", 1)[1]).exists()
    for path, code in ((unmarked, "NOT_SCRATCH"), (nested, "NOT_SCRATCH"), (elsewhere, "NOT_SCRATCH"),
                       (busy, "IN_USE")):
        result = ws.run("scratch", "rm", str(path), env=env)
        assert result.code == 1 and code in result.text, (path, result.text)
        assert Path(path).exists(), path


def test_scratch_ls_lists_each_entry_with_its_mark(ws):
    root = ws.base / "scratch"
    env = ws.env(XORCHESTRATE_DEV_SCRATCH_ROOT=root)
    marked = ws.run("scratch", "new", "listed", env=env).out.strip().splitlines()[-1]
    (root / "loose").mkdir()
    result = ws.run("scratch", "ls", env=env)
    assert result.code == 0, result.text
    rows = {line.split()[0].rstrip("/").rsplit("/", 1)[-1]: line.split()
            for line in result.out.splitlines() if line.strip()}
    assert "yes" in rows[marked.rsplit("/", 1)[1]] and "no" in rows["loose"], rows


def test_the_base_used_for_merged_is_the_remotes_default_branch(ws):
    assert git(ws.main, "symbolic-ref", "refs/remotes/origin/HEAD").endswith(DEFAULT_BRANCH)
    tree = ws.new("landed")
    commit(tree, "x.txt", "x\n")
    push(tree, "landed")
    git(ws.main, "fetch", "-q", "origin")
    git(ws.main, "merge", "-q", "--no-ff", "-m", "land", "origin/landed")
    git(ws.main, "push", "-q", "origin", DEFAULT_BRANCH)
    result = ws.run("report", "--json", env=ws.env(XORCHESTRATE_DEV_SCRATCH_ROOT=ws.base / "s"))
    assert by_path(json.loads(result.out)["trees"])[str(tree)]["verdict"] == "REMOVABLE", result.text
