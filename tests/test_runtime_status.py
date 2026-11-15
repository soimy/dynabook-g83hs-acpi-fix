from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from runtime_ssdt import device_status


class HotRemoveReadTests(unittest.TestCase):
    def test_device_without_status_is_already_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'SYMF0002:00').mkdir()
            self.assertIsNone(device_status('SYMF0002', root))

    def test_live_status_and_absent_device(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = root / 'SYMF0002:00'
            device.mkdir()
            (device / 'status').write_text('15\n')
            self.assertEqual(device_status('SYMF0002', root), 15)
            (device / 'status').unlink()
            device.rmdir()
            self.assertIsNone(device_status('SYMF0002', root))


if __name__ == '__main__':
    unittest.main()
