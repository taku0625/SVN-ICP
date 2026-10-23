import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class BatchProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("g++ is required for the progress writer test")
        cls.build = tempfile.TemporaryDirectory()
        root = Path(cls.build.name)
        source = root / "progress.cpp"
        source.write_text('''#include "core/BatchProgress.h"
#include <iostream>
int main(int argc, char **argv) {
  svnicp::BatchProgress progress;
  if (argc == 1) {
    progress.complete(1);
    return 0;
  }
  try {
    progress.configure(argv[1]);
    progress.complete(1710256319814613000LL);
    progress.complete(1710256319814613001LL);
  } catch (const std::exception &error) {
    std::cerr << error.what();
    return 1;
  }
}
''')
        cls.binary = root / "progress"
        include = Path(__file__).resolve().parents[2] / "svn-icp/include"
        subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-I", str(include), str(source), "-o", str(cls.binary)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def test_disabled_writer_creates_no_files(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([str(self.binary)], cwd=directory, check=True)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_atomic_snapshot_preserves_nanoseconds_and_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            subprocess.run([str(self.binary), str(path)], check=True)
            self.assertEqual(json.loads(path.read_text()), {
                "last_processed_timestamp_ns": 1710256319814613001,
                "consumed_lidar_frames": 2,
            })
            self.assertFalse(Path(str(path) + ".tmp").exists())

    def test_unwritable_destination_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing/progress.json"
            result = subprocess.run([str(self.binary), str(path)], check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Cannot write batch progress", result.stderr)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
