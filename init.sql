\set ON_ERROR_STOP on

DROP VIEW IF EXISTS student_overviews;
DROP VIEW IF EXISTS current_term;

DROP TABLE IF EXISTS course_links CASCADE;
DROP TABLE IF EXISTS courses CASCADE;
DROP TABLE IF EXISTS exams CASCADE;
DROP TABLE IF EXISTS students CASCADE;
DROP TABLE IF EXISTS enrollments CASCADE;
DROP TABLE IF EXISTS tutors CASCADE;
DROP TABLE IF EXISTS study_sessions CASCADE;
DROP TABLE IF EXISTS student_auth;
DROP TABLE IF EXISTS refresh_tokens CASCADE;
DROP TABLE IF EXISTS confidence_decay_log CASCADE;

DROP FUNCTION IF EXISTS linked_course_ids(INTEGER);
DROP FUNCTION IF EXISTS linked_course_ids_any(INTEGER);
DROP FUNCTION IF EXISTS is_admin();
DROP FUNCTION IF EXISTS current_user_id();
DROP FUNCTION IF EXISTS my_course_ids_for_term(SMALLINT, term_season);
DROP FUNCTION IF EXISTS my_all_course_ids();

DROP TYPE IF EXISTS sharing_setting CASCADE;
DROP TYPE IF EXISTS academic_term CASCADE;
DROP TYPE IF EXISTS term_season CASCADE;
DROP TYPE IF EXISTS exam_type CASCADE;
DROP TYPE IF EXISTS link_type CASCADE;

CREATE TYPE sharing_setting AS ENUM('closed', 'common_class', 'open');
CREATE TYPE term_season AS ENUM('spring', 'fall');
CREATE TYPE academic_term AS (academic_year SMALLINT, season term_season);
CREATE TYPE exam_type AS ENUM('in_class', 'quiz', 'common_hour', 'final');
CREATE TYPE link_type AS ENUM('strong', 'weak');

-- RLS helper: returns current user's student_id, or NULL if not set
CREATE FUNCTION current_user_id() RETURNS integer AS $$
  SELECT NULLIF(current_setting('app.current_user_id', true), '')::integer;
$$ LANGUAGE sql STABLE;

-- RLS helper: returns true if the current session is marked as admin
CREATE FUNCTION is_admin() RETURNS boolean AS $$
  SELECT current_setting('app.is_admin', true) = 'true';
$$ LANGUAGE sql STABLE;

CREATE TABLE courses (
    course_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    department TEXT NOT NULL,
    identifier TEXT NOT NULL,
    title TEXT,
    semester_hours TEXT,
    last_offered academic_term NOT NULL,
    no_tutor_needed BOOLEAN NOT NULL DEFAULT FALSE,
    no_tutor_pending BOOLEAN NOT NULL DEFAULT FALSE,

    CONSTRAINT last_offered_year_not_null CHECK ((last_offered).academic_year IS NOT NULL),
    CONSTRAINT last_offered_season_not_null CHECK ((last_offered).season IS NOT NULL)
);

ALTER TABLE courses ENABLE ROW LEVEL SECURITY;
CREATE POLICY courses_select ON courses FOR SELECT USING (true);
CREATE POLICY courses_insert ON courses FOR INSERT WITH CHECK (is_admin());
CREATE POLICY courses_update ON courses FOR UPDATE USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY courses_delete ON courses FOR DELETE USING (is_admin());

CREATE TABLE course_links (
    course_id_a INTEGER NOT NULL,
    course_id_b INTEGER NOT NULL,
    link_type link_type NOT NULL DEFAULT 'strong',
    PRIMARY KEY (course_id_a, course_id_b),
    CHECK (course_id_a < course_id_b),
    CONSTRAINT fk_link_a FOREIGN KEY (course_id_a) REFERENCES courses(course_id) ON DELETE CASCADE,
    CONSTRAINT fk_link_b FOREIGN KEY (course_id_b) REFERENCES courses(course_id) ON DELETE CASCADE
);

ALTER TABLE course_links ENABLE ROW LEVEL SECURITY;
CREATE POLICY course_links_select ON course_links FOR SELECT USING (true);
CREATE POLICY course_links_admin ON course_links USING (is_admin()) WITH CHECK (is_admin());

-- Resolves strong-linked course group (same tests, combined sessions).
CREATE OR REPLACE FUNCTION linked_course_ids(p_course_id INTEGER)
RETURNS INTEGER[] AS $$
    WITH RECURSIVE link_group AS (
        SELECT p_course_id AS course_id
        UNION
        SELECT CASE WHEN cl.course_id_a = lg.course_id THEN cl.course_id_b
                    ELSE cl.course_id_a END
        FROM course_links cl
        JOIN link_group lg ON cl.course_id_a = lg.course_id OR cl.course_id_b = lg.course_id
        WHERE cl.link_type = 'strong'
    )
    SELECT ARRAY(SELECT DISTINCT course_id FROM link_group);
$$ LANGUAGE sql STABLE;

