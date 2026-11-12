"""Shared agent guidance reaches every harness from one source, and nothing it publishes is local.

The checks read a repository through a view (``exists``, ``is_dir``, ``listdir``, ``text``, ``link``,
``files_under``) supplied by a support adapter, so the same contract judges the real repository and
small planted trees. Each check returns findings; an empty list means the behaviour holds. Skill names
are discovered, never listed here.
"""

import re

SKILLS = ".agents/skills"
MIRROR = ".claude/skills"
WORKFLOW = "docs/agent-workflow.md"
SERENA = ".serena/project.yml"
SERENA_IGNORED = (".serena/cache/", ".serena/memories/")
RULES = "AGENTS.md"
IMPORTER = "CLAUDE.md"
SECTION = "Agent workflow"

BEHAVIOURS = {
    "B1": "every skill folder holds a SKILL.md whose front matter names the folder and has a one-line description",
    "B2": "every skill is mirrored by a relative symlink to the same folder, and the mirror has nothing else",
    "B3": "every repository path the skills and the workflow guidance point at exists",
    "B4": "the skills and the workflow guidance contain no local paths, session identifiers or foreign emails",
    "B5": "the Serena project selects the python language server, the tools interface and .gitignore; its caches are ignored",
    "B6": "the importing instruction file loads the rules, and the rules' Agent workflow section points at the skills",
}

# Planted faults: name -> (the behaviour that must catch it, text its finding must name, other behaviours the
# same change genuinely breaks). Every other behaviour must stay clean.
FAULTS = {
    "missing-skill-file": ("B1", ".agents/skills/beta/SKILL.md"),
    "name-mismatch": ("B1", ".agents/skills/beta"),
    "empty-description": ("B1", ".agents/skills/beta"),
    "multi-line-description": ("B1", ".agents/skills/beta"),
    "missing-mirror": ("B2", ".claude/skills/beta"),
    "copy-instead-of-symlink": ("B2", ".claude/skills/beta"),
    "absolute-symlink": ("B2", ".claude/skills/beta"),
    "symlink-escapes-repository": ("B2", ".claude/skills/beta"),
    "symlink-to-another-skill": ("B2", ".claude/skills/beta"),
    "orphan-mirror-entry": ("B2", ".claude/skills/gamma"),
    "dead-link-pointer": ("B3", "docs/missing.md"),
    "dead-backtick-pointer": ("B3", "scripts/missing.py"),
    "renamed-document": ("B3", "docs/guide.md", ("B6",)),
    "workflow-guidance-missing": ("B3", WORKFLOW, ("B6",)),
    "home-path": ("B4", "/home/"),
    "temp-path": ("B4", "/tmp/"),
    "windows-mount-path": ("B4", "/mnt/c/"),
    "session-identifier": ("B4", "session"),
    "session-line": ("B4", "session"),
    "foreign-email": ("B4", "someone@example.com"),
    "broken-project-yml": ("B5", SERENA),
    "wrong-language": ("B5", "python"),
    "gitignore-not-honoured": ("B5", "ignore_all_files_in_gitignore"),
    "cache-not-ignored": ("B5", ".serena/cache/"),
    "memories-not-ignored": ("B5", ".serena/memories/"),
    "agent-interface-missing": ("B5", "agent_interface"),
    "agent-interface-not-tools": ("B5", "agent_interface"),
    "link-but-no-import": ("B6", IMPORTER),
    "section-missing": ("B6", SECTION),
    "section-without-skills": ("B6", SKILLS),
    "section-points-at-missing-document": ("B6", "docs/vanished.md"),
}


def check(view, behaviour):
    return CHECKS[behaviour](view)


def require(view, behaviour):
    found = check(view, behaviour)
    assert not found, f"{behaviour} ({BEHAVIOURS[behaviour]}) does not hold:\n- " + "\n- ".join(found)


# B1 -------------------------------------------------------------------------------------------------

