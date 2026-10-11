"""account events

app.account_events: a log of what happened to each account, for admins (the
Activity view on the Administration page). A trigger on app.profiles writes
it, so every change is caught, whoever makes it: signups and accounts an admin
creates, plan changes (upgraded, downgraded, or another role on the same
tier), name and email changes, deactivation, deletion, restoring, suspension
in Supabase Auth, and removal for good.

Who did it: the API tags its transactions with set_config('app.actor_id') and
'app.actor_source' (user, admin, system). Changes without a tag come from
Supabase Auth or Studio (source external). Every new profile is logged as the
user's own signup: Supabase Auth creates it in its own transaction. When an
admin (or the server, for the first admin) created the account, the API then
relabels that event as created, naming them; likewise it credits the admin
with a removal Supabase Auth carried out.

Rows keep the user's id and email, with no foreign key, so the history
survives the account being removed. Existing accounts get a signed_up event
at their profile's created_at.

Revision ID: c4d8e2f6a9b1
Revises: 8e2f4a6c1b93
Create Date: 2026-10-11 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d8e2f6a9b1"
down_revision: Union[str, Sequence[str], None] = "8e2f4a6c1b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENTS = (
    "signed_up",
    "created",
    "upgraded",
    "downgraded",
    "role_changed",
    "name_changed",
    "email_changed",
    "deactivated",
    "reactivated",
    "deleted",
    "restored",
    "suspended",
    "unsuspended",
    "removed",
)
SOURCES = ("user", "admin", "system", "external")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


TABLE = f"""
CREATE TABLE app.account_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    user_id uuid NOT NULL,
    user_email text,
    event text NOT NULL CONSTRAINT account_events_event CHECK (event IN ({_in(EVENTS)})),
    details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    actor_id uuid,
    actor_email text,
    source text NOT NULL CONSTRAINT account_events_source CHECK (source IN ({_in(SOURCES)}))
)
"""

# Tiers compare by their limits: per month, then per hour, then per minute;
# NULL (unlimited) is the highest.
FUNCTION = r"""
CREATE FUNCTION app.record_account_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := nullif(current_setting('app.actor_id', true), '')::uuid;
    actor_source text := nullif(current_setting('app.actor_source', true), '');
    kind text;
    old_tier text;
    new_tier text;
    old_rank numeric[];
    new_rank numeric[];
    was_suspended boolean;
    is_suspended boolean;
    logged_suspended boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- The user's own signup; the API relabels accounts it created.
        PERFORM app.add_account_event(
            NEW, 'signed_up', jsonb_build_object('role', NEW.role), NEW.id, 'user'
        );
        RETURN NEW;
    END IF;

    actor_source := coalesce(actor_source, 'external');

    IF TG_OP = 'DELETE' THEN
        PERFORM app.add_account_event(
            OLD, 'removed', jsonb_build_object('role', OLD.role), actor, actor_source
        );
        RETURN OLD;
    END IF;

    -- UPDATE: one event per kind of change
    IF NEW.role IS DISTINCT FROM OLD.role THEN
        SELECT r.tier, ARRAY[
            coalesce(t.per_month::numeric, 'Infinity'),
            coalesce(t.per_hour::numeric, 'Infinity'),
            coalesce(t.per_minute::numeric, 'Infinity')
        ] INTO old_tier, old_rank
        FROM app.roles r JOIN app.tiers t ON t.name = r.tier WHERE r.name = OLD.role;
        SELECT r.tier, ARRAY[
            coalesce(t.per_month::numeric, 'Infinity'),
            coalesce(t.per_hour::numeric, 'Infinity'),
            coalesce(t.per_minute::numeric, 'Infinity')
        ] INTO new_tier, new_rank
        FROM app.roles r JOIN app.tiers t ON t.name = r.tier WHERE r.name = NEW.role;
        kind := CASE
            WHEN new_rank > old_rank THEN 'upgraded'
            WHEN new_rank < old_rank THEN 'downgraded'
            ELSE 'role_changed'
        END;
        PERFORM app.add_account_event(NEW, kind, jsonb_build_object(
            'from_role', OLD.role, 'to_role', NEW.role,
            'from_tier', old_tier, 'to_tier', new_tier
        ), actor, actor_source);
    END IF;
    IF NEW.display_name IS DISTINCT FROM OLD.display_name THEN
        PERFORM app.add_account_event(NEW, 'name_changed', jsonb_build_object(
            'from', OLD.display_name, 'to', NEW.display_name
        ), actor, actor_source);
    END IF;
    IF NEW.email IS DISTINCT FROM OLD.email THEN
        PERFORM app.add_account_event(NEW, 'email_changed', jsonb_build_object(
            'from', OLD.email, 'to', NEW.email
        ), actor, actor_source);
    END IF;
    IF NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        PERFORM app.add_account_event(
            NEW, CASE WHEN NEW.is_active THEN 'reactivated' ELSE 'deactivated' END,
            '{}'::jsonb, actor, actor_source
        );
    END IF;
    IF NEW.is_deleted IS DISTINCT FROM OLD.is_deleted THEN
        PERFORM app.add_account_event(
            NEW, CASE WHEN NEW.is_deleted THEN 'deleted' ELSE 'restored' END,
            '{}'::jsonb, actor, actor_source
        );
    END IF;
    -- Banned or soft-deleted in Supabase Auth. A ban runs out with no update,
    -- so lifting one compares with the log, not with OLD: a ban that ran out
    -- is still logged as suspended until Supabase Auth clears it.
    IF NEW.banned_until IS DISTINCT FROM OLD.banned_until
        OR NEW.deleted_at IS DISTINCT FROM OLD.deleted_at
    THEN
        was_suspended := OLD.deleted_at IS NOT NULL
            OR coalesce(OLD.banned_until > now(), false);
        is_suspended := NEW.deleted_at IS NOT NULL
            OR coalesce(NEW.banned_until > now(), false);
        SELECT e.event = 'suspended' INTO logged_suspended
        FROM app.account_events e
        WHERE e.user_id = NEW.id AND e.event IN ('suspended', 'unsuspended')
        ORDER BY e.id DESC LIMIT 1;
        IF is_suspended AND NOT was_suspended THEN
            PERFORM app.add_account_event(
                NEW, 'suspended', jsonb_build_object('banned_until', NEW.banned_until),
                actor, actor_source
            );
        ELSIF NOT is_suspended AND coalesce(logged_suspended, false) THEN
            PERFORM app.add_account_event(
                NEW, 'unsuspended', '{}'::jsonb, actor, actor_source
            );
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""

