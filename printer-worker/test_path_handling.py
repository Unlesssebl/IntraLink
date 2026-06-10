import sys
import os
import unittest

# Добавляем путь printer-worker в pythonpath
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from executors.smb_executor import smb_executor

class TestSMBPathHandling(unittest.TestCase):
    def test_parse_directory_path(self):
        inf_path = r"\\truenas\Drivers\printer\Kyocera\M8124cidn\64bit"
        src_dir, dest_subdir, inf_filename = smb_executor.parse_driver_path(inf_path)
        
        self.assertEqual(src_dir, r"\\truenas\Drivers\printer\Kyocera\M8124cidn\64bit")
        self.assertEqual(dest_subdir, "64bit")
        self.assertEqual(inf_filename, "*.inf")

    def test_parse_directory_path_trailing_slash(self):
        inf_path = r"\\truenas\Drivers\printer\Kyocera\M8124cidn\64bit\\"
        src_dir, dest_subdir, inf_filename = smb_executor.parse_driver_path(inf_path)
        
        self.assertEqual(src_dir, r"\\truenas\Drivers\printer\Kyocera\M8124cidn\64bit")
        self.assertEqual(dest_subdir, "64bit")
        self.assertEqual(inf_filename, "*.inf")

    def test_parse_inf_file_path(self):
        inf_path = r"\\corp-share\drivers\hp_m428\hpbuio200l.inf"
        src_dir, dest_subdir, inf_filename = smb_executor.parse_driver_path(inf_path)
        
        self.assertEqual(src_dir, r"\\corp-share\drivers\hp_m428")
        self.assertEqual(dest_subdir, "hp_m428")
        self.assertEqual(inf_filename, "hpbuio200l.inf")

    def test_parse_inf_file_path_mixed_case(self):
        inf_path = r"\\corp-share\drivers\Xerox_B210\x2B210.INF"
        src_dir, dest_subdir, inf_filename = smb_executor.parse_driver_path(inf_path)
        
        self.assertEqual(src_dir, r"\\corp-share\drivers\Xerox_B210")
        self.assertEqual(dest_subdir, "Xerox_B210")
        self.assertEqual(inf_filename, "x2B210.INF")

if __name__ == "__main__":
    unittest.main()
