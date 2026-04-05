"""Tests for rate limit detection, usage API client, and wait strategy."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.claude.rate_limiter import (
    RateLimitDetector,
    UsageApiClient,
    UsageStatus,
    WaitStrategy,
)


class TestIsRateLimit:
    """Tests for RateLimitDetector.is_rate_limit pattern matching."""

    @pytest.mark.parametrize(
        "stderr",
        [
            "Error: rate limit exceeded",
            "rate_limit: too many requests",
            "HTTP 429: Too Many Requests",
            "too many requests, please slow down",
            "Server overloaded, try again",
            "Quota exceeded for model claude-opus-4-6",
            "API capacity reached",
            "Try again later after cooldown",
            "Limit reached for this billing period",
            "You've hit your limit for the day",
            "Please wait before making another request",
        ],
    )
    def test_detects_rate_limit_patterns(self, stderr: str):
        assert RateLimitDetector.is_rate_limit(stderr) is True

    @pytest.mark.parametrize(
        "stderr",
        [
            "Segmentation fault (core dumped)",
            "Connection refused",
            "Permission denied",
            "File not found: config.json",
            "Invalid JSON in response",
            "",
        ],
    )
    def test_rejects_non_rate_limit_errors(self, stderr: str):
        assert RateLimitDetector.is_rate_limit(stderr) is False

    def test_case_insensitive(self):
        assert RateLimitDetector.is_rate_limit("RATE LIMIT exceeded") is True
        assert RateLimitDetector.is_rate_limit("Rate Limit") is True


class TestIsQuotaLimit:
    """Tests for RateLimitDetector.is_quota_limit pattern matching."""

    @pytest.mark.parametrize(
        "stderr",
        [
            "5-hour usage limit reached",
            "5 hour rate limit exceeded",
            "five hour window exhausted",
            "weekly quota exceeded",
            "monthly limit reached",
        ],
    )
    def test_detects_quota_limit_patterns(self, stderr: str):
        assert RateLimitDetector.is_quota_limit(stderr) is True

    @pytest.mark.parametrize(
        "stderr",
        [
            "rate limit exceeded",
            "too many requests",
            "429",
        ],
    )
    def test_rejects_non_quota_patterns(self, stderr: str):
        assert RateLimitDetector.is_quota_limit(stderr) is False


class TestParseWaitSeconds:
    """Tests for RateLimitDetector.parse_wait_seconds stderr parsing."""

    def test_parse_retry_after_seconds(self):
        result = RateLimitDetector.parse_wait_seconds("retry after 30 seconds")
        assert result == 30.0

    def test_parse_retry_after_seconds_singular(self):
        result = RateLimitDetector.parse_wait_seconds("retry after 1 second")
        assert result == 1.0

    def test_parse_retry_after_short_form(self):
        result = RateLimitDetector.parse_wait_seconds("retry after 45s")
        assert result == 45.0

    def test_parse_retry_after_minutes(self):
        result = RateLimitDetector.parse_wait_seconds("retry after 2 minutes")
        assert result == 120.0

    def test_parse_retry_after_minutes_short(self):
        result = RateLimitDetector.parse_wait_seconds("retry after 5m")
        assert result == 300.0

    def test_parse_retry_after_hours(self):
        result = RateLimitDetector.parse_wait_seconds("retry after 1 hour")
        assert result == 3600.0

    def test_parse_try_again_in(self):
        result = RateLimitDetector.parse_wait_seconds("try again in 60 seconds")
        assert result == 60.0

    def test_parse_resets_at_future_time(self):
        fixed_now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        future = fixed_now + timedelta(minutes=5)
        time_str = future.strftime("%H:%M")
        stderr = f"Rate limit resets at {time_str} UTC"
        with patch("agentic_dev.claude.rate_limiter.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = RateLimitDetector.parse_wait_seconds(stderr)
        assert result is not None
        assert result == 300.0

    def test_parse_no_timing_hint(self):
        result = RateLimitDetector.parse_wait_seconds("rate limit exceeded")
        assert result is None

    def test_parse_empty_string(self):
        result = RateLimitDetector.parse_wait_seconds("")
        assert result is None

    def test_parse_case_insensitive(self):
        result = RateLimitDetector.parse_wait_seconds("Retry After 30 Seconds")
        assert result == 30.0


class TestUsageApiClient:
    """Tests for UsageApiClient credential loading and API polling."""

    def _make_credentials(self, tmp_path: Path, expires_in_hours: float = 4.0) -> Path:
        """Create a fake credentials file."""
        creds_path = tmp_path / ".credentials.json"
        expires_at = int(
            (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).timestamp()
            * 1000
        )
        creds_path.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-fake-token",
                "refreshToken": "sk-ant-ort01-fake-refresh",
                "expiresAt": expires_at,
            }
        }))
        return creds_path

    @staticmethod
    def _mock_httpx_client(get_response=None, post_response=None):
        """Create a properly mocked httpx.AsyncClient context manager."""
        mock_client = AsyncMock()
        if get_response is not None:
            mock_client.get.return_value = get_response
        if post_response is not None:
            mock_client.post.return_value = post_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @staticmethod
    def _mock_response(status_code: int, json_data: dict) -> MagicMock:
        """Create a mock httpx.Response (sync .json() method)."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data
        return resp

    async def test_get_utilization_success(self, tmp_path: Path):
        creds_path = self._make_credentials(tmp_path)
        client = UsageApiClient(credentials_path=creds_path)

        resp = self._mock_response(200, {
            "five_hour": {"utilization": 85.0, "resets_at": "2026-04-03T18:00:00Z"},
            "seven_day": {"utilization": 42.0, "resets_at": "2026-04-07T00:00:00Z"},
        })
        mock_client = self._mock_httpx_client(get_response=resp)

        with patch("agentic_dev.claude.rate_limiter.httpx.AsyncClient", return_value=mock_client):
            status = await client.get_utilization()

        assert status is not None
        assert status.five_hour == 85.0
        assert status.seven_day == 42.0
        assert status.is_limited is False

    async def test_get_utilization_detects_limited(self, tmp_path: Path):
        creds_path = self._make_credentials(tmp_path)
        client = UsageApiClient(credentials_path=creds_path)

        resp = self._mock_response(200, {
            "five_hour": {"utilization": 100.0, "resets_at": "2026-04-03T18:00:00Z"},
        })
        mock_client = self._mock_httpx_client(get_response=resp)

        with patch("agentic_dev.claude.rate_limiter.httpx.AsyncClient", return_value=mock_client):
            status = await client.get_utilization()

        assert status is not None
        assert status.is_limited is True
        assert status.resets_at is not None

    async def test_get_utilization_no_credentials(self, tmp_path: Path):
        nonexistent = tmp_path / "nope" / ".credentials.json"
        client = UsageApiClient(credentials_path=nonexistent)

        status = await client.get_utilization()
        assert status is None

    async def test_get_utilization_api_failure(self, tmp_path: Path):
        creds_path = self._make_credentials(tmp_path)
        client = UsageApiClient(credentials_path=creds_path)

        resp = self._mock_response(500, {"error": "internal server error"})
        mock_client = self._mock_httpx_client(get_response=resp)

        with patch("agentic_dev.claude.rate_limiter.httpx.AsyncClient", return_value=mock_client):
            status = await client.get_utilization()

        assert status is None

    async def test_get_utilization_expired_token_refreshes(self, tmp_path: Path):
        creds_path = self._make_credentials(tmp_path, expires_in_hours=-1.0)
        client = UsageApiClient(credentials_path=creds_path)

        refresh_resp = self._mock_response(200, {
            "access_token": "sk-ant-oat01-refreshed-token",
            "refresh_token": "sk-ant-ort01-new-refresh",
            "expires_in": 14400,
        })
        usage_resp = self._mock_response(200, {
            "five_hour": {"utilization": 50.0},
        })
        mock_client = self._mock_httpx_client(
            get_response=usage_resp, post_response=refresh_resp,
        )

        with patch("agentic_dev.claude.rate_limiter.httpx.AsyncClient", return_value=mock_client):
            status = await client.get_utilization()

        assert status is not None
        assert status.five_hour == 50.0
        mock_client.post.assert_called_once()

    async def test_wait_for_capacity_clears(self, tmp_path: Path):
        creds_path = self._make_credentials(tmp_path)
        client = UsageApiClient(credentials_path=creds_path)

        call_count = 0

        async def mock_get_utilization():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return UsageStatus(five_hour=100.0, is_limited=True)
            return UsageStatus(five_hour=80.0, is_limited=False)

        with patch.object(client, "get_utilization", side_effect=mock_get_utilization):
            with patch("agentic_dev.claude.rate_limiter.asyncio.sleep", new_callable=AsyncMock):
                result = await client.wait_for_capacity(poll_interval=0.1, timeout=10.0)

        assert result is True
        assert call_count == 3

    async def test_wait_for_capacity_timeout(self, tmp_path: Path):
        creds_path = self._make_credentials(tmp_path)
        client = UsageApiClient(credentials_path=creds_path)

        async def always_limited():
            return UsageStatus(five_hour=100.0, is_limited=True)

        with patch.object(client, "get_utilization", side_effect=always_limited):
            with patch("agentic_dev.claude.rate_limiter.asyncio.sleep", new_callable=AsyncMock):
                result = await client.wait_for_capacity(poll_interval=0.1, timeout=0.25)

        assert result is False


