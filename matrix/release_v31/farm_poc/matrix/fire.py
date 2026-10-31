"""Compartment fire source: growth, oxygen limit, fuel burnout, and products.

The fire is a source term inside a well-mixed zone. It does not resolve flame
or plume geometry; a ceiling-jet correlation supplies the extra heat and smoke
that a ceiling-mounted detector sees near the fire.

Literature values (typical, uncalibrated for any specific barn):
- t-squared growth coefficients, NFPA 72 Annex B: slow 0.00293, medium 0.01172,
  fast 0.0469, ultrafast 0.1876 kW/s^2.
- Cellulosic fuel (wood/straw class), SFPE Handbook: effective heat of
  combustion 12.4 MJ/kg, CO2 yield 1.27, CO yield 0.004, soot yield 0.015 kg/kg.
- Oxygen consumption: Thornton's rule, 13.1 MJ released per kg of O2.
- Under-ventilated CO yield approaches 0.2 kg/kg.
- Ceiling jet: Alpert (1972) maximum excess temperature.
- Detector-zone smoke follows the ceiling-jet temperature rise by
  Heskestad-Delichatsios similarity.
"""
from dataclasses import dataclass, field, asdict
from math import exp, sqrt

GROWTH_ALPHA_KW_S2 = {"slow": 0.00293, "medium": 0.01172, "fast": 0.0469, "ultrafast": 0.1876}
THORNTON_KJ_PER_KG_O2 = 13_100.0
AIR_CP_KJ_KG_K = 1.005
AIR_DENSITY_KG_M3 = 1.2

FUEL_MODES = {
    # heat of combustion kJ/kg, yields kg/kg, radiative fraction
    "flaming": {"heat_of_combustion_kj_kg": 12_400.0, "co2_yield": 1.27, "co_yield": 0.004,
                "soot_yield": 0.015, "water_yield": 0.55, "radiative_fraction": 0.30,
                "mass_extinction_m2_g": 8.7},
    "smoldering": {"heat_of_combustion_kj_kg": 7_000.0, "co2_yield": 1.0, "co_yield": 0.15,
                   "soot_yield": 0.05, "water_yield": 0.55, "radiative_fraction": 0.0,
                   "mass_extinction_m2_g": 4.4},
}
UNDERVENTILATED_CO_YIELD = 0.2
OXYGEN_EXTINCTION_PCT = 15.0
OXYGEN_FULL_BURNING_PCT = 19.0


