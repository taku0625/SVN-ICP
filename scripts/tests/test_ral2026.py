import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import yaml

from test_oxford_spires import oxford, poses, sequence


SEQUENCES = (
    "blenheim-palace-01", "blenheim-palace-02", "blenheim-palace-05", "bodleian-library-02",
    "christ-church-02", "christ-church-03", "christ-church-05", "keble-college-02",
    "keble-college-03", "keble-college-04", "keble-college-05", "observatory-quarter-01",
    "observatory-quarter-02",
)


def ral_sequence(root, speed="speed1", name="keble-college-02"):
    path = sequence(root / speed, name)
    bag = path / "bag"
    (path / "rosbag_mcap").rename(bag)
    (bag / "rosbag_mcap_0.mcap").rename(bag / "bag_0.mcap")
    metadata = yaml.safe_load((bag / "metadata.yaml").read_text())
    metadata["rosbag2_bagfile_information"]["relative_file_paths"] = ["bag_0.mcap"]
    (bag / "metadata.yaml").write_text(yaml.safe_dump(metadata))
    (path / "processed/trajectory/gt-tum.txt").rename(path / "ground-truth-lidar-tum.txt")
    # Distinct body-frame positions expose accidental use of the other GT file.
    np.savetxt(path / "ground-truth-tum.txt", poses([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]))
    return path


class Ral2026Tests(unittest.TestCase):
    def test_discovery_preserves_all_65_speed_variants_and_selects_exact_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for speed in range(1, 6):
                for name in SEQUENCES:
                    ral_sequence(root, f"speed{speed}", name)
            found = oxford.discover(root, None, "ral2026")
            self.assertEqual(len(found), 65)
            self.assertEqual(len({oxford.sequence_id(path, "ral2026") for path in found}), 65)
            chosen = "speed4/keble-college-02"
            self.assertEqual(oxford.discover(root, [chosen], "ral2026"), [root / chosen])
            self.assertEqual(len(oxford.discover(root / "speed1", None, "ral2026")), 13)
            self.assertEqual(oxford.sequence_id(oxford.discover(root / chosen, None, "ral2026")[0], "ral2026"), chosen)
            with self.assertRaisesRegex(ValueError, "Unknown sequences"):
                oxford.discover(root, ["keble-college-02"], "ral2026")

    def test_inspection_uses_lidar_gt_and_accepts_explicit_relative_gt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = ral_sequence(Path(directory))
            bag, metadata, topic, gt = oxford.inspect_sequence(path, None, "ral2026")
            self.assertEqual(bag, path / "bag")
            self.assertEqual(topic, "/lidar/points")
            self.assertEqual(gt, path / "ground-truth-lidar-tum.txt")
            self.assertEqual(metadata["rosbag2_bagfile_information"]["relative_file_paths"], ["bag_0.mcap"])
            self.assertEqual(oxford.inspect_sequence(path, None, "ral2026", "ground-truth-tum.txt")[3],
                             path / "ground-truth-tum.txt")
            for unsafe in ("../ground-truth-tum.txt", "/tmp/ground-truth-tum.txt"):
                with self.assertRaisesRegex(ValueError, "relative path"):
                    oxford.inspect_sequence(path, None, "ral2026", unsafe)

    def test_evaluation_keeps_duplicate_leaf_names_separate_and_scores_lidar_trajectory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            paths = [ral_sequence(dataset, speed) for speed in ("speed1", "speed2")]
            original = [(path / "bag/metadata.yaml").read_bytes() for path in paths]
            output = root / "results"

            def run(bag, topic, config, destination, args):
                self.assertTrue((bag / "bag_0.mcap").is_symlink())
                self.assertEqual(yaml.safe_load((bag / "metadata.yaml").read_text())[
                    "rosbag2_bagfile_information"]["version"], 8)
                np.savetxt(destination / "trajectory.tum", poses([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]))
                return {"lidar_frames": 4}

            with patch.object(oxford, "run_sequence", side_effect=run) as evaluator:
                code = oxford.main(["--layout", "ral2026", "--dataset-dir", str(dataset), "--output-dir", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(evaluator.call_count, 2)
            rows = json.loads((output / "summary.json").read_text())
            self.assertEqual([row["sequence"] for row in rows], ["speed1/keble-college-02", "speed2/keble-college-02"])
            for row in rows:
                self.assertEqual(row["status"], "completed")
                self.assertLess(row["ate_rmse_m"], 1e-10)
                self.assertTrue((output / row["sequence"] / "trajectory.tum").exists())
            self.assertEqual(original, [(path / "bag/metadata.yaml").read_bytes() for path in paths])

    def test_missing_lidar_gt_never_falls_back_to_body_gt_or_starts_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = ral_sequence(root / "dataset")
            (path / "ground-truth-lidar-tum.txt").unlink()
            with patch.object(oxford, "run_sequence") as evaluator:
                code = oxford.main(["--layout", "ral2026", "--dataset-dir", str(root / "dataset"),
                                    "--output-dir", str(root / "output")])
            self.assertEqual(code, 1)
            evaluator.assert_not_called()
            row = json.loads((root / "output/summary.json").read_text())[0]
            self.assertEqual(row["status"], "failed")
            self.assertIn("ground-truth-lidar-tum.txt", row["error"])


if __name__ == "__main__":
    unittest.main()
