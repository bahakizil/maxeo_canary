"""
Database Verification Module for Canary Tests

Provides database verification methods to validate the state of entities
at each step of the canary test flow.

IMPORTANT: This module uses RAW SQL queries only to avoid importing
SQLModel/SQLAlchemy ORM models which have complex relationships.
This makes it lightweight and independent of the main app's model structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from canary.utils import (
    get_canary_logger,
    decrypt_string,
    generate_totp_token
)

logger = get_canary_logger("canary.db_verification")


@dataclass
class VerificationResult:
    """Result of a verification check."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class DBVerifier:
    """
    Database verifier for canary tests.

    Uses raw SQL queries to verify the state of database entities
    at each step of the canary test flow.

    This approach avoids importing ORM models which have complex
    relationships and dependencies.
    """

    def __init__(self, connection_string: str, test_email: str):
        """
        Initialize the DB verifier.

        Args:
            connection_string: PostgreSQL connection string
            test_email: The canary test email to verify
        """
        self.engine: Engine = create_engine(connection_string)
        self.test_email = test_email
        self._user_cache = None
        self._workspace_cache = None

    def _execute_query(self, query: str, params: dict = None) -> list:
        """Execute a raw SQL query and return results."""
        with self.engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            return result.fetchall()

    def _execute_scalar(self, query: str, params: dict = None):
        """Execute a query and return a single value."""
        with self.engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            row = result.fetchone()
            return row[0] if row else None

    def get_user(self) -> Optional[dict]:
        """Get the canary test user by email."""
        if self._user_cache is not None:
            return self._user_cache

        query = """
            SELECT id, email, totp_secret, is_deleted, created_at
            FROM users
            WHERE email ILIKE :email AND NOT is_deleted
            LIMIT 1
        """
        rows = self._execute_query(query, {"email": self.test_email})
        if rows:
            row = rows[0]
            self._user_cache = {
                "id": row[0],
                "email": row[1],
                "totp_secret": row[2],
                "is_deleted": row[3],
                "created_at": row[4]
            }
            return self._user_cache
        return None

    def get_otp_code(self) -> Optional[str]:
        """
        Generate the current valid OTP code for the canary test user.

        Since TOTP is time-based, we generate the current valid code
        using the user's TOTP secret.
        """
        user = self.get_user()
        if not user:
            logger.warning(f"User not found for email: {self.test_email}")
            return None

        if not user.get("totp_secret"):
            logger.warning(f"User {self.test_email} has no TOTP secret")
            return None

        try:
            decrypted_secret = decrypt_string(user["totp_secret"])
            otp_code = generate_totp_token(decrypted_secret)
            return otp_code
        except Exception as e:
            logger.error(f"Failed to generate OTP for user {self.test_email}: {e}")
            return None

    def verify_user_exists(self) -> VerificationResult:
        """Verify that the canary test user was created."""
        user = self.get_user()
        if user:
            return VerificationResult(
                success=True,
                message=f"User found with ID: {user['id']}",
                data={"user_id": user["id"], "email": user["email"]}
            )
        return VerificationResult(
            success=False,
            message=f"User not found for email: {self.test_email}"
        )

    def get_workspace(self) -> Optional[dict]:
        """Get the canary test workspace by email."""
        if self._workspace_cache is not None:
            return self._workspace_cache

        query = """
            SELECT id, ulid, status, email, first_name, last_name,
                   created_at, is_deleted
            FROM workspaces
            WHERE email ILIKE :email AND NOT is_deleted
            ORDER BY created_at DESC
            LIMIT 1
        """
        rows = self._execute_query(query, {"email": self.test_email})
        if rows:
            row = rows[0]
            self._workspace_cache = {
                "id": row[0],
                "ulid": row[1],
                "status": row[2],
                "email": row[3],
                "first_name": row[4],
                "last_name": row[5],
                "created_at": row[6],
                "is_deleted": row[7]
            }
            return self._workspace_cache
        return None

    def refresh_workspace(self) -> Optional[dict]:
        """Clear workspace cache and get fresh data."""
        self._workspace_cache = None
        return self.get_workspace()

    def verify_workspace_created(self) -> VerificationResult:
        """Verify that a workspace was created for the test email."""
        workspace = self.get_workspace()
        if workspace:
            return VerificationResult(
                success=True,
                message=f"Workspace found with ID: {workspace['id']}, ULID: {workspace['ulid']}",
                data={
                    "workspace_id": workspace["id"],
                    "ulid": workspace["ulid"],
                    "status": workspace["status"]
                }
            )
        return VerificationResult(
            success=False,
            message=f"Workspace not found for email: {self.test_email}"
        )

    def verify_workspace_status(self, expected_status: str) -> VerificationResult:
        """Verify workspace has reached the expected status."""
        workspace = self.refresh_workspace()
        if not workspace:
            return VerificationResult(
                success=False,
                message="Workspace not found"
            )

        if workspace["status"] == expected_status:
            return VerificationResult(
                success=True,
                message=f"Workspace status is {expected_status}",
                data={"workspace_id": workspace["id"], "status": workspace["status"]}
            )

        return VerificationResult(
            success=False,
            message=f"Workspace status is {workspace['status']}, expected {expected_status}",
            data={"workspace_id": workspace["id"], "actual_status": workspace["status"]}
        )

    def get_categories_count(self, workspace_id: int) -> int:
        """Get the count of categories for a workspace."""
        query = """
            SELECT COUNT(*) FROM workspace_categories
            WHERE workspace_id = :workspace_id AND NOT is_deleted
        """
        return self._execute_scalar(query, {"workspace_id": workspace_id}) or 0

    def verify_categories_created(self, min_count: int = 3) -> VerificationResult:
        """Verify that categories were created for the workspace."""
        workspace = self.get_workspace()
        if not workspace:
            return VerificationResult(
                success=False,
                message="Workspace not found"
            )

        count = self.get_categories_count(workspace["id"])
        if count >= min_count:
            return VerificationResult(
                success=True,
                message=f"Found {count} categories (minimum: {min_count})",
                data={"workspace_id": workspace["id"], "categories_count": count}
            )

        return VerificationResult(
            success=False,
            message=f"Found only {count} categories, expected at least {min_count}",
            data={"workspace_id": workspace["id"], "categories_count": count}
        )

    def get_prompts_count(self, workspace_id: int) -> int:
        """Get the count of prompts for a workspace."""
        query = """
            SELECT COUNT(*) FROM workspace_prompts
            WHERE workspace_id = :workspace_id AND NOT is_deleted
        """
        return self._execute_scalar(query, {"workspace_id": workspace_id}) or 0

    def verify_prompts_created(self, min_count: int = 15) -> VerificationResult:
        """Verify that prompts were created for the workspace."""
        workspace = self.get_workspace()
        if not workspace:
            return VerificationResult(
                success=False,
                message="Workspace not found"
            )

        count = self.get_prompts_count(workspace["id"])
        if count >= min_count:
            return VerificationResult(
                success=True,
                message=f"Found {count} prompts (minimum: {min_count})",
                data={"workspace_id": workspace["id"], "prompts_count": count}
            )

        return VerificationResult(
            success=False,
            message=f"Found only {count} prompts, expected at least {min_count}",
            data={"workspace_id": workspace["id"], "prompts_count": count}
        )

    def get_latest_snapshot(self, workspace_id: int) -> Optional[dict]:
        """Get the latest snapshot for a workspace."""
        query = """
            SELECT id, status, created_at
            FROM snapshots
            WHERE workspace_id = :workspace_id
            ORDER BY created_at DESC
            LIMIT 1
        """
        rows = self._execute_query(query, {"workspace_id": workspace_id})
        if rows:
            row = rows[0]
            return {
                "id": row[0],
                "status": row[1],
                "created_at": row[2]
            }
        return None

    def verify_snapshot_created(self) -> VerificationResult:
        """Verify that a snapshot was created for the workspace."""
        workspace = self.get_workspace()
        if not workspace:
            return VerificationResult(
                success=False,
                message="Workspace not found"
            )

        snapshot = self.get_latest_snapshot(workspace["id"])
        if snapshot:
            return VerificationResult(
                success=True,
                message=f"Snapshot found with ID: {snapshot['id']}, status: {snapshot['status']}",
                data={
                    "workspace_id": workspace["id"],
                    "snapshot_id": snapshot["id"],
                    "status": snapshot["status"]
                }
            )

        return VerificationResult(
            success=False,
            message="No snapshot found for workspace",
            data={"workspace_id": workspace["id"]}
        )

    def verify_snapshot_completed(self) -> VerificationResult:
        """Verify that the latest snapshot is completed."""
        workspace = self.get_workspace()
        if not workspace:
            return VerificationResult(
                success=False,
                message="Workspace not found"
            )

        snapshot = self.get_latest_snapshot(workspace["id"])
        if not snapshot:
            return VerificationResult(
                success=False,
                message="No snapshot found",
                data={"workspace_id": workspace["id"]}
            )

        if snapshot["status"] == "COMPLETED":
            return VerificationResult(
                success=True,
                message=f"Snapshot {snapshot['id']} is completed",
                data={
                    "workspace_id": workspace["id"],
                    "snapshot_id": snapshot["id"],
                    "status": snapshot["status"]
                }
            )

        return VerificationResult(
            success=False,
            message=f"Snapshot status is {snapshot['status']}, expected COMPLETED",
            data={
                "workspace_id": workspace["id"],
                "snapshot_id": snapshot["id"],
                "actual_status": snapshot["status"]
            }
        )

    def get_snapshot_prompts_status(self, snapshot_id: int) -> Dict[str, int]:
        """Get counts of snapshot prompts by status."""
        query = """
            SELECT status, COUNT(*) as count
            FROM snapshot_prompts
            WHERE snapshot_id = :snapshot_id
            GROUP BY status
        """
        rows = self._execute_query(query, {"snapshot_id": snapshot_id})

        status_counts = {
            "total": 0,
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0
        }

        for row in rows:
            status = row[0].lower() if row[0] else "unknown"
            count = row[1]
            status_counts["total"] += count
            if status in status_counts:
                status_counts[status] = count

        return status_counts

    def verify_all_prompts_completed(self) -> VerificationResult:
        """Verify all snapshot prompts are completed."""
        workspace = self.get_workspace()
        if not workspace:
            return VerificationResult(
                success=False,
                message="Workspace not found"
            )

        snapshot = self.get_latest_snapshot(workspace["id"])
        if not snapshot:
            return VerificationResult(
                success=False,
                message="No snapshot found"
            )

        status_counts = self.get_snapshot_prompts_status(snapshot["id"])

        if status_counts["total"] == 0:
            return VerificationResult(
                success=False,
                message="No snapshot prompts found",
                data=status_counts
            )

        if status_counts["failed"] > 0:
            return VerificationResult(
                success=False,
                message=f"{status_counts['failed']} prompts failed",
                data=status_counts
            )

        if status_counts["pending"] > 0 or status_counts["processing"] > 0:
            return VerificationResult(
                success=False,
                message=f"Prompts still processing: {status_counts['pending']} pending, {status_counts['processing']} processing",
                data=status_counts
            )

        return VerificationResult(
            success=True,
            message=f"All {status_counts['completed']} prompts completed",
            data=status_counts
        )

    def get_competitors_count(self, workspace_id: int) -> int:
        """Get the count of competitors for a workspace."""
        query = """
            SELECT COUNT(*) FROM workspace_competitors
            WHERE workspace_id = :workspace_id AND NOT is_deleted
        """
        return self._execute_scalar(query, {"workspace_id": workspace_id}) or 0

    def get_categories_list(self, workspace_id: int, limit: int = 10) -> list:
        """Get list of categories for a workspace."""
        query = """
            SELECT id, name, created_at
            FROM workspace_categories
            WHERE workspace_id = :workspace_id AND NOT is_deleted
            ORDER BY created_at ASC
            LIMIT :limit
        """
        rows = self._execute_query(query, {"workspace_id": workspace_id, "limit": limit})
        return [{"id": r[0], "name": r[1], "created_at": str(r[2])} for r in rows]

    def get_prompts_list(self, workspace_id: int, limit: int = 10) -> list:
        """Get list of prompts for a workspace."""
        query = """
            SELECT id, name, is_tracked, created_at
            FROM workspace_prompts
            WHERE workspace_id = :workspace_id AND NOT is_deleted
            ORDER BY created_at ASC
            LIMIT :limit
        """
        rows = self._execute_query(query, {"workspace_id": workspace_id, "limit": limit})
        return [{"id": r[0], "name": r[1], "is_tracked": r[2], "created_at": str(r[3])} for r in rows]

    def get_competitors_list(self, workspace_id: int, limit: int = 10) -> list:
        """Get list of competitors for a workspace with brand info."""
        query = """
            SELECT wc.id,
                   COALESCE(NULLIF(bdi.name, ''), bdi.domain, 'Unknown') as name,
                   COALESCE(bdi.domain, 'N/A') as domain,
                   wc.created_at
            FROM workspace_competitors wc
            LEFT JOIN brand_domain_info bdi ON wc.brand_domain_info_id = bdi.id
            WHERE wc.workspace_id = :workspace_id AND NOT wc.is_deleted
            ORDER BY wc.created_at ASC
            LIMIT :limit
        """
        rows = self._execute_query(query, {"workspace_id": workspace_id, "limit": limit})
        return [{"id": r[0], "name": r[1] or "Unknown", "domain": r[2] or "N/A", "created_at": str(r[3])} for r in rows]

    def get_model_invocations_stats(self, workspace_id: int) -> Dict[str, Any]:
        """Get model invocation statistics for a workspace."""
        query = """
            SELECT
                model,
                COUNT(*) as call_count,
                ROUND(AVG(time_elapsed)::numeric, 2) as avg_time,
                ROUND(SUM(time_elapsed)::numeric, 2) as total_time,
                ROUND(SUM(total_cost)::numeric, 4) as total_cost,
                SUM(total_tokens) as total_tokens
            FROM model_invocations
            WHERE workspace_id = :workspace_id
            GROUP BY model
            ORDER BY total_time DESC
        """
        rows = self._execute_query(query, {"workspace_id": workspace_id})

        stats = {
            "by_model": [],
            "total_calls": 0,
            "total_time": 0.0,
            "total_cost": 0.0,
            "total_tokens": 0
        }

        for row in rows:
            model_stat = {
                "model": row[0],
                "call_count": row[1],
                "avg_time": float(row[2]) if row[2] else 0,
                "total_time": float(row[3]) if row[3] else 0,
                "total_cost": float(row[4]) if row[4] else 0,
                "total_tokens": row[5] or 0
            }
            stats["by_model"].append(model_stat)
            stats["total_calls"] += model_stat["call_count"]
            stats["total_time"] += model_stat["total_time"]
            stats["total_cost"] += model_stat["total_cost"]
            stats["total_tokens"] += model_stat["total_tokens"]

        return stats

    def get_slowest_model_invocations(self, workspace_id: int, limit: int = 5) -> list:
        """Get the slowest model invocations for debugging."""
        query = """
            SELECT model, time_elapsed, total_tokens, created_at
            FROM model_invocations
            WHERE workspace_id = :workspace_id AND time_elapsed IS NOT NULL
            ORDER BY time_elapsed DESC
            LIMIT :limit
        """
        rows = self._execute_query(query, {"workspace_id": workspace_id, "limit": limit})
        return [{
            "model": r[0],
            "time_elapsed": float(r[1]) if r[1] else 0,
            "total_tokens": r[2] or 0,
            "created_at": str(r[3])
        } for r in rows]

    def get_snapshot_prompts_list(self, snapshot_id: int, limit: int = 10) -> list:
        """Get list of snapshot prompts with their status."""
        query = """
            SELECT sp.id, wp.name, sp.status, sp.created_at
            FROM snapshot_prompts sp
            JOIN workspace_prompts wp ON sp.workspace_prompt_id = wp.id
            WHERE sp.snapshot_id = :snapshot_id
            ORDER BY sp.created_at ASC
            LIMIT :limit
        """
        rows = self._execute_query(query, {"snapshot_id": snapshot_id, "limit": limit})
        return [{"id": r[0], "name": r[1], "status": r[2], "created_at": str(r[3])} for r in rows]

    def get_comprehensive_data(self) -> Dict[str, Any]:
        """Get comprehensive data for detailed reporting."""
        workspace = self.get_workspace()
        if not workspace:
            return {"error": "Workspace not found"}

        workspace_id = workspace["id"]
        snapshot = self.get_latest_snapshot(workspace_id)

        data = {
            "workspace": {
                "id": workspace_id,
                "ulid": workspace["ulid"],
                "status": workspace["status"],
                "email": workspace["email"],
                "created_at": str(workspace["created_at"])
            },
            "categories_count": self.get_categories_count(workspace_id),
            "categories_list": self.get_categories_list(workspace_id),
            "prompts_count": self.get_prompts_count(workspace_id),
            "prompts_list": self.get_prompts_list(workspace_id),
            "competitors_count": self.get_competitors_count(workspace_id),
            "competitors_list": self.get_competitors_list(workspace_id),
            "snapshot": None,
            "snapshot_prompts_status": None,
            "snapshot_prompts_list": None,
            "model_invocations": self.get_model_invocations_stats(workspace_id),
            "slowest_invocations": self.get_slowest_model_invocations(workspace_id)
        }

        if snapshot:
            data["snapshot"] = {
                "id": snapshot["id"],
                "status": snapshot["status"],
                "created_at": str(snapshot["created_at"])
            }
            data["snapshot_prompts_status"] = self.get_snapshot_prompts_status(snapshot["id"])
            data["snapshot_prompts_list"] = self.get_snapshot_prompts_list(snapshot["id"])

        return data

    def verify_competitors_found(self, min_count: int = 1) -> VerificationResult:
        """Verify competitors were found for the workspace."""
        workspace = self.get_workspace()
        if not workspace:
            return VerificationResult(
                success=False,
                message="Workspace not found"
            )

        count = self.get_competitors_count(workspace["id"])
        if count >= min_count:
            return VerificationResult(
                success=True,
                message=f"Found {count} competitors",
                data={"workspace_id": workspace["id"], "competitors_count": count}
            )

        return VerificationResult(
            success=False,
            message=f"Found only {count} competitors, expected at least {min_count}",
            data={"workspace_id": workspace["id"], "competitors_count": count}
        )

    def full_verification(self) -> Dict[str, Any]:
        """
        Perform full verification of all canary test data.

        Returns a comprehensive report of all verification results.
        """
        workspace = self.get_workspace()
        workspace_id = workspace["id"] if workspace else None

        results = {
            "user": self.verify_user_exists(),
            "workspace": self.verify_workspace_created(),
            "workspace_status": None,
            "categories": None,
            "prompts": None,
            "snapshot": None,
            "snapshot_completed": None,
            "all_prompts_completed": None,
            "competitors": None
        }

        if workspace:
            results["workspace_status"] = self.verify_workspace_status("COMPLETED")
            results["categories"] = self.verify_categories_created()
            results["prompts"] = self.verify_prompts_created()
            results["snapshot"] = self.verify_snapshot_created()
            results["snapshot_completed"] = self.verify_snapshot_completed()
            results["all_prompts_completed"] = self.verify_all_prompts_completed()
            results["competitors"] = self.verify_competitors_found()

        # Build summary
        all_passed = all(
            r.success for r in results.values()
            if r is not None
        )

        return {
            "success": all_passed,
            "workspace_id": workspace_id,
            "results": {
                key: {
                    "success": val.success,
                    "message": val.message,
                    "data": val.data
                } if val else None
                for key, val in results.items()
            }
        }

    def cleanup_test_data(self) -> Dict[str, Any]:
        """
        Soft delete all canary test data (workspace and user).

        This is called after a successful test to clean up the database
        and avoid data pollution.

        Returns:
            Dictionary with cleanup results
        """
        results = {
            "workspace_deleted": False,
            "user_deleted": False,
            "errors": []
        }

        workspace = self.get_workspace()
        user = self.get_user()

        try:
            with self.engine.connect() as conn:
                # Soft delete workspace
                if workspace and not workspace.get("is_deleted"):
                    query = """
                        UPDATE workspaces
                        SET is_deleted = true, deleted_at = NOW()
                        WHERE id = :workspace_id AND NOT is_deleted
                    """
                    conn.execute(text(query), {"workspace_id": workspace["id"]})
                    conn.commit()
                    results["workspace_deleted"] = True
                    logger.info(f"Soft deleted workspace {workspace['id']} (ULID: {workspace['ulid']})")

                # Soft delete user
                if user and not user.get("is_deleted"):
                    query = """
                        UPDATE users
                        SET is_deleted = true, deleted_at = NOW()
                        WHERE id = :user_id AND NOT is_deleted
                    """
                    conn.execute(text(query), {"user_id": user["id"]})
                    conn.commit()
                    results["user_deleted"] = True
                    logger.info(f"Soft deleted user {user['id']} ({user['email']})")

        except Exception as e:
            error_msg = f"Cleanup error: {e}"
            logger.error(error_msg)
            results["errors"].append(error_msg)

        return results

    def close(self):
        """Close the database connection."""
        self.engine.dispose()
