-- db01, existing server08 console -> postgres peer. Read-only, no credentials.
-- psql -X -v ON_ERROR_STOP=1 -v run_first=202610140825 -v run_last=202610140835 -d fdsdb -f app-db-before.sql
-- Bounds below are proposed, not authorized. Supply final approved same-day KST bounds.
SELECT :'run_first' ~ '^[0-9]{8}(0[5-9]|1[0-9]|2[0-3])[0-5][0-9]$'
   AND :'run_last' ~ '^[0-9]{8}(0[5-9]|1[0-9]|2[0-3])[0-5][0-9]$'
   AND left(:'run_first', 8) = left(:'run_last', 8)
   AND left(:'run_first', 8) = to_char(current_timestamp AT TIME ZONE 'Asia/Seoul', 'YYYYMMDD')
   AND substring(:'run_first', 9, 4) >= '0500'
   AND substring(:'run_last', 9, 4) <= '2359'
   AND :'run_first' <= :'run_last' AS valid_window \gset
\if :valid_window
\else
  \echo INVALID_RUN_WINDOW_NO_REQUESTS
  SELECT 1 / 0 AS invalid_run_window; -- ON_ERROR_STOP=1 exits nonzero.
\endif
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SELECT current_database(), current_user, inet_server_addr(), version(),
       current_setting('TimeZone'), current_timestamp;
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'transactions'
 ORDER BY ordinal_position;
SELECT count(*) AS existing_rows_in_planned_run_window
FROM transactions
WHERE left(account_id, 7) = 'SIM_rg_'
  AND substring(account_id from 8 for 12) BETWEEN :'run_first' AND :'run_last'
  AND substring(account_id from 20 for 1) = '_';
COMMIT;
-- Require rc=0, exact COUNT=0, schema/DB identity match and fresh writer exclusion.
