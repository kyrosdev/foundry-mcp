import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "enhanced"))
from stable_update import REQUIRED, REPO, stage, update_lock


class StableUpdateTests(unittest.TestCase):
    def package(self, extra=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name in REQUIRED | set(extra or []):
                archive.writestr("Foundry-MCP/" + name, b"fixture")
        return stream.getvalue()

    def release(self, data):
        name = "Foundry-MCP-1.1.1-windows-x64.zip"
        return {"tag_name": "v1.1.1", "assets": [{
            "name": name,
            "browser_download_url": f"https://github.com/{REPO}/releases/download/v1.1.1/{name}",
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data),
        }]}

    def test_stage_then_skip_existing_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = self.package()
            result = stage(root, "1.1.0", self.release(data), lambda url, size: data)
            self.assertEqual(result["status"], "staged")
            self.assertEqual((root / ".foundry-updates/active").read_text(), "1.1.1")
            self.assertTrue((root / ".foundry-updates/1.1.1/enhanced/server.py").is_file())
            result = stage(root, "1.1.0", self.release(data), lambda *_: self.fail("Downloaded twice"))
            self.assertEqual(result["status"], "up_to_date")

    def test_hash_and_archive_traversal_rejected_before_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = self.package()
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                stage(root, "1.1.0", self.release(data), lambda *_: data[::-1])
            bad = self.package(["../../outside"])
            with self.assertRaisesRegex(ValueError, "Unexpected path"):
                stage(root, "1.1.0", self.release(bad), lambda *_: bad)
            self.assertFalse((root / ".foundry-updates/active").exists())

    def test_update_lock_is_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check"
            with update_lock(path) as first:
                self.assertTrue(first)
                with update_lock(path) as second:
                    self.assertFalse(second)
            with update_lock(path) as reopened:
                self.assertTrue(reopened)
