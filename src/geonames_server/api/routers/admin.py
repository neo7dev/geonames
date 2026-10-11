"""Administration page: admins find, create, edit and delete user accounts.

Authenticated with a login token like the rest of /portal, for admin roles
only. Queries run as the geonames_admin database role, which may only read the
account tables and change a profile's display name, role, is_active and
is_deleted. Creating
and permanently deleting a user goes through Supabase Auth's admin API.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, false, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api import gotrue
from geonames_server.api.account_events import (
    claim_creation,
    claim_removal,
    tag_actor,
)
from geonames_server.api.auth import Principal, forget_user, require_user
from geonames_server.api.dependencies import get_session
from geonames_server.api.schemas.admin import (
    AccountEvent as AccountEventOut,
)
from geonames_server.api.schemas.admin import (
    AccountEventKind,
    AccountEventList,
    AdminUser,
    AdminUserList,
    CreateUserRequest,
    RoleOption,
    UpdateUserRequest,
    UserStatus,
)
from geonames_server.config.config import settings
from geonames_server.database.models.accounts import (
    AccountEvent,
    ApiKey,
    Profile,
    Role,
)
from geonames_server.log.mylogger import get_logger

log = get_logger()

router = APIRouter(tags=["Administration"])

# Created by the admin user management migration
ADMIN_DB_ROLE = "geonames_admin"


async def require_portal_admin(
    principal: Principal = Depends(require_user),
) -> Principal:
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This page needs an admin account",
        )
    return principal


async def admin_session(
    principal: Principal = Depends(require_portal_admin),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    """The request's session, as geonames_admin until the transaction ends.

    Endpoints build their response before committing: after the commit the
    session is back to the server's own database user.
    """
    await _act_as_admin(session, principal)
    return session


async def _act_as_admin(session: AsyncSession, principal: Principal) -> None:
    await session.execute(text(f"SET LOCAL ROLE {ADMIN_DB_ROLE}"))
    # Changes in this transaction are logged as this admin's.
    await tag_actor(session, principal.user_id, "admin")


def _active_keys():
    return (
        select(func.count(ApiKey.id))
        .where(ApiKey.user_id == Profile.id, ApiKey.revoked_at.is_(None))
        .correlate(Profile)
        .scalar_subquery()
    )


def _users_select():
    auth_suspended = func.coalesce(
        Profile.deleted_at.is_not(None) | (Profile.banned_until > func.now()), false()
    )
    return select(
        Profile.id,
        Profile.email,
        Profile.display_name,
        Profile.role,
        Role.tier,
        Role.is_admin,
        Profile.is_active,
        Profile.is_deleted,
        auth_suspended.label("auth_suspended"),
        _active_keys().label("active_keys"),
        Profile.created_at,
    ).join(Role, Role.name == Profile.role)


_STATUS_FILTERS = {
    UserStatus.active: and_(Profile.is_active, Profile.is_deleted.is_(False)),
    UserStatus.inactive: and_(
        Profile.is_active.is_(False), Profile.is_deleted.is_(False)
    ),
    UserStatus.deleted: Profile.is_deleted,
}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _not_found(user_id: uuid.UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"User '{user_id}' not found"
    )


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def _user(session: AsyncSession, user_id: uuid.UUID) -> AdminUser:
    row = (
        await session.execute(_users_select().where(Profile.id == user_id))
    ).one_or_none()
    if row is None:
        raise _not_found(user_id)
    return AdminUser.model_validate(row._mapping)


async def _check_role(session: AsyncSession, name: str) -> Role:
    role = await session.get(Role, name)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Role '{name}' does not exist",
        )
    return role


def _gotrue_failed(e: gotrue.GoTrueError) -> HTTPException:
    if e.status_code is None or e.status_code >= 500:
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supabase Auth failed: {e}",
        )
    if e.status_code == status.HTTP_404_NOT_FOUND:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    if "already" in str(e).lower():
        return _conflict(str(e))
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)
    )


@router.get("/roles", response_model=list[RoleOption])
async def list_roles(session: AsyncSession = Depends(admin_session)):
    """Roles a user can be given, each with its tier (the user's plan)."""
    roles = await session.scalars(select(Role).order_by(Role.is_admin, Role.name))
    return roles.all()


@router.get("/users", response_model=AdminUserList)
async def list_users(
    q: Annotated[
        str | None,
        Query(max_length=254, description="Part of the email address or name"),
    ] = None,
    user_status: Annotated[
        UserStatus | None, Query(alias="status", description="Only users in this state")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(require_portal_admin),
    session: AsyncSession = Depends(admin_session),
):
    """Users matching the search, the calling admin first, then newest first."""
    filters = []
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        filters.append(
            or_(
                Profile.email.ilike(pattern, escape="\\"),
                Profile.display_name.ilike(pattern, escape="\\"),
            )
        )
    if user_status is not None:
        filters.append(_STATUS_FILTERS[user_status])

    total = await session.scalar(select(func.count(Profile.id)).where(*filters))
    rows = await session.execute(
        _users_select()
        .where(*filters)
        .order_by(
            (Profile.id == principal.user_id).desc(),
            Profile.created_at.desc(),
            Profile.id,
        )
        .limit(limit)
        .offset(offset)
    )
    return AdminUserList(
        users=[AdminUser.model_validate(row._mapping) for row in rows],
        total=total,
    )


@router.get("/users/{user_id}", response_model=AdminUser)
async def get_user(user_id: uuid.UUID, session: AsyncSession = Depends(admin_session)):
    return await _user(session, user_id)


@router.post("/users", response_model=AdminUser, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    principal: Principal = Depends(require_portal_admin),
    session: AsyncSession = Depends(admin_session),
):
    """Create a confirmed user, who can sign in with the password at once."""
    if len(body.password) < settings.password_min_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Use a password of at least {settings.password_min_length} characters",
        )
    if body.role is not None:
        await _check_role(session, body.role)

    # Do not keep a transaction open across the call to Supabase Auth.
    await session.commit()
    try:
        user_id = await gotrue.create_user(
            body.email,
            body.password,
            display_name=body.display_name or None,
        )
    except gotrue.GoTrueError as e:
        raise _gotrue_failed(e) from None

    try:
        await _act_as_admin(session, principal)
        # The signup trigger created the profile with the default role.
        if body.role is not None:
            result = await session.execute(
                update(Profile).where(Profile.id == user_id).values(role=body.role)
            )
            if result.rowcount == 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="The user was created but has no profile; is the signup trigger installed?",
                )
        await claim_creation(
            session, user_id, principal.user_id, principal.email, "admin", body.role
        )
        await _act_as_admin(session, principal)
        created = await _user(session, user_id)
        await session.commit()
    except Exception:
        # Undo the half-made user, so that trying again does not find the
        # email address already registered.
        await session.rollback()
        try:
            await gotrue.delete_user(user_id)
            removed = True
        except gotrue.GoTrueError:
            removed = False
        # The rollback undid the claim: log the attempt and its undoing as the admin's.
        try:
            await claim_creation(
                session, user_id, principal.user_id, principal.email, "admin"
            )
            if removed:
                await claim_removal(
                    session, user_id, principal.user_id, principal.email
                )
            await session.commit()
        except Exception as e:
            await session.rollback()
            log.error(f"Could not credit {principal.email} with user {user_id}: {e}")
        raise
    return created


