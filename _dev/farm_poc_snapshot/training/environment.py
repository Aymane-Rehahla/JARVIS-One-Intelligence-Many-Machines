"""In-process, agent-facing wrapper around the authoritative Matrix engine."""
from __future__ import annotations

from copy import deepcopy

from ..matrix.engine import MatrixEngine
from ..matrix.scenario import OvervoltageScenario,SensorFaultScenario,ThermalDisturbanceScenario
from .contract import public_observation


class MatrixEnvironment:
    """Small Gym-like interface; this wrapper is not a Python security sandbox."""

    SCENARIOS = {"benign", "overvoltage", "sensor_bias", "sensor_drift",
                 "sensor_stuck", "sensor_dropout", "thermal_self_correcting",
                 "thermal_recurring"}

    def __init__(self, *, seed: int = 1, config: dict | None = None, max_ticks: int = 32):
        if max_ticks < 1:
            raise ValueError("max_ticks must be positive")
        self._seed = int(seed)
        self._config = deepcopy(config or {})
        self.max_ticks = int(max_ticks)
        self._engine = MatrixEngine(seed=self._seed, config=self._config)
        self._done = False

    def reset(self, scenario_id: str = "benign", seed: int | None = None,
              scenario_config: dict | None = None) -> dict:
        if scenario_id not in self.SCENARIOS:
            raise ValueError(f"unsupported scenario_id: {scenario_id}")
        if seed is not None:
            self._seed = int(seed)
            self._engine.seed = self._seed
            # A supplied seed identifies a reproducible episode independent of
            # which reusable worker happened to receive it.
            self._engine._episode_index = -1
        self._engine.reset()
        self._engine.device_computer.publish()
        for computer in self._engine.device_computers.values():
            computer.publish()
        if scenario_id == "overvoltage":
            config = dict(scenario_config or {})
            allowed = {"inject_tick", "voltage_v", "peer_modes", "action", "action_fails",
                       "action_unverifiable", "action_arguments", "recovery_tick", "recovery_voltage_v"}
            unknown = set(config) - allowed
            if unknown:
                raise ValueError(f"unsupported overvoltage scenario parameters: {sorted(unknown)}")
            self._engine.start_scenario(OvervoltageScenario(**config))
        elif scenario_id.startswith("sensor_"):
            mode = {"sensor_bias": "bias", "sensor_drift": "drift",
                    "sensor_stuck": "stuck_at", "sensor_dropout": "dropout"}[scenario_id]
            config = dict(scenario_config or {})
            allowed = {"inject_tick", "end_tick", "sensor_id", "offset", "drift_per_tick", "stuck_value"}
            unknown = set(config) - allowed
            if unknown:
                raise ValueError(f"unsupported sensor fault parameters: {sorted(unknown)}")
            defaults = {"inject_tick": 2, "end_tick": 8, "sensor_id": "room_temperature_1"}
            defaults.update(config)
            self._engine.start_sensor_fault(SensorFaultScenario(mode=mode, **defaults))
        elif scenario_id.startswith("thermal_"):
            config = dict(scenario_config or {})
            allowed = {"inject_tick", "heat_load_w", "duration_ticks", "cycles",
                       "cycle_interval_ticks", "peer_modes"}
            unknown = set(config) - allowed
            if unknown:
                raise ValueError(f"unsupported thermal scenario parameters: {sorted(unknown)}")
            defaults = {"inject_tick": 2, "heat_load_w": 300.0, "duration_ticks": 3,
                        "cycles": 1 if scenario_id == "thermal_self_correcting" else 2,
                        "cycle_interval_ticks": 28}
            defaults.update(config)
            if scenario_id == "thermal_self_correcting" and defaults["cycles"] != 1:
                raise ValueError("thermal_self_correcting requires exactly one heat-load cycle")
            if scenario_id == "thermal_recurring" and defaults["cycles"] < 2:
                raise ValueError("thermal_recurring requires at least two heat-load cycles")
            self._engine.start_thermal_scenario(ThermalDisturbanceScenario(**defaults))
        self._done = False
        return public_observation(self._engine)

    def step(self, action: dict | None = None, *, expected_tick: int | None = None):
        """Step only the engine's next tick; callers cannot reorder simulation time.

        `decision`/`diagnosis` are supervision or agent outputs. Only entries in
        the explicit `commands` list invoke the existing Matrix command path.
        """
        if self._done:
            raise RuntimeError("episode is complete; call reset before stepping again")
        engine = self._engine
        if expected_tick is not None and expected_tick != engine.clock.tick:
            raise ValueError(f"expected_tick must equal the Matrix clock ({engine.clock.tick})")
        action = dict(action or {})
        unknown = set(action) - {"decision", "diagnosis", "commands"}
        if unknown:
            raise ValueError(f"unsupported action fields: {sorted(unknown)}")
        commands = action.get("commands", [])
        if not isinstance(commands, list):
            raise ValueError("commands must be a list")
        for command in commands:
            if not isinstance(command, dict) or set(command) - {"target_id", "command_type", "arguments"}:
                raise ValueError("each command requires target_id and command_type, with optional arguments")
            if not {"target_id", "command_type"} <= set(command):
                raise ValueError("each command requires target_id and command_type")
            engine.submit_command(command["target_id"], command["command_type"],
                                  command.get("arguments", {}), source="jarvis-real")
        engine.step(return_snapshot=False)
        observation = public_observation(engine)
        self._done = engine.clock.tick >= self.max_ticks
        return observation, self._done, {"tick": engine.clock.tick - 1}

    def run_steps(self, count: int):
        """Snapshot-suppressed batch stepping with the same sequential clock."""
        if count < 0:
            raise ValueError("count must be nonnegative")
        output = []
        for _ in range(count):
            if self._done:
                break
            output.append(self.step()[0])
        return output

    def _private_episode_record(self) -> dict:
        """Trusted recorder-only access; never returned by reset/step."""
        return {"hidden_truth": deepcopy(self._engine.hidden_truth),
                "evaluation": deepcopy(self._engine.results[-1]) if self._engine.results else None,
                "commands": deepcopy(self._engine.world.commands),
                "scenario": type(self._engine.scenario).__name__ if self._engine.scenario else None}
