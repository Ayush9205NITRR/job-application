CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fid TEXT NOT NULL,
  type TEXT NOT NULL,          -- 'open' | 'click'
  ts INTEGER NOT NULL,         -- unix seconds
  ua TEXT,
  country TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_fid ON events (fid);
