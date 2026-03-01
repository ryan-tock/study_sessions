window.toggleAvatarMenu = function() {
    document.getElementById('avatar-menu').classList.toggle('hidden');
};
document.addEventListener('click', function(e) {
    if (!e.target.closest('.nav-avatar-wrapper')) {
        document.getElementById('avatar-menu').classList.add('hidden');
    }
});

function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

(async function() {
    // ── Load courses (shared across all search sections) ──
    let allCourses = [];
    let fuse = null;
    try {
        const res = await fetch('/api/courses');
        allCourses = await res.json();
        fuse = new Fuse(allCourses, {
            keys: [
                { name: 'combined', weight: 2 },
                { name: 'identifier', weight: 1.5 },
                { name: 'department', weight: 1 },
                { name: 'title', weight: 1 },
            ],
            threshold: 0.35,
            includeScore: true,
            minMatchCharLength: 2,
        });
    } catch (e) {
        console.warn('Failed to load courses:', e);
    }

    function fuseSearch(q) {
        return fuse ? fuse.search(q).map(r => r.item) : [];
    }

    function bindSearch(inputId, resultsId, sectionId, renderFn) {
        const input = document.getElementById(inputId);
        const resultsEl = document.getElementById(resultsId);
        input.addEventListener('input', function() {
            const q = this.value.trim();
            if (!q || !fuse) { resultsEl.classList.add('hidden'); return; }
            renderFn(fuseSearch(q), resultsEl);
        });
        input.addEventListener('focus', function() {
            if (this.value.trim() && fuse) renderFn(fuseSearch(this.value.trim()), resultsEl);
        });
        document.addEventListener('click', function(e) {
            if (!e.target.closest('#' + sectionId) && !e.target.closest('#' + resultsId)) {
                resultsEl.classList.add('hidden');
            }
        });
    }

    // ── My Classes ──
    (function() {
        const input = document.getElementById('enroll-search-input');
        const resultsEl = document.getElementById('enroll-search-results');
        const listEl = document.getElementById('enrolled-list');

        async function loadEnrollments() {
            try {
                const res = await fetch('/api/my/enrollments');
                renderEnrolled(await res.json());
            } catch (e) { console.warn('Failed to load enrollments:', e); }
        }

        function renderEnrolled(courses) {
            if (!courses.length) {
                listEl.innerHTML = '<div class="user-course-empty">No classes added yet.</div>';
                return;
            }
            listEl.innerHTML = courses.map(c => `
                <div class="user-course-item" data-course-id="${c.course_id}">
                    <span class="user-course-code">${escHtml(c.department)} ${escHtml(c.identifier)}</span>
                    <span class="user-course-title">${escHtml(c.title || '')}</span>
                    <button type="button" class="user-course-remove" data-id="${c.course_id}">✕</button>
                </div>
            `).join('');
            listEl.querySelectorAll('.user-course-remove').forEach(btn => {
                btn.addEventListener('click', () => removeEnrollment(parseInt(btn.dataset.id, 10)));
            });
            loadCourseTutors();
        }

        async function loadCourseTutors() {
            try {
                var res = await fetch('/api/my/course_tutors');
                var data = await res.json();
                // Remove old tutor rows
                listEl.querySelectorAll('.course-tutors-row').forEach(el => el.remove());
                data.forEach(function(ct) {
                    var courseItem = listEl.querySelector('[data-course-id="' + ct.course_id + '"]');
                    if (!courseItem || !ct.tutors.length) return;
                    var row = document.createElement('div');
                    row.className = 'course-tutors-row';
                    row.innerHTML = '<span class="tutors-label">Tutors:</span> ' +
                        ct.tutors.map(function(t) {
                            return '<span class="tutor-chip">' +
                                escHtml(t.first_name) + ' ' + escHtml(t.last_name) + ' (' + t.confidence + ')' +
                            '</span>';
                        }).join(' ');
                    courseItem.after(row);
                });
            } catch(e) { /* silent */ }
        }

        async function removeEnrollment(courseId) {
            try {
                await fetch(`/api/my/enrollments/${courseId}`, { method: 'DELETE' });
                await loadEnrollments();
                window.dispatchEvent(new Event('enrollments-changed'));
            } catch (e) { console.warn('Failed to remove enrollment:', e); }
        }

        async function addEnrollment(courseId) {
            resultsEl.classList.add('hidden');
            input.value = '';
            try {
                const form = new FormData();
                form.append('course_id', courseId);
                await fetch('/api/my/enrollments', { method: 'POST', body: form });
                await loadEnrollments();
                window.dispatchEvent(new Event('enrollments-changed'));
            } catch (e) { console.warn('Failed to add enrollment:', e); }
        }

        bindSearch('enroll-search-input', 'enroll-search-results', 'my-classes-section',
            function(courses, resultsEl) {
                if (!courses.length) {
                    resultsEl.innerHTML = '<div class="course-search-empty">No courses found.</div>';
                } else {
                    resultsEl.innerHTML = courses.slice(0, 30).map(c => `
                        <div class="course-search-item course-search-item-action" data-id="${c.course_id}">
                            <span class="course-search-code">${escHtml(c.department)} ${escHtml(c.identifier)}</span>
                            <span class="course-search-title">${escHtml(c.title || '')}</span>
                            <span class="course-search-meta">${c.semester_hours ? escHtml(c.semester_hours) + ' cr' : ''}</span>
                        </div>
                    `).join('');
                    resultsEl.querySelectorAll('.course-search-item-action').forEach(el => {
                        el.addEventListener('click', () => addEnrollment(parseInt(el.dataset.id, 10)));
                    });
                }
                resultsEl.classList.remove('hidden');
            }
        );

        loadEnrollments();
    })();

    // ── I Can Tutor ──
    (function() {
        const input = document.getElementById('tutor-search-input');
        const resultsEl = document.getElementById('tutor-search-results');
        const listEl = document.getElementById('tutor-list');
        const panel = document.getElementById('tutor-add-panel');
        let selectedCourseId = null;

        async function loadTutorCaps() {
            try {
                const res = await fetch('/api/my/tutor_capabilities');
                renderTutorCaps(await res.json());
            } catch (e) { console.warn('Failed to load tutor capabilities:', e); }
        }

        function confidenceStyle(n) {
            if (n >= 8) return 'background:#dcfce7;color:#166534';
            if (n >= 5) return 'background:#fef9c3;color:#854d0e';
            return 'background:#fee2e2;color:#991b1b';
        }

        function renderTutorCaps(courses) {
            if (!courses.length) {
                listEl.innerHTML = '<div class="user-course-empty">No courses added yet.</div>';
                return;
            }
            listEl.innerHTML = courses.map(c => {
                const conf = c.confidence ?? 1;
                const style = confidenceStyle(conf);
                return `
                    <div class="user-course-item" data-id="${c.course_id}">
                        <span class="user-course-code">${escHtml(c.department)} ${escHtml(c.identifier)}</span>
                        <span class="user-course-title">${escHtml(c.title || '')}</span>
                        <span class="confidence-badge tutor-conf-display" style="${style}" title="Confidence: ${conf}/10">${conf}</span>
                        <input type="number" class="tutor-conf-input hidden" min="1" max="10" value="${conf}">
                        <button type="button" class="btn-action tutor-conf-save hidden">Save</button>
                        <button type="button" class="user-course-remove tutor-conf-cancel hidden">✕</button>
                        <button type="button" class="tutor-conf-edit" title="Edit confidence">✎</button>
                        <button type="button" class="user-course-remove tutor-remove-btn">✕</button>
                    </div>
                `;
            }).join('');
            listEl.querySelectorAll('.tutor-remove-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const id = parseInt(btn.closest('[data-id]').dataset.id, 10);
                    removeTutorCap(id);
                });
            });
            listEl.querySelectorAll('.tutor-conf-edit').forEach(btn => {
                btn.addEventListener('click', () => {
                    const item = btn.closest('.user-course-item');
                    item.querySelector('.tutor-conf-display').classList.add('hidden');
                    item.querySelector('.tutor-conf-edit').classList.add('hidden');
                    item.querySelector('.tutor-remove-btn').classList.add('hidden');
                    item.querySelector('.tutor-conf-input').classList.remove('hidden');
                    item.querySelector('.tutor-conf-save').classList.remove('hidden');
                    item.querySelector('.tutor-conf-cancel').classList.remove('hidden');
                    item.querySelector('.tutor-conf-input').focus();
                });
            });
            listEl.querySelectorAll('.tutor-conf-cancel').forEach(btn => {
                btn.addEventListener('click', () => {
                    const item = btn.closest('.user-course-item');
                    item.querySelector('.tutor-conf-display').classList.remove('hidden');
                    item.querySelector('.tutor-conf-edit').classList.remove('hidden');
                    item.querySelector('.tutor-remove-btn').classList.remove('hidden');
                    item.querySelector('.tutor-conf-input').classList.add('hidden');
                    item.querySelector('.tutor-conf-save').classList.add('hidden');
                    item.querySelector('.tutor-conf-cancel').classList.add('hidden');
                });
            });
            listEl.querySelectorAll('.tutor-conf-save').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const item = btn.closest('.user-course-item');
                    const courseId = parseInt(item.dataset.id, 10);
                    const val = parseInt(item.querySelector('.tutor-conf-input').value, 10);
                    if (isNaN(val) || val < 1 || val > 10) {
                        item.querySelector('.tutor-conf-input').focus();
                        return;
                    }
                    const form = new FormData();
                    form.append('course_id', courseId);
                    form.append('confidence', val);
                    await fetch('/api/my/tutor_capabilities', { method: 'POST', body: form });
                    await loadTutorCaps();
                });
            });
            listEl.querySelectorAll('.tutor-conf-input').forEach(inp => {
                inp.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter') inp.closest('.user-course-item').querySelector('.tutor-conf-save').click();
                    if (e.key === 'Escape') inp.closest('.user-course-item').querySelector('.tutor-conf-cancel').click();
                });
            });
        }

        async function removeTutorCap(courseId) {
            try {
                await fetch(`/api/my/tutor_capabilities/${courseId}`, { method: 'DELETE' });
                await loadTutorCaps();
                await loadRecommendations();
            } catch (e) { console.warn('Failed to remove tutor capability:', e); }
        }

        async function loadRecommendations() {
            try {
                const res = await fetch('/api/my/tutor_recommendations');
                renderRecommendations(await res.json());
            } catch (e) { console.warn('Failed to load recommendations:', e); }
        }

        function renderRecommendations(courses) {
            const container = document.getElementById('tutor-recommendations');
            const listEl = document.getElementById('tutor-rec-list');
            if (!courses.length) {
                container.classList.add('hidden');
                return;
            }
            container.classList.remove('hidden');
            listEl.innerHTML = courses.map(c => `
                <div class="tutor-rec-item">
                    <span class="user-course-code">${escHtml(c.department)} ${escHtml(c.identifier)}</span>
                    <span class="user-course-title">${escHtml(c.title || '')}</span>
                    <button type="button" class="btn-action btn-elevate tutor-rec-yes"
                            data-id="${c.course_id}"
                            data-name="${escHtml(c.department + ' ' + c.identifier)}">Yes</button>
                    <button type="button" class="tutor-rec-no" data-id="${c.course_id}">No</button>
                </div>
            `).join('');
            listEl.querySelectorAll('.tutor-rec-yes').forEach(btn => {
                btn.addEventListener('click', () => openTutorPanel(
                    parseInt(btn.dataset.id, 10), btn.dataset.name
                ));
            });
            listEl.querySelectorAll('.tutor-rec-no').forEach(btn => {
                btn.addEventListener('click', () => dismissRecommendation(parseInt(btn.dataset.id, 10)));
            });
        }

        async function dismissRecommendation(courseId) {
            try {
                await fetch(`/api/my/tutor_dismiss/${courseId}`, { method: 'POST' });
                await loadRecommendations();
            } catch (e) { console.warn('Failed to dismiss recommendation:', e); }
        }

        function openTutorPanel(courseId, courseName) {
            selectedCourseId = courseId;
            document.getElementById('tutor-selected-course-name').textContent = courseName;
            document.getElementById('tutor-confidence').value = '7';
            panel.classList.remove('hidden');
            resultsEl.classList.add('hidden');
            input.value = '';
        }

        function closeTutorPanel() {
            panel.classList.add('hidden');
            selectedCourseId = null;
        }

        async function saveTutorCapability() {
            if (!selectedCourseId) return;
            const confidence = parseInt(document.getElementById('tutor-confidence').value, 10);
            if (isNaN(confidence) || confidence < 1 || confidence > 10) {
                document.getElementById('tutor-confidence').focus();
                return;
            }
            try {
                const form = new FormData();
                form.append('course_id', selectedCourseId);
                form.append('confidence', confidence);
                await fetch('/api/my/tutor_capabilities', { method: 'POST', body: form });
                closeTutorPanel();
                await loadTutorCaps();
                await loadRecommendations();
            } catch (e) { console.warn('Failed to save tutor capability:', e); }
        }

        document.getElementById('tutor-add-btn').addEventListener('click', saveTutorCapability);
        document.getElementById('tutor-cancel-btn').addEventListener('click', closeTutorPanel);
        document.getElementById('tutor-confidence').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') saveTutorCapability();
        });

        bindSearch('tutor-search-input', 'tutor-search-results', 'i-can-tutor-section',
            function(courses, resultsEl) {
                if (!courses.length) {
                    resultsEl.innerHTML = '<div class="course-search-empty">No courses found.</div>';
                } else {
                    resultsEl.innerHTML = courses.slice(0, 30).map(c => `
                        <div class="course-search-item course-search-item-action"
                             data-id="${c.course_id}"
                             data-name="${escHtml(c.department + ' ' + c.identifier)}">
                            <span class="course-search-code">${escHtml(c.department)} ${escHtml(c.identifier)}</span>
                            <span class="course-search-title">${escHtml(c.title || '')}</span>
                            <span class="course-search-meta">${c.semester_hours ? escHtml(c.semester_hours) + ' cr' : ''}</span>
                        </div>
                    `).join('');
                    resultsEl.querySelectorAll('.course-search-item-action').forEach(el => {
                        el.addEventListener('click', () => openTutorPanel(
                            parseInt(el.dataset.id, 10),
                            el.dataset.name
                        ));
                    });
                }
                resultsEl.classList.remove('hidden');
            }
        );

        loadTutorCaps();
        loadRecommendations();
    })();

    // ── Upcoming Assessments ──
    (function() {
        var courseSelect = document.getElementById('assessment-course-select');
        var dateInput = document.getElementById('assessment-date-input');
        var typeSelect = document.getElementById('assessment-type-select');
        var reportBtn = document.getElementById('assessment-report-btn');
        var listEl = document.getElementById('assessments-list');

        // Default to tomorrow
        var tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        dateInput.value = tomorrow.toISOString().slice(0, 10);

        var _months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

        function updateReportBtn() {
            reportBtn.disabled = !courseSelect.value || !dateInput.value;
        }
        courseSelect.addEventListener('change', updateReportBtn);
        dateInput.addEventListener('input', updateReportBtn);

        async function populateCourses() {
            try {
                var res = await fetch('/api/my/enrollments');
                var courses = await res.json();
                courseSelect.innerHTML = '<option value="" disabled selected>Select course...</option>';
                courses.forEach(function(c) {
                    var opt = document.createElement('option');
                    opt.value = c.course_id;
                    opt.textContent = c.department + ' ' + c.identifier + (c.title ? ' \u2014 ' + c.title : '');
                    courseSelect.appendChild(opt);
                });
            } catch (e) { console.warn('Failed to load courses for assessment dropdown:', e); }
        }

        async function loadAssessments() {
            try {
                var res = await fetch('/api/my/assessments');
                renderAssessments(await res.json());
            } catch (e) { console.warn('Failed to load assessments:', e); }
        }

        function renderAssessments(assessments) {
            if (!assessments.length) {
                listEl.innerHTML = '<div class="user-course-empty">No upcoming assessments for your courses.</div>';
                return;
            }
            var lastDate = null;
            listEl.innerHTML = assessments.map(function(a) {
                var dateHeader = '';
                if (a.test_date !== lastDate) {
                    var parts = a.test_date.split('-');
                    dateHeader = '<div class="assessment-date-header">' +
                        _months[parseInt(parts[1], 10) - 1] + ' ' + parseInt(parts[2], 10) + ', ' + parts[0] +
                        '</div>';
                    lastDate = a.test_date;
                }
                var typeLabel, typeCls;
                if (a.exam_type === 'quiz') { typeLabel = 'Quiz'; typeCls = 'assessment-type-quiz'; }
                else if (a.exam_type === 'final') { typeLabel = 'Final'; typeCls = 'assessment-type-final'; }
                else if (a.exam_type === 'common_hour') { typeLabel = 'Common Hour'; typeCls = 'assessment-type-common'; }
                else { typeLabel = 'In-Class'; typeCls = 'assessment-type-test'; }
                var confirmCls = a.confirmed ? '' : ' assessment-unconfirmed';
                var actions = '';
                if (a.is_mine && !a.confirmed) {
                    actions += '<button type="button" class="user-course-remove assessment-delete" data-id="' + a.exam_id + '">\u2715</button>';
                }
                if (a.exam_type === 'final' && a.confirmed) {
                    actions += '<button type="button" class="btn-action assessment-dispute-btn" data-id="' + a.exam_id + '">Report: No Final</button>';
                }
                var pendingBadge = !a.confirmed ? '<span class="assessment-pending-badge">Pending</span>' : '';
                return dateHeader +
                    '<div class="user-course-item' + confirmCls + '">' +
                        '<span class="assessment-type-badge ' + typeCls + '">' + typeLabel + '</span>' +
                        '<span class="user-course-code">' + escHtml(a.department) + ' ' + escHtml(a.identifier) + '</span>' +
                        '<span class="user-course-title">' + escHtml(a.title || '') + '</span>' +
                        pendingBadge +
                        actions +
                    '</div>';
            }).join('');
            listEl.querySelectorAll('.assessment-delete').forEach(function(btn) {
                btn.addEventListener('click', function() { deleteAssessment(parseInt(btn.dataset.id, 10)); });
            });
            listEl.querySelectorAll('.assessment-dispute-btn').forEach(function(btn) {
                btn.addEventListener('click', function() { disputeAssessment(parseInt(btn.dataset.id, 10)); });
            });
        }

        async function deleteAssessment(examId) {
            try {
                var res = await fetch('/api/my/assessments/' + examId, { method: 'DELETE' });
                if (!res.ok) console.warn('Delete assessment failed:', res.status);
                await loadAssessments();
            } catch (e) { console.warn('Failed to delete assessment:', e); }
        }

        async function disputeAssessment(examId) {
            try {
                var res = await fetch('/api/my/assessments/' + examId + '/dispute', { method: 'POST' });
                if (!res.ok) console.warn('Dispute assessment failed:', res.status);
                await loadAssessments();
            } catch (e) { console.warn('Failed to dispute assessment:', e); }
        }

        reportBtn.addEventListener('click', async function() {
            if (!courseSelect.value || !dateInput.value) return;
            try {
                var form = new FormData();
                form.append('course_id', courseSelect.value);
                form.append('test_date', dateInput.value);
                form.append('exam_type', typeSelect.value);
                var res = await fetch('/api/my/assessments', { method: 'POST', body: form });
                if (!res.ok) console.warn('Report assessment failed:', res.status);
                await loadAssessments();
            } catch (e) { console.warn('Failed to report assessment:', e); }
        });

        populateCourses();
        loadAssessments();
        window.addEventListener('enrollments-changed', function() {
            populateCourses();
            loadAssessments();
        });
    })();

    // ── Study Sessions ──
    (function() {
        var listEl = document.getElementById('study-sessions-list');
        if (!listEl) return;

        var _months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

        async function loadSessions() {
            try {
                var res = await fetch('/api/my/study_sessions');
                var sessions = await res.json();
                if (!sessions.length) {
                    listEl.innerHTML = '<div class="user-course-empty">No study sessions scheduled for your courses.</div>';
                    return;
                }
                listEl.innerHTML = sessions.map(function(s) {
                    var typeLabel = s.exam_type === 'final' ? 'Final' :
                        s.exam_type === 'common_hour' ? 'Common Hour' :
                        s.exam_type === 'quiz' ? 'Quiz' : 'In-Class';
                    var typeCls = s.exam_type === 'final' ? 'assessment-type-final' :
                        s.exam_type === 'common_hour' ? 'assessment-type-common' :
                        s.exam_type === 'quiz' ? 'assessment-type-quiz' : 'assessment-type-test';
                    var dt = new Date(s.session_timestamp);
                    var dateStr = _months[dt.getMonth()] + ' ' + dt.getDate();
                    var hours = dt.getHours();
                    var ampm = hours >= 12 ? 'PM' : 'AM';
                    hours = hours % 12 || 12;
                    var mins = String(dt.getMinutes()).padStart(2, '0');
                    var timeStr = hours + ':' + mins + ' ' + ampm;
                    var tutorStr = s.is_tutor ? 'You are tutoring' :
                        s.tutor_first ? 'Tutor: ' + escHtml(s.tutor_first) + ' ' + escHtml(s.tutor_last) : 'No tutor assigned';
                    var tutorCls = 'study-session-tutor' + (s.is_tutor ? ' is-tutor' : '');
                    return '<div class="user-course-item study-session-item">' +
                        '<span class="assessment-type-badge ' + typeCls + '">' + typeLabel + '</span>' +
                        '<span class="user-course-code">' + escHtml(s.department) + ' ' + escHtml(s.identifier) + '</span>' +
                        '<span class="study-session-meta">' + dateStr + ' at ' + timeStr + ' \u00b7 ' + escHtml(s.location) + '</span>' +
                        '<span class="' + tutorCls + '">' + tutorStr + '</span>' +
                    '</div>';
                }).join('');
            } catch(e) {
                listEl.innerHTML = '<div class="user-course-empty">Could not load study sessions.</div>';
            }
        }

        loadSessions();
        window.addEventListener('enrollments-changed', loadSessions);
    })();

})();
