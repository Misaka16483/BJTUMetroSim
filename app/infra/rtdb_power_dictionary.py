from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RTDB_DATABASE_NAME = "db_qing6"
RTDB_TABLE_NAME = "188_2"
RTDB_VALUE_COLUMN_INDEX = 3
TABLE_DEFINITION_ENCODING = "gbk"
POWER_POINT_START = 1000
POWER_POINT_END = 1470
POWER_POINT_CONTRACT_VERSION = "RTDB-188_2-POWER-V1"


def display_point_to_transport_row(point_no: int) -> int:
    """Convert the 1-based point number shown in the definition to RTDB's 0-based row."""
    if isinstance(point_no, bool) or not isinstance(point_no, int) or point_no < 1:
        raise ValueError("point_no must be a positive 1-based integer")
    return point_no - 1


def transport_row_to_display_point(row_index: int) -> int:
    if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0:
        raise ValueError("row_index must be a non-negative 0-based integer")
    return row_index + 1


@dataclass(frozen=True)
class TableDefinitionRow:
    point_no: int
    name: str
    sample_value: float
    unit: str
    line_no: int

    @property
    def transport_row_index(self) -> int:
        return display_point_to_transport_row(self.point_no)


@dataclass(frozen=True)
class PowerPointContract:
    point_no: int
    source_name: str
    source_unit: str
    canonical_field: str
    canonical_unit: str
    entity_id: str
    scale: float = 1.0
    sign: float = 1.0
    source_min: float | None = None
    source_max: float | None = None
    comparison_role: str = "SEMANTIC_REFERENCE"
    assumption: str = ""

    @property
    def transport_row_index(self) -> int:
        return display_point_to_transport_row(self.point_no)

    def normalize(self, source_value: float) -> float:
        value = float(source_value) * self.scale * self.sign
        if not math.isfinite(value):
            raise ValueError(f"point {self.point_no} produced a non-finite value")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "displayPointNo": self.point_no,
            "transportRowIndex": self.transport_row_index,
            "sourceName": self.source_name,
            "sourceUnit": self.source_unit,
            "canonicalField": self.canonical_field,
            "canonicalUnit": self.canonical_unit,
            "entityId": self.entity_id,
            "scale": self.scale,
            "sign": self.sign,
            "sourceRange": [self.source_min, self.source_max],
            "comparisonRole": self.comparison_role,
            "assumption": self.assumption,
        }


@dataclass(frozen=True)
class NormalizedObservation:
    display_point_no: int
    transport_row_index: int
    canonical_field: str
    canonical_unit: str
    entity_id: str
    value: float
    source_name: str
    source_unit: str
    comparison_role: str

    def to_dict(self) -> dict[str, object]:
        return {
            "displayPointNo": self.display_point_no,
            "transportRowIndex": self.transport_row_index,
            "canonicalField": self.canonical_field,
            "canonicalUnit": self.canonical_unit,
            "entityId": self.entity_id,
            "value": self.value,
            "sourceName": self.source_name,
            "sourceUnit": self.source_unit,
            "comparisonRole": self.comparison_role,
        }


@dataclass(frozen=True)
class DictionaryAudit:
    source_path: str
    sha256: str
    row_count: int
    power_range_count: int
    mapped_point_count: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    station_power_identity_max_error_ratio: float

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "sourcePath": self.source_path,
            "sha256": self.sha256,
            "rowCount": self.row_count,
            "powerRangeCount": self.power_range_count,
            "mappedPointCount": self.mapped_point_count,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stationPowerIdentityMaxErrorRatio": self.station_power_identity_max_error_ratio,
        }


