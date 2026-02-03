"""
Cleanup Module for Canary Tests

Handles cleanup of test data created during canary tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, List, TYPE_CHECKING
from sqlmodel import Session, select, not_

from app.modules.canary.config import get_canary_config
from app.modules.canary.utils import get_canary_logger

if TYPE_CHECKING:
    from app.core.models.user import User
    from app.modules.workspace.models.workspace import Workspace

logger = get_canary_logger("canary.cleanup")


def _get_models():
    """Lazy import models to avoid circular imports.

    IMPORTANT: We import from app.core.models.models which is the central
    models registry. This ensures all models are properly initialized
    and avoids circular import issues.
    """
    from app.core.models.models import User, Workspace
    return User, Workspace


class CanaryCleanup:
    """
    Cleanup handler for canary test data.

    Handles soft deletion of workspaces and optionally users
    created during canary tests.
    """

    def __init__(self, session: Session):
        self.session = session
        self.config = get_canary_config()

    def cleanup_workspace(self, workspace_id: int) -> bool:
        """
        Soft delete a workspace created during canary test.

        Args:
            workspace_id: ID of the workspace to clean up

        Returns:
            True if cleanup was successful, False otherwise
        """
        User, Workspace = _get_models()
        try:
            workspace = self.session.exec(
                select(Workspace)
                .where(Workspace.id == workspace_id)
                .where(not_(Workspace.is_deleted))
            ).first()

            if not workspace:
                logger.warning(f"Workspace {workspace_id} not found for cleanup")
                return False

            # Verify this is a canary test workspace
            if not self._is_canary_workspace(workspace):
                logger.warning(f"Workspace {workspace_id} is not a canary test workspace, skipping cleanup")
                return False

            # Perform soft delete
            workspace.is_deleted = True
            workspace.deleted_at = datetime.now(timezone.utc)
            self.session.add(workspace)
            self.session.commit()

            logger.info(f"Soft deleted canary workspace {workspace_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup workspace {workspace_id}: {e}")
            self.session.rollback()
            return False

    def cleanup_user(self, user_email: str) -> bool:
        """
        Soft delete a user created during canary test.

        Args:
            user_email: Email of the user to clean up

        Returns:
            True if cleanup was successful, False otherwise
        """
        User, Workspace = _get_models()
        try:
            # Verify this is a canary test user
            if not self._is_canary_email(user_email):
                logger.warning(f"Email {user_email} is not a canary test email, skipping cleanup")
                return False

            user = self.session.exec(
                select(User)
                .where(User.email.ilike(user_email))
                .where(not_(User.is_deleted))
            ).first()

            if not user:
                logger.warning(f"User {user_email} not found for cleanup")
                return False

            # Perform soft delete
            user.is_deleted = True
            user.deleted_at = datetime.now(timezone.utc)
            self.session.add(user)
            self.session.commit()

            logger.info(f"Soft deleted canary user {user_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup user {user_email}: {e}")
            self.session.rollback()
            return False

    def cleanup_old_canary_data(self, hours: Optional[int] = None) -> dict:
        """
        Clean up old canary test data older than specified hours.

        Args:
            hours: Number of hours after which to clean up data.
                   Defaults to config.CLEANUP_AFTER_HOURS

        Returns:
            Dictionary with cleanup statistics
        """
        if hours is None:
            hours = self.config.CLEANUP_AFTER_HOURS

        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        stats = {
            "workspaces_cleaned": 0,
            "users_cleaned": 0,
            "errors": []
        }

        User, Workspace = _get_models()
        try:
            # Find old canary workspaces
            old_workspaces = self.session.exec(
                select(Workspace)
                .where(Workspace.email.like(f"%@{self.config.EMAIL_DOMAIN}"))
                .where(Workspace.created_at < cutoff_time)
                .where(not_(Workspace.is_deleted))
            ).all()

            for workspace in old_workspaces:
                try:
                    workspace.is_deleted = True
                    workspace.deleted_at = datetime.now(timezone.utc)
                    self.session.add(workspace)
                    stats["workspaces_cleaned"] += 1
                except Exception as e:
                    stats["errors"].append(f"Workspace {workspace.id}: {str(e)}")

            # Find old canary users
            old_users = self.session.exec(
                select(User)
                .where(User.email.like(f"%@{self.config.EMAIL_DOMAIN}"))
                .where(User.created_at < cutoff_time)
                .where(not_(User.is_deleted))
            ).all()

            for user in old_users:
                try:
                    user.is_deleted = True
                    user.deleted_at = datetime.now(timezone.utc)
                    self.session.add(user)
                    stats["users_cleaned"] += 1
                except Exception as e:
                    stats["errors"].append(f"User {user.id}: {str(e)}")

            self.session.commit()

            logger.info(
                f"Canary cleanup completed: {stats['workspaces_cleaned']} workspaces, "
                f"{stats['users_cleaned']} users cleaned up"
            )

        except Exception as e:
            logger.error(f"Error during canary cleanup: {e}")
            self.session.rollback()
            stats["errors"].append(str(e))

        return stats

    def _is_canary_email(self, email: str) -> bool:
        """Check if an email is a canary test email."""
        return email.lower().endswith(f"@{self.config.EMAIL_DOMAIN.lower()}")

    def _is_canary_workspace(self, workspace: Workspace) -> bool:
        """Check if a workspace is a canary test workspace."""
        if workspace.email and self._is_canary_email(workspace.email):
            return True
        return False

    def get_canary_workspaces(self, include_deleted: bool = False) -> List[Workspace]:
        """
        Get all canary test workspaces.

        Args:
            include_deleted: Whether to include soft-deleted workspaces

        Returns:
            List of canary workspaces
        """
        query = select(Workspace).where(
            Workspace.email.like(f"%@{self.config.EMAIL_DOMAIN}")
        )

        if not include_deleted:
            query = query.where(not_(Workspace.is_deleted))

        return self.session.exec(query.order_by(Workspace.created_at.desc())).all()

    def get_canary_users(self, include_deleted: bool = False) -> List[User]:
        """
        Get all canary test users.

        Args:
            include_deleted: Whether to include soft-deleted users

        Returns:
            List of canary users
        """
        query = select(User).where(
            User.email.like(f"%@{self.config.EMAIL_DOMAIN}")
        )

        if not include_deleted:
            query = query.where(not_(User.is_deleted))

        return self.session.exec(query.order_by(User.created_at.desc())).all()
