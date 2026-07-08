from __future__ import annotations

import unittest

from app.main import _pop_key


class VehicleConsoleInputTests(unittest.TestCase):
    def test_pop_key_parses_arrow_up(self) -> None:
        key, remaining = _pop_key("\x1b[Aq")

        self.assertEqual(key, "UP")
        self.assertEqual(remaining, "q")

    def test_pop_key_parses_arrow_down(self) -> None:
        key, remaining = _pop_key("\x1b[B")

        self.assertEqual(key, "DOWN")
        self.assertEqual(remaining, "")

    def test_pop_key_keeps_incomplete_escape_sequence(self) -> None:
        key, remaining = _pop_key("\x1b[")

        self.assertIsNone(key)
        self.assertEqual(remaining, "\x1b[")


if __name__ == "__main__":
    unittest.main()
