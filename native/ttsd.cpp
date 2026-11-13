// edvox-ttsd: a resident HTTP front end for llama.cpp audio generation.
//
// llama-tts is one-shot: every utterance pays for model load, mmproj load and
// graph setup (measured 2.4-3.4 s on a Ryzen 7 PRO 4750U, on top of the work
// itself). A voice conversation needs those costs paid once, so this binary
// loads the backbone and the mmproj exactly once and then serves /synthesize
// requests from the resident weights.
//
// llama args come from the command line (-m, -mm, -t, -ngl, --device, ...).
// Service args come from the environment so Kubernetes can set them:
//   TTS_HOST, TTS_PORT, TTS_FRAMES, TTS_LANG, EDVOX_SPEAKER_DIR
//
// Routes:
//   GET  /health      readiness
//   GET  /props       model + service description
//   POST /synthesize  {"text":..., "lang":..., "frames":..., "speaker":...,
//                      "reference_b64":..., "reference":..., "output":..., "seed":...}
//                     -> audio/wav, or JSON when "output" asks for a file
//
// edvox's existing TTS contract is file-based: the web server passes an output
// path and an uploaded voice-reference path, and the backend writes the audio.
// "output" and "reference" keep that contract working across pods (both sides
// mount the same volume). Writes and reads are confined to TTS_ALLOW_ROOTS.
#include "arg.h"
#include "common.h"
#include "sampling.h"
#include "log.h"
#include "llama.h"
#include "mtmd.h"
#include "mtmd-helper.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <cstdarg>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <sys/stat.h>
#include <map>
#include <mutex>
#include <signal.h>
#include <string>
#include <vector>

static std::atomic<bool> g_running{true};

static void logf(const char * fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[ttsd] ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
    fflush(stderr);
}

static void on_signal(int) {
    g_running.store(false);
}

// ---------------------------------------------------------------- little utils

static std::string env_str(const char * name, const std::string & fallback) {
    const char * v = getenv(name);
    return (v && *v) ? std::string(v) : fallback;
}

static int env_int(const char * name, int fallback) {
    const char * v = getenv(name);
    if (!v || !*v) {
        return fallback;
    }
    return atoi(v);
}

static double now_ms() {
    return std::chrono::duration<double, std::milli>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

// Decodes the flat JSON object edvox sends. Enough for the fields we use:
// string values (with the usual escapes), and numbers/booleans kept as text.
static bool json_unescape(const std::string & in, std::string & out) {
    out.clear();
    for (size_t i = 0; i < in.size(); i++) {
        char c = in[i];
        if (c != '\\') {
            out.push_back(c);
            continue;
        }
        if (i + 1 >= in.size()) {
            return false;
        }
        char e = in[++i];
        switch (e) {
            case '"':  out.push_back('"');  break;
            case '\\': out.push_back('\\'); break;
            case '/':  out.push_back('/');  break;
            case 'b':  out.push_back('\b'); break;
            case 'f':  out.push_back('\f'); break;
            case 'n':  out.push_back('\n'); break;
            case 'r':  out.push_back('\r'); break;
            case 't':  out.push_back('\t'); break;
            case 'u': {
                if (i + 4 >= in.size()) {
                    return false;
                }
                unsigned cp = strtoul(in.substr(i + 1, 4).c_str(), nullptr, 16);
                i += 4;
                if (cp < 0x80) {
                    out.push_back((char) cp);
                } else if (cp < 0x800) {
                    out.push_back((char) (0xC0 | (cp >> 6)));
                    out.push_back((char) (0x80 | (cp & 0x3F)));
                } else {
                    out.push_back((char) (0xE0 | (cp >> 12)));
                    out.push_back((char) (0x80 | ((cp >> 6) & 0x3F)));
                    out.push_back((char) (0x80 | (cp & 0x3F)));
                }
                break;
            }
            default: return false;
        }
    }
    return true;
}

static size_t json_skip_ws(const std::string & s, size_t i) {
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) {
        i++;
    }
    return i;
}

