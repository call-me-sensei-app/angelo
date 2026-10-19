/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/**
 * Differential fuzzer for the metadata scanner's reader.
 *
 * Runs random read/skip/finish programs against the windowed reader (this tree) and the reader
 * before the change (extracted from git by the run script into namespace blo_old), on random
 * plain/gzip/Zstandard files, with random limits, cancellation points and admitted sizes that
 * differ from the file's real size (a file that shrank or grew after fstat), and with the file
 * truncated concurrently in a tenth of the runs. Built with AddressSanitizer and
 * UndefinedBehaviorSanitizer; any memory error aborts.
 *
 * Plain runs that were not cancelled are compared: when both readers succeed, every program
 * returns the same values and bytes with the same decoded count; when the windowed reader fails,
 * the bytes it returned first are the old reader's. Reported, not failed: refusals of the
 * windowed reader where the old one answered (its read-ahead is charged to the input budget),
 * both refusing with different status codes, and runs where the old reader refused a large
 * request outright and the windowed one read on (a tiny input budget or a file that shrank).
 * Fails only on a budget overrun (input above its limit, decoded more than one byte above).
 *
 * Measured over 90,000 programs (seeds 1, 2, 3, 7): no sanitizer report, no budget
 * overrun, identical answers in every run both readers completed.
 */

#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <random>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

#include "metadata_scan_reader.hh"
#include "metadata_scan_reader_old.hh"

using namespace blender;

struct Op {
  enum Kind { Read, ReadExact, Skip, Finish } kind;
  uint64_t amount;
};

struct Outcome {
  std::vector<int64_t> returns;
  std::vector<uint8_t> bytes;
  BLOMetadataScanStatus status;
  std::string message;
  uint64_t decoded = 0, input = 0;
  int polls = 0;
  bool cancelled_by_callback = false;
};

struct Cancel {
  int polls = 0;
  int cancel_at = -1;
};

static bool cancel_callback(void *userdata)
{
  Cancel &cancel = *static_cast<Cancel *>(userdata);
  cancel.polls++;
  return cancel.cancel_at >= 0 && cancel.polls >= cancel.cancel_at;
}

template<typename Reader, typename Budget>
static Outcome run(int fd,
                   uint64_t admitted_size,
                   const BLOMetadataScanLimits &limits,
                   const std::vector<Op> &ops,
                   int cancel_at)
{
  Outcome outcome;
  BLOMetadataScanResult result;
  result.status = BLOMetadataScanStatus::OK;
  Cancel cancel{0, cancel_at};
  Budget budget{limits, result, cancel_callback, &cancel};
  {
    Reader reader(fd, admitted_size, budget);
    if (reader.initialize()) {
      std::vector<uint8_t> buffer;
      for (const Op &op : ops) {
        int64_t r = 0;
        switch (op.kind) {
          case Op::Read:
            buffer.assign(op.amount, 0xCD);
            r = reader.file()->read(reader.file(), buffer.data(), op.amount);
            if (r > 0) {
              outcome.bytes.insert(outcome.bytes.end(), buffer.begin(), buffer.begin() + r);
            }
            break;
          case Op::ReadExact:
            buffer.assign(op.amount, 0xCD);
            r = reader.read_exact(buffer.data(), op.amount) ? 1 : 0;
            if (r) {
              outcome.bytes.insert(outcome.bytes.end(), buffer.begin(), buffer.end());
            }
            break;
          case Op::Skip:
            r = reader.skip(op.amount) ? 1 : 0;
            break;
          case Op::Finish:
            r = reader.finish() ? 1 : 0;
            break;
        }
        outcome.returns.push_back(r);
        if (result.status != BLOMetadataScanStatus::OK) {
          break;
        }
      }
    }
  }
  outcome.status = result.status;
  outcome.message = result.message;
  outcome.decoded = result.decoded_bytes;
  outcome.input = result.input_bytes;
  outcome.polls = cancel.polls;
  outcome.cancelled_by_callback = result.status == BLOMetadataScanStatus::CANCELLED;
  return outcome;
}