def read_table_definition(path: Path) -> list[TableDefinitionRow]:
    rows: list[TableDefinitionRow] = []
    with path.open("r", encoding=TABLE_DEFINITION_ENCODING, newline="") as stream:
        for line_no, raw in enumerate(csv.reader(stream), start=1):
            if not raw or not raw[0].strip():
                continue
            try:
                point_no = int(raw[0])
            except ValueError as exc:
                raise ValueError(f"invalid point number at definition line {line_no}") from exc
            if len(raw) < 4:
                raise ValueError(f"definition line {line_no} has fewer than four columns")
            try:
                sample_value = float(raw[3] or 0.0)
            except ValueError as exc:
                raise ValueError(f"invalid sample value at definition line {line_no}") from exc
            rows.append(TableDefinitionRow(
                point_no=point_no,
                name=raw[2].strip() if len(raw) > 2 else "",
                sample_value=sample_value,
                unit=raw[8].strip() if len(raw) > 8 else "",
                line_no=line_no,
            ))
    return rows


def _substation_contracts() -> list[PowerPointContract]:
    contracts: list[PowerPointContract] = []
    fields = (
        (0, "站网流", "A", "currentA", "A", -5000.0, 5000.0),
        (1, "站网压", "V", "voltageV", "V", 0.0, 2000.0),
        (2, "站功率", "kW", "netPowerKw", "kW", -10000.0, 10000.0),
        (3, "站运行状态", "", "inService", "bool", 0.0, 1.0),
        (4, "站牵引供电", "", "tractionSupplyEnabled", "bool", 0.0, 1.0),
        (5, "站能量回馈", "kW", "feedbackPowerKw", "kW", 0.0, 10000.0),
    )
    for station_index in range(1, 13):
        base = 1001 + (station_index - 1) * 19
        entity_id = f"TS-09{station_index:02d}"
        role = "UNIT_AND_SIGN_REFERENCE" if station_index <= 10 else "UNBOUND_REFERENCE"
        for offset, name_suffix, unit, field, canonical_unit, low, high in fields:
            source_name = f"{station_index}#{name_suffix}"
            if station_index == 12 and offset == 0:
                source_name = "12#站电流"
            contracts.append(PowerPointContract(
                point_no=base + offset,
                source_name=source_name,
                source_unit=unit,
                canonical_field=field,
                canonical_unit=canonical_unit,
                entity_id=entity_id,
                source_min=low,
                source_max=high,
                comparison_role=role,
                assumption=(
                    "Teacher data is approximately 1.6 kV and is not a DC750V calibration target."
                    if field == "voltageV" else ""
                ),
            ))
    return contracts


def _train_summary_contracts() -> list[PowerPointContract]:
    contracts: list[PowerPointContract] = []
    fields = (
        (0, "编号", "", "trainNumber", "id", 1.0, 1.0, 0.0, 999999.0),
        (1, "激活端", "", "activeCab", "enum", 1.0, 1.0, 0.0, 2.0),
        (2, "方向", "", "directionCode", "enum", 1.0, 1.0, 0.0, 2.0),
        (3, "加速度", "m/s", "accelerationMps2", "m/s2", 1.0, 1.0, -3.0, 3.0),
        (4, "速度", "cm/s", "speedMps", "m/s", 0.01, 1.0, -15000.0, 15000.0),
        (5, "累计里程", "cm", "mileageM", "m", 0.01, 1.0, -10000000.0, 10000000.0),
    )
    for train_index in range(1, 21):
        base = 1229 + (train_index - 1) * 6
        for offset, suffix, unit, field, canonical_unit, scale, sign, low, high in fields:
            source_unit = unit if train_index == 1 else ""
            assumptions: list[str] = []
            if train_index > 1 and unit:
                assumptions.append("Unit is inherited from the identical train-1 point group; source cell is blank.")
            if offset == 3:
                assumptions.append("Source declares m/s; interpreted as acceleration in m/s2 by field semantics.")
            contracts.append(PowerPointContract(
                point_no=base + offset,
                source_name=f"列车{train_index}{suffix}",
                source_unit=source_unit,
                canonical_field=field,
                canonical_unit=canonical_unit,
                entity_id=f"SOURCE-TRAIN-{train_index:02d}",
                scale=scale,
                sign=sign,
                source_min=low,
                source_max=high,
                comparison_role="UNIT_AND_SIGN_REFERENCE" if offset in {4, 5} else "SEMANTIC_REFERENCE",
                assumption=" ".join(assumptions),
            ))
    return contracts


