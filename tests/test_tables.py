import importlib.util
from pathlib import Path
import struct
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/build_tables.py'
spec = importlib.util.spec_from_file_location('build_tables', SCRIPT)
tables = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tables)

SOURCE = '''DefinitionBlock ("", "DSDT", 2, "TOSHIB", "A010D", 0x20240103)
{
    Method (_Q01, 0, Serialized)  // _Qxx: EC Query, xx=0x00-0xFF
    {
        Notify (ADP1, 0x80)
        Notify (BAT1, 0x81)
        Notify (CHRG, 0x80) // Status Change
    }
}
'''


class RejectionTests(unittest.TestCase):
    def test_missing_or_duplicate_method_rejected(self):
        for source in [SOURCE.replace('_Q01', '_Q02'), SOURCE + SOURCE]:
            with self.assertRaises(ValueError):
                tables.paired_sources(source)

    def test_already_guarded_or_changed_firmware_rejected(self):
        _, guarded, _ = tables.paired_sources(SOURCE)
        for source in [SOURCE.replace('Notify (CHRG', 'Notify (CHG2'),
                       SOURCE.replace('Notify (CHRG, 0x80)', 'Notify (CHRG, 0x81)'), guarded]:
            with self.assertRaises(ValueError):
                tables.paired_sources(source)

    def test_truncated_and_invalid_checksums_rejected(self):
        with self.assertRaises(ValueError):
            tables.table_header(b'DSDT')
        bad = struct.pack('<4sIBB6s8sI4sI', b'DSDT', 36, 2, 0, b'TOSHIB', b'A010D   ', 1, b'INTL', 1)
        with self.assertRaises(ValueError):
            tables.table_header(bad)

    def test_control_and_guard_retain_battery_notifications(self):
        control, guard, revision = tables.paired_sources(SOURCE)
        self.assertEqual(revision, 0x20240104)
        for notification in ['Notify (ADP1, 0x80)', 'Notify (BAT1, 0x81)']:
            self.assertEqual(control.count(notification), 1)
            self.assertEqual(guard.count(notification), 1)
        self.assertNotIn('CondRefOf', control)
        self.assertIn('If (CondRefOf (CHRG))', guard)


if __name__ == '__main__':
    unittest.main()
