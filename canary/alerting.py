"""
Alerting Module for Canary Tests

Handles alerting to Sentry and Slack with detailed metrics reporting.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import httpx
import sentry_sdk

from canary.utils import get_canary_logger
from canary.config import get_canary_config

logger = get_canary_logger("canary.alerting")


@dataclass
class CanaryMetrics:
    """Metrics collected during canary test execution."""
    test_id: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    step_timings: Dict[str, float] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    # Detailed data from DB
    db_data: Dict[str, Any] = field(default_factory=dict)

    # UI verification results
    ui_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_duration_seconds(self) -> float:
        """Get total test duration in seconds."""
        if not self.start_time or not self.end_time:
            return 0.0
        return (self.end_time - self.start_time).total_seconds()

    def record_step_timing(self, step_name: str, duration_seconds: float) -> None:
        """Record timing for a specific step."""
        self.step_timings[step_name] = duration_seconds

    def record_error(self, step: str, error: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Record an error that occurred during the test."""
        self.errors.append({
            "step": step,
            "error": error,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    def set_db_data(self, data: Dict[str, Any]) -> None:
        """Set DB verification data."""
        self.db_data = data

    def set_ui_data(self, data: Dict[str, Any]) -> None:
        """Set UI verification data."""
        self.ui_data = data


@dataclass
class CanaryResult:
    """Result of a canary test execution."""
    success: bool
    test_id: str
    metrics: CanaryMetrics
    failed_step: Optional[str] = None
    error_message: Optional[str] = None
    db_state: Optional[Dict[str, Any]] = None
    workspace_id: Optional[int] = None
    workspace_ulid: Optional[str] = None


class CanaryTestError(Exception):
    """Exception raised when a canary test step fails."""

    def __init__(self, step: str, message: str, details: Optional[Dict[str, Any]] = None):
        self.step = step
        self.message = message
        self.details = details or {}
        super().__init__(f"[{step}] {message}")


class AlertManager:
    """Manages alerting for canary test results."""

    # Baseline timings (expected durations in seconds)
    BASELINE_TIMINGS = {
        "step_01_landing": 3.0,
        "step_02_click_get_report": 3.0,
        "step_03_fill_email": 45.0,
        "step_04_verify_user": 3.0,
        "step_05_fill_otp": 70.0,
        "step_06_workspace_details": 5.0,
        "step_07_wait_categories": 60.0,
        "step_08_approve_prompts": 90.0,
        "step_09_wait_snapshot": 300.0,
        "step_10_verify_dashboard": 5.0,
        "step_11_full_verification": 2.0,
    }

    def __init__(self):
        self.config = get_canary_config()

    async def send_failure_alert(self, result: CanaryResult) -> None:
        """Send failure alerts to all configured channels."""
        # Always send to Sentry
        self._send_sentry_alert(result)

        # Always send to Slack if configured
        if self.config.SLACK_WEBHOOK_URL:
            await self._send_slack_report(result, is_failure=True)

    async def send_success_notification(self, result: CanaryResult) -> None:
        """Send success notification with full details."""
        logger.info(
            f"Canary test {result.test_id} completed successfully "
            f"in {result.metrics.total_duration_seconds:.1f}s"
        )

        # Log to Sentry for monitoring
        sentry_sdk.set_context("canary_success", {
            "test_id": result.test_id,
            "duration_seconds": result.metrics.total_duration_seconds,
            "step_timings": result.metrics.step_timings,
            "workspace_id": result.workspace_id
        })

        # Send detailed Slack report
        if self.config.SLACK_WEBHOOK_URL:
            await self._send_slack_report(result, is_failure=False)

    def _send_sentry_alert(self, result: CanaryResult) -> None:
        """Send alert to Sentry."""
        try:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("canary_test", "true")
                scope.set_tag("test_id", result.test_id)
                scope.set_tag("failed_step", result.failed_step or "unknown")

                scope.set_context("canary_test", {
                    "test_id": result.test_id,
                    "failed_step": result.failed_step,
                    "error_message": result.error_message,
                    "total_duration_seconds": result.metrics.total_duration_seconds,
                    "workspace_id": result.workspace_id,
                    "workspace_ulid": result.workspace_ulid
                })

                scope.set_context("step_timings", result.metrics.step_timings)

                if result.db_state:
                    scope.set_context("db_state", result.db_state)

                if result.metrics.errors:
                    scope.set_context("errors", {"errors": result.metrics.errors})

                sentry_sdk.capture_message(
                    f"Canary Test Failed: {result.failed_step} - {result.error_message}",
                    level="error"
                )

            logger.info(f"Sent Sentry alert for canary test {result.test_id}")

        except Exception as e:
            logger.error(f"Failed to send Sentry alert: {e}")

    async def _send_slack_report(self, result: CanaryResult, is_failure: bool) -> None:
        """Send detailed Slack report."""
        if not self.config.SLACK_WEBHOOK_URL:
            return

        try:
            message = self._build_detailed_slack_message(result, is_failure)

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config.SLACK_WEBHOOK_URL,
                    json=message,
                    timeout=30.0
                )

                if response.status_code != 200:
                    logger.error(f"Slack webhook returned {response.status_code}: {response.text}")
                else:
                    logger.info(f"Sent Slack report for canary test {result.test_id}")

        except Exception as e:
            logger.error(f"Failed to send Slack report: {e}")

    def _build_detailed_slack_message(self, result: CanaryResult, is_failure: bool) -> Dict[str, Any]:
        """Build detailed Slack message with all metrics."""

        config = self.config
        now = datetime.now(timezone.utc)

        # Calculate Turkey time (UTC+3)
        from datetime import timedelta
        turkey_time = now + timedelta(hours=3)

        # Determine actual status based on DB data
        db_data = result.metrics.db_data
        actual_success = not is_failure

        # Check if workspace reached COMPLETED
        if db_data and not db_data.get("error"):
            ws_status = db_data.get("workspace", {}).get("status", "")
            if ws_status != "COMPLETED":
                actual_success = False

            # Check if snapshot completed
            snap = db_data.get("snapshot")
            if not snap or snap.get("status") != "COMPLETED":
                actual_success = False

        # Status header
        if is_failure or not actual_success:
            status_emoji = "🚨"
            status_text = "BAŞARISIZ" if is_failure else "EKSİK"
            header_color = "#FF0000" if is_failure else "#FFA500"
        else:
            status_emoji = "✅"
            status_text = "BAŞARILI"
            header_color = "#36A64F"

        # Build step timings analysis
        step_analysis = self._analyze_step_timings(result.metrics.step_timings)

        # Build DB state summary
        db_summary = self._build_db_summary(result.db_state, result.metrics.db_data)

        # Build UI verification summary
        ui_summary = self._build_ui_summary(result.metrics.ui_data)

        # Find slowest steps
        slowest_steps = self._find_slowest_steps(result.metrics.step_timings)

        # Build anomalies list
        anomalies = self._detect_anomalies(result, step_analysis)

        blocks = [
            # Header
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} CANARY TEST RAPORU - {status_text}",
                    "emoji": True
                }
            },
            # Divider
            {"type": "divider"},
            # Date/Time prominently
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📅 Test Zamanı:* `{turkey_time.strftime('%d %b %Y %H:%M')}` 🇹🇷 Türkiye\n_({now.strftime('%H:%M UTC')})_"
                }
            },
            # Basic info
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*🆔 Test ID:*\n`{result.test_id}`"},
                    {"type": "mrkdwn", "text": f"*⏱️ Toplam Süre:*\n`{result.metrics.total_duration_seconds:.1f}s`"}
                ]
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*🌐 Domain:*\n{config.TEST_BRAND_DOMAIN}"},
                    {"type": "mrkdwn", "text": f"*🏢 Workspace:*\n`{result.workspace_ulid or 'N/A'}`"}
                ]
            },
        ]

        # CRITICAL LOADING TIMES - User's key metrics
        loading_1 = result.metrics.step_timings.get("loading_1_form_to_prompts", 0)
        loading_2 = result.metrics.step_timings.get("loading_2_confirm_to_dashboard", 0)

        if loading_1 > 0 or loading_2 > 0:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🚀 KRİTİK YÜKLENİYOR SÜRELERİ*"
                }
            })

            loading_fields = []
            if loading_1 > 0:
                l1_status = "🟢" if loading_1 < 60 else ("🟡" if loading_1 < 120 else "🔴")
                loading_fields.append({
                    "type": "mrkdwn",
                    "text": f"*{l1_status} Loading 1:*\n`{loading_1:.1f}s`\n_Form → Prompts_"
                })
            if loading_2 > 0:
                l2_status = "🟢" if loading_2 < 90 else ("🟡" if loading_2 < 180 else "🔴")
                loading_fields.append({
                    "type": "mrkdwn",
                    "text": f"*{l2_status} Loading 2:*\n`{loading_2:.1f}s`\n_Confirm → Dashboard_"
                })

            if loading_fields:
                blocks.append({
                    "type": "section",
                    "fields": loading_fields
                })

        # Error info if failure
        if is_failure and result.error_message:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*❌ Hata Detayı:*\n```{result.failed_step}: {result.error_message}```"
                }
            })

        # Step Timings
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*📈 ADIM SÜRELERİ*"
            }
        })
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": step_analysis
            }
        })

        # Slowest steps warning
        if slowest_steps:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*⚠️ En Yavaş Adımlar:*\n{slowest_steps}"
                }
            })

        # DB Data
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*📦 VERİ DURUMU (DB)*"
            }
        })
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": db_summary
            }
        })

        # Model Invocations (AI Stats)
        ai_summary = self._build_ai_summary(result.metrics.db_data)
        if ai_summary:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🤖 AI MODEL SÜRELERİ*"
                }
            })
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ai_summary
                }
            })

        # UI Verification
        if ui_summary:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🖥️ UI DOĞRULAMA*"
                }
            })
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ui_summary
                }
            })

        # Anomalies
        if anomalies:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔍 ANOMALİLER*\n{anomalies}"
                }
            })

        # Footer
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Canary Test System | maxeo.ai | {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                }
            ]
        })

        return {"blocks": blocks}

    def _analyze_step_timings(self, timings: Dict[str, float]) -> str:
        """Analyze step timings and create formatted output."""
        if not timings:
            return "Süre verisi yok"

        lines = []
        step_names = {
            "setup": "Setup",
            "step_01_landing": "1. Landing Page",
            "step_02_click_get_report": "2. Get Report Click",
            "step_03_fill_email": "3. Form Doldurma",
            "step_04_verify_user": "4. User Doğrulama",
            "step_05_fill_otp": "5. OTP İşlemi",
            "step_06_workspace_details": "6. Workspace Oluşturma",
            "step_07_wait_categories": "7. Categories Bekleme",
            "step_08_approve_prompts": "8. Prompts Onaylama",
            "step_09_wait_snapshot": "9. Snapshot Bekleme",
            "step_10_verify_dashboard": "10. Dashboard Doğrulama",
            "step_11_full_verification": "11. Final Doğrulama",
        }

        for step_key, duration in timings.items():
            step_name = step_names.get(step_key, step_key)
            baseline = self.BASELINE_TIMINGS.get(step_key, 0)

            # Compare to baseline
            if baseline > 0:
                diff_pct = ((duration - baseline) / baseline) * 100
                if diff_pct > 20:
                    status = f"⚠️ +{diff_pct:.0f}%"
                elif diff_pct < -20:
                    status = f"🚀 {diff_pct:.0f}%"
                else:
                    status = "✅"
            else:
                status = "ℹ️"

            lines.append(f"• {step_name}: `{duration:.1f}s` {status}")

        return "\n".join(lines)

    def _build_db_summary(self, db_state: Optional[Dict], db_data: Dict) -> str:
        """Build DB data summary."""
        lines = []

        # From db_data (comprehensive data - PRIMARY SOURCE)
        if db_data and not db_data.get("error"):
            # Workspace info
            ws = db_data.get("workspace", {})
            if ws:
                lines.append(f"*Workspace:* ✅")
                lines.append(f"  • ID: `{ws.get('id', 'N/A')}`")
                lines.append(f"  • ULID: `{ws.get('ulid', 'N/A')}`")
                lines.append(f"  • Status: `{ws.get('status', 'N/A')}`")

            # Categories
            cat_count = db_data.get("categories_count", 0)
            cat_emoji = "✅" if cat_count >= 3 else "⚠️"
            lines.append(f"\n*Categories:* `{cat_count}` {cat_emoji}")
            if db_data.get("categories_list"):
                for cat in db_data["categories_list"][:5]:
                    lines.append(f"  • {cat.get('name', 'N/A')}")

            # Prompts
            prompt_count = db_data.get("prompts_count", 0)
            prompt_emoji = "✅" if prompt_count >= 15 else "⚠️"
            lines.append(f"\n*Prompts:* `{prompt_count}` {prompt_emoji}")
            if db_data.get("prompts_list"):
                for prompt in db_data["prompts_list"][:5]:
                    name = prompt.get('name', 'N/A')[:40]
                    is_tracked = "📍" if prompt.get('is_tracked') else ""
                    lines.append(f"  • {name} {is_tracked}")

            # Competitors
            comp_count = db_data.get("competitors_count", 0)
            comp_emoji = "✅" if comp_count >= 1 else "⚠️"
            lines.append(f"\n*Competitors:* `{comp_count}` {comp_emoji}")
            if db_data.get("competitors_list"):
                for comp in db_data["competitors_list"][:5]:
                    domain = comp.get('domain', '')
                    lines.append(f"  • {comp.get('name', 'N/A')} ({domain})")

            # Snapshot
            snap = db_data.get("snapshot")
            if snap:
                snap_status = snap.get("status", "N/A")
                snap_emoji = "✅" if snap_status == "COMPLETED" else "⚠️"
                lines.append(f"\n*Snapshot:* `{snap_status}` {snap_emoji}")
                lines.append(f"  • ID: `{snap.get('id', 'N/A')}`")

                # Snapshot prompts status
                sps = db_data.get("snapshot_prompts_status", {})
                if sps:
                    total = sps.get("total", 0)
                    completed = sps.get("completed", 0)
                    failed = sps.get("failed", 0)
                    lines.append(f"  • Prompts: `{completed}/{total}` completed")
                    if failed > 0:
                        lines.append(f"  • Failed: `{failed}` ❌")
            else:
                lines.append(f"\n*Snapshot:* Yok ⚠️")

        # Fallback to db_state (verification results)
        elif db_state and "results" in db_state:
            results = db_state["results"]

            ws = results.get("workspace", {})
            if ws:
                ws_data = ws.get("data", {})
                status_emoji = "✅" if ws.get("success") else "❌"
                lines.append(f"*Workspace:* {status_emoji}")
                if ws_data:
                    lines.append(f"  • ID: `{ws_data.get('workspace_id', 'N/A')}`")
                    lines.append(f"  • ULID: `{ws_data.get('ulid', 'N/A')}`")

            ws_status = results.get("workspace_status", {})
            if ws_status:
                status_emoji = "✅" if ws_status.get("success") else "❌"
                actual_status = ws_status.get("data", {}).get("actual_status", "N/A")
                lines.append(f"*Status:* `{actual_status}` {status_emoji}")

            cats = results.get("categories", {})
            if cats:
                status_emoji = "✅" if cats.get("success") else "❌"
                count = cats.get("data", {}).get("categories_count", 0)
                lines.append(f"*Categories:* `{count}` {status_emoji}")

            prompts = results.get("prompts", {})
            if prompts:
                status_emoji = "✅" if prompts.get("success") else "❌"
                count = prompts.get("data", {}).get("prompts_count", 0)
                lines.append(f"*Prompts:* `{count}` {status_emoji}")

            snapshot = results.get("snapshot", {})
            if snapshot:
                status_emoji = "✅" if snapshot.get("success") else "❌"
                snap_data = snapshot.get("data", {})
                snap_status = snap_data.get("status", "N/A")
                lines.append(f"*Snapshot:* `{snap_status}` {status_emoji}")

            competitors = results.get("competitors", {})
            if competitors:
                status_emoji = "✅" if competitors.get("success") else "❌"
                count = competitors.get("data", {}).get("competitors_count", 0)
                lines.append(f"*Competitors:* `{count}` {status_emoji}")

        return "\n".join(lines) if lines else "Veri toplanamadı ❌"

    def _build_ui_summary(self, ui_data: Dict) -> str:
        """Build UI verification summary."""
        if not ui_data:
            return ""

        lines = []

        if "dashboard_loaded" in ui_data:
            status = "✅" if ui_data["dashboard_loaded"] else "❌"
            lines.append(f"• Dashboard yüklendi: {status}")

        if "charts_visible" in ui_data:
            status = "✅" if ui_data["charts_visible"] else "❌"
            lines.append(f"• Charts görünür: {status}")

        if "current_url" in ui_data:
            lines.append(f"• URL: `{ui_data['current_url']}`")

        if "page_title" in ui_data:
            lines.append(f"• Sayfa: {ui_data['page_title']}")

        return "\n".join(lines) if lines else ""

    def _build_ai_summary(self, db_data: Dict) -> str:
        """Build AI model invocation summary."""
        if not db_data:
            return ""

        model_stats = db_data.get("model_invocations", {})
        slowest = db_data.get("slowest_invocations", [])

        if not model_stats or model_stats.get("total_calls", 0) == 0:
            return ""

        lines = []

        # Total stats
        total_time = model_stats.get("total_time", 0)
        total_cost = model_stats.get("total_cost", 0)
        total_calls = model_stats.get("total_calls", 0)
        total_tokens = model_stats.get("total_tokens", 0)

        lines.append(f"*Toplam:* `{total_calls}` çağrı, `{total_time:.1f}s` süre, `${total_cost:.4f}` maliyet")
        if total_tokens > 0:
            lines.append(f"*Tokenlar:* `{total_tokens:,}` toplam")

        # By model breakdown
        by_model = model_stats.get("by_model", [])
        if by_model:
            lines.append("\n*Model Bazında:*")
            for m in by_model[:5]:  # Top 5 models
                model_name = m.get("model", "?")
                # Shorten model name for display
                if "/" in model_name:
                    model_name = model_name.split("/")[-1]
                if len(model_name) > 25:
                    model_name = model_name[:22] + "..."

                calls = m.get("call_count", 0)
                time = m.get("total_time", 0)
                cost = m.get("total_cost", 0)
                lines.append(f"  • `{model_name}`: {calls}x, {time:.1f}s, ${cost:.4f}")

        # Slowest invocations
        if slowest:
            lines.append("\n*En Yavaş Çağrılar:*")
            for s in slowest[:3]:  # Top 3 slowest
                model = s.get("model", "?")
                if "/" in model:
                    model = model.split("/")[-1]
                if len(model) > 20:
                    model = model[:17] + "..."
                elapsed = s.get("time_elapsed", 0)
                lines.append(f"  • `{model}`: {elapsed:.1f}s")

        return "\n".join(lines)

    def _find_slowest_steps(self, timings: Dict[str, float]) -> str:
        """Find the slowest steps."""
        if not timings:
            return ""

        # Sort by duration
        sorted_steps = sorted(timings.items(), key=lambda x: x[1], reverse=True)

        # Get top 3 slowest
        slowest = sorted_steps[:3]

        lines = []
        step_names = {
            "step_03_fill_email": "Form Doldurma",
            "step_05_fill_otp": "OTP İşlemi",
            "step_07_wait_categories": "Categories",
            "step_08_approve_prompts": "Prompts",
            "step_09_wait_snapshot": "Snapshot",
        }

        for step, duration in slowest:
            if duration > 30:  # Only show if > 30s
                name = step_names.get(step, step)
                baseline = self.BASELINE_TIMINGS.get(step, 0)
                if baseline > 0:
                    diff = duration - baseline
                    if diff > 0:
                        lines.append(f"• {name}: `{duration:.1f}s` (+{diff:.1f}s)")

        return "\n".join(lines) if lines else ""

    def _detect_anomalies(self, result: CanaryResult, step_analysis: str) -> str:
        """Detect anomalies in the test result."""
        anomalies = []
        warnings = []

        # Check from comprehensive DB data
        db_data = result.metrics.db_data
        if db_data and not db_data.get("error"):
            ws = db_data.get("workspace", {})
            ws_status = ws.get("status", "UNKNOWN")

            # Workspace status check
            if ws_status != "COMPLETED":
                anomalies.append(f"🚨 Workspace status `{ws_status}` - COMPLETED olmalı!")

            # Prompts check
            prompt_count = db_data.get("prompts_count", 0)
            if prompt_count < 15:
                anomalies.append(f"🚨 Prompts: `{prompt_count}` - minimum 15 olmalı!")
            elif prompt_count < 20:
                warnings.append(f"⚠️ Prompts düşük: `{prompt_count}`")

            # Snapshot check
            snap = db_data.get("snapshot")
            if not snap:
                anomalies.append("🚨 Snapshot oluşmamış!")
            elif snap.get("status") != "COMPLETED":
                anomalies.append(f"🚨 Snapshot status: `{snap.get('status')}` - COMPLETED olmalı!")

            # Snapshot prompts check
            sps = db_data.get("snapshot_prompts_status", {})
            if sps:
                failed = sps.get("failed", 0)
                if failed > 0:
                    anomalies.append(f"🚨 {failed} snapshot prompt başarısız!")

            # Competitors check
            comp_count = db_data.get("competitors_count", 0)
            if comp_count == 0:
                warnings.append("⚠️ Competitor bulunamadı")

        # Fallback to db_state
        elif result.db_state and "results" in result.db_state:
            results = result.db_state["results"]

            ws_status = results.get("workspace_status", {})
            if ws_status and not ws_status.get("success"):
                actual = ws_status.get("data", {}).get("actual_status", "?")
                anomalies.append(f"🚨 Workspace status `{actual}` - COMPLETED olmalı!")

            prompts = results.get("prompts", {})
            if prompts and not prompts.get("success"):
                count = prompts.get("data", {}).get("prompts_count", 0)
                anomalies.append(f"🚨 Prompts: `{count}` - minimum 15 olmalı!")

            snapshot = results.get("snapshot", {})
            if snapshot and not snapshot.get("success"):
                anomalies.append("🚨 Snapshot oluşmamış!")

        # Check step timings for slowness
        for step, duration in result.metrics.step_timings.items():
            baseline = self.BASELINE_TIMINGS.get(step, 0)
            if baseline > 0:
                ratio = duration / baseline
                if ratio > 2.0:
                    step_name = step.replace("step_", "").replace("_", " ").title()
                    warnings.append(f"⚠️ `{step_name}` %{int((ratio-1)*100)} yavaş ({duration:.1f}s vs {baseline:.1f}s)")
                elif ratio > 1.5:
                    pass  # Minor slowness, don't report

        # Combine anomalies and warnings
        all_issues = anomalies + warnings
        if all_issues:
            return "\n".join(all_issues)
        else:
            return "✅ Anomali tespit edilmedi - tüm sistemler normal"
