#include "moss_transcribe_capi.h"
#include "audio_io.hpp"
#include "subtitle.hpp"

#include <boost/asio/ip/tcp.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <cmath>
#include <ctime>
#include <opusfile.h>

namespace fs = std::filesystem;
namespace asio = boost::asio;
namespace beast = boost::beast;
namespace websocket = beast::websocket;
using tcp = asio::ip::tcp;

static std::atomic<bool> g_stop{false};

struct Options {
    std::string model;
    fs::path root_dir;
    std::string task_queue_dir;
    std::string in_proc_dir;
    std::string complit_dir;
    std::string result_dir;
    std::string result_name = ".json";
    std::string main_url;
    double max_duration_kf = 0.0;
    double max_duration_add = 0.0;
    int max_new = -1;
};

struct Task {
    std::string work_type;
    std::string sid;
    std::string cid;
    std::string file;
    std::string next;
};

struct WsUrl {
    std::string host;
    std::string port;
    std::string target;
};

static bool extract_json_string(const std::string &json,
                                const std::string &key,
                                std::string &value) {
    std::string needle = "\"" + key + "\"";
    auto pos = json.find(needle);
    if (pos == std::string::npos)
        return false;

    pos = json.find(':', pos + needle.size());
    if (pos == std::string::npos)
        return false;

    ++pos;
    while (pos < json.size() &&
           std::isspace(static_cast<unsigned char>(json[pos])))
        ++pos;

    if (pos >= json.size() || json[pos] != '"')
        return false;

    ++pos;

    std::string result;
    bool escaped = false;

    for (; pos < json.size(); ++pos) {
        char c = json[pos];

        if (escaped) {
            switch (c) {
            case '"': result.push_back('"'); break;
            case '\\': result.push_back('\\'); break;
            case '/': result.push_back('/'); break;
            case 'b': result.push_back('\b'); break;
            case 'f': result.push_back('\f'); break;
            case 'n': result.push_back('\n'); break;
            case 'r': result.push_back('\r'); break;
            case 't': result.push_back('\t'); break;
            default: result.push_back(c); break;
            }
            escaped = false;
            continue;
        }

        if (c == '\\') {
            escaped = true;
            continue;
        }

        if (c == '"') {
            value = result;
            return true;
        }

        result.push_back(c);
    }

    return false;
}

static std::string json_escape(const std::string &s) {
    std::string out;
    out.reserve(s.size() + 8);

    for (char c : s) {
        switch (c) {
        case '"':  out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:   out.push_back(c); break;
        }
    }

    return out;
}

class MainConnection {
public:
    explicit MainConnection(const std::string &url) {
        parse_url(url, url_);
    }

    bool connect() {
        try {
            ioc_.restart();

            tcp::resolver resolver(ioc_);
            auto endpoints = resolver.resolve(url_.host, url_.port);

            ws_ = std::make_unique<Ws>(ioc_);
            auto ep = asio::connect(ws_->next_layer(), endpoints);

            ws_->set_option(websocket::stream_base::timeout::suggested(
                beast::role_type::client));

            ws_->handshake(url_.host, url_.target);
            ws_->text(true);

            connected_ = true;
            return true;
        } catch (const std::exception &e) {
            last_error_ = e.what();
            connected_ = false;
            ws_.reset();
            return false;
        }
    }

    bool send(const std::string &message) {
        if (!connected_ || !ws_)
            return false;

        try {
            ws_->write(asio::buffer(message));
            return true;
        } catch (const std::exception &e) {
            last_error_ = e.what();
            disconnect();
            return false;
        }
    }

