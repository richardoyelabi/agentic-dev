"""Tests for sprint-scoped spec filtering."""


from agentic_dev.documents.scoping import (
    extract_sprint_feature_ids,
    scope_spec_to_features,
    split_feature_sections,
)


# ---------------------------------------------------------------------------
# extract_sprint_feature_ids
# ---------------------------------------------------------------------------


class TestExtractSprintFeatureIds:
    def test_simple_feature_ids(self):
        scope = "- **Features:** [F001], [F002]"
        assert extract_sprint_feature_ids(scope) == {"F001", "F002"}

    def test_single_feature(self):
        scope = "- **Features:** [F003]"
        assert extract_sprint_feature_ids(scope) == {"F003"}

    def test_existing_prefix_stripped(self):
        scope = "- **Features:** [EXISTING-F001], [F002]"
        assert extract_sprint_feature_ids(scope) == {"F001", "F002"}

    def test_deleted_prefix_stripped(self):
        scope = "- **Features:** [DELETED-F005]"
        assert extract_sprint_feature_ids(scope) == {"F005"}

    def test_no_features(self):
        scope = "Sprint 1: Setup\n- **Dependencies:** none"
        assert extract_sprint_feature_ids(scope) == set()

    def test_multiple_lines(self):
        scope = (
            "## Sprint 1: Auth\n"
            "- **Features:** [F001], [F002]\n"
            "- **Dependencies:** none\n"
        )
        assert extract_sprint_feature_ids(scope) == {"F001", "F002"}

    def test_realistic_sprint_scope(self):
        scope = (
            "## Sprint 2: Dashboard\n"
            "- **Type:** new\n"
            "- **Features:** [F003], [F004]\n"
            "- **Dependencies:** Sprint 1\n"
            "- **Needs Integration:** no\n"
        )
        assert extract_sprint_feature_ids(scope) == {"F003", "F004"}


# ---------------------------------------------------------------------------
# scope_spec_to_features
# ---------------------------------------------------------------------------


BACKEND_SPEC = """\
# Backend Spec
## Tech Stack
- Framework: Django REST Framework
- Database: PostgreSQL
- Testing: Pytest
## Data Models
### [M001] User
- **Features:** [F001]
- **Fields:** id, email, password_hash
- **Relationships:** none
### [M002] Post
- **Features:** [F002]
- **Fields:** id, title, body, author_id
- **Relationships:** FK to User
### [M003] Comment
- **Features:** [F003]
- **Fields:** id, body, post_id, author_id
- **Relationships:** FK to Post, FK to User
## Services & Business Logic
### [S001] AuthService
- **Features:** [F001]
- **Purpose:** handles authentication
- **Inputs/Outputs:** credentials -> token
### [S002] PostService
- **Features:** [F002], [F003]
- **Purpose:** manages posts and comments
- **Inputs/Outputs:** post data -> post object
## Error Handling
- **Error response schema:** {"error": {"code": "...", "message": "..."}}
- **Exception hierarchy:** standard
## Infrastructure
- Database migrations strategy: Django migrations
- Environment variables required: DATABASE_URL, SECRET_KEY
"""

API_CONTRACT = """\
# API Contract
## Authentication
Bearer token via Authorization header
## Error Response Schema
{"error": {"code": "...", "message": "..."}}
## Endpoints
### [E001] POST /api/auth/login
- **Feature:** [F001]
- **Request:** {"email": "...", "password": "..."}
- **Response:** {"token": "..."}
### [E002] GET /api/posts
- **Feature:** [F002]
- **Request:** query params: page, limit
- **Response:** {"posts": [...]}
### [E003] POST /api/posts/{id}/comments
- **Feature:** [F003]
- **Request:** {"body": "..."}
- **Response:** {"comment": {...}}
"""

FRONTEND_SPEC = """\
# Frontend Spec
## Tech Stack
- Framework: Next.js 15
- Styling: Tailwind CSS v4
## Pages & Routes
### [P001] Login — /login
- **Features:** [F001]
- **Components:** LoginForm
- **State:** auth token
### [P002] Dashboard — /dashboard
- **Features:** [F002]
- **Components:** PostList, PostCard
- **State:** posts array
### [P003] Post Detail — /posts/:id
- **Features:** [F002], [F003]
- **Components:** PostView, CommentList, CommentForm
- **State:** post, comments
## Shared Components
### [C001] Navbar
- **Purpose:** navigation bar
- **Props:** user
- **Used by:** [P001], [P002], [P003]
## State Management
- TanStack Query for server state
## Authentication & Authorization
- JWT stored in httpOnly cookie
"""


