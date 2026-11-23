-- Jungle Coach accounts: only what is needed to log in (no e-mail, no password)
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,          -- 'discord' | 'google'
  provider_id TEXT NOT NULL,       -- user id at the provider
  name TEXT,                       -- display name (first name / Discord name)
  riot_id TEXT,                    -- optional, set by the user (Name#TAG)
  created_at INTEGER NOT NULL,
  last_login INTEGER NOT NULL,
  UNIQUE (provider, provider_id)
);
