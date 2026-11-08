-- Typed, deduplicated silver pitches for one season.
--
-- Rendered by build.py; every column list below is generated from contract.py, so the
-- output matches the schema contract exactly. Placeholders it fills:
--   casts           one strict cast per source column: a value that doesn't fit its type
--                   raises an error naming the column, value and partition date
--   row_hash        the exact-duplicate hash, shared with bronze_profile.sql
--   source          the season's bronze files joined to the lineage table
--   source_columns  the source column names
--   pitch_id        the key columns joined with '-'
--   sort            the sort columns
--   output_columns  every silver column, in contract order

WITH typed AS (
    SELECT
        {casts},
        {row_hash} AS bronze_row_hash,
        lineage.fetched_at AS bronze_fetched_at
    FROM {source}
),

-- Exact duplicates collapse, keeping the earliest fetch. Rows sharing a key but differing
-- in any bronze value survive as separate rows, and the audit fails on them.
deduped AS (
    SELECT {source_columns}, min(bronze_fetched_at) AS bronze_fetched_at
    FROM typed
    GROUP BY {source_columns}, bronze_row_hash
),

with_id AS (
    SELECT {pitch_id} AS pitch_id, * FROM deduped
)

SELECT {output_columns}
FROM with_id
ORDER BY {sort}
