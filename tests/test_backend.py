import hashlib
import tempfile
import unittest
from pathlib import Path

from src.payload_dumper import BackendError, sha256_file


class BackendTests(unittest.TestCase):
    def test_sha256_matches_known_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.img"
            path.write_bytes(b"payload")
            self.assertEqual(sha256_file(str(path)), hashlib.sha256(b"payload").hexdigest())

    def test_missing_input_is_reported(self):
        from src.payload_dumper import extract
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(BackendError):
                extract(str(Path(temp) / "missing.bin"), temp)


if __name__ == "__main__":
    unittest.main()
