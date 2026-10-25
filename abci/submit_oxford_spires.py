#!/usr/bin/env python3
"""Submit one rt_HG PBS job per Oxford Spires or RAL 2026 bag after a shared build."""

import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_oxford_spires import discover, inspect_sequence, load_tum, positive, sequence_id


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def make_task(directory, command, domain=None):
    directory.mkdir(parents=True)
    status = shlex.quote(str(directory / "status.json"))
    temporary = shlex.quote(str(directory / "status.json.tmp"))
    lines = ["#!/bin/bash", "set -euo pipefail", f"status={status}", f"temporary={temporary}",
             '[[ ! -e "$status" ]] || { echo "This task has already started; submit a new run" >&2; exit 1; }',
             'printf \'{"state":"running"}\\n\' > "$temporary"', 'mv -- "$temporary" "$status"',
             "finish() {", '  code="$?"', '  state=failed', '  [[ "$code" != 0 ]] || state=completed',
             '  printf \'{"state":"%s","exit_code":%d}\\n\' "$state" "$code" > "$temporary"',
             '  mv -- "$temporary" "$status"', '  exit "$code"', "}", "trap finish EXIT",
             "trap 'exit 130' INT", "trap 'exit 143' TERM"]
    if domain is not None:
        lines.append(f"export ROS_DOMAIN_ID={domain}")
    lines.append(shlex.join(command))
    (directory / "oxford-task.sh").write_text("\n".join(lines) + "\n")


