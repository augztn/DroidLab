import json
import tempfile
import unittest
from pathlib import Path

from src.debloater import (
    classify_package,
    is_core_protected,
    parse_devices,
    parse_package_list,
    read_package_selection,
)


class DebloaterParsingTests(unittest.TestCase):
    def test_parse_devices_with_states_and_attributes(self):
        devices = parse_devices(
            "List of devices attached\n"
            "R58M123456A device product:a54x model:SM_A546B device:a54x transport_id:2\n"
            "emulator-5554 unauthorized\n"
            "offline-serial offline\n"
        )
        self.assertEqual([d["serial"] for d in devices], ["R58M123456A", "emulator-5554", "offline-serial"])
        self.assertEqual(devices[0]["state"], "device")
        self.assertEqual(devices[0]["model"], "SM A546B")
        self.assertEqual(devices[0]["transport_id"], "2")
        self.assertEqual(devices[1]["state"], "unauthorized")

    def test_parse_package_list_handles_paths_and_plain_names(self):
        packages = parse_package_list(
            "package:/system/priv-app/SystemUI/SystemUI.apk=com.android.systemui\n"
            "package:/data/app/~~abc/base.apk=com.example.reader\n"
            "package:com.example.plain\n"
            "not-a-package-line\n"
        )
        self.assertEqual(packages["com.android.systemui"], "/system/priv-app/SystemUI/SystemUI.apk")
        self.assertEqual(packages["com.example.reader"], "/data/app/~~abc/base.apk")
        self.assertEqual(packages["com.example.plain"], "")

    def test_core_protection_and_conservative_unknown_classification(self):
        self.assertTrue(is_core_protected("com.android.systemui"))
        self.assertTrue(is_core_protected("com.android.providers.settings"))
        self.assertFalse(is_core_protected("com.samsung.android.oneconnect"))
        self.assertEqual(classify_package("com.android.systemui", "System")[0], "PROTECTED")
        self.assertEqual(classify_package("com.example.app", "System")[0], "UNKNOWN")
        self.assertEqual(classify_package("com.example.app", "User")[0], "USER APP")

    def test_read_package_selection_json_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "selection.json"
            json_path.write_text(json.dumps({"format": "droidlab-selection-v1",
                                             "packages": ["com.example.app", "com.android.systemui"]}),
                                 encoding="utf-8")
            self.assertEqual(read_package_selection(str(json_path)),
                             ["com.android.systemui", "com.example.app"])
            text_path = Path(tmp) / "selection.txt"
            text_path.write_text("# comment\ncom.example.app\ninvalid name\n", encoding="utf-8")
            self.assertEqual(read_package_selection(str(text_path)), ["com.example.app"])


if __name__ == "__main__":
    unittest.main()
