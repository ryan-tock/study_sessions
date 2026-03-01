# Database Schema

Schema is defined in `init.sql`. Reset with `reset.sh`.

## Custom Types

| Type | Values |
|---|---|
| `sharing_setting` | `closed`, `common_class`, `open` |
| `term_season` | `spring`, `fall` |
| `academic_term` | Composite: `(academic_year SMALLINT, season term_season)` |
| `exam_type` | `in_class`, `quiz`, `common_hour`, `final` |
| `link_type` | `strong`, `weak` |

## Tables

### students
| Column | Type | Notes |
|---|---|---|
| `student_id` | `INTEGER` | PK, identity |
| `first_name` | `TEXT` | |
| `last_name` | `TEXT` | |
| `discord_id` | `TEXT` | Optional |
| `sharing` | `sharing_setting` | Default `open` |
| `graduated_date` | `DATE` | NULL = undergraduate |
| `avatar_checked_at` | `TIMESTAMPTZ` | Last avatar refresh |

### student_auth
| Column | Type | Notes |
|---|---|---|
| `student_id` | `INTEGER` | PK, FK students (CASCADE) |
| `hashed_password` | `TEXT` | bcrypt |
| `is_admin` | `BOOLEAN` | Default FALSE |
| `is_root` | `BOOLEAN` | Default FALSE |
| `last_login` | `TIMESTAMPTZ` | NULL until first login |

### courses
| Column | Type | Notes |
|---|---|---|
| `course_id` | `INTEGER` | PK, identity |
| `department` | `TEXT` | e.g. CSCI |
| `identifier` | `TEXT` | e.g. 261 |
| `title` | `TEXT` | |
| `semester_hours` | `TEXT` | |
| `last_offered` | `academic_term` | Most recent term offered |

### course_links
| Column | Type | Notes |
|---|---|---|
| `course_id_a` | `INTEGER` | FK courses (CASCADE) |
| `course_id_b` | `INTEGER` | FK courses (CASCADE) |
| `link_type` | `link_type` | Default `strong` |
| | | PK on `(course_id_a, course_id_b)`, CHECK `a < b` |

### exams
| Column | Type | Notes |
|---|---|---|
| `exam_id` | `INTEGER` | PK, identity |
| `course_id` | `INTEGER` | FK courses |
| `test_date` | `DATE` | |
| `exam_type` | `exam_type` | Default `in_class` |
| `creator_id` | `INTEGER` | FK students (SET NULL) |
| `confirmed` | `BOOLEAN` | Default TRUE |
| `disputed` | `BOOLEAN` | Default FALSE |
| `deleted` | `BOOLEAN` | Default FALSE |
| | | UNIQUE on `(course_id, test_date, exam_type)` WHERE NOT deleted |

### enrollments
| Column | Type | Notes |
|---|---|---|
| `student_id` | `INTEGER` | FK students |
| `course_id` | `INTEGER` | FK courses |
| `term` | `academic_term` | |
| | | PK on `(student_id, course_id, term)` |

### tutors
| Column | Type | Notes |
|---|---|---|
| `student_id` | `INTEGER` | FK students |
| `course_id` | `INTEGER` | FK courses |
| `confidence` | `SMALLINT` | 1-10 (0 = dismissed) |
| `sharing` | `sharing_setting` | Default `open` |
| | | PK on `(student_id, course_id)` |

### study_sessions
| Column | Type | Notes |
|---|---|---|
| `session_id` | `INTEGER` | PK, identity |
| `tutor_student_id` | `INTEGER` | FK students, nullable (open sessions) |
| `exam_id` | `INTEGER` | FK exams |
| `session_timestamp` | `TIMESTAMPTZ` | |
| `location` | `TEXT` | Default `'Study Room'` |

### refresh_tokens
| Column | Type | Notes |
|---|---|---|
| `token_id` | `UUID` | PK, gen_random_uuid() |
| `student_id` | `INTEGER` | FK students (CASCADE) |
| `token_hash` | `TEXT` | bcrypt hash |
| `expires_at` | `TIMESTAMPTZ` | |
| `created_at` | `TIMESTAMPTZ` | Default NOW() |

## Functions

| Function | Returns | Purpose |
|---|---|---|
| `current_user_id()` | `INTEGER` | RLS helper: reads `app.current_user_id` session var |
| `is_admin()` | `BOOLEAN` | RLS helper: reads `app.is_admin` session var |
| `my_course_ids_for_term(year, season)` | `INTEGER[]` | SECURITY DEFINER: current user's course IDs for a term |
| `my_all_course_ids()` | `INTEGER[]` | SECURITY DEFINER: all course IDs user ever enrolled in |
| `linked_course_ids(course_id)` | `INTEGER[]` | Strong links only: recursive CTE for session/student merging |
| `linked_course_ids_any(course_id)` | `INTEGER[]` | All links (strong + weak): recursive CTE for tutor resolution |

## Views

- **`current_term`** -- Infers active term from exam proximity. Falls back to calendar (Jan-Jun = spring, Jul-Dec = fall). Always returns one row via COALESCE.
- **`student_overviews`** -- `student_id`, `first_name`, `last_name`, `graduated_date`

## RLS Summary

| Table | SELECT | INSERT | UPDATE | DELETE |
|---|---|---|---|---|
| courses | anyone | admin | admin | admin |
| course_links | anyone | admin | admin | admin |
| students | anyone | admin | admin or own row | admin |
| exams | anyone | admin or creator | admin | admin or creator (unconfirmed) |
| enrollments | sharing-based* | admin | admin | admin |
| tutors | sharing-based** | admin or own | admin or own | admin or own |
| study_sessions | anyone | admin | admin | admin |

\* Enrollment visibility depends on the enrolled student's `sharing` setting: `open` = everyone, `common_class` = same course same term, `closed` = self + admins only.

\** Tutor visibility uses the same sharing logic but checks all-time enrollment history (not term-specific).
