import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from radar.db import connection as db


@pytest.fixture()
def conn():
    c = db.get_connection(":memory:")
    db.init_db(c)
    yield c
    c.close()