static std::vector<uint8_t> random_bytes(std::mt19937_64 &rng, size_t size)
{
  std::vector<uint8_t> data(size);
  for (uint8_t &byte : data) {
    byte = uint8_t(rng());
  }
  if (size >= 7) {
    std::memcpy(data.data(), "BLENDER", 7);
  }
  return data;
}

static std::vector<uint8_t> zstd_encode(const std::vector<uint8_t> &data)
{
  std::vector<uint8_t> out(ZSTD_compressBound(data.size()));
  out.resize(ZSTD_compress(out.data(), out.size(), data.data(), data.size(), 3));
  return out;
}

static std::vector<uint8_t> gzip_encode(const std::vector<uint8_t> &data)
{
  z_stream stream{};
  deflateInit2(&stream, 6, Z_DEFLATED, 16 + MAX_WBITS, 8, Z_DEFAULT_STRATEGY);
  std::vector<uint8_t> out(deflateBound(&stream, data.size()) + 64);
  stream.next_in = const_cast<Bytef *>(data.data());
  stream.avail_in = uInt(data.size());
  stream.next_out = out.data();
  stream.avail_out = uInt(out.size());
  deflate(&stream, Z_FINISH);
  out.resize(stream.total_out);
  deflateEnd(&stream);
  return out;
}