ADD_EVENT = """
CREATE FUNCTION app.add_account_event(
    p app.profiles, kind text, details jsonb, actor uuid, actor_source text
) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
    INSERT INTO app.account_events
        (user_id, user_email, event, details, actor_id, actor_email, source)
    VALUES (
        p.id, p.email, kind, details, actor,
        (SELECT email FROM app.profiles WHERE id = actor), actor_source
    )
$$
"""

TRIGGER = """
CREATE TRIGGER app_record_account_event
AFTER INSERT OR UPDATE OR DELETE ON app.profiles
FOR EACH ROW EXECUTE FUNCTION app.record_account_event()
"""

# Accounts from before this log: when they signed up is all that is known.
BACKFILL = """
INSERT INTO app.account_events
    (created_at, user_id, user_email, event, details, source)
SELECT created_at, id, email, 'signed_up',
       jsonb_build_object('role', role, 'backfilled', true), 'external'
FROM app.profiles
ORDER BY created_at
"""


def upgrade() -> None:
    op.execute(TABLE)
    # Newest first, overall and per user (Postgres reads them backwards)
    op.create_index(
        "account_events_created_at_idx", "account_events", ["created_at"], schema="app"
    )
    op.create_index(
        "account_events_user_id_idx",
        "account_events",
        ["user_id", "created_at"],
        schema="app",
    )
    op.execute(ADD_EVENT)
    op.execute(FUNCTION)
    op.execute(TRIGGER)
    op.execute(BACKFILL)
    # Admins read the log; nobody but the trigger writes it.
    op.execute("GRANT SELECT ON app.account_events TO geonames_admin")
    op.execute(
        "REVOKE ALL ON FUNCTION app.record_account_event(), "
        "app.add_account_event(app.profiles, text, jsonb, uuid, text) FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER app_record_account_event ON app.profiles")
    op.execute("DROP FUNCTION app.record_account_event()")
    op.execute(
        "DROP FUNCTION app.add_account_event(app.profiles, text, jsonb, uuid, text)"
    )
    op.execute("DROP TABLE app.account_events")
