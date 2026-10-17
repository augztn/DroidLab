import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.payload_dumper import BackendError, _bundle_root, _find_cli, inspect_source, sha256_file

class BackendTests(unittest.TestCase):
    def test_sha256_matches_known_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.img"
            path.write_bytes(b"payload")
            self.assertEqual(sha256_file(str(path)), hashlib.sha256(b"payload").hexdigest())

    def test_missing_input_is_reported_before_backend_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.bin"
            with patch("src.payload_dumper._find_cli", return_value=None):
                with self.assertRaisesRegex(BackendError, "does not exist"):
                    inspect_source(str(missing))

    def test_partition_machine_output_parsed_as_kib(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "payload.bin"
            src.write_bytes(b"stub")
            result = CompletedProcess(["payload-dumper-go"], 0, "boot:65536\nsystem:1024\n", "")
            with patch("src.payload_dumper._run", return_value=result):
                parts = inspect_source(str(src))
        self.assertEqual(parts, [
            {"name": "boot", "size_bytes": 65536 * 1024, "hash": ""},
            {"name": "system", "size_bytes": 1024 * 1024, "hash": ""},
        ])

    def test_frozen_appimage_resolves_appdir_root(self):
        with patch("src.payload_dumper.sys.frozen", True, create=True):
            with patch("src.payload_dumper.sys.executable", "/tmp/Payload.AppDir/usr/bin/droidlab-gui-bin"):
                self.assertEqual(_bundle_root(), Path("/tmp/Payload.AppDir"))

    def test_cli_lookup_does_not_claim_uninstalled_backend(self):
        with patch("src.payload_dumper.shutil.which", return_value=None):
            with patch("src.payload_dumper.Path.is_file", return_value=False):
                self.assertIsNone(_find_cli())

if __name__ == "__main__":
    unittest.main()
