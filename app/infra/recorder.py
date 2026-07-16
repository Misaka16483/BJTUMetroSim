from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunRecorder:
    """SQLite-backed recording framework for events, telemetry and metrics."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self._batch_active = False
        self._batch_owner: int | None = None
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            if self._batch_active:
                self.connection.commit()
                self._batch_active = False
                self._batch_owner = None
            self.connection.close()

    def begin_batch(self) -> None:
        """Defer per-row commits so one simulation tick is one SQLite transaction."""
        owner = threading.get_ident()
        self._lock.acquire()
        try:
            if self._batch_active:
                if self._batch_owner != owner:
                    raise RuntimeError("RECORDER_BATCH_OWNED_BY_ANOTHER_THREAD")
                # Recover a transaction left open by an interrupted tick before
                # starting the next authoritative tick transaction.
                self.connection.rollback()
                self._batch_active = False
                self._batch_owner = None
                self._lock.release()
            self.connection.execute("BEGIN IMMEDIATE")
            self._batch_active = True
            self._batch_owner = owner
        except Exception:
            self._lock.release()
            raise

    def commit_batch(self) -> None:
        if not self._batch_active:
            raise RuntimeError("RECORDER_BATCH_NOT_ACTIVE")
        try:
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self._batch_active = False
            self._batch_owner = None
            self._lock.release()

    def rollback_batch(self) -> None:
        if not self._batch_active:
            return
        try:
            self.connection.rollback()
            self._batch_active = False
            self._batch_owner = None
        finally:
            self._lock.release()

    def _commit_if_needed(self) -> None:
        if not self._batch_active:
            self.connection.commit()

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
            CREATE TABLE IF NOT EXISTS traction_substations (
                substation_id TEXT PRIMARY KEY,
                line_id TEXT NOT NULL,
                name TEXT NOT NULL,
                mileage_m REAL NOT NULL,
                no_load_voltage_v REAL NOT NULL,
                internal_resistance_ohm REAL NOT NULL,
                rated_current_a REAL NOT NULL,
                overload_current_a REAL NOT NULL,
                efs_capacity_kw REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'IN_SERVICE',
                quality TEXT NOT NULL DEFAULT 'ENGINEERING_ESTIMATE',
                detail_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS feeder_arms (
                feeder_id TEXT PRIMARY KEY,
                substation_id TEXT NOT NULL,
                line_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                side TEXT NOT NULL,
                from_mileage_m REAL NOT NULL,
                to_mileage_m REAL NOT NULL,
                cable_resistance_ohm REAL NOT NULL,
                continuous_current_a REAL NOT NULL,
                short_time_current_a REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'CLOSED',
                quality TEXT NOT NULL DEFAULT 'ENGINEERING_ESTIMATE',
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(substation_id) REFERENCES traction_substations(substation_id)
            );
            CREATE TABLE IF NOT EXISTS contact_rail_sections (
                section_id TEXT PRIMARY KEY,
                line_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                from_mileage_m REAL NOT NULL,
                to_mileage_m REAL NOT NULL,
                resistance_ohm_per_km REAL NOT NULL,
                current_limit_a REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'ENERGIZED',
                quality TEXT NOT NULL DEFAULT 'ENGINEERING_ESTIMATE',
                detail_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS return_rail_sections (
                section_id TEXT PRIMARY KEY,
                line_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                from_mileage_m REAL NOT NULL,
                to_mileage_m REAL NOT NULL,
                resistance_ohm_per_km REAL NOT NULL,
                cross_bonding_group TEXT NOT NULL DEFAULT 'V0',
                quality TEXT NOT NULL DEFAULT 'ENGINEERING_ESTIMATE',
                detail_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS power_switches (
                switch_id TEXT PRIMARY KEY,
                line_id TEXT NOT NULL,
                switch_type TEXT NOT NULL,
                mileage_m REAL NOT NULL,
                from_node_id TEXT NOT NULL,
                to_node_id TEXT NOT NULL,
                normal_state TEXT NOT NULL,
                current_state TEXT NOT NULL,
                remote_controllable INTEGER NOT NULL DEFAULT 1,
                quality TEXT NOT NULL DEFAULT 'ENGINEERING_ESTIMATE',
                detail_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS supercapacitor_storage_systems (
                storage_id TEXT PRIMARY KEY,
                substation_id TEXT NOT NULL,
                line_id TEXT NOT NULL,
                rated_energy_kwh REAL NOT NULL,
                max_charge_power_kw REAL NOT NULL,
                max_discharge_power_kw REAL NOT NULL,
                discharge_trigger_power_kw REAL NOT NULL,
                min_soc REAL NOT NULL,
                max_soc REAL NOT NULL,
                charge_efficiency REAL NOT NULL,
                discharge_efficiency REAL NOT NULL,
                standby_power_kw REAL NOT NULL,
                status TEXT NOT NULL,
                quality TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(substation_id) REFERENCES traction_substations(substation_id)
            );
            CREATE TABLE IF NOT EXISTS train_voltage_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                train_id TEXT NOT NULL,
                power_section_id TEXT NOT NULL,
                mileage_m REAL,
                voltage_v REAL NOT NULL,
                current_a REAL NOT NULL,
                requested_power_kw REAL NOT NULL,
                traction_limit_ratio REAL NOT NULL,
                regen_limit_ratio REAL NOT NULL,
                voltage_level TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS substation_power_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                substation_id TEXT NOT NULL,
                voltage_v REAL NOT NULL,
                current_a REAL NOT NULL,
                power_kw REAL NOT NULL,
                energy_kwh REAL NOT NULL,
                load_ratio REAL NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS supercapacitor_power_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                storage_id TEXT NOT NULL,
                soc REAL NOT NULL,
                stored_energy_kwh REAL NOT NULL,
                charge_power_kw REAL NOT NULL,
                discharge_power_kw REAL NOT NULL,
                conversion_losses_kw REAL NOT NULL,
                cumulative_charged_kwh REAL NOT NULL,
                cumulative_discharged_kwh REAL NOT NULL,
                state TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id),
                FOREIGN KEY(storage_id) REFERENCES supercapacitor_storage_systems(storage_id)
            );
            CREATE TABLE IF NOT EXISTS regen_energy_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                generated_regen_kw REAL NOT NULL,
                absorbed_regen_kw REAL NOT NULL,
                feedback_regen_kw REAL NOT NULL,
                wasted_regen_kw REAL NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS regen_path_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                source_train_id TEXT NOT NULL,
                sink_type TEXT NOT NULL,
                sink_id TEXT NOT NULL,
                via_substation_id TEXT,
                source_feeder_id TEXT,
                sink_feeder_id TEXT,
                generated_kw REAL NOT NULL,
                delivered_kw REAL NOT NULL,
                losses_kw REAL NOT NULL,
                current_a REAL NOT NULL,
                path_resistance_ohm REAL NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS power_solver_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                converged INTEGER NOT NULL,
                iterations INTEGER NOT NULL,
                solve_time_ms REAL NOT NULL,
                power_balance_error_kw REAL NOT NULL,
                power_balance_error_ratio REAL NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS power_command_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                command_id TEXT NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                command_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(id),
                UNIQUE(run_id, command_id)
            );
            CREATE TABLE IF NOT EXISTS world_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                tick INTEGER NOT NULL,
                sim_time_ms INTEGER NOT NULL,
                encoding TEXT NOT NULL DEFAULT 'json+zlib',
                snapshot_blob BLOB NOT NULL,
                snapshot_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
                UNIQUE(run_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_station_passenger_station_time
                ON station_passenger_records(run_id, station_id, direction, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_train_load_train_time
                ON train_load_records(run_id, train_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_run_time
                ON dispatch_decisions(run_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_power_records_section_time
                ON power_records(run_id, power_section_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_traction_substations_line_mileage
                ON traction_substations(line_id, mileage_m);
            CREATE INDEX IF NOT EXISTS idx_feeder_arms_substation
                ON feeder_arms(substation_id, direction, side);
            CREATE INDEX IF NOT EXISTS idx_contact_rail_sections_line_mileage
                ON contact_rail_sections(line_id, direction, from_mileage_m, to_mileage_m);
            CREATE INDEX IF NOT EXISTS idx_power_switches_line_mileage
                ON power_switches(line_id, mileage_m);
            CREATE INDEX IF NOT EXISTS idx_supercapacitor_storage_substation
                ON supercapacitor_storage_systems(substation_id);
            CREATE INDEX IF NOT EXISTS idx_train_voltage_records_train_time
                ON train_voltage_records(run_id, train_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_substation_power_records_time
                ON substation_power_records(run_id, substation_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_supercapacitor_power_records_time
                ON supercapacitor_power_records(run_id, storage_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_regen_energy_records_time
                ON regen_energy_records(run_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_regen_path_records_time
                ON regen_path_records(run_id, sim_time_ms, source_train_id);
            CREATE INDEX IF NOT EXISTS idx_power_solver_records_time
                ON power_solver_records(run_id, sim_time_ms);
            CREATE INDEX IF NOT EXISTS idx_power_command_records_time
                ON power_command_records(run_id, sim_time_ms);
            CREATE TABLE IF NOT EXISTS sim_reports (
                run_id INTEGER PRIMARY KEY,
                generated_at TEXT NOT NULL,
                report_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_world_snapshots_run_time
                ON world_snapshots(run_id, sim_time_ms, sequence);
            """
        )
        self._commit_if_needed()

    def start_run(self, name: str, metadata: dict[str, Any] | None = None) -> int:
        cursor = self.connection.execute(
            "INSERT INTO runs(name, started_at, metadata_json) VALUES (?, ?, ?)",
            (
                name,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self._commit_if_needed()
        return int(cursor.lastrowid)

    def update_run_metadata(self, run_id: int, updates: dict[str, Any]) -> None:
        """Merge authoritative runtime metadata into an existing run."""
        with self._lock:
            row = self.connection.execute(
                "SELECT metadata_json FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"run_id={run_id}")
            metadata = json.loads(row[0])
            metadata.update(updates)
            self.connection.execute(
                "UPDATE runs SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), run_id),
            )
            self._commit_if_needed()

    def upsert_power_topology(self, topology: dict[str, Any]) -> None:
        line_id = str(topology.get("lineId", "9"))
        quality = str(topology.get("quality", "ENGINEERING_ESTIMATE"))
        for item in topology.get("substations", []):
            self.connection.execute(
                """
                INSERT INTO traction_substations(
                    substation_id, line_id, name, mileage_m, no_load_voltage_v,
                    internal_resistance_ohm, rated_current_a, overload_current_a,
                    efs_capacity_kw, status, quality, detail_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(substation_id) DO UPDATE SET
                    name=excluded.name,
                    mileage_m=excluded.mileage_m,
                    no_load_voltage_v=excluded.no_load_voltage_v,
                    internal_resistance_ohm=excluded.internal_resistance_ohm,
                    rated_current_a=excluded.rated_current_a,
                    overload_current_a=excluded.overload_current_a,
                    efs_capacity_kw=excluded.efs_capacity_kw,
                    status=excluded.status,
                    quality=excluded.quality,
                    detail_json=excluded.detail_json
                """,
                (
                    item["substationId"],
                    line_id,
                    item["name"],
                    item["mileageM"],
                    item["noLoadVoltageV"],
                    item["internalResistanceOhm"],
                    item["ratedCurrentA"],
                    item["overloadCurrentA"],
                    item.get("efsCapacityKw", 0.0),
                    item.get("status", "IN_SERVICE"),
                    item.get("quality", quality),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        for item in topology.get("supercapacitorStorageSystems", []):
            self.connection.execute(
                """
                INSERT INTO supercapacitor_storage_systems(
                    storage_id, substation_id, line_id, rated_energy_kwh,
                    max_charge_power_kw, max_discharge_power_kw,
                    discharge_trigger_power_kw, min_soc, max_soc,
                    charge_efficiency, discharge_efficiency, standby_power_kw,
                    status, quality, detail_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(storage_id) DO UPDATE SET
                    substation_id=excluded.substation_id,
                    rated_energy_kwh=excluded.rated_energy_kwh,
                    max_charge_power_kw=excluded.max_charge_power_kw,
                    max_discharge_power_kw=excluded.max_discharge_power_kw,
                    discharge_trigger_power_kw=excluded.discharge_trigger_power_kw,
                    min_soc=excluded.min_soc,
                    max_soc=excluded.max_soc,
                    charge_efficiency=excluded.charge_efficiency,
                    discharge_efficiency=excluded.discharge_efficiency,
                    standby_power_kw=excluded.standby_power_kw,
                    status=excluded.status,
                    quality=excluded.quality,
                    detail_json=excluded.detail_json
                """,
                (
                    item["storageId"],
                    item["substationId"],
                    line_id,
                    item["ratedEnergyKwh"],
                    item["maxChargePowerKw"],
                    item["maxDischargePowerKw"],
                    item.get("dischargeTriggerPowerKw", 1000.0),
                    item["minSoc"],
                    item["maxSoc"],
                    item["chargeEfficiency"],
                    item["dischargeEfficiency"],
                    item["standbyPowerKw"],
                    item.get("status", "IN_SERVICE"),
                    item.get("quality", quality),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        for item in topology.get("feeders", []):
            self.connection.execute(
                """
                INSERT INTO feeder_arms(
                    feeder_id, substation_id, line_id, direction, side,
                    from_mileage_m, to_mileage_m, cable_resistance_ohm,
                    continuous_current_a, short_time_current_a, status, quality, detail_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feeder_id) DO UPDATE SET
                    substation_id=excluded.substation_id,
                    direction=excluded.direction,
                    side=excluded.side,
                    from_mileage_m=excluded.from_mileage_m,
                    to_mileage_m=excluded.to_mileage_m,
                    cable_resistance_ohm=excluded.cable_resistance_ohm,
                    continuous_current_a=excluded.continuous_current_a,
                    short_time_current_a=excluded.short_time_current_a,
                    status=excluded.status,
                    quality=excluded.quality,
                    detail_json=excluded.detail_json
                """,
                (
                    item["feederId"],
                    item["substationId"],
                    line_id,
                    item["direction"],
                    item["side"],
                    item["fromMileageM"],
                    item["toMileageM"],
                    item["cableResistanceOhm"],
                    item["continuousCurrentA"],
                    item["shortTimeCurrentA"],
                    item.get("status", "CLOSED"),
                    item.get("quality", quality),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        for item in topology.get("contactRailSections", []):
            self.connection.execute(
                """
                INSERT INTO contact_rail_sections(
                    section_id, line_id, direction, from_mileage_m, to_mileage_m,
                    resistance_ohm_per_km, current_limit_a, status, quality, detail_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(section_id) DO UPDATE SET
                    direction=excluded.direction,
                    from_mileage_m=excluded.from_mileage_m,
                    to_mileage_m=excluded.to_mileage_m,
                    resistance_ohm_per_km=excluded.resistance_ohm_per_km,
                    current_limit_a=excluded.current_limit_a,
                    status=excluded.status,
                    quality=excluded.quality,
                    detail_json=excluded.detail_json
                """,
                (
                    item["sectionId"],
                    line_id,
                    item["direction"],
                    item["fromMileageM"],
                    item["toMileageM"],
                    item["resistanceOhmPerKm"],
                    item["currentLimitA"],
                    item.get("status", "ENERGIZED"),
                    item.get("quality", quality),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        for item in topology.get("returnRailSections", []):
            self.connection.execute(
                """
                INSERT INTO return_rail_sections(
                    section_id, line_id, direction, from_mileage_m, to_mileage_m,
                    resistance_ohm_per_km, cross_bonding_group, quality, detail_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(section_id) DO UPDATE SET
                    direction=excluded.direction,
                    from_mileage_m=excluded.from_mileage_m,
                    to_mileage_m=excluded.to_mileage_m,
                    resistance_ohm_per_km=excluded.resistance_ohm_per_km,
                    cross_bonding_group=excluded.cross_bonding_group,
                    quality=excluded.quality,
                    detail_json=excluded.detail_json
                """,
                (
                    item["sectionId"],
                    line_id,
                    item["direction"],
                    item["fromMileageM"],
                    item["toMileageM"],
                    item["resistanceOhmPerKm"],
                    item.get("crossBondingGroup", "V0"),
                    item.get("quality", quality),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        for item in topology.get("switches", []):
            self.connection.execute(
                """
                INSERT INTO power_switches(
                    switch_id, line_id, switch_type, mileage_m, from_node_id,
                    to_node_id, normal_state, current_state, remote_controllable,
                    quality, detail_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(switch_id) DO UPDATE SET
                    switch_type=excluded.switch_type,
                    mileage_m=excluded.mileage_m,
                    from_node_id=excluded.from_node_id,
                    to_node_id=excluded.to_node_id,
                    normal_state=excluded.normal_state,
                    current_state=excluded.current_state,
                    remote_controllable=excluded.remote_controllable,
                    quality=excluded.quality,
                    detail_json=excluded.detail_json
                """,
                (
                    item["switchId"],
                    line_id,
                    item["switchType"],
                    item["mileageM"],
                    item["fromNodeId"],
                    item["toNodeId"],
                    item["normalState"],
                    item["currentState"],
                    1 if item.get("remoteControllable", True) else 0,
                    item.get("quality", quality),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        self._commit_if_needed()

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
        self._commit_if_needed()

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
        self._commit_if_needed()

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
        self._commit_if_needed()

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
        self._commit_if_needed()

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
        self._commit_if_needed()

    def record_train_voltage(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        train_id: str,
        power_section_id: str,
        mileage_m: float | None = None,
        voltage_v: float,
        current_a: float,
        requested_power_kw: float,
        traction_limit_ratio: float,
        regen_limit_ratio: float,
        voltage_level: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO train_voltage_records(
                run_id, sim_time_ms, train_id, power_section_id, mileage_m, voltage_v,
                current_a, requested_power_kw, traction_limit_ratio,
                regen_limit_ratio, voltage_level, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                train_id,
                power_section_id,
                mileage_m,
                voltage_v,
                current_a,
                requested_power_kw,
                traction_limit_ratio,
                regen_limit_ratio,
                voltage_level,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self._commit_if_needed()

    def record_substation_power(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        substation_id: str,
        voltage_v: float,
        current_a: float,
        power_kw: float,
        energy_kwh: float,
        load_ratio: float,
        status: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO substation_power_records(
                run_id, sim_time_ms, substation_id, voltage_v, current_a,
                power_kw, energy_kwh, load_ratio, status, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                substation_id,
                voltage_v,
                current_a,
                power_kw,
                energy_kwh,
                load_ratio,
                status,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self._commit_if_needed()

    def record_regen_energy(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        generated_regen_kw: float,
        absorbed_regen_kw: float,
        feedback_regen_kw: float,
        wasted_regen_kw: float,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO regen_energy_records(
                run_id, sim_time_ms, generated_regen_kw, absorbed_regen_kw,
                feedback_regen_kw, wasted_regen_kw, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                generated_regen_kw,
                absorbed_regen_kw,
                feedback_regen_kw,
                wasted_regen_kw,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self._commit_if_needed()

    def record_supercapacitor_power(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        storage_id: str,
        soc: float,
        stored_energy_kwh: float,
        charge_power_kw: float,
        discharge_power_kw: float,
        conversion_losses_kw: float,
        cumulative_charged_kwh: float,
        cumulative_discharged_kwh: float,
        state: str,
        status: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO supercapacitor_power_records(
                run_id, sim_time_ms, storage_id, soc, stored_energy_kwh,
                charge_power_kw, discharge_power_kw, conversion_losses_kw,
                cumulative_charged_kwh, cumulative_discharged_kwh,
                state, status, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                storage_id,
                soc,
                stored_energy_kwh,
                charge_power_kw,
                discharge_power_kw,
                conversion_losses_kw,
                cumulative_charged_kwh,
                cumulative_discharged_kwh,
                state,
                status,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self._commit_if_needed()

    def record_power_solver(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        converged: bool,
        iterations: int,
        solve_time_ms: float,
        power_balance_error_kw: float,
        power_balance_error_ratio: float,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO power_solver_records(
                run_id, sim_time_ms, converged, iterations, solve_time_ms,
                power_balance_error_kw, power_balance_error_ratio, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                1 if converged else 0,
                iterations,
                solve_time_ms,
                power_balance_error_kw,
                power_balance_error_ratio,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self._commit_if_needed()

    def record_regen_path(
        self,
        run_id: int,
        *,
        sim_time_ms: int,
        source_train_id: str,
        sink_type: str,
        sink_id: str,
        via_substation_id: str | None,
        source_feeder_id: str | None,
        sink_feeder_id: str | None,
        generated_kw: float,
        delivered_kw: float,
        losses_kw: float,
        current_a: float,
        path_resistance_ohm: float,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO regen_path_records(
                run_id, sim_time_ms, source_train_id, sink_type, sink_id,
                via_substation_id, source_feeder_id, sink_feeder_id,
                generated_kw, delivered_kw, losses_kw, current_a,
                path_resistance_ohm, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sim_time_ms,
                source_train_id,
                sink_type,
                sink_id,
                via_substation_id,
                source_feeder_id,
                sink_feeder_id,
                generated_kw,
                delivered_kw,
                losses_kw,
                current_a,
                path_resistance_ohm,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self._commit_if_needed()

    def record_power_command(
        self,
        run_id: int,
        *,
        command_id: str,
        sim_time_ms: int,
        command_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO power_command_records(
                run_id, command_id, sim_time_ms, command_type, status, payload_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                command_id,
                sim_time_ms,
                command_type,
                status,
                json.dumps(payload or {}, ensure_ascii=False),
                error,
            ),
        )
        self._commit_if_needed()

    def replay_events(self, run_id: int, topic: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT tick, topic, payload_json, created_at FROM events WHERE run_id = ?"
        params: list[Any] = [run_id]
        if topic is not None:
            sql += " AND topic = ?"
            params.append(topic)
        sql += " ORDER BY COALESCE(tick, -1), id"
        rows = self.connection.execute(sql, params).fetchall()
        return [
            {
                "tick": row[0],
                "topic": row[1],
                "payload": json.loads(row[2]),
                "createdAt": row[3],
            }
            for row in rows
        ]

    def replay_power_commands(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT command_id, sim_time_ms, command_type, status, payload_json, error
            FROM power_command_records
            WHERE run_id = ?
            ORDER BY sim_time_ms, id
            """,
            (run_id,),
        ).fetchall()
        commands: list[dict[str, Any]] = []
        for command_id, sim_time_ms, command_type, status, payload_json, error in rows:
            payload = json.loads(payload_json)
            commands.append({
                "commandId": command_id,
                "simTimeMs": sim_time_ms,
                "commandType": command_type,
                "status": status,
                "requestPayload": payload.get("request", payload),
                "result": payload.get("result", {}),
                "error": error,
            })
        return commands

    def record_world_snapshot(
        self,
        run_id: int,
        *,
        sequence: int,
        tick: int,
        sim_time_ms: int,
        snapshot: dict[str, Any],
    ) -> str:
        """Persist an authoritative, immutable state frame and return its SHA-256."""
        canonical = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        snapshot_hash = hashlib.sha256(canonical).hexdigest()
        compressed = zlib.compress(canonical, level=6)
        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO world_snapshots(
                    run_id, sequence, tick, sim_time_ms, encoding,
                    snapshot_blob, snapshot_hash, created_at
                ) VALUES (?, ?, ?, ?, 'json+zlib', ?, ?, ?)
                """,
                (
                    run_id,
                    sequence,
                    tick,
                    sim_time_ms,
                    compressed,
                    snapshot_hash,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._commit_if_needed()
        return snapshot_hash

    def list_world_snapshots(self, run_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT sequence, tick, sim_time_ms, snapshot_hash,
                       length(snapshot_blob), created_at
                FROM world_snapshots
                WHERE run_id = ?
                ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "tick": row[1],
                "simTimeMs": row[2],
                "snapshotHash": row[3],
                "compressedBytes": row[4],
                "createdAt": row[5],
            }
            for row in rows
        ]

    def read_world_snapshot(
        self,
        run_id: int,
        *,
        sequence: int | None = None,
        sim_time_ms: int | None = None,
    ) -> dict[str, Any]:
        if sequence is not None:
            where = "run_id = ? AND sequence = ?"
            params: tuple[Any, ...] = (run_id, sequence)
            order = "sequence DESC"
        elif sim_time_ms is not None:
            where = "run_id = ? AND sim_time_ms <= ?"
            params = (run_id, sim_time_ms)
            order = "sim_time_ms DESC, sequence DESC"
        else:
            where = "run_id = ?"
            params = (run_id,)
            order = "sequence ASC"
        with self._lock:
            row = self.connection.execute(
                f"""
                SELECT sequence, tick, sim_time_ms, encoding, snapshot_blob, snapshot_hash
                FROM world_snapshots
                WHERE {where}
                ORDER BY {order}
                LIMIT 1
                """,
                params,
            ).fetchone()
        if row is None:
            raise KeyError(f"snapshot run_id={run_id} sequence={sequence} sim_time_ms={sim_time_ms}")
        raw = zlib.decompress(row[4]) if row[3] == "json+zlib" else bytes(row[4])
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_hash != row[5]:
            raise ValueError("WORLD_SNAPSHOT_HASH_MISMATCH")
        payload = json.loads(raw.decode("utf-8"))
        payload["snapshotHash"] = row[5]
        return payload

    def export_run(self, run_id: int) -> dict[str, Any]:
        run = self.connection.execute(
            "SELECT id, name, started_at, metadata_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise KeyError(f"run_id={run_id}")
        table_names = (
            "events",
            "metrics",
            "station_passenger_records",
            "train_load_records",
            "dwell_records",
            "dispatch_decisions",
            "power_records",
            "train_voltage_records",
            "substation_power_records",
            "supercapacitor_power_records",
            "regen_energy_records",
            "regen_path_records",
            "power_solver_records",
            "power_command_records",
            "world_snapshots",
        )
        payload: dict[str, Any] = {
            "run": {
                "id": run[0],
                "name": run[1],
                "startedAt": run[2],
                "metadata": json.loads(run[3]),
            },
            "tables": {},
        }
        previous_row_factory = self.connection.row_factory
        self.connection.row_factory = sqlite3.Row
        try:
            for table_name in table_names:
                rows = self.connection.execute(
                    f"SELECT * FROM {table_name} WHERE run_id = ? ORDER BY id",
                    (run_id,),
                ).fetchall()
                items: list[dict[str, Any]] = []
                for row in rows:
                    item = dict(row)
                    for key, value in list(item.items()):
                        if key.endswith("_json") and isinstance(value, str):
                            item[key[:-5]] = json.loads(value)
                            del item[key]
                        elif key == "snapshot_blob" and isinstance(value, bytes):
                            item[key] = f"<compressed:{len(value)} bytes>"
                    items.append(item)
                payload["tables"][table_name] = items
        finally:
            self.connection.row_factory = previous_row_factory
        return payload

    def export_run_json(self, run_id: int, output_path: str | Path) -> Path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.export_run(run_id), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

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
        self._commit_if_needed()

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
        self._commit_if_needed()

    def list_reports(self, limit: int = 3) -> list[dict[str, Any]]:
        """返回最近 N 次运行的报告摘要（按 run_id 倒序），不包含完整报告 JSON。"""
        rows = self.connection.execute(
            """
            SELECT r.id, r.name, r.started_at,
                   COALESCE(sr.generated_at, '') AS generated_at,
                   COALESCE(sr.report_json, '{}') AS report_json
            FROM runs r
            LEFT JOIN sim_reports sr ON sr.run_id = r.id
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for run_id, name, started_at, generated_at, report_json in rows:
            try:
                report = json.loads(report_json) if report_json else {}
            except (json.JSONDecodeError, TypeError):
                report = {}
            summary = report.get("summary", {})
            results.append({
                "runId": run_id,
                "scenarioName": name,
                "startedAt": started_at,
                "generatedAt": generated_at or None,
                "durationStr": summary.get("durationStr", "—"),
                "trainCount": summary.get("trainCount", 0),
                "stationCount": summary.get("stationCount", 0),
                "totalEvents": summary.get("totalEvents", 0),
            })
        return results

    def save_report(self, run_id: int, report: dict[str, Any]) -> None:
        """保存（或覆盖）一次运行的仿真报告。"""
        self.connection.execute(
            """
            INSERT INTO sim_reports(run_id, generated_at, report_json)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                generated_at=excluded.generated_at,
                report_json=excluded.report_json
            """,
            (
                run_id,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(report, ensure_ascii=False),
            ),
        )
        self._commit_if_needed()

    def get_report(self, run_id: int) -> dict[str, Any] | None:
        """读取一次运行的仿真报告，不存在返回 None。"""
        row = self.connection.execute(
            "SELECT report_json FROM sim_reports WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])
