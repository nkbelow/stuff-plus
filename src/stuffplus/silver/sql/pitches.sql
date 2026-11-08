-- Typed, deduplicated silver pitches for one season.
--
-- Rendered by build.py; every column list below is generated from contract.py, so the
-- output matches the schema contract exactly. Placeholders it fills:
--   casts           one strict CAST per source column (TRY_CAST would hide bad values)
--   source_columns  the source column names
--   pitch_id        the key columns joined with '-'
--   sort            the sort columns
--   output_columns  every silver column, in contract order
--   data_files      the season's complete, non-empty bronze files
-- The `lineage` table (data_file, fetched_at) is created by build.py before this runs.

WITH typed AS (
    SELECT
        {casts},
        -- Exactness covers the whole bronze row, not just contract columns: two rows that
        -- differ anywhere except the source filename are not duplicates of each other.
        md5(to_json(struct_pack(*COLUMNS(raw.* EXCLUDE (filename))))::VARCHAR) AS bronze_row_hash,
        lineage.fetched_at AS bronze_fetched_at
    FROM read_csv(
        {data_files},
        all_varchar = true, header = true, union_by_name = true, filename = true
    ) AS raw
    JOIN lineage ON raw.filename = lineage.data_file
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
