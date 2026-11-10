"""Independent expectations for the operator ports: companion records, version control and repository hosting.

Each check receives a subject (a real adapter or a fake) and states the port's
contract from its documented behaviour, not from any implementation.
"""

INVALID_NAMES = ("", ".hidden", "../escape", "a/b", "spaced name")


def companion_records(companion):
    """Project-scoped, extensible records: kinds and keys, project isolation, atomic create-only."""
    assert companion.initialize() is not None
    assert companion.exists(), "an initialized companion exists"
    assert companion.initialize() == [], "initialize never recreates what exists"
    alpha, beta, shared = companion.records("alpha"), companion.records("beta"), companion.records(None)
    assert alpha.project == "alpha" and shared.project is None
    assert alpha.read("overlay", "overlay.json") is None and alpha.keys("overlay") == []
    assert alpha.write("overlay", "overlay.json", b'{"a": 1}') is True
    assert alpha.read("overlay", "overlay.json") == b'{"a": 1}'
    assert beta.read("overlay", "overlay.json") is None, "one project's records are not another's"
    assert shared.read("overlay", "overlay.json") is None, "project records are not project-independent ones"
    assert alpha.write("overlay", "overlay.json", b"kept", create_only=True) is False
    assert alpha.read("overlay", "overlay.json") == b'{"a": 1}'
    assert alpha.write("overlay", "overlay.json", b"replaced") is True
    assert alpha.read("overlay", "overlay.json") == b"replaced"
    for key in ("b.json", "a.json"):
        shared.write("policies", key, b"{}")
    assert shared.keys("policies") == ["a.json", "b.json"]
    # A capability added later is just another kind: no port or adapter change.
    assert alpha.write("snapshot-archive", "bundle-1", b"\x00\x01") is True
    assert alpha.keys("snapshot-archive") == ["bundle-1"] and alpha.read("snapshot-archive", "bundle-1") == b"\x00\x01"
    for name in INVALID_NAMES:
        for call in (lambda: alpha.read(name, "key"), lambda: alpha.write("kind", name, b"x"),
                     lambda: companion.records(name or "/")):
            try:
                call()
            except Exception as error:  # noqa: BLE001 - any refusal satisfies the contract
                assert getattr(error, "code", None) == "invalid_arguments", (name, error)
            else:
                raise AssertionError(f"invalid name {name!r} was accepted")


def version_control(vcs, repository, linked, unrelated_directory):
    """``repository`` and ``linked`` are two worktrees of one repository; ``unrelated_directory`` is in none."""
    main, other = vcs.worktree(str(repository)), vcs.worktree(str(linked))
    assert main is not None and other is not None
    assert main.root == str(repository) and other.root == str(linked)
    assert main.common_dir == other.common_dir, "worktrees of one repository share its common directory"
    assert main.private_dir != other.private_dir
    assert vcs.worktree(str(unrelated_directory)) is None
    roots = vcs.root_commits(str(repository))
    assert roots and roots == vcs.root_commits(str(linked))
    assert vcs.root_commits(str(unrelated_directory)) == frozenset()


def repository_hosting(hosting, private_url, public_url, unanswerable_urls):
    for url, expected in ((private_url, "private"), (public_url, "public")):
        report = hosting.visibility(url)
        assert report.visibility == expected, (url, report)
        assert report.repository, report
    for url in unanswerable_urls:
        report = hosting.visibility(url)
        assert report.visibility == "unknown" and report.reason, (url, report)
