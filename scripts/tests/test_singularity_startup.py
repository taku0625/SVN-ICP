import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "docker/start_singularity.sh"


class SingularityStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="singularity startup ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "test image.sif"
        self.image.write_bytes(b"test image")
        self.dataset = self.root / "dataset's files"
        self.dataset.mkdir()
        self.work = self.root / "work directory"
        self.log = self.root / "runtime.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        SIF_IMAGE=str(self.image), SVNICP_DATASET_DIR=str(self.dataset),
                        SVNICP_WORK_DIR=str(self.work), TEST_RUNTIME_LOG=str(self.log),
                        TEST_SIF_IMAGE=str(self.image))
        # Simulate a host invocation even when this suite itself runs in a SIF.
        self.env.pop("SINGULARITY_CONTAINER", None)
        self.env.pop("APPTAINER_CONTAINER", None)
        program = "#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["TEST_RUNTIME_LOG"]).write_text(json.dumps({
    "runtime": Path(sys.argv[0]).name, "args": args,
    "gpu": os.environ.get("CUDA_VISIBLE_DEVICES")}))
if os.environ.get("TEST_RUN_COMMAND"):
    image_index = args.index(os.environ["TEST_SIF_IMAGE"])
    assert args[image_index + 1] == "/ros_entrypoint.sh"
    command = args[image_index + 2:]
    os.execvpe(command[0], command, os.environ.copy())
raise SystemExit(int(os.environ.get("TEST_RUNTIME_EXIT", "0")))
'''
        for name in ("singularity", "apptainer"):
            tool = self.bin / name
            tool.write_text(program)
            tool.chmod(0o755)

    def launch(self, *args):
        return subprocess.run(["bash", str(LAUNCHER), *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True)

    def recorded(self):
        return json.loads(self.log.read_text())

    def test_default_launch_mounts_host_paths_and_automatically_runs_evaluator(self):
        self.env.update(BUILD_JOBS="3", CMAKE_CUDA_ARCHITECTURES="90", ROS_DOMAIN_ID="81",
                        CUDA_VISIBLE_DEVICES="GPU-assigned-1,GPU-assigned-2")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.recorded()
        arguments = record["args"]
        self.assertEqual(arguments[:2], ["exec", "--nv"])
        self.assertIn(str(ROOT) + ":/workspace/SVN-ICP:ro", arguments)
        self.assertIn(str(self.dataset) + ":/dataset:ro", arguments)
        self.assertIn(str(self.work) + ":/work:rw", arguments)
        self.assertIn("UV_PROJECT_ENVIRONMENT=/work/uv-env", arguments)
        self.assertIn("SVNICP_EVAL_ENV=/work/uv-evaluation", arguments)
        self.assertIn("DATASET_DIR=/dataset", arguments)
        self.assertIn("BUILD_JOBS=3", arguments)
        self.assertIn("CMAKE_CUDA_ARCHITECTURES=90", arguments)
        self.assertIn("ROS_DOMAIN_ID=81", arguments)
        self.assertEqual(record["gpu"], self.env["CUDA_VISIBLE_DEVICES"])
        self.assertEqual(arguments[-4:], [str(self.image), "/ros_entrypoint.sh", "bash",
                                         "/workspace/SVN-ICP/scripts/run_oxford_spires_singularity.sh"])
        self.assertTrue(self.work.is_dir())

    def test_validation_options_are_forwarded_after_oxford_action(self):
        result = self.launch("--no-gpu", "--oxford-spires", "--dry-run", "--sequence", "name with spaces",
                             "--output-dir", "/work/results/validation 01")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.recorded()["args"]
        self.assertNotIn("--nv", arguments)
        self.assertEqual(arguments[-5:], ["--dry-run", "--sequence", "name with spaces",
                                          "--output-dir", "/work/results/validation 01"])

    def test_custom_script_runs_with_literal_arguments_and_propagates_exit(self):
        script = self.root / "custom script.py"
        output = self.root / "script result.json"
        script.write_text("import json, sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\nraise SystemExit(37)\n")
        self.env["TEST_RUN_COMMAND"] = "1"
        literal = "$(touch should-not-exist); a 'quoted' argument"
        result = self.launch("--", sys.executable, str(script), str(output), literal)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual(json.loads(output.read_text()), [literal])
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_shell_and_apptainer_fallback(self):
        (self.bin / "singularity").unlink()
        result = self.launch("--shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["runtime"], "apptainer")
        self.assertEqual(self.recorded()["args"][-2:], ["/ros_entrypoint.sh", "bash"])

    def test_missing_inputs_work_creation_failure_and_nested_launch_stop_before_runtime(self):
        blocked = self.root / "blocked"
        blocked.write_text("preserve")
        cases = [("--image", str(self.root / "missing.sif")),
                 ("--dataset-dir", str(self.root / "missing dataset")),
                 ("--work-dir", str(blocked / "child")), ("--shell", "--oxford-spires"), ("--",)]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.launch(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())
        self.env["SINGULARITY_CONTAINER"] = str(self.image)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("on the host", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(blocked.read_text(), "preserve")

    def test_runtime_path_and_failure_exit_status(self):
        self.env["TEST_RUNTIME_EXIT"] = "19"
        result = self.launch("--runtime", str(self.bin / "apptainer"), "--shell")
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual(self.recorded()["runtime"], "apptainer")


if __name__ == "__main__":
    unittest.main()