def _detailed_train_power_contracts() -> list[PowerPointContract]:
    rows = (
        (1370, "列车1-制动力", "kN", "totalBrakeForceN", "N", 1000.0, 1.0),
        (1371, "列车1-空气制动力", "kN", "pneumaticBrakeForceN", "N", 1000.0, 1.0),
        (1372, "列车1-电制动力", "kN", "electricBrakeForceN", "N", 1000.0, 1.0),
        (1398, "列车1-牵引手柄", "", "masterControllerPercent", "%", 1.0, 1.0),
        (1402, "列车1-网压", "V", "pantographVoltageV", "V", 1.0, 1.0),
        (1403, "列车1-#2车网流", "A", "motorCarCurrentA", "A", 1.0, 1.0),
        (1404, "列车1-#3车网流", "A", "motorCarCurrentA", "A", 1.0, 1.0),
        (1405, "列车1-#4车网流", "A", "motorCarCurrentA", "A", 1.0, 1.0),
        (1406, "列车1-#5车网流", "A", "motorCarCurrentA", "A", 1.0, 1.0),
        (1419, "列车1-#2车牵引能耗值", "kW", "tractionPowerKw", "kW", 1.0, 1.0),
        (1420, "列车1-#3车牵引能耗值", "kW", "tractionPowerKw", "kW", 1.0, 1.0),
        (1421, "列车1-#4车牵引能耗值", "kW", "tractionPowerKw", "kW", 1.0, 1.0),
        (1422, "列车1-#5车牵引能耗值", "kW", "tractionPowerKw", "kW", 1.0, 1.0),
        (1423, "列车1-#2车再生能耗值", "kW", "regenPowerAvailableKw", "kW", 1.0, -1.0),
        (1424, "列车1-#3车再生能耗值", "kW", "regenPowerAvailableKw", "kW", 1.0, -1.0),
        (1425, "列车1-#4车再生能耗值", "kW", "regenPowerAvailableKw", "kW", 1.0, -1.0),
        (1426, "列车1-#5车再生能耗值", "kW", "regenPowerAvailableKw", "kW", 1.0, -1.0),
        (1438, "列车1-全列牵引力", "kN", "totalTractionForceN", "N", 1000.0, 1.0),
        (1443, "列车1-全列能耗", "kW", "trainNetPowerKw", "kW", 1.0, 1.0),
    )
    contracts: list[PowerPointContract] = []
    for point_no, name, unit, field, canonical_unit, scale, sign in rows:
        car_suffix = f"-CAR-{point_no - 1401}" if 1403 <= point_no <= 1406 else ""
        if 1419 <= point_no <= 1426:
            car_suffix = f"-CAR-{2 + (point_no - 1419) % 4}"
        contracts.append(PowerPointContract(
            point_no=point_no,
            source_name=name,
            source_unit=unit,
            canonical_field=field,
            canonical_unit=canonical_unit,
            entity_id=f"SOURCE-TRAIN-01{car_suffix}",
            scale=scale,
            sign=sign,
            source_min=-100000.0,
            source_max=100000.0,
            comparison_role="UNIT_AND_SIGN_REFERENCE",
            assumption=(
                "Source regenerative power is negative; the simulator uses positive generated power."
                if field == "regenPowerAvailableKw" else ""
            ),
        ))
    return contracts


def build_power_point_contracts() -> tuple[PowerPointContract, ...]:
    contracts = _substation_contracts() + _train_summary_contracts() + _detailed_train_power_contracts()
    return tuple(sorted(contracts, key=lambda item: item.point_no))


def power_point_contract_document() -> dict[str, object]:
    points = [item.to_dict() for item in build_power_point_contracts()]
    return {
        "contractVersion": POWER_POINT_CONTRACT_VERSION,
        "databaseName": RTDB_DATABASE_NAME,
        "tableName": RTDB_TABLE_NAME,
        "valueColumnIndex": RTDB_VALUE_COLUMN_INDEX,
        "displayIndexBase": 1,
        "transportIndexBase": 0,
        "pointRange": [POWER_POINT_START, POWER_POINT_END],
        "writeEnabled": False,
        "points": points,
    }


