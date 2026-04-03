"""Rate limit detection, usage API polling, and layered wait strategy."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx


class RateLimitDetector:
    """Detects rate limit errors from Claude CLI stderr and parses wait times."""

    RATE_LIMIT_PATTERNS: list[str] = [
        "rate limit",
        "rate_limit",
        "429",
        "too many requests",
        "overloaded",
        "quota exceeded",
        "capacity",
        "try again later",
        "limit reached",
        "hit your limit",
        "please wait",
    ]

    QUOTA_LIMIT_PATTERNS: list[str] = [
        "5-hour",
        "5 hour",
        "five hour",
        "weekly",
        "monthly",
    ]

    _DURATION_REGEX = re.compile(
        r"(?:retry after|try again in)\s+(\d+)\s*(seconds?|s|minutes?|m|hours?|h)",
        re.IGNORECASE,
    )

    _RESETS_AT_REGEX = re.compile(
        r"resets?\s+at\s+(\d{1,2}:\d{2})",
        re.IGNORECASE,
    )

    _UNIT_MULTIPLIERS = {
        "s": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hour": 3600,
        "hours": 3600,
    }

    @staticmethod
    def is_rate_limit(stderr: str) -> bool:
        """Check if stderr indicates a rate limit error."""
        lower = stderr.lower()
        return any(p in lower for p in RateLimitDetector.RATE_LIMIT_PATTERNS)

    @staticmethod
    def is_quota_limit(stderr: str) -> bool:
        """Check if stderr indicates a quota-based limit (5-hour, weekly, monthly)."""
        lower = stderr.lower()
        return any(p in lower for p in RateLimitDetector.QUOTA_LIMIT_PATTERNS)

    @staticmethod
    def parse_wait_seconds(stderr: str) -> float | None:
        """Extract an explicit wait duration from stderr.

        Supports patterns like:
        - "retry after 30 seconds"
        - "try again in 2 minutes"
        - "resets at 14:30"

        Returns the wait time in seconds, or None if no timing hint found.
        """
        if not stderr:
            return None

        match = RateLimitDetector._DURATION_REGEX.search(stderr)
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            multiplier = RateLimitDetector._UNIT_MULTIPLIERS.get(unit, 1)
            return float(value * multiplier)

        match = RateLimitDetector._RESETS_AT_REGEX.search(stderr)
        if match:
            time_str = match.group(1)
            now = datetime.now(timezone.utc)
            hour, minute = map(int, time_str.split(":"))
            reset_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset_time <= now:
                reset_time = reset_time.replace(day=now.day + 1)
            delta = (reset_time - now).total_seconds()
            return delta

        return None


_logger = logging.getLogger(__name__)


@dataclass
class UsageStatus:
    """Snapshot of Anthropic API utilization across rate-limit windows."""

    five_hour: float | None = None
    seven_day: float | None = None
    seven_day_sonnet: float | None = None
    seven_day_opus: float | None = None
    resets_at: datetime | None = None
    is_limited: bool = False


class UsageApiClient:
    """Polls the Anthropic OAuth usage API for rate-limit status.

    Reads credentials from ``~/.claude/.credentials.json``.  Degrades
    gracefully (returns ``None``) when credentials are missing, expired
    and un-refreshable, or the API is unreachable.
    """

    USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
    TOKEN_REFRESH_URL = "https://platform.claude.com/v1/oauth/token"
    CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    API_TIMEOUT = 10.0

    def __init__(
        self,
        credentials_path: Path | None = None,
    ) -> None:
        self._credentials_path = credentials_path or (
            Path.home() / ".claude" / ".credentials.json"
        )

    async def _load_access_token(self) -> tuple[str | None, str | None]:
        """Read access + refresh tokens from the credentials file.

        Returns ``(access_token, refresh_token)`` or ``(None, None)``
        if the file is missing or malformed.
        """
        try:
            raw = self._credentials_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return None, None

        oauth = data.get("claudeAiOauth") or data
        access_token = oauth.get("accessToken") or oauth.get("access_token")
        refresh_token = oauth.get("refreshToken") or oauth.get("refresh_token")
        expires_at_ms = oauth.get("expiresAt", 0)

        if not access_token:
            return None, refresh_token

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if expires_at_ms and now_ms >= expires_at_ms:
            return None, refresh_token

        return access_token, refresh_token

    async def _refresh_token(self, refresh_token: str) -> str | None:
        """Exchange a refresh token for a new access token.

        Persists updated credentials to disk on success.
        Returns the new access token, or ``None`` on failure.
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.TOKEN_REFRESH_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self.CLIENT_ID,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=self.API_TIMEOUT,
                )
            if resp.status_code != 200:
                return None

            body = resp.json()
            new_access: str | None = body.get("access_token")
            new_refresh = body.get("refresh_token", refresh_token)
            expires_in = body.get("expires_in", 14400)

            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            new_expires_at = now_ms + expires_in * 1000

            try:
                raw = self._credentials_path.read_text(encoding="utf-8")
                creds = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                creds = {}

            oauth = creds.setdefault("claudeAiOauth", {})
            oauth["accessToken"] = new_access
            oauth["refreshToken"] = new_refresh
            oauth["expiresAt"] = new_expires_at

            tmp = self._credentials_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(creds, indent=2), encoding="utf-8")
            tmp.rename(self._credentials_path)

            return new_access
        except Exception:
            _logger.debug("Token refresh failed", exc_info=True)
            return None

    async def get_utilization(self) -> UsageStatus | None:
        """Query the usage API and return current utilization.

        Returns ``None`` if credentials are unavailable or the API
        request fails.
        """
        access_token, refresh_token = await self._load_access_token()

        if access_token is None and refresh_token:
            access_token = await self._refresh_token(refresh_token)

        if access_token is None:
            return None

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    self.USAGE_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "anthropic-beta": "oauth-2025-04-20",
                    },
                    timeout=self.API_TIMEOUT,
                )
            if resp.status_code != 200:
                return None

            body = resp.json()
        except Exception:
            _logger.debug("Usage API request failed", exc_info=True)
            return None

        status = UsageStatus()
        earliest_reset: datetime | None = None

        for key, attr in [
            ("five_hour", "five_hour"),
            ("seven_day", "seven_day"),
            ("seven_day_sonnet", "seven_day_sonnet"),
            ("seven_day_opus", "seven_day_opus"),
        ]:
            window = body.get(key)
            if window and isinstance(window, dict):
                util = window.get("utilization")
                if util is not None:
                    setattr(status, attr, float(util))
                    if float(util) >= 100.0:
                        status.is_limited = True
                    reset_str = window.get("resets_at")
                    if reset_str:
                        try:
                            reset_dt = datetime.fromisoformat(
                                reset_str.replace("Z", "+00:00")
                            )
                            if earliest_reset is None or reset_dt < earliest_reset:
                                earliest_reset = reset_dt
                        except ValueError:
                            pass

        status.resets_at = earliest_reset
        return status

    async def wait_for_capacity(
        self, poll_interval: float = 30.0, timeout: float = 900.0
    ) -> bool:
        """Poll until utilization drops below 100%.

        Returns ``True`` if capacity became available, ``False`` on timeout.
        """
        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout)

        while datetime.now(timezone.utc) < deadline:
            status = await self.get_utilization()
            if status is None or not status.is_limited:
                return True
            await asyncio.sleep(poll_interval)

        return False


