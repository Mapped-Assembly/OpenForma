// Native test harness + simulated plant. Board ports replace this file's IO.
#include "controller.h"
#include <algorithm>
#include <cmath>
#include <new>
#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {
struct AxisState {
    double simulated, firmware_position, target;
    std::int64_t signed_steps;
    std::uint64_t pulses;
    int pulse_us, direction, minimum_limit, maximum_limit, stalled;
};
struct Status {
    std::uint64_t accepted, rejected, sequence;
    int enabled, fault, reject_reason;
};
struct Plant final : forma::IO {
    forma::AxisConfig config[forma::max_axes]{};
    AxisState axes[forma::max_axes]{};
    int injected_min[forma::max_axes]{}, injected_max[forma::max_axes]{};
    std::size_t count;
    bool enabled = false;
    Plant(const forma::AxisConfig* values, std::size_t n) : count(n) {
        for (std::size_t i = 0; i < n; ++i) { config[i] = values[i]; axes[i].simulated = values[i].neutral; }
    }
    void enable(bool value) override {
        enabled = value;
        if (!value) for (std::size_t i = 0; i < count; ++i) { axes[i].pulse_us = 0; axes[i].direction = 0; }
    }
    void servo(std::size_t i, int pulse_us) override { if (enabled) axes[i].pulse_us = pulse_us; }
    void steps(std::size_t i, int direction, int count_steps) override {
        if (!enabled) return;
        axes[i].direction = count_steps ? direction : 0;
        axes[i].pulses += count_steps;
        if (!axes[i].stalled) axes[i].simulated += direction * count_steps / config[i].steps_per_unit;
        axes[i].simulated = std::clamp(axes[i].simulated, config[i].minimum, config[i].maximum);
    }
    bool limit(std::size_t i, int direction) const override {
        // Only the virtual stepper fixture includes end switches.
        if (config[i].drive != static_cast<int>(forma::Drive::stepper)) return false;
        if (direction < 0) return injected_min[i] || axes[i].simulated <= config[i].minimum + 1e-9;
        return injected_max[i] || axes[i].simulated >= config[i].maximum - 1e-9;
    }
    void advance(double dt) override {
        for (std::size_t i = 0; i < count; ++i) {
            const auto& c = config[i];
            auto& a = axes[i];
            if (c.drive == static_cast<int>(forma::Drive::servo) && a.pulse_us && !a.stalled) {
                const double requested = c.minimum + (a.pulse_us - c.pulse_min_us) /
                    (c.pulse_max_us - c.pulse_min_us) * (c.maximum - c.minimum);
                // Explicit idealized servo response: 180 degree/s, no torque solver.
                a.simulated += std::clamp(requested - a.simulated, -180.0 * dt, 180.0 * dt);
                a.simulated = std::clamp(a.simulated, c.minimum, c.maximum);
            }
        }
    }
};
struct Simulation {
    Plant plant;
    forma::Controller controller;
    Simulation(const forma::AxisConfig* c, std::size_t n, std::uint32_t watchdog)
        : plant(c, n), controller(c, n, watchdog) {}
};
}
EXPORT int forma_sil_abi() { return 1; }
EXPORT void* forma_sil_create(const forma::AxisConfig* config, std::size_t count, std::uint32_t watchdog) {
    if (!config || !count || count > forma::max_axes || watchdog < 20 || watchdog > 10000) return nullptr;
    for (std::size_t i = 0; i < count; ++i) {
        const auto& c = config[i];
        if (!std::isfinite(c.minimum) || !std::isfinite(c.maximum) || !std::isfinite(c.neutral) ||
            !std::isfinite(c.max_rate) || !std::isfinite(c.steps_per_unit) ||
            !std::isfinite(c.pulse_min_us) || !std::isfinite(c.pulse_max_us) ||
            c.minimum >= c.maximum || c.neutral < c.minimum || c.neutral > c.maximum ||
            c.max_rate <= 0 || c.max_rate > 100000 || c.steps_per_unit <= 0 || c.steps_per_unit > 100000 ||
            std::max(std::abs(c.minimum), std::abs(c.maximum)) * c.steps_per_unit > 1e12 ||
            c.max_rate * c.steps_per_unit > 1e7 || c.pulse_min_us < 500 || c.pulse_max_us > 2500 ||
            c.pulse_min_us >= c.pulse_max_us || (c.drive != 0 && c.drive != 1)) return nullptr;
    }
    try { return new Simulation(config, count, watchdog); } catch (...) { return nullptr; }
}
EXPORT void forma_sil_destroy(void* p) { delete static_cast<Simulation*>(p); }
EXPORT void forma_sil_feed(void* p, const char* bytes, std::size_t size, std::uint64_t now) {
    auto& s = *static_cast<Simulation*>(p); s.controller.feed(bytes, size, now, s.plant);
}
EXPORT void forma_sil_tick(void* p, std::uint64_t now) {
    auto& s = *static_cast<Simulation*>(p); s.controller.tick(now, s.plant);
}
EXPORT void forma_sil_status(void* p, Status* out) {
    const auto& c = static_cast<Simulation*>(p)->controller;
    *out = {c.accepted, c.rejected, c.last_sequence, c.enabled ? 1 : 0, static_cast<int>(c.fault), c.reject_reason};
}
EXPORT void forma_sil_axis(void* p, std::size_t i, AxisState* out) {
    auto& s = *static_cast<Simulation*>(p);
    if (i >= s.plant.count) return;
    *out = s.plant.axes[i];
    out->firmware_position = s.controller.position[i]; out->target = s.controller.target[i];
    out->signed_steps = s.controller.step_position[i];
    out->minimum_limit = s.plant.limit(i, -1) ? 1 : 0;
    out->maximum_limit = s.plant.limit(i, 1) ? 1 : 0;
}
EXPORT void forma_sil_inject(void* p, std::size_t i, int stalled, int minimum, int maximum) {
    auto& s = *static_cast<Simulation*>(p);
    if (i >= s.plant.count) return;
    s.plant.axes[i].stalled = stalled;
    s.plant.injected_min[i] = minimum; s.plant.injected_max[i] = maximum;
}
