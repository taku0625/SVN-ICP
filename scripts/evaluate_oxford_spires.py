#!/usr/bin/env python3
"""Batch SVN-ICP on Oxford Spires ROS 2 bags and compute SE(3)-aligned ATE.

ROS imports are deferred so discovery and metric tests also run without ROS.
"""
import argparse
import contextlib
import copy
import csv
import json
import math
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import yaml


def load_tum(path):
    values = np.loadtxt(path, comments="#", ndmin=2)
    if values.shape[0] < 3 or values.shape[1] != 8 or not np.isfinite(values).all():
        raise ValueError(f"Expected at least three finite TUM poses: {path}")
    if np.any(np.diff(values[:, 0]) <= 0):
        raise ValueError(f"Timestamps must be strictly increasing: {path}")
    if np.any(np.linalg.norm(values[:, 4:], axis=1) < 1e-8):
        raise ValueError(f"Invalid quaternion: {path}")
    return values


def ate_metrics(gt, estimate, max_difference):
    """Nearest, unique timestamp association; rigid alignment without scale fitting."""
    indices = np.searchsorted(gt[:, 0], estimate[:, 0])
    candidates = []
    for est_index, index in enumerate(indices):
        for gt_index in (index - 1, index):
            if 0 <= gt_index < len(gt):
                delta = abs(gt[gt_index, 0] - estimate[est_index, 0])
                if delta <= max_difference:
                    candidates.append((delta, est_index, gt_index))
    used_est, used_gt, pairs = set(), set(), []
    for _, est_index, gt_index in sorted(candidates):
        if est_index not in used_est and gt_index not in used_gt:
            used_est.add(est_index)
            used_gt.add(gt_index)
            pairs.append((est_index, gt_index))
    if len(pairs) < 3:
        raise ValueError("Fewer than three timestamp matches with GT; check clocks and --max-difference")
    pairs.sort()
    x = estimate[[p[0] for p in pairs], 1:4]
    y = gt[[p[1] for p in pairs], 1:4]
    xc, yc = x - x.mean(axis=0), y - y.mean(axis=0)
    u, _, vt = np.linalg.svd(xc.T @ yc)
    correction = np.eye(3)
    correction[-1, -1] = np.linalg.det(u @ vt)
    rotation = u @ correction @ vt
    errors = np.linalg.norm(xc @ rotation - yc, axis=1)
    return {
        "matched_poses": len(pairs), "estimated_poses": len(estimate),
        "association_ratio": len(pairs) / len(estimate),
        "ate_rmse_m": float(np.sqrt(np.mean(errors ** 2))),
        "ate_mean_m": float(errors.mean()), "ate_median_m": float(np.median(errors)),
        "ate_max_m": float(errors.max()),
    }


def discover(root, selected):
    if not root.is_dir():
        raise ValueError(f"Dataset directory does not exist: {root}")
    sequences = []

    def traversal_error(error):
        raise ValueError(f"Cannot inspect dataset directory: {error}") from error

    # Stop at each sequence so that raw point clouds and bag contents are not
    # traversed. Accept both the dataset root and a parent containing it.
    for current, directories, _ in os.walk(root, onerror=traversal_error):
        directories[:] = [name for name in directories if name not in {".git", ".docker", "__pycache__"}]
        path = Path(current)
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}-.+", path.name) or "rosbag_mcap" in directories:
            sequences.append(path)
            directories.clear()
    sequences.sort()
    if not sequences:
        children = sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir())
        visible = ", ".join(children[:20]) or "(empty directory)"
        if len(children) > 20:
            visible += ", ..."
        raise ValueError(
            f"No Oxford Spires sequence directories found in {root}. "
            f"Visible entries: {visible}. Expected <sequence>/rosbag_mcap/metadata.yaml "
            "and <sequence>/processed/trajectory/gt-tum.txt. "
            "Check DATASET_DIR on the Docker host and its read-only mount."
        )
    names = {}
    for path in sequences:
        if path.name in names:
            raise ValueError(f"Duplicate sequence name {path.name}: {names[path.name]} and {path}. "
                             "Set --dataset-dir to one dataset root.")
        names[path.name] = path
    if selected:
        unknown = set(selected) - {p.name for p in sequences}
        if unknown:
            raise ValueError(f"Unknown sequences: {sorted(unknown)}")
        sequences = [p for p in sequences if p.name in selected]
    return sequences


