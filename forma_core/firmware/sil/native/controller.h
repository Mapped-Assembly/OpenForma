#pragma once
#include <cstddef>
#include <cstdint>

namespace forma {
constexpr std::size_t max_axes = 8;
enum class Drive : int { servo = 0, stepper = 1 };
enum class Fault : int { none = 0, watchdog = 1, limit = 2, clock = 3 };
struct AxisConfig {
    double minimum, maximum, neutral, max_rate;
    double steps_per_unit, pulse_min_us, pulse_max_us;
    int drive;
};
// Platform boundary. A board port implements these calls with timers/GPIO.
// The controller itself contains neither desktop nor ESP32/Arduino APIs.
class IO {
public:
    virtual ~IO() = default;
    virtual void enable(bool value) = 0;
    virtual void servo(std::size_t axis, int pulse_us) = 0;
    virtual void steps(std::size_t axis, int direction, int count) = 0;
    virtual bool limit(std::size_t axis, int direction) const = 0;
    virtual void advance(double seconds) = 0;
};
class Controller {
public:
    Controller(const AxisConfig* config, std::size_t count, std::uint32_t watchdog_ms);
    void feed(const char* bytes, std::size_t size, std::uint64_t now, IO& io);
    void tick(std::uint64_t now, IO& io);
    bool enabled = false;
    Fault fault = Fault::none;
    std::uint64_t accepted = 0, rejected = 0, last_sequence = 0;
    int reject_reason = 0; // 1 syntax, 2 sequence, 3 range, 4 fault, 5 overflow
    double target[max_axes]{}, position[max_axes]{};
    std::int64_t step_position[max_axes]{};
private:
    AxisConfig config_[max_axes]{};
    std::size_t count_;
    std::uint32_t watchdog_ms_;
    std::uint64_t last_tick_ = 0, last_packet_ = 0;
    bool started_ = false, has_sequence_ = false, overflow_ = false;
    double step_credit_[max_axes]{};
    char buffer_[512]{};
    std::size_t used_ = 0;
    void packet(std::uint64_t now, IO& io);
    void trip(Fault reason, IO& io);
    void reject(int reason);
};
}
