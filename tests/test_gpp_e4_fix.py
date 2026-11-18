import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gpp_e4_fix', ROOT / 'fix/gpp_e4_fix.py')
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)


class GppE4OwnershipTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.owner = Path(directory) / 'owner'
        self.enterContext(patch.object(fix, 'OWNER', self.owner))
        self.access = self.enterContext(patch.object(fix, 'access', side_effect=self.register_access))
        self.register = 0x10010

    def register_access(self, mode, _payloads):
        before = self.register
        if mode == 'disable':
            self.register &= ~fix.MASK
        elif mode == 'restore':
            self.register |= fix.MASK
        return {'before': before, 'after': self.register}

    def test_sleep_pause_resume_and_stop_restore_only_e4(self):
        fix.change('apply', {})
        self.assertEqual(self.register, 0x10000)
        self.assertTrue(self.owner.exists())
        fix.change('pause', {})
        self.assertEqual(self.register, 0x10010)
        self.assertTrue(self.owner.exists())
        fix.change('resume', {})
        self.assertEqual(self.register, 0x10000)
        self.register |= 0x80  # Another owner changed a different event.
        fix.change('restore', {})
        self.assertEqual(self.register, 0x10090)
        self.assertFalse(self.owner.exists())

    def test_already_disabled_e4_stays_unowned(self):
        self.register = 0x10000
        fix.change('apply', {})
        for action in ('pause', 'resume', 'restore'):
            self.assertIsNone(fix.change(action, {}))
        self.access.assert_called_once_with('read', {})
        self.assertFalse(self.owner.exists())

    def test_lost_readback_can_still_restore_owned_bit(self):
        def lose_readback(mode, payloads):
            result = self.register_access(mode, payloads)
            if mode == 'disable':
                raise RuntimeError('lost readback')
            return result

        self.access.side_effect = lose_readback
        with self.assertRaisesRegex(RuntimeError, 'lost readback'):
            fix.change('apply', {})
        self.assertTrue(self.owner.exists())
        self.assertEqual(self.register, 0x10000)
        fix.change('restore', {})
        self.assertEqual(self.register, 0x10010)
        self.assertFalse(self.owner.exists())

    def test_previous_boot_state_cannot_restore_bit(self):
        self.register = 0x10000
        self.owner.write_text('previous boot\n')
        self.assertIsNone(fix.change('restore', {}))
        self.access.assert_not_called()
        self.assertEqual(self.register, 0x10000)
        self.assertFalse(self.owner.exists())


if __name__ == '__main__':
    unittest.main()
