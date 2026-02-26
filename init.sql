DROP VIEW IF EXISTS student_overviews;

DROP TABLE IF EXISTS courses CASCADE;
DROP TABLE IF EXISTS exams CASCADE;
DROP TABLE IF EXISTS students CASCADE;
DROP TABLE IF EXISTS enrollments CASCADE;
DROP TABLE IF EXISTS tutors CASCADE;
DROP TABLE IF EXISTS study_sessions CASCADE;
DROP TABLE IF EXISTS student_auth;
DROP TABLE IF EXISTS refresh_tokens CASCADE;
DROP TABLE IF EXISTS current_term;

DROP FUNCTION IF EXISTS is_admin();
DROP FUNCTION IF EXISTS current_user_id();

DROP TYPE IF EXISTS sharing_setting CASCADE;
DROP TYPE IF EXISTS academic_term CASCADE;
DROP TYPE IF EXISTS term_season CASCADE;

CREATE TYPE sharing_setting AS ENUM('closed', 'common_class', 'open');
CREATE TYPE term_season AS ENUM('spring', 'summer', 'fall');
CREATE TYPE academic_term AS (academic_year SMALLINT, season term_season);

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

    CONSTRAINT last_offered_year_not_null CHECK ((last_offered).academic_year IS NOT NULL),
    CONSTRAINT last_offered_season_not_null CHECK ((last_offered).season IS NOT NULL)
);

ALTER TABLE courses ENABLE ROW LEVEL SECURITY;
CREATE POLICY courses_select ON courses FOR SELECT USING (true);
CREATE POLICY courses_insert ON courses FOR INSERT WITH CHECK (is_admin());
CREATE POLICY courses_update ON courses FOR UPDATE USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY courses_delete ON courses FOR DELETE USING (is_admin());

CREATE TABLE exams (
    exam_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    course_id INTEGER NOT NULL,
    test_date DATE NOT NULL,
    creator_id INTEGER REFERENCES students(student_id) ON DELETE SET NULL,

    CONSTRAINT fk_exams_course
        FOREIGN KEY(course_id)
        REFERENCES courses(course_id)
);

ALTER TABLE exams ENABLE ROW LEVEL SECURITY;
CREATE POLICY exams_select ON exams FOR SELECT USING (true);
CREATE POLICY exams_insert ON exams FOR INSERT WITH CHECK (
    is_admin() OR creator_id = current_user_id()
);
CREATE POLICY exams_update ON exams FOR UPDATE USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY exams_delete ON exams FOR DELETE USING (
    is_admin() OR creator_id = current_user_id()
);

CREATE TABLE students (
    student_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    discord_id TEXT,
    first_name TEXT,
    last_name TEXT,
    sharing sharing_setting NOT NULL DEFAULT 'open',
    graduated_date DATE,  -- Null for undergraduate
    avatar_checked_at TIMESTAMPTZ
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

CREATE POLICY "enrollment_sharing" ON enrollments
USING (
  student_id = current_user_id()
  OR
  (
    course_id = ANY(current_setting('app.my_course_ids', true)::integer[])
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

    PRIMARY KEY (student_id, course_id),

    CONSTRAINT fk_course
        FOREIGN KEY(course_id)
        REFERENCES courses(course_id),

    CONSTRAINT fk_student
        FOREIGN KEY(student_id)
        REFERENCES students(student_id)
);

ALTER TABLE tutors ENABLE ROW LEVEL SECURITY;

-- Visibility: sharing-based (same logic as before), SELECT only
CREATE POLICY "tutor_sharing" ON tutors FOR SELECT
USING (
  student_id = current_user_id()
  OR
  (
    course_id = ANY(current_setting('app.my_course_ids', true)::integer[])
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
    tutor_student_id INTEGER NOT NULL,
    exam_id INTEGER NOT NULL,
    session_timestamp TIMESTAMPTZ NOT NULL,

    CONSTRAINT fk_session_tutor FOREIGN KEY (tutor_student_id)
        REFERENCES students (student_id),

    CONSTRAINT fk_session_exam FOREIGN KEY (exam_id)
        REFERENCES exams (exam_id),

    CONSTRAINT unique_tutor_exam UNIQUE (tutor_student_id, exam_id)
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
    last_login TIMESTAMPTZ
);

CREATE TABLE refresh_tokens (
    token_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id INTEGER NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_refresh_tokens_expires ON refresh_tokens(expires_at);

-- Singleton table: only one row allowed (id must be TRUE)
CREATE TABLE current_term (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE,
    term academic_term NOT NULL,
    CONSTRAINT single_row CHECK (id)
);

ALTER TABLE current_term ENABLE ROW LEVEL SECURITY;
CREATE POLICY current_term_select ON current_term FOR SELECT USING (true);
CREATE POLICY current_term_insert ON current_term FOR INSERT WITH CHECK (is_admin());
CREATE POLICY current_term_update ON current_term FOR UPDATE USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY current_term_delete ON current_term FOR DELETE USING (is_admin());

CREATE INDEX course_department ON courses (department);
CREATE INDEX exam_dates ON exams (test_date);