static bool json_flat(const std::string & body, std::map<std::string, std::string> & out) {
    size_t i = json_skip_ws(body, 0);
    if (i >= body.size() || body[i] != '{') {
        return false;
    }
    i++;
    while (true) {
        i = json_skip_ws(body, i);
        if (i >= body.size()) {
            return false;
        }
        if (body[i] == '}') {
            return true;
        }
        if (body[i] != '"') {
            return false;
        }
        // key
        size_t start = ++i;
        std::string key;
        while (i < body.size() && body[i] != '"') {
            if (body[i] == '\\') {
                i++;
            }
            i++;
        }
        if (i >= body.size()) {
            return false;
        }
        if (!json_unescape(body.substr(start, i - start), key)) {
            return false;
        }
        i++;  // closing quote
        i = json_skip_ws(body, i);
        if (i >= body.size() || body[i] != ':') {
            return false;
        }
        i = json_skip_ws(body, i + 1);
        if (i >= body.size()) {
            return false;
        }
        if (body[i] == '"') {
            start = ++i;
            while (i < body.size() && body[i] != '"') {
                if (body[i] == '\\') {
                    i++;
                }
                i++;
            }
            if (i >= body.size()) {
                return false;
            }
            std::string value;
            if (!json_unescape(body.substr(start, i - start), value)) {
                return false;
            }
            out[key] = value;
            i++;
        } else {
            start = i;
            while (i < body.size() && body[i] != ',' && body[i] != '}' && body[i] != '\n') {
                i++;
            }
            std::string value = body.substr(start, i - start);
            while (!value.empty() && (value.back() == ' ' || value.back() == '\r' || value.back() == '\t')) {
                value.pop_back();
            }
            out[key] = value;
        }
        i = json_skip_ws(body, i);
        if (i < body.size() && body[i] == ',') {
            i++;
        } else if (i < body.size() && body[i] == '}') {
            return true;
        } else {
            return false;
        }
    }
}

static std::string base64_decode(const std::string & in) {
    static int8_t T[256];
    static bool init = false;
    if (!init) {
        memset(T, -1, sizeof(T));
        const char * A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        for (int i = 0; i < 64; i++) {
            T[(unsigned char) A[i]] = i;
        }
        init = true;
    }
    std::string out;
    int val = 0, bits = -1;
    for (unsigned char c : in) {
        if (c == '\n' || c == '\r' || c == ' ' || c == '\t') {
            continue;
        }
        if (c == '=') {
            break;
        }
        int8_t d = T[c];
        if (d < 0) {
            continue;
        }
        if (bits == -1) {
            val = d;
            bits = 6;
        } else {
            val = (val << 6) | d;
            bits += 6;
            if (bits >= 8) {
                bits -= 8;
                out.push_back((char) ((val >> bits) & 0xFF));
            }
        }
    }
    return out;
}

// ------------------------------------------------------------------- HTTP bits

struct Request {
    std::string method;
    std::string path;
    std::string body;
    std::map<std::string, std::string> fields;  // parsed JSON body
};

struct Response {
    int status = 200;
    std::string content_type = "application/json";
    std::string body;
    std::vector<std::pair<std::string, std::string>> headers;
};

static std::string http_read_head(int fd, std::string & buffer) {
    while (buffer.find("\r\n\r\n") == std::string::npos) {
        char chunk[4096];
        ssize_t n = recv(fd, chunk, sizeof(chunk), 0);
        if (n <= 0) {
            return "";
        }
        buffer.append(chunk, n);
        if (buffer.size() > 64 * 1024) {
            return "";
        }
    }
    return buffer.substr(0, buffer.find("\r\n\r\n"));
}

