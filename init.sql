DROP VIEW IF EXISTS student_overviews;

DROP TABLE IF EXISTS courses CASCADE;
DROP TABLE IF EXISTS exams CASCADE;
DROP TABLE IF EXISTS students CASCADE;
DROP TABLE IF EXISTS enrollments CASCADE;
DROP TABLE IF EXISTS tutors CASCADE;
DROP TABLE IF EXISTS study_sessions CASCADE;
DROP TABLE IF EXISTS student_auth;
DROP TABLE IF EXISTS refresh_tokens CASCADE;

DROP TYPE IF EXISTS sharing_setting CASCADE;
DROP TYPE IF EXISTS academic_term CASCADE;
DROP TYPE IF EXISTS term_season CASCADE;

CREATE TYPE sharing_setting AS ENUM('closed', 'common_class', 'open');
CREATE TYPE term_season AS ENUM('spring', 'summer', 'fall');
CREATE TYPE academic_term AS (academic_year SMALLINT, season term_season);

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

CREATE TABLE exams (
    exam_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    course_id INTEGER NOT NULL,
    test_date DATE NOT NULL,

    CONSTRAINT fk_exams_course
        FOREIGN KEY(course_id)
        REFERENCES courses(course_id)
);

CREATE TABLE students (
    student_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    discord_id TEXT,
    first_name TEXT,
    last_name TEXT,
    sharing sharing_setting NOT NULL DEFAULT 'open',
    graduated_date DATE  -- Null for undergraduate
);

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
  student_id = current_setting('app.current_user_id', true)::integer
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

CREATE POLICY "tutor_sharing" ON tutors
USING (
  student_id = current_setting('app.current_user_id', true)::integer
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

CREATE TABLE student_auth (
    student_id INTEGER PRIMARY KEY REFERENCES students(student_id) ON DELETE CASCADE,
    hashed_password TEXT NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
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

CREATE INDEX course_department ON courses (department);
CREATE INDEX exam_dates ON exams (test_date);