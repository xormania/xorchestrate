"""One versioned onboarding and skill source for every harness."""

import hashlib
import json
import re
from pathlib import Path

from .control import GAP_REPORTING
from .role_packages import assignable_roles, catalog, descriptor
from .role_packages import role as role_package

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents/skills"



def document(name):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name):
        raise ValueError("Choose a skill from onboard")
    path = (SKILLS / name / "SKILL.md").resolve()
    if not path.is_relative_to(SKILLS.resolve()):
        raise ValueError("Skill source leaves the repository catalog")
    content = path.read_text()
    header, body = content.split("---", 2)[1:]
    # Catalog frontmatter uses plain scalar names and plain/folded descriptions.
    # Keep the runtime stdlib-only; do not interpret arbitrary YAML objects.
    metadata = {}
    field = None
    for line in header.strip().splitlines():
        if line.startswith((" ", "\t")) and field:
            metadata[field] = (metadata[field] + " " + line.strip()).strip()
        elif ":" in line:
            field, value = line.split(":", 1)
            metadata[field] = "" if value.strip() in {">", ">-", "|", "|-"} else value.strip()
    if metadata.get("name") != name or not metadata.get("description"):
        raise ValueError("Invalid skill metadata")
    return {
        "name": name,
        "description": metadata["description"],
        "source": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "content": body.strip(),
    }


class Onboarding:
    def __init__(self, api):
        self.api = api

    def role(self):
        return self.api.commands.role(self.api.control.current())

    def catalog(self):
        role = self.role()
        if role not in catalog()["roles"]:
            return []
        skills = role_package(role)["skills"]
        names = [skills["shared"], skills["role"]]
        tools = {row["name"] for row in self.api.tools()}
        if "framework_inspect" in tools:
            names += skills["framework"]
        entries = []
        for name in names:
            skill = document(name)
            skill["guidance_sha256"] = hashlib.sha256(
                json.dumps(skill, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            entries.append(skill)
        return entries

    def startup(self):
        role = self.role()
        if role not in catalog()["roles"]:
            return "Await an authorized role binding; work_context shows your current state."
        return (
            "Use the role-bound MCP tools. Call onboard once in a new session, after handover or a role change. "
            "It supplies the complete shared/role instructions and current brief. "
            "When it supplies a readiness challenge, call readiness_report with that challenge and onboarding revision "
            "to report for duty, including any startup blockers. "
            "For subsequent work/context changes refresh work_context. "
            "Managed workers use standalone conversations; Agentscient owns their sessions and assignments. "
            "The appointed interactive CO is the operator's native console: its commands, tools, settings, "
            "model, reasoning and exit controls remain the operator's choice. "
            "Read only relevant skills not already supplied by onboard. Your role: " + role + "."
        )

    def onboard(self):
        role = self.role()
        skills = self.catalog()
        if not skills:
            raise ValueError("Await an authorized role binding")
        tools = [row["name"] for row in self.api.tools()]
        package = descriptor(role)
        value = {
            "role": role,
            "responsibility": role_package(role)["responsibility"],
            "package": package,
            "shared": skills[0]["content"],
            "role_instructions": skills[1]["content"],
            "context": self.api.invoke("work_context", {}),
            "capability_gap_reporting": GAP_REPORTING,
            "tools": tools,
            "skills": [
                {"name": row["name"], "description": row["description"], "loaded": index < 2}
                for index, row in enumerate(skills)
            ],
        }
        if "orchestrate" in tools:
            value["assignable_roles"] = [
                {"name": name, "responsibility": role_package(name)["responsibility"]}
                for name in assignable_roles(role)
            ]
        revision = hashlib.sha256(json.dumps(
            [package, [(row["name"], row["guidance_sha256"]) for row in skills],
             tools, value.get("assignable_roles", []), GAP_REPORTING], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        value["revision"] = revision
        self.api.store.event(
            self.api.member,
            "onboarding_delivered",
            role=role,
            revision=revision,
            package=package,
            skills=[row["name"] for row in skills],
        )
        from .duty import onboard

        return onboard(self.api, value)

    def read(self, name):
        skill = next((r for r in self.catalog() if r["name"] == name), None)
        if not skill:
            raise ValueError("Choose a skill from your current onboard catalog")
        self.api.store.event(
            self.api.member, "skill_loaded", role=self.role(), name=name, sha256=skill["sha256"],
            guidance_sha256=skill["guidance_sha256"], source=skill["source"]
        )
        return skill
