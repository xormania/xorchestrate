"""Owned Git workspace preparation with literal arguments and inert Git behavior.

Only configured workspaces are addressable. Workspace processing receives a
small credential-free environment. Repository hooks, filters, merge drivers,
credential helpers and external diff programs cannot run during these commands.
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ..model import OrchestrationError
from ..outcomes import before_effect
from .github import repository_of
from .process import positive_number


class GitPRWorkspace:
    def __init__(self, options=None, *, credential=None, hosting_host="github.com"):
        options = options or {}
        if not isinstance(options, dict) or options.keys() - {"workspaces", "executable", "timeout", "author_name", "author_email"}:
            raise OrchestrationError("Invalid PR workspace options.", "invalid_configuration")
        self.workspaces = options.get("workspaces", {})
        self.executable = shutil.which(options.get("executable", "git"))
        self.timeout = positive_number(options, "timeout", 60)
        self.author_name = options.get("author_name", "xormania")
        self.author_email = options.get("author_email", "127287135+xormania@users.noreply.github.com")
        self.credential, self.hosting_host = credential, hosting_host
        if not self.executable or not isinstance(self.workspaces, dict):
            raise OrchestrationError("PR preparation requires Git and configured owned workspaces.", "invalid_configuration")
        for name, item in self.workspaces.items():
            if (not isinstance(name, str) or not isinstance(item, dict) or item.keys() - {"path", "remote", "repository"}
                    or not isinstance(item.get("path"), str) or not Path(item["path"]).is_absolute()
                    or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", item.get("repository", ""))
                    or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", item.get("remote", "origin"))):
                raise OrchestrationError("Invalid owned PR workspace.", "invalid_configuration")

    def _selected(self, name):
        with before_effect():
            if name not in self.workspaces:
                raise OrchestrationError("Select a configured owned workspace.", "pr_workspace_unknown")
            item = self.workspaces[name]
            root = Path(item["path"]).resolve()
            if not root.is_dir() or self._git(root, "rev-parse", "--show-toplevel").strip() != str(root):
                raise OrchestrationError("The configured workspace root changed.", "pr_workspace_mismatch")
            return item, root

    def _environment(self):
        # Neither token variables nor proxy credential variables are inherited.
        return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email, "GIT_COMMITTER_NAME": self.author_name,
            "GIT_COMMITTER_EMAIL": self.author_email}

    def _git(self, root, *arguments, check=True, binary=False, index_file=None):
        inert = ["-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false", "-c", "credential.helper=", "-c", "core.sshCommand=false",
            "-c", "protocol.ext.allow=never", "-c", "core.fsmonitor=false"]
        # Reading these configuration names does not execute their values.
        configured = subprocess.run([self.executable, "config", "--null", "--name-only", "--get-regexp",
            r"^(filter\..*\.(clean|smudge|process|required)|merge\..*\.driver|diff\..*\.(command|textconv))$"],
            cwd=root, env=self._environment(), capture_output=True, text=True, timeout=self.timeout, check=False)
        for key in configured.stdout.split("\0"):
            if key:
                inert.extend(["-c", key + ("=false" if key.endswith((".required", ".driver")) else "=")])
        try:
            environment = self._environment()
            if index_file is not None:
                environment["GIT_INDEX_FILE"] = str(index_file)
            result = subprocess.run([self.executable, *inert, *arguments], cwd=root,
                env=environment, capture_output=True, text=not binary, timeout=self.timeout, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise OrchestrationError("Git preparation did not return a verified result.", "pr_git_unknown") from error
        if check and result.returncode:
            raise OrchestrationError("Git could not complete the requested preparation.", "pr_git_rejected", {"outcome": "failed"})
        return result.stdout if check else result

    @contextmanager
    def _custody(self, root):
        private = Path(self._git(root, "rev-parse", "--absolute-git-dir").strip())
        with (private / "xorchestrate-pr.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise OrchestrationError("This workspace is being prepared by another participant.", "pr_workspace_busy") from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def identity(self, name):
        item, root = self._selected(name)
        return {"head": self._git(root, "rev-parse", "HEAD").strip(),
            "tree": self._git(root, "rev-parse", "HEAD^{tree}").strip(),
            "branch": self._git(root, "symbolic-ref", "--short", "HEAD").strip(),
            "worktree": str(root), "repository": item["repository"]}

    def _unchanged(self, root, expected_head):
        if self._git(root, "rev-parse", "HEAD").strip() != expected_head:
            raise OrchestrationError("The workspace head changed during preparation.", "pr_candidate_changed")

    def _paths(self, root, selected):
        paths = []
        for value in selected:
            path = Path(value)
            if (not value or path.is_absolute() or ".." in path.parts or value.startswith("-")
                    or any((root / Path(*path.parts[:index])).is_symlink() for index in range(1, len(path.parts) + 1))):
                raise OrchestrationError("Checkpoint paths must be owned tracked paths without symlinks.", "pr_checkpoint_paths")
            known = self._git(root, "ls-files", "--error-unmatch", "--", value, check=False)
            if known.returncode:
                raise OrchestrationError("Checkpoint paths must already be tracked.", "pr_checkpoint_paths")
            paths.append(value)
        return sorted(set(paths))

    def _checkpoint(self, root, expected_head, selected, original_diff, original_index):
        """Commit a verified immutable tree; the live index cannot add extra bytes."""
        index = Path(self._git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
        captured_index = index.read_bytes()
        if self._git(root, "ls-files", "--stage", "-z", binary=True) != original_index:
            raise OrchestrationError("Checkpoint staging changed; external work was preserved.", "pr_checkpoint_conflict")
        with tempfile.TemporaryDirectory(prefix="xorchestrate-pr-checkpoint-") as private:
            private_index = Path(private) / "index"
            private_index.write_bytes(captured_index)
            self._git(root, "add", "--", *selected, index_file=private_index)
            tree = self._git(root, "write-tree", index_file=private_index).strip()
            intended = self._git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv",
                expected_head, tree, "--", binary=True)
            if intended != original_diff:
                raise OrchestrationError("Checkpoint bytes changed; external work was preserved.", "pr_checkpoint_conflict")
            checkpoint = self._git(root, "commit-tree", tree, "-p", expected_head, "-m",
                "Checkpoint selected work before branch refresh").strip()
            lock_path = index.with_name(index.name + ".lock")
            try:
                descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as error:
                raise OrchestrationError("Another Git writer owns the index; work was preserved.",
                    "pr_checkpoint_conflict", {"checkpoint": checkpoint}) from error
            replaced = False
            try:
                self._unchanged(root, expected_head)
                if (index.read_bytes() != captured_index
                        or self._git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--", binary=True) != original_diff):
                    raise OrchestrationError("Workspace/index changed; external work and the proposed checkpoint were preserved.",
                        "pr_checkpoint_conflict", {"checkpoint": checkpoint})
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    output.write(private_index.read_bytes())
                    output.flush()
                    os.fsync(output.fileno())
                self._git(root, "update-ref", "HEAD", checkpoint, expected_head)
                os.replace(lock_path, index)
                replaced = True
            finally:
                os.close(descriptor)
                if not replaced:
                    lock_path.unlink(missing_ok=True)
            return checkpoint

    def refresh(self, name, expected_head, target, paths):
        item, root = self._selected(name)
        with self._custody(root):
            with before_effect():
                self._unchanged(root, expected_head)
                private = Path(self._git(root, "rev-parse", "--absolute-git-dir").strip())
                if any((private / marker).exists() for marker in ("MERGE_HEAD", "rebase-apply", "rebase-merge", "CHERRY_PICK_HEAD")):
                    raise OrchestrationError("Resolve the existing Git operation before refreshing.", "pr_existing_git_operation")
                target = self._git(root, "rev-parse", "--verify", target + "^{commit}").strip()
                selected = self._paths(root, paths)
                dirty = set(self._git(root, "diff", "--name-only", "--no-ext-diff", "--no-textconv", "HEAD", "--").splitlines())
                if dirty - set(selected):
                    raise OrchestrationError("Tracked changes outside the selected checkpoint require an explicit selection.",
                        "pr_checkpoint_conflict", {"paths": sorted(dirty - set(selected))[:100]})
                original_index = self._git(root, "ls-files", "--stage", "-z", binary=True)
                original_diff = self._git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--", binary=True)
                checkpoint = expected_head
                if not dirty and self._git(root, "merge-base", "--is-ancestor", target, "HEAD", check=False).returncode == 0:
                    return {"status": "no_change", **self.identity(name), "checkpoint": checkpoint, "paths": [], "conflicts": []}
                # Revalidate both bytes and staging immediately before checkpoint assembly.
                self._unchanged(root, expected_head)
                if (original_index != self._git(root, "ls-files", "--stage", "-z", binary=True)
                        or original_diff != self._git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--", binary=True)):
                    raise OrchestrationError("Workspace content changed while preparing the checkpoint.", "pr_checkpoint_conflict")
            if dirty:
                checkpoint = self._checkpoint(root, expected_head, selected, original_diff, original_index)
                if self._git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--", binary=True):
                    raise OrchestrationError("External work changed after checkpoint; resolve it before merging.",
                        "pr_checkpoint_conflict", {"checkpoint": checkpoint})
            result = self._git(root, "merge", "--no-edit", "--no-verify", target, check=False)
            conflicts = self._git(root, "diff", "--name-only", "--diff-filter=U", "--no-ext-diff", "--no-textconv").splitlines()
            if result.returncode and not conflicts:
                raise OrchestrationError("The target could not be merged; checkpoint and Git state were preserved.", "pr_refresh_rejected")
            return {"status": "needs_resolution" if conflicts else "updated", **self.identity(name),
                "checkpoint": checkpoint, "paths": selected if dirty else [], "conflicts": conflicts[:100], "target": target}

    def publish(self, name, expected_head, branch):
        item, root = self._selected(name)
        with self._custody(root):
            with before_effect():
                self._unchanged(root, expected_head)
                if self._git(root, "status", "--porcelain").strip():
                    raise OrchestrationError("Publish requires the checked clean source revision.", "pr_dirty_candidate")
                if self._git(root, "check-ref-format", "refs/heads/" + branch, check=False).returncode:
                    raise OrchestrationError("Invalid source branch.", "pr_branch_invalid")
                remote = self._git(root, "remote", "get-url", "--push", item.get("remote", "origin")).strip()
                objects = self._git(root, "rev-parse", "--path-format=absolute", "--git-path", "objects").strip()
                if not remote or remote.startswith("-"):
                    raise OrchestrationError("Publication remote must be a literal supported URL or local path.", "pr_remote_unsupported")
                parsed = urlsplit(remote)
                scheme = parsed.scheme
                if scheme == "https":
                    if parsed.username or parsed.password or repository_of(remote, self.hosting_host) != item["repository"]:
                        raise OrchestrationError("Publication URL does not match the configured repository.", "pr_remote_mismatch")
                elif scheme == "file":
                    if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment or not Path(unquote(parsed.path)).is_absolute():
                        raise OrchestrationError("Local publication requires an absolute filesystem URL.", "pr_remote_unsupported")
                    remote = str(Path(unquote(parsed.path)).resolve())
                elif not scheme:
                    if ":" in remote and not Path(remote).is_absolute() and not remote.startswith(("./", "../")):
                        raise OrchestrationError("SSH/SCP publication is not supported by this adapter.", "pr_remote_unsupported")
                    remote = str((root / remote).resolve())
                else:
                    raise OrchestrationError("Publication supports HTTPS and local Git remotes.", "pr_remote_unsupported")
                if scheme != "https" and not Path(remote).is_dir():
                    raise OrchestrationError("The local publication repository is unavailable.", "pr_remote_unsupported")
            # Publish from a trusted empty bare repository. Candidate configuration,
            # hooks, URL rewrites, filters and helpers never enter the credentialed process.
            with tempfile.TemporaryDirectory(prefix="xorchestrate-pr-publish-") as private:
                environment = self._environment()
                for variable in (("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy",
                        "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
                        "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "GIT_SSL_CAINFO") if scheme == "https" else ()):
                    if variable in os.environ:
                        environment[variable] = os.environ[variable]
                init = subprocess.run([self.executable, "init", "--bare", "-q", private],
                    env=environment, capture_output=True, timeout=self.timeout, check=False)
                if init.returncode:
                    raise OrchestrationError("Cannot prepare isolated Git publication.", "pr_publish_setup", {"outcome": "not_started"})
                (Path(private) / "objects" / "info" / "alternates").write_text(objects + "\n", encoding="utf-8")
                token = self.credential() if self.credential is not None and scheme == "https" else None
                if token:
                    environment.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                        "GIT_CONFIG_VALUE_0": "Authorization: Bearer " + token})

                def publish_command(*arguments):
                    try:
                        return subprocess.run([self.executable, "--git-dir=" + private,
                            "-c", "core.hooksPath=" + os.devnull, "-c", "credential.helper=",
                            "-c", "http.followRedirects=false", "-c", "protocol.ext.allow=never", *arguments],
                            cwd=private, env=environment, capture_output=True, text=True,
                            timeout=self.timeout, check=False)
                    except (OSError, subprocess.SubprocessError) as error:
                        raise OrchestrationError("Isolated publication needs remote reconciliation.", "pr_publish_unknown") from error

                with before_effect():
                    previous = publish_command("ls-remote", "--heads", "--", remote, "refs/heads/" + branch)
                    if previous.returncode:
                        raise OrchestrationError("Cannot observe the publication target.", "pr_publish_unavailable")
                    if previous.stdout.split() and previous.stdout.split()[0] == expected_head:
                        return {"status": "already_published", "head": expected_head, "branch": branch}
                sent = publish_command("push", "--no-verify", "--", remote, expected_head + ":refs/heads/" + branch)
                if sent.returncode:
                    # A transport failure can follow remote success; retain uncertainty.
                    raise OrchestrationError("Publication was not confirmed; inspect the remote branch.", "pr_publish_unknown")
                after = publish_command("ls-remote", "--heads", "--", remote, "refs/heads/" + branch)
                if after.returncode or not after.stdout.split() or after.stdout.split()[0] != expected_head:
                    raise OrchestrationError("Publication must be reconciled against the remote branch.", "pr_publish_unknown")
                return {"status": "published", "head": expected_head, "branch": branch}
