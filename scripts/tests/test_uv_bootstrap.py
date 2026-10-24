import hashlib
import io
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import unittest


BOOTSTRAP = Path(__file__).resolve().parents[1] / "ensure_uv.sh"


class UvBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="uv bootstrap ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        target = "aarch64-unknown-linux-musl" if platform.machine() in {"aarch64", "arm64"} else "x86_64-unknown-linux-musl"
        self.archive = self.root / ("uv-" + target + ".tar.gz")
        binary = b'#!/bin/sh\nprintf "uv 0.12.23\\n"\nprintf executed > "$TEST_UV_EXECUTED"\n'
        with tarfile.open(self.archive, "w:gz") as stream:
            member = tarfile.TarInfo("uv-" + target + "/uv")
            member.size = len(binary)
            member.mode = 0o755
            stream.addfile(member, io.BytesIO(binary))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksum = self.root / "release.sha256"
        self.checksum.write_text(digest + "  " + self.archive.name + "\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.marker = self.root / "executed"
        self.curl_log = self.root / "curl.log"
        curl = self.bin / "curl"
        curl.write_text("#!" + sys.executable + "\n" + '''
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
url = next(value for value in args if value.startswith("https://"))
assert url.startswith("https://github.com/astral-sh/uv/releases/download/0.12.23/uv-")
with open(os.environ["TEST_CURL_LOG"], "a") as stream:
    stream.write(url + "\\n")
if os.environ.get("TEST_CURL_FAIL"):
    raise SystemExit(22)
source = os.environ["TEST_UV_CHECKSUM"] if url.endswith(".sha256") else os.environ["TEST_UV_ARCHIVE"]
shutil.copyfile(source, args[args.index("-o") + 1])
''')
        curl.chmod(0o755)
        self.install = self.root / "managed tools"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        SVNICP_UV_DIR=str(self.install), TEST_UV_ARCHIVE=str(self.archive),
                        TEST_UV_CHECKSUM=str(self.checksum), TEST_UV_EXECUTED=str(self.marker),
                        TEST_CURL_LOG=str(self.curl_log))

    def run_bootstrap(self, *args):
        return subprocess.run(["bash", str(BOOTSTRAP), *args], env=self.env, cwd=self.root,
                              capture_output=True, text=True)

    def test_verified_install_and_cache_reuse_without_another_download(self):
        result = self.run_bootstrap("--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.install / "uv"))
        self.assertTrue(self.marker.exists())
        self.assertTrue(os.access(self.install / "uv", os.X_OK))
        original = self.curl_log.read_text()
        self.env["TEST_CURL_FAIL"] = "1"
        result = self.run_bootstrap("--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.curl_log.read_text(), original)
        self.assertEqual(list(self.install.glob(".install.*")), [])

    def test_checksum_failure_never_executes_or_publishes_the_download(self):
        self.checksum.write_text("0" * 64 + "  " + self.archive.name + "\n")
        result = self.run_bootstrap("--install")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.install / "uv").exists())
        self.assertEqual(list(self.install.glob(".install.*")), [])

    def test_network_failure_never_publishes_partial_install(self):
        self.env["TEST_CURL_FAIL"] = "1"
        result = self.run_bootstrap("--install")
        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertFalse((self.install / "uv").exists())
        self.assertEqual(list(self.install.glob(".install.*")), [])

    def test_explicit_executable_is_used_and_invalid_override_is_reported(self):
        executable = self.root / "provided uv"
        executable.write_text('#!/bin/sh\necho "uv 0.12.23"\n')
        executable.chmod(0o755)
        self.env["SVNICP_UV"] = str(executable)
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(executable))
        self.assertFalse(self.curl_log.exists())
        self.env["SVNICP_UV"] = str(self.root / "missing")
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SVNICP_UV", result.stderr)
        self.assertFalse(self.curl_log.exists())


if __name__ == "__main__":
    unittest.main()