class WaitStrategy:
    """Layered wait-time determination for rate limit retries.

    Layers (checked in order):
    1. Parse explicit wait time from stderr ("retry after 30s")
    2. Poll Anthropic usage API for reset time
    3. Exponential backoff as fallback
    """

    BURST_CAP = 240.0
    QUOTA_CAP = 600.0
    API_CAP = 900.0
    BUFFER = 5.0

    def __init__(
        self,
        usage_client: UsageApiClient | None = None,
        base_delay: float = 30.0,
    ) -> None:
        self._usage_client = usage_client
        self._base_delay = base_delay

    async def determine_wait(
        self,
        stderr: str,
        attempt: int,
        *,
        return_source: bool = False,
    ) -> float | tuple[float, str]:
        """Determine how long to wait before retrying.

        When *return_source* is ``True``, returns a ``(seconds, source)``
        tuple where *source* is one of ``"parsed_stderr"``,
        ``"usage_api"``, or ``"exponential_backoff"``.
        """
        # Layer 1: explicit wait time from stderr
        parsed = RateLimitDetector.parse_wait_seconds(stderr)
        if parsed is not None:
            result = parsed + self.BUFFER
            return (result, "parsed_stderr") if return_source else result

        # Layer 2: usage API reset time
        if self._usage_client:
            status = await self._usage_client.get_utilization()
            if status and status.resets_at:
                delta = (status.resets_at - datetime.now(timezone.utc)).total_seconds()
                if delta > 0:
                    result = min(delta + self.BUFFER, self.API_CAP)
                    return (result, "usage_api") if return_source else result

        # Layer 3: exponential backoff
        if RateLimitDetector.is_quota_limit(stderr):
            cap = self.QUOTA_CAP
        else:
            cap = self.BURST_CAP
        result = min(self._base_delay * (2 ** attempt), cap)
        return (result, "exponential_backoff") if return_source else result
