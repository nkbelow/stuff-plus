-- Typed, deduplicated silver pitches for one season.
--
-- Rendered by build.py, which fills three placeholders:
--   casts       one CAST per schema contract column, generated from contract.py
--   data_files  the season's complete, non-empty bronze files
--   lineage     a table of (data_file, fetched_at), one row per partition
--
-- Strict CAST (not TRY_CAST): a value that can't be typed fails the build.

WITH typed AS (
    SELECT
        {casts},
        lineage.fetched_at AS bronze_fetched_at
    FROM read_csv(
        {data_files},
        all_varchar = true, header = true, union_by_name = true, filename = true
    ) AS raw
    JOIN {lineage} AS lineage ON raw.filename = lineage.data_file
),

-- Exact duplicates collapse (a suspended game can land in two daily partitions).
-- Rows sharing a key but differing in any value survive, and the audit fails on them.
deduped AS (
    SELECT * EXCLUDE (bronze_fetched_at), min(bronze_fetched_at) AS bronze_fetched_at
    FROM typed
    GROUP BY ALL
)

SELECT
    game_pk || '-' || at_bat_number || '-' || pitch_number AS pitch_id,
    *
FROM deduped
ORDER BY game_date, game_pk, at_bat_number, pitch_number
