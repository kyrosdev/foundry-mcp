from unittest import TestCase
from unittest.mock import Mock, patch

from test_workbench import ROOT
from studio_testing import StudioTesting


class StudioTestingTests(TestCase):
    def test_single_player_returns_receipt_from_launch(self):
        testing = StudioTesting(Mock())
        receipt = {"passed": True, "players": 1, "asserted": True}
        with patch.object(testing, "luau", return_value=receipt) as luau:
            self.assertEqual(testing.server_assertion("studio", "return true", 1, 15, single_play=True), receipt)
        luau.assert_called_once()
        self.assertTrue(luau.call_args.args[1]["singlePlay"])

    def test_missing_reply_recovers_same_receipt_without_relaunching(self):
        testing = StudioTesting(Mock())
        receipt = {"passed": False, "players": 1, "asserted": True, "error": "Scenario assertion must return true"}
        with patch.object(testing, "luau", side_effect=[
            ValueError("Target is not reachable"),
            {"state": "running"},
            {"state": "finished", "result": receipt},
        ]) as luau, patch("studio_testing.time.sleep"):
            self.assertEqual(testing.server_assertion("studio", "return false", 1, 15, single_play=True), receipt)
        launch, first_poll, second_poll = luau.call_args_list
        for poll in (first_poll, second_poll):
            self.assertEqual(poll.args[1], {"token": launch.args[1]["token"]})
            self.assertTrue(poll.kwargs["readonly"])

    def test_missing_receipt_propagates_launch_error(self):
        testing = StudioTesting(Mock())
        with patch.object(testing, "luau", side_effect=[
            ValueError("Target is not reachable"),
            {"state": "unavailable"},
        ]), patch("studio_testing.time.sleep"):
            with self.assertRaisesRegex(ValueError, "Target is not reachable"):
                testing.server_assertion("studio", "return true", 1, 15, single_play=True)
