import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "docker/build_singularity.sh"


class SingularityBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sif build ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands.jsonl"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        COMMAND_LOG=str(self.log))
        self.output = self.root / "output image.sif"
        program = "#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\\n")
if os.environ.get("FAIL_STAGE") == tool + ":" + args[0]:
    raise SystemExit(17)
if tool == "docker":
    if args[:2] == ["image", "inspect"]:
        print("sha256:test-image")
    elif args[:2] == ["image", "save"]:
        assert args[-1] == "sha256:test-image"
        Path(args[args.index("--output") + 1]).write_bytes(b"docker archive")
elif args[0] == "build":
    assert args[2].startswith("docker-archive:///")
    archive = Path(args[2].removeprefix("docker-archive://"))
    assert archive.read_bytes() == b"docker archive"
    Path(args[1]).write_bytes(b"SIF fixture")
elif args[0] == "inspect":
    assert Path(args[1]).read_bytes() == b"SIF fixture"
'''
        for name in ("docker", "singularity", "apptainer"):
            path = self.bin / name
            path.write_text(program)
            path.chmod(0o755)

    def run_build(self, *args):
        return subprocess.run(["bash", str(BUILD), *args], env=self.env,
                              cwd=self.root, capture_output=True, text=True)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_cleaned_up(self):
        self.assertEqual(list(self.root.glob(".svnicp-image.*")), [])

    def test_build_export_and_convert_preserve_arguments_and_use_pinned_image(self):
        result = self.run_build("--output", str(self.output), "--image", "svnicp:test", "--",
                                "--build-arg", "TORCH_VERSION=2.5.1", "--label", "name=with spaces")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"SIF fixture")
        commands = self.commands()
        self.assertEqual([cmd[:2] for cmd in commands], [["docker", "info"], ["docker", "build"],
                         ["docker", "image"], ["docker", "image"], ["singularity", "build"],
                         ["singularity", "inspect"]])
        build = commands[1]
        self.assertEqual(build[build.index("-t") + 1], "svnicp:test")
        self.assertEqual(build[build.index("-f") + 1], str(ROOT / "docker/Dockerfile"))
        self.assertEqual(build[-1], str(ROOT))
        self.assertIn("name=with spaces", build)
        self.assert_cleaned_up()

    def test_archive_only_requires_no_sif_runtime_and_preserves_archive(self):
        archive = self.root / "docker archive.tar"
        (self.bin / "singularity").unlink()
        (self.bin / "apptainer").unlink()
        result = self.run_build("--archive-only", str(archive))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(archive.read_bytes(), b"docker archive")
        self.assertTrue(all(cmd[0] == "docker" for cmd in self.commands()))
        self.assert_cleaned_up()

    def test_convert_archive_uses_apptainer_without_docker(self):
        archive = self.root / "input archive.tar"
        archive.write_bytes(b"docker archive")
        (self.bin / "singularity").unlink()
        result = self.run_build("--from-archive", str(archive), "--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([cmd[:2] for cmd in self.commands()], [["apptainer", "build"], ["apptainer", "inspect"]])
        self.assertEqual(archive.read_bytes(), b"docker archive")
        self.assert_cleaned_up()

    def test_existing_image_can_be_exported_without_rebuilding(self):
        result = self.run_build("--skip-docker-build", "--runtime", str(self.bin / "apptainer"),
                                "--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(["docker", "build"], [cmd[:2] for cmd in self.commands()])
        self.assert_cleaned_up()

    def test_existing_destination_and_conflicting_options_cause_no_commands(self):
        self.output.write_bytes(b"keep this image")
        for args in (("--output", str(self.output)),
                     ("--archive-only", str(self.root / "image.tar"), "--output", str(self.output)),
                     ("--skip-docker-build", "--", "--no-cache"), ("--output",)):
            with self.subTest(args=args):
                result = self.run_build(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.commands(), [])
        self.assertEqual(self.output.read_bytes(), b"keep this image")

    def test_build_and_conversion_failures_never_publish_partial_images(self):
        for stage in ("docker:build", "singularity:build", "singularity:inspect"):
            with self.subTest(stage=stage):
                self.env["FAIL_STAGE"] = stage
                result = self.run_build("--output", str(self.output))
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertFalse(self.output.exists())
                self.assert_cleaned_up()


if __name__ == "__main__":
    unittest.main()
