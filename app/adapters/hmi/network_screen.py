from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.adapters.binary import (
    TcpFrameClient,
    read_u8,
    read_u16_le,
    read_u64_le,
    require_frame_size,
    write_display_header,
    write_float_array_le,
    write_float_le,
    write_u8,
    write_u8_array,
    write_u16_array_le,
    write_u16_le,
    write_u32_array_le,
)


@dataclass(frozen=True)
class NetworkScreenState:
    timestamp_ms: int | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    second: int | None = None
    curr_station_id: int = 0
    next_station_id: int = 0
    end_station_id: int = 0
    power_state: int = 0
    speed_mps: float = 0.0
    acceleration_mps2: float = 0.0
    power_pull: int = 0
    net_pressure: int = 1500
    speed_limit: int = 0
    level_pos: int = 0
    run_mode: int = 0
    master_voltage: int = 0
    run_dir: int = 0
    driver_room_state: int = 0
    door_states: list[int] = field(default_factory=list)
    stop_pos_states: list[int] = field(default_factory=list)
    fire_empty_run_states: list[int] = field(default_factory=list)
    warm_empty_state1: list[int] = field(default_factory=list)
    warm_empty_state2: list[int] = field(default_factory=list)
    pull_switch_states: list[int] = field(default_factory=list)
    charge_states: list[int] = field(default_factory=list)
    assist_high_switch_states: list[int] = field(default_factory=list)
    breaker_master_states: list[int] = field(default_factory=list)
    elect_stop_forces: list[int] = field(default_factory=list)
    wind_pressures: list[int] = field(default_factory=list)
    brake_pressures: list[int] = field(default_factory=list)
    usage_rates: list[int] = field(default_factory=list)
    line_net_currents: list[int] = field(default_factory=list)
    temperatures: list[float] = field(default_factory=list)
    pull_stream_states: list[int] = field(default_factory=list)
    emergency_stop_states: list[int] = field(default_factory=list)
    side_info_states: list[int] = field(default_factory=list)
    braker_states: list[int] = field(default_factory=list)
    line_and_elect_stop_states: list[int] = field(default_factory=list)
    line_voltages: list[int] = field(default_factory=list)
    stop_states: list[int] = field(default_factory=list)
    air_stop_forces: list[int] = field(default_factory=list)
    empty_pressures1: list[int] = field(default_factory=list)
    empty_pressures2: list[int] = field(default_factory=list)
    b05_b19_states: list[int] = field(default_factory=list)
    kma_elect_power_states: list[int] = field(default_factory=list)
    inverter_input_voltages: list[int] = field(default_factory=list)
    inverter_output_voltages: list[int] = field(default_factory=list)
    charger_output_voltages: list[int] = field(default_factory=list)
    inverter_input_currents: list[int] = field(default_factory=list)
    inverter_output_currents: list[int] = field(default_factory=list)
    charger_output_currents: list[int] = field(default_factory=list)
    tc1_km1: int = 0
    tc1_km3: int = 0
    tc1_km5: int = 0
    tc2_km1: int = 0
    tc2_km3: int = 0
    tc2_km5: int = 0
    tc1_battery_remain: int = 0
    tc1_battery_voltage: int = 0
    tc1_battery_charge_current: int = 0
    tc1_battery_output_current: int = 0
    tc1_battery_temp: int = 0
    tc1_high_voltage: int = 0
    tc1_low_voltage: int = 0
    tc1_high_pos: int = 0
    tc1_low_pos: int = 0
    tc1_high_temp: int = 0
    tc2_high_temp: int = 0
    tc1_temp_pos: int = 0
    tc2_temp_pos: int = 0
    tc2_battery_remain: int = 0
    tc2_battery_voltage: int = 0
    tc2_battery_charge_current: int = 0
    tc2_battery_output_current: int = 0
    tc2_battery_temp: int = 0
    tc2_high_voltage: int = 0
    tc2_low_voltage: int = 0
    tc2_high_pos: int = 0
    tc2_low_pos: int = 0
    smoke_temp_states: list[int] = field(default_factory=list)
    outside_temperatures: list[float] = field(default_factory=list)
    inside_temperatures: list[float] = field(default_factory=list)
    air_cond_modes: list[int] = field(default_factory=list)
    cold_wind_states: list[int] = field(default_factory=list)
    wind_states: list[int] = field(default_factory=list)
    compressor_states: list[int] = field(default_factory=list)
    big_wind_states: list[int] = field(default_factory=list)
    fresh_air_opening_11: list[int] = field(default_factory=list)
    fresh_air_opening_12: list[int] = field(default_factory=list)
    fresh_air_opening_21: list[int] = field(default_factory=list)
    fresh_air_opening_22: list[int] = field(default_factory=list)
    tc1_net_states: list[int] = field(default_factory=list)
    tc2_net_states: list[int] = field(default_factory=list)
    tc3_net_states: list[int] = field(default_factory=list)
    tc4_net_states: list[int] = field(default_factory=list)
    tc5_net_states: list[int] = field(default_factory=list)
    tc6_net_states: list[int] = field(default_factory=list)
    conn_ab: int = 0
    tc1_devs_state: int = 0
    tc2_devs_state: int = 0
    tc3_devs_state: int = 0
    tc4_devs_state: int = 0
    tc5_devs_state: int = 0
    tc6_devs_state: int = 0
    econn_dev_state: int = 0
    econn_dev_state2: int = 0
    fault_code: int = 0
    train_no: int = 0


