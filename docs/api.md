# API Reference

## Unauthenticated

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Login page (redirects to portal if logged in) |
| `POST` | `/login` | Form: `first_name`, `last_name`, `password` |
| `POST` | `/logout` | Clears cookies, revokes refresh token |
| `GET` | `/api/users/all` | All users for login autocomplete |
| `GET` | `/api/users/search?q=` | Name search (min 2 chars, max 8 results) |
| `GET` | `/api/courses` | Current-term course list |
| `GET` | `/api/discord_avatar/{discord_id}` | Discord avatar URL lookup |
| `GET` | `/avatar/{student_id}` | Serve cached avatar PNG (24h cache) |
| `POST` | `/api/avatar/fetch/{student_id}` | Fetch + cache Discord avatar |

## Authenticated (any user)

### Pages

| Method | Path | Description |
|---|---|---|
| `GET` | `/user/portal` | User portal page |
| `GET` | `/user/set_password` | First-login password setup |
| `POST` | `/user/set_password` | Form: `new_password`, optional `sharing` |
| `GET` | `/user/change_password` | Change password page |
| `POST` | `/user/change_password` | Form: `old_password`, `new_password`, `confirm_password` |
| `GET` | `/user/privacy` | Privacy settings page |
| `POST` | `/user/privacy` | Form: `sharing` (`open`, `common_class`, `closed`) |

### Enrollments

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/my/enrollments` | Current-term enrollments |
| `POST` | `/api/my/enrollments` | Form: `course_id` |
| `DELETE` | `/api/my/enrollments/{course_id}` | Remove enrollment |

### Tutor Capabilities

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/my/tutor_capabilities` | Tutor capabilities (confidence > 0) |
| `POST` | `/api/my/tutor_capabilities` | Form: `course_id`, `confidence` (1-10) |
| `DELETE` | `/api/my/tutor_capabilities/{course_id}` | Remove capability |
| `GET` | `/api/my/tutor_recommendations` | Past courses not yet in tutors table (excludes linked courses) |
| `POST` | `/api/my/tutor_dismiss/{course_id}` | Dismiss recommendation (confidence=0) |

### Assessments

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/my/assessments` | Upcoming assessments for enrolled courses |
| `POST` | `/api/my/assessments` | Form: `course_id`, `test_date`, `exam_type` (`in_class` or `quiz`) |
| `DELETE` | `/api/my/assessments/{exam_id}` | Delete own unconfirmed assessment |
| `POST` | `/api/my/assessments/{exam_id}/dispute` | Dispute a final exam |

### No-Tutor Reports

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/my/report_no_tutor/{course_id}` | Report non-academic course that doesn't need a tutor (pending admin approval) |

### Dark Mode

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/my/dark_mode` | Toggle dark mode preference (returns `{dark_mode: bool}`) |

### Course Tutors & Study Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/my/course_tutors` | Tutors for enrolled courses, grouped by course (respects RLS sharing) |
| `GET` | `/api/my/study_sessions` | Upcoming study sessions (enrolled courses + sessions where user is tutor) |

### Classmates

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/my/classmates` | Students sharing classes (respects RLS sharing) |
| `GET` | `/api/my/search_students?q=` | Search all students by name (respects RLS for course visibility) |

## Admin

### Portal & User Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/portal` | Admin portal page |
| `POST` | `/admin/set_admin` | Form: `target_id`, `make_admin` |
| `POST` | `/admin/api/edit_user` | Form: `target_id`, `first_name`, `last_name`, `discord_id` |
| `POST` | `/admin/delete_user` | Form: `target_id` |
| `POST` | `/admin/set_graduated` | Form: `target_id`, `graduated` |
| `GET` | `/admin/api/validate_discord/{id}` | Validate Discord ID via bot API |
| `POST` | `/admin/create_user` | Form: `first_name`, `last_name`, `discord_id` |
| `POST` | `/admin/api/dismiss_checklist` | Dismiss new-semester checklist for current term |

