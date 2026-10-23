import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class VoxelMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("g++ is required for the voxel map tests")
        cls.build = tempfile.TemporaryDirectory()
        root = Path(cls.build.name)
        source = root / "voxel_map.cpp"
        source.write_text('''#include "core/VoxelMapUtils.h"
#include <array>
#include <cassert>
#include <string>
#include <unordered_map>
#if __has_include(<tsl/robin_map.h>)
#include <tsl/robin_map.h>
#define HAVE_ROBIN_MAP 1
#endif

struct VoxelHash {
  std::size_t operator()(const std::array<std::int32_t, 3> &v) const {
    return svnicp::hash_voxel_index(v[0], v[1], v[2]);
  }
};

int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string mode = argv[1];
  if (mode == "hash") {
    std::array<bool, 32> buckets{};
    for (int x = -100; x < 100; ++x)
      for (int y = -100; y < 100; ++y)
        buckets[(svnicp::hash_voxel_index(x, y, 17) >> 20) & 31] = true;
    for (bool used: buckets) assert(used);
    assert(svnicp::hash_voxel_index(-1, 0, 0) != svnicp::hash_voxel_index(1, 0, 0));
    return 0;
  }
#ifdef HAVE_ROBIN_MAP
  if (mode == "growth") {
    tsl::robin_map<std::array<std::int32_t, 3>, int, VoxelHash> large;
    for (int x = 0; x < 120; ++x)
      for (int y = 0; y < 100; ++y)
        for (int z = 0; z < 100; ++z) {
          large.insert({{x, y, z}, 1});
          // Stop the old hash before it can exhaust host memory.
          assert(large.bucket_count() <= 8 * 1024 * 1024);
        }
    assert(large.size() == 1200000);
    return 0;
  }
  tsl::robin_map<int, double> map;
#else
  if (mode == "growth") return 77;
  std::unordered_map<int, double> map;
#endif
  const auto distance = [](double value) { return value; };
  if (mode == "range") {
    map = {{1, 1.}, {2, 4.}, {3, 9.}, {4, 16.}};
    svnicp::prune_local_map(map, distance, 9., 0);
    assert(map.size() == 3 && map.count(1) && map.count(2) && map.count(3));
  } else if (mode == "budget") {
    map = {{1, 1.}, {2, 4.}, {3, 9.}, {4, 16.}};
    svnicp::prune_local_map(map, distance, 100., 2);
    assert(map.size() == 2 && map.count(1) && map.count(2));
  } else if (mode == "stress") {
    for (int step = 0; step < 2000; ++step) {
      for (int i = 0; i < 100; ++i) map[step * 10 + i] = 0.;
      for (const auto &[key, value]: map) {
        (void)value;
        map[key] = (key - step * 10.) * (key - step * 10.);
      }
      svnicp::prune_local_map(map, distance, 2500., 40);
      assert(map.size() <= 40);
      for (const auto &[key, value]: map) { (void)key; assert(value <= 2500.); }
    }
    map.clear();
    svnicp::prune_local_map(map, distance, 0., 1);
    assert(map.empty());
  } else {
    assert(false);
  }
}
''')
        cls.binary = root / "voxel_map"
        include = Path(__file__).resolve().parents[2] / "svn-icp/include"
        compiled = subprocess.run([compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                                   "-I", str(include), str(source), "-o", str(cls.binary)],
                                  check=False, capture_output=True, text=True)
        if compiled.returncode:
            raise RuntimeError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def test_hash_uses_bits_beyond_the_old_twenty_bit_limit(self):
        subprocess.run([str(self.binary), "hash"], check=True)

    def test_million_voxel_robin_map_does_not_grow_without_limit(self):
        result = subprocess.run([str(self.binary), "growth"], check=False, timeout=45)
        if result.returncode == 77:
            self.skipTest("robin-map-dev headers are required for the large hash table test")
        self.assertEqual(result.returncode, 0)

    def test_range_pruning_with_unlimited_count(self):
        subprocess.run([str(self.binary), "range"], check=True)

    def test_budget_keeps_nearest_voxels(self):
        subprocess.run([str(self.binary), "budget"], check=True)

    def test_map_stays_bounded_over_long_motion(self):
        subprocess.run([str(self.binary), "stress"], check=True)


if __name__ == "__main__":
    unittest.main()
