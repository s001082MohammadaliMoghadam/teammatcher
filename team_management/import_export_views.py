from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.parsers import MultiPartParser
from django.db import transaction
from django.http import HttpResponse
import csv

from django.contrib.auth import get_user_model
from services.csv_service import validate_csv_file, resolve_user_identity
from team_management.models import TeamSet, Team, TeamAssignment, CourseEnrollment

User = get_user_model()


class CSVImportAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]
    MAX_FILE_SIZE_BYTES = 4 * 1024 * 1024

    def post(self, request, *args, **kwargs):
        course_id = request.data.get("course_id")
        file_obj = request.data.get("file")

        if not course_id:
            return Response({"error": "Missing required parameter: course_id"}, status=status.HTTP_400_BAD_REQUEST)
        if not file_obj:
            return Response({"error": "Missing required parameter: file"}, status=status.HTTP_400_BAD_REQUEST)
        if file_obj.size > self.MAX_FILE_SIZE_BYTES:
            return Response(
                {"error": f"File size exceeds 4MB ({file_obj.size / (1024*1024):.2f}MB)"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            file_data = file_obj.read()
            validation_result = validate_csv_file(file_data)
        except Exception as e:
            return Response({"error": f"Failed to parse file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        if not validation_result["valid"]:
            return Response(
                {"error": "CSV validation failed", "details": validation_result["errors"]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        imported_rows_count = 0
        created_teams_count = 0
        unresolved_identity_errors = []
        execution_warnings = list(validation_result["warnings"])

        try:
            with transaction.atomic():
                team_set_names = validation_result["team_sets"]
                team_set_map = {}
                for name in team_set_names:
                    team_set_obj, _ = TeamSet.objects.get_or_create(
                        name=name,
                        course_id=course_id   # associate with course
                    )
                    team_set_map[name] = team_set_obj

                for row_idx, row_data in enumerate(validation_result["rows"], start=2):
                    user_raw_string = row_data["user"]
                    mode = row_data.get("mode", "").strip()

                    learner = resolve_user_identity(user_raw_string)
                    if not learner:
                        unresolved_identity_errors.append(
                            f"Row {row_idx}: No account found for '{user_raw_string}'"
                        )
                        continue

                    if mode and mode in ['audit', 'verified', 'masters']:
                        CourseEnrollment.objects.update_or_create(
                            user=learner,
                            course_id=course_id,
                            defaults={'mode': mode}
                        )

                    for ts_name in team_set_names:
                        team_set_obj = team_set_map[ts_name]
                        team_cell_value = row_data.get(ts_name, "")

                        if not team_cell_value:
                            TeamAssignment.objects.filter(
                                learner=learner,
                                team__team_set=team_set_obj
                            ).delete()
                        else:
                            team_obj, team_created = Team.objects.get_or_create(
                                name=team_cell_value,
                                team_set=team_set_obj
                            )
                            if team_created:
                                created_teams_count += 1

                            TeamAssignment.objects.filter(
                                learner=learner,
                                team__team_set=team_set_obj
                            ).exclude(team=team_obj).delete()

                            if team_obj.is_full:
                                raise ValueError(f"Row {row_idx}: Team '{team_obj.name}' is full.")

                            TeamAssignment.objects.get_or_create(learner=learner, team=team_obj)

                    imported_rows_count += 1

                if unresolved_identity_errors:
                    raise ValueError("Import halted due to unresolved identities.")

        except ValueError as business_err:
            return Response(
                {"error": "Transaction aborted", "details": str(business_err), "unresolved_identifiers": unresolved_identity_errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as system_err:
            return Response(
                {"error": "Internal server error", "details": str(system_err)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            "status": "success",
            "summary": {
                "rows_successfully_imported": imported_rows_count,
                "teams_created": created_teams_count,
                "team_sets_processed": len(team_set_names)
            },
            "warnings": execution_warnings
        }, status=status.HTTP_201_CREATED)


class CSVExportAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        course_id = request.query_params.get("course_id")
        if not course_id:
            return Response({"error": "Missing course_id"}, status=status.HTTP_400_BAD_REQUEST)

        team_sets = TeamSet.objects.filter(course_id=course_id)
        if not team_sets.exists():
            return Response({"warning": f"No team sets for course {course_id}"}, status=status.HTTP_200_OK)

        learners = User.objects.filter(team_assignments__team__team_set__in=team_sets).distinct()

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="teams_{course_id}.csv"'
        writer = csv.writer(response)

        headers = ['user', 'mode'] + [ts.name for ts in team_sets]
        writer.writerow(headers)

        for learner in learners:
            row = [learner.username]
            enrollment = CourseEnrollment.objects.filter(user=learner, course_id=course_id).first()
            row.append(enrollment.mode if enrollment else '')
            for ts in team_sets:
                assignment = TeamAssignment.objects.filter(learner=learner, team__team_set=ts).first()
                row.append(assignment.team.name if assignment else '')
            writer.writerow(row)

        return response