### Admin User Enrollments & Tutors

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/api/user/{id}/enrollments` | User's current-term enrollments |
| `POST` | `/admin/api/user/{id}/enrollments` | Form: `course_id` -- add enrollment |
| `DELETE` | `/admin/api/user/{id}/enrollments/{course_id}` | Remove enrollment (current term) |
| `GET` | `/admin/api/user/{id}/tutor_capabilities` | User's tutor capabilities |
| `POST` | `/admin/api/user/{id}/tutor_capabilities` | Form: `course_id`, `confidence` -- add/update |
| `DELETE` | `/admin/api/user/{id}/tutor_capabilities/{course_id}` | Remove capability |

### Assessment Review

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/api/pending_assessments` | Unconfirmed/disputed exams + needs-session todos |
| `POST` | `/admin/api/confirm_assessment` | Form: `exam_id` -- confirm pending or delete disputed |
| `POST` | `/admin/api/revert_assessment` | Form: `exam_id` -- reject pending or restore disputed |
| `DELETE` | `/admin/api/pending_assessment/{exam_id}` | Delete unconfirmed assessment |

### Exam Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/api/calendar_exams` | All exams with enrolled students |
| `DELETE` | `/admin/api/exam/{exam_id}` | Soft-delete any exam |
| `GET` | `/admin/api/deleted_exams` | Current-term deleted and skipped exams |
| `POST` | `/admin/api/restore_exam` | Form: `exam_id` -- restore deleted exam |
| `POST` | `/admin/api/skip_exam` | Form: `exam_id` -- skip exam (no session needed) |
| `POST` | `/admin/api/unskip_exam` | Form: `exam_id` -- unskip exam |

### Study Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/api/exam/{exam_id}/scheduling_details` | Tutors, students, linked courses for scheduling |
| `POST` | `/admin/api/study_sessions` | Form: `exam_id`, `tutor_student_id` (optional), `session_timestamp`, `location` |
| `GET` | `/admin/api/study_sessions` | Future sessions for the current term (past sessions filtered out); includes `tutor_discord_id` |
| `PUT` | `/admin/api/study_sessions/{session_id}` | Form: `tutor_student_id` (optional), `session_timestamp`, `location` -- update session |
| `DELETE` | `/admin/api/study_sessions/{session_id}` | Delete session |

### Course Links

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/api/course_links` | All link pairs with link_type |
| `POST` | `/admin/api/course_links` | Form: `course_id_a`, `course_id_b`, `link_type` (`strong` or `weak`) |
| `DELETE` | `/admin/api/course_links/{course_id_a}/{course_id_b}` | Remove link |
| `GET` | `/admin/api/course_link_suggestions` | Title-similarity-based link suggestions |

### No-Tutor-Needed Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/api/no_tutor_pending` | Courses pending no-tutor approval (shown in Todos) |
| `POST` | `/admin/api/approve_no_tutor` | Form: `course_id` -- approve (sets `no_tutor_needed`) |
| `POST` | `/admin/api/reject_no_tutor` | Form: `course_id` -- reject (clears `no_tutor_pending`) |
| `POST` | `/admin/api/toggle_no_tutor` | Form: `course_id` -- toggle `no_tutor_needed` directly |
| `GET` | `/admin/api/no_tutor_approved` | Current-term courses with `no_tutor_needed` approved (shown in Restore & Review) |

### Import

| Method | Path | Description |
|---|---|---|
| `POST` | `/admin/api/preview_exam_pdf` | Upload PDF + `exam_type`, returns preview JSON |
| `POST` | `/admin/import_exams` | Form: `entries_json`, `exam_type`, `pdf_b64` |
| `POST` | `/admin/api/preview_courses` | Form: `academic_year`, `season` -- fetches from web |
| `POST` | `/admin/import_courses` | Form: `academic_year`, `season` |
| `POST` | `/admin/refresh_course_cache` | Wipe current term's course cache |
| `POST` | `/admin/api/import_courses_from_cache` | Import courses from final disk cache |
| `POST` | `/admin/api/import_exams_from_disk` | Form: `exam_type` -- import from existing PDF |

## Root only

| Method | Path | Description |
|---|---|---|
| `POST` | `/admin/backup` | Create timestamped backup |
| `GET` | `/admin/api/backups` | List backups |
| `POST` | `/admin/api/restore_backup` | Form: `backup_name` |
| `DELETE` | `/admin/api/backup/{name}` | Delete backup |
| `POST` | `/admin/wipe_selective` | Form: `what[]`, `term[]` |