    bool send_wdc(const std::string &message) {
        // отправка с подтверждением о доставке
        // сообщение должно содержать поле ack_id - идентификатор подтверждения

        std::string msg_ack_id;
        if (!extract_json_string(message, "ack_id", msg_ack_id)) {
            std::cerr << "Invalid sended message, expected ack_id"
                      << message << std::endl;
            return false;            
        }

        if (!send(message)) {
            return false;
        }

        std::string ret_message;

        if (!receive(ret_message)) {
            return false;
        }

        std::string msg_type;
        std::string ret_ack_id;

        if (!extract_json_string(ret_message, "type", msg_type)) {
            std::cerr << "Invalid message: "
                      << ret_message << std::endl;
            return false;
        }        

        if (msg_type != "ack") {
            std::cerr << "A delivery confirmation message was expected"
                      << " type='ack',"
                      << ret_message << std::endl;
            return false;
        } 
         
        if (!extract_json_string(ret_message, "ack_id", ret_ack_id)) {
            std::cerr << "Invalid delivery confirmation message,"
                      << "expected field ack_id"
                      << ret_message << std::endl;
            return false;
        }  
        
        if (msg_ack_id != ret_ack_id) {
            std::cerr << "delivery confirmation message,"
                      << "contain incorrect ack_id value"
                      << ret_message << std::endl;
            return false;
        }

        return true;
    }

    bool receive(std::string &message) {
        if (!connected_ || !ws_)
            return false;

        try {
            beast::flat_buffer buffer;
            ws_->read(buffer);
            message = beast::buffers_to_string(buffer.data());
            return true;
        } catch (const std::exception &e) {
            last_error_ = e.what();
            disconnect();
            return false;
        }
    }

    void disconnect() {
        if (ws_) {
            boost::system::error_code ec;
            ws_->close(websocket::close_code::normal, ec);
        }
        ws_.reset();
        connected_ = false;
    }

    bool connected() const { return connected_; }

    const std::string &last_error() const { return last_error_; }

private:
    using Ws = websocket::stream<tcp::socket>;

    static void parse_url(const std::string &url, WsUrl &out) {
        const std::string prefix = "ws://";
        if (url.rfind(prefix, 0) != 0) {
            throw std::runtime_error(
                "main_url must use ws:// scheme");
        }

        std::string rest = url.substr(prefix.size());

        auto slash = rest.find('/');
        std::string authority =
            slash == std::string::npos ? rest : rest.substr(0, slash);

        out.target =
            slash == std::string::npos ? "/" : rest.substr(slash);

        auto colon = authority.rfind(':');
        if (colon != std::string::npos &&
            authority.find(']') == std::string::npos) {
            out.host = authority.substr(0, colon);
            out.port = authority.substr(colon + 1);
        } else {
            out.host = authority;
            out.port = "80";
        }

        if (out.host.empty())
            throw std::runtime_error("invalid main_url host");

        if (out.port.empty())
            out.port = "80";
    }

    WsUrl url_;
    asio::io_context ioc_;
    std::unique_ptr<Ws> ws_;
    bool connected_ = false;
    std::string last_error_;
};

static void signal_handler(int) {
    g_stop.store(true);
}

static void print_usage() {
    std::cerr
        << "usage: moss-worker <model.gguf> "
           "--root-dir DIR "
           "--task-queue-dir DIR "
           "--in-proc-dir DIR "
           "--complit-dir DIR "
           "--result-dir DIR "
           "[--result-name NAME] "
           "[--main-url ws://host:port/path] "
           "[--max-new N]\n";
}

