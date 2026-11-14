import unittest

from test_workbench import ROOT
from impact import impact


class ImpactTests(unittest.TestCase):
    def test_relative_chain_without_target_name_and_skipped_tokens(self):
        source = "-- require(game.Folder)\nlocal a = 123\nreturn script.Parent"
        tree = {"children": [{"name": "Folder", "children": [
            {"name": "Reader", "source": source},
            {"name": "Other", "source": "return 123"},
            {"name": "Dynamic", "source": "return require(getModule())"},
        ]}]}
        result = impact({"tree": tree}, ("Folder",), 10, 2)
        self.assertEqual(result["literal_references"], [
            {"path": ("Folder", "Reader"), "line": 3, "excerpt": "return script.Parent"},
        ])
        self.assertEqual(result["unresolved_requires"], 1)

    def test_duplicate_actual_target_is_rejected(self):
        tree = {"children": [{"name": "Target"}, {"name": "Alias", "actualName": "Target"}]}
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            impact({"tree": tree}, ("Target",), 10, 2)
