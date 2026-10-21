import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "evaluate_oxford_spires.py"
SPEC = importlib.util.spec_from_file_location("oxford", SCRIPT)
oxford = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oxford)


def poses(xyz):
    result = np.zeros((len(xyz), 8))
    result[:, 0] = np.arange(len(xyz)) + 100.0
    result[:, 1:4] = xyz
    result[:, 7] = 1.0
    return result


def sequence(root, name="2024-03-12-keble-college-02", extra_topic=False):
    path = root / name
    bag = path / "rosbag_mcap"
    bag.mkdir(parents=True)
    (bag / "rosbag_mcap_0.mcap").write_bytes(b"fixture")
    topics = [{"topic_metadata": {"name": "/lidar/points", "type": "sensor_msgs/msg/PointCloud2",
                                   "offered_qos_profiles": [], "type_description_hash": "hash"},
               "message_count": 3}]
    if extra_topic:
        topics.append({"topic_metadata": {"name": "/other/points", "type": "sensor_msgs/msg/PointCloud2"},
                       "message_count": 3})
    (bag / "metadata.yaml").write_text(yaml.safe_dump({"rosbag2_bagfile_information": {
        "version": 9, "storage_identifier": "mcap", "relative_file_paths": ["rosbag_mcap_0.mcap"],
        "topics_with_message_count": topics, "ros_distro": "jazzy", "custom_data": {}}}))
    gt = path / "processed/trajectory/gt-tum.txt"
    gt.parent.mkdir(parents=True)
    np.savetxt(gt, poses([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]))
    return path