def inspect_sequence(sequence, lidar_topic):
    bag = sequence / "rosbag_mcap"
    metadata = yaml.safe_load((bag / "metadata.yaml").read_text())
    info = metadata["rosbag2_bagfile_information"]
    for relative in info["relative_file_paths"]:
        path = (bag / relative).resolve()
        if not path.is_relative_to(bag.resolve()) or not path.is_file():
            raise ValueError(f"Missing or unsafe bag file: {relative}")
    if not info["relative_file_paths"]:
        raise ValueError(f"No bag files: {bag}")
    topics = [t["topic_metadata"]["name"] for t in info["topics_with_message_count"]
              if t["topic_metadata"]["type"] == "sensor_msgs/msg/PointCloud2"
              and t["message_count"] > 0]
    if lidar_topic:
        if lidar_topic not in topics:
            raise ValueError(f"LiDAR topic {lidar_topic} not in {topics}")
    elif len(topics) != 1:
        raise ValueError(f"Specify --lidar-topic; PointCloud2 topics: {topics}")
    else:
        lidar_topic = topics[0]
    gt = sequence / "processed/trajectory/gt-tum.txt"
    return bag, metadata, lidar_topic, gt


def compatible_bag(bag, metadata, destination):
    """Use a private Humble metadata copy; never change the read-only dataset."""
    metadata = copy.deepcopy(metadata)
    info = metadata["rosbag2_bagfile_information"]
    info["version"] = min(info.get("version", 8), 8)
    info.pop("custom_data", None)
    info.pop("ros_distro", None)
    for topic in info["topics_with_message_count"]:
        topic["topic_metadata"]["offered_qos_profiles"] = ""
        topic["topic_metadata"].pop("type_description_hash", None)
    for relative in info["relative_file_paths"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to((bag / relative).resolve())
    (destination / "metadata.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False))
    return destination


def cloud_bounds(bag, topic):
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import PointCloud2

    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id="mcap"),
                rosbag2_py.ConverterOptions("", ""))
    reader.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
    first = last = None
    count = 0
    while reader.has_next():
        _, data, _ = reader.read_next()
        cloud = deserialize_message(data, PointCloud2)
        if count == 0:
            if not {"x", "y", "z"}.issubset({f.name for f in cloud.fields}):
                raise ValueError("PointCloud2 is missing XYZ fields")
            first = cloud.header.stamp.sec + cloud.header.stamp.nanosec * 1e-9
        stamp = cloud.header.stamp.sec + cloud.header.stamp.nanosec * 1e-9
        last_timestamp_ns = cloud.header.stamp.sec * 1_000_000_000 + cloud.header.stamp.nanosec
        if last is not None and stamp < last:
            raise ValueError("LiDAR header timestamps are not monotonic")
        last = stamp
        count += 1
    if not count:
        raise ValueError("No LiDAR frames in the selected topic")
    return first, last, count, last_timestamp_ns


def node_exit_error(phase, returncode):
    # ros2 run forwards a child's -9 through sys.exit(), which becomes 247.
    detail = " (SIGKILL; check the host kernel log for out-of-memory)" if returncode in {-9, 137, 247} else ""
    return RuntimeError(f"Node exited {phase}: {returncode}{detail}")