def power_point_contract_sha256() -> str:
    encoded = json.dumps(
        power_point_contract_document(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RtdbPowerDictionary:
    """Read-only semantic adapter. It never writes to RTDB or mutates the solver."""

    def __init__(self, contracts: Iterable[PowerPointContract] | None = None) -> None:
        selected = tuple(contracts or build_power_point_contracts())
        self.by_point_no = {item.point_no: item for item in selected}
        if len(self.by_point_no) != len(selected):
            raise ValueError("duplicate display point number in power dictionary")
        self.by_transport_row = {item.transport_row_index: item for item in selected}

    def decode(self, transport_row_index: int, source_value: float) -> NormalizedObservation:
        try:
            contract = self.by_transport_row[transport_row_index]
        except KeyError as exc:
            raise KeyError(f"unmapped RTDB row index {transport_row_index}") from exc
        return NormalizedObservation(
            display_point_no=contract.point_no,
            transport_row_index=transport_row_index,
            canonical_field=contract.canonical_field,
            canonical_unit=contract.canonical_unit,
            entity_id=contract.entity_id,
            value=contract.normalize(source_value),
            source_name=contract.source_name,
            source_unit=contract.source_unit,
            comparison_role=contract.comparison_role,
        )


def audit_table_definition(path: Path) -> DictionaryAudit:
    raw_rows = read_table_definition(path)
    errors: list[str] = []
    warnings: list[str] = []
    by_point = {item.point_no: item for item in raw_rows}
    if len(by_point) != len(raw_rows):
        errors.append("definition contains duplicate point numbers")
    for row in raw_rows:
        if row.point_no != row.line_no:
            errors.append(f"point {row.point_no} is on line {row.line_no}; 1-based identity is broken")
            if len(errors) >= 20:
                break

    contracts = build_power_point_contracts()
    mapped = 0
    for contract in contracts:
        source = by_point.get(contract.point_no)
        if source is None:
            errors.append(f"missing source point {contract.point_no}")
            continue
        mapped += 1
        if source.name != contract.source_name:
            errors.append(
                f"point {contract.point_no} name mismatch: {source.name!r} != {contract.source_name!r}"
            )
        if source.unit != contract.source_unit:
            errors.append(
                f"point {contract.point_no} unit mismatch: {source.unit!r} != {contract.source_unit!r}"
            )
        if contract.source_min is not None and source.sample_value < contract.source_min:
            warnings.append(f"point {contract.point_no} sample is below the engineering reference range")
        if contract.source_max is not None and source.sample_value > contract.source_max:
            warnings.append(f"point {contract.point_no} sample is above the engineering reference range")

    identity_errors: list[float] = []
    for station_index in range(1, 13):
        base = 1001 + (station_index - 1) * 19
        current = by_point[base].sample_value
        voltage = by_point[base + 1].sample_value
        power = by_point[base + 2].sample_value
        expected = voltage * current / 1000.0
        identity_errors.append(abs(power - expected) / max(abs(expected), 1.0))
    max_identity_error = max(identity_errors, default=0.0)
    if max_identity_error > 0.01:
        errors.append(f"source substation P=UI identity error {max_identity_error:.3%} exceeds 1%")

    power_rows = [
        item for item in raw_rows
        if POWER_POINT_START <= item.point_no <= POWER_POINT_END
    ]
    if len(power_rows) != POWER_POINT_END - POWER_POINT_START + 1:
        errors.append("power point range 1000..1470 is not contiguous")

    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return DictionaryAudit(
        source_path=str(path),
        sha256=sha256,
        row_count=len(raw_rows),
        power_range_count=len(power_rows),
        mapped_point_count=mapped,
        errors=tuple(errors),
        warnings=tuple(warnings),
        station_power_identity_max_error_ratio=max_identity_error,
    )
