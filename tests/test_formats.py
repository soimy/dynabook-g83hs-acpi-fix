import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/formats.py'
spec = importlib.util.spec_from_file_location('formats', SCRIPT)
formats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(formats)


class ArchiveIntegrityTests(unittest.TestCase):
    def test_preserves_microcode_and_compressed_initramfs(self):
        original = formats.cpio_record('kernel', mode=0o040755)
        original += formats.cpio_record('kernel/x86/microcode/GenuineIntel.bin', b'microcode bytes')
        original += formats.cpio_record('TRAILER!!!', mode=0)
        original += b'\0' * (-len(original) % 512)
        tail = b'\x28\xb5\x2f\xfd' + bytes(range(256))
        original += tail
        updated, result = formats.insert_acpi(original, b'DSDT payload')
        entries, _, _ = formats.read_cpio(updated)
        self.assertEqual(entries['kernel/x86/microcode/GenuineIntel.bin']['data'], b'microcode bytes')
        self.assertEqual(entries['kernel/firmware/acpi/DSDT.aml']['data'], b'DSDT payload')
        self.assertEqual(updated[result['early_cpio_bytes']:], tail)

    def test_existing_override_is_rejected(self):
        existing, _ = formats.insert_acpi(b'compressed initramfs', b'old table')
        with self.assertRaises(ValueError):
            formats.insert_acpi(existing, b'new table')

    def test_missing_trailer_and_bad_pe_are_rejected(self):
        with self.assertRaises(ValueError):
            formats.read_cpio(formats.cpio_record('x', b'data'))
        for bad in [b'', b'MZ' + b'\0' * 100]:
            with self.assertRaises(ValueError):
                formats.pe_sections(bad)

    def test_creates_early_archive_before_compressed_image(self):
        original = b'\x1f\x8bcompressed payload'
        updated, result = formats.insert_acpi(original, b'new table')
        entries, _, _ = formats.read_cpio(updated)
        self.assertEqual(updated[result['early_cpio_bytes']:], original)
        self.assertEqual(entries['kernel/firmware/acpi']['mode'] & 0o170000, 0o040000)


if __name__ == '__main__':
    unittest.main()