static bool parse_args(int argc, char **argv, Options &opt) {
    if (argc < 2) {
        print_usage();
        return false;
    }

    opt.model = argv[1];

    auto value = [&](int &i, const std::string &name,
                     std::string &dst) -> bool {
        if (i + 1 >= argc) {
            std::cerr << name << " requires a value\n";
            return false;
        }
        dst = argv[++i];
        return true;
    };

    for (int i = 2; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--root-dir") {
            std::string v;
            if (!value(i, arg, v))
                return false;
            opt.root_dir = v;
        } else if (arg == "--task-queue-dir") {
            if (!value(i, arg, opt.task_queue_dir))
                return false;
        } else if (arg == "--in-proc-dir") {
            if (!value(i, arg, opt.in_proc_dir))
                return false;
        } else if (arg == "--complit-dir") {
            if (!value(i, arg, opt.complit_dir))
                return false;
        } else if (arg == "--result-dir") {
            if (!value(i, arg, opt.result_dir))
                return false;
        } else if (arg == "--result-name") {
            if (!value(i, arg, opt.result_name))
                return false;
        } else if (arg == "--main-url") {
            if (!value(i, arg, opt.main_url))
                return false;
        } else if (arg == "--max-duration-kf" && i + 1 < argc) {
            opt.max_duration_kf = std::stod(argv[++i]);            
        } else if (arg == "--max-duration-add" && i + 1 < argc) {
            opt.max_duration_add = std::stod(argv[++i]);              
        } else if (arg == "--max-new" && i + 1 < argc) {
            opt.max_new = std::stoi(argv[++i]);
        } else {
            std::cerr << "unknown argument: " << arg << "\n";
            print_usage();
            return false;
        }
    }

    if (opt.root_dir.empty() ||
        opt.task_queue_dir.empty() ||
        opt.in_proc_dir.empty() ||
        opt.complit_dir.empty() ||
        opt.result_dir.empty()) {
        std::cerr << "all directory options are required\n";
        print_usage();
        return false;
    }

    return true;
}

static fs::path make_dir(const fs::path &root, const std::string &name) {
    fs::path p = root / name;
    std::error_code ec;
    fs::create_directories(p, ec);
    if (ec)
        throw std::runtime_error(
            "cannot create directory " + p.string() + ": " + ec.message());
    return p;
}

static bool is_ogg(const fs::path &path) {
    std::string ext = path.extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(),
                   [](unsigned char c) {
                       return static_cast<char>(std::tolower(c));
                   });
    return ext == ".ogg";
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

static bool safe_filename(const std::string &name) {
    if (name.empty() || name == "." || name == "..")
        return false;

    fs::path p(name);
    if (p.is_absolute())
        return false;

    for (const auto &part : p) {
        if (part == "..")
            return false;
    }

    return p.filename() == p;
}

static fs::path oldest_file(const fs::path &dir) {
    std::error_code ec;
    bool found = false;
    fs::path oldest;
    fs::file_time_type oldest_time;

    for (const auto &entry : fs::directory_iterator(
             dir, fs::directory_options::skip_permission_denied, ec)) {
        if (ec)
            break;

        if (!entry.is_regular_file(ec) || ec)
            continue;

        if (!is_ogg(entry.path()))
            continue;

        auto t = fs::last_write_time(entry.path(), ec);
        if (ec)
            continue;

        if (!found || t < oldest_time ||
            (t == oldest_time &&
             entry.path().filename().string() <
                 oldest.filename().string())) {
            found = true;
            oldest = entry.path();
            oldest_time = t;
        }
    }

    return found ? oldest : fs::path();
}

static bool write_result_atomic(const fs::path &result,
                                const std::string &text) {
    std::error_code ec;

    fs::path tmp = result;
    tmp += ".tmp";

    {
        std::ofstream out(
            tmp, std::ios::binary | std::ios::trunc);

        if (!out)
            return false;

        out.write(
            text.data(),
            static_cast<std::streamsize>(text.size()));

        if (!out)
            return false;
    }

    fs::rename(tmp, result, ec);

    if (ec) {
        fs::remove(result, ec);
        ec.clear();
        fs::rename(tmp, result, ec);
    }

    if (ec) {
        fs::remove(tmp, ec);
        return false;
    }

    return true;
}

