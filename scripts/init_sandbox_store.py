"""
init_sandbox_store.py -- called by _initialize_store during sandbox create.
Creates all tables in the store at ALEMS_STORE (or --path argument).
Must be run from the engine root with ALEMS_STORE set.
"""
import sys
import argparse

sys.path.insert(0, ".")

from core.database.sqlite_adapter import SQLiteAdapter


def main():
    # type: () -> None
    parser = argparse.ArgumentParser(description="Initialize sandbox store tables")
    parser.add_argument("--path", required=True, help="Absolute path to store file")
    args = parser.parse_args()

    db = SQLiteAdapter({"path": args.path})
    db.connect()
    db.create_tables()
    print(f"tables created: {args.path}")


if __name__ == "__main__":
    main()
