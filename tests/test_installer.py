import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install_sleep_fix.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lib = self.root / 'lib'
        self.hook = self.root / 'hook'
        self.dropin = self.root / 'dropin'
        self.record = self.root / 'record.json'
        self.files = {self.lib / 'sleep_fix.py': (b'code', 0o755),
                      self.lib / 'SSDT-CHRG.aml': (b'aml', 0o644),
                      self.hook: (b'hook', 0o755), self.dropin: (b'dropin', 0o644)}
        self.patches = [patch.object(installer, 'LIB', self.lib),
                        patch.object(installer, 'HOOK', self.hook),
                        patch.object(installer, 'DROPIN', self.dropin),
                        patch.object(installer, 'RECORD', self.record)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    @staticmethod
    def write_local(path, data, mode):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)

    def test_rejects_changed_source_before_privileged_install(self):
        clone = self.root / 'clone'
        for name in ('acpi/manifest.json', 'acpi/SSDT-CHRG.dsl',
                     'fix/sleep_fix.py', 'build/acpi/SSDT-CHRG.aml'):
            target = clone / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        code = clone / 'fix/sleep_fix.py'
        code.write_bytes(code.read_bytes() + b'\n# changed\n')
        with patch.object(installer, 'ROOT', clone):
            with self.assertRaisesRegex(RuntimeError, 'hardware-verified revision'):
                installer.payloads()
        self.assertFalse(self.record.exists())

    def test_existing_customization_is_never_overwritten(self):
        self.hook.write_bytes(b'unrelated customization')
        with patch.object(installer, 'payloads', return_value=self.files), \
                patch.object(installer, 'preflight') as preflight:
            with self.assertRaisesRegex(RuntimeError, 'target path already exists'):
                installer.install()
        self.assertEqual(self.hook.read_bytes(), b'unrelated customization')
        preflight.assert_not_called()

    def test_failed_selfcheck_removes_partial_install_and_restores_mask(self):
        module = Mock()
        module.prepare.side_effect = RuntimeError('injected self-check failure')
        with patch.object(installer, 'payloads', return_value=self.files), \
                patch.object(installer, 'preflight', return_value={'dsdt_sha256': 'native'}), \
                patch.object(installer, 'write_file', side_effect=self.write_local), \
                patch.object(installer, 'load_module', return_value=module), \
                patch.object(installer.subprocess, 'run'):
            with self.assertRaisesRegex(RuntimeError, 'injected self-check failure'):
                installer.install()
        module.restore.assert_called()
        self.assertFalse(any(p.exists() for p in self.files))
        self.assertFalse(self.record.exists())

    def test_modified_installation_is_not_executed_or_removed(self):
        entries = {}
        for path, (data, mode) in self.files.items():
            self.write_local(path, data, mode)
            entries[str(path)] = {'sha256': installer.sha(data), 'previous_file': None}
        self.record.write_text(json.dumps({'files': entries}))
        (self.lib / 'sleep_fix.py').write_bytes(b'modified locally')
        with patch.object(installer, 'load_module') as load_module:
            with self.assertRaisesRegex(RuntimeError, 'missing or changed'):
                installer.remove()
        load_module.assert_not_called()
        self.assertTrue(all(p.exists() for p in self.files))
        self.assertTrue(self.record.exists())


if __name__ == '__main__':
    unittest.main()
