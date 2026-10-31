"""Farm plant physics v2: multi-zone air network, thermal mass, gases, fire.

Physical state lives here; device fields are sensor/actuator representations.
Every published measurement passes through ``SensorBank`` (lag, noise,
resolution, range). See docs/matrix-physics.md for equations and sources.

Zones are well mixed. Each zone has an air node and a thermal-mass node
(ISO 13790-style two-node RC model). Air moves through the HVAC supply/return
loop, envelope infiltration, barn windows/vents (wind + stack effect), and
buoyancy-driven leakage between neighbouring rooms, carrying heat, moisture,
CO2, O2, CO, NH3, and smoke between zones.
"""
from math import ceil, exp, isfinite, sin, sqrt

from .animals import Flock
from .fire import Fire, smoke_obscuration_pct_per_m
from .sensors import SensorBank

AIR_RHO_CP = 1200.0  # J/(m^3 K), dry air near 20 °C
GRAVITY = 9.81
GAS_DENSITY_KG_M3 = {"co2": 1.829, "co": 1.164, "o2": 1.330, "nh3": 0.708}  # 20 °C, 1 atm
SPECIES = ("absolute_humidity_g_m3", "co2_ppm", "o2_pct", "co_ppm", "ammonia_ppm", "smoke_extinction_per_m")
HVAC_ZONES = ("barn", "storage", "workspace")
INCUBATORS = ("incubator-1", "incubator-2")

# Floor plan (length, width) and ceiling height per room; volumes follow.
DEFAULT_GEOMETRY = {
    "barn": {"floor_m": (12.5, 10.0), "height_m": 4.0, "window_area_m2": 1.0},
    "storage": {"floor_m": (7.5, 8.0), "height_m": 3.0, "window_area_m2": 0.0},
    "workspace": {"floor_m": (6.0, 5.5), "height_m": 3.0, "window_area_m2": 0.5},
}
# Wall-mounted zone thermostats sit below the ceiling jet; ceiling devices do not.
WALL_MOUNT_CEILING_JET_FACTOR = 0.25
DEFAULT_SENSOR_POSITIONS = {"barn": (1.0, 5.0), "storage": (0.5, 4.0), "workspace": (0.5, 2.75)}
SMOKE_DETECTOR_POSITION = (6.25, 5.0)


def saturation_vapor_pressure_hpa(temp_c: float) -> float:
    """Buck saturation-vapor-pressure approximation over liquid water."""
    t = float(temp_c)
    return 6.1121 * exp((18.678 - t / 234.5) * (t / (257.14 + t)))


def relative_humidity_to_absolute_g_m3(temp_c: float, rh_pct: float) -> float:
    vapor_hpa = saturation_vapor_pressure_hpa(temp_c) * max(0.0, float(rh_pct)) / 100.0
    return 216.7 * vapor_hpa / (float(temp_c) + 273.15)


def absolute_humidity_to_relative_pct(temp_c: float, absolute_g_m3: float) -> float:
    vapor_hpa = max(0.0, float(absolute_g_m3)) * (float(temp_c) + 273.15) / 216.7
    return min(100.0, max(0.0, 100.0 * vapor_hpa / saturation_vapor_pressure_hpa(temp_c)))


def r410a_saturation_pressure_kpa(temp_c: float) -> float:
    """Clausius-Clapeyron fit through R-410A saturation at 0 °C and 10 °C."""
    return exp(6.682 - 2374.0 * (1.0 / (float(temp_c) + 273.15) - 1.0 / 273.15))


def _advance_linear(value, equilibrium, conductance, capacity, dt):
    """Exact update for C*dT/dt=G*(T_eq-T), stable for large positive dt."""
    if dt <= 0 or conductance <= 0:
        return float(value)
    return float(equilibrium) + (float(value) - float(equilibrium)) * exp(-conductance * dt / capacity)


def _relax(value, inflows, source, volume, dt):
    """Advance a well-mixed concentration with balanced inflows and a source rate."""
    q_total = 0.0
    weighted = 0.0
    for q, concentration in inflows:
        q_total += q
        weighted += q * concentration
    if q_total <= 0.0:
        return value + source * dt / volume
    equilibrium = (weighted + source) / q_total
    return equilibrium + (value - equilibrium) * exp(-q_total * dt / volume)


