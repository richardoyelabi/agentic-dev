"""Tests for the ``agentic-dev.yaml`` track-override loader."""

import pytest

from agentic_dev.discovery.override import (
    OVERRIDE_FILENAME,
    load_track_override,
)
from agentic_dev.exceptions import AgenticDevError


class TestLoadTrackOverride:
    def test_returns_none_when_file_missing(self, tmp_path) -> None:
        assert load_track_override(tmp_path) is None

    def test_loads_single_track(self, tmp_path) -> None:
        (tmp_path / OVERRIDE_FILENAME).write_text(
            "tracks:\n"
            "  - name: app\n"
            "    path: .\n"
            "    kind: web\n"
            "    uat_kind: web\n",
            encoding="utf-8",
        )

        tracks = load_track_override(tmp_path)

        assert tracks is not None
        assert len(tracks) == 1
        assert tracks[0].name == "app"
        assert tracks[0].path == "."
        assert tracks[0].kind == "web"
        assert tracks[0].uat_kind == "web"

    def test_loads_multi_track(self, tmp_path) -> None:
        (tmp_path / OVERRIDE_FILENAME).write_text(
            "tracks:\n"
            "  - name: backend\n"
            "    path: backend\n"
            "    kind: api\n"
            "    uat_kind: api\n"
            "  - name: frontend\n"
            "    path: frontend\n"
            "    kind: web\n"
            "    uat_kind: web\n",
            encoding="utf-8",
        )

        tracks = load_track_override(tmp_path)

        assert tracks is not None
        assert {t.name for t in tracks} == {"backend", "frontend"}
        assert {t.kind for t in tracks} == {"api", "web"}

    def test_empty_tracks_list_raises(self, tmp_path) -> None:
        (tmp_path / OVERRIDE_FILENAME).write_text("tracks: []\n", encoding="utf-8")

        with pytest.raises(AgenticDevError, match="at least one track"):
            load_track_override(tmp_path)

    def test_non_mapping_raises(self, tmp_path) -> None:
        (tmp_path / OVERRIDE_FILENAME).write_text(
            "- just a list\n", encoding="utf-8"
        )

        with pytest.raises(AgenticDevError, match="mapping"):
            load_track_override(tmp_path)

    def test_invalid_track_data_raises(self, tmp_path) -> None:
        # Name with a space violates the slug pattern.
        (tmp_path / OVERRIDE_FILENAME).write_text(
            "tracks:\n  - name: has space\n    kind: web\n",
            encoding="utf-8",
        )

        with pytest.raises(Exception):  # pydantic ValidationError
            load_track_override(tmp_path)