def submit(args):
    if os.environ.get("SINGULARITY_CONTAINER") or os.environ.get("APPTAINER_CONTAINER"):
        raise ValueError("Run the batch submitter on the host, outside the container")
    if not args.project or not args.project.strip() or not args.queue or not args.queue.strip():
        raise ValueError("Export FRAI and FRAI_RES, or specify --project and --queue")
    if not re.fullmatch(r"[0-9]+:[0-5][0-9]:[0-5][0-9]", args.walltime) or not any(
            int(part) for part in args.walltime.split(":")):
        raise ValueError("--walltime must be a positive H:MM:SS duration")
    if args.build_jobs < 1:
        raise ValueError("--build-jobs must be positive")
    dataset = args.dataset_dir.expanduser().resolve()
    image = args.image.expanduser().resolve(strict=True)
    if not image.is_file() or image.stat().st_size == 0:
        raise ValueError("--image must be a nonempty SIF file")
    sequences = discover(dataset, args.sequence, args.layout)
    if any(any(character in sequence_id(path, args.layout) for character in ("\n", "\r", ",", ":")) for path in sequences):
        raise ValueError("Sequence names cannot contain PBS/bind separators")
    if args.layout == "ral2026":
        # Validate all metadata and LiDAR GT on the host before submitting anything.
        for sequence in sequences:
            _, _, _, gt = inspect_sequence(sequence, args.lidar_topic, args.layout, args.gt_file)
            load_tum(gt)
    domain_base = args.domain_base if args.domain_base is not None else (0 if args.layout == "ral2026" else 74)
    if not 0 <= domain_base <= 101 or domain_base + len(sequences) - 1 > 101:
        raise ValueError("Choose --domain-base so all per-sequence ROS domains stay in 0..101")
    run_id = uuid.uuid4().hex[:12]
    default_run = Path.home() / "svnicp-work/abci" / (args.layout + datetime.now().strftime("_%Y%m%d_%H%M%S_") + run_id)
    run = (args.run_dir or default_run).expanduser().resolve()
    work = (args.work_dir or run / "work").expanduser().resolve()
    if args.skip_build and not (work / "svnicp_ws/install/setup.bash").is_file():
        raise ValueError("--skip-build needs --work-dir with svnicp_ws/install/setup.bash")
    if not args.plan and not shutil.which(args.qsub):
        raise ValueError("qsub executable is required")
    for path in (ROOT, dataset, work, run, image):
        if any(character in str(path) for character in ("\n", "\r", ",", ":")):
            raise ValueError(f"Path contains an unsupported PBS/bind separator: {path}")
    run.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=True)
    results_relative = Path("results") / (args.layout + "_batch_" + run_id)
    launcher = ["bash", str(ROOT / "docker/start_singularity.sh"), "--image", str(image),
                "--dataset-dir", str(dataset), "--work-dir", str(work)]
    # Pass only known build knobs to the host launcher, independently of qsub's environment.
    task_prefix = ["env", "BUILD_JOBS=" + str(args.build_jobs)]
    if args.cuda_architectures:
        task_prefix.append("CMAKE_CUDA_ARCHITECTURES=" + args.cuda_architectures)
    manifest = {"repo_root": str(ROOT), "dataset_dir": str(dataset), "image": str(image), "layout": args.layout,
                "work_dir": str(work), "results_dir": str(work / results_relative),
                "walltime": args.walltime, "plan_only": args.plan, "prepare": None, "jobs": []}
    manifest_path = run / "manifest.json"
    base = [args.qsub, "-P", args.project, "-q", args.queue, "-l", "select=1",
            "-l", "walltime=" + args.walltime, "-v", "RTYPE=rt_HG"]

    def enqueue(row, name, dependency=None):
        directory = Path(row["job_dir"])
        command = [*base, "-N", name, "-S", "/bin/bash", "-o", str(directory / "stdout.log"),
                   "-e", str(directory / "stderr.log")]
        if dependency:
            command.extend(["-W", "depend=afterok:" + dependency])
        command.append(str(ROOT / "abci/job.sh"))
        row.update(command=command, submission_state="planned")
        save_json(manifest_path, manifest)
        print(f"cwd={shlex.quote(str(directory))} {shlex.join(command)}", flush=True)
        if args.plan:
            return "<build-job-id>" if name == "oxford_build" else None
        try:
            response = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=False)
            row.update(submission_stdout=response.stdout.strip(), submission_stderr=response.stderr.strip(),
                       submission_exit_code=response.returncode)
            if response.returncode:
                row["submission_state"] = "failed"
                print(response.stderr.strip() or "qsub failed", file=sys.stderr)
                return None
            job_id = response.stdout.strip()
            # A successful but unrecognized response must not trigger duplicate submission.
            if not re.fullmatch(r"[0-9]+(?:\.[A-Za-z0-9_.-]+)?", job_id):
                row["submission_state"] = "unknown"
                raise ValueError(f"qsub succeeded but returned an unrecognized job ID: {job_id!r}; inspect {manifest_path}")
            row.update(submission_state="submitted", job_id=job_id)
            return job_id
        except OSError as error:
            row.update(submission_state="failed", submission_stderr=str(error))
            print(str(error), file=sys.stderr)
            return None
        finally:
            save_json(manifest_path, manifest)

    prepare_id = None
    if not args.skip_build:
        directory = run / "prepare"
        make_task(directory, [*task_prefix, *launcher, "--oxford-spires", "--build-only"])
        manifest["prepare"] = {"job_dir": str(directory)}
        prepare_id = enqueue(manifest["prepare"], "oxford_build")
        if not prepare_id:
            print(f"Build job was not submitted; no sequence jobs submitted. Manifest: {manifest_path}")
            return 1
    failed = False
    for index, sequence in enumerate(sequences):
        name = sequence_id(sequence, args.layout)
        directory = run / "jobs" / name
        container_output = "/work/" + (results_relative / name).as_posix()
        if args.layout == "ral2026":
            evaluation = ["scripts/run_oxford_spires_singularity.sh", "--layout", args.layout]
        else:
            evaluation = ["scripts/my_startup.sh"]
        command = [*task_prefix, "ROS_LOG_DIR=" + container_output + "/ros_logs", *launcher,
                   "--", "bash", *evaluation, "--sequence", name,
                   "--skip-build", "--output-dir", container_output, "--play-rate", str(args.play_rate)]
        if args.lidar_topic:
            command.extend(["--lidar-topic", args.lidar_topic])
        if args.gt_file:
            command.extend(["--gt-file", args.gt_file])
        make_task(directory, command, domain_base + index)
        row = {"sequence": name, "job_dir": str(directory),
               "results_dir": str(work / results_relative / name), "ros_domain_id": domain_base + index}
        if args.layout == "ral2026":
            row["speed"] = sequence.parent.name
        manifest["jobs"].append(row)
        job_id = enqueue(row, f"oxford_{index + 1:03d}", prepare_id)
        failed |= not args.plan and job_id is None
    print(f"{len(sequences)} sequence job(s); manifest: {manifest_path}", flush=True)
    return int(failed)


