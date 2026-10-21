SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    repo_url TEXT,
    repo_path TEXT,
    issue_url TEXT,
    raw_issue TEXT,
    task_payload TEXT,
    result_payload TEXT,
    status TEXT,
    current_phase TEXT,
    iteration_count INTEGER,
    failure_type TEXT,
    error_message TEXT,
    report_path TEXT,
    trace_path TEXT,
    queued_at REAL,
    started_at REAL,
    finished_at REAL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    tool_name TEXT,
    tool_input TEXT,
    tool_output TEXT,
    success INTEGER,
    duration_ms INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS patches (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    diff TEXT,
    applied INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS test_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    command TEXT,
    status TEXT,
    output TEXT,
    duration_ms INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS reflections (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    task_type TEXT,
    success INTEGER,
    reflection TEXT,
    metadata TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT,
    trigger_conditions TEXT,
    procedure TEXT,
    preferred_tools TEXT,
    success_rate REAL,
    version TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS failure_patterns (
    id TEXT PRIMARY KEY,
    failure_type TEXT,
    symptoms TEXT,
    likely_causes TEXT,
    recommended_recovery TEXT,
    created_at TEXT
);
"""
