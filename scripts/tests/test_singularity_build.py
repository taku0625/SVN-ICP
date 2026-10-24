import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "docker/build_singularity.sh"
SPEC = importlib.util.spec_from_file_location("dockerfile_to_singularity", ROOT / "docker/dockerfile_to_singularity.py")
converter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(converter)


def section(definition, name):
    lines = definition.splitlines()
    start = next(i for i, line in enumerate(lines) if line.split()[:1] == ["%" + name]) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("%")), len(lines))
    return "\n".join(lines[start:end]) + "\n"


class SingularityBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="spython build ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands.jsonl"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        COMMAND_LOG=str(self.log), SPYTHON_PYTHON=sys.executable)
        self.output = self.root / "output image.sif"
        program = "#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
tool, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\\n")
if tool == "docker":
    raise SystemExit("Docker must not be called by the spython workflow")
if os.environ.get("FAIL_STAGE") == args[0]:
    raise SystemExit(17)
if args[0] == "build":
    definition = Path(args[-1]).read_text()
    assert "Bootstrap: docker" in definition and "%post -c /bin/bash" in definition
    Path(args[-2]).write_bytes(b"SIF fixture")
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

    def fixture(self, text):
        path = self.root / "Dockerfile"
        path.write_text(text)
        return path

    def test_real_spython_conversion_builds_definition_without_docker(self):
        result = self.run_build("--output", str(self.output), "--fakeroot", "--", "--disable-cache")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"SIF fixture")
        self.assertEqual([cmd[:2] for cmd in self.commands()], [["singularity", "build"], ["singularity", "inspect"]])
        self.assertEqual(self.commands()[0][2:4], ["--fakeroot", "--disable-cache"])
        definition = self.output.with_suffix(".def").read_text()
        post = section(definition, "post")
        subprocess.run(["bash", "-n"], input=post, text=True, check=True)
        self.assertEqual(list(self.root.glob(".svnicp-image.*")), [])

    def test_definition_only_requires_no_runtime_and_resolves_runtime_environment(self):
        (self.bin / "singularity").unlink()
        (self.bin / "apptainer").unlink()
        definition = self.root / "recipe.def"
        result = self.run_build("--definition-only", "--definition", str(definition),
                                "--build-arg", "ROS_DISTRO=jazzy", "--build-arg", "CMAKE_CUDA_ARCHITECTURES=80;90")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands(), [])
        body = section(definition.read_text(), "environment")
        names = ["ROS_DISTRO", "CMAKE_CUDA_ARCHITECTURES", "Torch_DIR", "CMAKE_PREFIX_PATH", "LD_LIBRARY_PATH"]
        command = body + "\n" + "exec " + shlex.join([
            sys.executable, "-c", "import json, os; print(json.dumps([os.environ[n] for n in " + repr(names) + "]))"])
        values = subprocess.check_output(["bash", "-c", command], env=dict(self.env, LD_LIBRARY_PATH="/inherited"), text=True)
        self.assertEqual(json.loads(values), ["jazzy", "80;90", "/opt/libtorch/share/cmake/Torch",
                                             "/opt/libtorch/share/cmake/Torch", "/opt/libtorch/lib:/inherited"])

    def test_generated_post_exports_arguments_and_preserves_shell_and_run_boundaries(self):
        source = self.fixture('FROM ubuntu:22.04\nSHELL ["/bin/bash", "-c"]\n'
                              'ARG ARCH=75;80;90\nENV ARCHES=${ARCH}\n'
                              'RUN [[ "$ARCHES" == "75;80;90" ]]\n'
                              'RUN export SVNICP_RUN_LOCAL=yes; cd /\n'
                              'RUN test -z "${SVNICP_RUN_LOCAL+x}"\n' +
                              'RUN ' + json.dumps([sys.executable, "-c",
                              'import os; from pathlib import Path; Path("result.txt").write_text(os.environ["ARCHES"])']) + '\n')
        body = section(converter.convert(source, {}), "post")
        result = subprocess.run(["bash", "-c", body], cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "result.txt").read_text(), "75;80;90")

    def test_failed_run_stops_later_steps(self):
        source = self.fixture("FROM ubuntu:22.04\nRUN false && touch first\nRUN touch later\n")
        result = subprocess.run(["bash", "-c", section(converter.convert(source, {}), "post")], cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "first").exists())
        self.assertFalse((self.root / "later").exists())

    def test_build_argument_values_are_literal_and_do_not_execute_shell_commands(self):
        source = self.fixture("FROM ubuntu:22.04\nARG VALUE=x\nENV VALUE=${VALUE}\n")
        literal = "a'; touch owned; # $HOME $(touch injected)"
        body = section(converter.convert(source, {"VALUE": literal}), "post")
        result = subprocess.check_output(["bash", "-c", body + '\nprintf "%s" "$VALUE"'], cwd=self.root, text=True)
        self.assertEqual(result, literal)
        self.assertFalse((self.root / "owned").exists())
        self.assertFalse((self.root / "injected").exists())

    def test_apptainer_fallback_and_custom_runtime(self):
        (self.bin / "singularity").unlink()
        result = self.run_build("--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(cmd[0] == "apptainer" for cmd in self.commands()))
        result = self.run_build("--output", str(self.root / "explicit.sif"),
                                "--runtime", str(self.bin / "apptainer"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_outputs_and_unknown_build_arguments_do_not_run_builder(self):
        self.output.write_bytes(b"keep this image")
        result = self.run_build("--output", str(self.output))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b"keep this image")
        self.output.unlink()
        definition = self.output.with_suffix(".def")
        definition.write_text("keep this definition")
        result = self.run_build("--output", str(self.output))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(definition.read_text(), "keep this definition")
        definition.unlink()
        result = self.run_build("--output", str(self.output), "--build-arg", "UNKNOWN=x")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands(), [])
        self.assertFalse(self.output.with_suffix(".def").exists())

    def test_conversion_failures_keep_definition_and_never_publish_partial_sif(self):
        for stage in ("build", "inspect"):
            with self.subTest(stage=stage):
                self.env["FAIL_STAGE"] = stage
                output = self.root / (stage + ".sif")
                result = self.run_build("--output", str(output))
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertFalse(output.exists())
                self.assertTrue(output.with_suffix(".def").exists())
                self.assertEqual(list(self.root.glob(".svnicp-image.*")), [])


if __name__ == "__main__":
    unittest.main()