static bool process_file(
    moss_transcribe_ctx *ctx,
    const fs::path &input,
    const mt::Audio &audio_data,
    const fs::path &result,
    int max_new) {

    std::cout << "Processing: " << input.filename().string()
              << std::endl;

    auto start = std::chrono::steady_clock::now();

    char *raw =
		moss_transcribe_capi_transcribe_pcm(
			ctx,
			audio_data.samples.data(),
			static_cast<int>(audio_data.samples.size()), 
			audio_data.sample_rate,
			max_new);

    if (!raw) {
        std::cerr
            << "ERROR: transcription failed: "
            << moss_transcribe_capi_last_error(ctx)
            << std::endl;
        return false;
    }

    std::string transcript(raw);
    moss_transcribe_capi_free_string(raw);

    auto segments =
        mt::subtitle_segments_from_transcript(
            transcript,
            /*postprocess=*/false);

    std::string json_text = mt::to_json(segments);

    if (!write_result_atomic(result, json_text)) {
        std::cerr
            << "ERROR: cannot write result: "
            << result << std::endl;
        return false;
    }

    auto end = std::chrono::steady_clock::now();
    double seconds =
        std::chrono::duration<double>(end - start).count();

    std::cout << "Completed: "
              << input.filename().string()
              << " in "
              << std::fixed << std::setprecision(2)
              << seconds << " sec"
              << std::endl;

    return true;
}

static bool move_file(const fs::path &from,
                      const fs::path &to) {
    std::error_code ec;

    fs::create_directories(to.parent_path(), ec);
    if (ec) {
        std::cerr << "cannot create "
                  << to.parent_path()
                  << ": " << ec.message() << std::endl;
        return false;
    }

    fs::rename(from, to, ec);

    if (ec) {
        std::cerr << "cannot move "
                  << from << " -> " << to
                  << ": " << ec.message()
                  << std::endl;
        return false;
    }

    return true;
}

static bool connect_with_retries(MainConnection &connection) {
    constexpr int retry_interval_sec = 2;
    constexpr int retry_window_sec = 20;

    auto deadline =
        std::chrono::steady_clock::now() +
        std::chrono::seconds(retry_window_sec);

    int attempt = 0;

    while (!g_stop.load()) {
        ++attempt;

        std::cout << "Connecting to MAIN (attempt "
                  << attempt << ")..." << std::endl;

        if (connection.connect()) {
            std::cout << "MAIN connected" << std::endl;
            return true;
        }

        std::cerr << "MAIN connection failed: "
                  << connection.last_error()
                  << std::endl;

        if (std::chrono::steady_clock::now() >= deadline)
            break;

        for (int i = 0;
             i < retry_interval_sec * 10 && !g_stop.load();
             ++i) {
            std::this_thread::sleep_for(
                std::chrono::milliseconds(100));
        }
    }

    return false;
}

static bool send_ready(MainConnection &connection, const std::string &work_types) {
    return connection.send("{\"type\":\"ready\", \"work_types\":\"" + work_types + "\"}");
}

static bool send_task_done(MainConnection &connection,
                           const std::string &work_dir, 
                           const std::string &next_dir,
                           const std::string &file,
                           const std::string &sid,
                           const std::string &cid
                        ) {
    std::string msg;
    msg.reserve(128 + work_dir.length() + next_dir.length() + (file.length() * 2) + sid.length() + cid.length());

    msg += R"({"type":"task_done",)";
    msg += R"("work_type":")" + work_dir  + R"(",)";
    msg += R"("sid":")"       + sid       + R"(",)";
    msg += R"("cid":")"       + cid       + R"(",)";    
    msg += R"("file":")"      + file      + R"(",)";
    msg += R"("next":")"      + next_dir  + R"(",)";
    msg += R"("ack_id":")"    + file      + R"("})";
        
    return connection.send_wdc(msg);
}

