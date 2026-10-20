import unittest
from src.command_guards import is_destructive_adb, is_destructive_fastboot, is_valid_partition_name

class AdbGuardTests(unittest.TestCase):
    def test_destructive_adb_commands_are_flagged(self):
        cases = (
            ["adb", "reboot", "bootloader"], ["adb", "sideload", "ota.zip"],
            ["adb", "root"], ["adb", "remount"], ["adb", "uninstall", "com.example.app"],
            ["adb", "-s", "serial", "shell", "rm", "-rf", "/data"],
            ["adb", "shell", "pm", "uninstall", "com.example.app"],
            ["shell", "setprop", "ro.debuggable", "1"],
        )
        for command in cases:
            with self.subTest(command=command):
                self.assertTrue(is_destructive_adb(command))

    def test_safe_adb_commands_are_not_flagged(self):
        for command in (["adb", "devices", "-l"], ["adb", "version"],
                        ["adb", "shell", "getprop", "ro.build.version.release"],
                        ["shell", "ls", "/sdcard"]):
            with self.subTest(command=command):
                self.assertFalse(is_destructive_adb(command))

class FastbootGuardTests(unittest.TestCase):
    def test_destructive_fastboot_commands_are_flagged(self):
        cases = (["flash", "boot", "boot.img"], ["-w"], ["flashall"], ["update", "update.zip"],
                 ["oem", "unlock"], ["flashing", "unlock_critical"],
                 ["-s", "serial", "create-logical-partition", "product", "1024"],
                 ["erase", "userdata"], ["delete-logical-partition", "product"])
        for command in cases:
            with self.subTest(command=command):
                self.assertTrue(is_destructive_fastboot(command))

    def test_safe_fastboot_commands_are_not_flagged(self):
        for command in (["devices"], ["getvar", "product"], ["-s", "serial", "getvar", "all"]):
            with self.subTest(command=command):
                self.assertFalse(is_destructive_fastboot(command))

class PartitionNameTests(unittest.TestCase):
    def test_partition_names_require_alphanumeric_first_character(self):
        self.assertTrue(is_valid_partition_name("boot_a"))
        self.assertTrue(is_valid_partition_name("vendor.img"))
        self.assertFalse(is_valid_partition_name("-userdata"))
        self.assertFalse(is_valid_partition_name("."))
        self.assertFalse(is_valid_partition_name(""))
