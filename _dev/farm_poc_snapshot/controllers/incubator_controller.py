from statistics import median

from .base_controller import BaseController


class IncubatorController(BaseController):
    """Local incubator controller: protection first, then climate control.

    - Overvoltage relay on the supply (its own measuring element); latched, with an automatic reset
      after the supply has been normal for ``overvoltage_auto_reset_s``.
    - Independent high-limit thermostat (auto-resetting) on room and heater
      outlet temperatures.
    - PI temperature control with time-proportioned heater output, using the
      median of working room sensors so one bad probe cannot drive the heater.
    - Humidifier on/off control, raising the setpoint for lockdown.
    - Fan/damper boost when the cabinet runs warm; turning stops at lockdown.
    """

    KP_PCT_PER_K = 150.0
    KI_PCT_PER_K_S = 0.25
    GAIN_SCALE_EXPONENT = 2.0

    def __init__(self, device):
        super().__init__(device)
        self.integral_pct = 60.0
        self.normal_supply_s = 0.0
        self.base_fan_pct = float(device.internal["fan_command_pct"])
        self.base_damper_pct = float(device.internal["damper_command_pct"])
        self.cooling_assist = False

    def step(self, engine):
        device = self.device
        i = device.internal
        cfg = device.config
        dt = float(engine.clock.step_seconds)
        if not device.online:
            device._publish()  # browned-out controller: no protection logic, no control
            return
        threshold = float(cfg.get("overvoltage_threshold_v", 260.0))
        # The protective relay has its own fast measuring element on the supply.
        voltage = float(i["ac_voltage_v"])
        if voltage > threshold:
            was_cutoff = i["cutoff"]; heater_was_on = i["heater_on"]
            i["power_status"] = "overvoltage"; i["cutoff"] = True
            device.apply_heater_output(0.0, dt)
            if "OVERVOLTAGE" not in i["faults"]:
                i["faults"].append("OVERVOLTAGE")
                engine.emit("fault", device.device_id, code="OVERVOLTAGE", voltage_v=voltage, threshold_v=threshold)
            if "OVERVOLTAGE_PROTECTION" not in i["alarms"]: i["alarms"].append("OVERVOLTAGE_PROTECTION")
            if not was_cutoff:
                engine.emit("protection_activated", device.device_id, reason="overvoltage", voltage_v=voltage,
                            threshold_v=threshold, heater_was_on=heater_was_on, heater_on=i["heater_on"],
                            heater_power_w=i["heater_power_w"], cutoff=i["cutoff"])
            self.normal_supply_s = 0.0
        elif voltage < 50:
            i["power_status"] = "outage"
            self.normal_supply_s = 0.0
        elif voltage < 180:
            i["power_status"] = "undervoltage"
            self.normal_supply_s = 0.0
        else:
            i["power_status"] = "normal"
            reset_after = cfg.get("overvoltage_auto_reset_s", 120.0)
            if i["cutoff"] and reset_after is not None and voltage <= threshold - 5.0:
                self.normal_supply_s += dt
                if self.normal_supply_s >= float(reset_after):
                    self._clear_overvoltage()
                    engine.emit("protection_reset", device.device_id, reason="supply_normal_auto_reset",
                                voltage_v=voltage, threshold_v=threshold, cutoff=False, heater_on=i["heater_on"])
            else:
                self.normal_supply_s = 0.0

        readings = [float(v) for v in i["room_temperatures_c"] if v is not None]
        outlet = i.get("heater_outlet_c")
        self._high_limit(engine, readings, outlet, dt)

        if (cfg.get("thermostat_enabled", True) and i.get("heater_control_mode", "automatic") == "automatic"
                and not i["cutoff"] and not i.get("high_limit_tripped") and not i.get("emergency_stop_active")):
            if readings:
                # Tuned for a 1 s loop; a coarser simulation step holds each output
                # longer, so gains shrink to keep the sampled loop stable.
                scale = min(1.0, 10.0 / max(dt, 1e-9)) ** self.GAIN_SCALE_EXPONENT
                error = float(i["temperature_setpoint_c"]) - median(readings)
                proportional = self.KP_PCT_PER_K * scale * error
                candidate = self.integral_pct + self.KI_PCT_PER_K_S * scale * error * dt
                # Conditional integration prevents wind-up against the 0-100 % limits.
                if 0.0 <= proportional + candidate <= 100.0 or (proportional + candidate > 100.0 and error < 0) \
                        or (proportional + candidate < 0.0 and error > 0):
                    self.integral_pct = max(0.0, min(100.0, candidate))
                device.apply_heater_output(max(0.0, min(100.0, proportional + self.integral_pct)), dt)
        elif i["cutoff"] or i.get("high_limit_tripped") or i.get("emergency_stop_active"):
            device.apply_heater_output(0.0, dt)

        lockdown = int(i.get("days_remaining", 99)) <= 3
        if cfg.get("humidity_control_enabled", True) and i.get("heater_control_mode", "automatic") == "automatic":
            setpoint = float(cfg.get("lockdown_humidity_setpoint_pct", 70.0)) if lockdown else float(i["humidity_setpoint_pct"])
            humidities = [float(v) for v in i["room_humidity_pct"] if v is not None]
            if humidities:
                rh = median(humidities)
                if rh < setpoint - 2.0: i["humidifier_on"] = True
                elif rh > setpoint + 1.0: i["humidifier_on"] = False
        if lockdown and cfg.get("stop_turning_at_lockdown", True):
            i["turning"] = False

        if cfg.get("fan_control_enabled", True) and readings:
            warm = median(readings) - float(i["temperature_setpoint_c"])
            if warm > 0.6 and not self.cooling_assist:
                self.cooling_assist = True
                self.base_fan_pct, self.base_damper_pct = float(i["fan_command_pct"]), float(i["damper_command_pct"])
                i["fan_command_pct"], i["damper_command_pct"] = 100.0, 100.0
            elif warm < 0.2 and self.cooling_assist:
                self.cooling_assist = False
                i["fan_command_pct"], i["damper_command_pct"] = self.base_fan_pct, self.base_damper_pct
        device._publish()

    def _high_limit(self, engine, readings, outlet, dt):
        device = self.device
        i = device.internal
        limit = float(device.config.get("high_limit_c", 39.5))
        outlet_limit = float(device.config.get("heater_outlet_limit_c", 65.0))
        hot = any(v > limit for v in readings) or (outlet is not None and float(outlet) > outlet_limit)
        if i.get("hidden_faults", {}).get("high_limit_failed"):
            hot = False  # welded or drifted safety thermostat never opens
        if hot and not i.get("high_limit_tripped"):
            i["high_limit_tripped"] = True
            heater_was_on = i["heater_on"]
            self.heater_on_before_trip = heater_was_on
            device.apply_heater_output(0.0, dt)
            if "OVERTEMPERATURE" not in i["faults"]:
                i["faults"].append("OVERTEMPERATURE")
                engine.emit("fault", device.device_id, code="OVERTEMPERATURE")
            if "OVERTEMPERATURE_PROTECTION" not in i["alarms"]: i["alarms"].append("OVERTEMPERATURE_PROTECTION")
            engine.emit("protection_activated", device.device_id, reason="overtemperature",
                        heater_was_on=heater_was_on, heater_on=False, heater_power_w=0.0, cutoff=True)
        elif i.get("high_limit_tripped") and readings and max(readings) < limit - 1.0 and (outlet is None or float(outlet) < outlet_limit - 15.0):
            i["high_limit_tripped"] = False
            # The safety thermostat is in series with the heater; when it closes
            # again a manually set or free-running heater resumes.
            if not device.config.get("thermostat_enabled", True) or i.get("heater_control_mode") != "automatic":
                i["heater_on"] = getattr(self, "heater_on_before_trip", i["heater_on"])
            i["faults"] = [f for f in i["faults"] if f != "OVERTEMPERATURE"]
            i["alarms"] = [a for a in i["alarms"] if a != "OVERTEMPERATURE_PROTECTION"]
            engine.emit("protection_reset", device.device_id, reason="overtemperature_cleared",
                        cutoff=bool(i["cutoff"]), heater_on=i["heater_on"])

    def _clear_overvoltage(self):
        i = self.device.internal
        i["cutoff"] = False; i["heater_control_mode"] = "automatic"
        i["faults"] = [f for f in i["faults"] if f != "OVERVOLTAGE"]
        i["alarms"] = [a for a in i["alarms"] if a != "OVERVOLTAGE_PROTECTION"]
        self.normal_supply_s = 0.0

    def command(self, kind, arguments):
        i = self.device.internal
        if kind == "reset_protection":
            if float(i["ac_voltage_v"]) > float(self.device.config.get("overvoltage_threshold_v", 260.0)):
                raise ValueError("supply still exceeds protection threshold")
            self._clear_overvoltage(); return {"overtemperature_cutoff": bool(i.get("high_limit_tripped"))}
        if kind == "emergency_stop":
            # Opens the heater contactor; fans, sensors, and the controller stay powered.
            i["emergency_stop_active"] = bool(arguments.get("on", True))
            return {"emergency_stop_active": i["emergency_stop_active"]}
        if kind == "set_heater":
            if (i["cutoff"] or i.get("high_limit_tripped")) and arguments.get("on", False): raise ValueError("protective cutoff is active")
            i["heater_control_mode"] = "manual"; i["heater_on"] = bool(arguments["on"])
            i["heater_output_pct"] = 100.0 if i["heater_on"] else 0.0
            return {"heater_on": i["heater_on"]}
        raise ValueError(f"unsupported incubator command: {kind}")
