"""Tests for the Track abstraction."""

import pytest

from agentic_dev.tracks import (
    Track,
    TrackPhase,
    TrackProgress,
    default_tracks,
    expected_architecture_docs,
    parse_track_spec,
)


class TestTrack:
    def test_minimal_track(self):
        track = Track(name="web")
        assert track.name == "web"
        assert track.path == "."
        assert track.kind == "generic"
        assert track.uat_kind is None

    def test_full_track(self):
        track = Track(name="api", path="services/api", kind="api", uat_kind="api")
        assert track.path == "services/api"
        assert track.kind == "api"
        assert track.uat_kind == "api"

    def test_rejects_uppercase_name(self):
        with pytest.raises(ValueError, match="must match"):
            Track(name="Web")

    def test_rejects_invalid_chars(self):
        with pytest.raises(ValueError, match="must match"):
            Track(name="my service")

    def test_allows_hyphen_and_underscore(self):
        Track(name="api-v2")
        Track(name="api_v2")


class TestParseTrackSpec:
    def test_name_only(self):
        track = parse_track_spec("web")
        assert track == Track(name="web", path="web", kind="generic", uat_kind=None)

    def test_name_and_path(self):
        track = parse_track_spec("api::services/api")
        assert track.name == "api"
        assert track.path == "services/api"
        assert track.kind == "generic"

    def test_name_path_kind(self):
        track = parse_track_spec("worker::workers/jobs::worker")
        assert track.kind == "worker"

    def test_all_fields(self):
        track = parse_track_spec("web::frontend::web::web")
        assert track.uat_kind == "web"

    def test_empty_path_defaults_to_name(self):
        track = parse_track_spec("api::")
        assert track.path == "api"

    def test_empty_kind_defaults_to_generic(self):
        track = parse_track_spec("api::path::")
        assert track.kind == "generic"

    def test_empty_spec_raises(self):
        with pytest.raises(ValueError, match="missing name"):
            parse_track_spec("")

    def test_blank_name_raises(self):
        with pytest.raises(ValueError, match="missing name"):
            parse_track_spec("::path")


class TestExpectedArchitectureDocs:
    def test_single_generic_track(self):
        tracks = [Track(name="app")]
        assert expected_architecture_docs(tracks) == ["app_spec"]

    def test_single_api_track_adds_contract(self):
        tracks = [Track(name="api", kind="api")]
        assert expected_architecture_docs(tracks) == ["api_spec", "api_contract"]

    def test_multi_track_with_api(self):
        tracks = [
            Track(name="web", kind="web", uat_kind="web"),
            Track(name="api", kind="api", uat_kind="api"),
            Track(name="worker", kind="worker"),
        ]
        assert expected_architecture_docs(tracks) == [
            "web_spec", "api_spec", "worker_spec", "api_contract",
        ]

    def test_multi_track_without_api(self):
        tracks = [
            Track(name="cli", kind="cli"),
            Track(name="lib", kind="library"),
        ]
        assert expected_architecture_docs(tracks) == ["cli_spec", "lib_spec"]


class TestDefaultTracks:
    def test_default_single_app_track(self):
        tracks = default_tracks()
        assert len(tracks) == 1
        assert tracks[0].name == "app"
        assert tracks[0].path == "."
        assert tracks[0].kind == "generic"


class TestTrackProgress:
    def test_pending_default(self):
        progress = TrackProgress(track_name="web")
        assert progress.phase == TrackPhase.PENDING
        assert progress.session_id is None
        assert progress.failed_at_phase is None

    def test_serialization_roundtrip(self):
        progress = TrackProgress(
            track_name="api",
            phase=TrackPhase.QA,
            session_id="sess-123",
            failed_at_phase=TrackPhase.DEV,
        )
        data = progress.model_dump()
        restored = TrackProgress.model_validate(data)
        assert restored == progress


class TestTrackPhase:
    def test_values(self):
        assert TrackPhase.PENDING == "pending"
        assert TrackPhase.DEV == "dev"
        assert TrackPhase.QA == "qa"
        assert TrackPhase.CORRECTION == "correction"
        assert TrackPhase.COMPLETE == "complete"
        assert TrackPhase.FAILED == "failed"
