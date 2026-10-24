import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oxford_spires import sequence


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("abci_batch", ROOT / "abci/submit_oxford_spires.py")
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


class AbciBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="abci batch ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = self.root / "dataset"
        sequence(self.dataset)
        sequence(self.dataset, "2024-03-12-keble-college-03")
        self.image = self.root / "test image.sif"
        self.image.write_bytes(b"SIF fixture")
        self.run = self.root / "run"
        self.log = self.root / "submissions.json"
        self.qsub = self.root / "qsub"
        self.qsub.write_text("#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
log = Path(os.environ["TEST_QSUB_LOG"])
rows = json.loads(log.read_text()) if log.exists() else []
rows.append({"args": sys.argv[1:], "cwd": os.getcwd()})
log.write_text(json.dumps(rows))
if os.environ.get("TEST_QSUB_FAILURE") == str(len(rows)):
    print("quota exceeded", file=sys.stderr)
    raise SystemExit(17)
print(str(1000 + len(rows)) + ".pbs1")
''')
        self.qsub.chmod(0o755)
        self.environment = patch.dict(os.environ, {"FRAI": "test-project", "FRAI_RES": "test-queue",
                                                  "TEST_QSUB_LOG": str(self.log),
                                                  "SINGULARITY_CONTAINER": "", "APPTAINER_CONTAINER": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def launch(self, *args):
        return batch.main(["submit", "--dataset-dir", str(self.dataset), "--image", str(self.image),
                           "--run-dir", str(self.run), "--qsub", str(self.qsub), *args])

    def manifest(self):
        return json.loads((self.run / "manifest.json").read_text())

    def test_one_gpu_per_sequence_after_one_build_with_private_logs_and_ros_domains(self):
        self.assertEqual(self.launch(), 0)
        calls = json.loads(self.log.read_text())
        self.assertEqual(len(calls), 3)
        expected = ["-P", "test-project", "-q", "test-queue", "-l", "select=1",
                    "-l", "walltime=1:00:00", "-v", "RTYPE=rt_HG"]
        for call in calls:
            self.assertEqual(call["args"][:len(expected)], expected)
            self.assertEqual(call["args"][-1], str(ROOT / "abci/job.sh"))
            self.assertTrue((Path(call["cwd"]) / "oxford-task.sh").exists())
            self.assertEqual(call["args"][call["args"].index("-o") + 1], str(Path(call["cwd"]) / "stdout.log"))
        self.assertNotIn("-W", calls[0]["args"])
        for call in calls[1:]:
            self.assertEqual(call["args"][call["args"].index("-W") + 1], "depend=afterok:1001.pbs1")
        manifest = self.manifest()
        self.assertEqual([job["ros_domain_id"] for job in manifest["jobs"]], [74, 75])
        self.assertEqual(len({job["results_dir"] for job in manifest["jobs"]}), 2)
        for job in manifest["jobs"]:
            task = (Path(job["job_dir"]) / "oxford-task.sh").read_text()
            self.assertIn("--sequence " + job["sequence"], task)
            self.assertIn("--skip-build", task)
        self.assertIn("--build-only", (self.run / "prepare/oxford-task.sh").read_text())

    def test_plan_submits_nothing_and_existing_run_is_preserved(self):
        self.assertEqual(self.launch("--plan"), 0)
        self.assertFalse(self.log.exists())
        original = (self.run / "manifest.json").read_text()
        self.assertEqual(batch.main(["collect", "--run-dir", str(self.run)]), 2)
        with self.assertRaises(SystemExit):
            self.launch("--plan")
        self.assertEqual((self.run / "manifest.json").read_text(), original)

    def test_build_submission_failure_stops_all_sequence_submissions(self):
        os.environ["TEST_QSUB_FAILURE"] = "1"
        self.assertEqual(self.launch(), 1)
        self.assertEqual(len(json.loads(self.log.read_text())), 1)
        self.assertEqual(self.manifest()["jobs"], [])
        self.assertEqual(batch.main(["collect", "--run-dir", str(self.run)]), 1)

    def test_one_submission_failure_does_not_stop_other_sequences(self):
        os.environ["TEST_QSUB_FAILURE"] = "2"
        self.assertEqual(self.launch(), 1)
        self.assertEqual(len(json.loads(self.log.read_text())), 3)
        self.assertEqual([row["submission_state"] for row in self.manifest()["jobs"]], ["failed", "submitted"])
        self.assertEqual(batch.main(["collect", "--run-dir", str(self.run)]), 1)

    def test_skip_build_reuses_the_given_workspace_without_dependencies(self):
        work = self.root / "existing work"
        setup = work / "svnicp_ws/install/setup.bash"
        setup.parent.mkdir(parents=True)
        setup.write_text("# existing build\n")
        self.assertEqual(self.launch("--skip-build", "--work-dir", str(work)), 0)
        self.assertIsNone(self.manifest()["prepare"])
        calls = json.loads(self.log.read_text())
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("-W" not in call["args"] for call in calls))

    def test_job_dispatcher_preserves_task_exit_code_and_status(self):
        task = self.root / "task"
        batch.make_task(task, [sys.executable, "-c", "raise SystemExit(37)"])
        environment = dict(os.environ, PBS_O_WORKDIR=str(task))
        result = subprocess.run(["bash", str(ROOT / "abci/job.sh")], env=environment, check=False)
        self.assertEqual(result.returncode, 37)
        status = (task / "status.json").read_text()
        self.assertEqual(json.loads(status), {"state": "failed", "exit_code": 37})
        result = subprocess.run(["bash", str(ROOT / "abci/job.sh")], env=environment, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((task / "status.json").read_text(), status)

    def test_direct_job_submission_keeps_single_sequence_launcher_behavior(self):
        repository = self.root / "direct repository"
        launcher = repository / "docker/start_singularity.sh"
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$TEST_DIRECT_ARGS"\nexit 23\n')
        arguments_file = self.root / "direct args"
        environment = dict(os.environ, PBS_O_WORKDIR=str(repository), SVNICP_REPO_ROOT=str(repository),
                           SIF_IMAGE=str(self.image), TEST_DIRECT_ARGS=str(arguments_file))
        result = subprocess.run(["bash", str(ROOT / "abci/job.sh")], env=environment, check=False)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(arguments_file.read_text().splitlines(),
                         ["--image", str(self.image), "--", "bash", "scripts/my_startup.sh"])

    def test_collection_requires_matching_results_and_successful_task_completion(self):
        self.assertEqual(self.launch("--plan"), 0)
        manifest = self.manifest()
        for job in manifest["jobs"]:
            batch.save_json(Path(job["job_dir"]) / "status.json", {"state": "completed", "exit_code": 0})
            results = Path(job["results_dir"])
            results.mkdir(parents=True)
            batch.save_json(results / "summary.json", [{"sequence": job["sequence"], "status": "completed", "ate_rmse_m": 0.1}])
        self.assertEqual(batch.main(["collect", "--run-dir", str(self.run)]), 0)
        self.assertEqual(len(json.loads((self.run / "summary.json").read_text())), 2)
        self.assertTrue((self.run / "summary.csv").exists())
        results = Path(manifest["jobs"][0]["results_dir"]) / "summary.json"
        results.unlink()
        self.assertEqual(batch.main(["collect", "--run-dir", str(self.run)]), 1)


if __name__ == "__main__":
    unittest.main()
