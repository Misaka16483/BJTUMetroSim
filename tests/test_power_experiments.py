from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from app.api_server import Line9DataService, build_server
from app.domain.power.experiments import (
    PowerExperimentRegistry,
    PowerExperimentRequest,
    PowerExperimentRunner,
    SUPPORTED_PROBLEMS,
)


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"


class PowerExperimentTests(unittest.TestCase):
    def test_all_four_problems_produce_feasible_improving_candidates(self) -> None:
        runner = PowerExperimentRunner(TOPOLOGY)
        for index, problem in enumerate(sorted(SUPPORTED_PROBLEMS), start=1):
            with self.subTest(problem=problem):
                request = PowerExperimentRequest.from_dict({
                    "problem": problem,
                    "populationSize": 4,
                    "generations": 1,
                    "timeSlots": 8,
                    "seed": 20260711,
                })
                result = runner.run(request, experiment_id=f"TEST-{index}")
                self.assertEqual(result["status"], "COMPLETED")
                self.assertTrue(result["bestTrial"]["feasible"])
                self.assertLessEqual(result["bestTrial"]["score"], result["baseline"]["score"])
                self.assertLess(result["bestTrial"]["metrics"]["maxBalanceErrorRatio"], 0.01)
                self.assertGreaterEqual(result["trialCount"], 4)

    def test_same_seed_is_deterministic(self) -> None:
        runner = PowerExperimentRunner(TOPOLOGY)
        request = PowerExperimentRequest.from_dict({
            "problem": "REGEN_MATCHING",
            "populationSize": 5,
            "generations": 2,
            "timeSlots": 8,
            "seed": 42,
        })
        first = runner.run(request, experiment_id="FIRST")
        second = runner.run(request, experiment_id="SECOND")
        self.assertEqual(first["bestTrial"]["candidate"], second["bestTrial"]["candidate"])
        self.assertAlmostEqual(first["bestTrial"]["score"], second["bestTrial"]["score"], places=9)

    def test_registry_persists_summary_and_trials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            registry = PowerExperimentRegistry(TOPOLOGY, Path(temporary_dir) / "experiments.sqlite")
            try:
                created = registry.create({
                    "problem": "TRACTION_STAGGER",
                    "populationSize": 4,
                    "generations": 1,
                    "timeSlots": 8,
                })
                summary = registry.get(created["experimentId"])
                detail = registry.get(created["experimentId"], include_trials=True)
                self.assertEqual(len(registry.list()), 1)
                self.assertNotIn("trials", summary)
                self.assertEqual(len(detail["trials"]), created["trialCount"])
            finally:
                registry.close()

    def test_http_api_creates_and_reads_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            registry = PowerExperimentRegistry(TOPOLOGY, Path(temporary_dir) / "api-experiments.sqlite")
            service = Line9DataService(run_dir=Path(temporary_dir))
            server = build_server("127.0.0.1", 0, service)
            server.RequestHandlerClass.experiment_registry = registry
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                ssl_keylog = os.environ.pop("SSLKEYLOGFILE", None)
                host, port = server.server_address
                body = json.dumps({
                    "problem": "EFS_CAPACITY",
                    "populationSize": 4,
                    "generations": 1,
                    "timeSlots": 8,
                }).encode("utf-8")
                request = Request(
                    f"http://{host}:{port}/api/power/experiments",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=15) as response:
                    created = json.load(response)
                experiment_id = created["data"]["experimentId"]
                with urlopen(f"http://{host}:{port}/api/power/experiments/{experiment_id}", timeout=15) as response:
                    fetched = json.load(response)
                self.assertTrue(created["ok"])
                self.assertEqual(fetched["data"]["experimentId"], experiment_id)
            finally:
                if ssl_keylog is not None:
                    os.environ["SSLKEYLOGFILE"] = ssl_keylog
                server.shutdown()
                server.server_close()
                registry.close()


if __name__ == "__main__":
    unittest.main()