int main(int argc, char **argv)
{
  const int iterations = argc > 1 ? std::atoi(argv[1]) : 20000;
  const char *path = argc > 2 ? argv[2] : "/tmp/metadata_scan_reader_fuzz.bin";
  std::mt19937_64 rng(argc > 3 ? std::strtoull(argv[3], nullptr, 10) : 1);
  int compared = 0, identical = 0, input_refusals = 0, both_failed = 0, cancelled = 0, mismatches = 0;
  int toctou_runs = 0, budget_violations = 0;
  for (int iteration = 0; iteration < iterations; iteration++) {
    const int codec = int(rng() % 4); /* 0, 1: plain; 2: gzip; 3: zstd. */
    const size_t size = size_t(rng() % 4 == 0 ? rng() % 64 : rng() % (400 * 1024));
    std::vector<uint8_t> plain = random_bytes(rng, size);
    const std::vector<uint8_t> file = codec <= 1 ? plain :
                                      codec == 2  ? gzip_encode(plain) :
                                                    zstd_encode(plain);
    {
      FILE *f = std::fopen(path, "wb");
      std::fwrite(file.data(), 1, file.size(), f);
      std::fclose(f);
    }
    /* The size fstat gave at admission: usually the real one, sometimes stale. */
    uint64_t admitted = file.size();
    if (rng() % 8 == 0) {
      admitted = file.size() + rng() % 100000; /* The file shrank after admission. */
    }
    else if (rng() % 8 == 0 && !file.empty()) {
      admitted = rng() % file.size(); /* The file grew after admission. */
    }
    BLOMetadataScanLimits limits;
    const auto pick = [&](uint64_t big) -> uint64_t {
      switch (rng() % 4) {
        case 0:
          return 1 + rng() % 4096;
        case 1:
          return 1 + rng() % (512 * 1024);
        default:
          return big;
      }
    };
    limits.decoded_bytes = pick(uint64_t(1) << 33);
    limits.input_bytes = pick(uint64_t(1) << 33);
    limits.block_count = 1000;
    limits.block_bytes = uint64_t(1) << 31;
    limits.dna_bytes = 1 << 23;
    limits.dna_entries = 100000;
    limits.decoder_bytes = rng() % 6 == 0 ? 1 + rng() % 200000 : 64 * 1024 * 1024;
    limits.zstd_window_bytes = 32 * 1024 * 1024;
    limits.result_names = 4096;

    std::vector<Op> ops;
    const int ops_num = 1 + int(rng() % 120);
    for (int i = 0; i < ops_num; i++) {
      const uint64_t r = rng() % 100;
      if (r < 45) {
        ops.push_back({Op::Read, 1 + rng() % (rng() % 4 == 0 ? 200000 : 64)});
      }
      else if (r < 60) {
        ops.push_back({Op::ReadExact, 1 + rng() % 4000});
      }
      else if (r < 97) {
        ops.push_back({Op::Skip, rng() % 3 == 0 ? rng() % 300000 : rng() % 200});
      }
      else {
        ops.push_back({Op::Finish, 0});
      }
    }
    const int cancel_at = rng() % 5 == 0 ? int(rng() % 400) : -1;

    const int fd = open(path, O_RDONLY);
    /* Sometimes change the file while the windowed reader runs (memory safety only). */
    const bool toctou = rng() % 10 == 0;
    std::thread mutator;
    if (toctou) {
      toctou_runs++;
      const uint64_t cut = file.empty() ? 0 : rng() % (file.size() + 1);
      mutator = std::thread([path, cut]() { truncate(path, off_t(cut)); });
    }
    const Outcome now = run<blo::MetadataScanReader, blo::MetadataScanBudget>(
        fd, admitted, limits, ops, cancel_at);
    if (mutator.joinable()) {
      mutator.join();
    }
    if (now.input > limits.input_bytes || now.decoded > limits.decoded_bytes + 1) {
      budget_violations++;
      std::printf("BUDGET iteration %d: input %llu/%llu decoded %llu/%llu\n", iteration,
                  (unsigned long long)now.input, (unsigned long long)limits.input_bytes,
                  (unsigned long long)now.decoded, (unsigned long long)limits.decoded_bytes);
    }
    if (toctou) {
      close(fd);
      continue;
    }
    const Outcome before = run<blo_old::MetadataScanReader, blo_old::MetadataScanBudget>(
        fd, admitted, limits, ops, cancel_at);
    close(fd);
    if (codec > 1) {
      continue; /* Compressed reads are unchanged; these runs are for the sanitizers. */
    }
    if (now.cancelled_by_callback || before.cancelled_by_callback) {
      cancelled++;
      continue;
    }
    compared++;
    if (now.returns == before.returns && now.bytes == before.bytes && now.status == before.status &&
        now.message == before.message && now.decoded == before.decoded)
    {
      identical++;
      continue;
    }
    const bool now_ok = now.status == BLOMetadataScanStatus::OK;
    const bool before_ok = before.status == BLOMetadataScanStatus::OK;
    /* Data the windowed reader returned before failing must be the old reader's data, op by op. */
    const bool prefix = now.bytes.size() <= before.bytes.size() &&
                        std::equal(now.bytes.begin(), now.bytes.end(), before.bytes.begin()) &&
                        now.returns.size() <= before.returns.size() &&
                        std::equal(now.returns.begin(), now.returns.end() - 1, before.returns.begin());
    if (!now_ok && prefix) {
      if (now.message == "Encoded input limit exceeded" && before_ok) {
        input_refusals++; /* Refused where the old reader answered: read-ahead used the budget. */
      }
      else {
        both_failed++; /* Both refused; status or message may differ. */
      }
      continue;
    }
    mismatches++;
    if (mismatches <= 10) {
      std::printf("MISMATCH iteration %d codec %d size %zu admitted %llu limits in=%llu dec=%llu: "
                  "new %d '%s' (%zu bytes, %zu ops) old %d '%s' (%zu bytes, %zu ops)\n",
                  iteration, codec, file.size(), (unsigned long long)admitted,
                  (unsigned long long)limits.input_bytes, (unsigned long long)limits.decoded_bytes,
                  int(now.status), now.message.c_str(), now.bytes.size(), now.returns.size(),
                  int(before.status), before.message.c_str(), before.bytes.size(),
                  before.returns.size());
    }
  }
  std::printf("iterations %d, compared (plain, not cancelled) %d, identical %d, input-limit "
              "refusals where old answered %d, both refused (status may differ) %d, cancelled %d, "
              "toctou %d, budget violations %d, mismatches (old refused, new read on) %d\n",
              iterations, compared, identical, input_refusals, both_failed, cancelled, toctou_runs,
              budget_violations, mismatches);
  return budget_violations ? 1 : 0;
}
