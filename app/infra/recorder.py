from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunRecorder:
    """SQLite-backed recording framework for events, telemetry and metrics."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                tick INTEGER,
                topic TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                tick INTEGER,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS station_passenger_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                station_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                arrivals INTEGER NOT NULL DEFAULT 0,
                boarding INTEGER NOT NULL DEFAULT 0,
                alighting INTEGER NOT NULL DEFAULT 0,
                waiting INTEGER NOT NULL DEFAULT 0,
                left_behind INTEGER NOT NULL DEFAULT 0,
                platform_density_pax_per_m2 REAL,
                crowding_level TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS train_load_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                train_id TEXT NOT NULL,
                onboard_pax INTEGER NOT NULL,
                capacity_pax INTEGER,
                load_factor REAL,
                vehicle_load_kg REAL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS dwell_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                train_id TEXT NOT NULL,
                station_id TEXT NOT NULL,
                arrival_ms INTEGER,
                depart_ms INTEGER,
                planned_dwell_sec REAL,
                estimated_dwell_sec REAL,
                actual_dwell_sec REAL,
                dispatch_hold_sec REAL DEFAULT 0,
                reason TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS dispatch_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                decision_id TEXT NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                train_id TEXT,
                station_id TEXT,
                action TEXT NOT NULL,
                duration_sec REAL,
                reason TEXT NOT NULL,
                expected_impact_json TEXT NOT NULL DEFAULT '{}',
                applied INTEGER NOT NULL DEFAULT 1,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id),
                UNIQUE(run_id, decision_id)
            );
            CREATE TABLE IF NOT EXISTS power_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                power_section_id TEXT NOT NULL,
                requested_power_kw REAL NOT NULL,
                available_power_kw REAL NOT NULL,
                traction_limit_ratio REAL NOT NULL,
                voltage_level TEXT NOT NULL,
                energy_kwh REAL NOT NULL DEFAULT 0,
                regen_energy_kwh REAL NOT NULL DEFAULT 0,
                absorbed_regen_kw REAL,
                wasted_regen_kw REAL,
                source TEXT NOT NULL DEFAULT 'SELF_SIM',
                quality TEXT NOT NULL DEFAULT 'ESTIMATED',
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_station_passenger_station_time
                ON station_passenger_records(run_id, station_id, direction, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_train_load_train_time
                ON train_load_records(run_id, train_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_run_time
                ON dispatch_decisions(run_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_power_records_section_time
                ON power_records(run_id, power_section_id, sim_time_ms);
            """
        )
        self.connection.commit()

    def start_run(self, name: str, metadata: dict[str, Any] | None = None) -> int:
        cursor = self.connection.execute(
            "INSERT INTO runs(name, started_at, metadata_json) VALUES (?, ?, ?)",
            (
                name,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def record_station_passenger(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        station_id: str,
        direction: str,
        arrivals: int = 0,
        boarding: int = 0,
        alighting: int = 0,
        waiting: int = 0,
        left_behind: int = 0,
        platform_density_pax_per_m2: float | None = None,
        crowding_level: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO station_passenger_records(
                run_id, sim_time_ms, station_id, direction, arrivals, boarding,
                alighting, waiting, left_behind, platform_density_pax_per_m2,
                crowding_level, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                station_id,
                direction,
                arrivals,
                boarding,
                alighting,
                waiting,
                left_behind,
                platform_density_pax_per_m2,
                crowding_level,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def record_train_load(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        train_id: str,
        onboard_pax: int,
        capacity_pax: int,
        load_factor: float,
        vehicle_load_kg: float,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO train_load_records(
                run_id, sim_time_ms, train_id, onboard_pax, capacity_pax,
                load_factor, vehicle_load_kg, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                train_id,
                onboard_pax,
                capacity_pax,
                load_factor,
                vehicle_load_kg,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def record_dwell(
        self,
        run_id: int,
        *,
        train_id: str,
        station_id: str,
        arrival_ms: int | None,
        depart_ms: int | None,
        planned_dwell_sec: float,
        estimated_dwell_sec: float,
        actual_dwell_sec: float | None = None,
        dispatch_hold_sec: float = 0.0,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO dwell_records(
                run_id, train_id, station_id, arrival_ms, depart_ms,
                planned_dwell_sec, estimated_dwell_sec, actual_dwell_sec,
                dispatch_hold_sec, reason, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                train_id,
                station_id,
                arrival_ms,
                depart_ms,
                planned_dwell_sec,
                estimated_dwell_sec,
                actual_dwell_sec,
                dispatch_hold_sec,
                reason,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def record_dispatch_decision(
        self,
        run_id: int,
        *,
        decision_id: str,
        sim_time_ms: int,
        train_id: str | None,
        station_id: str | None,
        action: str,
        duration_sec: float,
        reason: str,
        expected_impact: dict[str, Any] | None = None,
        applied: bool = True,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO dispatch_decisions(
                run_id, decision_id, sim_time_ms, train_id, station_id, action,
                duration_sec, reason, expected_impact_json, applied, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                decision_id,
                sim_time_ms,
                train_id,
                station_id,
                action,
                duration_sec,
                reason,
                json.dumps(expected_impact or {}, ensure_ascii=False),
                1 if applied else 0,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def record_power(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        power_section_id: str,
        requested_power_kw: float,
        available_power_kw: float,
        traction_limit_ratio: float,
        voltage_level: str,
        energy_kwh: float,
        regen_energy_kwh: float,
        absorbed_regen_kw: float | None = None,
        wasted_regen_kw: float | None = None,
        source: str = "SELF_SIM",
        quality: str = "ESTIMATED",
        detail: dict[str, Any] | None = None,
    ) -> None:
        if source == "PLATFORM":
            raise ValueError("power_records cannot use source='PLATFORM' before an explicit power interface exists")
        self.connection.execute(
            """
            INSERT INTO power_records(
                run_id, sim_time_ms, power_section_id, requested_power_kw,
                available_power_kw, traction_limit_ratio, voltage_level,
                energy_kwh, regen_energy_kwh, absorbed_regen_kw, wasted_regen_kw,
                source, quality, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                power_section_id,
                requested_power_kw,
                available_power_kw,
                traction_limit_ratio,
                voltage_level,
                energy_kwh,
                regen_energy_kwh,
                absorbed_regen_kw,
                wasted_regen_kw,
                source,
                quality,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def record_event(
        self,
        run_id: int,
        topic: str,
        payload: dict[str, Any],
        *,
        tick: int | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO events(run_id, tick, topic, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                tick,
                topic,
                json.dumps(payload, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def record_metric(
        self,
        run_id: int,
        name: str,
        value: float,
        *,
        unit: str | None = None,
        tick: int | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO metrics(run_id, tick, name, value, unit, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                tick,
                name,
                value,
                unit,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()
