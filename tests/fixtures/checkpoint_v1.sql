-- Checkpoint format created before the ORM migration.
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE items(
    warning_id TEXT PRIMARY KEY, checker TEXT NOT NULL,
    ordinal INTEGER NOT NULL, warning TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending', verdict TEXT, baseline TEXT
);
CREATE TABLE checker_cache(checker TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE checker_patterns(
    checker TEXT NOT NULL, name TEXT NOT NULL, classification TEXT NOT NULL,
    value TEXT NOT NULL, uses INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(checker, name, classification)
);
INSERT INTO metadata VALUES
    ('format', '1'),
    ('scope', '{"project_id":0,"stream_id":"master","snapshot_id":"old"}'),
    ('model', '{"model":"legacy"}'),
    ('write', 'false'),
    ('queue_complete', 'true');
INSERT INTO items VALUES(
    'legacy-warning', 'CHECKER', 0, '{"warning_id":"legacy-warning"}',
    'proposed', '{"classification":"UC","comment":"Need context","evidence":[]}',
    '{"classification_id":0,"comment":""}'
);
INSERT INTO checker_cache VALUES('CHECKER', '{"documentation":"Old docs"}');
INSERT INTO checker_patterns VALUES(
    'CHECKER', 'old-pattern', 'FP',
    '{"name":"old-pattern","criterion":"Check guard","checks":["Read guard"]}', 2
);
