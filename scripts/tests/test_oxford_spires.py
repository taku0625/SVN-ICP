import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
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
    def test_cloud_bounds_keeps_exact_nanoseconds_and_accepts_single_frame(self):
        stamp = types.SimpleNamespace(sec=1710256319, nanosec=814613001)
        cloud = types.SimpleNamespace(header=types.SimpleNamespace(stamp=stamp),
                                      fields=[types.SimpleNamespace(name=name) for name in ("x", "y", "z")])

        class Reader:
            def __init__(self):
                self.messages = [cloud]

            def open(self, *args):
                pass

            def set_filter(self, *args):
                pass

            def has_next(self):
                return bool(self.messages)

            def read_next(self):
                return "/points", self.messages.pop(0), 0

        reader = types.SimpleNamespace(SequentialReader=Reader, StorageOptions=lambda **kwargs: None,
                                       ConverterOptions=lambda *args: None, StorageFilter=lambda **kwargs: None)
        serialization = types.SimpleNamespace(deserialize_message=lambda data, cls: data)
        messages = types.SimpleNamespace(PointCloud2=object)
        with patch.dict(sys.modules, {"rosbag2_py": reader, "rclpy.serialization": serialization,
                                      "sensor_msgs.msg": messages}):
            first, last, count, exact = oxford.cloud_bounds(Path("fixture"), "/points")
        self.assertEqual(count, 1)
        self.assertEqual(first, last)
        self.assertEqual(exact, 1710256319814613001)

    def test_processing_wait_uses_consumption_not_trajectory_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            end = 1710256319814613001
            clock = [0.0]
            updates = iter([(1, end - 1), (2, end)])
            path.write_text(json.dumps({"consumed_lidar_frames": 0,
                                        "last_processed_timestamp_ns": 0}))

            def advance(duration):
                clock[0] += duration
                count, stamp = next(updates)
                path.write_text(json.dumps({"consumed_lidar_frames": count,
                                            "last_processed_timestamp_ns": stamp}))

            node = types.SimpleNamespace(poll=lambda: None)
            with patch.object(oxford.time, "monotonic", side_effect=lambda: clock[0]), \
                 patch.object(oxford.time, "sleep", side_effect=advance):
                result = oxford.wait_for_processing(node, path, end, 0.75)
            self.assertEqual(result["last_processed_timestamp_ns"], end)
            self.assertEqual(result["consumed_lidar_frames"], 2)
            self.assertGreater(clock[0], 0.75)

    def test_processing_stall_remains_an_execution_error(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [0.0]

            def advance(duration):
                clock[0] += duration

            node = types.SimpleNamespace(poll=lambda: None)
            with patch.object(oxford.time, "monotonic", side_effect=lambda: clock[0]), \
                 patch.object(oxford.time, "sleep", side_effect=advance), \
                 self.assertRaisesRegex(TimeoutError, "No LiDAR processing progress"):
                oxford.wait_for_processing(node, Path(directory) / "missing.json", 100, 0.5)

    def test_initial_zero_timestamp_is_not_processing_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            path.write_text('{"last_processed_timestamp_ns": 0, "consumed_lidar_frames": 0}')
            node = types.SimpleNamespace(poll=lambda: None)

            def complete(duration):
                path.write_text('{"last_processed_timestamp_ns": 0, "consumed_lidar_frames": 1}')

            with patch.object(oxford.time, "sleep", side_effect=complete) as sleep:
                result = oxford.wait_for_processing(node, path, 0, 5)
            sleep.assert_called_once()
            self.assertEqual(result["consumed_lidar_frames"], 1)

    def test_nested_discovery_and_single_sequence_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = sequence(root / "data/sequences")
            second = sequence(root / "data/sequences", "2024-03-12-keble-college-03")
            # Sequence internals must not be mistaken for more sequences.
            sequence(first / "raw", "2024-03-12-keble-college-04")
            self.assertEqual(oxford.discover(root, None), [first, second])
            self.assertEqual(oxford.discover(root, [second.name]), [second])
            self.assertEqual(oxford.discover(first, None), [first])

    def test_structural_discovery_accepts_sequence_without_date_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = sequence(root, "keble-02")
            self.assertEqual(oxford.discover(root, None), [path])

    def test_missing_sequences_report_visible_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            (root / "README.md").write_text("dataset tools")
            with self.assertRaisesRegex(ValueError, r"Visible entries: README.md, scripts/.*Check DATASET_DIR"):
                oxford.discover(root, None)

    def test_duplicate_nested_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sequence(root / "copy1")
            sequence(root / "copy2")
            with self.assertRaisesRegex(ValueError, "Duplicate sequence name"):
                oxford.discover(root, None)

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
        for playback_exit, short, node_exit in ((0, False, 0), (0, True, 0), (7, False, 0), (0, False, 247)):
            with self.subTest(playback_exit=playback_exit, short=short, node_exit=node_exit), \
                 tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                executable = root / "ros2"
                executable.write_text("""#!/usr/bin/env python3
import os, pathlib, sys, time, yaml
if sys.argv[1] == 'run':
    params = yaml.safe_load(pathlib.Path(sys.argv[-1]).read_text())['/svnicp/SVNICP']['ros__parameters']
    output = pathlib.Path(params['tum_trajectory_path'])
    start, step = (101.0, 0.1) if os.environ['FIXTURE_SHORT_TRAJECTORY'] == '1' else (100.0, 1.0)
    output.write_text(''.join(f'{start + i * step} {i} 0 0 0 0 0 1\\n' for i in range(3)))
    pathlib.Path(params['batch_progress_path']).write_text(
        '{"last_processed_timestamp_ns": 102000000000, "consumed_lidar_frames": 3}')
    (output.parent / 'node.pid').write_text(str(os.getpid()))
    if os.environ['FIXTURE_NODE_EXIT'] != '0':
        raise SystemExit(int(os.environ['FIXTURE_NODE_EXIT']))
    while True: time.sleep(1)
else:
    raise SystemExit(int(os.environ['FIXTURE_PLAYBACK_EXIT']))
""")
                executable.chmod(0o755)
                monitor = types.SimpleNamespace(
                    count_subscribers=lambda topic, folder=root: int((folder / "node.pid").exists()),
                    destroy_node=lambda: None)
                context = types.SimpleNamespace(try_shutdown=lambda: None)
                fake_ros = types.SimpleNamespace(init=lambda **kwargs: None,
                                                create_node=lambda name, node=monitor, **kwargs: node)
                args = types.SimpleNamespace(startup_timeout=5, play_rate=0.05,
                                             read_ahead_queue_size=10, sequence_timeout=5,
                                             drain_timeout=5, end_tolerance=0.2)
                config = {"/svnicp/SVNICP": {"ros__parameters": {"lidar_param": {"topic": "/old"}}}}
                with patch.dict(sys.modules, {"rclpy": fake_ros,
                                              "rclpy.context": types.SimpleNamespace(Context=lambda ctx=context: ctx),
                                              "rclpy.signals": types.SimpleNamespace(
                                                  SignalHandlerOptions=types.SimpleNamespace(NO=0))}), \
                     patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"],
                                             "FIXTURE_PLAYBACK_EXIT": str(playback_exit),
                                             "FIXTURE_NODE_EXIT": str(node_exit),
                                             "FIXTURE_SHORT_TRAJECTORY": str(int(short))}), \
                     patch.object(oxford, "cloud_bounds", return_value=(100.0, 102.0, 3, 102000000000)):
                    if node_exit:
                        with self.assertRaisesRegex(RuntimeError, "247.*SIGKILL.*out-of-memory"):
                            oxford.run_sequence(root, "/points", config, root, args)
                    elif playback_exit:
                        with self.assertRaisesRegex(RuntimeError, "Bag playback failed: 7"):
                            oxford.run_sequence(root, "/points", config, root, args)
                    else:
                        result = oxford.run_sequence(root, "/points", config, root, args)
                        self.assertEqual(result["lidar_frames"], 3)
                        self.assertEqual(result["estimated_end"], 101.2 if short else 102.0)
                        self.assertEqual(result["consumed_lidar_frames"], 3)
                params = yaml.safe_load((root / "parameters.yaml").read_text())["/svnicp/SVNICP"]["ros__parameters"]
                self.assertEqual(params["lidar_param"]["topic"], "/points")
                self.assertEqual(params["auto_shutdown_progress"], 0.0)
                self.assertEqual(config["/svnicp/SVNICP"]["ros__parameters"]["lidar_param"]["topic"], "/old")
                with self.assertRaises(ProcessLookupError):
                    os.kill(int((root / "node.pid").read_text()), 0)

    def test_shutdown_does_not_replace_original_failure_or_interrupt(self):
        for failure in (RuntimeError("Node exited during playback: 247"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                class Context:
                    valid = True

                    def try_shutdown(self):
                        self.valid = False

                context = Context()

                def subscribe(topic, ctx=context, error=failure):
                    ctx.try_shutdown()  # Mimic ROS already having shut down.
                    raise error

                monitor = types.SimpleNamespace(count_subscribers=subscribe, destroy_node=lambda: None)
                fake_ros = types.SimpleNamespace(init=lambda **kwargs: None,
                                                create_node=lambda *args, node=monitor, **kwargs: node)
                config = {"/svnicp/SVNICP": {"ros__parameters": {"lidar_param": {}}}}
                args = types.SimpleNamespace(startup_timeout=5)
                with patch.dict(sys.modules, {"rclpy": fake_ros,
                                              "rclpy.context": types.SimpleNamespace(Context=lambda ctx=context: ctx),
                                              "rclpy.signals": types.SimpleNamespace(
                                                  SignalHandlerOptions=types.SimpleNamespace(NO=0))}), \
                     patch.object(oxford, "cloud_bounds", return_value=(100, 102, 3, 102000000000)), \
                     patch.object(oxford.subprocess, "Popen", return_value=object()), \
                     patch.object(oxford, "stop") as stop, \
                     self.assertRaises(type(failure)) as caught:
                    oxford.run_sequence(Path(folder), "/points", config, Path(folder), args)
                self.assertIs(caught.exception, failure)
                self.assertFalse(context.valid)
                self.assertEqual(stop.call_count, 1)

    def test_user_interrupt_is_saved_and_stops_remaining_sequences(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sequence(root / "data")
            sequence(root / "data", "2024-03-12-keble-college-03")
            output = root / "results"
            with patch.object(oxford, "run_sequence", side_effect=KeyboardInterrupt()) as run:
                result = oxford.main(["--dataset-dir", str(root / "data"), "--output-dir", str(output)])
            self.assertEqual(result, 130)
            self.assertEqual(run.call_count, 1)
            rows = json.loads((output / "summary.json").read_text())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "interrupted")
            self.assertEqual(rows[0]["error"], "Interrupted by user")

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

    def test_low_association_ratio_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sequence(root / "data")
            estimate = np.repeat(poses([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]), 2, axis=0)
            estimate[1::2, 0] += 0.01

            def run(bag, topic, config, output, args):
                np.savetxt(output / "trajectory.tum", estimate)
                return {"lidar_frames": 8, "consumed_lidar_frames": 8}

            output = root / "results"
            with patch.object(oxford, "run_sequence", side_effect=run):
                result = oxford.main(["--dataset-dir", str(root / "data"), "--output-dir", str(output),
                                      "--min-association-ratio", "0.99", "--end-tolerance", "0.01"])
            self.assertEqual(result, 0)
            row = json.loads((output / "summary.json").read_text())[0]
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["association_ratio"], 0.5)
            self.assertEqual(row["error"], "")

    def test_unscorable_gt_does_not_prevent_estimation(self):
        for problem in ("missing", "invalid", "offset"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                first = sequence(root / "data")
                sequence(root / "data", "2024-03-12-keble-college-03")
                gt = first / "processed/trajectory/gt-tum.txt"
                if problem == "missing":
                    gt.unlink()
                elif problem == "offset":
                    values = np.loadtxt(gt)
                    values[:, 0] += 5.0
                    np.savetxt(gt, values)
                else:
                    gt.write_text("invalid ground truth")

                def run(bag, topic, config, output, args):
                    np.savetxt(output / "trajectory.tum", poses([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]))
                    return {"lidar_frames": 4, "consumed_lidar_frames": 4}

                output = root / "results"
                with patch.object(oxford, "run_sequence", side_effect=run) as runner:
                    result = oxford.main(["--dataset-dir", str(root / "data"), "--output-dir", str(output)])
                self.assertEqual(result, 0)
                self.assertEqual(runner.call_count, 2)
                rows = json.loads((output / "summary.json").read_text())
                self.assertEqual([row["status"] for row in rows], ["completed", "completed"])
                self.assertIn("evaluation_warning", rows[0])
                self.assertIsNone(rows[0]["ate_rmse_m"])
                self.assertEqual(rows[1]["ate_rmse_m"], 0.0)

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
