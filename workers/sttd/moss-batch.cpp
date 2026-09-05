#include "moss_transcribe_capi.h"
#include "audio_io.hpp"
#include "subtitle.hpp"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <opusfile.h>

namespace fs = std::filesystem;

struct Options {
    std::string model;
    fs::path input_dir;
    fs::path output_dir;
    int max_new = -1;
    bool overwrite = false;
};

static void print_usage() {
    std::cerr
        << "usage: moss-batch <model.gguf> <audio-dir> <output-dir> "
           "[--max-new N] [--overwrite]\n";
}

static bool parse_args(int argc, char **argv, Options &opt) {
    if (argc < 4) {
        print_usage();
        return false;
    }

    opt.model = argv[1];
    opt.input_dir = argv[2];
    opt.output_dir = argv[3];

    for (int i = 4; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--max-new" && i + 1 < argc) {
            opt.max_new = std::stoi(argv[++i]);
        } else if (arg == "--overwrite") {
            opt.overwrite = true;
        } else {
            std::cerr << "unknown argument: " << arg << "\n";
            print_usage();
            return false;
        }
    }

    return true;
}

static bool is_ogg(const fs::path &path) {
    std::string ext = path.extension().string();

    std::transform(
        ext.begin(),
        ext.end(),
        ext.begin(),
        [](unsigned char c) {
            return static_cast<char>(std::tolower(c));
        });

    return ext == ".ogg";
}

static std::string format_seconds(double seconds) {
    std::ostringstream ss;
    ss << std::fixed << std::setprecision(2) << seconds;
    return ss.str();
}

bool load_audio_ogg_opus_mono(const std::string& path, mt::Audio& out) {
    int error = 0;
    // Открываем OGG Opus файл
    OggOpusFile* od = op_open_file(path.c_str(), &error);
    if (!od || error < 0) {
        // MT_LOGE("failed to open ogg opus: %s", path.c_str());
        return false;
    }

    // Проверяем количество каналов в первом логическом потоке
    int channels = op_channel_count(od, -1);
    if (channels != 1) {
        // MT_LOGE("audio is not mono, channels: %d", channels);
        op_free(od);
        return false;
    }

    std::vector<float> pcm_data;
    const int buffer_size = 1024; 
    float buffer[buffer_size]; // Буфер для чтения фреймов

    // Читаем аудиоданные порциями
    while (true) {
        // op_read_float читает данные и автоматически микширует/декодирует в float
        int samples_per_channel = op_read_float(od, buffer, buffer_size, nullptr);
        
        if (samples_per_channel < 0) {
            // Ошибка декодирования фрейма
            op_free(od);
            return false;
        }
        if (samples_per_channel == 0) {
            break; // Конец файла
        }

        // Так как канал один, количество сэмплов равно samples_per_channel
        pcm_data.insert(pcm_data.end(), buffer, buffer + samples_per_channel);
    }

    // Освобождаем ресурсы
    op_free(od);

    // Заполняем выходную структуру
    out.samples = std::move(pcm_data);
    out.sample_rate = 48000; // Opus внутри всегда декодирует в 48000 Hz на выходе API (стандарт кодека)

    return true;
}

