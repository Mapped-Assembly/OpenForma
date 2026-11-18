#include "controller.h"
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

namespace forma {
namespace {
void spaces(char*& p) { while (*p == ' ' || *p == '\t' || *p == '\r') ++p; }
bool integer(char*& p, std::uint64_t& value) {
    spaces(p);
    if (*p < '0' || *p > '9') return false;
    value = 0;
    while (*p >= '0' && *p <= '9') {
        const auto digit = static_cast<unsigned>(*p++ - '0');
        // Protocol integers deliberately fit exactly in JavaScript numbers.
        if (value > (9007199254740991ULL - digit) / 10) return false;
        value = value * 10 + digit;
    }
    return *p == 0 || *p == ' ' || *p == '\t' || *p == '\r';
}
bool number(char*& p, double& value) {
    spaces(p);
    char* end = nullptr;
    value = std::strtod(p, &end);
    if (end == p || !std::isfinite(value)) return false;
    p = end;
    return *p == 0 || *p == ' ' || *p == '\t' || *p == '\r';
}
}
Controller::Controller(const AxisConfig* config, std::size_t count, std::uint32_t watchdog_ms)
    : count_(count), watchdog_ms_(watchdog_ms) {
    for (std::size_t i = 0; i < count_; ++i) {
        config_[i] = config[i];
        position[i] = target[i] = config[i].neutral;
        step_position[i] = std::llround(config[i].neutral * config[i].steps_per_unit);
    }
}
void Controller::trip(Fault reason, IO& io) {
    if (fault == Fault::none) fault = reason;
    enabled = false;
    io.enable(false);
}
void Controller::reject(int reason) { ++rejected; reject_reason = reason; }
void Controller::tick(std::uint64_t now, IO& io) {
    if (!started_) { started_ = true; last_tick_ = now; }
    if (now < last_tick_) { trip(Fault::clock, io); return; }
    const auto elapsed = now - last_tick_;
    last_tick_ = now;
    if (enabled && now - last_packet_ >= watchdog_ms_) trip(Fault::watchdog, io);
    // No catch-up jump after a paused simulator / delayed host.
    const double dt = static_cast<double>(std::min<std::uint64_t>(elapsed, 20)) / 1000.0;
    if (!enabled || fault != Fault::none) return;
    // Check all axes first so a limit blocks the entire output group.
    for (std::size_t i = 0; i < count_; ++i) {
        const double delta = target[i] - position[i];
        if ((delta > 1e-9 && io.limit(i, 1)) || (delta < -1e-9 && io.limit(i, -1))) {
            trip(Fault::limit, io); return;
        }
    }
    for (std::size_t i = 0; i < count_; ++i) {
        const auto& c = config_[i];
        if (c.drive == static_cast<int>(Drive::servo)) {
            position[i] += std::clamp(target[i] - position[i], -c.max_rate * dt, c.max_rate * dt);
            const auto pulse = static_cast<int>(std::lround(c.pulse_min_us +
                (position[i] - c.minimum) / (c.maximum - c.minimum) * (c.pulse_max_us - c.pulse_min_us)));
            io.servo(i, pulse);
        } else {
            const auto desired = std::llround(target[i] * c.steps_per_unit);
            const auto delta = desired - step_position[i];
            step_credit_[i] += c.max_rate * c.steps_per_unit * dt;
            const auto budget = static_cast<std::int64_t>(std::floor(step_credit_[i] + 1e-9));
            const auto n = std::min<std::int64_t>(std::abs(delta), budget);
            const int direction = delta > 0 ? 1 : delta < 0 ? -1 : 0;
            io.steps(i, direction, static_cast<int>(n));
            step_position[i] += direction * n;
            position[i] = static_cast<double>(step_position[i]) / c.steps_per_unit;
            // Retain only fractional timing credit; idle cannot bank future bursts.
            step_credit_[i] = std::max(0.0, step_credit_[i] - static_cast<double>(budget));
        }
    }
    io.advance(dt);
}
void Controller::packet(std::uint64_t now, IO& io) {
    char* p = buffer_;
    if (std::strncmp(p, "F1 ", 3) != 0) { reject(1); return; }
    p += 3;
    std::uint64_t sequence = 0, enable = 0, count = 0;
    if (!integer(p, sequence) || !integer(p, enable) || !integer(p, count) || enable > 1 || count != count_) {
        reject(1); return;
    }
    double values[max_axes]{};
    for (std::size_t i = 0; i < count_; ++i) {
        if (!number(p, values[i])) { reject(1); return; }
        if (values[i] < config_[i].minimum || values[i] > config_[i].maximum) { reject(3); return; }
    }
    spaces(p);
    if (*p) { reject(1); return; }
    if (has_sequence_ && sequence <= last_sequence) { reject(2); return; }
    if (enable && fault != Fault::none) { reject(4); return; }
    has_sequence_ = true;
    last_sequence = sequence;
    last_packet_ = now;
    ++accepted;
    reject_reason = 0;
    for (std::size_t i = 0; i < count_; ++i) target[i] = values[i];
    enabled = enable != 0;
    io.enable(enabled);
}
void Controller::feed(const char* bytes, std::size_t size, std::uint64_t now, IO& io) {
    tick(now, io);
    for (std::size_t i = 0; i < size; ++i) {
        const char c = bytes[i];
        if (c == '\n') {
            if (overflow_) reject(5);
            else { buffer_[used_] = 0; packet(now, io); }
            used_ = 0; overflow_ = false;
        } else if (c == 0 || used_ >= sizeof(buffer_) - 1) {
            overflow_ = true;
        } else if (!overflow_) buffer_[used_++] = c;
    }
}
}