@dataclass(frozen=True)
class TractionCutoffRequest:
    timestamp_ms: int
    msg_id: int
    pull_control_mask: int
    reserve: int = 0
    identify_bytes: bytes = b"\x55\xaa\x55\xaa"
    total_len: int = 26
    data_len: int = 2
    verify_type: int = 0
    verify_code: int = 0
    protocol_id: int = 0

    @property
    def requested_car_numbers(self) -> list[int]:
        return [index + 1 for index in range(6) if self.pull_control_mask & (1 << index)]


class NetworkScreenFrameBuilder:
    frame_size_bytes = 572
    data_size_bytes = 548

    def build(self, state: NetworkScreenState) -> bytes:
        now = datetime.now()
        timestamp_ms = state.timestamp_ms if state.timestamp_ms is not None else int(now.timestamp() * 1000)
        frame = bytearray(self.frame_size_bytes)
        write_display_header(frame, self.frame_size_bytes, self.data_size_bytes, timestamp_ms)
        self._write_time(frame, state, now)
        write_u8(frame, 36, state.curr_station_id)
        write_u8(frame, 37, state.next_station_id)
        write_u8(frame, 38, state.end_station_id)
        write_u8(frame, 39, state.power_state)
        write_float_le(frame, 40, state.speed_mps)
        write_float_le(frame, 44, state.acceleration_mps2)
        write_u16_le(frame, 48, state.power_pull)
        write_u16_le(frame, 50, state.net_pressure)
        write_u16_le(frame, 52, state.speed_limit)
        write_u8(frame, 54, state.level_pos)
        write_u8(frame, 55, state.run_mode)
        write_u16_le(frame, 56, state.master_voltage)
        write_u8(frame, 58, state.run_dir)
        write_u8(frame, 59, state.driver_room_state)
        self._write_arrays(frame, state)
        self._write_single_values(frame, state)
        return bytes(frame)

    @staticmethod
    def _write_time(frame: bytearray, state: NetworkScreenState, now: datetime) -> None:
        write_u16_le(frame, 24, state.year if state.year is not None else now.year)
        write_u16_le(frame, 26, state.month if state.month is not None else now.month)
        write_u16_le(frame, 28, state.day if state.day is not None else now.day)
        write_u16_le(frame, 30, state.hour if state.hour is not None else now.hour)
        write_u16_le(frame, 32, state.minute if state.minute is not None else now.minute)
        write_u16_le(frame, 34, state.second if state.second is not None else now.second)

    @staticmethod
    def _write_arrays(frame: bytearray, state: NetworkScreenState) -> None:
        write_u32_array_le(frame, 60, state.door_states, 6)
        write_u8_array(frame, 84, state.stop_pos_states, 6)
        write_u8_array(frame, 90, state.fire_empty_run_states, 6)
        write_u8_array(frame, 96, state.warm_empty_state1, 6)
        write_u8_array(frame, 102, state.warm_empty_state2, 6)
        write_u8_array(frame, 108, state.pull_switch_states, 6)
        write_u8_array(frame, 114, state.charge_states, 6)
        write_u8_array(frame, 120, state.assist_high_switch_states, 6)
        write_u8_array(frame, 126, state.breaker_master_states, 6)
        write_u16_array_le(frame, 132, state.elect_stop_forces, 6)
        write_u16_array_le(frame, 144, state.wind_pressures, 6)
        write_u16_array_le(frame, 156, state.brake_pressures, 6)
        write_u8_array(frame, 168, state.usage_rates, 6)
        write_u8_array(frame, 174, state.line_net_currents, 6)
        write_float_array_le(frame, 180, state.temperatures, 6)
        write_u8_array(frame, 204, state.pull_stream_states, 6)
        write_u8_array(frame, 210, state.emergency_stop_states, 10)
        write_u8_array(frame, 220, state.side_info_states, 6)
        write_u8_array(frame, 226, state.braker_states, 11)
        write_u8_array(frame, 237, state.line_and_elect_stop_states, 6)
        write_u16_array_le(frame, 244, state.line_voltages, 6)
        write_u8_array(frame, 256, state.stop_states, 6)
        write_u16_array_le(frame, 262, state.air_stop_forces, 6)
        write_u16_array_le(frame, 274, state.empty_pressures1, 6)
        write_u16_array_le(frame, 286, state.empty_pressures2, 6)
        write_u8_array(frame, 298, state.b05_b19_states, 6)
        write_u8_array(frame, 304, state.kma_elect_power_states, 6)
        write_u16_array_le(frame, 310, state.inverter_input_voltages, 6)
        write_u16_array_le(frame, 322, state.inverter_output_voltages, 6)
        write_u16_array_le(frame, 334, state.charger_output_voltages, 6)
        write_u8_array(frame, 346, state.inverter_input_currents, 6)
        write_u8_array(frame, 352, state.inverter_output_currents, 6)
        write_u8_array(frame, 358, state.charger_output_currents, 6)
        write_u32_array_le(frame, 408, state.smoke_temp_states, 6)
        write_float_array_le(frame, 432, state.outside_temperatures, 6)
        write_float_array_le(frame, 456, state.inside_temperatures, 6)
        write_u8_array(frame, 480, state.air_cond_modes, 6)
        write_u8_array(frame, 486, state.cold_wind_states, 6)
        write_u8_array(frame, 492, state.wind_states, 6)
        write_u16_array_le(frame, 498, state.compressor_states, 6)
        write_u8_array(frame, 510, state.big_wind_states, 6)
        write_u8_array(frame, 516, state.fresh_air_opening_11, 6)
        write_u8_array(frame, 522, state.fresh_air_opening_12, 6)
        write_u8_array(frame, 528, state.fresh_air_opening_21, 6)
        write_u8_array(frame, 534, state.fresh_air_opening_22, 6)
        write_u16_array_le(frame, 540, state.tc1_net_states, 2)
        write_u16_array_le(frame, 544, state.tc2_net_states, 2)
        write_u8_array(frame, 548, state.tc3_net_states, 2)
        write_u8_array(frame, 550, state.tc4_net_states, 2)
        write_u8_array(frame, 552, state.tc5_net_states, 2)
        write_u8_array(frame, 554, state.tc6_net_states, 2)

    @staticmethod
    def _write_single_values(frame: bytearray, state: NetworkScreenState) -> None:
        write_u8(frame, 364, state.tc1_km1)
        write_u8(frame, 365, state.tc1_km3)
        write_u8(frame, 366, state.tc1_km5)
        write_u8(frame, 367, state.tc2_km1)
        write_u8(frame, 368, state.tc2_km3)
        write_u8(frame, 369, state.tc2_km5)
        write_u16_le(frame, 370, state.tc1_battery_remain)
        write_u16_le(frame, 372, state.tc1_battery_voltage)
        write_u16_le(frame, 374, state.tc1_battery_charge_current)
        write_u16_le(frame, 376, state.tc1_battery_output_current)
        write_u16_le(frame, 378, state.tc1_battery_temp)
        write_u16_le(frame, 380, state.tc1_high_voltage)
        write_u16_le(frame, 382, state.tc1_low_voltage)
        write_u8(frame, 384, state.tc1_high_pos)
        write_u8(frame, 385, state.tc1_low_pos)
        write_u16_le(frame, 386, state.tc1_high_temp)
        write_u16_le(frame, 388, state.tc2_high_temp)
        write_u8(frame, 390, state.tc1_temp_pos)
        write_u8(frame, 391, state.tc2_temp_pos)
        write_u16_le(frame, 392, state.tc2_battery_remain)
        write_u16_le(frame, 394, state.tc2_battery_voltage)
        write_u16_le(frame, 396, state.tc2_battery_charge_current)
        write_u16_le(frame, 398, state.tc2_battery_output_current)
        write_u16_le(frame, 400, state.tc2_battery_temp)
        write_u16_le(frame, 402, state.tc2_high_voltage)
        write_u16_le(frame, 404, state.tc2_low_voltage)
        write_u8(frame, 406, state.tc2_high_pos)
        write_u8(frame, 407, state.tc2_low_pos)
        write_u8(frame, 556, state.conn_ab)
        write_u16_le(frame, 558, state.tc1_devs_state)
        write_u16_le(frame, 560, state.tc2_devs_state)
        write_u8(frame, 562, state.tc3_devs_state)
        write_u8(frame, 563, state.tc4_devs_state)
        write_u8(frame, 564, state.tc5_devs_state)
        write_u8(frame, 565, state.tc6_devs_state)
        write_u8(frame, 566, state.econn_dev_state)
        write_u8(frame, 567, state.econn_dev_state2)
        write_u16_le(frame, 568, state.fault_code)
        write_u16_le(frame, 570, state.train_no)