def collect(args):
    run = args.run_dir.expanduser().resolve()
    manifest = json.loads((run / "manifest.json").read_text())
    prepare = manifest.get("prepare")
    prepare_failed = False
    if prepare:
        path = Path(prepare["job_dir"]) / "status.json"
        prepare_status = json.loads(path.read_text()) if path.exists() else {}
        prepare_failed = prepare.get("submission_state") == "failed" or prepare_status.get("state") == "failed"
    rows = []
    for job in manifest["jobs"]:
        directory = Path(job["job_dir"])
        path = directory / "status.json"
        status = json.loads(path.read_text()) if path.exists() else {}
        row = {"sequence": job["sequence"], "status": "pending", "error": "",
               "job_id": job.get("job_id", ""), "job_dir": str(directory), "results_dir": job["results_dir"]}
        if "speed" in job:
            row["speed"] = job["speed"]
        result = Path(job["results_dir"]) / "summary.json"
        if job.get("submission_state") == "failed":
            row.update(status="failed", error=job.get("submission_stderr", "qsub failed"))
        elif status.get("state") == "completed" and result.exists():
            values = json.loads(result.read_text())
            if len(values) != 1 or values[0].get("sequence") != job["sequence"]:
                raise ValueError(f"Unexpected sequence results: {result}")
            row.update(values[0])
        elif status.get("state") == "failed":
            row.update(status="failed", error=f"Job exit code {status.get('exit_code')}; inspect job logs")
            if result.exists():
                values = json.loads(result.read_text())
                if len(values) == 1 and values[0].get("sequence") == job["sequence"]:
                    row.update(values[0])
                    row["status"] = "failed"
        elif status.get("state") == "completed":
            row.update(status="failed", error="Job completed without summary.json")
        elif prepare_failed:
            row.update(status="blocked", error="Shared build failed; inspect prepare logs and qstat")
        elif status.get("state") == "running":
            row["status"] = "running"
        rows.append(row)
    save_json(run / "summary.json", rows)
    if rows:
        with (run / "summary.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=sorted(set().union(*(row.keys() for row in rows))))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    if prepare_failed or any(row["status"] in {"failed", "blocked"} for row in rows):
        return 1
    return 2 if not rows or any(row["status"] != "completed" for row in rows) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    start = commands.add_parser("submit", help="Submit a build job and one dependent GPU job per sequence")
    start.add_argument("--dataset-dir", type=Path, default=Path.home() / "dataset")
    start.add_argument("--layout", choices=("oxford", "ral2026"), default="oxford")
    start.add_argument("--gt-file", help="GT path relative to each sequence; RAL default: ground-truth-lidar-tum.txt")
    start.add_argument("--image", type=Path, default=ROOT / "images/svnicp-humble-cuda124-uv.sif")
    start.add_argument("--run-dir", type=Path, help="New shared directory for manifest and PBS logs")
    start.add_argument("--work-dir", type=Path, help="Shared /work bind (default: <run-dir>/work)")
    start.add_argument("--project", default=os.environ.get("FRAI"))
    start.add_argument("--queue", default=os.environ.get("FRAI_RES"))
    start.add_argument("--walltime", default="1:00:00")
    start.add_argument("--qsub", default="qsub")
    start.add_argument("--sequence", action="append")
    start.add_argument("--play-rate", type=positive, default=0.05)
    start.add_argument("--lidar-topic")
    start.add_argument("--domain-base", type=int, help="First ROS domain (default: Oxford 74, RAL 0)")
    start.add_argument("--build-jobs", type=int, default=2)
    start.add_argument("--cuda-architectures", default=os.environ.get("CMAKE_CUDA_ARCHITECTURES"))
    start.add_argument("--skip-build", action="store_true")
    start.add_argument("--plan", action="store_true", help="Generate tasks and print commands without submitting")
    start.set_defaults(function=submit)
    merge = commands.add_parser("collect", help="Merge results; exit 2 while any jobs are unfinished")
    merge.add_argument("--run-dir", type=Path, required=True)
    merge.set_defaults(function=collect)
    args = parser.parse_args(argv)
    try:
        return args.function(args)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
