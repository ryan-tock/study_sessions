set -e

DB_TO_DROP="study_sessions"
DB_TO_CREATE="study_sessions"
DB_USER="app_user"
DB_PASS=$(openssl rand -base64 24)
DB_DEV_USER="ryan"

echo "Dropping and remaking database"
sudo -u "$DB_DEV_USER" dropdb --if-exists "$DB_TO_DROP"
sudo -u "$DB_DEV_USER" createdb "$DB_TO_CREATE"

echo "Running init file"
sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -f "init.sql"

echo "Creating users"
sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -c "DROP USER IF EXISTS $DB_USER;" 2>/dev/null || true
sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -c "GRANT CONNECT ON DATABASE $DB_TO_CREATE TO $DB_USER;"
sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -c "GRANT USAGE ON SCHEMA public TO $DB_USER;"
sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO $DB_USER;"
sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -c "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;"

sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO $DB_USER;"
sudo -u "$DB_DEV_USER" psql -d "$DB_TO_CREATE" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO $DB_USER;"

python << EOF
import bcrypt
import psycopg2
import sys

try:
    hashed = bcrypt.hashpw("$ADMIN_PASSWORD".encode(), bcrypt.gensalt()).decode()
    
    conn = psycopg2.connect("dbname=$DB_TO_CREATE user=$DB_DEV_USER")
    cur = conn.cursor()
    
    cur.execute(
        "INSERT INTO students (first_name, last_name, sharing) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING student_id",
        ('Admin', 'User', 'closed')
    )
    result = cur.fetchone()
    
    if result:
        student_id = result[0]
        print(f"Created admin student with ID: {student_id}")
    else:
        cur.execute("SELECT student_id FROM students WHERE first_name = %s AND last_name = %s", ('Admin', 'User'))
        student_id = cur.fetchone()[0]
        print(f"Using existing admin student ID: {student_id}")
    
    cur.execute(
        """INSERT INTO student_auth (student_id, hashed_password, is_admin) 
           VALUES (%s, %s, %s) 
           ON CONFLICT (student_id) 
           DO UPDATE SET hashed_password = %s, is_admin = %s""",
        (student_id, hashed, True, hashed, True)
    )
    
    conn.commit()
    cur.close()
    conn.close()
    print("Admin user created successfully")
    
except Exception as e:
    print(f"Error creating admin: {e}", file=sys.stderr)
    sys.exit(1)
EOF

echo "Database initialized. Add the following lines to your .envrc file"
echo "export DATABASE_URL='postgresql://$DB_USER:$DB_PASS@localhost/$DB_TO_CREATE'" 


# python python_scripts/curl_website.py
# source set_api_keys.sh && python python_scripts/init_auth.py
# python python_scripts/fill_courses.py
# python python_scripts/fill_exams.py
# python python_scripts/fill_finals.py
# python python_scripts/fill_students.py