static bool http_handle(int fd, Request & req) {
    std::string buffer;
    std::string head = http_read_head(fd, buffer);
    if (head.empty()) {
        return false;
    }
    size_t body_start = buffer.find("\r\n\r\n");
    std::string rest  = buffer.substr(body_start + 4);

    size_t line_end = head.find("\r\n");
    std::string line = head.substr(0, line_end);
    {
        size_t sp1 = line.find(' ');
        size_t sp2 = sp1 == std::string::npos ? std::string::npos : line.find(' ', sp1 + 1);
        if (sp1 == std::string::npos || sp2 == std::string::npos) {
            return false;
        }
        req.method = line.substr(0, sp1);
        req.path   = line.substr(sp1 + 1, sp2 - sp1 - 1);
    }

    size_t content_length = 0;
    {
        size_t i = 0;
        while (i < head.size()) {
            size_t eol = head.find("\r\n", i);
            if (eol == std::string::npos) {
                eol = head.size();
            }
            std::string h = head.substr(i, eol - i);
            size_t colon = h.find(':');
            if (colon != std::string::npos) {
                std::string name = h.substr(0, colon);
                for (auto & c : name) {
                    c = (char) tolower(c);
                }
                if (name == "content-length") {
                    content_length = (size_t) strtoul(h.c_str() + colon + 1, nullptr, 10);
                }
            }
            i = eol + 2;
        }
    }

    if (content_length > 64u * 1024u * 1024u) {
        return false;
    }
    while (rest.size() < content_length) {
        char chunk[8192];
        ssize_t n = recv(fd, chunk, sizeof(chunk), 0);
        if (n <= 0) {
            return false;
        }
        rest.append(chunk, n);
    }
    req.body = rest.substr(0, content_length);
    return true;
}

static void http_write(int fd, const Response & res) {
    char status[64];
    snprintf(status, sizeof(status), "HTTP/1.1 %d %s\r\n", res.status,
             res.status == 200 ? "OK" : (res.status == 400 ? "Bad Request"
                                    : res.status == 404 ? "Not Found" : "Server Error"));
    std::string out = status;
    out += "Content-Type: " + res.content_type + "\r\n";
    out += "Content-Length: " + std::to_string(res.body.size()) + "\r\n";
    out += "Connection: close\r\n";
    for (auto & kv : res.headers) {
        out += kv.first + ": " + kv.second + "\r\n";
    }
    out += "\r\n";
    out += res.body;

    size_t sent = 0;
    while (sent < out.size()) {
        ssize_t n = send(fd, out.data() + sent, out.size() - sent, MSG_NOSIGNAL);
        if (n <= 0) {
            return;
        }
        sent += n;
    }
}

// -------------------------------------------------------------------- the service

struct Speaker {
    mtmd_bitmap * bitmap;
    std::string   path;   // set when it came from a file, for cleanup of temp refs
    bool          temp;
};

class TtsService {
public:
    common_params params;
    std::unique_ptr<common_init_result> llama_init;
    llama_model * model  = nullptr;
    llama_context * lctx = nullptr;
    common_sampler * smpl = nullptr;
    mtmd::context_ptr mctx;
    mtmd_helper_gen_audio * gen = nullptr;

    std::string host           = "0.0.0.0";
    int         port           = 8191;
    int         default_frames = 512;
    int         max_frames     = 2048;
    std::string lang           = "en";
    std::string speaker_dir    = "/speakers";
    std::vector<std::string>   allow_roots;

    std::mutex lock;  // inference is serialised: one llama_context
    std::map<std::string, Speaker> speakers;
    uint64_t requests   = 0;
    double   audio_seconds = 0.0;
    double   work_seconds  = 0.0;
    double   last_ms       = 0.0;