class OxfordTests(unittest.TestCase):
    def test_docker_wrapper_preserves_paths_and_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dataset = root / "dataset with spaces"
            dataset.mkdir()
            capture = root / "docker-args.json"
            docker = root / "docker"
            docker.write_text("#!/usr/bin/env python3\nimport json, os, pathlib, sys\n"
                              "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n")
            docker.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       DATASET_DIR=str(dataset), OUTPUT_DIR=str(root / "output with spaces"),
                       CAPTURE=str(capture), DISPLAY="")
            wrapper = SCRIPT.parent / "run_oxford_spires.sh"
            subprocess.run(["bash", str(wrapper), "--dry-run", "--sequence", "2024-03-12-keble-college-02"],
                           env=env, check=True, capture_output=True)
            arguments = json.loads(capture.read_text())
            self.assertIn(str(dataset) + ":/storage/oxford_spires:ro", arguments)
            self.assertEqual(arguments[-4:], ["--", "--dry-run", "--sequence", "2024-03-12-keble-college-02"])

    def test_process_lifecycle_and_playback_failure(self):
        # Real child processes exercise command construction, output, and cleanup;
        # only ROS discovery and the bag reader are replaced by fixture metadata.
        for playback_exit in (0, 7):
            with self.subTest(playback_exit=playback_exit), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                executable = root / "ros2"
                executable.write_text("""#!/usr/bin/env python3
import os, pathlib, sys, time, yaml
if sys.argv[1] == 'run':
    params = yaml.safe_load(pathlib.Path(sys.argv[-1]).read_text())['/svnicp/SVNICP']['ros__parameters']
    output = pathlib.Path(params['tum_trajectory_path'])
    output.write_text('100 0 0 0 0 0 0 1\\n101 1 0 0 0 0 0 1\\n102 1 1 0 0 0 0 1\\n')
    (output.parent / 'node.pid').write_text(str(os.getpid()))
    while True: time.sleep(1)
else:
    raise SystemExit(int(os.environ['FIXTURE_PLAYBACK_EXIT']))
""")
                executable.chmod(0o755)
                monitor = types.SimpleNamespace(
                    count_subscribers=lambda topic: int((root / "node.pid").exists()),
                    destroy_node=lambda: None)
                fake_ros = types.SimpleNamespace(init=lambda: None, shutdown=lambda: None,
                                                create_node=lambda name: monitor,
                                                spin_once=lambda node, timeout_sec: None)
                args = types.SimpleNamespace(startup_timeout=5, play_rate=0.05,
                                             read_ahead_queue_size=10, sequence_timeout=5,
                                             drain_timeout=5, end_tolerance=0.2)
                config = {"/svnicp/SVNICP": {"ros__parameters": {"lidar_param": {"topic": "/old"}}}}
                with patch.dict(sys.modules, {"rclpy": fake_ros}), \
                     patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"],
                                             "FIXTURE_PLAYBACK_EXIT": str(playback_exit)}), \
                     patch.object(oxford, "cloud_bounds", return_value=(100.0, 102.0, 3)):
                    if playback_exit:
                        with self.assertRaisesRegex(RuntimeError, "Bag playback failed: 7"):
                            oxford.run_sequence(root, "/points", config, root, args)
                    else:
                        result = oxford.run_sequence(root, "/points", config, root, args)
                        self.assertEqual(result["lidar_frames"], 3)
                        self.assertEqual(result["estimated_end"], 102.0)
                params = yaml.safe_load((root / "parameters.yaml").read_text())["/svnicp/SVNICP"]["ros__parameters"]
                self.assertEqual(params["lidar_param"]["topic"], "/points")
                self.assertEqual(params["auto_shutdown_progress"], 0.0)
                self.assertEqual(config["/svnicp/SVNICP"]["ros__parameters"]["lidar_param"]["topic"], "/old")
                with self.assertRaises(ProcessLookupError):
                    os.kill(int((root / "node.pid").read_text()), 0)

    def test_rigid_alignment_removes_rotation_and_translation(self):
        gt = poses([[0, 0, 0], [1, 0, 0], [1, 1, 1], [0, 2, 0]])
        est = gt.copy()
        est[:, 1:4] = gt[:, 1:4] @ np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]]) + [8, 3, 2]
        result = oxford.ate_metrics(gt, est, 0.05)
        self.assertLess(result["ate_rmse_m"], 1e-12)
        self.assertEqual(result["matched_poses"], 4)

    def test_scale_is_not_fitted(self):
        gt = poses([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
        est = gt.copy()
        est[:, 1:4] *= 2
        self.assertAlmostEqual(oxford.ate_metrics(gt, est, 0.05)["ate_rmse_m"], np.sqrt(0.5))

    def test_timestamp_mismatch_fails(self):
        gt = poses([[0, 0, 0], [1, 0, 0], [1, 1, 0]])
        est = gt.copy()
        est[:, 0] += 0.5
        with self.assertRaisesRegex(ValueError, "timestamp matches"):
            oxford.ate_metrics(gt, est, 0.05)

    def test_associations_do_not_reuse_gt(self):
        gt = poses([[0, 0, 0], [1, 0, 0], [1, 1, 0]])
        est = np.repeat(gt, 2, axis=0)
        est[1::2, 0] += 0.01
        result = oxford.ate_metrics(gt, est, 0.05)
        self.assertEqual(result["matched_poses"], 3)
        self.assertEqual(result["association_ratio"], 0.5)

    def test_metadata_copy_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seq = sequence(root)
            bag, metadata, topic, _ = oxford.inspect_sequence(seq, None)
            before = (bag / "metadata.yaml").read_bytes()
            destination = root / "compat"
            destination.mkdir()
            oxford.compatible_bag(bag, metadata, destination)
            converted = yaml.safe_load((destination / "metadata.yaml").read_text())["rosbag2_bagfile_information"]
            self.assertEqual(converted["version"], 8)
            self.assertEqual(converted["topics_with_message_count"][0]["topic_metadata"]["offered_qos_profiles"], "")
            self.assertEqual((destination / "rosbag_mcap_0.mcap").read_bytes(), b"fixture")
            self.assertEqual((bag / "metadata.yaml").read_bytes(), before)
            self.assertEqual(metadata["rosbag2_bagfile_information"]["version"], 9)
            self.assertEqual(topic, "/lidar/points")

    def test_ambiguous_topic_requires_override(self):
        with tempfile.TemporaryDirectory() as folder:
            seq = sequence(Path(folder), extra_topic=True)
            with self.assertRaisesRegex(ValueError, "--lidar-topic"):
                oxford.inspect_sequence(seq, None)
            self.assertEqual(oxford.inspect_sequence(seq, "/other/points")[2], "/other/points")

    def test_discovery_and_partial_failure_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sequence(root)
            broken = sequence(root, "2024-03-13-observatory-quarter-01")
            (broken / "rosbag_mcap/metadata.yaml").unlink()
            output = root / "results"
            # Dry-run must not touch the ROS/GPU execution path.
            with patch.object(oxford, "run_sequence", side_effect=AssertionError("ROS executed")):
                result = oxford.main(["--dataset-dir", str(root), "--output-dir", str(output), "--dry-run"])
            self.assertEqual(result, 1)
            rows = json.loads((output / "validation.json").read_text())
            self.assertEqual([r["status"] for r in rows], ["validated_only", "failed"])
            self.assertFalse((output / "summary.json").exists())
            with self.assertRaisesRegex(ValueError, "Unknown sequences"):
                oxford.discover(root, ["unknown"])

    def test_all_thirteen_sequences_are_discovered(self):
        names = [
            "2024-03-12-keble-college-02", "2024-03-12-keble-college-03",
            "2024-03-12-keble-college-04", "2024-03-12-keble-college-05",
            "2024-03-13-observatory-quarter-01", "2024-03-13-observatory-quarter-02",
            "2024-03-14-blenheim-palace-01", "2024-03-14-blenheim-palace-02",
            "2024-03-14-blenheim-palace-05", "2024-03-18-christ-church-02",
            "2024-03-18-christ-church-03", "2024-03-20-christ-church-05",
            "2024-05-20-bodleian-library-02",
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in names:
                sequence(root, name)
            self.assertEqual([p.name for p in oxford.discover(root, None)], sorted(names))

    def test_existing_summary_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sequence(root)
            output = root / "results"
            output.mkdir()
            summary = output / "summary.json"
            summary.write_text("keep me")
            with self.assertRaises(SystemExit):
                oxford.main(["--dataset-dir", str(root), "--output-dir", str(output)])
            self.assertEqual(summary.read_text(), "keep me")

    def test_bad_tum_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.tum"
            bad = poses([[0, 0, 0], [1, 0, 0], [1, 1, 0]])
            bad[1, 0] = bad[0, 0]
            np.savetxt(path, bad)
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                oxford.load_tum(path)


if __name__ == "__main__":
    unittest.main()
