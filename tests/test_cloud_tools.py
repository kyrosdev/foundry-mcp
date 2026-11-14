import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "enhanced"))
from workbench import Workbench


class CloudToolsTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.workspace = Path(folder)
        self.wb = Workbench(None, folder, folder)
        self.cloud = self.wb.workflows.cloud_tools

    def test_publish_verifies_preview_and_records_version(self):
        file = self.workspace / "qa.rbxlx"
        file.write_bytes(b"<roblox/>")
        args = {"place_id":"2","file":file.name,"action":"prepare"}
        plan = self.cloud.publish("window", 1, args)
        file.write_bytes(b"<changed/>")
        apply = {**args,"action":"apply","plan_id":plan["plan_id"]}
        with patch("cloud_tools.api_key_request", return_value={"versionNumber":4}) as request:
            with self.assertRaisesRegex(ValueError, "changed since preview"):
                self.cloud.publish("window", 1, apply)
            request.assert_not_called()
            file.write_bytes(b"<roblox/>")
            committed = self.cloud.publish("window", 1, apply)
            self.assertEqual(committed["versionNumber"], 4)
            request.assert_called_once_with(
                "/universes/v1/1/places/2/versions?versionType=Published", b"<roblox/>", "application/xml"
            )
            with self.assertRaisesRegex(ValueError, "not ready"):
                self.cloud.publish("window", 1, apply)

    def test_datastore_update_keeps_etag_and_metadata(self):
        entry = {"etag":"original","value":5,"users":["users/1"],"attributes":{"marker":True}}
        with patch("cloud_tools.api_key_request", side_effect=[entry,{"value":7}]) as request:
            plan = self.cloud.datastore("window", 42, {"action":"prepare","store":"QA","key":"probe","value":7,"transport":"api_key"})
            result = self.cloud.datastore("window", 42, {"action":"apply","plan_id":plan["plan_id"]})
        self.assertEqual(result["state"], "committed")
        endpoint, body, content_type, method = request.call_args.args
        self.assertEqual(endpoint, "/cloud/v2/universes/42/data-stores/QA/entries/probe")
        self.assertEqual(json.loads(body), {**entry,"value":7})
        self.assertEqual((content_type,method), ("application/json","PATCH"))

    def test_cloud_luau_rejects_a_task_for_another_place(self):
        with self.assertRaisesRegex(ValueError, "selected place"):
            self.cloud.luau("window", 42, {"action":"logs","place_id":"7",
                "task_path":"universes/42/places/8/versions/1/luau-execution-sessions/id/tasks/id"})

    def test_restore_checks_exact_published_revision_and_status_never_reposts(self):
        with self.wb.store.connect() as db:
            db.executemany("INSERT INTO release_checks VALUES(?,?,?,?,?)",
                           [("42","7","9","old",0),("42","7","10","valid",1),("42","8","20","other",2)])
        metadata = {"path":"assets/7","assetType":"Place","revisionId":"12"}
        with patch.object(self.wb.studio_publish,"target",return_value=(None,"42")), \
             patch.object(self.wb.studio_publish,"published_version",side_effect=[12,12,12,13]), \
             patch.object(self.wb.workflows,"cloud",side_effect=[
                 metadata,{"path":"assets/7/versions/10"},{"path":"assets/7/versions/13"}]) as cloud:
            receipt = self.cloud.publish("window",42,{"place_id":"7","action":"restore_latest"})
            self.assertEqual(receipt["state"],"awaiting_verification")
            self.assertTrue(receipt["blocked"])
            result = self.cloud.publish("window",42,{"place_id":"7","action":"restore_status",
                                                      "plan_id":receipt["plan_id"]})
            self.assertEqual((result["restored_from"],result["published_version"]),(10,13))
            self.assertEqual(cloud.call_count,3)
            self.assertEqual(cloud.call_args.args[1:],
                ("POST","/assets/v1/assets/7/versions:rollback",["asset:read","asset:write"],
                 {"assetVersion":"assets/7/versions/10"}))


if __name__ == "__main__":
    unittest.main()