-- Resolves all linked courses (strong + weak) for tutor resolution.
CREATE OR REPLACE FUNCTION linked_course_ids_any(p_course_id INTEGER)
RETURNS INTEGER[] AS $$
    WITH RECURSIVE link_group AS (
        SELECT p_course_id AS course_id
        UNION
        SELECT CASE WHEN cl.course_id_a = lg.course_id THEN cl.course_id_b
                    ELSE cl.course_id_a END
        FROM course_links cl
        JOIN link_group lg ON cl.course_id_a = lg.course_id OR cl.course_id_b = lg.course_id
    )
    SELECT ARRAY(SELECT DISTINCT course_id FROM link_group);
$$ LANGUAGE sql STABLE;

-- students must be created before exams (exams.creator_id references students)
CREATE TABLE students (
    student_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    discord_id TEXT,
    first_name TEXT,
    last_name TEXT,
    sharing sharing_setting NOT NULL DEFAULT 'open',
    graduated_date DATE,  -- Null for undergraduate
    avatar_checked_at TIMESTAMPTZ,
    dark_mode BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE students ENABLE ROW LEVEL SECURITY;
CREATE POLICY students_select ON students FOR SELECT USING (true);
CREATE POLICY students_insert ON students FOR INSERT WITH CHECK (is_admin());
-- Non-admins can update their own row only (e.g. sharing_setting, avatar_checked_at)
CREATE POLICY students_update ON students FOR UPDATE
    USING (is_admin() OR student_id = current_user_id())
    WITH CHECK (is_admin() OR student_id = current_user_id());
CREATE POLICY students_delete ON students FOR DELETE USING (is_admin());

CREATE VIEW student_overviews AS SELECT student_id, first_name, last_name, graduated_date FROM students;

CREATE TABLE exams (
    exam_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    course_id INTEGER NOT NULL,
    test_date DATE NOT NULL,
    exam_type exam_type NOT NULL DEFAULT 'in_class',
    creator_id INTEGER REFERENCES students(student_id) ON DELETE SET NULL,
    confirmed BOOLEAN NOT NULL DEFAULT TRUE,
    disputed BOOLEAN NOT NULL DEFAULT FALSE,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    skipped BOOLEAN NOT NULL DEFAULT FALSE,

    CONSTRAINT fk_exams_course
        FOREIGN KEY(course_id)
        REFERENCES courses(course_id)
);
CREATE UNIQUE INDEX unique_exam ON exams (course_id, test_date, exam_type) WHERE NOT deleted;

ALTER TABLE exams ENABLE ROW LEVEL SECURITY;
CREATE POLICY exams_select ON exams FOR SELECT USING (true);
CREATE POLICY exams_insert ON exams FOR INSERT WITH CHECK (
    is_admin() OR creator_id = current_user_id()
);
CREATE POLICY exams_update ON exams FOR UPDATE USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY exams_delete ON exams FOR DELETE USING (
    is_admin() OR (creator_id = current_user_id() AND NOT confirmed)
);

CREATE TABLE enrollments (
    student_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    term academic_term,

    CONSTRAINT term_year_not_null
        CHECK ((term).academic_year IS NOT NULL),
    CONSTRAINT term_season_not_null
        CHECK ((term).season IS NOT NULL),

    PRIMARY KEY (student_id, course_id, term),

    CONSTRAINT fk_enrollments_course
        FOREIGN KEY(course_id)
        REFERENCES courses(course_id),

    CONSTRAINT fk_student
        FOREIGN KEY(student_id)
        REFERENCES students(student_id)
);

ALTER TABLE enrollments ENABLE ROW LEVEL SECURITY;

-- RLS helper: returns the current user's enrolled course IDs for a specific term.
-- SECURITY DEFINER bypasses RLS on enrollments to avoid infinite recursion in
-- the enrollment_sharing policy.
CREATE FUNCTION my_course_ids_for_term(p_year SMALLINT, p_season term_season)
RETURNS integer[] AS $$
  SELECT ARRAY(
    SELECT course_id FROM enrollments
    WHERE student_id = current_user_id()
      AND (term).academic_year = p_year
      AND (term).season = p_season
  )
$$ LANGUAGE sql STABLE SECURITY DEFINER;

-- RLS helper: returns all course IDs the current user has ever been enrolled in.
-- SECURITY DEFINER bypasses RLS on enrollments for the same reason.
CREATE FUNCTION my_all_course_ids()
RETURNS integer[] AS $$
  SELECT ARRAY(
    SELECT DISTINCT course_id FROM enrollments
    WHERE student_id = current_user_id()
  )
$$ LANGUAGE sql STABLE SECURITY DEFINER;

CREATE POLICY "enrollment_sharing" ON enrollments
USING (
  student_id = current_user_id()
  OR
  (
    -- common_class: only show rows for courses the viewer is also in that same term
    course_id = ANY(my_course_ids_for_term((term).academic_year, (term).season))
    AND
    EXISTS (SELECT 1 FROM students WHERE student_id = enrollments.student_id AND sharing = 'common_class')
  )
  OR (
    EXISTS (SELECT 1 FROM students WHERE student_id = enrollments.student_id AND sharing = 'open')
  )
);

CREATE POLICY enrollments_admin ON enrollments USING (is_admin()) WITH CHECK (is_admin());

CREATE TABLE tutors (
    student_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    confidence SMALLINT,
    sharing sharing_setting NOT NULL DEFAULT 'open',

    CONSTRAINT confidence_range CHECK (confidence >= 0 AND confidence <= 10),

    PRIMARY KEY (student_id, course_id),

    CONSTRAINT fk_course
        FOREIGN KEY(course_id)
        REFERENCES courses(course_id),

    CONSTRAINT fk_student
        FOREIGN KEY(student_id)
        REFERENCES students(student_id)
);

ALTER TABLE tutors ENABLE ROW LEVEL SECURITY;

-- Visibility: sharing-based, SELECT only
CREATE POLICY "tutor_sharing" ON tutors FOR SELECT
USING (
  student_id = current_user_id()
  OR
  (
    -- common_class: show tutor capability if viewer has ever been enrolled in that course
    course_id = ANY(my_all_course_ids())
    AND
    EXISTS (SELECT 1 FROM students WHERE student_id = tutors.student_id AND sharing = 'common_class')
  )
  OR (
    EXISTS (SELECT 1 FROM students WHERE student_id = tutors.student_id AND sharing = 'open')
  )
);

-- Admins can do everything
CREATE POLICY tutors_admin ON tutors USING (is_admin()) WITH CHECK (is_admin());

-- Non-admins can only write their own rows
CREATE POLICY tutors_own_insert ON tutors FOR INSERT WITH CHECK (student_id = current_user_id());
CREATE POLICY tutors_own_update ON tutors FOR UPDATE
    USING (student_id = current_user_id())
    WITH CHECK (student_id = current_user_id());
CREATE POLICY tutors_own_delete ON tutors FOR DELETE USING (student_id = current_user_id());

CREATE TABLE study_sessions (
    session_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    tutor_student_id INTEGER,
    exam_id INTEGER NOT NULL,
    session_timestamp TIMESTAMPTZ NOT NULL,
    location TEXT NOT NULL DEFAULT 'Study Room',

    CONSTRAINT fk_session_tutor FOREIGN KEY (tutor_student_id)
        REFERENCES students (student_id),

    CONSTRAINT fk_session_exam FOREIGN KEY (exam_id)
        REFERENCES exams (exam_id)
);

ALTER TABLE study_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY study_sessions_select ON study_sessions FOR SELECT USING (true);
CREATE POLICY study_sessions_insert ON study_sessions FOR INSERT WITH CHECK (is_admin());
CREATE POLICY study_sessions_update ON study_sessions FOR UPDATE USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY study_sessions_delete ON study_sessions FOR DELETE USING (is_admin());

CREATE TABLE student_auth (
    student_id INTEGER PRIMARY KEY REFERENCES students(student_id) ON DELETE CASCADE,
    hashed_password TEXT NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_root BOOLEAN NOT NULL DEFAULT FALSE,
    last_login TIMESTAMPTZ,
    last_seen_term academic_term
);

CREATE TABLE refresh_tokens (
    token_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id INTEGER NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_refresh_tokens_expires ON refresh_tokens(expires_at);

-- Computed view: infers current term from exam dates when data exists,
-- falling back to calendar date (Jan-Jun → spring, Jul-Dec → fall) when the DB is empty.
-- Picks the term whose exams are closest (by days) to today.
CREATE VIEW current_term AS
WITH exam_terms AS (
    SELECT
        EXTRACT(YEAR FROM test_date)::smallint AS academic_year,
        CASE WHEN EXTRACT(MONTH FROM test_date) <= 6 THEN 'spring'::term_season
             ELSE 'fall'::term_season
        END AS season,
        ABS(test_date - CURRENT_DATE) AS days_from_today
    FROM exams
    WHERE NOT deleted
),
term_distances AS (
    SELECT academic_year, season, MIN(days_from_today) AS min_distance
    FROM exam_terms
    GROUP BY academic_year, season
    ORDER BY min_distance ASC
    LIMIT 1
)
SELECT
    COALESCE(
        (SELECT academic_year FROM term_distances),
        EXTRACT(YEAR FROM NOW())::smallint
    ) AS academic_year,
    COALESCE(
        (SELECT season FROM term_distances),
        CASE WHEN EXTRACT(MONTH FROM NOW()) <= 6 THEN 'spring'::term_season
             ELSE 'fall'::term_season
        END
    ) AS season;

CREATE TABLE confidence_decay_log (
    academic_year SMALLINT NOT NULL,
    season term_season NOT NULL,
    PRIMARY KEY (academic_year, season)
);

CREATE INDEX course_department ON courses (department);
CREATE INDEX exam_dates ON exams (test_date);