def front_matter(text):
    """``{key: (value, single_line)}`` from a leading ``---`` block of ``key: value`` lines, or None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        match = re.fullmatch(r"([A-Za-z_][\w-]*):\s*(.*)", line)
        if match:
            key = match.group(1)
            value = match.group(2).strip()
            fields[key] = (value, value not in ("|", ">", "|-", ">-", "|+", ">+"))
        elif line.strip() and key is not None:
            fields[key] = (fields[key][0], False)  # an indented continuation: not one line
    return None


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def well_formed_skills(view):
    names = view.listdir(SKILLS)
    if names is None:
        return [f"{SKILLS} does not exist"]
    if not names:
        return [f"{SKILLS} holds no skill"]
    found = []
    for name in names:
        folder = f"{SKILLS}/{name}"
        if not view.is_dir(folder):
            found.append(f"{folder} is not a skill folder")
            continue
        text = view.text(f"{folder}/SKILL.md")
        if text is None:
            found.append(f"{folder}/SKILL.md does not exist")
            continue
        fields = front_matter(text)
        if fields is None:
            found.append(f"{folder}/SKILL.md has no front matter block")
            continue
        declared = _unquote(fields.get("name", ("", True))[0])
        if declared != name:
            found.append(f"{folder}/SKILL.md declares name {declared!r}, not its folder name {name!r}")
        description, single = fields.get("description", ("", True))
        if not _unquote(description).strip() or not single:
            found.append(f"{folder}/SKILL.md needs a non-empty one-line description")
    return found


# B2 -------------------------------------------------------------------------------------------------

def mirrored_skills(view):
    names = view.listdir(SKILLS)
    if names is None:
        return [f"nothing to mirror: {SKILLS} does not exist"]
    found = []
    for name in names:
        entry, source = f"{MIRROR}/{name}", f"{SKILLS}/{name}"
        link = view.link(entry)
        if not link["present"]:
            found.append(f"{entry} is missing")
        elif not link["symlink"]:
            found.append(f"{entry} is a copy, not a symlink to {source}")
        elif link["target"].startswith("/"):
            found.append(f"{entry} is an absolute symlink ({link['target']}); it must be relative")
        elif link["resolved"] is None:
            found.append(f"{entry} resolves outside the repository ({link['target']})")
        elif link["resolved"] != source:
            found.append(f"{entry} resolves to {link['resolved']}, not {source}")
    for entry in view.listdir(MIRROR) or []:
        if entry not in names:
            found.append(f"{MIRROR}/{entry} has no source in {SKILLS}")
    return found


# B3 and B6 pointers ---------------------------------------------------------------------------------

LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
TICKED = re.compile(r"`([^`\n]+)`")
PATHLIKE = re.compile(r"[A-Za-z0-9_.@-][A-Za-z0-9_./@-]*")


def normalise(path):
    """A repository-relative path without ``.`` or ``..`` parts, or None when it climbs out."""
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _directory(path):
    return path.rsplit("/", 1)[0] if "/" in path else ""


def ignored_lines(view):
    lines = []
    for line in (view.text(".gitignore") or "").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "!")):
            lines.append(line.lstrip("/"))
    return lines


def _ignored(path, ignored):
    for line in ignored:
        directory = line if line.endswith("/") else line + "/"
        if path == line or path.rstrip("/") == line.rstrip("/") or path.startswith(directory):
            return True
    return False


def references(text, source):
    """``(written, candidates)`` for each markdown link and backticked repository path in ``text``."""
    base = _directory(source)
    for match in LINK.finditer(text):
        target = match.group(1)
        if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith("#"):
            continue  # a URL or a same-page anchor
        target = re.split(r"[#?]", target, maxsplit=1)[0]
        if target:
            joined = target.lstrip("/") if target.startswith("/") else f"{base}/{target}"
            yield target, [normalise(joined)]
    for match in TICKED.finditer(text):
        written = match.group(1).strip()
        if not PATHLIKE.fullmatch(written) or written.startswith(("~", "/", "-", "@")) or "/" not in written:
            continue  # not a repository path: a command, a glob, a placeholder or a bare file name
        yield written, [normalise(written), normalise(f"{base}/{written}")]


def dead_pointers(view, text, source, ignored):
    found = []
    for written, candidates in references(text, source):
        if _ignored(written.lstrip("/"), ignored):
            continue  # names something .gitignore keeps out of the repository
        if not any(candidate is not None and view.exists(candidate) for candidate in candidates):
            found.append(f"{source} points at {written}, which does not exist in the repository")
    return found


def skill_files(view):
    return [f"{SKILLS}/{name}/SKILL.md" for name in view.listdir(SKILLS) or []
            if view.text(f"{SKILLS}/{name}/SKILL.md") is not None]


def resolving_pointers(view):
    found = [] if view.text(WORKFLOW) is not None else [f"{WORKFLOW} does not exist"]
    ignored = ignored_lines(view)
    for source in [*skill_files(view), WORKFLOW]:
        text = view.text(source)
        if text is not None:
            found.extend(dead_pointers(view, text, source, ignored))
    return found


# B4 -------------------------------------------------------------------------------------------------

LOCAL = (
    ("a home path", re.compile(r"(?<![\w.])(?:/home/|/Users/|/root/)[^\s`)\"']+")),
    ("a temp path", re.compile(r"(?<![\w.])(?:/tmp/|/var/tmp/|/var/folders/|/private/var/|/run/user/)[^\s`)\"']+")),
    ("a Windows mount path", re.compile(r"(?<![\w.])/mnt/[A-Za-z](?:/[^\s`)\"']*)?(?![\w])|\b[A-Za-z]:\\")),
    ("a session identifier", re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("a session line or link", re.compile(r"(?im)^\s*[\w-]*-Session:|/code/session_\w+")),
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def published_files(view):
    return [path for path in [*view.files_under(SKILLS), WORKFLOW] if view.text(path) is not None]


def nothing_local(view):
    sources = published_files(view)
    if not sources:
        return [f"nothing to check: no files under {SKILLS} and no {WORKFLOW}"]
    allowed = {email for email in EMAIL.findall(view.text(RULES) or "") if "noreply" in email}
    found = []
    for source in sources:
        text = view.text(source)
        for kind, pattern in LOCAL:
            for match in pattern.finditer(text):
                found.append(f"{source} contains {kind}: {match.group(0).strip()}")
        for email in EMAIL.findall(text):
            if email not in allowed:
                found.append(f"{source} contains an email address other than the public author address: {email}")
    return found


# B5 -------------------------------------------------------------------------------------------------

class _Invalid(Exception):
    pass


def _scalar(value):
    value = value.strip()
    if value.startswith("["):
        if not value.endswith("]"):
            raise _Invalid(value)
        return [_scalar(item) for item in value[1:-1].split(",") if item.strip()]
    if value.startswith("{"):
        if not value.endswith("}"):
            raise _Invalid(value)
        return {}
    if value[:1] in "\"'":
        if len(value) < 2 or value[-1] != value[0]:
            raise _Invalid(value)
        return value[1:-1]
    return {"true": True, "false": False, "null": None, "~": None}.get(value.lower(), value)


def _uncomment(line):
    quote = None
    for index, character in enumerate(line):
        if quote:
            quote = None if character == quote else quote
        elif character in "\"'":
            quote = character
        elif character == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


BLOCK_SCALAR = re.compile(r"[|>][+-]?[1-9]?|[|>][1-9][+-]?")


def project_settings(text):
    """Top-level settings of a project file in the YAML subset Serena writes, or None when it does not parse.

    Top-level ``key: scalar``, ``key: [flow, list]``, a ``key: |`` or ``key: >`` block scalar (its indented
    lines taken as text), and ``key:`` followed by ``- item`` lines or an indented mapping (kept opaque;
    indented settings under a list item belong to that item). Tabs in indentation, a line that is not a
    setting, an indented line with no setting above it and unterminated quotes or brackets do not parse.
    Anchors, aliases and nested values are not supported.
    """
    settings, key, block = {}, None, None
    try:
        for raw in text.splitlines():
            if block is not None:
                if not raw.strip() or raw[:1] == " ":
                    block.append(raw.strip())
                    continue
                settings[key], block = "\n".join(block).strip(), None
            line = _uncomment(raw).rstrip()
            if not line.strip():
                continue
            indentation = line[:len(line) - len(line.lstrip())]
            if "\t" in indentation:
                return None
            if not indentation and not line.startswith("- "):
                match = re.fullmatch(r"([A-Za-z_][\w-]*):(?:\s+(.*))?", line)
                if not match:
                    return None
                key, value = match.group(1), match.group(2)
                if value is not None and BLOCK_SCALAR.fullmatch(value.strip()):
                    settings[key], block = "", []
                else:
                    settings[key] = None if value is None else _scalar(value)
                continue
            if key is None:
                return None
            item = line.strip()
            if item.startswith("- ") or item == "-":
                if settings[key] is None:
                    settings[key] = []
                if not isinstance(settings[key], list):
                    return None
                settings[key].append(_scalar(item[1:]) if item[1:].strip() else None)
            elif re.fullmatch(r"[\w.-]+:(?:\s+.*)?", item):
                if settings[key] is None:
                    settings[key] = {}
                if not isinstance(settings[key], (dict, list)):
                    return None
            else:
                return None
        if block is not None:
            settings[key] = "\n".join(block).strip()
    except _Invalid:
        return None
    return settings


def _listed(value):
    return value if isinstance(value, list) else [] if value is None else [value]


def serena_project(view):
    found = []
    text = view.text(SERENA)
    if text is None:
        found.append(f"{SERENA} does not exist")
    else:
        settings = project_settings(text)
        if settings is None:
            found.append(f"{SERENA} does not parse")
        else:
            languages = [item for name in ("language_servers", "languages", "language")
                         for item in _listed(settings.get(name))]
            if "python" not in languages:
                found.append(f"{SERENA} does not select the python language server (found: {languages})")
            if settings.get("ignore_all_files_in_gitignore") is not True:
                found.append(f"{SERENA} does not set ignore_all_files_in_gitignore: true")
            if settings.get("agent_interface") != "tools":
                found.append(f"{SERENA} does not set agent_interface: tools "
                             f"(found: {settings.get('agent_interface')!r}); other interfaces hide the symbol tools")
    ignored = ignored_lines(view)
    for path in SERENA_IGNORED:
        if not any(line.rstrip("/") == path.rstrip("/") for line in ignored):
            found.append(f".gitignore does not ignore {path}")
    return found


# B6 -------------------------------------------------------------------------------------------------

def section(text, title):
    """The body of the first heading named ``title`` up to the next heading of its level or above."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(#{1,6})\s+(.*?)\s*#*\s*", line)
        if match and match.group(2).strip().lower() == title.lower():
            level = len(match.group(1))
            body = []
            for following in lines[index + 1:]:
                heading = re.match(r"(#{1,6})\s", following)
                if heading and len(heading.group(1)) <= level:
                    break
                body.append(following)
            return "\n".join(body)
    return None


def rules_reach_every_harness(view):
    found = []
    importer = view.text(IMPORTER)
    if importer is None:
        found.append(f"{IMPORTER} does not exist")
    elif not any(line.strip() == f"@{RULES}" for line in importer.splitlines()):
        linked = f"]({RULES})" in importer
        found.append(f"{IMPORTER} has no line @{RULES} importing the rules"
                     + (" (a markdown link alone does not load them)" if linked else ""))
    rules = view.text(RULES)
    body = None if rules is None else section(rules, SECTION)
    if body is None:
        found.append(f"{RULES} has no '{SECTION}' section")
        return found
    if SKILLS not in body:
        found.append(f"{RULES} '{SECTION}' section does not name {SKILLS} as where the skills live")
    written = [target for target, _ in references(body, RULES)]
    if WORKFLOW not in [target.lstrip("/") for target in written]:
        found.append(f"{RULES} '{SECTION}' section does not point at {WORKFLOW}")
    found.extend(dead_pointers(view, body, RULES, ignored_lines(view)))
    return found


CHECKS = {
    "B1": well_formed_skills,
    "B2": mirrored_skills,
    "B3": resolving_pointers,
    "B4": nothing_local,
    "B5": serena_project,
    "B6": rules_reach_every_harness,
}