    bool init(int argc, char ** argv) {
        common_init();
        if (!common_params_parse(argc, argv, params, LLAMA_EXAMPLE_TTS)) {
            return false;
        }
        // hidden states feed the audio stages; without this the pipeline stalls
        params.embedding = true;

        host           = env_str("TTS_HOST", host);
        port           = env_int("TTS_PORT", port);
        default_frames = env_int("TTS_FRAMES", default_frames);
        max_frames     = env_int("TTS_MAX_FRAMES", max_frames);
        lang           = env_str("TTS_LANG", lang);
        speaker_dir    = env_str("EDVOX_SPEAKER_DIR", speaker_dir);
        {
            // TTS_ALLOW_ROOTS is a colon-separated list of directories the
            // service may read references from and write audio into.
            std::string roots = env_str("TTS_ALLOW_ROOTS", "/app/edvox/audio:/app/edvox/voices");
            size_t i = 0;
            while (i < roots.size()) {
                size_t sep = roots.find(':', i);
                std::string root = roots.substr(i, sep == std::string::npos ? std::string::npos : sep - i);
                while (!root.empty() && root.front() == ' ') {
                    root.erase(root.begin());
                }
                if (!root.empty()) {
                    allow_roots.push_back(root);
                }
                if (sep == std::string::npos) {
                    break;
                }
                i = sep + 1;
            }
        }

        llama_backend_init();
        llama_numa_init(params.numa);

        llama_init = common_init_from_params(params);
        model  = llama_init->model();
        lctx   = llama_init->context();
        smpl   = llama_init->sampler(0);
        if (!model || !lctx || !smpl) {
            logf("failed to init llama");
            return false;
        }

        mtmd_context_params mp = mtmd_context_params_default();
        mp.use_gpu = params.mmproj_use_gpu;
        mp.device  = params.mmproj_device;
        mctx.reset(mtmd_init_from_file(params.mmproj.path.c_str(), model, mp));
        if (!mctx) {
            logf("failed to load mmproj %s", params.mmproj.path.c_str());
            return false;
        }
        if (mtmd_gen_audio_get_info(mctx.get()).type == MTMD_GEN_AUDIO_TYPE_NONE) {
            logf("mmproj does not support audio generation");
            return false;
        }

        gen = mtmd_helper_gen_audio_init(lctx, mctx.get());
        if (!gen) {
            logf("failed to init audio generation helper");
            return false;
        }

        load_named_speakers();
        logf("resident: model=%s mmproj=%s listening on %s:%d default_frames=%d speakers=%zu",
            params.model.path.c_str(), params.mmproj.path.c_str(), host.c_str(), port,
            default_frames, speakers.size());
        return true;
    }

    void shutdown() {
        if (gen) {
            mtmd_helper_gen_audio_free(gen);
            gen = nullptr;
        }
        for (auto & kv : speakers) {
            if (kv.second.bitmap) {
                mtmd_bitmap_free(kv.second.bitmap);
            }
            if (kv.second.temp && !kv.second.path.empty()) {
                unlink(kv.second.path.c_str());
            }
        }
        speakers.clear();
        llama_backend_free();
    }

    std::string describe() {
        std::string names = "[";
        bool first = true;
        for (auto & kv : speakers) {
            if (!first) {
                names += ",";
            }
            first = false;
            names += "\"" + kv.first + "\"";
        }
        names += "]";
        double realtime = work_seconds > 0 ? audio_seconds / work_seconds : 0.0;
        return "{\"model\":\"" + params.model.path + "\",\"mmproj\":\"" + params.mmproj.path
               + "\",\"default_frames\":" + std::to_string(default_frames) + ",\"max_frames\":"
               + std::to_string(max_frames) + ",\"lang\":\"" + lang + "\",\"speakers\":" + names
               + ",\"requests_served\":" + std::to_string(requests) + ",\"audio_seconds\":"
               + std::to_string((long long) (audio_seconds * 1000) / 1000.0) + ",\"work_seconds\":"
               + std::to_string((long long) (work_seconds * 1000) / 1000.0) + ",\"realtime_factor\":"
               + std::to_string((long long) (realtime * 1000) / 1000.0) + ",\"last_ms\":"
               + std::to_string((long long) last_ms) + "}";
    }