class PhysicalWorld:
    """Authoritative temperatures, moisture, gases, smoke, fire, and flock state."""

    MAX_SUBSTEP_S = 5.0

    def __init__(self, devices, config=None, seed=0):
        config = config or {}
        environment = config.get("environment", {})
        i1 = devices["incubator-1"].internal
        hvac = devices["hvac"].internal
        apu = devices["animal-protection"].internal
        outside_t = float(environment.get("outside_temperature_c", i1.get("ambient_temperature_c", 22.0)))
        outside_rh = float(environment.get("outside_relative_humidity_pct", i1.get("ambient_humidity_pct", 40.0)))
        if not isfinite(outside_t) or not -80.0 <= outside_t <= 80.0:
            raise ValueError("environment.outside_temperature_c must be within [-80, 80] °C")
        if not isfinite(outside_rh) or not 0.0 <= outside_rh <= 100.0:
            raise ValueError("environment.outside_relative_humidity_pct must be within [0, 100]")
        geometry = {name: dict(DEFAULT_GEOMETRY[name], **environment.get("geometry", {}).get(name, {}))
                    for name in HVAC_ZONES}
        volumes = {name: g["floor_m"][0] * g["floor_m"][1] * g["height_m"] for name, g in geometry.items()}
        volumes.update(environment.get("hvac_zone_volume_m3", {}))
        floor_area = {name: float(volumes[name]) / geometry[name]["height_m"] for name in HVAC_ZONES}
        roof_area = dict(floor_area)
        wall_area = {name: 2.0 * (sqrt(floor_area[name]) * 2.0) * geometry[name]["height_m"] for name in HVAC_ZONES}
        u_value = float(environment.get("envelope_u_w_m2_k", 0.5))  # insulated sandwich panel
        default_ua = {name: u_value * (roof_area[name] + wall_area[name]) for name in HVAC_ZONES}
        # ISO 13790 "medium" construction: 165 kJ/(m^2 K) of floor area.
        default_capacity = {name: 165_000.0 * floor_area[name] for name in HVAC_ZONES}
        self.parameters = {
            "incubator_thermal_capacity_j_k": float(environment.get("incubator_thermal_capacity_j_k", 40_000.0)),
            "incubator_envelope_ua_w_k": float(environment.get("incubator_envelope_ua_w_k", 8.0)),
            "incubator_volume_m3": float(environment.get("incubator_volume_m3", 0.5)),
            "incubator_internal_coupling_w_k": float(environment.get("incubator_internal_coupling_w_k", 18.0)),
            "incubator_circulation_m3_h": float(environment.get("incubator_circulation_m3_h", 150.0)),
            "incubator_room_zone": environment.get("incubator_room_zone", "workspace"),
            "hvac_zone_capacity_j_k": {**default_capacity, **environment.get("hvac_zone_capacity_j_k", {})},
            "hvac_zone_ua_w_k": {**default_ua, **environment.get("hvac_zone_ua_w_k", {})},
            "hvac_zone_volume_m3": {name: float(v) for name, v in volumes.items()},
            "animal_zone_volume_m3": float(environment.get("animal_zone_volume_m3", volumes["barn"])),
            "geometry": geometry,
            "roof_fraction": {n: roof_area[n] / (roof_area[n] + wall_area[n]) for n in HVAC_ZONES},
            "air_mass_coupling_w_k": {n: 9.1 * 2.5 * floor_area[n] for n in HVAC_ZONES},
            "window_u_w_m2_k": 2.8,
            "window_shgc": 0.6,
            "roof_absorptance": 0.6,
            "outside_film_w_m2_k": 20.0,
            "infiltration_ach": {"barn": 0.25, "storage": 0.2, "workspace": 0.25,
                                 **environment.get("infiltration_ach", {})},
            "barn_window_area_m2": float(environment.get("barn_window_area_m2", 1.0)),
            "barn_vent_area_m2": float(environment.get("barn_vent_area_m2", 0.5)),
            "interzone_openings": [tuple(item) for item in environment.get("interzone_openings", (
                ("barn", "workspace", 0.02, 2.1), ("workspace", "storage", 0.02, 2.1),
                ("barn", "storage", 0.01, 2.1)))],
            "interzone_base_m3_h": float(environment.get("interzone_base_m3_h", 5.0)),
            "hvac_filter_smoke_efficiency": float(environment.get("hvac_filter_smoke_efficiency", 0.15)),
            "coil_minimum_supply_c": 8.0,
        }
        if self.parameters["incubator_room_zone"] not in HVAC_ZONES + ("outside",):
            raise ValueError("environment.incubator_room_zone must be an HVAC zone or 'outside'")
        positive = (self.parameters["incubator_thermal_capacity_j_k"], self.parameters["incubator_volume_m3"],
                    self.parameters["animal_zone_volume_m3"], *self.parameters["hvac_zone_capacity_j_k"].values(),
                    *self.parameters["hvac_zone_volume_m3"].values())
        if any(not isfinite(float(v)) or float(v) <= 0.0 for v in positive):
            raise ValueError("physical zone capacities and volumes must be positive")
        ua = (self.parameters["incubator_envelope_ua_w_k"], *self.parameters["hvac_zone_ua_w_k"].values())
        if any(not isfinite(float(v)) or float(v) < 0.0 for v in ua):
            raise ValueError("physical envelope UA values must be non-negative")
        incubator_air_capacity = AIR_RHO_CP * self.parameters["incubator_volume_m3"] * 3.0
        if self.parameters["incubator_thermal_capacity_j_k"] <= incubator_air_capacity:
            raise ValueError("incubator thermal capacity must exceed its air capacity")
        self._capacity = {name: (AIR_RHO_CP * self.parameters["hvac_zone_volume_m3"][name] * 2.0,
                                 float(self.parameters["hvac_zone_capacity_j_k"][name])) for name in HVAC_ZONES}
        for name in INCUBATORS:
            self._capacity[name] = (incubator_air_capacity,
                                    self.parameters["incubator_thermal_capacity_j_k"] - incubator_air_capacity)
        self._volume = {**self.parameters["hvac_zone_volume_m3"],
                        **{name: self.parameters["incubator_volume_m3"] for name in INCUBATORS}}
        self._volume["barn"] = self.parameters["animal_zone_volume_m3"]

        sensor_config = config.get("sensors", {})
        self.sensors = SensorBank(seed, ideal=sensor_config.get("ideal", False),
                                  overrides=sensor_config.get("overrides"))
        self.flock = Flock(environment.get("flock"))
        self.fire = None
        self._products = None
        self.storm = None  # active severe-weather offsets applied on top of the base weather

        weather = getattr(devices["animal-protection"], "weather_provider", {}) or {}
        self.state = {"outside": {
            "temperature_c": outside_t,
            "absolute_humidity_g_m3": relative_humidity_to_absolute_g_m3(outside_t, outside_rh),
            "co2_ppm": float(environment.get("outside_co2_ppm", 420.0)),
            "o2_pct": float(environment.get("outside_o2_pct", 20.95)),
            "co_ppm": float(environment.get("outside_co_ppm", 0.1)),
            "ammonia_ppm": 0.0,
            "smoke_extinction_per_m": 0.0,
            "wind_speed_m_s": float(environment.get("wind_speed_m_s", weather.get("wind_speed_m_s", 2.0))),
            "solar_w_m2": float(environment.get("solar_w_m2", 0.0)),
            "wind_direction_deg": float(environment.get("wind_direction_deg", weather.get("wind_direction_deg", 220.0))),
            "rain_mm_h": float(environment.get("rain_mm_h", 0.0)),
            "pressure_hpa": float(environment.get("pressure_hpa", weather.get("barometric_pressure_hpa", 1013.0))),
            "floodwater_level_cm": 0.0,
            "cumulative_rain_mm": 0.0,
        }, "zones": {}, "fire": {"active": False}, "storm": {"active": False},
        "barn_floor_water_cm": 0.0,
        "benign_sources": {"co2_ppm_s": 0.0, "ammonia_ppm_s": 0.0}, "animals": {
            "welfare_index_pct": 100.0,
            "thermal_exposure_degree_hours": 0.0,
            "hypoxia_exposure_pct_hours": 0.0,
            "gas_exposure_ppm_hours": 0.0,
            "co_exposure_ppm_hours": 0.0,
            "hours_without_water": 0.0,
            "hours_without_feed": 0.0,
        }, "hvac": {}, "airflows_m3_h": {}}
        # Weather inputs before storm offsets; the effective outdoor state is rebuilt each tick.
        self._outside_base = {key: self.state["outside"][key] for key in ("temperature_c", "wind_speed_m_s", "rain_mm_h", "pressure_hpa")}
        indoor_defaults = {"co2_ppm": 450.0, "o2_pct": 20.94, "co_ppm": 0.1, "ammonia_ppm": 0.0,
                           "smoke_extinction_per_m": 0.0}
        for device_id in INCUBATORS:
            d = devices[device_id].internal
            temps = [float(v) for v in d["room_temperatures_c"] if v is not None]
            rhs = [float(v) for v in d["room_humidity_pct"] if v is not None]
            temp = sum(temps) / len(temps) if temps else 37.5
            rh = sum(rhs) / len(rhs) if rhs else 55.0
            self.state["zones"][device_id] = {"temperature_c": temp, "mass_temperature_c": temp,
                "absolute_humidity_g_m3": relative_humidity_to_absolute_g_m3(temp, rh),
                **indoor_defaults,
                # Privileged physical input. Scenarios may set this bounded
                # heat source; sensors remain derived from zone truth.
                "external_heat_load_w": 0.0}
        for name, z in hvac["zones"].items():
            t = float(z["temperature_c"])
            self.state["zones"][name] = {"temperature_c": t, "mass_temperature_c": t,
                "absolute_humidity_g_m3": relative_humidity_to_absolute_g_m3(t, z["humidity_pct"]),
                **indoor_defaults}
        self._hvac_sensor_offsets = {name: {
            "temperature": [None if v is None else float(v) - float(z["temperature_c"]) for v in z.get("temperature_sensors_c", [])],
            "humidity": [None if v is None else float(v) - float(z["humidity_pct"]) for v in z.get("humidity_sensors_pct", [])],
        } for name, z in hvac["zones"].items()}
        barn_quality = apu["air_quality_by_zone"].get("barn", {})
        barn_initial = {
            "co2_ppm": environment.get("barn_initial_co2_ppm", barn_quality.get("co2_ppm") if barn_quality.get("co2_ppm") is not None else 600.0),
            "o2_pct": environment.get("barn_initial_o2_pct", barn_quality.get("o2_pct") if barn_quality.get("o2_pct") is not None else 20.9),
            "ammonia_ppm": environment.get("barn_initial_ammonia_ppm", barn_quality.get("ammonia_ppm") if barn_quality.get("ammonia_ppm") is not None else 5.0),
        }
        self._gas_sensors_available = {key: barn_quality.get(key) is not None
                                       for key in ("co2_ppm", "o2_pct", "ammonia_ppm")}
        self._gas_sensors_available["co_ppm"] = bool(devices["animal-protection"].capabilities.get("carbon_monoxide", True))
        self.state["zones"]["barn"].update({key: float(value) for key, value in barn_initial.items()})
        self._sensor_offsets = {}
        self._humidity_offsets = {}
        for device_id in INCUBATORS:
            d = devices[device_id].internal
            truth = self.state["zones"][device_id]["temperature_c"]
            self._sensor_offsets[device_id] = [None if v is None else float(v) - truth for v in d["room_temperatures_c"]]
            valid_rh = [v for v in d["room_humidity_pct"] if v is not None]
            mean_rh = sum(valid_rh) / max(1, len(valid_rh))
            self._humidity_offsets[device_id] = [None if v is None else float(v) - mean_rh for v in d["room_humidity_pct"]]
        self.sync_devices(devices, initialize=True)

    # ------------------------------------------------------------------ inputs
    def set_incubator_external_heat_load(self, device_id: str, watts: float) -> None:
        """Set a bounded scenario heat source in the authoritative plant state."""
        if device_id not in INCUBATORS:
            raise ValueError("external heat loads are supported only for incubator zones")
        watts = float(watts)
        if not isfinite(watts) or not 0.0 <= watts <= 10_000.0:
            raise ValueError("external heat load must be finite and within [0, 10000] W")
        self.state["zones"][device_id]["external_heat_load_w"] = watts

    def ignite(self, zone: str, **parameters) -> Fire:
        """Start a fire in an HVAC zone; parameters follow ``fire.Fire``."""
        if zone not in HVAC_ZONES:
            raise ValueError("fires are supported in barn, storage, and workspace zones")
        if "position_m" not in parameters:
            length, width = self.parameters["geometry"][zone]["floor_m"]
            parameters["position_m"] = (length / 2.0, width / 2.0)
        self.fire = Fire(zone=zone, **parameters)
        self._update_fire_summary()
        return self.fire

    def extinguish(self) -> None:
        """Begin suppression; heat release decays over about a minute."""
        if self.fire is not None:
            self.fire.extinguish()
            self._update_fire_summary()

    def set_fire_source(self, *, heat_w: float, smoke_source_ppm_s: float = 0.0) -> None:
        """Legacy constant-output barn fire; products follow the flaming yields."""
        heat_w = float(heat_w)
        if not isfinite(heat_w) or not 0.0 <= heat_w <= 20_000_000.0:
            raise ValueError("fire source exceeds bounded simulation configuration")
        if heat_w <= 0.0:
            self.extinguish()
            return
        self.ignite("barn", steady_kw=heat_w / 1000.0, peak_kw=max(heat_w / 1000.0, 0.001), fuel_kg=50_000.0)

    def set_outside_conditions(self, *, temperature_c: float, relative_humidity_pct: float,
                               solar_w_m2: float | None = None, wind_speed_m_s: float | None = None,
                               wind_direction_deg: float | None = None, rain_mm_h: float | None = None,
                               pressure_hpa: float | None = None) -> None:
        """Update simulated ambient boundary conditions, not any sensor directly."""
        temperature_c, relative_humidity_pct = float(temperature_c), float(relative_humidity_pct)
        if not isfinite(temperature_c) or not -80.0 <= temperature_c <= 80.0:
            raise ValueError("outside temperature must be finite and within [-80, 80] °C")
        if not isfinite(relative_humidity_pct) or not 0.0 <= relative_humidity_pct <= 100.0:
            raise ValueError("outside humidity must be finite and within [0, 100] %")
        outside = self.state["outside"]
        self._outside_base["temperature_c"] = temperature_c
        outside["temperature_c"] = temperature_c
        outside["absolute_humidity_g_m3"] = relative_humidity_to_absolute_g_m3(
            temperature_c, relative_humidity_pct)
        if solar_w_m2 is not None:
            if not isfinite(float(solar_w_m2)) or not 0.0 <= float(solar_w_m2) <= 1_400.0:
                raise ValueError("solar irradiance must be within [0, 1400] W/m^2")
            outside["solar_w_m2"] = float(solar_w_m2)
        if wind_speed_m_s is not None:
            if not isfinite(float(wind_speed_m_s)) or not 0.0 <= float(wind_speed_m_s) <= 80.0:
                raise ValueError("wind speed must be within [0, 80] m/s")
            outside["wind_speed_m_s"] = float(wind_speed_m_s)
            self._outside_base["wind_speed_m_s"] = float(wind_speed_m_s)
        if wind_direction_deg is not None:
            outside["wind_direction_deg"] = float(wind_direction_deg) % 360.0
        if rain_mm_h is not None:
            if not isfinite(float(rain_mm_h)) or not 0.0 <= float(rain_mm_h) <= 300.0:
                raise ValueError("rain rate must be within [0, 300] mm/h")
            outside["rain_mm_h"] = float(rain_mm_h)
            self._outside_base["rain_mm_h"] = float(rain_mm_h)
        if pressure_hpa is not None:
            if not isfinite(float(pressure_hpa)) or not 850.0 <= float(pressure_hpa) <= 1085.0:
                raise ValueError("barometric pressure must be within [850, 1085] hPa")
            outside["pressure_hpa"] = float(pressure_hpa)
            self._outside_base["pressure_hpa"] = float(pressure_hpa)

    def start_storm(self, **storm) -> None:
        """Severe weather on top of the base weather: pressure dip, wind, rain, cooling.

        Keys: ``pressure_drop_hpa``, ``wind_m_s``, ``gust_m_s``, ``rain_mm_h``,
        ``cooling_c``, ``ramp_s`` and optional ``tornado_strike`` (local wind
        far above the storm's). Offsets ramp in and out over ``ramp_s``.
        """
        self.storm = {"pressure_drop_hpa": 8.0, "wind_m_s": 12.0, "gust_m_s": 20.0, "rain_mm_h": 30.0,
                      "cooling_c": 4.0, "ramp_s": 1800.0, "tornado_strike": False, **storm,
                      "elapsed_s": 0.0, "ending": False, "level": 0.0}
        self.state["storm"] = {"active": True, **{k: v for k, v in self.storm.items()}}

    def end_storm(self) -> None:
        if self.storm is not None:
            self.storm["ending"] = True

    def _storm_offsets(self, dt):
        storm = self.storm
        if storm is None:
            return {}
        ramp = max(1.0, float(storm["ramp_s"]))
        storm["elapsed_s"] += dt
        storm["level"] = max(0.0, min(1.0, storm["level"] + (-dt if storm["ending"] else dt) / ramp))
        if storm["ending"] and storm["level"] <= 0.0:
            self.storm = None
            self.state["storm"] = {"active": False}
            return {}
        level = storm["level"]
        gust_phase = 0.5 * (1.0 + sin(storm["elapsed_s"] / 37.0))
        wind = level * (storm["wind_m_s"] + (storm["gust_m_s"] - storm["wind_m_s"]) * gust_phase)
        if storm.get("tornado_strike") and 0.45 < level < 0.75 and not storm["ending"]:
            wind = max(wind, 45.0)  # brief vortex passage over the site
        self.state["storm"] = {"active": True, **storm}
        return {"pressure_hpa": -level * storm["pressure_drop_hpa"], "wind_speed_m_s": wind,
                "rain_mm_h": level * storm["rain_mm_h"], "temperature_c": -level * storm["cooling_c"]}

    # ----------------------------------------------------------------- physics
    def integrate(self, devices, dt):
        """Advance physical state once using post-controller equipment feedback."""
        dt = float(dt)
        if dt <= 0:
            return
        steps = max(1, int(ceil(dt / self.MAX_SUBSTEP_S)))
        h = dt / steps
        outside = self.state["outside"]
        offsets = self._storm_offsets(dt)
        for key, value in self._outside_base.items():
            outside[key] = value + offsets.get(key, 0.0)
        outside["wind_speed_m_s"] = max(0.0, outside["wind_speed_m_s"]); outside["rain_mm_h"] = max(0.0, outside["rain_mm_h"])
        self._hydrology(devices, dt)
        plant = self._equipment_inputs(devices)
        apu = devices["animal-protection"].internal
        barn_t = self.state["zones"]["barn"]["temperature_c"]
        flock = self.flock.outputs(apu.get("population_count") or 0, barn_t,
                                   feed_available=plant["feed_available"], water_available=plant["water_available"])
        for _ in range(steps):
            self._substep(h, plant, flock)
        self._finish_tick(devices, dt, plant, flock)

    def _hydrology(self, devices, dt):
        """Site runoff, floodwater level, and water entering the barn."""
        outside = self.state["outside"]
        apu = devices["animal-protection"].internal
        rain = outside["rain_mm_h"]
        outside["cumulative_rain_mm"] += rain * dt / 3600.0
        level = outside["floodwater_level_cm"]
        # Runoff concentrates about 0.25 cm of standing water per mm of rain; drains exponentially (tau ~3 h).
        level += (0.25 * rain - level / 3.0) * dt / 3600.0
        outside["floodwater_level_cm"] = max(0.0, level)
        barrier = apu.get("flood_barrier_state") == "deployed"
        low_vents_open = float((apu.get("vent_actual_positions_pct") or {}).get("barn", 0.0)) > 5.0
        sill_cm = 60.0 if barrier else 10.0
        entry = max(0.0, level - sill_cm) + (max(0.0, level - 20.0) if low_vents_open and not barrier else 0.0)
        floor = self.state["barn_floor_water_cm"]
        drain_tau_h = 0.3 if self.state.get("barn_pumping") else 2.0  # civil-protection pumps on site
        floor += (0.5 * entry - floor / drain_tau_h) * dt / 3600.0
        self.state["barn_floor_water_cm"] = max(0.0, floor)
        self._water_entering_barn = entry > 0.0

    def _equipment_inputs(self, devices):
        p = self.parameters
        plant = {"incubators": {}}
        for device_id in INCUBATORS:
            d = devices[device_id].internal
            nominal = [max(1.0, float(v)) for v in d.get("fan_nominal_rpm", [])]
            actual = [max(0.0, float(v or 0.0)) for v in d.get("fan_actual_rpm", d.get("fan_rpm", []))]
            fan_ratio = sum(actual) / max(sum(nominal[:len(actual)]), 1.0) if actual else 0.0
            damper = max(0.0, min(100.0, float(d.get("damper_position_pct", d.get("damper_actual_pct") or 0.0)))) / 100.0
            fresh_m3_s = float(d.get("rated_airflow_m3_h", 33.61)) * fan_ratio * damper / 3600.0
            if d.get("door_open"):
                fresh_m3_s += 0.03  # an open cabinet door exchanges about 100 m^3/h
            plant["incubators"][device_id] = {
                "fresh_m3_s": fresh_m3_s,
                "heater_w": max(0.0, float(d.get("heater_delivered_w", d.get("heater_power_w", 0.0)))) * 0.92,
                "humidifier_g_s": max(0.0, float(d.get("humidifier_evaporation_ml_h", d.get("water_consumption_rate_ml_h", 0.0)))) * 0.7 / 3600.0,
                "circulation_m3_s": (p["incubator_circulation_m3_h"] * fan_ratio + 10.0) / 3600.0,
                "eggs": max(0, int(d.get("eggs") or 0)),
                "incubation_day": float(d.get("incubation_day", 0)) + (float(d.get("uptime_s", 0.0)) % 86_400.0) / 86_400.0,
            }
        hvac = devices["hvac"].internal
        speed = max(0.0, min(100.0, float(hvac.get("fan_speed_pct", 0.0) or 0.0))) / 100.0 if hvac.get("supply_fan_on") else 0.0
        supply_m3_s = max(0.0, float(hvac.get("airflow_m3_h", 0.0))) * speed * float(hvac.get("airflow_factor", 1.0)) / 3600.0
        dampers = hvac.get("damper_positions_pct", {})
        total = sum(max(0.0, float(v)) for v in dampers.values())
        if total <= 0.0:
            supply_m3_s, total = 0.0, 1.0  # every zone damper shut: the fan is dead-headed
        plant["hvac"] = {
            "supply_m3_s": supply_m3_s,
            "fractions": {name: max(0.0, float(dampers.get(name, 0.0))) / total for name in HVAC_ZONES},
            "outdoor_air_fraction": max(0.0, min(1.0, float(hvac.get("outdoor_air_fraction_effective", hvac.get("outdoor_air_fraction_actual", 0.2))))),
            "cooling_w": max(0.0, float(hvac.get("cooling_capacity_delivered_w", 0.0))),
            "heating_w": max(0.0, float(hvac.get("heating_delivered_w", 0.0))),
        }
        apu_device = devices["animal-protection"]
        apu = apu_device.internal
        windows = apu.get("window_actual_positions_pct") or {}
        window_frac = (sum(float(v) for v in windows.values()) / len(windows) / 100.0) if windows else 0.0
        vent_frac = float((apu.get("vent_actual_positions_pct") or {}).get("barn", apu.get("vents_pct", 0.0))) / 100.0
        if apu.get("emergency_shutter_state") == "deployed":
            window_frac = 0.0  # storm shutters seal the window openings
        plant["barn_opening_m2"] = (p["barn_window_area_m2"] * max(0.0, window_frac)
                                    + p["barn_vent_area_m2"] * max(0.0, vent_frac))
        hoppers = apu.get("feed_hopper_kg") or {}
        plant["feed_available"] = (sum(hoppers.values()) > 0.0) if hoppers else (
            bool(apu.get("feed_supply_available", True)) and float(apu.get("feed_storage_level_pct", 0.0)) > 0)
        plant["water_available"] = bool(apu.get("drinker_water_available", apu.get("water_pump_on", True)))
        return plant

    def _substep(self, h, plant, flock):
        p = self.parameters
        zones = self.state["zones"]
        outside = self.state["outside"]
        old = {name: dict(z) for name, z in zones.items()}
        room_name = p["incubator_room_zone"]
        flows = {name: [] for name in zones}  # (q_m3_s, source composition)
        heat = {name: 0.0 for name in zones}       # convective W into the air node
        mass_heat = {name: 0.0 for name in zones}  # W into the mass node
        source = {name: {s: 0.0 for s in SPECIES} for name in zones}  # species per second * volume

        # HVAC supply/return loop with outdoor-air mixing, filter, and coil.
        hv = plant["hvac"]
        supply = None
        q_s = hv["supply_m3_s"]
        if q_s > 1e-6:
            returned = {key: sum(hv["fractions"][n] * old[n][key] for n in HVAC_ZONES)
                        for key in ("temperature_c",) + SPECIES}
            oa = hv["outdoor_air_fraction"]
            mixed = {key: oa * outside[key] + (1.0 - oa) * returned[key] for key in returned}
            supply = dict(mixed)
            supply["smoke_extinction_per_m"] *= (1.0 - p["hvac_filter_smoke_efficiency"])
            capacity = AIR_RHO_CP * q_s
            cooling = hv["cooling_w"]
            sensible = 0.0
            coil_t = mixed["temperature_c"]
            condensate = 0.0
            if cooling > 0.0:
                coil_t, sensible, supply["absolute_humidity_g_m3"] = self._coil(
                    mixed["temperature_c"], mixed["absolute_humidity_g_m3"], q_s, cooling)
                condensate = (mixed["absolute_humidity_g_m3"] - supply["absolute_humidity_g_m3"]) * q_s
            supply["temperature_c"] = coil_t + hv["heating_w"] / capacity
            self.state["hvac"] = {"supply_temperature_c": supply["temperature_c"],
                "supply_absolute_humidity_g_m3": supply["absolute_humidity_g_m3"],
                "return_temperature_c": returned["temperature_c"],
                "return_absolute_humidity_g_m3": returned["absolute_humidity_g_m3"],
                "mixed_temperature_c": mixed["temperature_c"], "coil_sensible_w": max(0.0, sensible),
                "condensate_g_s": max(0.0, condensate), "supply_m3_h": q_s * 3600.0}
            for name in HVAC_ZONES:
                q = q_s * hv["fractions"][name]
                if q > 0.0:
                    flows[name].append((q, supply))
        else:
            self.state["hvac"] = {"supply_temperature_c": None, "supply_absolute_humidity_g_m3": None,
                "return_temperature_c": None, "return_absolute_humidity_g_m3": None,
                "mixed_temperature_c": None, "coil_sensible_w": 0.0, "condensate_g_s": 0.0, "supply_m3_h": 0.0}

        # Envelope infiltration and barn natural ventilation (wind + stack).
        for name in HVAC_ZONES:
            q_inf = p["infiltration_ach"][name] * self._volume[name] / 3600.0
            flows[name].append((q_inf, outside))
        opening = plant["barn_opening_m2"]
        if opening > 0.0:
            t_in = old["barn"]["temperature_c"] + 273.15
            dt_stack = abs(old["barn"]["temperature_c"] - outside["temperature_c"])
            q_wind = 0.3 * opening / 2.0 * outside["wind_speed_m_s"]
            q_stack = 0.65 * opening / 2.0 * sqrt(2.0 * GRAVITY * 1.0 * dt_stack / t_in)
            q_nat = sqrt(q_wind ** 2 + q_stack ** 2)
            flows["barn"].append((q_nat, outside))
        else:
            q_nat = 0.0

        # Buoyancy-driven exchange through door gaps between rooms.
        interzone = {}
        for a, b, area, height in p["interzone_openings"]:
            ta, tb = old[a]["temperature_c"] + 273.15, old[b]["temperature_c"] + 273.15
            q = p["interzone_base_m3_h"] / 3600.0 + 0.6 * area / 3.0 * sqrt(GRAVITY * height * abs(ta - tb) / ((ta + tb) / 2.0))
            flows[a].append((q, old[b]))
            flows[b].append((q, old[a]))
            interzone[f"{a}<->{b}"] = q * 3600.0

        # Incubator cabinets inside their room.
        room = outside if room_name == "outside" else old[room_name]
        for device_id, inc in plant["incubators"].items():
            z = old[device_id]
            if inc["fresh_m3_s"] > 0.0:
                flows[device_id].append((inc["fresh_m3_s"], room))
                if room_name != "outside":
                    flows[room_name].append((inc["fresh_m3_s"], z))
            heat[device_id] += inc["heater_w"] + float(z.get("external_heat_load_w", 0.0))
            source[device_id]["absolute_humidity_g_m3"] += inc["humidifier_g_s"]
            egg_w = inc["eggs"] * 0.14 / (1.0 + exp(-(inc["incubation_day"] - 15.0) / 1.8))
            mass_heat[device_id] += egg_w
            # Embryo respiration: about 19.6 kJ per litre O2 on yolk fat, RQ 0.72.
            o2_m3_s = egg_w / 19.6e6
            source[device_id]["o2_pct"] -= o2_m3_s * 100.0
            source[device_id]["co2_ppm"] += 0.72 * o2_m3_s * 1e6
            if room_name != "outside":
                envelope_w = p["incubator_envelope_ua_w_k"] * (z["temperature_c"] - room["temperature_c"])
                heat[room_name] += envelope_w + (inc["heater_w"] * 0.08 / 0.92)  # heater casing losses

        # Animals in the barn.
        heat["barn"] += flock["sensible_w"]
        source["barn"]["absolute_humidity_g_m3"] += flock["moisture_g_s"]
        source["barn"]["co2_ppm"] += flock["co2_kg_s"] / GAS_DENSITY_KG_M3["co2"] * 1e6
        source["barn"]["o2_pct"] -= flock["o2_kg_s"] / GAS_DENSITY_KG_M3["o2"] * 100.0
        source["barn"]["ammonia_ppm"] += flock["ammonia_g_s"] / 1000.0 / GAS_DENSITY_KG_M3["nh3"] * 1e6
        if self.state["barn_floor_water_cm"] > 0.0:
            # Standing water evaporates from the floor (about 25 g/m^2/h at barn conditions).
            source["barn"]["absolute_humidity_g_m3"] += 25.0 * self.parameters["hvac_zone_volume_m3"]["barn"] / 4.0 / 3600.0 * min(1.0, self.state["barn_floor_water_cm"])
        benign = self.state.get("benign_sources", {})
        source["barn"]["co2_ppm"] += float(benign.get("co2_ppm_s", 0.0)) * self._volume["barn"]
        source["barn"]["ammonia_ppm"] += float(benign.get("ammonia_ppm_s", 0.0)) * self._volume["barn"]

        # Fire products.
        self._products = None
        if self.fire is not None and self.fire.state != "out":
            fz = self.fire.zone
            products = self.fire.advance(h, old[fz]["o2_pct"])
            self._products = products
            heat[fz] += products["convective_w"]
            mass_heat[fz] += products["radiative_w"]
            source[fz]["o2_pct"] -= products["o2_kg_s"] / GAS_DENSITY_KG_M3["o2"] * 100.0
            source[fz]["co2_ppm"] += products["co2_kg_s"] / GAS_DENSITY_KG_M3["co2"] * 1e6
            source[fz]["co_ppm"] += products["co_kg_s"] / GAS_DENSITY_KG_M3["co"] * 1e6
            source[fz]["smoke_extinction_per_m"] += products["smoke_extinction_area_m2_s"]
            source[fz]["absolute_humidity_g_m3"] += products["water_kg_s"] * 1000.0

        # Solar gains: sol-air on roofs (mass node) and window transmission (air).
        solar = outside.get("solar_w_m2", 0.0)
        for name in HVAC_ZONES:
            z = zones[name]
            c_air, c_mass = self._capacity[name]
            ua = float(p["hvac_zone_ua_w_k"][name])
            window_ua = p["window_u_w_m2_k"] * p["geometry"][name]["window_area_m2"]
            heat[name] += p["window_shgc"] * p["geometry"][name]["window_area_m2"] * solar * 0.3
            sol_air = outside["temperature_c"] + p["roof_fraction"][name] * p["roof_absorptance"] * solar / p["outside_film_w_m2_k"]
            g_am = p["air_mass_coupling_w_k"][name]
            self._advance_zone(z, old[name], flows[name], heat[name], g_am, window_ua, outside["temperature_c"],
                               c_air, c_mass, mass_heat[name], ua, sol_air, source[name], self._volume[name], h)
        for device_id in INCUBATORS:
            z = zones[device_id]
            c_air, c_mass = self._capacity[device_id]
            self._advance_zone(z, old[device_id], flows[device_id], heat[device_id],
                               p["incubator_internal_coupling_w_k"], p["incubator_envelope_ua_w_k"],
                               room["temperature_c"], c_air, c_mass, mass_heat[device_id], 0.0, room["temperature_c"],
                               source[device_id], self._volume[device_id], h)
        self.state["airflows_m3_h"] = {"hvac_supply": q_s * 3600.0, "barn_natural": q_nat * 3600.0, **interzone}

    def _coil(self, mixed_t, mixed_ah, q_m3_s, total_w):
        """Split coil capacity into sensible and latent load (leaving air at 95 % RH).

        Solves sensible(t) + latent(t) = capacity for the leaving temperature t,
        bounded below by the coil's minimum leaving temperature.
        """
        capacity = AIR_RHO_CP * q_m3_s

        def load(t):
            latent = max(0.0, mixed_ah - relative_humidity_to_absolute_g_m3(t, 95.0)) * q_m3_s * 2_450.0
            return capacity * (mixed_t - t) + latent

        low, high = self.parameters["coil_minimum_supply_c"], mixed_t
        if low >= high or load(low) <= total_w:
            leaving = min(low, high)
        else:
            for _ in range(30):
                middle = 0.5 * (low + high)
                if load(middle) > total_w:
                    low = middle
                else:
                    high = middle
            leaving = 0.5 * (low + high)
        leaving_ah = min(mixed_ah, relative_humidity_to_absolute_g_m3(leaving, 95.0))
        return leaving, capacity * (mixed_t - leaving), leaving_ah

    @staticmethod
    def _advance_zone(z, old, flows, heat_w, g_air_mass, ua_air, t_ext_air, c_air, c_mass, mass_heat_w,
                      ua_mass, t_ext_mass, sources, volume, h):
        g_flow = 0.0
        weighted = 0.0
        for q, comp in flows:
            g = AIR_RHO_CP * q
            g_flow += g
            weighted += g * comp["temperature_c"]
        t_mass = old["mass_temperature_c"]
        g_total = g_flow + g_air_mass + ua_air
        if g_total > 0.0:
            equilibrium = (weighted + g_air_mass * t_mass + ua_air * t_ext_air + heat_w) / g_total
            z["temperature_c"] = _advance_linear(old["temperature_c"], equilibrium, g_total, c_air, h)
        else:
            z["temperature_c"] = old["temperature_c"] + heat_w * h / c_air
        g_mass = g_air_mass + ua_mass
        mass_eq = (g_air_mass * z["temperature_c"] + ua_mass * t_ext_mass + mass_heat_w) / max(g_mass, 1e-9)
        z["mass_temperature_c"] = _advance_linear(t_mass, mass_eq, g_mass, c_mass, h)
        for key in SPECIES:
            z[key] = _relax(old[key], [(q, comp[key]) for q, comp in flows], sources[key], volume, h)
        z["absolute_humidity_g_m3"] = max(0.0, min(z["absolute_humidity_g_m3"],
            relative_humidity_to_absolute_g_m3(z["temperature_c"], 100.0)))  # condensation
        z["o2_pct"] = max(0.0, min(25.0, z["o2_pct"]))
        for key in ("co2_ppm", "co_ppm", "ammonia_ppm", "smoke_extinction_per_m"):
            z[key] = max(0.0, z[key])

    def _finish_tick(self, devices, dt, plant, flock):
        apu = devices["animal-protection"].internal
        barn = self.state["zones"]["barn"]
        apu["feed_consumption_rate_kg_h"] = round(flock["feed_kg_s"] * 3600.0, 6)
        apu["water_consumption_rate_l_h"] = round(flock["water_l_s"] * 3600.0, 6)
        activity = self.flock.advance_welfare(self.state["animals"], dt,
            temperature_c=barn["temperature_c"], o2_pct=barn["o2_pct"], co2_ppm=barn["co2_ppm"],
            co_ppm=barn["co_ppm"], ammonia_ppm=barn["ammonia_ppm"],
            feed_available=plant["feed_available"], water_available=plant["water_available"],
            floor_water_cm=self.state["barn_floor_water_cm"])
        apu["animal_activity"] = {"activity_index_pct": activity}
        apu["inactivity_alert"] = activity < 10.0
        apu["aggregate_welfare_index_pct"] = self.state["animals"]["welfare_index_pct"]
        if self.fire is not None and self.fire.state == "out":
            self.fire = None
        self._update_fire_summary()

    def _update_fire_summary(self):
        if self.fire is None:
            self.state["fire"] = {"active": False}
        else:
            self.state["fire"] = {"active": True, **self.fire.public_summary()}

    def _fire_excess(self, zone, position, ceiling=False):
        """Ceiling-jet excess at a device position in ``zone`` (zero elsewhere)."""
        fire = self.fire
        if fire is None or fire.zone != zone or fire.hrr_kw <= 0.0:
            return {"temperature_c": 0.0, "smoke_extinction_per_m": 0.0, "co_ppm": 0.0}
        products = self._products or fire._products(0.0, fire.oxygen_factor(self.state["zones"][zone]["o2_pct"]))
        excess = fire.local_excess(self.parameters["geometry"][zone]["height_m"], fire.distance_to(position), products)
        if not ceiling:
            excess = {key: value * WALL_MOUNT_CEILING_JET_FACTOR for key, value in excess.items()}
        return excess

    # ---------------------------------------------------------------- sensors
    def sync_devices(self, devices, initialize=False, dt=0.0):
        """Publish sensor views of current truth through the measurement chain."""
        sensors = self.sensors
        if initialize:
            class _Exact:  # configured initial readings are published as given
                @staticmethod
                def read(*args, **kwargs):
                    return sensors.read(*args, exact=True, **kwargs)
            s = _Exact()
        else:
            s = sensors
        outside = self.state["outside"]
        room_name = self.parameters["incubator_room_zone"]
        room = outside if room_name == "outside" else self.state["zones"][room_name]
        outside_rh = absolute_humidity_to_relative_pct(outside["temperature_c"], outside["absolute_humidity_g_m3"])
        for device_id in INCUBATORS:
            device = devices[device_id]
            internal = device.internal
            truth = self.state["zones"][device_id]
            previous_t = list(internal["room_temperatures_c"])
            previous_h = list(internal["room_humidity_pct"])
            internal["room_temperatures_c"] = [None if off is None else s.read(f"{device_id}.temperature_{n + 1}",
                "temperature", truth["temperature_c"] + off, dt) for n, off in enumerate(self._sensor_offsets[device_id])]
            if not initialize:
                base_rh = absolute_humidity_to_relative_pct(truth["temperature_c"], truth["absolute_humidity_g_m3"])
                internal["room_humidity_pct"] = [None if off is None else s.read(f"{device_id}.humidity_{n + 1}",
                    "humidity", min(100.0, max(0.0, base_rh + off)), dt) for n, off in enumerate(self._humidity_offsets[device_id])]
            room_rh = absolute_humidity_to_relative_pct(room["temperature_c"], room["absolute_humidity_g_m3"])
            internal["ambient_temperature_c"] = s.read(f"{device_id}.ambient_temperature", "temperature", room["temperature_c"], dt)
            internal["ambient_humidity_pct"] = s.read(f"{device_id}.ambient_humidity", "humidity", room_rh, dt)
            circulation = self.parameters["incubator_circulation_m3_h"]
            nominal = [max(1.0, float(v)) for v in internal.get("fan_nominal_rpm", [])]
            actual = [max(0.0, float(v or 0.0)) for v in internal.get("fan_actual_rpm", internal.get("fan_rpm", []))]
            fan_ratio = sum(actual) / max(sum(nominal[:len(actual)]), 1.0) if actual else 0.0
            circulation_m3_s = (circulation * fan_ratio + 10.0) / 3600.0
            delivered = max(0.0, float(internal.get("heater_delivered_w", internal.get("heater_power_w", 0.0)))) * 0.92
            outlet_truth = truth["temperature_c"] + delivered / (AIR_RHO_CP * circulation_m3_s)
            internal["heater_outlet_truth_c"] = outlet_truth
            internal["heater_outlet_c"] = s.read(f"{device_id}.heater_outlet", "temperature", outlet_truth, dt)
            internal["measured_ac_voltage_v"] = s.read(f"{device_id}.voltage", "voltage", internal["ac_voltage_v"], dt)
            if "fan_actual_rpm" in internal and device.config.get("fan_feedback_enabled", True):
                internal["fan_rpm"] = [None if rpm is None else round(s.read(f"{device_id}.fan_{n + 1}", "rpm", rpm, dt))
                                       for n, rpm in enumerate(internal["fan_actual_rpm"])]
            if not initialize:
                device._previous_temperatures = previous_t
                device._previous_humidities = previous_h
            device._publish(dt)

        hvac = devices["hvac"]
        for name, zone in hvac.internal["zones"].items():
            truth = self.state["zones"][name]
            old_sensor_t = [v for v in zone.get("temperature_sensors_c", []) if v is not None]
            old_sensor_h = [v for v in zone.get("humidity_sensors_pct", []) if v is not None]
            old = zone["temperature_c"], zone["humidity_pct"]
            offsets = self._hvac_sensor_offsets[name]
            readings_t, readings_h = [], []
            base_x, base_y = DEFAULT_SENSOR_POSITIONS[name]
            for n, off in enumerate(offsets["temperature"]):
                excess = self._fire_excess(name, (base_x + 3.0 * n, base_y))
                local = truth["temperature_c"] + excess["temperature_c"]
                readings_t.append(None if off is None else s.read(f"hvac.{name}.temperature_{n + 1}", "temperature",
                                                                  local + off, dt, local_temperature_c=local))
            for n, off in enumerate(offsets["humidity"]):
                excess = self._fire_excess(name, (base_x + 3.0 * n, base_y))
                local = truth["temperature_c"] + excess["temperature_c"]
                rh = absolute_humidity_to_relative_pct(local, truth["absolute_humidity_g_m3"])
                readings_h.append(None if off is None else s.read(f"hvac.{name}.humidity_{n + 1}", "humidity",
                                                                  min(100.0, max(0.0, rh + off)), dt, local_temperature_c=local))
            zone["temperature_sensors_c"] = readings_t
            zone["humidity_sensors_pct"] = readings_h
            valid_t = [v for v in readings_t if v is not None]
            valid_h = [v for v in readings_h if v is not None]
            # The controller's zone value is the mean of its working sensors, not plant truth.
            zone["temperature_c"] = round(sum(valid_t) / len(valid_t), 4) if valid_t else None
            zone["humidity_pct"] = round(sum(valid_h) / len(valid_h), 4) if valid_h else None
            current_sample = (zone["temperature_c"], zone["humidity_pct"])
            if initialize:
                hvac._previous_zones[name] = current_sample
            else:
                hvac._previous_zones[name] = old if not (old_sensor_t or old_sensor_h) else (
                    sum(old_sensor_t) / len(old_sensor_t) if old_sensor_t else None,
                    sum(old_sensor_h) / len(old_sensor_h) if old_sensor_h else None)
        plant_hvac = self.state.get("hvac") or {}
        hi = hvac.internal
        supply_t = plant_hvac.get("supply_temperature_c")
        return_t = plant_hvac.get("return_temperature_c")
        if supply_t is None:  # no airflow: duct sensors sit in still air near the unit
            supply_t = return_t = self.state["zones"]["workspace"]["temperature_c"]
            supply_ah = return_ah = self.state["zones"]["workspace"]["absolute_humidity_g_m3"]
        else:
            supply_ah = plant_hvac["supply_absolute_humidity_g_m3"]
            return_ah = plant_hvac["return_absolute_humidity_g_m3"]
        hi["supply_c"] = s.read("hvac.supply_temperature", "temperature", supply_t, dt)
        hi["return_c"] = s.read("hvac.return_temperature", "temperature", return_t, dt)
        hi["supply_humidity_pct"] = s.read("hvac.supply_humidity", "humidity", absolute_humidity_to_relative_pct(supply_t, supply_ah), dt)
        hi["return_humidity_pct"] = s.read("hvac.return_humidity", "humidity", absolute_humidity_to_relative_pct(return_t, return_ah), dt)
        hi["coil_delivered_cooling_w"] = round(plant_hvac.get("coil_sensible_w", 0.0), 3)
        hi["condensate_rate_g_s"] = round(plant_hvac.get("condensate_g_s", 0.0), 6)
        if hi.get("compressor_on"):
            # An undercharged circuit runs a colder, lower-pressure evaporator.
            charge = float(hi.get("hidden_faults", {}).get("refrigerant_charge_pct", 100.0))
            evaporator_c = (supply_t if supply_t is not None else 12.0) - 6.0 - 0.3 * max(0.0, 100.0 - charge)
        else:
            evaporator_c = outside["temperature_c"]  # pressures equalize at ambient when stopped
        hi["refrigerant_temperature_c"] = s.read("hvac.refrigerant_temperature", "temperature", evaporator_c, dt)
        hi["refrigerant_pressure_kpa"] = s.read("hvac.refrigerant_pressure", "pressure", r410a_saturation_pressure_kpa(evaporator_c), dt)
        hi["outdoor"] = {"temperature_c": s.read("hvac.outdoor_temperature", "temperature", outside["temperature_c"], dt),
                         "humidity_pct": s.read("hvac.outdoor_humidity", "humidity", outside_rh, dt)}
        hvac._publish(dt)

        apu = devices["animal-protection"]
        barn = self.state["zones"]["barn"]
        excess = self._fire_excess("barn", SMOKE_DETECTOR_POSITION, ceiling=True)
        detector_temperature = barn["temperature_c"] + excess["temperature_c"]
        extinction = barn["smoke_extinction_per_m"] + excess["smoke_extinction_per_m"]
        obscuration = s.read("animal-protection.smoke_detector", "smoke", smoke_obscuration_pct_per_m(extinction),
                             dt, local_temperature_c=detector_temperature)
        quality = apu.internal["air_quality_by_zone"].setdefault("barn", {})
        for key, kind in (("co2_ppm", "co2"), ("o2_pct", "o2"), ("ammonia_ppm", "nh3"), ("co_ppm", "co")):
            reading = s.read(f"animal-protection.{key}", kind, barn[key], dt)
            if self._gas_sensors_available[key]:
                quality[key] = reading
            elif key == "co_ppm":
                quality[key] = None
        weather = apu.weather_provider
        if weather.get("configured"):
            weather["external_temperature_c"] = round(outside["temperature_c"], 1)
            weather["wind_speed_m_s"] = s.read("animal-protection.anemometer", "wind", outside["wind_speed_m_s"], dt)
            weather["wind_direction_deg"] = round(outside["wind_direction_deg"]) % 360
            weather["rainfall_rate_mm_h"] = s.read("animal-protection.rain_gauge", "rain", outside["rain_mm_h"], dt)
            weather["cumulative_rainfall_mm"] = round(outside["cumulative_rain_mm"], 1)
            weather["barometric_pressure_hpa"] = s.read("animal-protection.barometer", "barometric", outside["pressure_hpa"], dt)
            weather["floodwater_level_cm"] = s.read("animal-protection.flood_gauge", "level", outside["floodwater_level_cm"], dt)
            weather["intrusion_detected"] = self.state["barn_floor_water_cm"] > 0.2 or bool(getattr(self, "_water_entering_barn", False))
        apu.apply_detector_readings(obscuration)
        apu.internal["aggregate_welfare_index_pct"] = round(self.state["animals"]["welfare_index_pct"], 4)
        apu._publish(dt)
