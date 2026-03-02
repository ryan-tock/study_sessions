window.toggleAvatarMenu = function() {
    document.getElementById('avatar-menu').classList.toggle('hidden');
};
window.toggleDarkMode = function(e) {
    e.preventDefault();
    fetch('/api/my/dark_mode', { method: 'POST' })
        .then(function(res) { return res.json(); })
        .then(function(data) {
            document.body.classList.toggle('dark', data.dark_mode);
            var toggle = document.getElementById('dark-mode-toggle');
            if (toggle) toggle.textContent = data.dark_mode ? 'Light Mode' : 'Dark Mode';
        })
        .catch(function() {});
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
        let enrolledCourses = [];

        async function loadEnrollments() {
            try {
                const res = await fetch('/api/my/enrollments');
                enrolledCourses = await res.json();
                renderEnrolled();
            } catch (e) { console.warn('Failed to load enrollments:', e); }
        }

        function renderEnrolled() {
            if (!enrolledCourses.length) {
                listEl.innerHTML = '<div class="user-course-empty">No classes added yet.</div>';
                return;
            }
            listEl.innerHTML = enrolledCourses.map(c => {
                var full = allCourses.find(ac => ac.course_id === c.course_id);
                var noTutorBtn = '';
                if (full && full.no_tutor_needed) {
                    noTutorBtn = '<span class="no-tutor-badge">No tutor needed</span>';
                } else if (full && full.no_tutor_pending) {
                    noTutorBtn = '<span class="no-tutor-badge pending">Pending</span>';
                } else {
                    noTutorBtn = '<button type="button" class="no-tutor-report-btn" data-id="' + c.course_id + '" title="Report that this course doesn\'t need a tutor">No tutor needed</button>';
                }
                return `
                <div class="user-course-item" data-course-id="${c.course_id}">
                    <span class="user-course-code">${escHtml(c.department)} ${escHtml(c.identifier)}</span>
                    <span class="user-course-title">${escHtml(c.title || '')}</span>
                    ${noTutorBtn}
                    <button type="button" class="user-course-remove" data-id="${c.course_id}">✕</button>
                </div>`;
            }).join('');
            listEl.querySelectorAll('.user-course-remove').forEach(btn => {
                btn.addEventListener('click', () => removeEnrollment(parseInt(btn.dataset.id, 10)));
            });
            listEl.querySelectorAll('.no-tutor-report-btn').forEach(btn => {
                btn.addEventListener('click', () => reportNoTutor(parseInt(btn.dataset.id, 10)));
            });
            loadCourseTutors();
        }

        async function reportNoTutor(courseId) {
            var btn = listEl.querySelector('.no-tutor-report-btn[data-id="' + courseId + '"]');
            if (btn) {
                btn.outerHTML = '<span class="no-tutor-badge pending">Pending</span>';
            }
            var full = allCourses.find(ac => ac.course_id === courseId);
            if (full) full.no_tutor_pending = true;
            try {
                var res = await fetch('/api/my/report_no_tutor/' + courseId, { method: 'POST' });
                if (!res.ok) throw new Error();
            } catch (e) {
                if (full) full.no_tutor_pending = false;
                renderEnrolled();
            }
        }

        async function loadCourseTutors() {
            try {
                var [tutorRes, classmateRes] = await Promise.all([
                    fetch('/api/my/course_tutors'),
                    fetch('/api/my/classmates')
                ]);
                var tutorData = await tutorRes.json();
                var classmateData = await classmateRes.json();

                // Pivot classmates by course_id
                var classmateByCourse = {};
                classmateData.forEach(function(cm) {
                    cm.courses.forEach(function(course) {
                        if (!classmateByCourse[course.course_id]) classmateByCourse[course.course_id] = [];
                        classmateByCourse[course.course_id].push(cm);
                    });
                });

                listEl.querySelectorAll('.course-tutors-row, .course-classmates-row').forEach(el => el.remove());

                // Insert tutors and classmates after each course item
                enrolledCourses.forEach(function(c) {
                    var courseItem = listEl.querySelector('[data-course-id="' + c.course_id + '"]');
                    if (!courseItem) return;
                    var anchor = courseItem;

                    var ct = tutorData.find(t => t.course_id === c.course_id);
                    if (ct && ct.tutors.length) {
                        var tRow = document.createElement('div');
                        tRow.className = 'course-tutors-row';
                        tRow.innerHTML = '<span class="tutors-label">Tutors:</span><span class="chips-wrap">' +
                            ct.tutors.map(function(t) {
                                var label = escHtml(t.first_name) + ' ' + escHtml(t.last_name) + ' (' + t.confidence + ')';
                                if (t.from_course) label += ' <span class="tutor-from">' + escHtml(t.from_course) + '</span>';
                                return '<span class="tutor-chip">' + label + '</span>';
                            }).join(' ') + '</span>';
                        anchor.after(tRow);
                        anchor = tRow;
                    }

                    var cms = classmateByCourse[c.course_id];
                    if (cms && cms.length) {
                        var cRow = document.createElement('div');
                        cRow.className = 'course-classmates-row';
                        cRow.innerHTML = '<span class="tutors-label">Classmates:</span><span class="chips-wrap">' +
                            cms.map(function(cm) {
                                return '<span class="classmate-chip">' +
                                    escHtml(cm.first_name) + ' ' + escHtml(cm.last_name) +
                                '</span>';
                            }).join(' ') + '</span>';
                        anchor.after(cRow);
                    }
                });
            } catch(e) { /* silent */ }
        }

        async function removeEnrollment(courseId) {
            var removed = enrolledCourses.find(c => c.course_id === courseId);
            enrolledCourses = enrolledCourses.filter(c => c.course_id !== courseId);
            renderEnrolled();
            window.dispatchEvent(new Event('enrollments-changed'));
            try {
                var res = await fetch(`/api/my/enrollments/${courseId}`, { method: 'DELETE' });
                if (!res.ok) throw new Error();
            } catch (e) {
                if (removed) { enrolledCourses.push(removed); renderEnrolled(); }
                window.dispatchEvent(new Event('enrollments-changed'));
            }
        }

        async function addEnrollment(courseId) {
            resultsEl.classList.add('hidden');
            input.value = '';
            var course = allCourses.find(c => c.course_id === courseId);
            if (course && !enrolledCourses.find(c => c.course_id === courseId)) {
                enrolledCourses.push(course);
                renderEnrolled();
                window.dispatchEvent(new Event('enrollments-changed'));
            }
            try {
                const form = new FormData();
                form.append('course_id', courseId);
                var res = await fetch('/api/my/enrollments', { method: 'POST', body: form });
                if (!res.ok) throw new Error();
            } catch (e) {
                enrolledCourses = enrolledCourses.filter(c => c.course_id !== courseId);
                renderEnrolled();
                window.dispatchEvent(new Event('enrollments-changed'));
            }
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
        let tutorCaps = [];

        async function loadTutorCaps() {
            try {
                const res = await fetch('/api/my/tutor_capabilities');
                tutorCaps = await res.json();
                renderTutorCaps();
            } catch (e) { console.warn('Failed to load tutor capabilities:', e); }
        }

        function confidenceStyle(n) {
            if (n >= 8) return 'background:#dcfce7;color:#166534';
            if (n >= 5) return 'background:#fef9c3;color:#854d0e';
            return 'background:#fee2e2;color:#991b1b';
        }

        function renderTutorCaps() {
            if (!tutorCaps.length) {
                listEl.innerHTML = '<div class="user-course-empty">No courses added yet.</div>';
                return;
            }
            listEl.innerHTML = tutorCaps.map(c => {
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
                    var oldConf = (tutorCaps.find(c => c.course_id === courseId) || {}).confidence;
                    var cap = tutorCaps.find(c => c.course_id === courseId);
                    if (cap) cap.confidence = val;
                    renderTutorCaps();
                    try {
                        const form = new FormData();
                        form.append('course_id', courseId);
                        form.append('confidence', val);
                        var res = await fetch('/api/my/tutor_capabilities', { method: 'POST', body: form });
                        if (!res.ok) throw new Error();
                    } catch(e) {
                        if (cap && oldConf !== undefined) { cap.confidence = oldConf; renderTutorCaps(); }
                    }
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
            var removed = tutorCaps.find(c => c.course_id === courseId);
            tutorCaps = tutorCaps.filter(c => c.course_id !== courseId);
            renderTutorCaps();
            loadRecommendations();
            try {
                var res = await fetch(`/api/my/tutor_capabilities/${courseId}`, { method: 'DELETE' });
                if (!res.ok) throw new Error();
            } catch (e) {
                if (removed) { tutorCaps.push(removed); renderTutorCaps(); }
                loadRecommendations();
            }
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
            // Optimistic: hide the rec item immediately
            var recItems = document.querySelectorAll('#tutor-rec-list .tutor-rec-item');
            recItems.forEach(function(el) {
                var noBtn = el.querySelector('.tutor-rec-no[data-id="' + courseId + '"]');
                if (noBtn) el.style.display = 'none';
            });
            try {
                var res = await fetch(`/api/my/tutor_dismiss/${courseId}`, { method: 'POST' });
                if (!res.ok) throw new Error();
                loadRecommendations();
            } catch (e) {
                recItems.forEach(function(el) { el.style.display = ''; });
            }
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
            var courseId = selectedCourseId;
            var course = allCourses.find(c => c.course_id === courseId);
            closeTutorPanel();
            if (course) {
                var existing = tutorCaps.find(c => c.course_id === courseId);
                if (existing) {
                    existing.confidence = confidence;
                } else {
                    tutorCaps.push({
                        course_id: courseId, department: course.department,
                        identifier: course.identifier, title: course.title, confidence: confidence
                    });
                }
                renderTutorCaps();
                loadRecommendations();
            }
            try {
                const form = new FormData();
                form.append('course_id', courseId);
                form.append('confidence', confidence);
                var res = await fetch('/api/my/tutor_capabilities', { method: 'POST', body: form });
                if (!res.ok) throw new Error();
            } catch (e) {
                tutorCaps = tutorCaps.filter(c => c.course_id !== courseId);
                renderTutorCaps();
                loadRecommendations();
            }
        }

        document.getElementById('tutor-add-btn').addEventListener('click', saveTutorCapability);
        document.getElementById('tutor-cancel-btn').addEventListener('click', closeTutorPanel);
        document.getElementById('tutor-confidence').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') saveTutorCapability();
        });

        bindSearch('tutor-search-input', 'tutor-search-results', 'i-can-tutor-section',
            function(courses, resultsEl) {
                var filtered = courses.filter(c => !c.no_tutor_needed);
                if (!filtered.length) {
                    resultsEl.innerHTML = '<div class="course-search-empty">No courses found.</div>';
                } else {
                    resultsEl.innerHTML = filtered.slice(0, 30).map(c => `
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
            // Optimistic: hide the item immediately
            var btn = listEl.querySelector('.assessment-delete[data-id="' + examId + '"]');
            var item = btn ? btn.closest('.user-course-item') : null;
            if (item) item.style.display = 'none';
            try {
                var res = await fetch('/api/my/assessments/' + examId, { method: 'DELETE' });
                if (!res.ok) throw new Error();
                loadAssessments();
            } catch (e) {
                if (item) item.style.display = '';
            }
        }

        async function disputeAssessment(examId) {
            // Optimistic: disable button immediately
            var btn = listEl.querySelector('.assessment-dispute-btn[data-id="' + examId + '"]');
            if (btn) { btn.disabled = true; btn.textContent = 'Reported'; }
            try {
                var res = await fetch('/api/my/assessments/' + examId + '/dispute', { method: 'POST' });
                if (!res.ok) throw new Error();
                loadAssessments();
            } catch (e) {
                if (btn) { btn.disabled = false; btn.textContent = 'Report: No Final'; }
            }
        }

        reportBtn.addEventListener('click', async function() {
            if (!courseSelect.value || !dateInput.value) return;
            reportBtn.disabled = true;
            try {
                var form = new FormData();
                form.append('course_id', courseSelect.value);
                form.append('test_date', dateInput.value);
                form.append('exam_type', typeSelect.value);
                var res = await fetch('/api/my/assessments', { method: 'POST', body: form });
                if (!res.ok) {
                    var errData = await res.json().catch(function() { return {}; });
                    reportBtn.textContent = errData.detail || 'Failed';
                    reportBtn.classList.add('btn-error');
                    setTimeout(function() {
                        reportBtn.textContent = 'Report';
                        reportBtn.classList.remove('btn-error');
                        reportBtn.disabled = false;
                    }, 2000);
                    return;
                }
                await loadAssessments();
                courseSelect.value = '';
                reportBtn.textContent = 'Reported!';
                reportBtn.classList.add('btn-success');
                setTimeout(function() {
                    reportBtn.textContent = 'Report';
                    reportBtn.classList.remove('btn-success');
                    updateReportBtn();
                }, 2000);
            } catch (e) {
                reportBtn.disabled = false;
                console.warn('Failed to report assessment:', e);
            }
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

    // ── Classmates ──
    (function() {
        var listEl = document.getElementById('classmates-list');
        var searchInput = document.getElementById('classmate-search-input');
        if (!listEl) return;

        function renderClassmate(c) {
            var courses = c.courses.map(function(cr) {
                return '<span class="classmate-course-chip">' + escHtml(cr.department) + escHtml(cr.identifier) + '</span>';
            }).join(' ');
            return '<div class="classmate-item">' +
                '<span class="classmate-name">' + escHtml(c.first_name) + ' ' + escHtml(c.last_name) + '</span>' +
                '<span class="classmate-courses">' + (courses || '<span style="color:#888;">No visible courses</span>') + '</span>' +
            '</div>';
        }

        var searchTimer = null;
        searchInput.addEventListener('input', function() {
            var q = searchInput.value.trim();
            clearTimeout(searchTimer);
            if (q.length < 2) {
                listEl.innerHTML = '';
                return;
            }
            searchTimer = setTimeout(async function() {
                try {
                    var res = await fetch('/api/my/search_students?q=' + encodeURIComponent(q));
                    var data = await res.json();
                    if (!data.length) {
                        listEl.innerHTML = '<div class="user-course-empty">No students found.</div>';
                    } else {
                        listEl.innerHTML = data.map(renderClassmate).join('');
                    }
                } catch(e) {
                    listEl.innerHTML = '<div class="user-course-empty">Could not load classmates.</div>';
                }
            }, 300);
        });
    })();

})();
