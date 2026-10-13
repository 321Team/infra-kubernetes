-- db01 postgres peer. Actual meta.json RUN only; no new requests or DELETE.
-- psql -X -A -t -F '|' -v ON_ERROR_STOP=1 -v run=ACTUAL_12_DIGIT_RUN -d fdsdb -f app-db-after.sql
SELECT :'run' ~ '^[0-9]{12}$' AS valid_run \gset
\if :valid_run
\else
  \echo INVALID_RUN
  SELECT 1 / 0 AS invalid_run; -- ON_ERROR_STOP=1 exits nonzero.
\endif
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SELECT current_database(), current_user, current_timestamp;
SELECT account_id, transaction_id, transaction_type, amount,
       fds_detected, fds_evaluation_skipped,
       coalesce((SELECT string_agg(r->>'rule_id', ',')
                 FROM jsonb_array_elements(fds_rules) r
                 WHERE (r->>'triggered')::bool), '-'), occurred_at
FROM transactions
WHERE left(account_id, length('SIM_rg_' || :'run' || '_')) = 'SIM_rg_' || :'run' || '_'
ORDER BY received_at;
COMMIT;