class TractionCutoffRequestParser:
    frame_size_bytes = 26

    def parse(self, frame: bytes) -> TractionCutoffRequest:
        require_frame_size(frame, self.frame_size_bytes, "HMI traction cutoff request")
        if frame[0:4] != b"\x55\xaa\x55\xaa":
            raise ValueError("HMI traction cutoff request has invalid identify bytes")
        return TractionCutoffRequest(
            timestamp_ms=read_u64_le(frame, 8),
            msg_id=read_u16_le(frame, 22),
            pull_control_mask=read_u8(frame, 24),
            reserve=read_u8(frame, 25),
            identify_bytes=frame[0:4],
            total_len=read_u16_le(frame, 4),
            data_len=read_u16_le(frame, 6),
            verify_type=read_u16_le(frame, 16),
            verify_code=read_u16_le(frame, 18),
            protocol_id=read_u16_le(frame, 20),
        )


@dataclass
class NetworkScreenClient:
    host: str = "192.168.100.122"
    port: int = 8888
    timeout_s: float = 3.0
    builder: NetworkScreenFrameBuilder = field(default_factory=NetworkScreenFrameBuilder)

    def send_state(self, state: NetworkScreenState) -> None:
        frame = self.builder.build(state)
        with TcpFrameClient(self.host, self.port, self.timeout_s) as client:
            client.send_frame(frame)