    // Paths must stay inside the declared roots: this service is reachable from
    // a public ingress, and the file-based protocol is only safe when bounded.
    bool path_allowed(const std::string & path) const {
        if (path.empty() || path.front() != '/' || path.find("..") != std::string::npos) {
            return false;
        }
        for (auto & root : allow_roots) {
            if (path.size() > root.size() + 1 && path.compare(0, root.size(), root) == 0
                && path[root.size()] == '/') {
                return true;
            }
        }
        return false;
    }

    // A cloned reference is the most expensive thing to decode, so cache it by
    // path and content stamp; an edited upload gets a fresh entry.
    mtmd_bitmap * cached_path_bitmap(const std::string & path, std::string & key) {
        struct stat st{};
        std::string stamp = path;
        if (stat(path.c_str(), &st) == 0) {
            stamp += ":" + std::to_string((long long) st.st_mtime) + ":" + std::to_string((long long) st.st_size);
        }
        key = stamp;
        auto it = speakers.find(stamp);
        if (it != speakers.end()) {
            return it->second.bitmap;
        }
        mtmd_bitmap * b = load_bitmap_file(path);
        if (!b) {
            return nullptr;
        }
        speakers[stamp] = Speaker{ b, "", false };
        return b;
    }

    Response synthesize(const Request & req) {
        Response res;
        std::map<std::string, std::string> f;
        if (!req.body.empty()) {
            if (!json_flat(req.body, f)) {
                res.status = 400;
                res.body   = "{\"error\":\"body must be a flat JSON object\"}";
                return res;
            }
        }
        std::string text = f.count("text") ? f["text"] : "";
        if (text.empty()) {
            res.status = 400;
            res.body   = "{\"error\":\"text is required\"}";
            return res;
        }
        if (text.size() > 6000) {
            text.resize(6000);
        }

        int frames = default_frames;
        if (f.count("frames")) {
            frames = atoi(f["frames"].c_str());
        }
        if (frames < 1) {
            frames = default_frames;
        }
        if (frames > max_frames) {
            frames = max_frames;
        }
        std::string lang_use = f.count("lang") && !f["lang"].empty() ? f["lang"] : lang;

        // speaker conditioning: a named voice from EDVOX_SPEAKER_DIR, an uploaded
        // reference path, or an inline reference clip. All are cached.
        const mtmd_bitmap * speaker = nullptr;
        std::string speaker_key;
        if (f.count("reference") && !f["reference"].empty()) {
            std::string ref = f["reference"];
            if (!path_allowed(ref)) {
                res.status = 400;
                res.body   = "{\"error\":\"reference path is outside TTS_ALLOW_ROOTS\"}";
                return res;
            }
            speaker = cached_path_bitmap(ref, speaker_key);
            if (!speaker) {
                res.status = 400;
                res.body   = "{\"error\":\"reference audio could not be decoded\"}";
                return res;
            }
        } else if (f.count("speaker") && !f["speaker"].empty()) {
            speaker_key = f["speaker"];
            auto it = speakers.find(speaker_key);
            if (it == speakers.end()) {
                if (speaker_key.find('/') != std::string::npos || speaker_key.find("..") != std::string::npos) {
                    res.status = 400;
                    res.body   = "{\"error\":\"bad speaker name\"}";
                    return res;
                }
                std::string path = speaker_dir + "/" + speaker_key;
                mtmd_bitmap * b = load_bitmap_file(path);
                if (!b) {
                    res.status = 400;
                    res.body   = "{\"error\":\"unknown speaker\",\"known\":" + describe_speakers() + "}";
                    return res;
                }
                speakers[speaker_key] = Speaker{ b, path, false };
            }
            speaker = speakers[speaker_key].bitmap;
        } else if (f.count("reference_b64") && !f["reference_b64"].empty()) {
            std::string bytes = base64_decode(f["reference_b64"]);
            if (bytes.size() < 1000 || bytes.size() > 40u * 1024u * 1024u) {
                res.status = 400;
                res.body   = "{\"error\":\"reference_b64 is not a usable audio clip\"}";
                return res;
            }
            unsigned long long hash = 1469598103934665603ULL;
            for (unsigned char c : bytes) {
                hash = (hash ^ c) * 1099511628211ULL;
            }
            speaker_key = "ref-" + std::to_string(hash);
            auto it = speakers.find(speaker_key);
            if (it == speakers.end()) {
                mtmd_bitmap * b = load_bitmap_buf((const unsigned char *) bytes.data(), bytes.size());
                if (!b) {
                    res.status = 400;
                    res.body   = "{\"error\":\"reference audio could not be decoded\"}";
                    return res;
                }
                speakers[speaker_key] = Speaker{ b, "", false };
            }
            speaker = speakers[speaker_key].bitmap;
        } else if (!speakers.empty()) {
            // Nothing asked for conditioning: use the first configured named voice
            // rather than going unconditioned. A voice that changes between turns is
            // not a voice, and an unconditioned Qwen3-TTS output has no accent at all.
            speaker_key = speakers.begin()->first;
            speaker     = speakers.begin()->second.bitmap;
        }

        std::lock_guard<std::mutex> guard(lock);
        const double t0 = now_ms();

        // a fresh conversation: previous turns must not leak into this one
        common_sampler_reset(smpl);
        llama_memory_clear(llama_get_memory(lctx), false);
        mtmd_helper_gen_audio_reset(gen);

        mtmd_helper_gen_audio_inp inp = mtmd_helper_gen_audio_inp_default();
        inp.seq_id      = 0;
        inp.prompt      = text.c_str();
        inp.prompt_len  = text.size();
        inp.speaker_ref = speaker ? const_cast<mtmd_bitmap *>(speaker) : nullptr;
        inp.lang        = lang_use.c_str();
        inp.out_type    = MTMD_HELPER_GEN_AUDIO_OUTTYPE_WAV;
        inp.top_k       = params.sampling.top_k;
        inp.top_p       = params.sampling.top_p;
        if (f.count("seed") && !f["seed"].empty()) {
            inp.seed = (uint32_t) strtoul(f["seed"].c_str(), nullptr, 10);
        }

        if (mtmd_helper_gen_audio_set_input(gen, &inp) != 0) {
            res.status = 500;
            res.body   = "{\"error\":\"set_input failed\"}";
            return res;
        }

        int32_t remaining = 1;
        while (remaining > 0) {
            remaining = mtmd_helper_gen_audio_step_prompt(gen, params.n_batch);
            if (remaining < 0) {
                res.status = 500;
                res.body   = "{\"error\":\"prompt processing failed\"}";
                return res;
            }
        }
        const double t_prompt = now_ms();

        const int max_new = frames;
        int n_frames        = 0;
        llama_token sampled = common_sampler_sample(smpl, lctx, -1);
        common_sampler_accept(smpl, sampled, true);
        const float * h_state = llama_get_embeddings_ith(lctx, -1);

        bool stop = false;
        while (!stop && n_frames < max_new) {
            const float * h_next  = nullptr;
            int32_t ret = mtmd_helper_gen_audio_step_gen(gen, sampled, h_state, &h_next, &stop);
            if (ret < 0) {
                res.status = 500;
                res.body   = "{\"error\":\"generation failed at frame " + std::to_string(n_frames) + "\"}";
                return res;
            }
            if (!h_next) {
                break;
            }
            n_frames++;
            h_state = h_next;
            sampled = common_sampler_sample(smpl, lctx, -1);
            common_sampler_accept(smpl, sampled, true);
        }
        const double t_gen = now_ms();

        int32_t sample_rate = 0;
        const char * data   = nullptr;
        size_t data_len     = 0;
        int64_t n_samples   = 0;
        if (mtmd_helper_gen_audio_flush(gen) != 0
            || mtmd_helper_gen_audio_get_output(gen, &sample_rate, &data, &data_len, &n_samples) != 0) {
            res.status = 500;
            res.body   = "{\"error\":\"vocoder failed\"}";
            return res;
        }
        const double t_vocoder = now_ms();

        if (!data || data_len == 0 || n_frames == 0) {
            res.status = 500;
            res.body   = "{\"error\":\"no audio produced\"}";
            return res;
        }

        const double audio_s  = sample_rate > 0 ? (double) n_samples / sample_rate : 0.0;
        const double total_ms = t_vocoder - t0;
        requests++;
        audio_seconds += audio_s;
        work_seconds  += total_ms / 1000.0;
        last_ms        = total_ms;

        // file mode keeps edvox's existing contract: web server owns the path,
        // serves it from its own audio root, and reads timings from JSON.
        std::string output = f.count("output") ? f["output"] : "";
        if (!output.empty()) {
            if (!path_allowed(output)) {
                res.status = 400;
                res.body   = "{\"error\":\"output path is outside TTS_ALLOW_ROOTS\"}";
                return res;
            }
            FILE * fp = fopen(output.c_str(), "wb");
            if (!fp) {
                res.status = 500;
                res.body   = "{\"error\":\"cannot write output path\"}";
                return res;
            }
            fwrite(data, 1, data_len, fp);
            fclose(fp);
            res.status       = 200;
            res.content_type = "application/json";
            res.body         = "{\"output\":\"" + output + "\",\"frames\":" + std::to_string(n_frames)
                               + ",\"sample_rate\":" + std::to_string(sample_rate)
                               + ",\"generation_seconds\":" + std::to_string(total_ms / 1000.0)
                               + ",\"audio_seconds\":" + std::to_string(audio_s)
                               + ",\"prompt_seconds\":" + std::to_string((t_prompt - t0) / 1000.0)
                               + ",\"gen_seconds\":" + std::to_string((t_gen - t_prompt) / 1000.0)
                               + ",\"vocoder_seconds\":" + std::to_string((t_vocoder - t_gen) / 1000.0)
                               + ",\"speaker\":\"" + (speaker_key.empty() ? std::string("none") : speaker_key)
                               + "\"}";
            logf("synthesized %d frames, %.2fs audio in %.0f ms (prompt %.0f + gen %.0f + vocoder %.0f) -> %s",
                n_frames, audio_s, total_ms, t_prompt - t0, t_gen - t_prompt, t_vocoder - t_gen,
                output.c_str());
            return res;
        }

        res.status       = 200;
        res.content_type = "audio/wav";
        res.body.assign(data, data_len);
        res.headers      = {
            { "X-Edvox-Frames",        std::to_string(n_frames) },
            { "X-Edvox-Audio-Seconds", std::to_string(audio_s) },
            { "X-Edvox-Sample-Rate",   std::to_string(sample_rate) },
            { "X-Edvox-Prompt-Ms",     std::to_string(t_prompt - t0) },
            { "X-Edvox-Gen-Ms",        std::to_string(t_gen - t_prompt) },
            { "X-Edvox-Vocoder-Ms",    std::to_string(t_vocoder - t_gen) },
            { "X-Edvox-Total-Ms",      std::to_string(total_ms) },
            { "X-Edvox-Speaker",       speaker_key.empty() ? "none" : speaker_key },
        };
        logf("synthesized %d frames, %.2fs audio in %.0f ms (prompt %.0f + gen %.0f + vocoder %.0f), speaker=%s",
            n_frames, audio_s, total_ms, t_prompt - t0, t_gen - t_prompt, t_vocoder - t_gen,
            speaker_key.empty() ? "none" : speaker_key.c_str());
        return res;
    }

private:
    // decodes from bytes, so a cloned reference never has to touch the disk
    mtmd_bitmap * load_bitmap_buf(const unsigned char * buf, size_t len) {
        mtmd_helper_bitmap_wrapper w =
            mtmd_helper_bitmap_init_from_buf(mctx.get(), buf, len, false, mtmd_helper_init_opt_default());
        return w.bitmap;
    }

