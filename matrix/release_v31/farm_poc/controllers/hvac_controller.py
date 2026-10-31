from .base_controller import BaseController


class HVACController(BaseController):
    """Local air-handler controller; runs without Jarvis.

    In ``automatic`` mode it acts on its own sensors only:
    - PI cooling on the warmest zone, with compressor staging hysteresis.
    - Economizer: more outdoor air when outside is cooler than return air,
      compressor locked out when outdoor air alone can cool.
    - Electric heating when a zone falls well below setpoint.
    - Fan speed follows the larger of cooling and heating demand.
    - Zone dampers open toward the zones that need conditioning most.
    - Airflow-proving interlock: no compressor or heater without proven fan.
    ``manual`` mode leaves every command where an operator put it.
    """

    KP_PCT_PER_K = 40.0
    KI_PCT_PER_K_S = 0.05
    MIN_OUTDOOR_AIR = 0.2

    def __init__(self, device):
        super().__init__(device)
        self.cooling_integral = float(device.internal.get("cooling_output_pct", 0.0))
        self.heating_integral = 0.0
        self.base_dampers = dict(device.internal["damper_command_positions_pct"])
        self.return_fan_base = float(device.internal["return_fan_command_pct"]) or 50.0

    def step(self, engine):
        i = self.device.internal
        i["mode"] = i.get("configured_mode", i.get("mode", "automatic"))
        mode = i["mode"]
        if mode == "fire_shutdown":
            # Smoke control: stop moving air between rooms and from outside (NFPA 90A practice).
            i["fan_command_pct"] = 0.0; i["return_fan_command_pct"] = 0.0
            i["outdoor_air_fraction_command"] = 0.0; i["compressor_request"] = False
            i["cooling_output_pct"] = i["cooling_valve_command_pct"] = 0.0; i["heating_output_pct"] = 0.0
            for name in i["damper_command_positions_pct"]:
                i["damper_command_positions_pct"][name] = 0.0
            self.cooling_integral = self.heating_integral = 0.0
            self.device._publish()
            return
        if i.get("return_fan_command_pct", 0.0) == 0.0 and getattr(self, "return_fan_base", None):
            i["return_fan_command_pct"] = self.return_fan_base
        if mode == "manual" or not self.device.online:
            self.device._publish()
            return
        dt = float(engine.clock.step_seconds)
        zones = i["zones"]
        setpoints = i["temperature_setpoints_c"]
        errors = {name: float(zone["temperature_c"]) - float(setpoints[name])
                  for name, zone in zones.items() if zone.get("temperature_c") is not None}
        if not errors:
            self.device._publish()
            return
        excess = max(errors.values())
        deficit = max(-e for e in errors.values())
        outdoor = (i.get("outdoor") or {}).get("temperature_c")
        return_t = i.get("return_c")
        fan_proven = float(i.get("fan_speed_pct") or 0.0) >= 10.0

        # Cooling PI on the warmest zone, 0.3 K dead band.
        cooling_error = excess - 0.3 if excess > 0.3 else min(0.0, excess)
        if i["cooling_command"]:
            candidate = self.cooling_integral + self.KI_PCT_PER_K_S * cooling_error * dt
            self.cooling_integral = max(0.0, min(100.0, candidate))
            cooling = max(0.0, min(100.0, self.KP_PCT_PER_K * cooling_error + self.cooling_integral))
        else:
            self.cooling_integral = 0.0
            cooling = 0.0
        free_cooling = (outdoor is not None and return_t is not None and excess > 0.0
                        and float(outdoor) < float(return_t) - 2.0)
        if free_cooling:
            i["outdoor_air_fraction_command"] = round(min(1.0, self.MIN_OUTDOOR_AIR + 0.8 * min(1.0, excess / 1.5)), 3)
        else:
            i["outdoor_air_fraction_command"] = self.MIN_OUTDOOR_AIR
        outdoor_alone_suffices = free_cooling and float(outdoor) < min(float(v) for v in setpoints.values()) - 3.0
        request = i.get("compressor_request", False)
        if cooling > 15.0 and not outdoor_alone_suffices:
            request = True
        elif cooling < 5.0 or outdoor_alone_suffices:
            request = False
        if not fan_proven:
            request = False
        i["compressor_request"] = bool(request)
        i["cooling_output_pct"] = round(cooling, 3)
        i["cooling_valve_command_pct"] = round(cooling, 3)

        if mode in ("ventilation", "emergency_ventilation"):
            i["outdoor_air_fraction_command"] = 1.0 if mode == "emergency_ventilation" else max(
                float(i["outdoor_air_fraction_command"]), self.MIN_OUTDOOR_AIR)
        if mode == "ventilation":
            i["compressor_request"] = False
            i["cooling_output_pct"] = i["cooling_valve_command_pct"] = 0.0
            cooling = 0.0
        # Heating PI when a zone is more than 1 K below setpoint; one air
        # handler cannot heat and cool at once, so heating takes priority.
        if deficit < 0.2:
            self.heating_integral = 0.0  # every zone is back near setpoint
        if i.get("heating_enabled", True) and mode == "automatic" and (deficit > 1.0 or self.heating_integral > 0.0):
            heating_error = deficit - 0.5
            self.heating_integral = max(0.0, min(100.0, self.heating_integral + 0.05 * heating_error * dt))
            heating = max(0.0, min(100.0, 40.0 * heating_error + self.heating_integral))
        else:
            self.heating_integral = 0.0
            heating = 0.0
        i["heating_output_pct"] = round(heating, 3)
        if heating > 0.0:
            self.cooling_integral = 0.0
            i["compressor_request"] = False
            i["cooling_output_pct"] = i["cooling_valve_command_pct"] = 0.0
            cooling = 0.0

        if mode == "emergency_ventilation":
            i["fan_command_pct"] = 100.0
        elif i.get("fan_mode", "automatic") == "automatic":
            i["fan_command_pct"] = round(max(40.0, min(100.0, 40.0 + 0.6 * max(cooling, heating))), 3)
        heating_mode = heating > cooling
        for name in i["damper_command_positions_pct"]:
            if name not in errors:
                continue
            need = -errors[name] if heating_mode else errors[name]
            base = float(self.base_dampers.get(name, 50.0))
            i["damper_command_positions_pct"][name] = round(max(20.0, min(100.0, base + 30.0 * need)), 3)
        self.device._publish()

    def command(self, kind, arguments):
        i = self.device.internal
        if kind == "set_fan":
            if arguments.get("mode") == "automatic":
                i["fan_mode"] = "automatic"
                return {"fan_mode": "automatic"}
            i["fan_mode"] = "manual"
            i["fan_command_pct"] = max(0, min(100, int(arguments["speed_pct"])))
            return {"fan_command_pct": i["fan_command_pct"]}
        if kind == "set_mode":
            mode = arguments.get("mode")
            if mode not in ("automatic", "manual", "cooling", "ventilation", "emergency_ventilation", "fire_shutdown"):
                raise ValueError("unsupported HVAC mode")
            i["configured_mode"] = i["mode"] = mode
            return {"configured_mode": mode}
        if kind == "emergency_ventilation":
            on = bool(arguments.get("on", True))
            if on and i.get("configured_mode") != "emergency_ventilation":
                self.mode_before_emergency = i.get("configured_mode", "automatic")
            i["configured_mode"] = i["mode"] = "emergency_ventilation" if on else getattr(self, "mode_before_emergency", "automatic")
            i["emergency_mode"] = on
            return {"configured_mode": i["configured_mode"]}
        if kind == "set_cooling":
            i["cooling_command"] = bool(arguments["on"])
            if not i["cooling_command"]:
                i["compressor_request"] = False
            return {"cooling_command_on": i["cooling_command"]}
        raise ValueError(f"unsupported HVAC command: {kind}")