static bool parse_task(const std::string &message,
                       const std::string &expected_work_type,
                       Task &task) {
    std::string msg_type;                        
    std::string work_type;
    std::string sid;
    std::string cid;
    std::string file;
    std::string next;

    if (!extract_json_string(message, "type", msg_type) ||
        !extract_json_string(message, "work_type", work_type) ||
        !extract_json_string(message, "sid", sid) ||
        !extract_json_string(message, "cid", cid) ||
        !extract_json_string(message, "file", file))
        {
        std::cerr << "Invalid task message: "
                  << message << std::endl;
        return false;
    }

    if (msg_type != "task") {
        std::cerr << "Ignoring message with type='"
                  << msg_type
                  << "', expected '"
                  << "task"
                  << "'" << std::endl;
        return false;
    } 

    if (work_type != expected_work_type) {
        std::cerr << "Ignoring task with work_type='"
                  << work_type
                  << "', expected '"
                  << expected_work_type
                  << "'" << std::endl;
        return false;
    }

    if (!safe_filename(file)) {
        std::cerr << "Invalid task file name: "
                  << file << std::endl;
        return false;
    }

    if (!extract_json_string(message, "next", next)) {
        next = "";
    }

    task.work_type = work_type;
    task.sid  = sid;
    task.cid  = cid;
    task.file = file;
    task.next = next;
    return true;
}

static bool process_task(
    moss_transcribe_ctx *ctx,
    const Options &opt,
    const fs::path &queue_dir,
    const fs::path &in_proc_dir,
    const fs::path &complete_dir,
    const fs::path &result_dir,
    const std::string &work_type,
    const std::string &sid,
    const std::string &cid,
    const std::string &file) {

    fs::path queue_file = queue_dir / file;
    fs::path complete_file = complete_dir / file;

    fs::path result_file;

    if (!work_type.empty() && !sid.empty() && !cid.empty()) {
        fs::path target_result_dir = result_dir / sid / work_type;
        fs::create_directories(target_result_dir);

        result_file = target_result_dir / (cid + opt.result_name);
    } else {
        result_file =
            result_dir /
            (fs::path(file).stem().string() + opt.result_name);
    }

	// Создаем структуру для хранения аудиоданных
	mt::Audio audio_data;		

	// Загружаем аудиофайл
	if (!load_audio_ogg_opus_mono(queue_file.string().c_str(), audio_data)) {
		std::cerr
			<< "\n  ERROR: "
			<< "error while loading file"
			<< "\n";
		return false;
	}    

    int smples_count = static_cast<int>(audio_data.samples.size());
    double max_duration = (smples_count / 16000.0) * opt.max_duration_kf + opt.max_duration_add; 

    auto now = std::chrono::system_clock::now();
    auto future_time = now + std::chrono::duration<double>(max_duration);

    // 3. Переводим во временную точку с точностью до минут
    // Округляем вверх (std::ceil) количество минут с начала эпохи
    auto duration_in_mins = std::chrono::duration_cast<std::chrono::minutes>(future_time.time_since_epoch());
    double exact_mins = std::chrono::duration<double, std::ratio<60>>(future_time.time_since_epoch()).count();
    long long ceiled_mins = static_cast<long long>(std::ceil(exact_mins));

    // Восстанавливаем time_t из округленных минут
    std::time_t final_time_t = ceiled_mins * 60;

    // 4. Переводим в локальное время (или gmtime для UTC)
    std::tm local_tm = *std::localtime(&final_time_t);

    // 5. Форматируем в строку в формате YYMMDDhhmm
    std::ostringstream oss;
    oss << std::put_time(&local_tm, "%y%m%d%H%M");
    std::string last_time_str = oss.str();    

    std::string new_file_name = sid + "_" + cid + "_" + work_type + "_" + last_time_str + "_" + opt.complit_dir + ".ogg";

    fs::path in_proc_file = in_proc_dir / new_file_name;

    if (!move_file(queue_file, in_proc_file)) {
        std::cerr << "Task could not be claimed: "
                  << file << std::endl;
        return false;
    }

    if (!process_file(ctx, in_proc_file, audio_data, result_file, opt.max_new)) {
        std::cerr
            << "Task failed. File remains in: "
            << in_proc_file
            << std::endl;
        return false;
    }

    if (!move_file(in_proc_file, complete_file)) {
        std::cerr
            << "Result saved, but source could not be moved to complete: "
            << in_proc_file
            << std::endl;
        return false;
    }

    return true;
}

