"""Aggregate poultry flock as a simple input/output plant.

Inputs: barn air temperature, oxygen, carbon monoxide, feed and water
availability. Outputs: sensible and latent heat, CO2, O2 uptake, litter
ammonia, feed and water intake, and an experimental welfare index.

Reference values (typical, uncalibrated):
- Total heat production of broilers, CIGR 2002: 10.62 * m^0.75 W at 20 °C.
- CO2 production, CIGR 2002: 0.185 m^3/h per 1000 W of total animal heat.
- Respiratory quotient about 0.95 for fed birds.
- Feed intake falls roughly 1.5 %/K above 21 °C; water intake rises roughly
  6 %/K above 21 °C; water:feed ratio about 1.9 at thermoneutral conditions.
- Litter ammonia emission is set to about 0.2 g NH3 per bird per day at 20 °C
  with a Q10 of 2.
The welfare index is a dimensionless demonstration metric, not a veterinary
prediction.
"""
from math import exp

LATENT_HEAT_J_G = 2_450.0
CO2_DENSITY_KG_M3 = 1.829
O2_DENSITY_KG_M3 = 1.330


class Flock:
    def __init__(self, config=None):
        config = config or {}
        self.mass_kg = float(config.get("bird_mass_kg", 2.0))
        self.feed_kg_bird_day = float(config.get("feed_kg_per_bird_day", 0.12))
        self.water_feed_ratio = float(config.get("water_to_feed_ratio", 1.9))
        self.lower_critical_c = float(config.get("lower_critical_temperature_c", 16.0))
        self.upper_critical_c = float(config.get("upper_critical_temperature_c", 26.0))
        self.ammonia_g_bird_day = float(config.get("ammonia_g_per_bird_day", 0.2))
        if self.mass_kg <= 0 or self.feed_kg_bird_day < 0 or self.water_feed_ratio < 0:
            raise ValueError("flock parameters must be positive")
        self.hours_without_water = 0.0
        self.hours_without_feed = 0.0

    def outputs(self, count, air_temperature_c, *, feed_available=True, water_available=True):
        """Instantaneous flock source terms for ``count`` birds."""
        count = max(0, int(count or 0))
        t = float(air_temperature_c)
        base_w = 10.62 * self.mass_kg ** 0.75
        # Birds raise metabolism to stay warm below the lower critical temperature.
        cold_factor = 1.0 + 0.02 * max(0.0, self.lower_critical_c - t)
        heat_stress = max(0.0, t - self.upper_critical_c)
        feed_factor = max(0.2, 1.0 - 0.015 * max(0.0, t - 21.0))
        if not feed_available:
            feed_factor = 0.0
        total_w = base_w * cold_factor * (0.85 + 0.15 * feed_factor) * count
        # Panting shifts heat loss from sensible to latent as air warms.
        sensible_fraction = min(0.8, max(0.15, 0.75 - 0.025 * (t - 20.0)))
        latent_w = total_w * (1.0 - sensible_fraction)
        co2_m3_s = 0.185 * (total_w / 1000.0) / 3600.0
        o2_m3_s = co2_m3_s / 0.95
        water_factor = 1.0 + 0.06 * max(0.0, t - 21.0)
        feed_kg_s = count * self.feed_kg_bird_day * feed_factor / 86_400.0
        water_l_s = count * self.feed_kg_bird_day * self.water_feed_ratio * water_factor / 86_400.0
        if not water_available:
            water_l_s = 0.0
        ammonia_g_s = count * self.ammonia_g_bird_day / 86_400.0 * 2.0 ** ((t - 20.0) / 10.0)
        return {
            "sensible_w": total_w * sensible_fraction,
            "latent_w": latent_w,
            "moisture_g_s": latent_w / LATENT_HEAT_J_G,
            "co2_kg_s": co2_m3_s * CO2_DENSITY_KG_M3,
            "o2_kg_s": o2_m3_s * O2_DENSITY_KG_M3,
            "ammonia_g_s": ammonia_g_s,
            "feed_kg_s": feed_kg_s,
            "water_l_s": water_l_s,
            "heat_stress_k": heat_stress,
            "cold_stress_k": max(0.0, self.lower_critical_c - t),
        }

    def advance_welfare(self, animals, dt, *, temperature_c, o2_pct, co2_ppm, co_ppm, ammonia_ppm,
                        feed_available, water_available, floor_water_cm=0.0):
        """Accumulate exposure and update the aggregate welfare index in ``animals``."""
        hours = float(dt) / 3600.0
        thermal = max(0.0, self.lower_critical_c - 10.0 - temperature_c, temperature_c - self.upper_critical_c)
        hypoxia = max(0.0, 19.5 - o2_pct)
        gas = max(0.0, co2_ppm - 5000.0) / 1000.0
        co = max(0.0, co_ppm - 50.0) / 100.0
        ammonia = max(0.0, ammonia_ppm - 25.0) / 25.0
        self.hours_without_water = 0.0 if water_available else self.hours_without_water + hours
        self.hours_without_feed = 0.0 if feed_available else self.hours_without_feed + hours
        thirst = max(0.0, self.hours_without_water - 6.0) / 6.0
        hunger = max(0.0, self.hours_without_feed - 12.0) / 12.0
        animals["thermal_exposure_degree_hours"] += thermal * hours
        animals["hypoxia_exposure_pct_hours"] += hypoxia * hours
        animals["gas_exposure_ppm_hours"] += max(0.0, co2_ppm - 5000.0) * hours
        animals["co_exposure_ppm_hours"] = animals.get("co_exposure_ppm_hours", 0.0) + max(0.0, co_ppm) * hours
        animals["hours_without_water"] = self.hours_without_water
        animals["hours_without_feed"] = self.hours_without_feed
        # Birds standing in water lose heat and get wet litter: stress grows with depth.
        wet = max(0.0, float(floor_water_cm) - 1.0) / 5.0
        damage_pct_h = thermal * 0.5 + hypoxia * 2.0 + gas * 0.1 + co * 4.0 + ammonia * 0.5 + thirst * 2.0 + hunger + wet
        recovery_pct_h = 0.01 if damage_pct_h == 0 else 0.0
        animals["welfare_index_pct"] = max(0.0, min(100.0,
            animals["welfare_index_pct"] + (recovery_pct_h - damage_pct_h) * hours))
        # Activity falls with welfare and with heat stress (birds rest and pant).
        activity = 65.0 * animals["welfare_index_pct"] / 100.0 * exp(-0.08 * max(0.0, temperature_c - self.upper_critical_c))
        return round(activity, 4)