    mtmd_bitmap * load_bitmap_file(const std::string & path) {
        FILE * fp = fopen(path.c_str(), "rb");
        if (!fp) {
            return nullptr;
        }
        std::vector<unsigned char> bytes;
        char chunk[65536];
        size_t n;
        while ((n = fread(chunk, 1, sizeof(chunk), fp)) > 0) {
            bytes.insert(bytes.end(), chunk, chunk + n);
        }
        fclose(fp);
        if (bytes.empty()) {
            return nullptr;
        }
        return load_bitmap_buf(bytes.data(), bytes.size());
    }

    std::string describe_speakers() {
        std::string names = "[";
        bool first = true;
        for (auto & kv : speakers) {
            if (!first) {
                names += ",";
            }
            first = false;
            names += "\"" + kv.first + "\"";
        }
        return names + "]";
    }

    void load_named_speakers() {
        const char * list = getenv("EDVOX_SPEAKERS");
        std::vector<std::string> names;
        if (list && *list) {
            std::string s(list);
            size_t i = 0;
            while (i < s.size()) {
                size_t comma = s.find(',', i);
                std::string name = s.substr(i, comma == std::string::npos ? std::string::npos : comma - i);
                while (!name.empty() && (name.front() == ' ' || name.front() == '\t')) {
                    name.erase(name.begin());
                }
                while (!name.empty() && (name.back() == ' ' || name.back() == '\t')) {
                    name.pop_back();
                }
                if (!name.empty()) {
                    names.push_back(name);
                }
                if (comma == std::string::npos) {
                    break;
                }
                i = comma + 1;
            }
        }
        for (auto & name : names) {
            std::string path = speaker_dir + "/" + name;
            mtmd_bitmap * b  = load_bitmap_file(path);
            if (!b) {
                logf("speaker %s: could not load %s", name.c_str(), path.c_str());
                continue;
            }
            speakers[name] = Speaker{ b, path, false };
            logf("speaker %s: conditioned from %s", name.c_str(), path.c_str());
        }
    }
};