static int run_queue_mode(
    moss_transcribe_ctx *ctx,
    const Options &opt,
    const fs::path &queue_dir,
    const fs::path &in_proc_dir,
    const fs::path &complete_dir,
    const fs::path &result_dir) {

    std::cout << "Worker mode: directory queue" << std::endl;
    std::cout << "Polling interval: 100 ms" << std::endl;

    std::string work_type = queue_dir.filename().string();

    while (!g_stop.load()) {
        fs::path file = oldest_file(queue_dir);

        if (!file.empty()) {
            const std::string name =
                file.filename().string();

            const std::string stem = 
                fs::path(file).stem().string();

            std::string sid = "";
            std::string cid = "";   

            size_t underscore_pos = stem.find('_');
            if (underscore_pos != std::string::npos) {
                sid = stem.substr(0, underscore_pos);
                cid = stem.substr(underscore_pos + 1);
            }

            if (!process_task(
                    ctx, opt,
                    queue_dir,
                    in_proc_dir,
                    complete_dir,
                    result_dir,
                    work_type,
                    sid,
                    cid,
                    name)) {
                std::cerr
                    << "Queue task failed; worker stops."
                    << std::endl;
                return 1;
            }

            continue;
        }

        std::this_thread::sleep_for(
            std::chrono::milliseconds(100));
    }

    return 0;
}

static int run_main_mode(
    moss_transcribe_ctx *ctx,
    const Options &opt,
    const fs::path &queue_dir,
    const fs::path &in_proc_dir,
    const fs::path &complete_dir,
    const fs::path &result_dir) {

    std::cout << "Worker mode: MAIN websocket" << std::endl;
    std::cout << "MAIN URL: " << opt.main_url << std::endl;

    MainConnection connection(opt.main_url);

    bool pending_done = false;
    std::string pending_file;
    std::string pending_sid;
    std::string pending_cid;

    while (!g_stop.load()) {
        if (!connection.connected()) {
            if (!connect_with_retries(connection)) {
                if (g_stop.load())
                    break;

                std::cerr
                    << "MAIN unavailable for 20 seconds; "
                       "retrying after 20 seconds."
                    << std::endl;

                for (int i = 0;
                     i < 200 && !g_stop.load();
                     ++i) {
                    std::this_thread::sleep_for(
                        std::chrono::milliseconds(100));
                }

                continue;
            }

            if (pending_done) {
                if (!send_task_done(
                        connection,
                        opt.in_proc_dir,
                        opt.complit_dir,
                        pending_file,
                        pending_sid,
                        pending_cid
                    )) {
                    continue;
                }

                pending_done = false;
                pending_file.clear();
                pending_sid.clear();
                pending_cid.clear();
            }

            if (!send_ready(connection, opt.task_queue_dir)) {
                continue;
            }

            std::cout << "Ready sent to MAIN" << std::endl;
        }

        std::string message;

        if (!connection.receive(message)) {
            std::cerr
                << "MAIN websocket disconnected: "
                << connection.last_error()
                << std::endl;
            continue;
        }

        Task task;

        if (!parse_task(
                message,
                opt.task_queue_dir,
                task)) {
            continue;
        }

        const fs::path &next_dir = task.next.empty() ? complete_dir : make_dir(opt.root_dir, task.next);

        std::cout
            << "Task received: "
            << task.file
            << std::endl;

        bool ok = process_task(
            ctx,
            opt,
            queue_dir,
            in_proc_dir,
            next_dir,
            result_dir,
            task.work_type,
            task.sid,
            task.cid,
            task.file);

        if (!ok) {
            std::cerr
                << "Task processing failed. "
                   "Worker stops because protocol has no "
                   "task_failed message."
                << std::endl;
            return 1;
        }

        pending_done = true;
        pending_file = task.file;
        pending_sid  = task.sid;
        pending_cid  = task.cid;

        if (!send_task_done(
                connection,
                opt.in_proc_dir,
                opt.complit_dir,
                pending_file,
                pending_sid,
                pending_cid
            )) {
            std::cerr
                << "task_done send failed; will resend "
                   "after reconnect."
                << std::endl;
            continue;
        }

        pending_done = false;
        pending_file.clear();
        pending_sid.clear();
        pending_cid.clear();

        if (!send_ready(connection, opt.task_queue_dir)) {
            std::cerr
                << "ready send failed; will reconnect."
                << std::endl;
            continue;
        }

        std::cout << "Ready sent to MAIN" << std::endl;
    }

    connection.disconnect();
    return 0;
}