@router.patch("/users/{user_id}", response_model=AdminUser)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    principal: Principal = Depends(require_portal_admin),
    session: AsyncSession = Depends(admin_session),
):
    """Change a user's name or role (and so their plan), deactivate or restore them."""
    changes = body.model_dump(exclude_unset=True)
    if "display_name" in changes:
        changes["display_name"] = changes["display_name"] or None
    if "role" in changes:
        role = await _check_role(session, changes["role"])
    if user_id == principal.user_id:
        # An admin cannot lock themselves out; another admin has to.
        if changes.get("is_active") is False or changes.get("is_deleted") is True:
            raise _conflict("You cannot deactivate or delete your own account")
        if "role" in changes and not role.is_admin:
            raise _conflict("You cannot take the admin role away from yourself")

    result = await session.execute(
        update(Profile).where(Profile.id == user_id).values(**changes)
    )
    if result.rowcount == 0:
        raise _not_found(user_id)
    updated = await _user(session, user_id)
    await session.commit()
    forget_user(user_id)
    return updated


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    permanent: Annotated[
        bool,
        Query(
            description="Remove the user, their keys and usage for good. "
            "Only for users already deleted"
        ),
    ] = False,
    principal: Principal = Depends(require_portal_admin),
    session: AsyncSession = Depends(admin_session),
) -> None:
    """Soft-delete a user (restore with PATCH is_deleted=false), or remove them for good."""
    if user_id == principal.user_id:
        raise _conflict("You cannot delete your own account")

    if not permanent:
        result = await session.execute(
            update(Profile).where(Profile.id == user_id).values(is_deleted=True)
        )
        if result.rowcount == 0:
            raise _not_found(user_id)
        await session.commit()
        forget_user(user_id)
        return

    is_deleted = await session.scalar(
        select(Profile.is_deleted).where(Profile.id == user_id)
    )
    if is_deleted is None:
        raise _not_found(user_id)
    if not is_deleted:
        raise _conflict(
            "Delete the user first; only deleted users can be removed for good"
        )
    # Do not keep a transaction open across the call to Supabase Auth.
    await session.commit()
    try:
        await gotrue.delete_user(user_id)
    except gotrue.GoTrueError as e:
        raise _gotrue_failed(e) from None
    forget_user(user_id)
    try:
        await claim_removal(session, user_id, principal.user_id, principal.email)
        await session.commit()
    except Exception as e:
        # The user is gone already; only the log's credit is missing.
        await session.rollback()
        log.error(
            f"Removed user {user_id}, but could not credit {principal.email} "
            f"with it in the account log: {e}"
        )


@router.get("/events", response_model=AccountEventList)
async def list_events(
    user_id: Annotated[
        uuid.UUID | None, Query(description="Only this account's events")
    ] = None,
    event: Annotated[
        list[AccountEventKind] | None,
        Query(description="Only these kinds of event; repeat for several"),
    ] = None,
    q: Annotated[
        str | None,
        Query(max_length=254, description="Part of the account's or actor's email"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(admin_session),
):
    """What happened to accounts, newest first: signups, accounts created, plan
    upgrades and downgrades, name and email changes, deactivation, deletion,
    restoring, suspension and removal, and who did it."""
    filters = []
    if user_id is not None:
        filters.append(AccountEvent.user_id == user_id)
    if event:
        filters.append(AccountEvent.event.in_([e.value for e in event]))
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        filters.append(
            or_(
                AccountEvent.user_email.ilike(pattern, escape="\\"),
                AccountEvent.actor_email.ilike(pattern, escape="\\"),
            )
        )

    total = await session.scalar(select(func.count(AccountEvent.id)).where(*filters))
    rows = await session.scalars(
        select(AccountEvent)
        .where(*filters)
        .order_by(AccountEvent.created_at.desc(), AccountEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return AccountEventList(
        events=[AccountEventOut.model_validate(row) for row in rows], total=total
    )