// ----------------------------------------------------------------------- main

int main(int argc, char ** argv) {
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);

    TtsService service;
    if (!service.init(argc, argv)) {
        return 1;
    }

    int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        logf("socket failed");
        service.shutdown();
        return 1;
    }
    int one = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons((uint16_t) service.port);
    addr.sin_addr.s_addr = inet_addr(service.host.c_str());
    if (inet_addr(service.host.c_str()) == INADDR_NONE) {
        addr.sin_addr.s_addr = INADDR_ANY;
    }
    if (bind(listen_fd, (sockaddr *) &addr, sizeof(addr)) != 0) {
        logf("bind %s:%d failed: %s", service.host.c_str(), service.port, strerror(errno));
        close(listen_fd);
        service.shutdown();
        return 1;
    }
    if (listen(listen_fd, 32) != 0) {
        logf("listen failed: %s", strerror(errno));
        close(listen_fd);
        service.shutdown();
        return 1;
    }
    logf("listening on %s:%d", service.host.c_str(), service.port);

    while (g_running.load()) {
        int fd = accept(listen_fd, nullptr, nullptr);
        if (fd < 0) {
            if (!g_running.load()) {
                break;
            }
            continue;
        }
        timeval tv{ 120, 0 };
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

        Request req;
        if (!http_handle(fd, req)) {
            close(fd);
            continue;
        }

        Response res;
        if (req.method == "GET" && (req.path == "/health" || req.path == "/healthz")) {
            res.body = "{\"status\":\"ok\",\"requests_served\":" + std::to_string(service.requests) + "}";
        } else if (req.method == "GET" && (req.path == "/props" || req.path == "/v1/props")) {
            res.body = service.describe();
        } else if (req.method == "POST" && req.path == "/synthesize") {
            res = service.synthesize(req);
        } else {
            res.status = 404;
            res.body   = "{\"error\":\"not found\",\"routes\":[\"/health\",\"/props\",\"/synthesize\"]}";
        }
        http_write(fd, res);
        close(fd);
    }

    shutdown(listen_fd, SHUT_RDWR);
    close(listen_fd);
    service.shutdown();
    logf("stopped");
    return 0;
}
