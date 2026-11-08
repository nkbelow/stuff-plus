-- Facts about one season's bronze rows that the silver file alone can't show.
--
-- Rendered by source.py. Placeholders it fills:
--   row_hash           the exact-duplicate hash, shared with pitches.sql
--   deprecated_counts  one non-empty count per deprecated column present (may be empty)
--   source             the bronze files joined to the lineage table

SELECT
    count(*) AS rows_read,
    count(DISTINCT {row_hash}) AS distinct_rows,
    count(*) FILTER (WHERE raw.game_date IS DISTINCT FROM CAST(lineage.game_date AS VARCHAR))
        AS date_mismatches,
    array_to_string(
        list(DISTINCT raw.game_date || ' in partition ' || lineage.game_date)
            FILTER (WHERE raw.game_date IS DISTINCT FROM CAST(lineage.game_date AS VARCHAR))[1:5],
        ', '
    ) AS date_mismatch_examples
    {deprecated_counts}
FROM {source}
