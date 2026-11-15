import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'production/sleep_fix.py'
spec = importlib.util.spec_from_file_location('sleep_fix', SOURCE)
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)


class FakeGPE:
    def __init__(self, masked=False, fail=False):
        self.is_masked = masked
        self.fail = fail
        self.writes = []

    def read_text(self):
        return '1 enabled ' + ('masked' if self.is_masked else 'unmasked')

    def write_text(self, value):
        self.writes.append(value)
        if self.fail:
            raise OSError('simulated rejected mask')
        self.is_masked = value.strip() == 'mask'


class MaskRestorationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.gpe = FakeGPE()
        self.patches = [patch.object(fix, 'RUN', self.directory),
                        patch.object(fix, 'STATE', self.directory / 'pending.json'),
                        patch.object(fix, 'RECEIPT', self.directory / 'last-cycle.json'),
                        patch.object(fix, 'GPE', self.gpe),
                        patch.object(fix, 'EC', FakeGPE()),
                        patch.object(fix, 'boot_id', return_value='same-boot'),
                        patch.object(fix, 'pmc', return_value=500),
                        patch.object(fix, 'dsdt_hash', return_value='original')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def pending(self, originally_masked):
        fix.atomic_json(fix.STATE, {'boot_id': 'same-boot', 'originally_masked': originally_masked,
                                    'pmc_before_us': 100, 'dsdt_sha256': 'original'})

    def test_cancelled_or_failed_suspend_restores_owned_mask(self):
        self.pending(False)
        self.gpe.is_masked = True
        self.assertTrue(fix.restore())
        self.assertFalse(self.gpe.is_masked)
        self.assertFalse(fix.STATE.exists())
        self.assertTrue(json.loads(fix.RECEIPT.read_text())['mask_state_restored'])

    def test_preserves_preexisting_mask(self):
        self.pending(True)
        self.gpe.is_masked = True
        fix.restore()
        self.assertTrue(self.gpe.is_masked)
        self.assertEqual(self.gpe.writes, [])

    def test_repeated_cleanup_does_not_change_unowned_state(self):
        self.pending(False)
        self.gpe.is_masked = True
        fix.restore()
        self.gpe.writes.clear()
        self.assertFalse(fix.restore())
        self.assertEqual(self.gpe.writes, [])

    def test_previous_boot_state_cannot_unmask_current_policy(self):
        self.pending(False)
        with patch.object(fix, 'boot_id', return_value='new-boot'):
            self.gpe.is_masked = True
            self.assertFalse(fix.restore())
        self.assertTrue(self.gpe.is_masked)


if __name__ == '__main__':
    unittest.main()
