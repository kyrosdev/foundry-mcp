import time
import unittest
from unittest.mock import Mock

from test_workbench import ROOT
from cursors import ViewportCursors


class CursorTransportTests(unittest.TestCase):
    def test_refresh_reuses_renderer_and_recovers_after_edit_reload(self):
        wb = Mock(connection_id="agent")
        ok = {"content": [{"type": "text", "text": "true"}]}
        missing = {"content": [{"type": "text", "text": "false"}]}
        wb.rpc.side_effect = [ok, ok, missing, ok, ok]
        cursors = ViewportCursors(wb)
        agents = [{"id": "agent", "name": "Codex", "active": True}]
        row = {"studio": "studio", "actor": "agent", "tool": "extra_snapshot",
               "started": time.time(), "cursor": {"path": ["Workspace"], "stage": "Start"}}

        cursors.publish([row], [], agents, True)
        row["cursor"]["stage"] = "Continue"
        cursors.publish([row], [], agents, True)
        row["cursor"]["stage"] = "Resume after reload"
        cursors.publish([row], [], agents, True)
        cursors.publish([], [], agents, True)

        codes = [call.args[1]["arguments"]["code"] for call in wb.rpc.call_args_list]
        self.assertEqual(len(codes), 5)
        self.assertIn("loadArt", codes[0])
        self.assertIn("state.update(payload)", codes[1])
        self.assertNotIn("loadArt", codes[1])
        self.assertLess(len(codes[1]), len(codes[0]) // 10)
        self.assertIn("state.update(payload)", codes[2])
        self.assertIn("loadArt", codes[3])
        self.assertNotIn("loadArt", codes[4])
        self.assertFalse(cursors.studios)
