CREATE EXTENSION IF NOT EXISTS vector;

-- FTS column is also ensured at runtime via init_db/_ensure_columns for existing volumes.
-- Fresh volumes get the GIN index after the ORM creates code_chunks.
