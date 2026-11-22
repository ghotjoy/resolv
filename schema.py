"""Ordered, transactional SQLite schema migrations."""
from pathlib import Path
import sqlite3

MIGRATIONS = Path(__file__).with_name("migrations")


def statements(script):
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise sqlite3.DatabaseError("incomplete migration statement")


def migrate(connection):
    with connection:
        # Serialize startup upgrades, including the version check.
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        versions = [int(file.name[:4]) for file in files]
        if not versions or versions != list(range(1, len(files) + 1)):
            raise sqlite3.DatabaseError("missing or unordered migrations")
        if current > versions[-1]:
            raise sqlite3.DatabaseError("database schema is newer than this application")
        for version, file in zip(versions, files):
            if version <= current:
                continue
            if version == 2 and not any(column[1] == "id" for column in connection.execute("PRAGMA table_info(audit)")):
                connection.execute("ALTER TABLE audit RENAME TO audit_legacy")
                connection.execute("""CREATE TABLE audit(id INTEGER PRIMARY KEY,
                    ts TEXT,user TEXT,action TEXT,inc TEXT,prev TEXT,new TEXT,descr TEXT)""")
                connection.execute("INSERT INTO audit SELECT rowid, * FROM audit_legacy")
                connection.execute("DROP TABLE audit_legacy")
            for statement in statements(file.read_text()):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={version}")
