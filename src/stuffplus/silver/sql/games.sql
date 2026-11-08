-- Per-game coverage for one audited season, into the temp table dq_games.
--
-- Tracking outages happen a whole game at a time (one park's system down), so spin
-- coverage is measured per game. Rendered by quality.py; it fills `season` and `thrown`
-- (the filter for pitches actually thrown, excluding automatic calls). Parameters:
-- $1 spin outage coverage, $2 and $3 the pitches-per-game range.

CREATE OR REPLACE TEMP TABLE dq_games AS
WITH per_game AS (
    SELECT
        game_pk,
        -- A suspended game resumes on a later date; it is dated by its first pitch.
        min(game_date) AS game_date,
        any_value(home_team) AS home_team,
        count(*) AS pitches,
        count(*) FILTER (WHERE {thrown}) AS pitches_thrown,
        count(release_spin_rate) FILTER (WHERE {thrown})
            / nullif(count(*) FILTER (WHERE {thrown}), 0) AS spin_coverage
    FROM audited
    GROUP BY game_pk
)

SELECT
    {season} AS season,
    *,
    list_filter(
        [
            CASE WHEN spin_coverage < $1 THEN 'spin_outage' END,
            CASE WHEN pitches NOT BETWEEN $2 AND $3 THEN 'pitch_count' END
        ],
        lambda flag: flag IS NOT NULL
    ) AS flags
FROM per_game
ORDER BY game_date, game_pk
