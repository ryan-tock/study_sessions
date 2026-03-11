# User Guide

## First Login

On first login you'll be prompted to set a personal password (12+ chars, 1+ number, 1+ special character) and choose a privacy setting. You can skip password setup and do it later from the avatar menu.

### Privacy Settings

| Setting | Who can see your schedule |
|---|---|
| **Open** | Anyone logged in |
| **Classmates only** | Only students sharing a course with you that term |
| **Private** | Only admins |

This controls who sees your enrollments and tutor capabilities. Admins always have full visibility.

---

## User Portal

### Report Exam

Report an in-class test or quiz not already on the schedule. Your report appears as "Pending" until an admin confirms it.

### Upcoming Exams

Exams are color-coded by type: red = final, blue = common hour, yellow = quiz, green = in-class test.

- **Delete (X)** on your own unconfirmed reports removes them
- **Report: No Final** on confirmed finals disputes the exam for admin review

### My Classes

Fuzzy search for courses -- partial names and typos work. Each enrolled course shows tutor chips (name + confidence) and classmate chips (respects privacy settings). Graduated users cannot modify enrollments.

### I Can Tutor

Declare courses you can tutor with a confidence level (1-10). Click the pencil icon to edit confidence inline (Enter to save, Escape to cancel).

**Confidence colors:** green = 8-10, yellow = 5-7, red = 1-4

### Classmates

Search any student by name. Results show shared courses, respecting their privacy settings. Hidden for graduated users.

---

## Admin Portal

Accessible to Study Session Coordinators, Scholarship Chairs, BCA Scholarship holders, and root.

### Import Courses

"Fetch & Preview" downloads the course catalog from the school website. A status indicator shows whether courses are already imported, on disk, or not yet fetched. This will break if Mines ever updates their website format. If this no longer works, contact `scholarship@r71.org`.

### Import Exams

Upload Common Hour or Finals PDFs. The preview table marks entries as "Will import", "Already exists", or "Not in DB" (course not yet imported). For finals, use the detailed exam grid PDF, not the overview. This may also break if the PDF format changes so contact `scholarship@r71.org` if it does.

### Todos

Items needing admin action:

- **Needs Session**: Exams within 5 days with no study session scheduled. "Skip" defers it.
- **New Report**: Student-reported exams. Confirm or reject.
- **Disputed Final**: Students disputing a final. Delete the final or restore it.
- **No-Tutor Reports**: Courses flagged as non-academic. Approve or dismiss.

### Manage Users

Click a user's **role badge** to change their role (opens a dropdown). Changing to/from admin roles requires confirmation.

**Edit modal**: Click "Edit" to modify name, Discord ID, enrollments, and tutor capabilities for any user.

### Course Links

| Link type | Effect |
|---|---|
| **Strong** | Same exam -- students and tutors are combined into one session |
| **Weak** | Related material -- tutors transfer but sessions stay separate |

Open from the link icon on any exam. Auto-generated suggestions based on title similarity are shown.

### Study Sessions

**Scheduling**: Select a tutor (or "No tutor" for open sessions), set date/time (defaults to day before exam at 3pm), and location.

**Copy Ping** generates a Discord announcement with a heading, student pings, and a message body:

```
# MATH112 (Calc 2) Study session
<@student1> <@student2>

[message body with session details and tutor mention]
```

The message includes the date, time, location, and tutor. **Copy to Clipboard** copies the full announcement for pasting into Discord.

### Restore & Review

Deleted exams, skipped exams, disputed finals, and approved no-tutor courses. Filter by category or search by course.

### Backup & Restore (Root Only)

"Make Backup" creates a timestamped snapshot of the entire database. "Browse Backups" lets you view, restore, or delete backups. Restoring replaces all non-root data.
