"""Seeded outdoor weather with a day/night cycle.

Outdoor air follows a diurnal temperature wave (minimum near sunrise, maximum
mid-afternoon) plus a slow synoptic drift; absolute humidity stays roughly
constant so relative humidity rises at night and falls in the afternoon.
Solar irradiance follows the sun between sunrise and sunset and is reduced by
cloud cover; wind drifts around a mean and picks up in the afternoon.
Drift terms are Ornstein-Uhlenbeck processes drawn from a seeded generator, so
the same seed always produces the same weather.
"""
from math import cos, exp, pi, sin, sqrt
import random

from .physical_world import absolute_humidity_to_relative_pct, relative_humidity_to_absolute_g_m3

DAY_S = 86_400.0


class WeatherModel:
    def __init__(self, seed, config=None):
        config = config or {}
        self.mean_temperature_c = float(config.get("mean_temperature_c", 22.0))
        self.daily_amplitude_c = float(config.get("daily_amplitude_c", 5.0))
        self.mean_relative_humidity_pct = float(config.get("mean_relative_humidity_pct", 55.0))
        self.peak_solar_w_m2 = float(config.get("peak_solar_w_m2", 850.0))
        self.sunrise_h = float(config.get("sunrise_h", 6.0))
        self.sunset_h = float(config.get("sunset_h", 18.5))
        self.mean_wind_m_s = float(config.get("mean_wind_m_s", 3.0))
        self.start_hour = float(config.get("start_hour", 6.0))
        if not (0.0 <= self.sunrise_h < self.sunset_h <= 24.0):
            raise ValueError("weather sunrise/sunset hours must be ordered within a day")
        if not 0.0 <= self.start_hour < 24.0:
            raise ValueError("weather start_hour must be within [0, 24)")
        self.rng = random.Random(f"farm-weather-{seed}")
        self.drift_c = 0.0      # synoptic temperature anomaly, about 2 K over ~2 days
        self.cloud = 0.3        # cloud fraction 0..1
        self.wind_anomaly = 0.0
        self.pressure = float(config.get("mean_pressure_hpa", 1013.0))
        self.wind_direction = float(config.get("prevailing_wind_deg", 220.0))
        self.raining = False
        self.rain_intensity = 0.0
        self.mean_absolute_humidity = relative_humidity_to_absolute_g_m3(
            self.mean_temperature_c, self.mean_relative_humidity_pct)

    @staticmethod
    def _ou(value, mean, tau_s, sigma, dt, noise):
        decay = exp(-dt / tau_s)
        return mean + (value - mean) * decay + sigma * sqrt(1.0 - decay * decay) * noise

    def hour_of_day(self, time_s):
        return (self.start_hour + float(time_s) / 3600.0) % 24.0

    def conditions(self, time_s, dt):
        hour = self.hour_of_day(time_s)
        self.drift_c = self._ou(self.drift_c, 0.0, 2.0 * DAY_S, 2.0, dt, self.rng.gauss(0.0, 1.0))
        self.cloud = min(1.0, max(0.0, self._ou(self.cloud, 0.3, 6 * 3600.0, 0.25, dt, self.rng.gauss(0.0, 1.0))))
        self.wind_anomaly = self._ou(self.wind_anomaly, 0.0, 1800.0, 1.2, dt, self.rng.gauss(0.0, 1.0))
        # Coldest just before sunrise, warmest about 15:00; clouds damp the swing.
        swing = self.daily_amplitude_c * (1.0 - 0.5 * self.cloud)
        temperature = self.mean_temperature_c + self.drift_c + swing * cos(2.0 * pi * (hour - 15.0) / 24.0)
        absolute = self.mean_absolute_humidity * (1.0 + 0.04 * self.drift_c / 2.0)
        humidity = absolute_humidity_to_relative_pct(temperature, absolute)
        if self.sunrise_h < hour < self.sunset_h:
            elevation = sin(pi * (hour - self.sunrise_h) / (self.sunset_h - self.sunrise_h))
            solar = self.peak_solar_w_m2 * elevation * (1.0 - 0.75 * self.cloud ** 3)
        else:
            solar = 0.0
        self.pressure = self._ou(self.pressure, 1013.0, 3.0 * DAY_S, 6.0, dt, self.rng.gauss(0.0, 1.0))
        self.wind_direction = (self.wind_direction + self.rng.gauss(0.0, 1.0) * 8.0 * sqrt(dt / 3600.0)) % 360.0
        # Rain showers start more readily under low pressure and heavy cloud.
        hazard_per_h = 0.01 + 0.15 * max(0.0, (1008.0 - self.pressure) / 10.0) + 0.05 * max(0.0, self.cloud - 0.6)
        if not self.raining and self.rng.random() < hazard_per_h * dt / 3600.0:
            self.raining, self.rain_intensity = True, self.rng.lognormvariate(0.7, 0.8)
        elif self.raining and self.rng.random() < 0.6 * dt / 3600.0:
            self.raining, self.rain_intensity = False, 0.0
        if self.raining:
            self.cloud = max(self.cloud, 0.8)
        afternoon = 0.5 * (1.0 + cos(2.0 * pi * (hour - 15.0) / 24.0))
        wind = max(0.0, self.mean_wind_m_s * (0.7 + 0.6 * afternoon) + self.wind_anomaly)
        return {"temperature_c": temperature, "relative_humidity_pct": humidity,
                "solar_w_m2": max(0.0, solar), "wind_speed_m_s": wind, "hour_of_day": hour,
                "wind_direction_deg": self.wind_direction, "pressure_hpa": self.pressure,
                "rain_mm_h": min(300.0, self.rain_intensity)}

    def apply(self, engine):
        weather = self.conditions(engine.clock.time, engine.clock.step_seconds)
        engine.physical_world.set_outside_conditions(
            temperature_c=weather["temperature_c"], relative_humidity_pct=weather["relative_humidity_pct"],
            solar_w_m2=min(1_400.0, weather["solar_w_m2"]), wind_speed_m_s=min(80.0, weather["wind_speed_m_s"]),
            wind_direction_deg=weather["wind_direction_deg"], rain_mm_h=weather["rain_mm_h"],
            pressure_hpa=max(900.0, min(1060.0, weather["pressure_hpa"])))
        return weather
