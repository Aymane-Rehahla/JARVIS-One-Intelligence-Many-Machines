from .base_controller import BaseController


class AnimalProtectionController(BaseController):
    """Barn climate and hazard logic that runs without Jarvis.

    In ``automatic`` controller mode, using only its own sensors, the HVAC
    barn readings it receives, and the authorised weather-warning feed:
    - Tornado warning (feed only, never local wind alone): deploy storm
      shutters, close windows and gates, vents to a minimum. A manual deploy
      of the shutters or the flood barrier (owner or Jarvis) holds until it
      is retracted by hand.
    - Severe weather warning: close windows and gates, vents to a minimum.
    - Flood warning or rising floodwater: deploy the flood barrier and close
      the low vents. Flood mode latches: it ends only after the water has
      stayed below 2 cm (no warning, no intrusion) for 30 minutes, so the
      barrier is not cycled with every wobble of the gauge.
    - Smoke detected: close windows and vents to limit air feeding a fire.
    - Poor air (CO2, NH3, CO): open vents and windows to purge.
    - Barn too warm: open windows when outside is cooler, otherwise close
      them and ask HVAC for cooling.
    - Barn too cold, or outside much hotter/colder than the barn: close down
      to minimum ventilation.
    When a protective closure conflicts with air quality or heat, the conflict
    is recorded and HVAC is asked for emergency ventilation instead.
    Requests to HVAC go through the normal command path and are rate limited.
    """

    REQUEST_COOLDOWN_S = 600.0
    FLOOD_RELEASE_S = 1800.0

    def __init__(self, device):
        super().__init__(device)
        self.base_windows = float(device.internal["window_command_pct"])
        self.base_vents = float(device.internal["vent_command_pct"])
        self._last_request = {}
        self._flood_latched = False
        self._flood_clear_since = None

    def _flood_mode(self, flood_now, level, now):
        if flood_now:
            self._flood_latched, self._flood_clear_since = True, None
        elif self._flood_latched:
            if level >= 2.0:
                self._flood_clear_since = None
            elif self._flood_clear_since is None:
                self._flood_clear_since = now
            elif now - self._flood_clear_since >= self.FLOOD_RELEASE_S:
                self._flood_latched, self._flood_clear_since = False, None
        return self._flood_latched

    def step(self, engine):
        i = self.device.internal
        if i["flood"] or i["weather_warning"]:
            i["emergency_ready"] = True
        if i.get("controller_mode", "automatic") == "automatic" and self.device.online:
            self._climate(engine)
        self.device._publish()

    def _climate(self, engine):
        i = self.device.internal
        w = self.device.weather_provider
        cfg = self.device.config.get("climate", {})
        comfort_high = float(cfg.get("comfort_high_c", 26.0))
        comfort_low = float(cfg.get("comfort_low_c", 16.0))
        barn = i["air_quality_by_zone"].get("barn", {})
        temperature = i["environment"].get("temperature_c")
        outside = w.get("external_temperature_c") if w.get("configured") else None
        windows, vents = self.base_windows, self.base_vents
        tornado = bool(w.get("tornado_warning"))
        storm = tornado or bool(w.get("severe_weather_warning"))
        flood_level = float(w.get("floodwater_level_cm") or 0.0) if w.get("configured") else 0.0
        flood = self._flood_mode(bool(w.get("flood_warning")) or flood_level >= 5.0 or bool(w.get("intrusion_detected")),
                                 flood_level, engine.clock.time)
        poor_air = ((barn.get("co2_ppm") or 0) >= 2500 or (barn.get("ammonia_ppm") or 0) >= 20
                    or (barn.get("co_ppm") or 0) >= 35)
        hot = temperature is not None and temperature > comfort_high
        required = []
        if i.get("smoke_detected"):
            windows, vents = 0.0, 0.0
            required.append("close_openings_for_smoke")
        elif storm:
            windows, vents = 0.0, 10.0
            required.append("close_windows_for_storm")
        elif poor_air:
            windows, vents = max(windows, 60.0), 100.0
            required.append("purge_barn_air")
        elif hot:
            if outside is not None and outside < temperature - 1.0:
                windows = min(100.0, self.base_windows + 25.0 * (temperature - comfort_high))
                vents = 100.0
            else:
                windows, vents = 5.0, max(10.0, self.base_vents * 0.5)
                self._request(engine, "hvac", "set_cooling", {"on": True})
        elif temperature is not None and temperature < comfort_low:
            windows, vents = 0.0, 10.0
        elif temperature is not None and outside is not None and outside > temperature + 1.0:
            windows, vents = 5.0, max(15.0, self.base_vents * 0.5)  # keep hot air out
        elif temperature is not None and outside is not None and outside < temperature - 6.0:
            windows, vents = 5.0, 15.0  # keep heat in; minimum air exchange only
        if flood:
            vents = 0.0
            required.append("close_low_vents_for_flood")
        i["window_command_pct"] = round(windows, 3)
        i["vent_command_pct"] = round(vents, 3)
        # A manual deploy (owner or Jarvis) holds until someone retracts it by hand.
        i["emergency_shutter_command"] = "deployed" if tornado or i.get("emergency_shutter_manual") else "retracted"
        if tornado: required.append("deploy_storm_shutters")
        i["flood_barrier_command"] = "deployed" if flood or i.get("flood_barrier_manual") else "retracted"
        if flood: required.append("deploy_flood_barrier")
        for gate in i["door_gate_commands"]:
            if storm or flood:
                i["door_gate_commands"][gate] = "closed"
        if storm or flood:
            required.append("close_gates")
        conflicts = []
        if (storm or flood or i.get("smoke_detected")) and (poor_air or hot):
            conflicts.append({"conflict": "protective_closure_vs_air_quality" if poor_air else "protective_closure_vs_heat",
                              "protective_reason": "smoke" if i.get("smoke_detected") else "storm" if storm else "flood",
                              "detected_at_s": i["uptime_s"]})
            # Mechanical ventilation substitutes for closed openings, unless there is smoke to spread.
            if not i.get("smoke_detected"):
                self._request(engine, "hvac", "emergency_ventilation", {"on": True})
        i["safety_conflicts"] = conflicts
        i["required_protective_actions"] = required
        i["emergency_mode"] = bool(tornado or flood or i.get("smoke_detected"))

    def _request(self, engine, target, kind, arguments):
        key = (target, kind, tuple(sorted(arguments.items())))
        now = engine.clock.time
        if now - self._last_request.get(key, -1e18) < self.REQUEST_COOLDOWN_S:
            return
        hvac = engine.world.devices["hvac"].internal
        if kind == "set_cooling" and bool(hvac.get("cooling_command")) == bool(arguments.get("on")):
            return
        if kind == "emergency_ventilation" and (hvac.get("configured_mode") == "emergency_ventilation") == bool(arguments.get("on")):
            return
        self._last_request[key] = now
        self.request_external_action(engine, target, kind, arguments)

    def command(self, kind, arguments):
        i = self.device.internal
        if kind == "set_vents":
            position = max(0, min(100, int(arguments["position_pct"])))
            i["vent_command_pct"] = position
            self.base_vents = float(position)
            return {"vent_command_pct": position}
        if kind == "set_windows":
            position = max(0, min(100, int(arguments["position_pct"])))
            i["window_command_pct"] = position
            self.base_windows = float(position)
            return {"window_command_pct": position}
        if kind == "set_gate":
            name, position = arguments.get("gate"), arguments.get("position")
            if name not in i["door_gate_commands"] or position not in ("open", "closed"):
                raise ValueError("unknown gate or position")
            i["door_gate_commands"][name] = position
            return {"door_gate_commands": dict(i["door_gate_commands"])}
        if kind == "deploy_flood_barrier":
            if not self.device.capabilities["flood_barrier"]: raise ValueError("flood barrier is not installed")
            i["flood_barrier_manual"] = bool(arguments.get("deploy", True))
            i["flood_barrier_command"] = "deployed" if i["flood_barrier_manual"] else "retracted"
            return {"flood_barrier_command": i["flood_barrier_command"]}
        if kind == "deploy_shutters":
            if not self.device.capabilities["emergency_shutters"]: raise ValueError("storm shutters are not installed")
            i["emergency_shutter_manual"] = bool(arguments.get("deploy", True))
            i["emergency_shutter_command"] = "deployed" if i["emergency_shutter_manual"] else "retracted"
            return {"emergency_shutter_command": i["emergency_shutter_command"]}
        if kind == "activate_alarm":
            i["alarm_manual_activation"] = bool(arguments.get("on", True))
            return {"alarm_manual_activation": i["alarm_manual_activation"]}
        raise ValueError(f"unsupported animal protection command: {kind}")

    SUPPORTED_REQUESTS = {
        "animal-protection": {"set_vents", "set_windows", "set_gate", "deploy_flood_barrier", "deploy_shutters", "activate_alarm"},
        "hvac": {"set_cooling", "set_fan", "emergency_ventilation", "set_mode"},
    }

    def request_external_action(self, engine, target_id, command_type, arguments):
        if command_type not in self.SUPPORTED_REQUESTS.get(target_id, set()):
            raise ValueError(f"unsupported external request {target_id}:{command_type}")
        return engine.submit_command(target_id, command_type, arguments, source=self.device.device_id)