def wait_for_processing(node, progress_path, last_timestamp_ns, timeout):
    """Wait for consumed input, independent of which scans produced odometry."""
    deadline = time.monotonic() + timeout
    consumed = -1
    while True:
        if node.poll() is not None:
            raise node_exit_error("before completion", node.returncode)
        try:
            progress = json.loads(progress_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            progress = {}
        if progress.get("consumed_lidar_frames", 0) > consumed:
            consumed = progress.get("consumed_lidar_frames", 0)
            deadline = time.monotonic() + timeout
        if consumed > 0 and progress.get("last_processed_timestamp_ns", -1) >= last_timestamp_ns:
            return progress
        if time.monotonic() > deadline:
            raise TimeoutError("No LiDAR processing progress before --drain-timeout; see svnicp.log and progress.json")
        time.sleep(0.5)


def stop(process):
    if process is None or process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run_sequence(bag, topic, config, output, args):
    import rclpy
    from rclpy.context import Context
    from rclpy.signals import SignalHandlerOptions

    first, last, count, last_timestamp_ns = cloud_bounds(bag, topic)
    trajectory = output / "trajectory.tum"
    progress_path = output / "progress.json"
    params = copy.deepcopy(config)
    node_params = params["/svnicp/SVNICP"]["ros__parameters"]
    node_params.update(dataset_duration=0.0, auto_shutdown_progress=0.0,
                       save_tum_trajectory=True, tum_trajectory_path=str(trajectory),
                       batch_progress_path=str(progress_path))
    node_params["lidar_param"]["topic"] = topic
    params_path = output / "parameters.yaml"
    params_path.write_text(yaml.safe_dump(params, sort_keys=False))
    # Force reliable delivery to match the existing node's subscriber QoS.
    qos = output / "qos.yaml"
    qos.write_text(yaml.safe_dump({topic: {"reliability": "reliable", "durability": "volatile",
                                         "history": "keep_last", "depth": 10}}))
    context = Context()
    # Keep Ctrl+C as KeyboardInterrupt and isolate shutdown between sequences.
    rclpy.init(context=context, signal_handler_options=SignalHandlerOptions.NO)
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(context.try_shutdown)
        monitor = rclpy.create_node("oxford_spires_batch_monitor", context=context)
        cleanup.callback(monitor.destroy_node)
        with (output / "svnicp.log").open("w") as node_log, (output / "playback.log").open("w") as player_log:
            node = subprocess.Popen([
                "ros2", "run", "svn-icp", "svnicp_lio_node", "--ros-args",
                "-r", "__node:=SVNICP", "-r", "__ns:=/svnicp", "--params-file", str(params_path)
            ], stdout=node_log, stderr=subprocess.STDOUT, start_new_session=True)
            cleanup.callback(stop, node)
            deadline = time.monotonic() + args.startup_timeout
            while monitor.count_subscribers(topic) == 0:
                if node.poll() is not None:
                    raise node_exit_error("during startup", node.returncode)
                if time.monotonic() > deadline:
                    raise TimeoutError("Node did not subscribe before startup timeout")
                # DDS graph discovery does not need a default-context executor.
                time.sleep(0.2)
            player = subprocess.Popen([
                "ros2", "bag", "play", str(bag), "--rate", str(args.play_rate),
                "--read-ahead-queue-size", str(args.read_ahead_queue_size),
                "--qos-profile-overrides-path", str(qos), "--topics", topic
            ], stdout=player_log, stderr=subprocess.STDOUT, start_new_session=True)
            cleanup.callback(stop, player)
            deadline = time.monotonic() + args.sequence_timeout
            while player.poll() is None:
                if node.poll() is not None:
                    raise node_exit_error("during playback", node.returncode)
                if time.monotonic() > deadline:
                    raise TimeoutError("Playback exceeded --sequence-timeout")
                time.sleep(0.5)
            if player.returncode != 0:
                raise RuntimeError(f"Bag playback failed: {player.returncode}")
            progress = wait_for_processing(node, progress_path, last_timestamp_ns, args.drain_timeout)
    result = {"lidar_frames": count, "lidar_start": first, "lidar_end": last,
              "consumed_lidar_frames": progress["consumed_lidar_frames"]}
    try:
        poses = load_tum(trajectory)
        result.update(estimated_start=float(poses[0, 0]), estimated_end=float(poses[-1, 0]))
    except (OSError, ValueError) as error:
        result["trajectory_warning"] = str(error)
    return result


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Must be finite and greater than zero")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sequence", action="append", help="Repeat to select a subset; default: all")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "svn-icp/config/oxford_spires.yaml")
    parser.add_argument("--lidar-topic", help="Required if a bag has multiple PointCloud2 topics")
    parser.add_argument("--play-rate", type=positive, default=0.05)
    parser.add_argument("--read-ahead-queue-size", type=int, default=10)
    parser.add_argument("--max-difference", type=positive, default=0.05, help="GT association tolerance in seconds")
    parser.add_argument("--min-association-ratio", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--end-tolerance", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--startup-timeout", type=positive, default=120)
    parser.add_argument("--drain-timeout", type=positive, default=180,
                        help="Maximum seconds without LiDAR processing progress after playback")
    parser.add_argument("--sequence-timeout", type=positive, default=86400)
    parser.add_argument("--dry-run", action="store_true", help="Validate layout, GT and metadata without ROS/GPU")
    parser.add_argument("--list-sequences", action="store_true", help="Show discovered sequence paths without building or evaluating")
    args = parser.parse_args(argv)
    if args.read_ahead_queue_size <= 0:
        parser.error("--read-ahead-queue-size must be positive")
    try:
        sequences = discover(args.dataset_dir.resolve(), args.sequence)
    except ValueError as error:
        parser.error(str(error))
    if args.list_sequences:
        print(f"Found {len(sequences)} sequence(s):", flush=True)
        for sequence in sequences:
            print(sequence, flush=True)
        return 0
    config = yaml.safe_load(args.config.read_text())
    if config["/svnicp/SVNICP"]["ros__parameters"]["estimator"] != "ICP":
        parser.error("This batch runner evaluates LiDAR-only ICP; KF calibration is not configured")
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.dry_run and (args.output_dir / "summary.json").exists():
        parser.error("Output already contains summary.json; choose a new --output-dir")
    rows = []
    interrupted = False
    for sequence in sequences:
        row = {"sequence": sequence.name, "status": "failed", "error": ""}
        try:
            bag, metadata, topic, gt = inspect_sequence(sequence, args.lidar_topic)
            row["lidar_topic"] = topic
            if args.dry_run:
                load_tum(gt)
                row["status"] = "validated_only"
            else:
                output = args.output_dir / sequence.name
                # Never truncate or silently reuse previous results.
                output.mkdir(exist_ok=False)
                with tempfile.TemporaryDirectory(prefix="svnicp_oxford_") as temporary:
                    playback_bag = compatible_bag(bag, metadata, Path(temporary))
                    row.update(run_sequence(playback_bag, topic, config, output, args))
                row["status"] = "completed"
                try:
                    row.update(ate_metrics(load_tum(gt), load_tum(output / "trajectory.tum"), args.max_difference))
                except Exception as error:  # noqa: BLE001
                    row.update(ate_rmse_m=None, ate_mean_m=None, ate_median_m=None,
                               ate_max_m=None, evaluation_warning=str(error))
        except KeyboardInterrupt:
            row.update(status="interrupted", error="Interrupted by user")
            interrupted = True
        except Exception as error:  # noqa: BLE001
            row["error"] = str(error)
        rows.append(row)
        print(json.dumps(row), flush=True)
        # Persist progress after each sequence, including failures.
        prefix = "validation" if args.dry_run else "summary"
        (args.output_dir / f"{prefix}.json").write_text(json.dumps(rows, indent=2) + "\n")
        columns = sorted(set().union(*(r.keys() for r in rows)))
        with (args.output_dir / f"{prefix}.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        if interrupted:
            return 130
    return int(any(row["status"] == "failed" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
