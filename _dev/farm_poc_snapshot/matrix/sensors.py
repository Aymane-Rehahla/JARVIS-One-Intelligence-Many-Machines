"""Measurement-chain model: lag, noise, resolution, range, and heat damage.

Physical truth stays in ``PhysicalWorld``; every published reading passes
through one ``SensorBank`` channel. Defaults are typical datasheet-class values
for the sensor technology named in each entry, not a specific product.
"""
from math import exp
import random

# kind: (noise_sigma, time_constant_s, resolution, low, high)
SENSOR_SPECS = {
    # Thermistor / RTD probe in moving air.
    "temperature": (0.03, 20.0, 0.01, -40.0, 125.0),
    # Capacitive RH element.
    "humidity": (0.3, 8.0, 0.1, 0.0, 100.0),
    # NDIR CO2, 0-10000 ppm range class with slow diffusion inlet.
    "co2": (8.0, 60.0, 1.0, 0.0, 10_000.0),
    # Electrochemical O2 cell.
    "o2": (0.02, 10.0, 0.01, 0.0, 25.0),
    # Electrochemical CO cell, T90 well under 60 s.
    "co": (0.5, 25.0, 1.0, 0.0, 1_000.0),
    # Electrochemical NH3 cell.
    "nh3": (0.3, 45.0, 0.1, 0.0, 100.0),
    # Photoelectric smoke chamber, obscuration in %/m.
    "smoke": (0.05, 8.0, 0.01, 0.0, 100.0),
    # Mains RMS voltage transducer.
    "voltage": (0.15, 0.05, 0.1, 0.0, 400.0),
    # Fan tachometer.
    "rpm": (5.0, 0.0, 1.0, 0.0, 10_000.0),
    # Refrigerant pressure transducer.
    "pressure": (3.0, 1.0, 1.0, 0.0, 5_000.0),
    # Cup anemometer (averaged), tipping-bucket rain gauge, barometer, flood level probe.
    "wind": (0.3, 3.0, 0.1, 0.0, 75.0),
    "rain": (0.0, 60.0, 0.2, 0.0, 300.0),
    "barometric": (0.1, 10.0, 0.1, 850.0, 1085.0),
    "level": (0.3, 5.0, 0.5, 0.0, 300.0),
}

# Sensor electronics stop reporting above this local air temperature.
MAX_OPERATING_TEMPERATURE_C = 85.0


class SensorBank:
    """Seeded, deterministic measurement channels.

    Every channel draws exactly one random number per tick, even while it is
    failed or unavailable, so a fault on one channel never shifts the noise
    stream of the others and same-seed replays stay identical.
    """

    def __init__(self, seed, *, ideal=False, overrides=None):
        self.rng = random.Random(f"farm-sensors-{seed}")
        self.ideal = bool(ideal)
        self.specs = {**SENSOR_SPECS, **{k: tuple(v) for k, v in (overrides or {}).items()}}
        self.state = {}
        self.failed = set()
        self.faults: dict[str, dict] = {}   # channel -> injected measurement fault (Matrix-private)
        self.last_reading: dict[str, float] = {}

    def read(self, channel, kind, value, dt, *, local_temperature_c=None, exact=False):
        """Return the published reading for ``value`` or ``None`` if unavailable.

        ``exact`` seeds the channel with the value itself (configured initial
        readings) without noise or quantization.
        """
        noise = self.rng.gauss(0.0, 1.0)
        if channel in self.failed:
            return None
        if local_temperature_c is not None and local_temperature_c > MAX_OPERATING_TEMPERATURE_C:
            self.failed.add(channel)
            self.state.pop(channel, None)
            return None
        if value is None:
            self.state.pop(channel, None)
            return None
        value = float(value)
        if self.ideal or exact:
            self.state[channel] = value
            reading = value
        else:
            sigma, tau, resolution, low, high = self.specs[kind]
            previous = self.state.get(channel)
            if previous is None or tau <= 0.0 or dt <= 0.0:
                lagged = value if previous is None or tau <= 0.0 else previous
            else:
                lagged = value + (previous - value) * exp(-dt / tau)
            self.state[channel] = lagged
            reading = min(high, max(low, lagged + sigma * noise))
            if resolution:
                reading = round(round(reading / resolution) * resolution, 6)
        fault = self.faults.get(channel)
        if fault is None or exact:
            self.last_reading[channel] = reading
            return reading
        return self._faulted(channel, kind, reading, dt, fault)

    def _faulted(self, channel, kind, healthy, dt, fault):
        """Stuck, biased, drifting, or dropped-out output on top of the healthy chain.

        The healthy chain still runs (same single noise draw, same lag state), so
        a twin run without the fault sees identical noise on every channel.
        """
        fault["elapsed_s"] = fault.get("elapsed_s", 0.0) + float(dt)
        if fault["mode"] == "dropout":
            return None
        if fault["mode"] == "stuck":
            return fault.setdefault("value", self.last_reading.get(channel, healthy))
        shift = fault["offset"] if fault["mode"] == "bias" else fault["rate_per_s"] * fault["elapsed_s"]
        _, _, resolution, low, high = self.specs[kind]
        reading = min(high, max(low, healthy + shift))
        if resolution and not self.ideal:
            reading = round(round(reading / resolution) * resolution, 6)
        return reading

    def inject(self, channel: str, mode: str, *, offset: float = 0.0, rate_per_s: float = 0.0) -> None:
        if mode not in ("stuck", "bias", "drift", "dropout"):
            raise ValueError("sensor fault mode must be stuck, bias, drift, or dropout")
        self.faults[channel] = {"mode": mode, "offset": float(offset), "rate_per_s": float(rate_per_s)}

    def clear(self, channel: str) -> None:
        self.faults.pop(channel, None)

    def is_failed(self, channel):
        return channel in self.failed
