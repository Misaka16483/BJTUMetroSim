from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "bj-metro-sim" / "src"


class FrontendLifecycleVisibilityContractTests(unittest.TestCase):
    def test_live_lifecycle_controls_are_in_an_unhidden_header_group(self) -> None:
        source = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
        header_start = source.index("<header")
        header_end = source.index("</header>", header_start)
        header = source[header_start:header_end]
        control_position = header.index("<SimulationLifecycleControls")
        parent_start = header.rfind("<div", 0, control_position)
        parent_end = header.index(">", parent_start)
        parent_tag = header[parent_start : parent_end + 1]
        visibility_guard = header[max(0, control_position - 240) : control_position]

        self.assertIn('className="flex shrink-0 items-center gap-1.5"', parent_tag)
        self.assertNotIn("hidden", parent_tag)
        self.assertIn("backendStatus === 'connected'", visibility_guard)
        self.assertIn("dataMode === 'LIVE_SIM'", visibility_guard)

    def test_start_button_calls_the_real_backend_start_endpoint(self) -> None:
        controls = (FRONTEND / "components" / "ControlPanel.tsx").read_text(
            encoding="utf-8"
        )
        store = (FRONTEND / "store" / "useSimStore.ts").read_text(encoding="utf-8")
        api = (FRONTEND / "data" / "backendApi.ts").read_text(encoding="utf-8")
        click_position = controls.index("onClick={beginStart}")
        button_start = controls.rfind("<button", 0, click_position)
        button_end = controls.index(">", click_position)
        button_tag = controls[button_start : button_end + 1]

        self.assertNotIn("hidden", button_tag)
        self.assertIn("await startBackendSim();", controls)
        self.assertIn("await simStart();", store)
        self.assertIn("return postJson('/api/sim/start');", api)

    def test_auto_dispatch_panel_isolated_from_non_plan_dispatch_state(self) -> None:
        panel = (FRONTEND / "components" / "AutoDispatchPanel.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn("const plannedTrainIds", panel)
        self.assertIn(".filter((departure) => plannedTrainIds.has(departure.trainId))", panel)
        self.assertIn("const planDepartureCount = planEnabled", panel)
        self.assertIn("? '当前场景未启用运行图'", panel)
        self.assertIn("planEnabled && engineClockState === 'RUNNING'", panel)
        self.assertIn("departure.simTimeMs", panel)

    def test_passenger_history_resets_at_backend_stream_boundary(self) -> None:
        passenger = (FRONTEND / "components" / "StationPassengerView.tsx").read_text(
            encoding="utf-8"
        )
        store = (FRONTEND / "store" / "useSimStore.ts").read_text(encoding="utf-8")

        self.assertIn("const sessionId = useSimStore", passenger)
        self.assertIn("const streamKey = `${sessionId ?? 'no-session'}:${runId ?? 'no-run'}`", passenger)
        self.assertIn("}, [focus, streamKey]);", passenger)
        self.assertIn("}, [engineClockState, focus, streamKey]);", passenger)
        self.assertIn("key={`${streamKey}:waiting`}", passenger)
        stream_reset = store[store.index("...(streamChanged ? {") :]
        self.assertIn("trainColors: {}", stream_reset)
        self.assertIn("totalPassengers: 0", stream_reset)

    def test_auto_dispatch_queue_has_a_backend_backed_time_editor(self) -> None:
        panel = (FRONTEND / "components" / "AutoDispatchPanel.tsx").read_text(
            encoding="utf-8"
        )
        store = (FRONTEND / "store" / "useSimStore.ts").read_text(encoding="utf-8")
        api = (FRONTEND / "data" / "backendApi.ts").read_text(encoding="utf-8")

        self.assertIn('type="time"', panel)
        self.assertIn("onInput={(event) => setDraftStartTime(event.currentTarget.value)}", panel)
        self.assertIn("await rescheduleAutoDispatchDuty(editingDutyId, plannedStartS);", panel)
        self.assertIn("engineClockState === 'RUNNING'", panel)
        self.assertIn("['IN_DEPOT', 'READY'].includes(duty.lifecycleState)", panel)
        self.assertIn("simRescheduleAutoDispatchDuty(dutyId, plannedStartS)", store)
        self.assertIn("set({ operationPlan: result.operationPlan });", store)
        self.assertIn("/api/sim/auto-dispatch/queue/reschedule", api)


if __name__ == "__main__":
    unittest.main()
