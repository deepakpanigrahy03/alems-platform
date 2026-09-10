#!/usr/bin/env python3
"""Initialize DB tables. Idempotent — safe to run on existing DBs."""
from scripts.tools.path_loader import get_alems_db_path
from core.database.sqlite_adapter import SQLiteAdapter

db = SQLiteAdapter({'path': get_alems_db_path()})
db.create_tables()
print("  Tables ready")
