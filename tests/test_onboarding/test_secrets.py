"""Tests for ``parse_secrets_template`` — the .env placeholder parser."""

from pathlib import Path

from agentic_dev.onboarding.secrets import SecretsState, parse_secrets_template


class TestParseSecretsTemplate:
    """``parse_secrets_template`` classifies entries as filled or placeholder.

    The placeholder syntax is ``KEY=<FILL ME: hint>``. Any other value is
    treated as filled, including empty strings (which the user may have set
    intentionally). Comment lines and blank lines are ignored.
    """

    def test_missing_file_returns_empty_state(self, tmp_path: Path) -> None:
        state = parse_secrets_template(tmp_path / "missing.env")

        assert isinstance(state, SecretsState)
        assert state.filled == {}
        assert state.unfilled_required == []
        assert state.has_unfilled_required() is False

    def test_empty_file_has_no_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.env"
        path.write_text("")

        state = parse_secrets_template(path)

        assert state.has_unfilled_required() is False

    def test_all_filled_values(self, tmp_path: Path) -> None:
        path = tmp_path / "filled.env"
        path.write_text("DJANGO_SECRET_KEY=abc123\nDATABASE_URL=postgres://x\n")

        state = parse_secrets_template(path)

        assert state.filled == {
            "DJANGO_SECRET_KEY": "abc123",
            "DATABASE_URL": "postgres://x",
        }
        assert state.has_unfilled_required() is False

    def test_placeholder_flagged_as_unfilled(self, tmp_path: Path) -> None:
        path = tmp_path / "mixed.env"
        path.write_text(
            "DJANGO_SECRET_KEY=abc123\n"
            "AGORA_APP_ID=<FILL ME: get from Agora console>\n"
        )

        state = parse_secrets_template(path)

        assert state.filled == {"DJANGO_SECRET_KEY": "abc123"}
        assert state.unfilled_required == ["AGORA_APP_ID"]
        assert state.has_unfilled_required() is True

    def test_multiple_placeholders_listed_in_order(self, tmp_path: Path) -> None:
        path = tmp_path / "many.env"
        path.write_text(
            "AGORA_APP_ID=<FILL ME: app id>\n"
            "AGORA_APP_CERTIFICATE=<FILL ME: cert>\n"
            "RESEND_API_KEY=<FILL ME>\n"
        )

        state = parse_secrets_template(path)

        assert state.unfilled_required == [
            "AGORA_APP_ID",
            "AGORA_APP_CERTIFICATE",
            "RESEND_API_KEY",
        ]

    def test_ignores_comments_and_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "with-comments.env"
        path.write_text(
            "# auto-filled\nDJANGO_SECRET_KEY=abc\n\n# user-supplied\n"
            "AGORA_APP_ID=<FILL ME>\n"
        )

        state = parse_secrets_template(path)

        assert state.filled == {"DJANGO_SECRET_KEY": "abc"}
        assert state.unfilled_required == ["AGORA_APP_ID"]

    def test_quoted_values_treated_as_filled(self, tmp_path: Path) -> None:
        path = tmp_path / "quoted.env"
        path.write_text('TOKEN="hello world"\nSECRET=\'abc\'\n')

        state = parse_secrets_template(path)

        assert state.filled == {"TOKEN": "hello world", "SECRET": "abc"}
        assert state.has_unfilled_required() is False

    def test_empty_value_not_required(self, tmp_path: Path) -> None:
        """Empty values are intentional opt-outs, not placeholders."""
        path = tmp_path / "empty-val.env"
        path.write_text("OPTIONAL_KEY=\n")

        state = parse_secrets_template(path)

        assert state.has_unfilled_required() is False
        assert state.filled == {"OPTIONAL_KEY": ""}
