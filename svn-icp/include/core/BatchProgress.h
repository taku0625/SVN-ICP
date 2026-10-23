#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>

namespace svnicp {
  class BatchProgress {
    std::filesystem::path path_;
    std::uint64_t consumed_lidar_frames_ = 0;

    void write(std::int64_t timestamp_ns) const {
      if (path_.empty()) return;
      auto temporary = path_;
      temporary += ".tmp";
      std::ofstream stream(temporary);
      stream << "{\"last_processed_timestamp_ns\":" << timestamp_ns
             << ",\"consumed_lidar_frames\":" << consumed_lidar_frames_ << "}\n";
      stream.close();
      if (!stream) throw std::runtime_error("Cannot write batch progress: " + temporary.string());
      std::filesystem::rename(temporary, path_);
    }

  public:
    void configure(const std::string &path) {
      path_ = path;
      consumed_lidar_frames_ = 0;
      write(0);
    }

    void complete(std::int64_t timestamp_ns) {
      if (path_.empty()) return;
      ++consumed_lidar_frames_;
      write(timestamp_ns);
    }
  };
}