class TestWaitStrategy:
    """Tests for WaitStrategy layered wait-time determination."""

    async def test_layer1_parsed_stderr_takes_priority(self):
        """Explicit 'retry after' in stderr should override all other layers."""
        mock_usage = AsyncMock()
        mock_usage.get_utilization.return_value = UsageStatus(
            five_hour=100.0,
            is_limited=True,
            resets_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        strategy = WaitStrategy(usage_client=mock_usage, base_delay=30.0)

        wait = await strategy.determine_wait("retry after 45 seconds", attempt=0)

        assert wait == 50.0  # 45 + 5 buffer
        mock_usage.get_utilization.assert_not_called()

    async def test_layer2_usage_api_when_no_parse(self):
        """When stderr has no timing hint, use the usage API reset time."""
        reset_time = datetime.now(timezone.utc) + timedelta(seconds=60)
        mock_usage = AsyncMock()
        mock_usage.get_utilization.return_value = UsageStatus(
            five_hour=100.0,
            is_limited=True,
            resets_at=reset_time,
        )
        strategy = WaitStrategy(usage_client=mock_usage, base_delay=30.0)

        wait = await strategy.determine_wait("rate limit exceeded", attempt=0)

        assert 55.0 < wait < 75.0  # ~60 + 5 buffer, with tolerance
        mock_usage.get_utilization.assert_called_once()

    async def test_layer3_exponential_when_no_api(self):
        """Without API client, fall back to exponential backoff."""
        strategy = WaitStrategy(usage_client=None, base_delay=30.0)

        wait = await strategy.determine_wait("rate limit exceeded", attempt=0)
        assert wait == 30.0

        wait = await strategy.determine_wait("rate limit exceeded", attempt=1)
        assert wait == 60.0

        wait = await strategy.determine_wait("rate limit exceeded", attempt=2)
        assert wait == 120.0

    async def test_burst_limit_caps_at_240(self):
        """Burst limits (no quota pattern) cap at 240 seconds."""
        strategy = WaitStrategy(usage_client=None, base_delay=30.0)

        wait = await strategy.determine_wait("rate limit exceeded", attempt=10)
        assert wait == 240.0

    async def test_quota_limit_caps_at_600(self):
        """Quota limits (5-hour/weekly) cap at 600 seconds."""
        strategy = WaitStrategy(usage_client=None, base_delay=30.0)

        wait = await strategy.determine_wait("5-hour rate limit exceeded", attempt=10)
        assert wait == 600.0

    async def test_usage_api_failure_falls_through_to_backoff(self):
        """If usage API returns None, fall through to exponential backoff."""
        mock_usage = AsyncMock()
        mock_usage.get_utilization.return_value = None
        strategy = WaitStrategy(usage_client=mock_usage, base_delay=30.0)

        wait = await strategy.determine_wait("rate limit exceeded", attempt=1)

        assert wait == 60.0  # exponential backoff: 30 * 2^1

    async def test_usage_api_no_reset_time_falls_through(self):
        """If API returns status but no resets_at, fall through to backoff."""
        mock_usage = AsyncMock()
        mock_usage.get_utilization.return_value = UsageStatus(
            five_hour=100.0, is_limited=True, resets_at=None,
        )
        strategy = WaitStrategy(usage_client=mock_usage, base_delay=30.0)

        wait = await strategy.determine_wait("rate limit exceeded", attempt=0)

        assert wait == 30.0  # falls through to backoff

    async def test_determine_wait_returns_source(self):
        """determine_wait returns a (wait, source) tuple."""
        strategy = WaitStrategy(usage_client=None, base_delay=30.0)

        wait, source = await strategy.determine_wait(
            "retry after 10 seconds", attempt=0, return_source=True,
        )
        assert wait == 15.0
        assert source == "parsed_stderr"

        wait, source = await strategy.determine_wait(
            "rate limit exceeded", attempt=0, return_source=True,
        )
        assert wait == 30.0
        assert source == "exponential_backoff"
