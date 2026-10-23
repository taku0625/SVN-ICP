#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

namespace svnicp {
  inline std::size_t hash_voxel_index(std::int32_t x, std::int32_t y, std::int32_t z) {
    // Do not mask to 20 bits: large robin maps otherwise keep growing their
    // table to resolve clusters that rehashing can never separate.
    return (static_cast<std::size_t>(static_cast<std::uint32_t>(x)) * 73856093ULL)
         ^ (static_cast<std::size_t>(static_cast<std::uint32_t>(y)) * 19349669ULL)
         ^ (static_cast<std::size_t>(static_cast<std::uint32_t>(z)) * 83492791ULL);
  }

  // Copy keys before erasing: robin_map can relocate other entries on erase.
  template <typename Map, typename SquaredDistance>
  void prune_local_map(Map &map, SquaredDistance distance_squared,
                       double max_distance_squared, std::size_t max_voxels) {
    using Key = typename Map::key_type;
    std::vector<Key> remove;
    std::vector<std::pair<double, Key>> candidates;
    if (max_voxels) candidates.reserve(map.size());
    for (const auto &[key, cloud]: map) {
      const double distance = distance_squared(cloud);
      if (distance > max_distance_squared) {
        remove.push_back(key);
      } else if (max_voxels) {
        candidates.emplace_back(distance, key);
      }
    }
    if (max_voxels && candidates.size() > max_voxels) {
      const auto first_removed = candidates.begin() + max_voxels;
      std::nth_element(candidates.begin(), first_removed, candidates.end(),
                       [](const auto &left, const auto &right) { return left.first < right.first; });
      for (auto it = first_removed; it != candidates.end(); ++it) remove.push_back(it->second);
    }
    for (const auto &key: remove) map.erase(key);
  }
}