class TestScopeSpecToFeatures:
    def test_returns_unchanged_when_no_feature_ids(self):
        result = scope_spec_to_features(BACKEND_SPEC, set())
        assert result == BACKEND_SPEC

    def test_returns_unchanged_when_empty_spec(self):
        result = scope_spec_to_features("", {"F001"})
        assert result == ""

    def test_returns_unchanged_when_whitespace_only(self):
        result = scope_spec_to_features("   \n  ", {"F001"})
        assert result == "   \n  "

    def test_filters_backend_spec_to_f001(self):
        result = scope_spec_to_features(BACKEND_SPEC, {"F001"})

        # Should include: M001, S001, Tech Stack, Error Handling, Infrastructure
        assert "[M001] User" in result
        assert "[S001] AuthService" in result
        assert "## Tech Stack" in result
        assert "## Error Handling" in result
        assert "## Infrastructure" in result

        # Should exclude: M002, M003, S002
        assert "[M002] Post" not in result
        assert "[M003] Comment" not in result
        assert "[S002] PostService" not in result

    def test_filters_backend_spec_to_f002_and_f003(self):
        result = scope_spec_to_features(BACKEND_SPEC, {"F002", "F003"})

        # Should include: M002, M003, S002 (references both F002 and F003)
        assert "[M002] Post" in result
        assert "[M003] Comment" in result
        assert "[S002] PostService" in result

        # Should exclude: M001, S001 (only F001)
        assert "[M001] User" not in result
        assert "[S001] AuthService" not in result

    def test_filters_api_contract_singular_feature(self):
        """API contract uses singular **Feature:** instead of **Features:**."""
        result = scope_spec_to_features(API_CONTRACT, {"F001"})

        assert "[E001] POST /api/auth/login" in result
        assert "[E002] GET /api/posts" not in result
        assert "[E003] POST /api/posts/{id}/comments" not in result

        # Document-level sections always included
        assert "## Authentication" in result
        assert "## Error Response Schema" in result

    def test_filters_frontend_spec_to_f001(self):
        result = scope_spec_to_features(FRONTEND_SPEC, {"F001"})

        assert "[P001] Login" in result
        assert "[P002] Dashboard" not in result
        assert "[P003] Post Detail" not in result

        # Shared components without Features line are always kept
        assert "[C001] Navbar" in result
        assert "## State Management" in result
        assert "## Authentication & Authorization" in result

    def test_multi_feature_section_included_on_partial_match(self):
        """A section with Features: [F002], [F003] should be included
        when filtering for just F002."""
        result = scope_spec_to_features(BACKEND_SPEC, {"F002"})

        # S002 references [F002], [F003] — should be included because F002 matches
        assert "[S002] PostService" in result
        # P003 references [F002], [F003] in frontend spec
        result_fe = scope_spec_to_features(FRONTEND_SPEC, {"F002"})
        assert "[P003] Post Detail" in result_fe

    def test_preserves_level2_headers_even_when_all_subsections_filtered(self):
        """Level-2 headers like ## Data Models should remain even if all
        their ### subsections are filtered out."""
        result = scope_spec_to_features(BACKEND_SPEC, {"F003"})

        # Only M003 matches F003, but ## Data Models header should remain
        assert "## Data Models" in result
        assert "[M003] Comment" in result
        assert "[M001] User" not in result

    def test_all_features_returns_everything(self):
        """Passing all feature IDs should return the full spec."""
        all_ids = {"F001", "F002", "F003"}
        result = scope_spec_to_features(BACKEND_SPEC, all_ids)

        assert "[M001] User" in result
        assert "[M002] Post" in result
        assert "[M003] Comment" in result
        assert "[S001] AuthService" in result
        assert "[S002] PostService" in result

    def test_existing_feature_prefix_in_spec(self):
        """Features with EXISTING- prefix should match bare IDs."""
        spec = (
            "# Spec\n"
            "### [M001] User\n"
            "- **Features:** [EXISTING-F001]\n"
            "- **Fields:** id, email\n"
        )
        result = scope_spec_to_features(spec, {"F001"})
        assert "[M001] User" in result

    def test_section_without_features_line_always_included(self):
        spec = (
            "# Spec\n"
            "## Components\n"
            "### [C001] Button\n"
            "- **Purpose:** reusable button\n"
            "- **Used by:** [P001]\n"
            "### [M001] Data\n"
            "- **Features:** [F099]\n"
            "- **Fields:** id\n"
        )
        result = scope_spec_to_features(spec, {"F001"})
        # C001 has no Features line, so always included
        assert "[C001] Button" in result
        # M001 has Features [F099] which doesn't match F001
        assert "[M001] Data" not in result


# ---------------------------------------------------------------------------
# split_feature_sections
# ---------------------------------------------------------------------------

_FEATURES_DOC = (
    "# Features Request\n\n"
    "Some intro preamble.\n\n"
    "## Feature: [F001] Student listing\n"
    "### Acceptance Criteria\n- [ ] shows the list\n\n"
    "## Feature: [F002] Student detail\n"
    "### Acceptance Criteria\n- [ ] shows detail\n\n"
    "## Feature: [F010] Reports\n"
    "### Acceptance Criteria\n- [ ] shows reports\n"
)


class TestSplitFeatureSections:
    def test_returns_one_unit_per_feature_in_order(self):
        sections = split_feature_sections(_FEATURES_DOC)
        assert [fid for fid, _ in sections] == ["F001", "F002", "F010"]

    def test_each_unit_is_self_contained_with_preamble(self):
        sections = dict(split_feature_sections(_FEATURES_DOC))
        f001 = sections["F001"]
        # preamble (title) carried into each unit
        assert "# Features Request" in f001
        assert "Some intro preamble." in f001
        # only this feature's section, not the others
        assert "[F001]" in f001
        assert "[F002]" not in f001 and "[F010]" not in f001
        assert "shows the list" in f001
        assert "shows detail" not in f001

    def test_no_feature_headers_returns_empty(self):
        assert split_feature_sections("# Features Request\n\nNothing here.") == []

    def test_empty_doc_returns_empty(self):
        assert split_feature_sections("") == []
