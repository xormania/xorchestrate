"""Exercise local PHP archive extraction; download/cache tests live in test_toolchain."""

import hashlib
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "actions/php-runtime/install-php.sh"


class PHPExtractionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("bin", "temp"):
            (self.root / name).mkdir()
        php = self.root / "php"
        php.write_text("#!/bin/sh\nprintf 'fixture PHP\\n'\n")
        php.chmod(0o755)
        self.archive = self.root / "php.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.add(php, arcname="php")
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        curl = self.root / "bin/curl"
        curl.write_text('#!/bin/sh\necho download >> "$FIXTURE_DOWNLOADS"\nexit 99\n')
        curl.chmod(0o755)
        self.downloads = self.root / "downloads"
        self.path_file = self.root / "github-path"
        self.env = {
            **os.environ,
            "PATH": str(self.root / "bin") + os.pathsep + os.environ["PATH"],
            "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
            "RUNNER_TEMP": str(self.root / "temp"),
            "GITHUB_PATH": str(self.path_file),
            "FIXTURE_DOWNLOADS": str(self.downloads),
        }

    def install(self):
        return subprocess.run(["bash", str(INSTALLER), str(self.archive), self.digest],
                              env=self.env, capture_output=True, text=True)

    def test_each_extraction_uses_its_own_runtime_without_changing_the_archive(self):
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("fixture PHP", result.stdout)
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), self.digest)
        self.assertFalse(self.downloads.exists())
        runtimes = self.path_file.read_text().splitlines()
        self.assertEqual(len(set(runtimes)), 2)
        self.assertTrue(all((Path(path) / "php").is_file() for path in runtimes))

    def test_missing_or_changed_archive_fails_without_downloading_or_publishing_a_runtime(self):
        for content, message in ((None, "missing"), (b"not the pinned archive", "checksum mismatch")):
            with self.subTest(content=content):
                if content is None:
                    self.archive.unlink()
                else:
                    self.archive.write_bytes(content)
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout)
                self.assertFalse(self.path_file.exists())
                self.assertFalse(self.downloads.exists())
                self.assertEqual(list((self.root / "temp").iterdir()), [])

    def test_failed_extraction_removes_partial_runtime_and_does_not_publish_it(self):
        self.archive.write_bytes(b"not a gzip archive")
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.path_file.exists())
        self.assertFalse(self.downloads.exists())
        self.assertEqual(list((self.root / "temp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
