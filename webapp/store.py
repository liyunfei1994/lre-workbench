"""SQLite records for a small, single-host team workspace."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
 password_hash TEXT NOT NULL, role TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS account_security (
 user_id TEXT PRIMARY KEY REFERENCES users(id), password_changed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_limits (
 key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, until_time REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
 revision INTEGER NOT NULL, inputs TEXT NOT NULL,
 updated_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS revisions (
 project_id TEXT NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
 name TEXT NOT NULL, description TEXT NOT NULL, inputs TEXT NOT NULL,
 author TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 PRIMARY KEY(project_id,revision));
CREATE TABLE IF NOT EXISTS design_group_reviews (
 project_id TEXT NOT NULL, revision INTEGER NOT NULL,
 reviews TEXT NOT NULL, updated_by TEXT NOT NULL REFERENCES users(id), updated REAL NOT NULL,
 PRIMARY KEY(project_id,revision),
 FOREIGN KEY(project_id,revision) REFERENCES revisions(project_id,revision) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 revision INTEGER NOT NULL, submitted_by TEXT NOT NULL REFERENCES users(id),
 status TEXT NOT NULL, inputs TEXT NOT NULL, created REAL NOT NULL,
 started REAL, finished REAL, progress TEXT, result TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created);
CREATE INDEX IF NOT EXISTS jobs_project ON jobs(project_id,created);
CREATE TABLE IF NOT EXISTS fixed_steady_tasks (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 source_job_id TEXT NOT NULL REFERENCES jobs(id),
 submitted_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 task TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fixed_steady_project ON fixed_steady_tasks(project_id,created);
CREATE TABLE IF NOT EXISTS fixed_steady_framework_checks (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES fixed_steady_tasks(id),
 submitted_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 report TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fixed_steady_checks_task ON fixed_steady_framework_checks(task_id,created);
CREATE TABLE IF NOT EXISTS fixed_steady_trim_definitions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES fixed_steady_tasks(id),
 submitted_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 definition TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fixed_steady_trim_task ON fixed_steady_trim_definitions(task_id,created);
CREATE TABLE IF NOT EXISTS fixed_steady_trim_checks (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES fixed_steady_tasks(id),
 submitted_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 report TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fixed_steady_trim_checks_task ON fixed_steady_trim_checks(task_id,created);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            if "kind" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'design'")
            if "analysis_task" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN analysis_task TEXT")

    @contextmanager
    def connect(self, immediate=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if immediate:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def one(self, sql, args=()):
        with self.connect() as db:
            row = db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).rowcount
