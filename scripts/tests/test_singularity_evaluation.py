import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from test_oxford_spires import sequence
from test_ral2026 import ral_sequence


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/run_oxford_spires_singularity.sh"


class SingularityEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="singularity evaluation ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = self.root / "dataset"
        sequence(self.dataset)
        self.output = self.root / "results"
        self.workspace = self.root / "workspace"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        modules = self.root / "ros modules"
        (modules / "rclpy").mkdir(parents=True)
        (modules / "rclpy/__init__.py").write_text("")
        (modules / "rclpy/type_support.py").write_text("def check_for_type_support(message): pass\n")
        (modules / "rosbag2_py.py").write_text("")
        (modules / "sensor_msgs/msg").mkdir(parents=True)
        (modules / "sensor_msgs/__init__.py").write_text("")
        (modules / "sensor_msgs/msg/__init__.py").write_text("class PointCloud2: pass\n")
        setup = self.root / "ros setup.bash"
        setup.write_text("export PYTHONPATH=" + shlex.quote(str(modules)) + '\"${PYTHONPATH:+:$PYTHONPATH}\"\n')
        self.log = self.root / "build.json"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        SVNICP_WORKSPACE=str(self.workspace), SVNICP_EVAL_ENV=str(self.root / "eval venv"),
                        SVNICP_PYTHON=sys.executable, SVNICP_ROS_SETUP=str(setup),
                        DATASET_DIR=str(self.dataset), OUTPUT_DIR=str(self.output),
                        BUILD_COMMAND_LOG=str(self.log))
        program = "#!" + sys.executable + "\n" + '''
import json, os, shlex, sys
from pathlib import Path
tool, args = Path(sys.argv[0]).name, sys.argv[1:]
workspace = Path(os.environ["SVNICP_WORKSPACE"])
if tool == "colcon":
    Path(os.environ["BUILD_COMMAND_LOG"]).write_text(json.dumps({
        "args": args, "jobs": os.environ["CMAKE_BUILD_PARALLEL_LEVEL"]}))
    if os.environ.get("FAIL_BUILD"):
        raise SystemExit(17)
    prefix = workspace / "install/svn-icp"
    node = prefix / "lib/svn-icp/svnicp_lio_node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\\nexit 0\\n")
    node.chmod(0o755)
    (workspace / "install/setup.bash").write_text("export TEST_OVERLAY=yes\\n")
elif tool == "ros2":
    assert args == ["pkg", "prefix", "svn-icp"]
    assert os.environ["TEST_OVERLAY"] == "yes"
    print(workspace / "install/svn-icp")
elif tool == "ldd":
    print("libodom.so => not found" if os.environ.get("MISSING_LIBRARY") else "libodom.so => /installed/libodom.so")
'''
        for name in ("colcon", "ros2", "ldd"):
            tool = self.bin / name
            tool.write_text(program)
            tool.chmod(0o755)

    def run_runner(self, *args):
        return subprocess.run(["bash", str(RUNNER), *args], env=self.env, cwd=self.root,
                              capture_output=True, text=True)

    def run_startup(self, *args):
        return subprocess.run(["bash", str(ROOT / "scripts/my_startup.sh"), *args],
                              env=self.env, cwd=self.root, capture_output=True, text=True)

    def test_startup_defaults_to_exactly_one_sequence(self):
        self.env.pop("OXFORD_SEQUENCE", None)
        sequence(self.dataset, "2024-03-12-keble-college-03")
        result = self.run_startup("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads((self.output / "validation.json").read_text())
        self.assertEqual([row["sequence"] for row in rows], ["2024-03-12-keble-college-02"])
        self.assertEqual(rows[0]["status"], "validated_only")
        self.assertFalse(self.log.exists())

    def test_startup_selects_another_sequence_and_preserves_results_on_unknown_name(self):
        sequence(self.dataset, "2024-03-12-keble-college-03")
        result = self.run_startup("--sequence", "2024-03-12-keble-college-03", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.output / "validation.json"
        original = report.read_text()
        self.assertEqual([row["sequence"] for row in json.loads(original)], ["2024-03-12-keble-college-03"])
        result = self.run_startup("--sequence", "unknown-sequence", "--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown sequences", result.stderr)
        self.assertEqual(report.read_text(), original)
        self.assertFalse(self.log.exists())

    def test_inspection_uses_real_uv_and_evaluator_without_ros_or_build(self):
        self.env["SVNICP_ROS_SETUP"] = str(self.root / "missing ROS setup")
        result = self.run_runner("--list-sequences")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2024-03-12-keble-college-02", result.stdout)
        result = self.run_runner("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads((self.output / "validation.json").read_text())
        self.assertEqual(rows[0]["status"], "validated_only")
        self.assertFalse(self.log.exists())
        cfg = (Path(self.env["SVNICP_EVAL_ENV"]) / "pyvenv.cfg").read_text()
        self.assertIn("include-system-site-packages = true", cfg)
        python = Path(self.env["SVNICP_EVAL_ENV"]) / "bin/python"
        version = subprocess.check_output([str(python), "-c", "import numpy; print(numpy.__version__)"], text=True)
        self.assertTrue(version.startswith("1."))

    def test_ral2026_dry_run_passes_layout_and_speed_selection_through_real_runner(self):
        dataset = self.root / "ral2026"
        ral_sequence(dataset, "speed1")
        ral_sequence(dataset, "speed2")
        result = self.run_runner("--dataset-dir", str(dataset), "--layout", "ral2026", "--dry-run",
                                 "--sequence", "speed2/keble-college-02")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads((self.output / "validation.json").read_text())
        self.assertEqual([row["sequence"] for row in rows], ["speed2/keble-college-02"])
        self.assertEqual(rows[0]["status"], "validated_only")
        self.assertTrue(rows[0]["ground_truth"].endswith("ground-truth-lidar-tum.txt"))
        self.assertFalse(self.log.exists())

    def test_build_workflow_keeps_artifacts_outside_source_and_selects_overlay(self):
        self.env["DATASET_DIR"] = str(self.root / "no dataset required")
        result = self.run_runner("--build-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        build = json.loads(self.log.read_text())
        self.assertEqual(build["jobs"], "2")
        arguments = build["args"]
        for option, suffix in (("--log-base", "log"), ("--build-base", "build"), ("--install-base", "install")):
            self.assertEqual(arguments[arguments.index(option) + 1], str(self.workspace / suffix))
        self.assertIn(str(ROOT / "stein_msgs"), arguments)
        self.assertIn(str(ROOT / "svn-icp"), arguments)
        self.assertIn("Node ready:", result.stdout)
        self.assertFalse(self.output.exists())

    def test_missing_dataset_and_invalid_arguments_stop_before_build(self):
        for arguments in (("--dataset-dir", str(self.root / "missing")), ("--play-rate", "0")):
            with self.subTest(arguments=arguments):
                result = self.run_runner(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def test_build_failure_preserves_exit_status(self):
        self.env["FAIL_BUILD"] = "1"
        result = self.run_runner("--build-only")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertNotIn("Node ready:", result.stdout)
        self.assertFalse(self.output.exists())

    def test_skip_build_requires_install_and_reports_missing_libraries(self):
        result = self.run_runner("--skip-build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No install/setup.bash", result.stderr)
        self.assertFalse(self.log.exists())
        result = self.run_runner("--build-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        original = self.log.read_text()
        self.env["MISSING_LIBRARY"] = "1"
        result = self.run_runner("--skip-build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing node libraries", result.stderr)
        self.assertEqual(self.log.read_text(), original)
        self.assertFalse((self.output / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
