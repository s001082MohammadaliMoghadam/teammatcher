import csv
import io

from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status

from .models import TeamSet, Team, TeamAssignment, CourseEnrollment
from services.csv_service import validate_csv_file, resolve_user_identity


def _make_csv_bytes(headers, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return io.BytesIO(buf.getvalue().encode("utf-8"))


# ── Participant A: Model Tests ─────────────────────────────────────────────────

class TeamSetModelTest(TestCase):

    def test_create_with_course_id(self):
        ts = TeamSet.objects.create(name="discussion-teams", course_id="course-v1:Org+101+2026")
        self.assertEqual(TeamSet.objects.count(), 1)
        self.assertIn("discussion-teams", str(ts))

    def test_str_includes_course_id(self):
        ts = TeamSet.objects.create(name="labs", course_id="course-v1:Org+101+2026")
        self.assertEqual(str(ts), "labs (course-v1:Org+101+2026)")

    def test_same_name_different_course_allowed(self):
        TeamSet.objects.create(name="teams", course_id="course-A")
        TeamSet.objects.create(name="teams", course_id="course-B")
        self.assertEqual(TeamSet.objects.filter(name="teams").count(), 2)

    def test_same_name_same_course_raises(self):
        TeamSet.objects.create(name="teams", course_id="course-A")
        with self.assertRaises(Exception):
            TeamSet.objects.create(name="teams", course_id="course-A")


class TeamModelTest(TestCase):

    def setUp(self):
        self.team_set = TeamSet.objects.create(name="discussion-teams", course_id="course-A")

    def test_create_and_str(self):
        team = Team.objects.create(name="Team Alpha", team_set=self.team_set)
        self.assertEqual(str(team), "discussion-teams - Team Alpha")

    def test_default_max_members_is_five(self):
        team = Team.objects.create(name="Team Beta", team_set=self.team_set)
        self.assertEqual(team.max_members, 5)

    def test_not_full_when_empty(self):
        team = Team.objects.create(name="Team Beta", team_set=self.team_set, max_members=3)
        self.assertFalse(team.is_full)
        self.assertEqual(team.current_member_count, 0)

    def test_is_full_at_capacity(self):
        team = Team.objects.create(name="Team Gamma", team_set=self.team_set, max_members=2)
        for i in range(2):
            user = User.objects.create_user(username=f"user{i}", password="pass")
            TeamAssignment.objects.create(learner=user, team=team)
        self.assertTrue(team.is_full)
        self.assertEqual(team.current_member_count, 2)

    def test_duplicate_name_in_same_set_raises(self):
        Team.objects.create(name="Team X", team_set=self.team_set)
        with self.assertRaises(Exception):
            Team.objects.create(name="Team X", team_set=self.team_set)

    def test_same_name_in_different_set_allowed(self):
        other_set = TeamSet.objects.create(name="other-set", course_id="course-B")
        Team.objects.create(name="Team X", team_set=self.team_set)
        Team.objects.create(name="Team X", team_set=other_set)
        self.assertEqual(Team.objects.filter(name="Team X").count(), 2)


class TeamAssignmentModelTest(TestCase):

    def setUp(self):
        self.team_set = TeamSet.objects.create(name="test-set", course_id="course-A")
        self.team = Team.objects.create(name="Team A", team_set=self.team_set)
        self.user = User.objects.create_user(username="student1", password="pass")

    def test_create_assignment_str_and_active(self):
        assignment = TeamAssignment.objects.create(learner=self.user, team=self.team)
        self.assertTrue(assignment.is_active)
        self.assertEqual(str(assignment), "student1 -> Team A")

    def test_duplicate_assignment_raises(self):
        TeamAssignment.objects.create(learner=self.user, team=self.team)
        with self.assertRaises(Exception):
            TeamAssignment.objects.create(learner=self.user, team=self.team)

    def test_assignment_deleted_when_team_deleted(self):
        TeamAssignment.objects.create(learner=self.user, team=self.team)
        self.team.delete()
        self.assertEqual(TeamAssignment.objects.count(), 0)


# ── Participant A: API Tests ───────────────────────────────────────────────────

class TeamSetAPITest(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_list_team_sets(self):
        TeamSet.objects.create(name="set-a", course_id="c1")
        response = self.client.get("/api/team-sets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_create_team_set(self):
        response = self.client.post("/api/team-sets/", {"name": "project-teams", "course_id": "c1"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(TeamSet.objects.count(), 1)

    def test_retrieve_team_set(self):
        ts = TeamSet.objects.create(name="set-a", course_id="c1")
        response = self.client.get(f"/api/team-sets/{ts.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "set-a")

    def test_delete_team_set(self):
        ts = TeamSet.objects.create(name="set-a", course_id="c1")
        response = self.client.delete(f"/api/team-sets/{ts.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(TeamSet.objects.count(), 0)


class TeamAPITest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.team_set = TeamSet.objects.create(name="ts", course_id="c1")

    def test_create_team(self):
        response = self.client.post("/api/teams/", {"name": "Team Alpha", "team_set": self.team_set.id, "max_members": 4})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_list_teams(self):
        Team.objects.create(name="Team A", team_set=self.team_set)
        response = self.client.get("/api/teams/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_filter_teams_by_team_set(self):
        other_set = TeamSet.objects.create(name="other", course_id="c2")
        Team.objects.create(name="Team A", team_set=self.team_set)
        Team.objects.create(name="Team B", team_set=other_set)
        response = self.client.get(f"/api/teams/?team_set={self.team_set.id}")
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Team A")

    def test_retrieve_team(self):
        team = Team.objects.create(name="Team A", team_set=self.team_set)
        response = self.client.get(f"/api/teams/{team.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_team(self):
        team = Team.objects.create(name="Team A", team_set=self.team_set)
        response = self.client.delete(f"/api/teams/{team.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Team.objects.count(), 0)


class TeamAssignmentAPITest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.team_set = TeamSet.objects.create(name="ts", course_id="c1")
        self.team = Team.objects.create(name="Team A", team_set=self.team_set, max_members=5)
        self.user = User.objects.create_user(username="student1", password="pass")

    def test_list_assignments(self):
        TeamAssignment.objects.create(learner=self.user, team=self.team)
        response = self.client.get("/api/assignments/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_create_assignment(self):
        response = self.client.post("/api/assignments/", {"learner": self.user.id, "team": self.team.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_assignment_missing_team_returns_400(self):
        response = self.client.post("/api/assignments/", {"learner": self.user.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_assignment_missing_learner_returns_400(self):
        response = self.client.post("/api/assignments/", {"team": self.team.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_assignment_rejected(self):
        TeamAssignment.objects.create(learner=self.user, team=self.team)
        response = self.client.post("/api/assignments/", {"learner": self.user.id, "team": self.team.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_full_team_rejects_new_assignment(self):
        full_team = Team.objects.create(name="Tiny", team_set=self.team_set, max_members=1)
        u1 = User.objects.create_user(username="s1", password="pass")
        u2 = User.objects.create_user(username="s2", password="pass")
        self.client.post("/api/assignments/", {"learner": u1.id, "team": full_team.id})
        response = self.client.post("/api/assignments/", {"learner": u2.id, "team": full_team.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_assignments_by_learner(self):
        other_user = User.objects.create_user(username="other", password="pass")
        other_team = Team.objects.create(name="Team B", team_set=self.team_set)
        TeamAssignment.objects.create(learner=self.user, team=self.team)
        TeamAssignment.objects.create(learner=other_user, team=other_team)
        response = self.client.get(f"/api/assignments/?learner={self.user.id}")
        self.assertEqual(len(response.data), 1)

    def test_filter_assignments_by_team(self):
        other_user = User.objects.create_user(username="other", password="pass")
        other_team = Team.objects.create(name="Team B", team_set=self.team_set)
        TeamAssignment.objects.create(learner=self.user, team=self.team)
        TeamAssignment.objects.create(learner=other_user, team=other_team)
        response = self.client.get(f"/api/assignments/?team={self.team.id}")
        self.assertEqual(len(response.data), 1)

    def test_patch_moves_student_to_new_team(self):
        team2 = Team.objects.create(name="Team B", team_set=self.team_set)
        assignment = TeamAssignment.objects.create(learner=self.user, team=self.team)
        response = self.client.patch(f"/api/assignments/{assignment.id}/", {"team": team2.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assignment.refresh_from_db()
        self.assertEqual(assignment.team, team2)

    def test_patch_to_full_team_rejected(self):
        full_team = Team.objects.create(name="Full", team_set=self.team_set, max_members=1)
        blocker = User.objects.create_user(username="blocker", password="pass")
        TeamAssignment.objects.create(learner=blocker, team=full_team)
        assignment = TeamAssignment.objects.create(learner=self.user, team=self.team)
        response = self.client.patch(f"/api/assignments/{assignment.id}/", {"team": full_team.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ── Participant C: CSV Service Tests ──────────────────────────────────────────

class ValidateCSVFileTest(TestCase):

    def _make(self, headers, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        for row in rows:
            w.writerow(row)
        return buf.getvalue().encode("utf-8")

    def test_valid_csv_is_accepted(self):
        data = self._make(["user", "mode", "discussion-teams"], [["alice", "audit", "Team A"]])
        result = validate_csv_file(data)
        self.assertTrue(result["valid"])
        self.assertEqual(result["team_sets"], ["discussion-teams"])
        self.assertEqual(len(result["rows"]), 1)

    def test_empty_file_rejected(self):
        result = validate_csv_file(b"   ")
        self.assertFalse(result["valid"])
        self.assertIn("empty", result["errors"][0].lower())

    def test_non_utf8_encoding_rejected(self):
        result = validate_csv_file(b"\xff\xfe invalid bytes")
        self.assertFalse(result["valid"])
        self.assertIn("UTF-8", result["errors"][0])

    def test_single_column_header_rejected(self):
        result = validate_csv_file(b"user\nalice\n")
        self.assertFalse(result["valid"])

    def test_wrong_header_order_rejected(self):
        data = self._make(["mode", "user"], [["audit", "alice"]])
        result = validate_csv_file(data)
        self.assertFalse(result["valid"])
        self.assertIn("sequence", result["errors"][0].lower())

    def test_invalid_mode_rejected(self):
        data = self._make(["user", "mode"], [["alice", "observer"]])
        result = validate_csv_file(data)
        self.assertFalse(result["valid"])
        self.assertTrue(any("observer" in e for e in result["errors"]))

    def test_all_valid_modes_accepted(self):
        for mode in ["audit", "verified", "masters"]:
            data = self._make(["user", "mode"], [[f"user_{mode}", mode]])
            result = validate_csv_file(data)
            self.assertTrue(result["valid"], f"Mode '{mode}' should be valid")

    def test_duplicate_user_adds_warning(self):
        data = self._make(["user", "mode"], [["alice", "audit"], ["alice", "audit"]])
        result = validate_csv_file(data)
        self.assertTrue(any("Duplicate" in w for w in result["warnings"]))

    def test_missing_user_identifier_rejected(self):
        data = self._make(["user", "mode"], [["", "audit"]])
        result = validate_csv_file(data)
        self.assertFalse(result["valid"])
        self.assertTrue(any("Missing required user" in e for e in result["errors"]))

    def test_headers_only_no_rows_rejected(self):
        result = validate_csv_file(b"user,mode\n")
        self.assertFalse(result["valid"])
        self.assertIn("no student records", result["errors"][0])

    def test_identifies_multiple_team_set_columns(self):
        data = self._make(["user", "mode", "labs", "projects"], [["alice", "audit", "Team A", "Team B"]])
        result = validate_csv_file(data)
        self.assertEqual(result["team_sets"], ["labs", "projects"])

    def test_no_team_set_columns_is_valid(self):
        data = self._make(["user", "mode"], [["alice", "audit"]])
        result = validate_csv_file(data)
        self.assertTrue(result["valid"])
        self.assertEqual(result["team_sets"], [])


class ResolveUserIdentityTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="JohnDoe", email="john@example.com", password="pass")

    def test_resolve_by_numeric_id(self):
        self.assertEqual(resolve_user_identity(str(self.user.id)), self.user)

    def test_resolve_by_username_case_insensitive(self):
        self.assertEqual(resolve_user_identity("johndoe"), self.user)
        self.assertEqual(resolve_user_identity("JOHNDOE"), self.user)

    def test_resolve_by_email_case_insensitive(self):
        self.assertEqual(resolve_user_identity("JOHN@EXAMPLE.COM"), self.user)

    def test_unknown_identifier_returns_none(self):
        self.assertIsNone(resolve_user_identity("nobody"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(resolve_user_identity(""))

    def test_numeric_id_not_found_returns_none(self):
        self.assertIsNone(resolve_user_identity("99999"))


# ── Participant C: Import/Export API Tests ────────────────────────────────────

class CSVImportAPITest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="admin", password="pass", email="admin@test.com")
        self.client.force_authenticate(user=self.admin)
        self.learner = User.objects.create_user(username="alice", password="pass", email="alice@test.com")

    def test_requires_authentication(self):
        unauthenticated = APIClient()
        response = unauthenticated.post("/api/api/import-csv/", {"course_id": "c1"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_course_id_returns_400(self):
        f = _make_csv_bytes(["user", "mode"], [["alice", "audit"]])
        response = self.client.post("/api/api/import-csv/", {"file": f}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_file_returns_400(self):
        response = self.client.post("/api/api/import-csv/", {"course_id": "c1"}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_csv_returns_422(self):
        bad_csv = io.BytesIO(b"mode,user\nalice,audit\n")
        response = self.client.post("/api/api/import-csv/", {"course_id": "c1", "file": bad_csv}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_valid_import_creates_teamset_team_and_assignment(self):
        f = _make_csv_bytes(["user", "mode", "discussion-teams"], [["alice", "audit", "Team A"]])
        response = self.client.post("/api/api/import-csv/", {"course_id": "c1", "file": f}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(TeamSet.objects.filter(name="discussion-teams", course_id="c1").exists())
        self.assertTrue(Team.objects.filter(name="Team A").exists())
        self.assertTrue(TeamAssignment.objects.filter(learner=self.learner).exists())

    def test_empty_team_cell_removes_existing_assignment(self):
        ts = TeamSet.objects.create(name="labs", course_id="c1")
        team = Team.objects.create(name="Team A", team_set=ts)
        TeamAssignment.objects.create(learner=self.learner, team=team)
        f = _make_csv_bytes(["user", "mode", "labs"], [["alice", "audit", ""]])
        self.client.post("/api/api/import-csv/", {"course_id": "c1", "file": f}, format="multipart")
        self.assertFalse(TeamAssignment.objects.filter(learner=self.learner, team__team_set=ts).exists())

    def test_new_team_name_auto_creates_team(self):
        f = _make_csv_bytes(["user", "mode", "labs"], [["alice", "audit", "Brand New Team"]])
        self.client.post("/api/api/import-csv/", {"course_id": "c1", "file": f}, format="multipart")
        self.assertTrue(Team.objects.filter(name="Brand New Team").exists())

    def test_unresolved_identity_aborts_import(self):
        f = _make_csv_bytes(["user", "mode", "labs"], [["ghost_user_xyz", "audit", "Team A"]])
        response = self.client.post("/api/api/import-csv/", {"course_id": "c1", "file": f}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(TeamAssignment.objects.exists())

    def test_enrollment_mode_recorded(self):
        f = _make_csv_bytes(["user", "mode"], [["alice", "verified"]])
        self.client.post("/api/api/import-csv/", {"course_id": "c1", "file": f}, format="multipart")
        enrollment = CourseEnrollment.objects.filter(user=self.learner, course_id="c1").first()
        self.assertIsNotNone(enrollment)
        self.assertEqual(enrollment.mode, "verified")

    def test_import_response_summary_fields(self):
        f = _make_csv_bytes(["user", "mode", "labs"], [["alice", "audit", "Team A"]])
        response = self.client.post("/api/api/import-csv/", {"course_id": "c1", "file": f}, format="multipart")
        self.assertIn("summary", response.data)
        self.assertIn("rows_successfully_imported", response.data["summary"])
        self.assertIn("teams_created", response.data["summary"])


class CSVExportAPITest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="admin", password="pass", email="admin@test.com")
        self.client.force_authenticate(user=self.admin)
        self.learner = User.objects.create_user(username="alice", password="pass")

    def test_requires_authentication(self):
        unauthenticated = APIClient()
        response = unauthenticated.get("/api/api/export-csv/?course_id=c1")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_course_id_returns_400(self):
        response = self.client.get("/api/api/export-csv/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_team_sets_returns_warning(self):
        response = self.client.get("/api/api/export-csv/?course_id=unknown")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("warning", response.data)

    def test_export_headers_are_correct(self):
        ts = TeamSet.objects.create(name="labs", course_id="c1")
        team = Team.objects.create(name="Team A", team_set=ts)
        TeamAssignment.objects.create(learner=self.learner, team=team)
        response = self.client.get("/api/api/export-csv/?course_id=c1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reader = csv.reader(io.StringIO(response.content.decode("utf-8")))
        headers = next(reader)
        self.assertEqual(headers[:2], ["user", "mode"])
        self.assertIn("labs", headers)

    def test_export_data_matches_database(self):
        ts = TeamSet.objects.create(name="labs", course_id="c1")
        team = Team.objects.create(name="Team Alpha", team_set=ts)
        TeamAssignment.objects.create(learner=self.learner, team=team)
        CourseEnrollment.objects.create(user=self.learner, course_id="c1", mode="verified")
        response = self.client.get("/api/api/export-csv/?course_id=c1")
        reader = csv.reader(io.StringIO(response.content.decode("utf-8")))
        next(reader)
        row = next(reader)
        self.assertEqual(row[0], "alice")
        self.assertEqual(row[1], "verified")
        self.assertEqual(row[2], "Team Alpha")

    def test_export_empty_mode_when_no_enrollment(self):
        ts = TeamSet.objects.create(name="labs", course_id="c1")
        team = Team.objects.create(name="Team A", team_set=ts)
        TeamAssignment.objects.create(learner=self.learner, team=team)
        response = self.client.get("/api/api/export-csv/?course_id=c1")
        reader = csv.reader(io.StringIO(response.content.decode("utf-8")))
        next(reader)
        row = next(reader)
        self.assertEqual(row[1], "")
