-- ============================================================
-- DocuMind schema
-- SQLite + sqlite-vec extension
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ----- Folders (knowledge bases) ------------------------------
CREATE TABLE IF NOT EXISTS folder (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    color       TEXT DEFAULT 'amber',
    created_at  TEXT DEFAULT (datetime('now'))
);

-- ----- Documents (Excel files) --------------------------------
CREATE TABLE IF NOT EXISTS document (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id     INTEGER NOT NULL REFERENCES folder(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    file_size     INTEGER,
    doc_metadata  TEXT,                       -- JSON: jira/customer/module/version extracted from cover
    status        TEXT DEFAULT 'pending',     -- pending/parsing/indexing/ready/failed
    error_msg     TEXT,
    uploaded_at   TEXT DEFAULT (datetime('now')),
    indexed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_document_folder ON document(folder_id);
CREATE INDEX IF NOT EXISTS idx_document_status ON document(status);

-- ----- Sheets -------------------------------------------------
CREATE TABLE IF NOT EXISTS sheet (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id             INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    sheet_index             INTEGER NOT NULL,
    sheet_type              TEXT,             -- cover/history/table/memo/screen/unknown
    classifier_confidence   REAL,
    screenshot_path         TEXT,
    raw_text                TEXT              -- full sheet text dump for fallback
);
CREATE INDEX IF NOT EXISTS idx_sheet_document ON sheet(document_id);

-- ----- Chunks (core retrieval unit) ---------------------------
CREATE TABLE IF NOT EXISTS chunk (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id        INTEGER NOT NULL REFERENCES sheet(id) ON DELETE CASCADE,
    document_id     INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    folder_id       INTEGER NOT NULL REFERENCES folder(id)   ON DELETE CASCADE,
    text            TEXT NOT NULL,            -- plain text for BM25
    markdown        TEXT,                     -- formatted version for LLM
    chunk_metadata  TEXT,                     -- JSON: jira tags, colors, hierarchy path, custom keys
    hierarchy_path  TEXT,                     -- "工事→予算反映 > ダイビル > 各月対応"
    chunk_order     INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chunk_sheet    ON chunk(sheet_id);
CREATE INDEX IF NOT EXISTS idx_chunk_document ON chunk(document_id);
CREATE INDEX IF NOT EXISTS idx_chunk_folder   ON chunk(folder_id);

-- ----- Multi-perspective summaries ----------------------------
CREATE TABLE IF NOT EXISTS chunk_summary (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id     INTEGER NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
    perspective  TEXT NOT NULL,             -- 'technical' / 'business' / 'keywords'
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summary_chunk ON chunk_summary(chunk_id);

-- ----- Images (embedded in sheets) ----------------------------
CREATE TABLE IF NOT EXISTS image (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id                INTEGER NOT NULL REFERENCES sheet(id) ON DELETE CASCADE,
    file_path               TEXT NOT NULL,
    anchor_cell             TEXT,                -- e.g. "B5:K30"
    vl_description          TEXT,                -- VL model output
    related_annotations     TEXT                 -- JSON: red box markers etc
);
CREATE INDEX IF NOT EXISTS idx_image_sheet ON image(sheet_id);

-- ----- Change log (from 変更履歴 sheets) ----------------------
CREATE TABLE IF NOT EXISTS change_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    log_date     TEXT,
    description  TEXT,
    tags         TEXT                          -- JSON array
);
CREATE INDEX IF NOT EXISTS idx_changelog_doc ON change_log(document_id);

-- ----- Data dictionary (from table-type sheets) ---------------
CREATE TABLE IF NOT EXISTS data_dict (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    sheet_id        INTEGER NOT NULL REFERENCES sheet(id) ON DELETE CASCADE,
    table_name      TEXT,
    table_name_en   TEXT,
    column_name     TEXT,
    column_name_en  TEXT,
    description     TEXT
);
CREATE INDEX IF NOT EXISTS idx_dict_doc      ON data_dict(document_id);
CREATE INDEX IF NOT EXISTS idx_dict_colname  ON data_dict(column_name);
CREATE INDEX IF NOT EXISTS idx_dict_colen    ON data_dict(column_name_en);

-- ----- FTS5 full text (Japanese pre-tokenized via Lindera) ----
-- 'unicode61 remove_diacritics 0' is fine because we pre-tokenize with Lindera
-- and store space-separated tokens in fts_text.
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    fts_text,
    content='',
    tokenize='unicode61 remove_diacritics 0'
);

-- ----- Chat history (optional, useful for debugging) ----------
CREATE TABLE IF NOT EXISTS chat_message (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT,
    role          TEXT,                       -- user/assistant
    content       TEXT,
    context_mode  TEXT,                       -- all / folder:<id> / doc:<id>
    citations     TEXT,                       -- JSON array of chunk ids
    confidence    INTEGER,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON chat_message(session_id);