int main(int argc, char **argv) {
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    Options opt;

    try {
        if (!parse_args(argc, argv, opt))
            return 2;
    } catch (const std::exception &e) {
        std::cerr << "argument error: "
                  << e.what() << std::endl;
        return 2;
    }

    if (!fs::exists(opt.model)) {
        std::cerr << "model not found: "
                  << opt.model << std::endl;
        return 1;
    }

    try {
        fs::path queue_dir =
            make_dir(opt.root_dir, opt.task_queue_dir);

        fs::path in_proc_dir =
            make_dir(opt.root_dir, opt.in_proc_dir);

        fs::path complete_dir =
            make_dir(opt.root_dir, opt.complit_dir);

        fs::path result_dir = opt.result_dir;
        if (opt.result_dir[0] != '/') {
            result_dir = make_dir(opt.root_dir, opt.result_dir);
        }     

        std::cout << "========================================"
                  << std::endl;
        std::cout << "MOSS worker" << std::endl;
        std::cout << "Model:       " << opt.model << std::endl;
        std::cout << "Root:        " << opt.root_dir << std::endl;
        std::cout << "Queue:       " << queue_dir << std::endl;
        std::cout << "In process:  " << in_proc_dir << std::endl;
        std::cout << "Complete:    " << complete_dir << std::endl;
        std::cout << "Results:     " << result_dir << std::endl;
        std::cout << "Result name: " << opt.result_name << std::endl;

        if (!opt.main_url.empty())
            std::cout << "MAIN:        " << opt.main_url << std::endl;
        else
            std::cout << "MAIN:        disabled" << std::endl;

        std::cout << "========================================"
                  << std::endl;

        std::cout << "Loading model..." << std::endl;

        auto load_start = std::chrono::steady_clock::now();

        moss_transcribe_ctx *ctx =
            moss_transcribe_capi_load(
                opt.model.c_str());

        if (!ctx) {
            std::cerr << "failed to load model"
                      << std::endl;
            return 1;
        }

        auto load_end = std::chrono::steady_clock::now();

        std::cout
            << "Model loaded in "
            << std::fixed << std::setprecision(2)
            << std::chrono::duration<double>(
                   load_end - load_start).count()
            << " sec"
            << std::endl;

        int rc;

        if (opt.main_url.empty()) {
            rc = run_queue_mode(
                ctx, opt,
                queue_dir,
                in_proc_dir,
                complete_dir,
                result_dir);
        } else {
            rc = run_main_mode(
                ctx, opt,
                queue_dir,
                in_proc_dir,
                complete_dir,
                result_dir);
        }

        moss_transcribe_capi_free(ctx);

        std::cout << "Worker stopped." << std::endl;
        return rc;

    } catch (const std::exception &e) {
        std::cerr
            << "fatal error: "
            << e.what()
            << std::endl;
        return 1;
    }
}