int main(int argc, char **argv) {
    Options opt;

    if (!parse_args(argc, argv, opt)) {
        return 2;
    }

    if (!fs::exists(opt.model)) {
        std::cerr << "model not found: " << opt.model << "\n";
        return 1;
    }

    if (!fs::exists(opt.input_dir) ||
        !fs::is_directory(opt.input_dir)) {
        std::cerr << "audio directory not found: "
                  << opt.input_dir << "\n";
        return 1;
    }

    std::error_code ec;
    fs::create_directories(opt.output_dir, ec);

    if (ec) {
        std::cerr << "failed to create output directory: "
                  << opt.output_dir << ": "
                  << ec.message() << "\n";
        return 1;
    }

    // ---------------------------------------------------------
    // Collect WAV files
    // ---------------------------------------------------------

    std::vector<fs::path> files;

    for (const auto &entry : fs::directory_iterator(opt.input_dir)) {
        if (!entry.is_regular_file()) {
            continue;
        }

        if (is_ogg(entry.path())) {
            files.push_back(entry.path());
        }
    }

    std::sort(
        files.begin(),
        files.end(),
        [](const fs::path &a, const fs::path &b) {
            return a.filename().string() < b.filename().string();
        });

    if (files.empty()) {
        std::cerr << "no OGG files found in "
                  << opt.input_dir << "\n";
        return 1;
    }

    std::cout << "Found " << files.size() << " OGG files\n";
    std::cout << "Model:  " << opt.model << "\n";
    std::cout << "Input:  " << opt.input_dir << "\n";
    std::cout << "Output: " << opt.output_dir << "\n";
    std::cout << "\n";

    // ---------------------------------------------------------
    // Load model ONCE
    // ---------------------------------------------------------

    auto load_start = std::chrono::steady_clock::now();

    std::cout << "Loading model...\n";

    moss_transcribe_ctx *ctx =
        moss_transcribe_capi_load(opt.model.c_str());

    if (!ctx) {
        std::cerr << "failed to load model\n";
        return 1;
    }

    auto load_end = std::chrono::steady_clock::now();

    double load_seconds =
        std::chrono::duration<double>(load_end - load_start).count();

    std::cout
        << "Model loaded in "
        << std::fixed
        << std::setprecision(2)
        << load_seconds
        << " sec\n\n";

    // ---------------------------------------------------------
    // Process files
    // ---------------------------------------------------------

    auto total_start = std::chrono::steady_clock::now();

    size_t processed = 0;
    size_t skipped = 0;
    size_t failed = 0;

    for (size_t i = 0; i < files.size(); ++i) {
        const fs::path &wav = files[i];

        fs::path json =
            opt.output_dir /
            (wav.stem().string() + ".json");

        if (fs::exists(json) && !opt.overwrite) {
            ++skipped;

            std::cout
                << "["
                << (i + 1)
                << "/"
                << files.size()
                << "] "
                << wav.filename().string()
                << "  SKIP (already exists)\n";

            continue;
        }

        auto start = std::chrono::steady_clock::now();

        std::cout
            << "["
            << (i + 1)
            << "/"
            << files.size()
            << "] "
            << wav.filename().string()
            << " ... "
            << std::flush;
			        
        // Создаем структуру для хранения аудиоданных
        mt::Audio audio_data;		

        // Загружаем аудиофайл
        if (!load_audio_ogg_opus_mono(wav.string().c_str(), audio_data)) {
            std::cerr
                << "\n  ERROR: "
                << "error while loading file"
                << "\n";

            ++failed;
            continue; 
        }
		
        char *raw =
            moss_transcribe_capi_transcribe_pcm(
                ctx,
                audio_data.samples.data(),
				static_cast<int>(audio_data.samples.size()), 
				audio_data.sample_rate,
                opt.max_new);
				
//            moss_transcribe_capi_transcribe_path(
//                ctx,
//                wav.string().c_str(),
//                opt.max_new);

        if (!raw) {
            std::cerr
                << "\n  ERROR: "
                << moss_transcribe_capi_last_error(ctx)
                << "\n";

            ++failed;
            continue;
        }

        std::string transcript(raw);

        moss_transcribe_capi_free_string(raw);

        // Use the exact same parser/exporter as the official CLI.
        auto segments =
            mt::subtitle_segments_from_transcript(
                transcript,
                /*postprocess=*/false);

        std::string json_text =
            mt::to_json(segments);

        // Write atomically: *.tmp -> *.json
        fs::path tmp = json;
        tmp += ".tmp";

        {
            std::ofstream out(
                tmp,
                std::ios::binary | std::ios::trunc);

            if (!out) {
                std::cerr
                    << "\n  ERROR: cannot create "
                    << tmp
                    << "\n";

                ++failed;
                continue;
            }

            out.write(
                json_text.data(),
                static_cast<std::streamsize>(
                    json_text.size()));

            if (!out) {
                std::cerr
                    << "\n  ERROR: write failed\n";

                ++failed;
                continue;
            }
        }

        fs::rename(tmp, json, ec);

        if (ec) {
            // Windows may not replace an existing file.
            fs::remove(json, ec);
            ec.clear();

            fs::rename(tmp, json, ec);
        }

        if (ec) {
            std::cerr
                << "\n  ERROR: cannot rename "
                << tmp
                << " -> "
                << json
                << ": "
                << ec.message()
                << "\n";

            ++failed;
            continue;
        }

        auto end = std::chrono::steady_clock::now();

        double seconds =
            std::chrono::duration<double>(
                end - start).count();

        ++processed;

        std::cout
            << format_seconds(seconds)
            << " sec\n";
    }

    // ---------------------------------------------------------
    // Cleanup
    // ---------------------------------------------------------

    moss_transcribe_capi_free(ctx);

    auto total_end = std::chrono::steady_clock::now();

    double inference_seconds =
        std::chrono::duration<double>(
            total_end - total_start).count();

    std::cout << "\n";
    std::cout << "========================================\n";
    std::cout << "Processed: "
              << processed << "\n";
    std::cout << "Skipped:   "
              << skipped << "\n";
    std::cout << "Failed:    "
              << failed << "\n";
    std::cout << "Model load:"
              << " "
              << std::fixed
              << std::setprecision(2)
              << load_seconds
              << " sec\n";
    std::cout << "Processing:"
              << " "
              << inference_seconds
              << " sec\n";
    std::cout << "========================================\n";

    return failed ? 1 : 0;
}