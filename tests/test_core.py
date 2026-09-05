import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop" / "src"))

from rmos.core import fingerprint_tree, parse_selected, safe_name, validate_uuid

UUID = "550e8400-e29b-41d4-a716-446655440000"


class CoreTests(unittest.TestCase):
    def test_uuid(self):
        self.assertEqual(validate_uuid(UUID.upper()), UUID)
        with self.assertRaises(ValueError):
            validate_uuid("../../bad")

    def test_selected(self):
        text = f"# comment\n{UUID}\n\n{UUID}\n"
        self.assertEqual(parse_selected(text), [UUID])

    def test_safe_name(self):
        self.assertEqual(safe_name('Project: A/B', UUID), 'Project_ A_B')

    def test_fingerprint_is_deterministic_and_content_sensitive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a").write_text("one")
            first = fingerprint_tree(root)
            second = fingerprint_tree(root)
            self.assertEqual(first, second)
            (root / "a").write_text("two")
            self.assertNotEqual(first, fingerprint_tree(root))


if __name__ == "__main__":
    unittest.main()
