-- Additive live-fixture migration: email_verified defaults to unverified.
ALTER TABLE fixture_users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0;
