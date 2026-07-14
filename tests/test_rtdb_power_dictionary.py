from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.infra.rtdb_power_dictionary import (
    RtdbPowerDictionary,
    audit_table_definition,
    build_power_point_contracts,
    display_point_to_transport_row,
    power_point_contract_document,
    power_point_contract_sha256,
    read_table_definition,
    transport_row_to_display_point,
)


ROOT = Path(__file__).resolve().parents[1]


class RtdbPowerDictionaryTests(unittest.TestCase):
    def test_point_number_and_transport_row_use_explicit_bases(self) -> None:
        self.assertEqual(display_point_to_transport_row(1), 0)
        self.assertEqual(display_point_to_transport_row(1000), 999)
        self.assertEqual(display_point_to_transport_row(1470), 1469)
        self.assertEqual(transport_row_to_display_point(0), 1)
        self.assertEqual(transport_row_to_display_point(1422), 1423)
        with self.assertRaises(ValueError):
            display_point_to_transport_row(0)
        with self.assertRaises(ValueError):
            transport_row_to_display_point(-1)

    def test_parser_reads_headerless_gbk_definition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.csv"
            path.write_bytes("1,,站网压,825,0,0,0,,V\r\n".encode("gbk"))
            rows = read_table_definition(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].point_no, 1)
        self.assertEqual(rows[0].transport_row_index, 0)
        self.assertEqual(rows[0].name, "站网压")
        self.assertEqual(rows[0].unit, "V")

    def test_dictionary_normalizes_speed_distance_force_and_regen_sign(self) -> None:
        dictionary = RtdbPowerDictionary()
        speed = dictionary.decode(1232, 1234.0)
        mileage = dictionary.decode(1233, 1020000.0)
        brake_force = dictionary.decode(1369, 234.352)
        regen = dictionary.decode(1422, -3.66346)
        self.assertEqual(speed.display_point_no, 1233)
        self.assertAlmostEqual(speed.value, 12.34)
        self.assertAlmostEqual(mileage.value, 10200.0)
        self.assertAlmostEqual(brake_force.value, 234352.0)
        self.assertAlmostEqual(regen.value, 3.66346)
        self.assertEqual(regen.canonical_field, "regenPowerAvailableKw")

    def test_contract_has_no_duplicate_point_or_transport_index(self) -> None:
        contracts = build_power_point_contracts()
        self.assertEqual(len({item.point_no for item in contracts}), len(contracts))
        self.assertEqual(len({item.transport_row_index for item in contracts}), len(contracts))
        self.assertGreaterEqual(len(contracts), 200)
        document = power_point_contract_document()
        self.assertEqual(document["displayIndexBase"], 1)
        self.assertEqual(document["transportIndexBase"], 0)
        self.assertFalse(document["writeEnabled"])
        self.assertEqual(len(power_point_contract_sha256()), 64)

    def test_teacher_definition_passes_contract_audit_when_available(self) -> None:
        source = ROOT / "188_2.tableData-1(1).csv"
        if not source.exists():
            self.skipTest("teacher definition is an external read-only artifact")
        report = audit_table_definition(source)
        self.assertTrue(report.passed, report.errors)
        self.assertEqual(report.row_count, 5000)
        self.assertEqual(report.power_range_count, 471)
        self.assertGreaterEqual(report.mapped_point_count, 200)
        self.assertLess(report.station_power_identity_max_error_ratio, 0.01)


if __name__ == "__main__":
    unittest.main()
