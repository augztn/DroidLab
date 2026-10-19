import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from src.payload_dumper import (
    BackendError,
    _bundle_root,
    _find_cli,
    extract,
    inspect_source,
    sha256_file,
)


class BackendTests(unittest.TestCase):
    def test_sha256_matches_known_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.img"
            path.write_bytes(b"payload")
            self.assertEqual(sha256_file(str(path)), hashlib.sha256(b"payload").hexdigest())

    def test_missing_input_is_reported_before_backend_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.bin"
            with patch("src.payload_dumper._find_cli", return_value=None) as find_cli:
                with self.assertRaisesRegex(BackendError, "does not exist"):
                    inspect_source(str(missing))
                find_cli.assert_not_called()

    def test_partition_machine_output_parsed_as_kib(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "payload.bin"
            src.write_bytes(b"stub")
            result = CompletedProcess(
                ["payload-dumper-go"], 0, "boot:65536\nsystem:1024\n", ""
            )
            with patch("src.payload_dumper._run", return_value=result):
                parts = inspect_source(str(src))
        self.assertEqual(
            parts,
            [
                {"name": "boot", "size_bytes": 65536 * 1024, "hash": ""},
                {"name": "system", "size_bytes": 1024 * 1024, "hash": ""},
            ],
        )

    def test_partition_parser_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "payload.bin"
            src.write_bytes(b"stub")
            result = CompletedProcess(
                ["payload-dumper-go"],
                0,
                "header text\nboot:2048\nvendor:not-a-size\n:512\n",
                "",
            )
            with patch("src.payload_dumper._run", return_value=result):
                parts = inspect_source(str(src))
        self.assertEqual(
            parts, [{"name": "boot", "size_bytes": 2048 * 1024, "hash": ""}]
        )

    def test_inspect_source_reports_backend_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "payload.bin"
            src.write_bytes(b"stub")
            result = CompletedProcess(
                ["payload-dumper-go"], 2, "unsupported delta operation", ""
            )
            with patch("src.payload_dumper._run", return_value=result):
                with self.assertRaisesRegex(BackendError, "unsupported delta operation"):
                    inspect_source(str(src))

    def test_extract_rejects_unsupported_extension_before_backend_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "firmware.tar"
            src.write_bytes(b"stub")
            with patch("src.payload_dumper._find_cli", return_value=None) as find_cli:
                with self.assertRaisesRegex(BackendError, "payload .bin file or OTA .zip"):
                    extract(str(src), str(Path(temp) / "out"))
                find_cli.assert_not_called()

    def test_extract_missing_input_is_reported_before_backend_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.bin"
            with patch("src.payload_dumper._find_cli", return_value=None) as find_cli:
                with self.assertRaisesRegex(BackendError, "does not exist"):
                    extract(str(missing), str(Path(temp) / "out"))
                find_cli.assert_not_called()

    def test_extract_reports_missing_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "payload.bin"
            src.write_bytes(b"stub")
            with patch("src.payload_dumper._find_cli", return_value=None):
                with self.assertRaisesRegex(BackendError, "payload-dumper-go was not found"):
                    extract(str(src), str(Path(temp) / "out"))

    def test_extract_reports_backend_exit_code(self):
        class FakeProcess:
            stdout = io.StringIO("backend started\n")
            def wait(self):
                return 3
            def poll(self):
                return 3

        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "payload.bin"
            src.write_bytes(b"stub")
            with patch("src.payload_dumper._find_cli", return_value=["payload-dumper-go"]):
                with patch("src.payload_dumper.subprocess.Popen", return_value=FakeProcess()):
                    with self.assertRaisesRegex(BackendError, "exited with status 3"):
                        extract(str(src), str(Path(temp) / "out"))

    @unittest.skipIf(os.name == "nt", "AppImage path resolution is Linux-specific")
    def test_frozen_appimage_resolves_appdir_root(self):
        with patch("src.payload_dumper.sys.frozen", True, create=True):
            with patch(
                "src.payload_dumper.sys.executable",
                "/tmp/Payload.AppDir/usr/bin/droidlab-gui-bin",
            ):
                self.assertEqual(_bundle_root(), Path("/tmp/Payload.AppDir"))

    def test_development_bundle_root_is_project_root(self):
        with patch("src.payload_dumper.sys.frozen", False, create=True):
            self.assertEqual(_bundle_root(), Path(__file__).resolve().parent.parent)

    def test_cli_lookup_does_not_claim_uninstalled_backend(self):
        with patch("src.payload_dumper.shutil.which", return_value=None):
            with patch("src.payload_dumper.Path.is_file", return_value=False):
                self.assertIsNone(_find_cli())


if __name__ == "__main__":
    unittest.main()
