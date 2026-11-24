# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Point the demo seed at the small fixed portfolio the tests were written against.

Import this module before creating a demo Store. The shipped demo in assets/ has more holdings
and longer history, so exact-value tests would otherwise depend on sample data that may grow.
"""

from pathlib import Path

import storage

FIXTURES = Path(__file__).resolve().parent / "test_fixtures"
storage.SEED_DIR = FIXTURES
