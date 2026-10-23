"""Manifest-sweep tests, written from the slice-1 contract.

Pins `ops/devlane/fixtures/apps/FAULT-KINDS.md` and every
`apps/*/FAULTS.json` found. The kinds file is the subject and is not
written here. A missing kinds file is one red
(`test_fault_kinds_md_exists`). In-memory validation cases use the
contract's nine kind ids, so a missing file is not their failure:
each assertion goes red on the named validation property.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[4]
APPS = REPO / "ops" / "devlane" / "fixtures" / "apps"
KINDS = APPS / "FAULT-KINDS.md"

KIND_IDS = tuple(f"K{i}" for i in range(1, 10))
KIND_TITLES = {
    "K1": "untrusted string reaching a shell or argv",
    "K2": "a path escaping its root",
    "K3": "a loose comparison (false as zero)",
    "K4": "a secret in the environment or a log",
    "K5": "an error swallowed",
    "K6": "a boundary off by one (half-open intervals)",
    "K7": "a test proving against a stub",
    "K8": "an oracle taken from the code under test",
    "K9": "an unpinned or drifted dependency",
}
IDIOMS = ("Python", "PHP", "Rust")
PROBE_SHAPES = ("failing test", "static grep", "runtime check")
TOP_KEYS = ("name", "check", "faults")
FAULT_KEYS = (
    "id",
    "kind",
    "severity",
    "location",
    "declared",
    "practiced",
    "finding",
    "neighbour",
    "probe",
    "fix",
    "visible_to",
)
LOC_KEYS = ("path", "lines")
QUOTE_KEYS = ("text", "path", "lines")
NEIGHBOUR_KEYS = ("path", "lines", "why_clean")
SEVERITIES = {"p1", "p2", "p3"}
VISIBLE = {"test", "audit", "both"}
ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d{2}$")
KIND_TOKEN = re.compile(r"\bK[1-9]\b")


def _plant_bytes(world: World, path: Path, data: bytes) -> None:
    world.plant_bytes(path, data)


def _span_text(path: Path, lines: list[int]) -> str:
    start, end = lines
    body = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(body[start - 1 : end])


def _as_lines(value) -> list[int] | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        return [value[0], value[1]]
    return None


def _id_prefix_ok(name: str, fid: str) -> bool:
    if not isinstance(fid, str) or not ID_RE.match(fid):
        return False
    prefix = fid.rsplit("-", 1)[0]
    compact = re.sub(r"[^A-Za-z0-9]+", "", name).upper()
    if compact.startswith(prefix):
        return True
    parts = [part for part in re.split(r"[-_\s]+", name) if part]
    initialism = "".join(part[0] for part in parts).upper()
    return prefix == initialism


def _resolved_under(app_root: Path, rel: str) -> Path | None:
    if not isinstance(rel, str) or not rel.strip():
        return None
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (app_root / candidate).resolve()
    try:
        resolved.relative_to(app_root.resolve())
    except ValueError:
        return None
    return resolved


def _spans_overlap(path_a, lines_a, path_b, lines_b) -> bool:
    if path_a != path_b:
        return False
    span_a = _as_lines(lines_a)
    span_b = _as_lines(lines_b)
    if span_a is None or span_b is None:
        return False
    return span_a[0] <= span_b[1] and span_b[0] <= span_a[1]


def _probe_in_probes(app_root: Path, command: str) -> list[str]:
    problems: list[str] = []
    tokens = command.split()
    probes_root = (app_root / "probes").resolve()
    found = False
    for token in tokens:
        if token.startswith("-"):
            continue
        if "/" not in token and not token.startswith("probes"):
            continue
        resolved = _resolved_under(app_root, token)
        if resolved is None:
            problems.append(f"probe path {token} escapes the app root")
            continue
        try:
            resolved.relative_to(probes_root)
        except ValueError:
            problems.append(f"probe path {token} is not under probes/")
            continue
        if not resolved.is_file():
            problems.append(f"probe path {token} is missing")
            continue
        found = True
    if not found and not problems:
        problems.append("probe is not a command under probes/")
    return problems


def parse_kind_rows(text: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        kid = cells[0]
        if kid in KIND_IDS:
            rows[kid] = stripped
    return rows


def validate_manifest(app_root: Path, kind_ids: set[str]) -> list[str]:
    """Return problems; empty means the manifest satisfies the sweep."""
    problems: list[str] = []
    manifest_path = app_root / "FAULTS.json"
    if not manifest_path.is_file():
        return [f"{manifest_path} is missing"]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{manifest_path} is not JSON: {exc}"]
    if not isinstance(data, dict):
        return [f"{manifest_path} is not an object"]
    for key in TOP_KEYS:
        if key not in data:
            problems.append(f"missing top-level key {key}")
    if problems:
        return problems
    if not isinstance(data["name"], str) or not data["name"].strip():
        problems.append("name must be a non-empty string")
    elif data["name"] != app_root.name:
        problems.append(
            f"name {data['name']!r} does not match directory {app_root.name!r}"
        )
    if not isinstance(data["check"], str) or not data["check"].strip():
        problems.append("check must be a non-empty command")
    faults = data.get("faults")
    if not isinstance(faults, list):
        problems.append("faults must be a list")
        return problems
    n_faults = len(faults)
    if n_faults < 6 or n_faults > 8:
        problems.append(f"need six to eight faults, found {n_faults}")
    ids: list[str] = []
    kinds_seen: set[str] = set()
    for index, fault in enumerate(faults, 1):
        prefix = f"fault[{index}]"
        if not isinstance(fault, dict):
            problems.append(f"{prefix} is not an object")
            continue
        for key in FAULT_KEYS:
            if key not in fault:
                problems.append(f"{prefix} missing {key}")
        if any(key not in fault for key in FAULT_KEYS):
            continue
        fid = fault["id"]
        if not isinstance(fid, str) or not ID_RE.match(fid):
            problems.append(f"{prefix} id {fid!r} is not <APP>-<nn>")
        elif not _id_prefix_ok(str(data.get("name", "")), fid):
            problems.append(
                f"{prefix} id {fid!r} prefix does not match app {data.get('name')!r}"
            )
        else:
            ids.append(fid)
        kind = fault["kind"]
        if kind not in kind_ids:
            problems.append(f"{prefix} kind {kind!r} does not resolve")
        else:
            kinds_seen.add(kind)
        if fault["severity"] not in SEVERITIES:
            problems.append(f"{prefix} severity {fault['severity']!r}")
        if fault["visible_to"] not in VISIBLE:
            problems.append(f"{prefix} visible_to {fault['visible_to']!r}")
        if not isinstance(fault["finding"], str) or not fault["finding"].strip():
            problems.append(f"{prefix} finding is empty")
        if not isinstance(fault["probe"], str) or not fault["probe"].strip():
            problems.append(f"{prefix} probe is empty")
        else:
            for item in _probe_in_probes(app_root, fault["probe"]):
                problems.append(f"{prefix} {item}")
        fix_resolved = _resolved_under(app_root, str(fault["fix"]))
        if fix_resolved is None:
            problems.append(f"{prefix} fix {fault['fix']} escapes the app root")
        elif not fix_resolved.is_file():
            problems.append(f"{prefix} fix {fault['fix']} is missing")
        problems.extend(
            _validate_loc(app_root, fault["location"], f"{prefix}.location")
        )
        problems.extend(
            _validate_quote(app_root, fault["declared"], f"{prefix}.declared")
        )
        problems.extend(
            _validate_quote(app_root, fault["practiced"], f"{prefix}.practiced")
        )
        problems.extend(
            _validate_neighbour(
                app_root, fault["neighbour"], f"{prefix}.neighbour"
            )
        )
        if (
            isinstance(fault["location"], dict)
            and isinstance(fault["neighbour"], dict)
            and _spans_overlap(
                fault["location"].get("path"),
                fault["location"].get("lines"),
                fault["neighbour"].get("path"),
                fault["neighbour"].get("lines"),
            )
        ):
            problems.append(f"{prefix} location overlaps neighbour")
    if len(ids) != len(set(ids)):
        problems.append(f"fault ids are not unique: {ids}")
    if n_faults >= 6 and len(kinds_seen) < 6:
        problems.append(
            f"need six or more distinct kinds, found {sorted(kinds_seen)}"
        )
    return problems


def _validate_loc(app_root: Path, loc, label: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(loc, dict):
        return [f"{label} is not an object"]
    for key in LOC_KEYS:
        if key not in loc:
            problems.append(f"{label} missing {key}")
    if problems:
        return problems
    return _validate_file_span(app_root, loc["path"], loc["lines"], label)


def _validate_quote(app_root: Path, quote, label: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(quote, dict):
        return [f"{label} is not an object"]
    for key in QUOTE_KEYS:
        if key not in quote:
            problems.append(f"{label} missing {key}")
    if problems:
        return problems
    text = quote["text"]
    if not isinstance(text, str) or not text.strip():
        problems.append(f"{label} text is empty")
        return problems
    problems.extend(
        _validate_file_span(app_root, quote["path"], quote["lines"], label)
    )
    if problems:
        return problems
    resolved = _resolved_under(app_root, quote["path"])
    if resolved is None:
        return problems
    span = _span_text(resolved, quote["lines"])
    if text not in span:
        problems.append(
            f"{label} quote is not present verbatim at the stated span"
        )
    return problems


def _validate_neighbour(app_root: Path, neighbour, label: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(neighbour, dict):
        return [f"{label} is not an object"]
    for key in NEIGHBOUR_KEYS:
        if key not in neighbour:
            problems.append(f"{label} missing {key}")
    if problems:
        return problems
    if (
        not isinstance(neighbour["why_clean"], str)
        or not neighbour["why_clean"].strip()
    ):
        problems.append(f"{label} why_clean is empty")
    problems.extend(
        _validate_file_span(
            app_root, neighbour["path"], neighbour["lines"], label
        )
    )
    return problems


def _validate_file_span(app_root: Path, rel, lines, label: str) -> list[str]:
    if not isinstance(rel, str) or not rel.strip():
        return [f"{label} path is empty"]
    resolved = _resolved_under(app_root, rel)
    if resolved is None:
        return [f"{label} path {rel} escapes the app root"]
    if not resolved.is_file():
        return [f"{label} path {rel} does not exist"]
    span = _as_lines(lines)
    if span is None:
        return [f"{label} lines must be [start, end]"]
    start, end = span
    n_lines = len(resolved.read_text(encoding="utf-8").splitlines())
    if start < 1 or end < start or end > n_lines:
        return [f"{label} span {start}-{end} is outside 1-{n_lines}"]
    return []


class FaultKindsExist(unittest.TestCase):
    def test_fault_kinds_md_exists(self):
        self.assertTrue(
            KINDS.is_file(),
            f"{KINDS} is missing; the contract requires FAULT-KINDS.md",
        )

    def test_each_kind_row_maps_id_to_title_idiom_and_probe_shape(self):
        text = KINDS.read_text(encoding="utf-8") if KINDS.is_file() else ""
        rows = parse_kind_rows(text)
        self.assertEqual(
            set(rows),
            set(KIND_IDS),
            "FAULT-KINDS.md must have one table row per kind id",
        )
        for kind_id, title in KIND_TITLES.items():
            with self.subTest(kind=kind_id):
                row = rows[kind_id]
                self.assertIn(title, row, f"{kind_id} title missing from its row")
                self.assertTrue(
                    any(idiom in row for idiom in IDIOMS),
                    f"{kind_id} row must name an idiom: {row!r}",
                )
                self.assertTrue(
                    any(shape in row for shape in PROBE_SHAPES),
                    f"{kind_id} row must name a probe shape: {row!r}",
                )


class ManifestSweep(unittest.TestCase):
    def test_every_found_manifest_validates(self):
        self.assertTrue(
            KINDS.is_file(),
            f"{KINDS} is missing; the sweep requires FAULT-KINDS.md",
        )
        kind_ids = set(KIND_TOKEN.findall(KINDS.read_text(encoding="utf-8")))
        manifests = sorted(APPS.glob("*/FAULTS.json"))
        for path in manifests:
            with self.subTest(app=path.parent.name):
                problems = validate_manifest(path.parent, kind_ids)
                self.assertEqual(problems, [], problems)

    def test_a_manifest_missing_a_required_field_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-missing-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            del payload["faults"][0]["finding"]
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("finding" in item for item in problems),
                problems,
            )

    def test_a_manifest_with_an_unknown_kind_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-kind-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["kind"] = "K99"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(any("K99" in item for item in problems), problems)

    def test_a_manifest_with_an_empty_quote_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-quote-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["declared"]["text"] = "   "
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("declared" in item and "empty" in item for item in problems),
                problems,
            )

    def test_a_quote_missing_from_its_stated_span_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-span-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["practiced"]["text"] = "THIS QUOTE IS ABSENT"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("verbatim" in item for item in problems),
                problems,
            )

    def test_a_missing_probe_or_fix_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-probe-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            (app / "probes" / "p01.sh").unlink()
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(any("probe" in item for item in problems), problems)
        with World(prefix="manifest-fix-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            (app / "fixes" / "p01.patch").unlink()
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(any("fix" in item for item in problems), problems)

    def test_duplicate_ids_too_few_rows_and_too_few_kinds_are_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-ids-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][1]["id"] = payload["faults"][0]["id"]
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(any("unique" in item for item in problems), problems)
        with World(prefix="manifest-count-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"] = payload["faults"][:2]
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("six to eight" in item for item in problems),
                problems,
            )
        with World(prefix="manifest-kinds-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            for fault in payload["faults"]:
                fault["kind"] = "K1"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("distinct kinds" in item for item in problems),
                problems,
            )

    def test_a_span_past_eof_or_a_missing_neighbour_file_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-eof-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["location"]["lines"] = [1, 999]
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(any("outside" in item for item in problems), problems)
        with World(prefix="manifest-neigh-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["neighbour"]["path"] = "no-such-neigh.py"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("no-such-neigh.py" in item for item in problems),
                problems,
            )

    def test_name_must_match_the_app_directory(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-name-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["name"] = "not-the-directory-name"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("does not match directory" in item for item in problems),
                problems,
            )

    def test_wrong_id_prefix_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-prefix-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["id"] = "WRONG-01"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("prefix" in item for item in problems),
                problems,
            )

    def test_a_path_escaping_the_app_root_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-escape-") as world:
            root = world.root
            app = self._plant_valid_app(world, root / "tmpapp")
            outside = root / "outside.py"
            _plant_bytes(world, outside, b"OUTSIDE\n")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["location"]["path"] = "../outside.py"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("escapes the app root" in item for item in problems),
                problems,
            )

    def test_overlapping_location_and_neighbour_are_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-overlap-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            loc = payload["faults"][0]["location"]
            payload["faults"][0]["neighbour"]["path"] = loc["path"]
            payload["faults"][0]["neighbour"]["lines"] = list(loc["lines"])
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("overlaps neighbour" in item for item in problems),
                problems,
            )

    def test_a_probe_outside_probes_is_refused(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-probe-cmd-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["probe"] = "false"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("probes/" in item for item in problems),
                problems,
            )
        with World(prefix="manifest-probe-src-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            payload = json.loads((app / "FAULTS.json").read_text())
            payload["faults"][0]["probe"] = "python3 src/mod.py"
            planted = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            _plant_bytes(world, app / "FAULTS.json", planted)
            problems = validate_manifest(app, kind_ids)
            self.assertTrue(
                any("not under probes/" in item for item in problems),
                problems,
            )

    def test_a_valid_six_row_manifest_has_no_problems(self):
        kind_ids = set(KIND_IDS)
        with World(prefix="manifest-ok-") as world:
            app = self._plant_valid_app(world, world.root / "tmpapp")
            problems = validate_manifest(app, kind_ids)
            self.assertEqual(problems, [], problems)

    def _plant_valid_app(self, world: World, app: Path) -> Path:
        readme_lines = ["# tmpapp", ""]
        src_lines = ["# src"]
        faults = []
        for index in range(1, 7):
            declared = f"DECLARED_QUOTE_TMP_{index:02d}."
            practiced = f"PRACTICED_QUOTE_TMP_{index:02d}"
            neighbour = f"NEIGHBOUR_CLEAN_TMP_{index:02d}"
            readme_lines.append(declared)
            src_lines.append(practiced)
            src_lines.append(neighbour)
            readme_path = "README.md"
            src_path = "src/mod.py"
            dec_line = len(readme_lines)
            prac_line = len(src_lines) - 1
            neigh_line = len(src_lines)
            probe_rel = f"probes/p{index:02d}.sh"
            fix_rel = f"fixes/p{index:02d}.patch"
            _plant_bytes(world, app / probe_rel, b"#!/bin/sh\nexit 1\n")
            _plant_bytes(world, app / fix_rel, b"dummy patch\n")
            faults.append(
                {
                    "id": f"TMP-{index:02d}",
                    "kind": f"K{index}",
                    "severity": "p2",
                    "location": {
                        "path": src_path,
                        "lines": [prac_line, prac_line],
                    },
                    "declared": {
                        "text": declared,
                        "path": readme_path,
                        "lines": [dec_line, dec_line],
                    },
                    "practiced": {
                        "text": practiced,
                        "path": src_path,
                        "lines": [prac_line, prac_line],
                    },
                    "finding": f"tmpapp plants {index} as a quotation pair",
                    "neighbour": {
                        "path": src_path,
                        "lines": [neigh_line, neigh_line],
                        "why_clean": f"{neighbour} has no fault",
                    },
                    "probe": f"sh {probe_rel}",
                    "fix": fix_rel,
                    "visible_to": "audit" if index % 2 else "test",
                }
            )
        _plant_bytes(
            world,
            app / "README.md",
            ("\n".join(readme_lines) + "\n").encode("utf-8"),
        )
        _plant_bytes(
            world,
            app / "src" / "mod.py",
            ("\n".join(src_lines) + "\n").encode("utf-8"),
        )
        manifest = {
            "name": app.name,
            "check": "python3 -c 'print(0)'",
            "faults": faults,
        }
        planted = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        _plant_bytes(world, app / "FAULTS.json", planted)
        loaded = json.loads((app / "FAULTS.json").read_text(encoding="utf-8"))
        self.assertEqual(len(loaded["faults"]), 6)
        self.assertEqual(loaded["name"], app.name)
        return app


if __name__ == "__main__":
    unittest.main()
