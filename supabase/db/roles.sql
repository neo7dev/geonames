-- From supabase/supabase docker/volumes/db/roles.sql, trimmed to the services we run.
-- supabase_functions_admin is dropped: it only exists when webhooks.sql (edge functions) runs,
-- and the failed ALTER aborts database initialisation.
-- NOTE: change to your own passwords for production environments
\set pgpass `echo "$POSTGRES_PASSWORD"`

ALTER USER authenticator WITH PASSWORD :'pgpass';
ALTER USER supabase_auth_admin WITH PASSWORD :'pgpass';