@dataclass
class Fire:
    zone: str
    mode: str = "flaming"
    growth: str = "medium"
    peak_kw: float = 300.0
    fuel_kg: float = 60.0
    position_m: tuple = (2.0, 2.0)
    smolder_initial_kw: float = 0.1
    smolder_growth_kw_h: float = 0.5
    smolder_peak_kw: float = 2.0
    transition_to_flaming_s: float | None = None
    steady_kw: float | None = None
    elapsed_s: float = 0.0
    flaming_elapsed_s: float = 0.0
    hrr_kw: float = 0.0
    state: str = "burning"
    decay_tau_s: float = 60.0
    fuel_consumed_kg: float = 0.0
    history: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.mode not in FUEL_MODES:
            raise ValueError("fire mode must be flaming or smoldering")
        if self.growth not in GROWTH_ALPHA_KW_S2:
            raise ValueError("unknown fire growth class")
        if not 0.0 < float(self.peak_kw) <= 20_000.0 or not 0.0 < float(self.fuel_kg) <= 50_000.0:
            raise ValueError("fire peak HRR or fuel mass is outside the bounded simulation range")

    def oxygen_factor(self, o2_pct):
        span = OXYGEN_FULL_BURNING_PCT - OXYGEN_EXTINCTION_PCT
        return min(1.0, max(0.0, (float(o2_pct) - OXYGEN_EXTINCTION_PCT) / span))

    def advance(self, dt, o2_pct):
        """Advance by ``dt`` seconds; return the source terms for that interval."""
        dt = float(dt)
        if self.state == "out" or dt <= 0:
            self.hrr_kw = 0.0 if self.state == "out" else self.hrr_kw
            return self._products(0.0, 1.0)
        self.elapsed_s += dt
        factor = self.oxygen_factor(o2_pct)
        if self.state in ("extinguishing", "burnout"):
            self.hrr_kw *= exp(-dt / self.decay_tau_s)
            if self.hrr_kw < 0.05:
                self.hrr_kw = 0.0
                self.state = "out"
        elif self.steady_kw is not None:
            self.hrr_kw = float(self.steady_kw) * factor
        elif self.mode == "smoldering":
            if self.transition_to_flaming_s is not None and self.elapsed_s >= self.transition_to_flaming_s and factor > 0:
                self.mode = "flaming"
                # Continue the t-squared curve from the current smoldering output.
                self.flaming_elapsed_s = sqrt(self.hrr_kw / GROWTH_ALPHA_KW_S2[self.growth])
            self.hrr_kw = min(self.smolder_peak_kw,
                              self.smolder_initial_kw + self.smolder_growth_kw_h * self.elapsed_s / 3600.0)
            # Smoldering survives in vitiated air far below the flaming limit.
            self.hrr_kw *= min(1.0, max(0.0, float(o2_pct) / 10.0))
        if self.mode == "flaming" and self.state == "burning" and self.steady_kw is None:
            self.flaming_elapsed_s += dt
            alpha = GROWTH_ALPHA_KW_S2[self.growth]
            demand = min(float(self.peak_kw), alpha * self.flaming_elapsed_s ** 2)
            # A flaming fire starved of oxygen keeps pyrolysing at a low rate.
            self.hrr_kw = max(demand * factor, min(demand, 1.0))
        props = FUEL_MODES[self.mode]
        burned = self.hrr_kw * dt / props["heat_of_combustion_kj_kg"]
        remaining = max(0.0, float(self.fuel_kg) - self.fuel_consumed_kg)
        if burned >= remaining and self.state == "burning":
            burned = remaining
            self.state = "burnout"
        self.fuel_consumed_kg += burned
        return self._products(burned / dt if dt else 0.0, factor)

    def extinguish(self):
        if self.state == "burning":
            self.state = "extinguishing"
            self.decay_tau_s = 30.0

    def _products(self, burning_rate_kg_s, oxygen_factor):
        props = FUEL_MODES[self.mode]
        co_yield = props["co_yield"] + (UNDERVENTILATED_CO_YIELD - props["co_yield"]) * (1.0 - oxygen_factor) \
            if self.mode == "flaming" else props["co_yield"]
        hrr_kw = self.hrr_kw
        return {
            "hrr_kw": hrr_kw,
            "convective_w": hrr_kw * 1000.0 * (1.0 - props["radiative_fraction"]),
            "radiative_w": hrr_kw * 1000.0 * props["radiative_fraction"],
            "o2_kg_s": hrr_kw / THORNTON_KJ_PER_KG_O2,
            "co2_kg_s": burning_rate_kg_s * props["co2_yield"],
            "co_kg_s": burning_rate_kg_s * co_yield,
            "soot_kg_s": burning_rate_kg_s * props["soot_yield"],
            # Optical extinction area produced per second (m^2/s).
            "smoke_extinction_area_m2_s": burning_rate_kg_s * props["soot_yield"] * props["mass_extinction_m2_g"] * 1000.0,
            "water_kg_s": burning_rate_kg_s * props["water_yield"],
            "co_yield": co_yield,
        }

    def ceiling_jet(self, ceiling_height_m, distance_m):
        """Alpert excess gas temperature (K) under the ceiling at ``distance_m``."""
        q = self.hrr_kw
        if q <= 0.0:
            return 0.0
        height = max(0.5, float(ceiling_height_m) - 0.5)  # fuel bed about 0.5 m high
        radius = max(0.05, float(distance_m))
        if radius / height <= 0.18:
            return 16.9 * q ** (2.0 / 3.0) / height ** (5.0 / 3.0)
        return 5.38 * (q / radius) ** (2.0 / 3.0) / height

    def local_excess(self, ceiling_height_m, distance_m, products):
        """Extra temperature, soot, and CO a ceiling-mounted device sees near the fire."""
        delta_t = self.ceiling_jet(ceiling_height_m, distance_m)
        props = FUEL_MODES[self.mode]
        convective = 1.0 - props["radiative_fraction"]
        per_kelvin = AIR_CP_KJ_KG_K / (convective * props["heat_of_combustion_kj_kg"])
        mass_fraction_per_yield = delta_t * per_kelvin
        soot_g_m3 = mass_fraction_per_yield * props["soot_yield"] * AIR_DENSITY_KG_M3 * 1000.0
        return {
            "temperature_c": delta_t,
            "smoke_extinction_per_m": soot_g_m3 * props["mass_extinction_m2_g"],
            "co_ppm": mass_fraction_per_yield * products["co_yield"] * (28.97 / 28.01) * 1e6,
        }

    def distance_to(self, point):
        return sqrt((self.position_m[0] - point[0]) ** 2 + (self.position_m[1] - point[1]) ** 2)

    def public_summary(self):
        """Evaluator-only description; never published to devices or Jarvis."""
        data = asdict(self)
        data.pop("history", None)
        return data


def smoke_obscuration_pct_per_m(extinction_per_m):
    """Light obscuration over one metre for an extinction coefficient (1/m)."""
    return 100.0 * (1.0 - exp(-max(0.0, float(extinction_per_m))))
