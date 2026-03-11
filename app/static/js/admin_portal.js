        var _DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
        var _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

        // Format an ISO date string (YYYY-MM-DD) or Date object to "Wed, Jan 15"
        function formatDate(d) {
            if (typeof d === 'string') {
                var parts = d.split('-');
                d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
            }
            return _DAYS[d.getDay()] + ', ' + _MONTHS[d.getMonth()] + ' ' + d.getDate();
        }

        window.dismissChecklist = function(btn) {
            btn.parentElement.classList.add('hidden');
            fetch('/admin/api/dismiss_checklist', { method: 'POST' }).catch(function() {});
        };

        let showingGraduates = false;

        function applyGraduateFilter() {
            document.querySelectorAll('#user-table-body tr[data-graduated="true"]').forEach(function(row) {
                row.style.display = showingGraduates ? '' : 'none';
            });
        }

        window.toggleGraduates = function() {
            showingGraduates = !showingGraduates;
            document.getElementById('toggle-graduates-btn').textContent =
                showingGraduates ? 'Hide Graduates' : 'Show Graduates';
            applyGraduateFilter();
        };

        applyGraduateFilter();

        // ===== Populate import section term labels and status =====
        const _portalData = JSON.parse(document.getElementById('portal-data').textContent);

        // ===== Role change popup =====
        (function() {
            var popup = document.getElementById('role-change-popup');
            var _targetId = null, _currentRole = null, _name = null;

            window.openRolePopup = function(event, studentId, currentRole, name) {
                event.stopPropagation();
                _targetId = studentId;
                _currentRole = currentRole;
                _name = name;

                var roleOptions = (_portalData.role_options || []).filter(function(opt) {
                    if (_portalData.current_user_role === 'study_session_coordinator') {
                        return opt.level < _portalData.current_user_role_level;
                    }
                    return opt.level <= _portalData.current_user_role_level;
                });

                popup.innerHTML = roleOptions.map(function(opt) {
                    var cls = opt.value === currentRole ? ' active-role' : '';
                    return '<button type="button" class="' + cls + '" data-role="' + opt.value + '">' + opt.label + '</button>';
                }).join('');

                popup.querySelectorAll('button').forEach(function(btn) {
                    btn.addEventListener('click', function() {
                        var newRole = btn.dataset.role;
                        if (newRole === _currentRole) { closeRolePopup(); return; }
                        var adminRoles = ['study_session_coordinator', 'scholarship_chair', 'bca_scholarship'];
                        var isMajorChange = adminRoles.indexOf(newRole) !== -1 || adminRoles.indexOf(_currentRole) !== -1;
                        if (isMajorChange && !confirm('Change role for ' + _name + ' to ' + btn.textContent.trim() + '?\n\nNote: they may need to log out and back in for changes to take effect.')) return;
                        var form = document.createElement('form');
                        form.method = 'POST';
                        form.action = '/admin/set_role';
                        var tid = document.createElement('input');
                        tid.type = 'hidden'; tid.name = 'target_id'; tid.value = _targetId;
                        var role = document.createElement('input');
                        role.type = 'hidden'; role.name = 'role'; role.value = newRole;
                        form.appendChild(tid);
                        form.appendChild(role);
                        document.body.appendChild(form);
                        form.submit();
                    });
                });

                var rect = event.currentTarget.getBoundingClientRect();
                popup.classList.remove('hidden');
                var popupWidth = popup.offsetWidth;
                var popupHeight = popup.offsetHeight;
                var left = rect.left;
                if (left + popupWidth > window.innerWidth - 8) {
                    left = window.innerWidth - popupWidth - 8;
                }
                if (left < 8) left = 8;
                var top = rect.bottom + 4;
                if (top + popupHeight > window.innerHeight - 8) {
                    top = rect.top - popupHeight - 4;
                }
                popup.style.left = left + 'px';
                popup.style.top = top + 'px';
            };

            function closeRolePopup() {
                popup.classList.add('hidden');
                _targetId = null;
            }

            document.addEventListener('click', function(e) {
                if (!popup.classList.contains('hidden') && !popup.contains(e.target)) {
                    closeRolePopup();
                }
            });
        })();
        (function() {
            const ct = _portalData.current_term;
            const cts = _portalData.current_term_status || {};

            const termLabel = ct
                ? ct.season.charAt(0).toUpperCase() + ct.season.slice(1) + ' ' + ct.academic_year
                : 'Unknown term';
            const noTermHtml = '<span style="color:#e74c3c">No term detected</span>';

            // Course import
            const courseTermEl = document.getElementById('course-term-label');
            if (courseTermEl) courseTermEl.textContent = ct ? termLabel : '';
            if (!ct && courseTermEl) courseTermEl.innerHTML = noTermHtml;

            const cacheStatEl = document.getElementById('course-cache-status');
            if (cacheStatEl) {
                if (cts.courses_in_db > 0) {
                    cacheStatEl.innerHTML = '<span style="color:#27ae60;">\u2713 ' + cts.courses_in_db + ' courses in database</span>';
                } else if (cts.has_course_cache) {
                    cacheStatEl.innerHTML = '<span style="color:#e67e22;">\u2713 On server \u00b7 Not imported</span>' +
                        ' <button type="button" class="btn-action btn-elevate" id="import-courses-cache-btn" style="padding:2px 8px;font-size:11px;margin-left:6px;">Import</button>';
                    document.getElementById('import-courses-cache-btn').addEventListener('click', async function() {
                        var btn = this;
                        btn.disabled = true;
                        btn.textContent = 'Importing\u2026';
                        try {
                            var res = await fetch('/admin/api/import_courses_from_cache', { method: 'POST' });
                            var data = await res.json();
                            if (res.ok) {
                                cacheStatEl.innerHTML = '<span style="color:#27ae60;">\u2713 Imported: ' + data.summary + '</span>';
                            } else {
                                cacheStatEl.innerHTML = '<span style="color:#e74c3c;">Import failed: ' + (data.detail || 'unknown error') + '</span>';
                            }
                        } catch (e) {
                            cacheStatEl.innerHTML = '<span style="color:#e74c3c;">Import failed</span>';
                        }
                    });
                } else {
                    cacheStatEl.innerHTML = '<span style="color:#888;">No courses downloaded</span>';
                }
            }

            // Exam import
            const examTermEl = document.getElementById('exam-term-label');
            if (examTermEl) examTermEl.textContent = ct ? termLabel : '';
            if (!ct && examTermEl) examTermEl.innerHTML = noTermHtml;

            const pdfStatusEl = document.getElementById('exam-pdf-status');
            if (pdfStatusEl) {
                function pdfIcon(label, hasPdf, inDbCount, examType) {
                    var statusHtml;
                    if (inDbCount > 0) {
                        statusHtml = '<span style="color:#27ae60;">\u2713 ' + inDbCount + ' in database</span>';
                    } else if (hasPdf) {
                        var btnId = 'import-' + examType + '-btn';
                        statusHtml = '<span style="color:#e67e22;">\u2713 On server \u00b7 Not imported</span>' +
                            ' <button type="button" class="btn-action btn-elevate" id="' + btnId + '" style="padding:2px 8px;font-size:11px;margin-left:6px;">Import</button>';
                    } else {
                        statusHtml = '<span style="color:#aaa;">\u2717 Not yet uploaded</span>';
                    }
                    return '<div class="exam-pdf-status-row"><span class="exam-pdf-status-label">' + label + ':</span>' + statusHtml + '</div>';
                }
                pdfStatusEl.innerHTML =
                    pdfIcon('Common Hour', cts.has_common_hour_pdf, cts.common_hour_in_db, 'common_hour') +
                    pdfIcon('Finals', cts.has_finals_pdf, cts.finals_in_db, 'final');

                function attachPdfImport(btnId, examType, rowLabel) {
                    var btn = document.getElementById(btnId);
                    if (!btn) return;
                    btn.addEventListener('click', async function() {
                        btn.disabled = true;
                        btn.textContent = 'Importing\u2026';
                        var row = btn.closest('.exam-pdf-status-row');
                        try {
                            var body = new URLSearchParams();
                            body.append('exam_type', examType);
                            var res = await fetch('/admin/api/import_exams_from_disk', { method: 'POST', body: body });
                            var data = await res.json();
                            if (res.ok) {
                                var span = row.querySelector('.exam-pdf-status-label');
                                row.innerHTML = '<span class="exam-pdf-status-label">' + rowLabel + ':</span>' +
                                    '<span style="color:#27ae60;">\u2713 Imported ' + data.inserted + ' exam(s)</span>';
                            } else {
                                btn.textContent = 'Failed';
                                btn.style.color = '#e74c3c';
                            }
                        } catch (e) {
                            btn.textContent = 'Failed';
                            btn.style.color = '#e74c3c';
                        }
                    });
                }
                attachPdfImport('import-common_hour-btn', 'common_hour', 'Common Hour');
                attachPdfImport('import-final-btn', 'final', 'Finals');
            }
        })();

        // ===== Populate wipe terms list =====
        (function() {
            const list = document.getElementById('wipe-terms-list');
            if (!list) return;
            const terms = (_portalData && _portalData.wipe_terms) || [];
            if (!terms.length) {
                list.innerHTML = '<em style="color:#aaa;font-size:13px;">No wipeable data found in /data.</em>';
                return;
            }
            terms.forEach(function(t) {
                const label = document.createElement('label');
                label.className = 'wipe-option wipe-term-option';
                const badges = [];
                if (t.has_users)        badges.push('<span class="wipe-badge wipe-badge-users">users</span>');
                if (t.has_exams)        badges.push('<span class="wipe-badge wipe-badge-exams">exams</span>');
                if (t.has_courses)      badges.push('<span class="wipe-badge wipe-badge-courses">courses</span>');
                if (t.has_course_cache) badges.push('<span class="wipe-badge wipe-badge-cache">course data</span>');
                label.innerHTML = '<input type="checkbox" name="term" value="' + t.term + '"> <strong>' + t.label + '</strong> ' + badges.join('');
                list.appendChild(label);
            });
        })();

        // ===== Create User validation =====
        const firstNameInput = document.getElementById('first_name');
        const lastNameInput = document.getElementById('last_name');
        const discordInput = document.getElementById('discord_id');
        const createBtn = document.getElementById('create-user-btn');
        let discordState = 'idle';
        let discordTimer = null;
        const NAME_PATTERN = /^[\p{L}\p{M} '\-.]+$/u;

        function setReqClass(id, cls) {
            const el = document.getElementById(id);
            el.classList.remove('met', 'unmet', 'checking', 'no-token');
            if (cls) el.classList.add(cls);
        }

        function updateCreateBtn() {
            const firstName = firstNameInput.value.trim();
            const lastName = lastNameInput.value.trim();
            const discord = discordInput.value.trim();
            const numericOk = /^\d+$/.test(discord);
            const firstNameOk = firstName && NAME_PATTERN.test(firstName);
            const lastNameOk = lastName && NAME_PATTERN.test(lastName);
            if (!firstName) setReqClass('req-firstname', null);
            else setReqClass('req-firstname', firstNameOk ? 'met' : 'unmet');
            if (!lastName) setReqClass('req-lastname', null);
            else setReqClass('req-lastname', lastNameOk ? 'met' : 'unmet');
            if (!discord) setReqClass('req-discord-numeric', null);
            else setReqClass('req-discord-numeric', numericOk ? 'met' : 'unmet');
            const discordOk = discordState === 'valid' || discordState === 'no-token';
            createBtn.disabled = !(firstNameOk && lastNameOk && numericOk && discordOk);
        }

        function hideDiscordPreview() {
            document.getElementById('discord-preview').classList.add('hidden');
        }

        function showDiscordPreview(avatarUrl, username) {
            document.getElementById('discord-preview-img').src = avatarUrl;
            document.getElementById('discord-preview-name').textContent = username;
            document.getElementById('discord-preview').classList.remove('hidden');
        }

        function onDiscordInput() {
            const val = discordInput.value.trim();
            clearTimeout(discordTimer);
            hideDiscordPreview();
            if (!val || !/^\d+$/.test(val)) {
                discordState = 'idle';
                setReqClass('req-discord-valid', null);
                document.getElementById('req-discord-text').textContent = 'Valid Discord account with profile picture';
                document.getElementById('req-discord-spinner').classList.add('hidden');
            } else {
                discordState = 'checking';
                setReqClass('req-discord-valid', 'checking');
                document.getElementById('req-discord-text').textContent = 'Checking Discord account...';
                document.getElementById('req-discord-spinner').classList.remove('hidden');
                discordTimer = setTimeout(function() { doDiscordCheck(val); }, 1000);
            }
            updateCreateBtn();
        }

        async function doDiscordCheck(discordId) {
            try {
                const res = await fetch('/admin/api/validate_discord/' + discordId);
                if (!res.ok) throw new Error();
                const data = await res.json();
                document.getElementById('req-discord-spinner').classList.add('hidden');
                if (data.status === 'valid') {
                    discordState = 'valid';
                    setReqClass('req-discord-valid', 'met');
                    document.getElementById('req-discord-text').textContent = 'Valid Discord account with profile picture';
                    showDiscordPreview(data.avatar_url, data.username);
                } else if (data.status === 'no_token') {
                    discordState = 'no-token';
                    setReqClass('req-discord-valid', 'no-token');
                    document.getElementById('req-discord-text').textContent = 'Cannot verify (no bot token configured)';
                } else {
                    discordState = 'invalid';
                    setReqClass('req-discord-valid', 'unmet');
                    document.getElementById('req-discord-text').textContent = data.error || 'Invalid Discord account';
                }
            } catch (e) {
                document.getElementById('req-discord-spinner').classList.add('hidden');
                discordState = 'invalid';
                setReqClass('req-discord-valid', 'unmet');
                document.getElementById('req-discord-text').textContent = 'Could not verify Discord ID';
            }
            updateCreateBtn();
        }

        firstNameInput.addEventListener('input', updateCreateBtn);
        lastNameInput.addEventListener('input', updateCreateBtn);
        discordInput.addEventListener('input', onDiscordInput);

        // ===== Make Backup =====
        window.makeBackup = async function() {
            const btn = document.getElementById('make-backup-btn');
            btn.disabled = true;
            btn.textContent = 'Saving…';
            try {
                const res = await fetch('/admin/backup', { method: 'POST' });
                const data = await res.json();
                btn.textContent = 'Make Backup';
                btn.disabled = false;
                if (data.ok) {
                    btn.textContent = 'Saved ✓';
                    setTimeout(() => { btn.textContent = 'Make Backup'; }, 2000);
                }
            } catch(e) {
                btn.textContent = 'Make Backup';
                btn.disabled = false;
            }
        };

        // ===== Backup Browser =====
        function formatBackupName(name) {
            const [date, time] = name.split('_');
            const [y, mo, d] = date.split('-');
            const h = time.slice(0, 2), mi = time.slice(2, 4);
            const dt = new Date(y, mo - 1, d, h, mi);
            return dt.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
        }

        function formatTermName(term) {
            if (!term) return null;
            const [year, letter] = term.split('_');
            const season = letter === 'A' ? 'Spring' : 'Fall';
            return `${season} ${year}`;
        }

        async function loadBackupsList() {
            const listEl = document.getElementById('backups-list');
            listEl.innerHTML = '<div class="backups-loading">Loading…</div>';
            try {
                const res = await fetch('/admin/api/backups');
                const backups = await res.json();
                if (!backups.length) {
                    listEl.innerHTML = '<div class="backups-loading">No backups found.</div>';
                    return;
                }
                listEl.innerHTML = backups.map(b => `
                    <div class="backup-item">
                        <div class="backup-item-info">
                            <span class="backup-item-date">${formatBackupName(b.name)}</span>
                            <span class="backup-item-count">${b.user_count} users${b.term ? ' · ' + formatTermName(b.term) : ''}</span>
                        </div>
                        <div class="backup-item-actions">
                            <button type="button" class="btn-action btn-elevate backup-restore-btn" data-name="${b.name}">Restore</button>
                            <button type="button" class="btn-action btn-delete backup-delete-btn" data-name="${b.name}">Delete</button>
                        </div>
                    </div>
                `).join('');
                listEl.querySelectorAll('.backup-restore-btn').forEach(btn => {
                    btn.addEventListener('click', () => restoreBackup(btn.dataset.name, btn));
                });
                listEl.querySelectorAll('.backup-delete-btn').forEach(btn => {
                    btn.addEventListener('click', () => deleteBackup(btn.dataset.name, btn));
                });
            } catch(e) {
                listEl.innerHTML = '<div class="backups-loading">Failed to load backups.</div>';
            }
        }

        async function restoreBackup(name, btn) {
            if (!confirm('Restore from backup ' + formatBackupName(name) + '?\n\nAll non-root data will be wiped and replaced with this backup. The root account and password are preserved.')) return;
            btn.disabled = true;
            btn.textContent = 'Restoring…';
            try {
                const form = new FormData();
                form.append('backup_name', name);
                const res = await fetch('/admin/api/restore_backup', { method: 'POST', body: form });
                const data = await res.json();
                if (data.ok) {
                    window.location.href = '/admin/portal?message=Backup+restored+successfully';
                } else {
                    btn.disabled = false;
                    btn.textContent = 'Restore';
                    alert('Restore failed — no data found.');
                }
            } catch(e) {
                btn.disabled = false;
                btn.textContent = 'Restore';
            }
        }

        async function deleteBackup(name, btn) {
            if (!confirm('Delete backup from ' + formatBackupName(name) + '? This cannot be undone.')) return;
            btn.disabled = true;
            try {
                const res = await fetch('/admin/api/backup/' + name, { method: 'DELETE' });
                const data = await res.json();
                if (data.ok) await loadBackupsList();
            } catch(e) {
                btn.disabled = false;
            }
        }

        window.openBackupsPanel = function() {
            document.getElementById('backups-panel').classList.remove('hidden');
            document.getElementById('backups-backdrop').classList.remove('hidden');
            loadBackupsList();
        };

        window.closeBackupsPanel = function() {
            document.getElementById('backups-panel').classList.add('hidden');
            document.getElementById('backups-backdrop').classList.add('hidden');
        };

        // Intercept graduate toggle forms to avoid page reload
        // ===== Exam PDF import =====
        const examPdfInput = document.getElementById('exam-pdf-input');
        const examPreviewBtn = document.getElementById('exam-preview-btn');
        const examTypeSelect = document.getElementById('exam-type-select');

        function updatePreviewBtn() {
            examPreviewBtn.disabled = !examPdfInput.files.length || !examTypeSelect.value;
        }

        if (examPdfInput) {
            examPdfInput.addEventListener('change', updatePreviewBtn);
            examTypeSelect.addEventListener('change', function() {
                updatePreviewBtn();
                document.getElementById('finals-format-note').classList.toggle('hidden', this.value !== 'final');
            });
        }

        window.cancelExamPreview = function() {
            document.getElementById('exam-preview-area').classList.add('hidden');
            document.getElementById('exam-preview-error').classList.add('hidden');
            examPdfInput.value = '';
            updatePreviewBtn();
        };

        window.previewExamPdf = async function() {
            const pdfType = document.getElementById('exam-type-select').value;
            const file = examPdfInput.files[0];
            if (!file) return;

            const previewArea = document.getElementById('exam-preview-area');
            const previewError = document.getElementById('exam-preview-error');
            const previewBody = document.getElementById('exam-preview-body');
            const previewSummary = document.getElementById('exam-preview-summary');
            const importBtn = document.getElementById('exam-import-btn');

            examPreviewBtn.disabled = true;
            examPreviewBtn.textContent = 'Parsing...';
            previewArea.classList.add('hidden');
            previewError.classList.add('hidden');

            const formData = new FormData();
            formData.append('pdf_file', file);
            formData.append('exam_type', pdfType);

            try {
                const res = await fetch('/admin/api/preview_exam_pdf', { method: 'POST', body: formData });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `Server error ${res.status}`);
                }
                const data = await res.json();
                const entries = data.entries;

                // Render table
                previewBody.innerHTML = '';
                let importable = [];
                entries.forEach(function(e) {
                    const tr = document.createElement('tr');
                    const statusClass = e.duplicate ? 'badge-no-auth' : (e.found ? 'badge-admin' : 'badge-no-auth');
                    const statusText = e.duplicate ? 'Already exists' : (e.found ? 'Will import' : 'Not in DB');
                    tr.innerHTML =
                        '<td><strong>' + e.department + e.identifier + '</strong></td>' +
                        '<td>' + (e.title || '<em style="color:#999">Unknown</em>') + '</td>' +
                        '<td>' + e.date + '</td>' +
                        '<td><span class="badge ' + statusClass + '">' + statusText + '</span></td>';
                    if (!e.found || e.duplicate) tr.style.opacity = '0.5';
                    previewBody.appendChild(tr);
                    if (e.found && !e.duplicate) importable.push(e);
                });

                // Summary
                const notFoundEntries = entries.filter(e => !e.found);
                const notFound = notFoundEntries.length;
                const dupes = entries.filter(e => e.duplicate).length;
                let summaryParts = [`<strong>${entries.length}</strong> entries found in PDF`];
                if (notFound) summaryParts.push(`<a id="missing-courses-link" class="missing-courses-link">${notFound} course(s) not in DB</a>`);
                if (dupes) summaryParts.push(`<span style="color:#888">${dupes} already exist</span>`);
                previewSummary.innerHTML = summaryParts.join(' &nbsp;·&nbsp; ');
                const missingDetail = document.getElementById('missing-courses-detail');
                missingDetail.classList.add('hidden');
                if (notFound) {
                    missingDetail.innerHTML = notFoundEntries.map(e => `<span class="missing-course-badge">${e.department}${e.identifier}</span>`).join('');
                    document.getElementById('missing-courses-link').addEventListener('click', function() {
                        missingDetail.classList.toggle('hidden');
                    });
                }

                // Import button
                if (importable.length > 0) {
                    document.getElementById('exam-entries-json').value = JSON.stringify(
                        importable.map(e => ({ department: e.department, identifier: e.identifier, date: e.date }))
                    );
                    document.getElementById('exam-type-hidden').value = pdfType;
                    // Stash the PDF as base64 so it can be saved to disk only on confirm
                    const reader = new FileReader();
                    reader.onload = function(ev) {
                        document.getElementById('exam-pdf-b64').value = ev.target.result.split(',')[1] || '';
                    };
                    reader.readAsDataURL(file);
                    importBtn.textContent = 'Import ' + importable.length + ' exam' + (importable.length !== 1 ? 's' : '');
                    importBtn.disabled = false;
                } else {
                    document.getElementById('exam-pdf-b64').value = '';
                    importBtn.textContent = 'Nothing to import';
                    importBtn.disabled = true;
                }

                previewArea.classList.remove('hidden');
            } catch (err) {
                previewError.textContent = 'Error: ' + err.message;
                previewError.classList.remove('hidden');
            } finally {
                examPreviewBtn.disabled = false;
                examPreviewBtn.textContent = 'Preview';
            }
        };

        // ===== Course import =====
        window.previewCourses = async function() {
            const ct = _portalData.current_term;
            const previewError = document.getElementById('course-preview-error');
            if (!ct) {
                previewError.textContent = 'Cannot determine current term.';
                previewError.classList.remove('hidden');
                return;
            }
            if (_portalData.current_term_status.has_course_cache) {
                if (!confirm('Courses for this term are already on the server. Re-fetch from web and overwrite?')) {
                    return;
                }
            }
            const year = ct.academic_year;
            const season = ct.season;
            const previewArea = document.getElementById('course-preview-area');
            const previewBody = document.getElementById('course-preview-body');
            const previewSummary = document.getElementById('course-preview-summary');
            const importBtn = document.getElementById('course-import-btn');
            const previewBtn = document.getElementById('course-preview-btn');

            previewBtn.disabled = true;
            previewBtn.textContent = 'Fetching…';
            previewArea.classList.add('hidden');
            previewError.classList.add('hidden');

            const formData = new FormData();
            formData.append('academic_year', year);
            formData.append('season', season);

            try {
                const res = await fetch('/admin/api/preview_courses', { method: 'POST', body: formData });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Server error ' + res.status);
                }
                const data = await res.json();

                let parts = [
                    '<strong>' + data.total + '</strong> courses across <strong>' + Object.keys(data.departments).length + '</strong> depts',
                    '<span style="color:#27ae60">' + data.new + ' new</span>',
                ];
                if (data.update) parts.push('<span style="color:#f39c12">' + data.update + ' update' + (data.update !== 1 ? 's' : '') + '</span>');
                if (data.already_current) parts.push('<span style="color:#888">' + data.already_current + ' up to date</span>');
                if (data.errors.length) parts.push('<span style="color:#e74c3c">' + data.errors.length + ' dept(s) failed</span>');
                previewSummary.innerHTML = parts.join(' &nbsp;·&nbsp; ');

                previewBody.innerHTML = '';
                Object.entries(data.departments).sort().forEach(([dept, counts]) => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = '<td><strong>' + dept + '</strong></td>' +
                        '<td>' + counts.total + '</td>' +
                        '<td>' + (counts.new > 0 ? '<span style="color:#27ae60">+' + counts.new + '</span>' : '<span style="color:#aaa">—</span>') + '</td>' +
                        '<td>' + (counts.update > 0 ? '<span style="color:#f39c12">↑' + counts.update + '</span>' : '<span style="color:#aaa">—</span>') + '</td>';
                    previewBody.appendChild(tr);
                });

                document.getElementById('course-import-year').value = year;
                document.getElementById('course-import-season').value = season;
                const importable = data.new + data.update;
                if (importable > 0) {
                    const btnParts = [];
                    if (data.new > 0) btnParts.push(data.new + ' new');
                    if (data.update > 0) btnParts.push(data.update + ' update' + (data.update !== 1 ? 's' : ''));
                    importBtn.textContent = 'Import (' + btnParts.join(', ') + ')';
                    importBtn.disabled = false;
                } else {
                    importBtn.textContent = 'Nothing to import';
                    importBtn.disabled = true;
                }
                previewArea.classList.remove('hidden');

                // Expire the preview after 10 minutes to match server-side TTL
                if (window._coursePreviewTimer) clearTimeout(window._coursePreviewTimer);
                window._coursePreviewTimer = setTimeout(function() {
                    window._coursePreviewTimer = null;
                    document.getElementById('course-preview-area').classList.add('hidden');
                    const err = document.getElementById('course-preview-error');
                    err.textContent = 'Preview expired after 10 minutes. Please fetch again.';
                    err.classList.remove('hidden');
                }, 10 * 60 * 1000);
            } catch (err) {
                previewError.textContent = 'Error: ' + err.message;
                previewError.classList.remove('hidden');
            } finally {
                previewBtn.disabled = false;
                previewBtn.textContent = 'Fetch & Preview';
            }
        };

        window.cancelCoursePreview = function() {
            if (window._coursePreviewTimer) {
                clearTimeout(window._coursePreviewTimer);
                window._coursePreviewTimer = null;
            }
            document.getElementById('course-preview-area').classList.add('hidden');
            document.getElementById('course-preview-error').classList.add('hidden');
        };

        // Intercept graduate toggle forms to avoid page reload
        document.querySelectorAll('form[action="/admin/set_graduated"]').forEach(function(form) {
            form.addEventListener('submit', function(e) {
                e.preventDefault();
                const row = form.closest('tr');
                const graduatedInput = form.querySelector('input[name="graduated"]');
                const btn = form.querySelector('button');
                const willBeGraduated = graduatedInput.value === 'true';

                btn.disabled = true;
                fetch('/admin/set_graduated', { method: 'POST', body: new FormData(form) })
                    .then(function(res) {
                        if (res.ok || res.redirected) {
                            row.setAttribute('data-graduated', willBeGraduated ? 'true' : 'false');
                            btn.textContent = willBeGraduated ? 'Unmark Graduate' : 'Mark Graduate';
                            btn.className = 'btn-action btn-graduate';
                            graduatedInput.value = willBeGraduated ? 'false' : 'true';
                            if (willBeGraduated && !showingGraduates) {
                                showingGraduates = true;
                                document.getElementById('toggle-graduates-btn').textContent = 'Hide Graduates';
                            }
                            applyGraduateFilter();
                        }
                        btn.disabled = false;
                    })
                    .catch(function() { btn.disabled = false; });
            });
        });

        // ===== Exam Calendar =====
        (async function() {
            const container = document.getElementById('exam-calendar-container');
            if (!container) return;
            const MONTH_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            const DOW = ['Su','Mo','Tu','We','Th','Fr','Sa'];

            function renderMonth(year, month, byDate) {
                const wrap = document.createElement('div');
                wrap.className = 'exam-cal-month';

                const grid = document.createElement('div');
                grid.className = 'exam-cal-grid';

                for (const d of DOW) {
                    const h = document.createElement('div');
                    h.className = 'exam-cal-dow';
                    h.textContent = d;
                    grid.appendChild(h);
                }

                const firstDay = new Date(year, month - 1, 1).getDay();
                const daysInMonth = new Date(year, month, 0).getDate();

                for (let i = 0; i < firstDay; i++) {
                    const empty = document.createElement('div');
                    empty.className = 'exam-cal-day';
                    grid.appendChild(empty);
                }

                for (let d = 1; d <= daysInMonth; d++) {
                    const ds = year + '-' + String(month).padStart(2,'0') + '-' + String(d).padStart(2,'0');
                    const dayExams = byDate[ds] || [];
                    const cell = document.createElement('div');
                    cell.className = 'exam-cal-day' + (dayExams.length ? ' has-exams' : '');

                    const num = document.createElement('div');
                    num.className = 'exam-cal-day-num';
                    num.textContent = d;
                    cell.appendChild(num);

                    if (dayExams.length) {
                        const chips = document.createElement('div');
                        chips.className = 'exam-cal-chips';

                        // Finals → one collapsed chip with all courses in the tooltip
                        const finals = dayExams.filter(e => e.exam_type === 'final');
                        if (finals.length) {
                            const chip = document.createElement('span');
                            chip.className = 'exam-cal-chip exam-cal-chip-final';
                            chip.textContent = 'Finals';
                            chip.title = finals.map(e => e.department + e.identifier + (e.title ? ' — ' + e.title : '')).join('\n');
                            chips.appendChild(chip);
                        }

                        // Common hour → individual chips
                        for (const e of dayExams.filter(e => e.exam_type === 'common_hour')) {
                            const chip = document.createElement('span');
                            chip.className = 'exam-cal-chip exam-cal-chip-common_hour';
                            chip.textContent = e.department + e.identifier;
                            chip.title = (e.title || e.department + e.identifier) + ' — common hour';
                            chips.appendChild(chip);
                        }

                        // In-class tests → individual chips
                        for (const e of dayExams.filter(e => e.exam_type === 'in_class')) {
                            const chip = document.createElement('span');
                            chip.className = 'exam-cal-chip exam-cal-chip-in_class';
                            chip.textContent = e.department + e.identifier;
                            chip.title = (e.title || e.department + e.identifier) + ' — in-class test';
                            chips.appendChild(chip);
                        }

                        // Quizzes → individual chips
                        for (const e of dayExams.filter(e => e.exam_type === 'quiz')) {
                            const chip = document.createElement('span');
                            chip.className = 'exam-cal-chip exam-cal-chip-quiz';
                            chip.textContent = e.department + e.identifier;
                            chip.title = (e.title || e.department + e.identifier) + ' — quiz';
                            chips.appendChild(chip);
                        }

                        cell.appendChild(chips);
                    }

                    grid.appendChild(cell);
                }

                wrap.appendChild(grid);
                return wrap;
            }

            async function loadCalendar() {
            try {
                const res = await fetch('/admin/api/calendar_exams');
                if (!res.ok) throw new Error('Server error');
                const exams = await res.json();

                if (!exams.length) {
                    container.innerHTML = '<p style="color:#888;font-size:13px;">No exams scheduled yet.</p>';
                    return;
                }

                const byDate = {};
                for (const e of exams) {
                    byDate[e.date] = byDate[e.date] || [];
                    byDate[e.date].push(e);
                }

                const monthSet = new Set(Object.keys(byDate).map(d => d.slice(0,7)));
                const months = [...monthSet].sort();

                // Default to current month if it has exams, else nearest future, else last
                const todayYM = new Date().toISOString().slice(0, 7);
                const defaultMonth = months.find(m => m >= todayYM) || months[months.length - 1];

                container.innerHTML = '';

                // Header: nav buttons + month selector + today + legend
                const header = document.createElement('div');
                header.className = 'exam-cal-header';

                // Left side: prev / select / next / today
                const nav = document.createElement('div');
                nav.className = 'exam-cal-nav';

                const prevBtn = document.createElement('button');
                prevBtn.type = 'button';
                prevBtn.className = 'exam-cal-nav-btn exam-cal-arrow-btn';
                prevBtn.textContent = '‹';
                nav.appendChild(prevBtn);

                const sel = document.createElement('select');
                sel.className = 'exam-cal-month-select';
                for (const ym of months) {
                    const [y, mo] = ym.split('-').map(Number);
                    const opt = document.createElement('option');
                    opt.value = ym;
                    opt.textContent = MONTH_ABBR[mo - 1] + ' ' + y;
                    if (ym === defaultMonth) opt.selected = true;
                    sel.appendChild(opt);
                }
                nav.appendChild(sel);

                const nextBtn = document.createElement('button');
                nextBtn.type = 'button';
                nextBtn.className = 'exam-cal-nav-btn exam-cal-arrow-btn';
                nextBtn.textContent = '›';
                nav.appendChild(nextBtn);

                const todayBtn = document.createElement('button');
                todayBtn.type = 'button';
                todayBtn.className = 'exam-cal-nav-btn exam-cal-today-btn';
                todayBtn.textContent = 'Today';
                nav.appendChild(todayBtn);

                header.appendChild(nav);

                // Right side: legend
                const legend = document.createElement('div');
                legend.className = 'exam-cal-legend';
                legend.innerHTML =
                    '<span class="exam-cal-chip exam-cal-chip-in_class">In-Class</span>' +
                    '<span class="exam-cal-chip exam-cal-chip-quiz">Quiz</span>' +
                    '<span class="exam-cal-chip exam-cal-chip-common_hour">Common Hour</span>' +
                    '<span class="exam-cal-chip exam-cal-chip-final">Final</span>';
                header.appendChild(legend);
                container.appendChild(header);

                // Month view area
                const view = document.createElement('div');
                container.appendChild(view);

                function showMonth(ym) {
                    const [y, mo] = ym.split('-').map(Number);
                    view.innerHTML = '';
                    view.appendChild(renderMonth(y, mo, byDate));
                    sel.value = ym;
                    prevBtn.disabled = months.indexOf(ym) === 0;
                    nextBtn.disabled = months.indexOf(ym) === months.length - 1;
                }

                sel.addEventListener('change', function() { showMonth(this.value); });
                prevBtn.addEventListener('click', function() {
                    const idx = months.indexOf(sel.value);
                    if (idx > 0) showMonth(months[idx - 1]);
                });
                nextBtn.addEventListener('click', function() {
                    const idx = months.indexOf(sel.value);
                    if (idx < months.length - 1) showMonth(months[idx + 1]);
                });
                todayBtn.addEventListener('click', function() {
                    showMonth(defaultMonth);
                });
                showMonth(defaultMonth);
            } catch (e) {
                container.innerHTML = '<p style="color:#e74c3c;font-size:13px;">Failed to load exam schedule.</p>';
            }
            }

            loadCalendar();
            document.addEventListener('exams-changed', loadCalendar);
        })();

        // ===== Upcoming Exams list =====
        (function() {
            const container = document.getElementById('upcoming-exams-container');
            if (!container) return;
            function examTypeBadge(type) {
                if (type === 'final') return { cls: 'assessment-type-final', label: 'Final' };
                if (type === 'common_hour') return { cls: 'assessment-type-common', label: 'Common Hour' };
                if (type === 'quiz') return { cls: 'assessment-type-quiz', label: 'Quiz' };
                return { cls: 'assessment-type-test', label: 'In-Class' };
            }

            async function deleteExam(examId) {
                if (!confirm('Delete this exam? This cannot be undone.')) return;
                try {
                    var res = await fetch('/admin/api/exam/' + examId, { method: 'DELETE' });
                    if (!res.ok) console.warn('Delete exam failed:', res.status);
                    document.dispatchEvent(new CustomEvent('exams-changed'));
                } catch (e) { console.warn('Failed to delete exam:', e); }
            }

            async function loadUpcoming() {
                try {
                    const res = await fetch('/admin/api/calendar_exams');
                    if (!res.ok) throw new Error('Server error');
                    const exams = await res.json();

                    const today = new Date().toISOString().slice(0, 10);
                    const upcoming = exams.filter(e => e.date >= today)
                        .sort((a, b) => a.date.localeCompare(b.date) || a.department.localeCompare(b.department));

                    if (!upcoming.length) {
                        container.innerHTML = '<p style="color:#888;font-size:13px;">No upcoming exams.</p>';
                        return;
                    }

                    const list = document.createElement('div');
                    list.className = 'upcoming-exams-list';

                    let lastDate = null;
                    for (const e of upcoming) {
                        if (e.date !== lastDate) {
                            const dateLabel = document.createElement('div');
                            dateLabel.className = 'upcoming-exams-date';
                            dateLabel.textContent = formatDate(e.date);
                            list.appendChild(dateLabel);
                            lastDate = e.date;
                        }
                        const item = document.createElement('div');
                        item.className = 'upcoming-exam-item';
                        const badge = examTypeBadge(e.exam_type);
                        var linkCount = (typeof getLinkCount === 'function') ? getLinkCount(e.course_id) : 0;
                        var linkIndicator = linkCount > 0
                            ? '<span class="course-link-indicator" title="' + linkCount + ' linked course(s)">\uD83D\uDD17</span>'
                            : '';
                        item.innerHTML =
                            '<span class="assessment-type-badge ' + badge.cls + '">' + badge.label + '</span>' +
                            '<span class="upcoming-exam-course">' + e.department + e.identifier + linkIndicator + '</span>' +
                            '<span class="upcoming-exam-title">' + (e.title || '') + '</span>' +
                            '<button type="button" class="btn-action upcoming-exam-link" data-course-id="' + e.course_id +
                                '" data-name="' + e.department + e.identifier +
                                '" style="padding:2px 8px;font-size:11px;margin-left:auto;">Link</button>' +
                            '<button type="button" class="btn-action btn-elevate upcoming-exam-schedule" data-id="' + e.exam_id + '" style="padding:2px 8px;font-size:11px;">Schedule</button>' +
                            '<button type="button" class="user-course-remove upcoming-exam-delete" data-id="' + e.exam_id + '">\u2715</button>';
                        list.appendChild(item);
                    }
                    container.innerHTML = '';
                    container.appendChild(list);

                    list.querySelectorAll('.upcoming-exam-link').forEach(function(btn) {
                        btn.addEventListener('click', function() {
                            openLinkModal(parseInt(btn.dataset.courseId, 10), btn.dataset.name);
                        });
                    });
                    list.querySelectorAll('.upcoming-exam-schedule').forEach(function(btn) {
                        btn.addEventListener('click', function() { openScheduleModal(parseInt(btn.dataset.id, 10)); });
                    });
                    list.querySelectorAll('.upcoming-exam-delete').forEach(function(btn) {
                        btn.addEventListener('click', function() { deleteExam(parseInt(btn.dataset.id, 10)); });
                    });
                } catch (e) {
                    container.innerHTML = '<p style="color:#e74c3c;font-size:13px;">Failed to load exams.</p>';
                }
            }

            loadUpcoming();
            document.addEventListener('exams-changed', loadUpcoming);
            document.addEventListener('links-changed', loadUpcoming);
        })();

        // ===== Edit User Modal =====
        function refreshRowAvatar(studentId, discordId) {
            if (!discordId) return;
            const row = document.querySelector('#user-table-body tr[data-student-id="' + studentId + '"]');
            if (!row) return;
            const avatarEl = row.querySelector('.user-table-avatar');
            if (!avatarEl) return;
            fetch('/api/avatar/fetch/' + studentId, { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (!data.ok) return;
                    let img = avatarEl.querySelector('img');
                    if (!img) {
                        img = document.createElement('img');
                        img.alt = '';
                        avatarEl.insertBefore(img, avatarEl.firstChild);
                    }
                    img.src = '/avatar/' + studentId + '?t=' + Date.now();
                    img.style.display = 'block';
                    const initials = avatarEl.querySelector('.user-table-avatar-initials');
                    if (initials) initials.style.display = 'none';
                })
                .catch(() => {});
        }

        // ===== Edit User: enrollment & tutor management =====
        var _editAllCourses = [];
        var _editCourseFuse = null;
        fetch('/api/courses').then(function(r) { return r.json(); }).then(function(c) {
            _editAllCourses = c;
            _editCourseFuse = new Fuse(c, {
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
        });

        var _editSelectedTutorCourse = null;

        function _editEsc(s) {
            var d = document.createElement('div');
            d.textContent = s;
            return d.innerHTML;
        }

        function _editCourseSearch(q, exclude) {
            if (!_editCourseFuse) return [];
            return _editCourseFuse.search(q).map(function(r) { return r.item; }).filter(function(c) {
                return exclude.indexOf(c.course_id) < 0;
            }).slice(0, 8);
        }

        async function _editLoadEnrollments(studentId) {
            var el = document.getElementById('edit-user-enrollments');
            try {
                var res = await fetch('/admin/api/user/' + studentId + '/enrollments');
                var courses = await res.json();
                if (!courses.length) {
                    el.innerHTML = '<div class="user-course-empty">No enrollments.</div>';
                    return;
                }
                el.innerHTML = courses.map(function(c) {
                    return '<div class="user-course-item" data-course-id="' + c.course_id + '">' +
                        '<span class="user-course-code">' + _editEsc(c.department) + ' ' + _editEsc(c.identifier) + '</span>' +
                        '<span class="user-course-title">' + _editEsc(c.title || '') + '</span>' +
                        '<button type="button" class="user-course-remove edit-enroll-remove" data-id="' + c.course_id + '">\u2715</button>' +
                    '</div>';
                }).join('');
                el.querySelectorAll('.edit-enroll-remove').forEach(function(btn) {
                    btn.addEventListener('click', async function() {
                        await fetch('/admin/api/user/' + studentId + '/enrollments/' + btn.dataset.id, { method: 'DELETE' });
                        await _editLoadEnrollments(studentId);
                    });
                });
            } catch(e) { el.innerHTML = '<div class="user-course-empty">Failed to load.</div>'; }
        }

        async function _editLoadTutors(studentId) {
            var el = document.getElementById('edit-user-tutors');
            try {
                var res = await fetch('/admin/api/user/' + studentId + '/tutor_capabilities');
                var caps = await res.json();
                if (!caps.length) {
                    el.innerHTML = '<div class="user-course-empty">No tutor capabilities.</div>';
                    return;
                }
                el.innerHTML = caps.map(function(c) {
                    var style = c.confidence >= 8 ? 'background:#dcfce7;color:#166534'
                              : c.confidence >= 5 ? 'background:#fef9c3;color:#854d0e'
                              : 'background:#fee2e2;color:#991b1b';
                    return '<div class="user-course-item" data-course-id="' + c.course_id + '">' +
                        '<span class="user-course-code">' + _editEsc(c.department) + ' ' + _editEsc(c.identifier) + '</span>' +
                        '<span class="user-course-title">' + _editEsc(c.title || '') + '</span>' +
                        '<span class="confidence-badge" style="' + style + '" title="Confidence: ' + c.confidence + '/10">' + c.confidence + '</span>' +
                        '<button type="button" class="user-course-remove edit-tutor-remove" data-id="' + c.course_id + '">\u2715</button>' +
                    '</div>';
                }).join('');
                el.querySelectorAll('.edit-tutor-remove').forEach(function(btn) {
                    btn.addEventListener('click', async function() {
                        await fetch('/admin/api/user/' + studentId + '/tutor_capabilities/' + btn.dataset.id, { method: 'DELETE' });
                        await _editLoadTutors(studentId);
                    });
                });
            } catch(e) { el.innerHTML = '<div class="user-course-empty">Failed to load.</div>'; }
        }

        // Enrollment search dropdown
        (function() {
            var input = document.getElementById('edit-enroll-search');
            var dropdown = document.getElementById('edit-enroll-dropdown');
            if (!input || !dropdown) return;

            input.addEventListener('input', function() {
                var q = input.value.trim();
                if (q.length < 2) { dropdown.classList.add('hidden'); return; }
                var enrolledEls = document.querySelectorAll('#edit-user-enrollments [data-course-id]');
                var exclude = [];
                enrolledEls.forEach(function(el) { exclude.push(parseInt(el.dataset.courseId, 10)); });
                var matches = _editCourseSearch(q, exclude);
                if (!matches.length) { dropdown.classList.add('hidden'); return; }
                dropdown.innerHTML = matches.map(function(c) {
                    return '<div class="course-search-item" data-id="' + c.course_id + '">' +
                        '<strong>' + _editEsc(c.combined || c.department + c.identifier) + '</strong> ' +
                        _editEsc(c.title || '') + '</div>';
                }).join('');
                dropdown.classList.remove('hidden');
                dropdown.querySelectorAll('.course-search-item').forEach(function(el) {
                    el.addEventListener('click', async function() {
                        dropdown.classList.add('hidden');
                        input.value = '';
                        var sid = document.getElementById('edit-user-id').value;
                        var form = new FormData();
                        form.append('course_id', el.dataset.id);
                        await fetch('/admin/api/user/' + sid + '/enrollments', { method: 'POST', body: form });
                        await _editLoadEnrollments(sid);
                    });
                });
            });
            document.addEventListener('click', function(e) {
                if (!input.contains(e.target) && !dropdown.contains(e.target)) {
                    dropdown.classList.add('hidden');
                }
            });
        })();

        // Tutor search dropdown
        (function() {
            var input = document.getElementById('edit-tutor-search');
            var dropdown = document.getElementById('edit-tutor-dropdown');
            var addBtn = document.getElementById('edit-tutor-add-btn');
            if (!input || !dropdown || !addBtn) return;

            input.addEventListener('input', function() {
                var q = input.value.trim();
                _editSelectedTutorCourse = null;
                addBtn.disabled = true;
                if (q.length < 2) { dropdown.classList.add('hidden'); return; }
                var tutorEls = document.querySelectorAll('#edit-user-tutors [data-course-id]');
                var exclude = [];
                tutorEls.forEach(function(el) { exclude.push(parseInt(el.dataset.courseId, 10)); });
                var matches = _editCourseSearch(q, exclude);
                if (!matches.length) { dropdown.classList.add('hidden'); return; }
                dropdown.innerHTML = matches.map(function(c) {
                    return '<div class="course-search-item" data-id="' + c.course_id + '">' +
                        '<strong>' + _editEsc(c.combined || c.department + c.identifier) + '</strong> ' +
                        _editEsc(c.title || '') + '</div>';
                }).join('');
                dropdown.classList.remove('hidden');
                dropdown.querySelectorAll('.course-search-item').forEach(function(el) {
                    el.addEventListener('click', function() {
                        var id = parseInt(el.dataset.id, 10);
                        _editSelectedTutorCourse = _editAllCourses.find(function(c) { return c.course_id === id; });
                        input.value = _editSelectedTutorCourse ? (_editSelectedTutorCourse.combined || _editSelectedTutorCourse.department + _editSelectedTutorCourse.identifier) : '';
                        dropdown.classList.add('hidden');
                        addBtn.disabled = !_editSelectedTutorCourse;
                    });
                });
            });
            document.addEventListener('click', function(e) {
                if (!input.contains(e.target) && !dropdown.contains(e.target)) {
                    dropdown.classList.add('hidden');
                }
            });

            addBtn.addEventListener('click', async function() {
                if (!_editSelectedTutorCourse) return;
                var sid = document.getElementById('edit-user-id').value;
                var conf = parseInt(document.getElementById('edit-tutor-confidence').value, 10);
                if (isNaN(conf) || conf < 1 || conf > 10) {
                    document.getElementById('edit-tutor-confidence').focus();
                    return;
                }
                addBtn.disabled = true;
                var form = new FormData();
                form.append('course_id', _editSelectedTutorCourse.course_id);
                form.append('confidence', conf);
                await fetch('/admin/api/user/' + sid + '/tutor_capabilities', { method: 'POST', body: form });
                _editSelectedTutorCourse = null;
                input.value = '';
                await _editLoadTutors(sid);
            });
        })();

        window.openEditModal = function(btn) {
            var studentId = btn.dataset.studentId;
            document.getElementById('edit-user-id').value = studentId;
            document.getElementById('edit-first-name').value = btn.dataset.firstName;
            document.getElementById('edit-last-name').value = btn.dataset.lastName;
            document.getElementById('edit-discord-id').value = btn.dataset.discordId || '';
            document.getElementById('edit-user-error').classList.add('hidden');
            document.getElementById('edit-enroll-search').value = '';
            document.getElementById('edit-tutor-search').value = '';
            _editSelectedTutorCourse = null;
            document.getElementById('edit-tutor-add-btn').disabled = true;
            document.getElementById('edit-user-panel').classList.remove('hidden');
            document.getElementById('edit-user-backdrop').classList.remove('hidden');
            refreshRowAvatar(studentId, btn.dataset.discordId);
            _editLoadEnrollments(studentId);
            _editLoadTutors(studentId);
        };

        window.closeEditModal = function() {
            document.getElementById('edit-user-panel').classList.add('hidden');
            document.getElementById('edit-user-backdrop').classList.add('hidden');
        };

        window.saveEditUser = async function() {
            const studentId = document.getElementById('edit-user-id').value;
            const firstName = document.getElementById('edit-first-name').value.trim();
            const lastName = document.getElementById('edit-last-name').value.trim();
            const discordId = document.getElementById('edit-discord-id').value.trim();
            const errorEl = document.getElementById('edit-user-error');

            if (!firstName || !lastName) {
                errorEl.textContent = 'First and last name are required.';
                errorEl.classList.remove('hidden');
                return;
            }
            if (discordId && !/^\d+$/.test(discordId)) {
                errorEl.textContent = 'Discord ID must be numeric.';
                errorEl.classList.remove('hidden');
                return;
            }

            const saveBtn = document.getElementById('edit-user-save-btn');
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving…';
            errorEl.classList.add('hidden');

            try {
                const formData = new FormData();
                formData.append('target_id', studentId);
                formData.append('first_name', firstName);
                formData.append('last_name', lastName);
                formData.append('discord_id', discordId);

                const res = await fetch('/admin/api/edit_user', { method: 'POST', body: formData });
                const data = await res.json();

                if (!res.ok) {
                    errorEl.textContent = data.detail || 'Error saving changes.';
                    errorEl.classList.remove('hidden');
                    return;
                }

                // Update the name cell in the table row
                const row = document.querySelector('#user-table-body tr[data-student-id="' + studentId + '"]');
                if (row) {
                    const nameCell = row.querySelector('.user-table-name-cell');
                    if (nameCell) {
                        // Update only the text node, preserving the avatar element
                        const textNodes = [...nameCell.childNodes].filter(n => n.nodeType === Node.TEXT_NODE);
                        textNodes.forEach(n => n.remove());
                        nameCell.append(' ' + firstName + ' ' + lastName);
                    }
                    // Update the edit button's data attributes
                    const editBtn = row.querySelector('.btn-edit');
                    if (editBtn) {
                        editBtn.dataset.firstName = firstName;
                        editBtn.dataset.lastName = lastName;
                        editBtn.dataset.discordId = discordId;
                    }
                    // If discord ID changed, refresh the avatar
                    refreshRowAvatar(studentId, discordId);
                }
                closeEditModal();
            } catch (err) {
                errorEl.textContent = 'Network error. Please try again.';
                errorEl.classList.remove('hidden');
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Changes';
            }
        };

        // ===== Pending Assessment Reports =====
        (async function() {
            var container = document.getElementById('pending-assessments-container');
            if (!container) return;

            async function loadPending() {
                try {
                    var res = await fetch('/admin/api/pending_assessments');
                    if (!res.ok) throw new Error('Server error');
                    var items = await res.json();

                    if (!items.length) {
                        container.innerHTML = '<p style="color:#888;font-size:13px;">No items to review.</p>';
                        return;
                    }

                    container.innerHTML = items.map(function(a) {
                        var typeLabel = a.exam_type === 'quiz' ? 'Quiz' : a.exam_type === 'final' ? 'Final' : a.exam_type === 'common_hour' ? 'Common Hour' : 'In-Class Test';
                        if (a.review_type === 'needs_session') {
                            var needsTypeCls = a.exam_type === 'quiz' ? 'assessment-type-quiz' : a.exam_type === 'final' ? 'assessment-type-final' : a.exam_type === 'common_hour' ? 'assessment-type-common' : 'assessment-type-test';
                            var alsoCovers = (a.also_covers && a.also_covers.length)
                                ? ' <span style="color:#888;font-size:11px;">(+ ' + a.also_covers.join(', ') + ')</span>'
                                : '';
                            return '<div class="backup-item">' +
                                '<div class="backup-item-info">' +
                                    '<span class="backup-item-date">' +
                                        '<span class="assessment-type-badge ' + needsTypeCls + '">Needs Session</span> ' +
                                        a.department + ' ' + a.identifier + alsoCovers + ' \u2014 ' + typeLabel + '</span>' +
                                    '<span class="backup-item-count">Exam on ' + formatDate(a.test_date) + '</span>' +
                                '</div>' +
                                '<div class="backup-item-actions">' +
                                    '<button type="button" class="btn-action btn-elevate needs-session-btn" data-id="' + a.exam_id + '">Schedule</button>' +
                                    '<button type="button" class="btn-action skip-exam-btn" data-id="' + a.exam_id + '">Skip</button>' +
                                '</div>' +
                            '</div>';
                        }
                        var reporter = ((a.reporter_first || '') + ' ' + (a.reporter_last || '')).trim() || 'Unknown';
                        var reviewBadge, confirmLabel, revertLabel, detailText;
                        if (a.review_type === 'disputed') {
                            reviewBadge = '<span class="assessment-type-badge assessment-type-final">Disputed Final</span>';
                            confirmLabel = 'Delete Final';
                            revertLabel = 'Restore';
                            detailText = formatDate(a.test_date);
                        } else {
                            reviewBadge = '<span class="assessment-type-badge assessment-type-test">New Report</span>';
                            confirmLabel = 'Confirm';
                            revertLabel = 'Reject';
                            detailText = formatDate(a.test_date) + ' \u00b7 Reported by ' + reporter;
                        }
                        return '<div class="backup-item">' +
                            '<div class="backup-item-info">' +
                                '<span class="backup-item-date">' + reviewBadge + ' ' +
                                    a.department + ' ' + a.identifier + ' \u2014 ' + typeLabel + '</span>' +
                                '<span class="backup-item-count">' + detailText + '</span>' +
                            '</div>' +
                            '<div class="backup-item-actions">' +
                                '<button type="button" class="btn-action btn-elevate pending-confirm-btn" data-id="' + a.exam_id + '">' + confirmLabel + '</button>' +
                                '<button type="button" class="btn-action btn-delete pending-revert-btn" data-id="' + a.exam_id + '">' + revertLabel + '</button>' +
                            '</div>' +
                        '</div>';
                    }).join('');

                    container.querySelectorAll('.needs-session-btn').forEach(function(btn) {
                        btn.addEventListener('click', function() { openScheduleModal(parseInt(btn.dataset.id, 10)); });
                    });
                    container.querySelectorAll('.skip-exam-btn').forEach(function(btn) {
                        btn.addEventListener('click', function() { skipExam(parseInt(btn.dataset.id, 10), btn); });
                    });
                    container.querySelectorAll('.pending-confirm-btn').forEach(function(btn) {
                        btn.addEventListener('click', function() { confirmAssessment(parseInt(btn.dataset.id, 10), btn); });
                    });
                    container.querySelectorAll('.pending-revert-btn').forEach(function(btn) {
                        btn.addEventListener('click', function() { revertAssessment(parseInt(btn.dataset.id, 10), btn); });
                    });

                } catch (e) {
                    container.innerHTML = '<p style="color:#e74c3c;font-size:13px;">Failed to load pending reports.</p>';
                }
            }

            async function skipExam(examId, btn) {
                btn.disabled = true;
                try {
                    var form = new FormData();
                    form.append('exam_id', examId);
                    var res = await fetch('/admin/api/skip_exam', { method: 'POST', body: form });
                    if (!res.ok) console.warn('Skip exam failed:', res.status);
                    await loadPending();
                    document.dispatchEvent(new CustomEvent('exams-changed'));
                } catch (e) {
                    btn.disabled = false;
                }
            }

            async function confirmAssessment(examId, btn) {
                btn.disabled = true;
                try {
                    var form = new FormData();
                    form.append('exam_id', examId);
                    var res = await fetch('/admin/api/confirm_assessment', { method: 'POST', body: form });
                    if (!res.ok) console.warn('Confirm assessment failed:', res.status);
                    await loadPending();
                    document.dispatchEvent(new CustomEvent('exams-changed'));
                } catch (e) {
                    btn.disabled = false;
                }
            }

            async function revertAssessment(examId, btn) {
                btn.disabled = true;
                try {
                    var form = new FormData();
                    form.append('exam_id', examId);
                    var res = await fetch('/admin/api/revert_assessment', { method: 'POST', body: form });
                    if (!res.ok) console.warn('Revert assessment failed:', res.status);
                    await loadPending();
                    document.dispatchEvent(new CustomEvent('exams-changed'));
                } catch (e) {
                    btn.disabled = false;
                }
            }

            loadPending();
            document.addEventListener('exams-changed', loadPending);
            document.addEventListener('sessions-changed', loadPending);
        })();

        // ===== Course Link Modal =====
        (function() {
            var allCourses = [];
            var linkCourseFuse = null;
            var allLinks = [];
            var linkCountMap = {};
            var selectedNewCourse = null;
            var sourceCourseId = null;

            fetch('/api/courses').then(function(r) { return r.json(); }).then(function(c) {
                allCourses = c;
                linkCourseFuse = new Fuse(c, {
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
            });

            function escHtml(s) {
                var d = document.createElement('div');
                d.textContent = s;
                return d.innerHTML;
            }

            async function loadAllLinks() {
                try {
                    var res = await fetch('/admin/api/course_links');
                    if (!res.ok) throw new Error();
                    allLinks = await res.json();
                    linkCountMap = {};
                    allLinks.forEach(function(l) {
                        linkCountMap[l.course_id_a] = (linkCountMap[l.course_id_a] || 0) + 1;
                        linkCountMap[l.course_id_b] = (linkCountMap[l.course_id_b] || 0) + 1;
                    });
                    document.dispatchEvent(new CustomEvent('links-changed'));
                } catch(e) { allLinks = []; linkCountMap = {}; }
            }

            window.getLinkCount = function(courseId) { return linkCountMap[courseId] || 0; };

            window.openLinkModal = async function(courseId, courseName) {
                sourceCourseId = courseId;
                selectedNewCourse = null;
                document.getElementById('link-source-course-id').value = courseId;
                document.getElementById('link-source-info').textContent = courseName;
                document.getElementById('link-new-course').value = '';
                document.getElementById('link-add-btn').disabled = true;

                var panel = document.getElementById('link-course-panel');
                var backdrop = document.getElementById('link-course-backdrop');
                panel.classList.remove('hidden');
                backdrop.classList.remove('hidden');

                renderExistingLinks();
                loadSuggestions();
            };

            window.closeLinkModal = function() {
                document.getElementById('link-course-panel').classList.add('hidden');
                document.getElementById('link-course-backdrop').classList.add('hidden');
                sourceCourseId = null;
            };

            function renderExistingLinks() {
                var el = document.getElementById('link-existing-links');
                var links = allLinks.filter(function(l) {
                    return l.course_id_a === sourceCourseId || l.course_id_b === sourceCourseId;
                });
                if (!links.length) {
                    el.innerHTML = '<p style="color:#888;font-size:13px;">No links yet.</p>';
                    return;
                }
                el.innerHTML = links.map(function(l) {
                    var other = l.course_id_a === sourceCourseId
                        ? { id: l.course_id_b, dept: l.b_department, ident: l.b_identifier, title: l.b_title }
                        : { id: l.course_id_a, dept: l.a_department, ident: l.a_identifier, title: l.a_title };
                    var typeCls = l.link_type === 'strong' ? 'link-type-strong' : 'link-type-weak';
                    var typeLabel = l.link_type === 'strong' ? 'Strong' : 'Weak';
                    return '<div class="course-link-group">' +
                        '<span class="link-type-badge ' + typeCls + '">' + typeLabel + '</span> ' +
                        '<span class="course-link-chip">' + escHtml(other.dept + other.ident) +
                        (other.title ? ' \u2014 ' + escHtml(other.title) : '') + '</span>' +
                        '<button type="button" class="user-course-remove unlink-btn" ' +
                            'data-a="' + l.course_id_a + '" data-b="' + l.course_id_b + '">\u2715</button>' +
                    '</div>';
                }).join('');
                el.querySelectorAll('.unlink-btn').forEach(function(btn) {
                    btn.addEventListener('click', async function() {
                        await fetch('/admin/api/course_links/' + btn.dataset.a + '/' + btn.dataset.b, { method: 'DELETE' });
                        await loadAllLinks();
                        renderExistingLinks();
                        loadSuggestions();
                    });
                });
            }

            async function loadSuggestions() {
                var area = document.getElementById('link-suggestions-area');
                if (!area || !sourceCourseId) { if (area) area.innerHTML = ''; return; }
                try {
                    var res = await fetch('/admin/api/course_link_suggestions');
                    if (!res.ok) throw new Error();
                    var all = await res.json();
                    var relevant = all.filter(function(s) {
                        return s.a.course_id === sourceCourseId || s.b.course_id === sourceCourseId;
                    });
                    if (!relevant.length) { area.innerHTML = ''; return; }
                    area.innerHTML = '<label style="font-size:13px;color:#888;">Suggestions:</label>' +
                        relevant.map(function(s, idx) {
                            var other = s.a.course_id === sourceCourseId ? s.b : s.a;
                            var typeCls = s.link_type === 'strong' ? 'link-type-strong' : 'link-type-weak';
                            var typeLabel = s.link_type === 'strong' ? 'Strong' : 'Weak';
                            return '<div class="course-link-suggestion">' +
                                '<span class="course-link-chip">' + escHtml(other.department + other.identifier) + '</span>' +
                                ' <span style="color:#aaa;font-size:11px;">(' + Math.round(s.similarity * 100) + '%)</span>' +
                                ' <span class="link-type-badge ' + typeCls + '">' + typeLabel + '</span>' +
                                ' <button type="button" class="btn-action btn-elevate suggest-link-btn" data-idx="' + idx + '" style="padding:2px 8px;font-size:11px;">Link</button>' +
                            '</div>';
                        }).join('');
                    area.querySelectorAll('.suggest-link-btn').forEach(function(btn) {
                        var s = relevant[parseInt(btn.dataset.idx, 10)];
                        btn.addEventListener('click', async function() {
                            btn.disabled = true;
                            var form = new FormData();
                            form.append('course_id_a', s.a.course_id);
                            form.append('course_id_b', s.b.course_id);
                            form.append('link_type', s.link_type);
                            await fetch('/admin/api/course_links', { method: 'POST', body: form });
                            await loadAllLinks();
                            renderExistingLinks();
                            loadSuggestions();
                        });
                    });
                } catch(e) { area.innerHTML = ''; }
            }

            // Autocomplete for add-link input
            var input = document.getElementById('link-new-course');
            var dropdown = document.getElementById('link-new-dropdown');
            if (input && dropdown) {
                input.addEventListener('input', function() {
                    var q = input.value.trim();
                    if (q.length < 2 || !linkCourseFuse) { dropdown.classList.add('hidden'); return; }
                    var matches = linkCourseFuse.search(q).map(function(r) { return r.item; }).filter(function(c) {
                        return c.course_id !== sourceCourseId;
                    }).slice(0, 8);
                    if (!matches.length) { dropdown.classList.add('hidden'); return; }
                    dropdown.innerHTML = matches.map(function(c) {
                        return '<div class="course-search-item" data-id="' + c.course_id + '">' +
                            '<strong>' + escHtml(c.combined || c.department + c.identifier) + '</strong> ' +
                            escHtml(c.title || '') + '</div>';
                    }).join('');
                    dropdown.classList.remove('hidden');
                    dropdown.querySelectorAll('.course-search-item').forEach(function(el) {
                        el.addEventListener('click', function() {
                            var id = parseInt(el.dataset.id, 10);
                            selectedNewCourse = allCourses.find(function(c) { return c.course_id === id; });
                            input.value = selectedNewCourse ? (selectedNewCourse.combined || selectedNewCourse.department + selectedNewCourse.identifier) : '';
                            dropdown.classList.add('hidden');
                            document.getElementById('link-add-btn').disabled = !selectedNewCourse;
                        });
                    });
                });
                document.addEventListener('click', function(e) {
                    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
                        dropdown.classList.add('hidden');
                    }
                });
            }

            var addBtn = document.getElementById('link-add-btn');
            if (addBtn) {
                addBtn.addEventListener('click', async function() {
                    if (!selectedNewCourse || !sourceCourseId) return;
                    addBtn.disabled = true;
                    var form = new FormData();
                    form.append('course_id_a', sourceCourseId);
                    form.append('course_id_b', selectedNewCourse.course_id);
                    form.append('link_type', document.getElementById('link-type-select').value);
                    try {
                        var res = await fetch('/admin/api/course_links', { method: 'POST', body: form });
                        if (!res.ok) {
                            var data = await res.json();
                            alert(data.detail || 'Failed to link courses');
                        }
                    } catch(e) {}
                    selectedNewCourse = null;
                    input.value = '';
                    addBtn.disabled = true;
                    await loadAllLinks();
                    renderExistingLinks();
                    loadSuggestions();
                });
            }

            loadAllLinks();
        })();

        // ===== Restore & Review (unified: deleted/skipped exams + no-tutor approved) =====
        (function() {
            var container = document.getElementById('restore-exams-container');
            var filterBar = document.getElementById('restore-filter-bar');
            if (!container) return;
            var allExamItems = [];
            var allNoTutorItems = [];
            var activeFilter = 'all';

            function examTypeBadge(type) {
                if (type === 'final') return { cls: 'assessment-type-final', label: 'Final' };
                if (type === 'common_hour') return { cls: 'assessment-type-common', label: 'Common Hour' };
                if (type === 'quiz') return { cls: 'assessment-type-quiz', label: 'Quiz' };
                return { cls: 'assessment-type-test', label: 'In-Class' };
            }

            var expanded = false;
            var COLLAPSED_LIMIT = 4;

            function dedup(items) {
                var map = {};
                var order = [];
                items.forEach(function(a) {
                    var key = a.department + '|' + a.identifier + '|' + a.test_date + '|' + a.exam_type;
                    if (!map[key]) {
                        map[key] = { ids: [], department: a.department, identifier: a.identifier, title: a.title, test_date: a.test_date, exam_type: a.exam_type, is_skipped: a.is_skipped, is_deleted: a.is_deleted, is_disputed: a.is_disputed };
                        order.push(key);
                    }
                    map[key].ids.push(a.exam_id);
                });
                return order.map(function(k) { return map[k]; });
            }

            function getFilterCounts() {
                var counts = { all: 0, deleted: 0, skipped: 0, disputed: 0, no_tutor: allNoTutorItems.length };
                allExamItems.forEach(function(a) {
                    if (a.is_skipped) counts.skipped++;
                    else if (a.is_disputed) counts.disputed++;
                    else counts.deleted++;
                });
                counts.all = allExamItems.length + allNoTutorItems.length;
                return counts;
            }

            function renderFilterBar() {
                if (!filterBar) return;
                var counts = getFilterCounts();
                var filters = [
                    { key: 'all', label: 'All' },
                    { key: 'deleted', label: 'Deleted' },
                    { key: 'skipped', label: 'Skipped' },
                    { key: 'disputed', label: 'Disputed' },
                    { key: 'no_tutor', label: 'No Tutor' }
                ];
                filterBar.innerHTML = filters.filter(function(f) {
                    return f.key === 'all' || counts[f.key] > 0;
                }).map(function(f) {
                    return '<button type="button" class="restore-filter-chip' +
                        (activeFilter === f.key ? ' active' : '') +
                        '" data-filter="' + f.key + '">' +
                        f.label + (counts[f.key] > 0 ? ' <span class="restore-filter-count">' + counts[f.key] + '</span>' : '') +
                        '</button>';
                }).join('');
                filterBar.querySelectorAll('.restore-filter-chip').forEach(function(btn) {
                    btn.addEventListener('click', function() {
                        activeFilter = btn.dataset.filter;
                        expanded = false;
                        renderFilterBar();
                        var searchEl = container.querySelector('#restore-exams-search');
                        render(searchEl ? searchEl.value.trim() : '');
                    });
                });
            }

            function applyTypeFilter(examItems, noTutorItems) {
                if (activeFilter === 'all') return { exams: examItems, noTutor: noTutorItems };
                if (activeFilter === 'no_tutor') return { exams: [], noTutor: noTutorItems };
                var filtered = examItems.filter(function(a) {
                    if (activeFilter === 'deleted') return !a.is_skipped && !a.is_disputed;
                    if (activeFilter === 'skipped') return a.is_skipped;
                    if (activeFilter === 'disputed') return a.is_disputed;
                    return true;
                });
                return { exams: filtered, noTutor: [] };
            }

            function render(search) {
                var examItems = allExamItems;
                var noTutorItems = allNoTutorItems;
                if (search) {
                    var q = search.toLowerCase();
                    examItems = examItems.filter(function(a) {
                        return (a.department + ' ' + a.identifier).toLowerCase().indexOf(q) >= 0
                            || (a.title || '').toLowerCase().indexOf(q) >= 0;
                    });
                    noTutorItems = noTutorItems.filter(function(c) {
                        return (c.department + ' ' + c.identifier).toLowerCase().indexOf(q) >= 0
                            || (c.title || '').toLowerCase().indexOf(q) >= 0;
                    });
                }
                var filtered = applyTypeFilter(examItems, noTutorItems);
                var merged = dedup(filtered.exams);
                var totalCount = merged.length + filtered.noTutor.length;
                var searchHtml = '<div class="course-search-input-wrap" style="margin-bottom:10px;">' +
                    '<input type="text" id="restore-exams-search" placeholder="Search\u2026" autocomplete="off" value="' + (search || '') + '">' +
                    '</div>';

                if (!allExamItems.length && !allNoTutorItems.length) {
                    container.innerHTML = '<p style="color:#888;font-size:13px;">Nothing to review.</p>';
                    return;
                }
                if (!totalCount) {
                    container.innerHTML = searchHtml + '<p style="color:#888;font-size:13px;">No matches.</p>';
                    container.querySelector('#restore-exams-search').addEventListener('input', function() { render(this.value.trim()); });
                    return;
                }
                // Build combined list of HTML rows
                var rows = [];
                // No-tutor items first
                filtered.noTutor.forEach(function(c) {
                    rows.push('<div class="backup-item">' +
                        '<div class="backup-item-info"><span class="backup-item-date">' +
                            '<span class="assessment-type-badge assessment-type-quiz">No Tutor</span> ' +
                            c.department + ' ' + c.identifier + (c.title ? ' \u2014 ' + c.title : '') +
                        '</span></div>' +
                        '<div class="backup-item-actions">' +
                            '<button type="button" class="btn-action btn-elevate no-tutor-revoke-btn" data-id="' + c.course_id + '">Revoke</button>' +
                        '</div></div>');
                });
                // Exam items
                var visibleExams = (!expanded && !search && merged.length > COLLAPSED_LIMIT) ? merged.slice(0, COLLAPSED_LIMIT) : merged;
                var hiddenCount = merged.length - visibleExams.length;
                visibleExams.forEach(function(a) {
                    var badge = examTypeBadge(a.exam_type);
                    var statusBadge = a.is_skipped ? '<span class="badge badge-skipped">Skipped</span> '
                        : a.is_disputed ? '<span class="assessment-type-badge assessment-type-final">Disputed</span> ' : '';
                    var actionBtn = a.is_skipped
                        ? '<button type="button" class="btn-action btn-elevate unskip-exam-btn" data-ids="' + a.ids.join(',') + '">Unskip</button>'
                        : '<button type="button" class="btn-action btn-elevate restore-exam-btn" data-ids="' + a.ids.join(',') + '">Restore</button>';
                    rows.push('<div class="backup-item">' +
                        '<div class="backup-item-info">' +
                            '<span class="backup-item-date">' +
                                '<span class="assessment-type-badge ' + badge.cls + '">' + badge.label + '</span> ' +
                                statusBadge +
                                a.department + ' ' + a.identifier + (a.title ? ' \u2014 ' + a.title : '') +
                            '</span>' +
                            '<span class="backup-item-count">' + formatDate(a.test_date) + '</span>' +
                        '</div>' +
                        '<div class="backup-item-actions">' + actionBtn + '</div>' +
                    '</div>');
                });
                container.innerHTML = searchHtml + rows.join('') +
                    (hiddenCount > 0 ? '<button type="button" id="restore-show-all" class="btn-action" style="margin-top:8px;">Show all (' + merged.length + ')</button>' : '') +
                    (expanded && !search && merged.length > COLLAPSED_LIMIT ? '<button type="button" id="restore-show-less" class="btn-action" style="margin-top:8px;">Show less</button>' : '');
                container.querySelector('#restore-exams-search').addEventListener('input', function() { render(this.value.trim()); });
                container.querySelectorAll('.restore-exam-btn').forEach(function(btn) {
                    btn.addEventListener('click', function() { restoreExam(btn.dataset.ids.split(',').map(Number), btn); });
                });
                container.querySelectorAll('.unskip-exam-btn').forEach(function(btn) {
                    btn.addEventListener('click', function() { unskipExam(btn.dataset.ids.split(',').map(Number), btn); });
                });
                container.querySelectorAll('.no-tutor-revoke-btn').forEach(function(btn) {
                    btn.addEventListener('click', async function() {
                        btn.disabled = true;
                        try {
                            var form = new FormData();
                            form.append('course_id', btn.dataset.id);
                            var res = await fetch('/admin/api/toggle_no_tutor', { method: 'POST', body: form });
                            if (!res.ok) throw new Error('Failed');
                            await loadAll();
                            document.dispatchEvent(new CustomEvent('no-tutor-changed'));
                        } catch(e) { btn.disabled = false; }
                    });
                });
                var showAllBtn = container.querySelector('#restore-show-all');
                if (showAllBtn) showAllBtn.addEventListener('click', function() { expanded = true; render(search); });
                var showLessBtn = container.querySelector('#restore-show-less');
                if (showLessBtn) showLessBtn.addEventListener('click', function() { expanded = false; render(search); });
            }

            async function loadAll() {
                try {
                    var results = await Promise.all([
                        fetch('/admin/api/deleted_exams').then(function(r) { return r.ok ? r.json() : []; }),
                        fetch('/admin/api/no_tutor_approved').then(function(r) { return r.ok ? r.json() : []; })
                    ]);
                    allExamItems = results[0];
                    allNoTutorItems = results[1];
                    renderFilterBar();
                    var searchEl = container.querySelector('#restore-exams-search');
                    render(searchEl ? searchEl.value.trim() : '');
                } catch (e) {
                    container.innerHTML = '<p style="color:#e74c3c;font-size:13px;">Failed to load review items.</p>';
                }
            }

            async function restoreExam(examIds, btn) {
                btn.disabled = true;
                try {
                    for (var i = 0; i < examIds.length; i++) {
                        var form = new FormData();
                        form.append('exam_id', examIds[i]);
                        var res = await fetch('/admin/api/restore_exam', { method: 'POST', body: form });
                        if (!res.ok) console.warn('Restore exam failed:', res.status);
                    }
                    await loadAll();
                    document.dispatchEvent(new CustomEvent('exams-changed'));
                } catch (e) {
                    btn.disabled = false;
                }
            }

            async function unskipExam(examIds, btn) {
                btn.disabled = true;
                try {
                    for (var i = 0; i < examIds.length; i++) {
                        var form = new FormData();
                        form.append('exam_id', examIds[i]);
                        var res = await fetch('/admin/api/unskip_exam', { method: 'POST', body: form });
                        if (!res.ok) console.warn('Unskip exam failed:', res.status);
                    }
                    await loadAll();
                    document.dispatchEvent(new CustomEvent('exams-changed'));
                } catch (e) {
                    btn.disabled = false;
                }
            }

            loadAll();
            document.addEventListener('exams-changed', loadAll);
            document.addEventListener('no-tutor-changed', loadAll);
        })();

        // ===== No-Tutor-Needed Pending Reports =====
        (function() {
            var container = document.getElementById('no-tutor-pending-container');
            if (!container) return;

            function render(items) {
                if (!items.length) {
                    container.innerHTML = '';
                    return;
                }
                container.innerHTML =
                    '<div style="margin-bottom:14px;">' +
                    '<div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#888;margin-bottom:8px;">No-Tutor Reports</div>' +
                    items.map(function(c) {
                        return '<div class="backup-item" data-course-id="' + c.course_id + '">' +
                            '<div class="backup-item-info">' +
                                '<span class="backup-item-date">' +
                                    '<span class="assessment-pending-badge">Pending</span> ' +
                                    c.department + ' ' + c.identifier + (c.title ? ' \u2014 ' + c.title : '') +
                                '</span>' +
                            '</div>' +
                            '<div class="backup-item-actions" style="display:flex;gap:6px;">' +
                                '<button type="button" class="btn-action btn-elevate no-tutor-approve-btn" data-id="' + c.course_id + '">Approve</button>' +
                                '<button type="button" class="btn-action no-tutor-dismiss-btn" data-id="' + c.course_id + '">Dismiss</button>' +
                            '</div>' +
                        '</div>';
                    }).join('') +
                    '</div>';

                container.querySelectorAll('.no-tutor-approve-btn').forEach(function(btn) {
                    btn.addEventListener('click', function() { handleAction(btn, '/admin/api/approve_no_tutor'); });
                });
                container.querySelectorAll('.no-tutor-dismiss-btn').forEach(function(btn) {
                    btn.addEventListener('click', function() { handleAction(btn, '/admin/api/reject_no_tutor'); });
                });
            }

            async function handleAction(btn, url) {
                btn.disabled = true;
                try {
                    var form = new FormData();
                    form.append('course_id', btn.dataset.id);
                    var res = await fetch(url, { method: 'POST', body: form });
                    if (!res.ok) throw new Error('Failed');
                    loadPending();
                    document.dispatchEvent(new CustomEvent('no-tutor-changed'));
                } catch(e) {
                    btn.disabled = false;
                }
            }

            async function loadPending() {
                try {
                    var res = await fetch('/admin/api/no_tutor_pending');
                    if (!res.ok) throw new Error('Server error');
                    render(await res.json());
                } catch(e) {
                    container.innerHTML = '';
                }
            }

            loadPending();
            document.addEventListener('no-tutor-changed', loadPending);
        })();

        // ===== Study Session Ping Helpers =====
        function buildFallbackBody(tutorName, tutorDiscordId, datetime, location) {
            var dtObj = new Date(datetime);
            var timeStr = dtObj.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
            var dateStr = dtObj.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
            var tutorMention = tutorDiscordId ? '<@' + tutorDiscordId + '>' : tutorName;
            var text = 'Study session on **' + dateStr + '** at **' + timeStr + '** in **' + location + '**.';
            if (tutorName && tutorName !== 'TBD') {
                text += ' ' + tutorMention + ' will be there to tutor.';
            }
            return text;
        }

        function buildPingHeader(exam, students) {
            var courseName = exam.department + exam.identifier;
            var titlePart = exam.title ? ' (' + exam.title + ')' : '';
            var text = '# ' + courseName + titlePart + ' Study session\n';

            var withDiscord = students.filter(function(s) { return s.discord_id; });
            if (withDiscord.length) {
                text += withDiscord.map(function(s) { return '<@' + s.discord_id + '>'; }).join(' ') + '\n';
            }
            return text;
        }

        function buildPingFooter(students) {
            var withoutDiscord = students.filter(function(s) { return !s.discord_id; });
            if (withoutDiscord.length) {
                return '\n*Also notify (no Discord): ' + withoutDiscord.map(function(s) { return s.first_name + ' ' + s.last_name; }).join(', ') + '*\n';
            }
            return '';
        }

        // ===== Study Session Scheduling Modal =====
        (function() {
            var _scheduleData = null;

            window.openScheduleModal = async function(examId) {
                var panel = document.getElementById('schedule-session-panel');
                var backdrop = document.getElementById('schedule-session-backdrop');
                var errorEl = document.getElementById('schedule-error');
                var submitBtn = document.getElementById('schedule-submit-btn');

                errorEl.classList.add('hidden');
                submitBtn.disabled = false;
                submitBtn.textContent = 'Schedule';
                document.getElementById('schedule-session-id').value = '';
                document.getElementById('schedule-location').value = 'Study Room';
                document.getElementById('schedule-exam-id').value = examId;
                document.getElementById('schedule-exam-info').innerHTML = '<span style="color:#888;font-size:13px;">Loading\u2026</span>';
                document.getElementById('schedule-tutor-select').innerHTML = '<option value="" disabled selected>Loading\u2026</option>';
                document.getElementById('schedule-students-preview').innerHTML = '';
                document.getElementById('schedule-datetime').value = '';

                panel.classList.remove('hidden');
                backdrop.classList.remove('hidden');

                try {
                    var res = await fetch('/admin/api/exam/' + examId + '/scheduling_details');
                    if (!res.ok) throw new Error('Failed to load exam details');
                    var data = await res.json();
                    _scheduleData = data;

                    var exam = data.exam;
                    var typeLabel = exam.exam_type === 'final' ? 'Final' :
                                    exam.exam_type === 'common_hour' ? 'Common Hour' :
                                    exam.exam_type === 'quiz' ? 'Quiz' : 'In-Class Test';
                    var examInfoHtml =
                        '<strong>' + exam.department + exam.identifier + '</strong> ' + typeLabel +
                        (exam.title ? ' \u2014 ' + exam.title : '') +
                        '<br><span style="color:#888;font-size:13px;">Exam date: ' + formatDate(exam.test_date) + '</span>';
                    if (data.linked_courses && data.linked_courses.length) {
                        var strongLinks = data.linked_courses.filter(function(c) { return c.link_type === 'strong'; });
                        var weakLinks = data.linked_courses.filter(function(c) { return c.link_type === 'weak'; });
                        if (strongLinks.length) {
                            examInfoHtml += '<br><span style="font-size:12px;"><span class="link-type-badge link-type-strong">Strong</span> ' +
                                strongLinks.map(function(c) { return c.department + c.identifier; }).join(', ') + '</span>';
                        }
                        if (weakLinks.length) {
                            examInfoHtml += '<br><span style="font-size:12px;"><span class="link-type-badge link-type-weak">Weak</span> ' +
                                weakLinks.map(function(c) { return c.department + c.identifier; }).join(', ') + '</span>';
                        }
                    }
                    document.getElementById('schedule-exam-info').innerHTML = examInfoHtml;

                    if (data.has_session && data.existing_session_id) {
                        errorEl.innerHTML = 'A study session already exists for this exam. <a href="#" class="delete-existing-session" data-session-id="' +
                            data.existing_session_id + '" style="color:var(--danger-color,#c0392b);text-decoration:underline;cursor:pointer;">Delete it</a>';
                        errorEl.classList.remove('hidden');
                        errorEl.querySelector('.delete-existing-session').addEventListener('click', async function(ev) {
                            ev.preventDefault();
                            var sid = this.dataset.sessionId;
                            if (!confirm('Delete the existing session so you can create a new one?')) return;
                            var delRes = await fetch('/admin/api/study_sessions/' + sid, { method: 'DELETE' });
                            if (delRes.ok) {
                                errorEl.classList.add('hidden');
                                document.dispatchEvent(new CustomEvent('sessions-changed'));
                            } else {
                                errorEl.textContent = 'Failed to delete existing session.';
                            }
                        });
                    }

                    var tutorSelect = document.getElementById('schedule-tutor-select');
                    tutorSelect.innerHTML = '<option value="" selected>No tutor (open session)</option>';
                    for (var i = 0; i < data.tutors.length; i++) {
                        var t = data.tutors[i];
                        var opt = document.createElement('option');
                        opt.value = t.student_id;
                        var tutorLabel = t.first_name + ' ' + t.last_name + ' (confidence: ' + t.confidence + ')';
                        if (t.from_course) tutorLabel += ' [from ' + t.from_course + ']';
                        opt.textContent = tutorLabel;
                        tutorSelect.appendChild(opt);
                    }

                    // Default datetime to day before exam at 3:00 PM
                    var examDate = new Date(exam.test_date + 'T15:00');
                    examDate.setDate(examDate.getDate() - 1);
                    var dtLocal = examDate.getFullYear() + '-' +
                        String(examDate.getMonth() + 1).padStart(2, '0') + '-' +
                        String(examDate.getDate()).padStart(2, '0') + 'T15:00';
                    document.getElementById('schedule-datetime').value = dtLocal;

                    // Enrolled students preview
                    var studentsEl = document.getElementById('schedule-students-preview');
                    if (!data.students.length) {
                        studentsEl.innerHTML = '<span style="color:#888;font-size:13px;">No enrolled students for this course.</span>';
                    } else {
                        var withoutDiscord = data.students.filter(function(s) { return !s.discord_id; });
                        var html = '<label style="font-size:13px;">Enrolled students (' + data.students.length + ')</label>';
                        html += '<div style="font-size:12px;color:#666;max-height:120px;overflow-y:auto;margin-top:4px;">';
                        for (var j = 0; j < data.students.length; j++) {
                            var s = data.students[j];
                            var icon = s.discord_id ? '<span style="color:#27ae60;">\u2713</span>' : '<span style="color:#aaa;">\u2717</span>';
                            html += '<div>' + icon + ' ' + s.first_name + ' ' + s.last_name + '</div>';
                        }
                        html += '</div>';
                        if (withoutDiscord.length) {
                            html += '<div style="font-size:11px;color:#e67e22;margin-top:4px;">' + withoutDiscord.length + ' student(s) without Discord</div>';
                        }
                        studentsEl.innerHTML = html;
                    }
                } catch (err) {
                    errorEl.textContent = 'Error: ' + err.message;
                    errorEl.classList.remove('hidden');
                }
            };

            window.closeScheduleModal = function() {
                document.getElementById('schedule-session-panel').classList.add('hidden');
                document.getElementById('schedule-session-backdrop').classList.add('hidden');
                _scheduleData = null;
            };

            window.openEditSessionModal = async function(session) {
                await openScheduleModal(session.exam_id);
                document.getElementById('schedule-session-id').value = session.session_id;
                document.getElementById('schedule-submit-btn').textContent = 'Save';
                // Pre-fill tutor
                if (session.tutor_id) {
                    document.getElementById('schedule-tutor-select').value = session.tutor_id;
                }
                // Pre-fill datetime
                if (session.session_timestamp) {
                    var dt = new Date(session.session_timestamp);
                    var dtLocal = dt.getFullYear() + '-' +
                        String(dt.getMonth() + 1).padStart(2, '0') + '-' +
                        String(dt.getDate()).padStart(2, '0') + 'T' +
                        String(dt.getHours()).padStart(2, '0') + ':' +
                        String(dt.getMinutes()).padStart(2, '0');
                    document.getElementById('schedule-datetime').value = dtLocal;
                }
                // Pre-fill location
                if (session.location) {
                    document.getElementById('schedule-location').value = session.location;
                }
                // Hide "session already exists" warning for edits
                document.getElementById('schedule-error').classList.add('hidden');
            };

            document.getElementById('schedule-submit-btn').addEventListener('click', async function() {
                var examId = document.getElementById('schedule-exam-id').value;
                var tutorId = document.getElementById('schedule-tutor-select').value;
                var datetime = document.getElementById('schedule-datetime').value;
                var location = document.getElementById('schedule-location').value.trim() || 'Study Room';
                var errorEl = document.getElementById('schedule-error');
                var submitBtn = document.getElementById('schedule-submit-btn');

                if (!datetime) {
                    errorEl.textContent = 'Please select a date and time.';
                    errorEl.classList.remove('hidden');
                    return;
                }

                var sessionId = document.getElementById('schedule-session-id').value;
                var isEdit = !!sessionId;

                submitBtn.disabled = true;
                submitBtn.textContent = isEdit ? 'Saving\u2026' : 'Scheduling\u2026';
                errorEl.classList.add('hidden');

                try {
                    var form = new FormData();
                    if (tutorId) form.append('tutor_student_id', tutorId);
                    form.append('session_timestamp', datetime);
                    form.append('location', location);

                    var res;
                    if (isEdit) {
                        res = await fetch('/admin/api/study_sessions/' + sessionId, { method: 'PUT', body: form });
                    } else {
                        form.append('exam_id', examId);
                        res = await fetch('/admin/api/study_sessions', { method: 'POST', body: form });
                    }
                    var data = await res.json();

                    if (!res.ok) {
                        throw new Error(data.detail || (isEdit ? 'Failed to update session' : 'Failed to create session'));
                    }

                    closeScheduleModal();
                    document.dispatchEvent(new CustomEvent('sessions-changed'));
                    document.dispatchEvent(new CustomEvent('exams-changed'));
                } catch (err) {
                    errorEl.textContent = err.message;
                    errorEl.classList.remove('hidden');
                    submitBtn.disabled = false;
                    submitBtn.textContent = isEdit ? 'Save' : 'Schedule';
                }
            });

        })();

        // ===== Scheduled Sessions =====
        (function() {
            var container = document.getElementById('scheduled-sessions-container');
            if (!container) return;
            var sessionsList = [];

            function examTypeBadge(type) {
                if (type === 'final') return { cls: 'assessment-type-final', label: 'Final' };
                if (type === 'common_hour') return { cls: 'assessment-type-common', label: 'Common Hour' };
                if (type === 'quiz') return { cls: 'assessment-type-quiz', label: 'Quiz' };
                return { cls: 'assessment-type-test', label: 'In-Class' };
            }

            function formatTimestamp(iso) {
                var dt = new Date(iso);
                return formatDate(dt) + ' at ' + dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
            }

            function generatePingText(s) {
                var exam = { department: s.department, identifier: s.identifier, title: s.title };
                var students = s.students || [];
                var header = buildPingHeader(exam, students);
                var footer = buildPingFooter(students);
                var tutorName = s.tutor_first ? s.tutor_first + ' ' + s.tutor_last : 'TBD';
                var body = buildFallbackBody(tutorName, s.tutor_discord_id, s.session_timestamp, s.location);
                return (header + '\n' + body + '\n' + footer).trimEnd();
            }

            async function loadSessions() {
                try {
                    var res = await fetch('/admin/api/study_sessions');
                    if (!res.ok) throw new Error('Server error');
                    sessionsList = await res.json();

                    if (!sessionsList.length) {
                        container.innerHTML = '<p style="color:#888;font-size:13px;">No study sessions scheduled.</p>';
                        return;
                    }

                    container.innerHTML = sessionsList.map(function(s) {
                        var badge = examTypeBadge(s.exam_type);
                        return '<div class="backup-item">' +
                            '<div class="backup-item-info">' +
                                '<span class="backup-item-date">' +
                                    '<span class="assessment-type-badge ' + badge.cls + '">' + badge.label + '</span> ' +
                                    s.department + s.identifier + (s.title ? ' \u2014 ' + s.title : '') +
                                '</span>' +
                                '<span class="backup-item-count">' +
                                    'Tutor: ' + (s.tutor_first ? s.tutor_first + ' ' + s.tutor_last : 'TBD') +
                                    ' \u00b7 ' + formatTimestamp(s.session_timestamp) +
                                    ' \u00b7 ' + s.location +
                                '</span>' +
                            '</div>' +
                            '<div class="backup-item-actions">' +
                                '<button type="button" class="btn-action session-edit-btn" data-sid="' + s.session_id + '">Edit</button>' +
                                '<button type="button" class="btn-action btn-edit session-copy-btn" data-sid="' + s.session_id + '">Copy Ping</button>' +
                                '<button type="button" class="btn-action btn-delete session-delete-btn" data-sid="' + s.session_id + '">Delete</button>' +
                            '</div>' +
                        '</div>';
                    }).join('');

                    container.querySelectorAll('.session-edit-btn').forEach(function(btn) {
                        btn.addEventListener('click', function() {
                            var sid = parseInt(btn.dataset.sid, 10);
                            var session = null;
                            for (var i = 0; i < sessionsList.length; i++) {
                                if (sessionsList[i].session_id === sid) { session = sessionsList[i]; break; }
                            }
                            if (session) openEditSessionModal(session);
                        });
                    });
                    container.querySelectorAll('.session-copy-btn').forEach(function(btn) {
                        btn.addEventListener('click', function() {
                            var sid = parseInt(btn.dataset.sid, 10);
                            var session = null;
                            for (var i = 0; i < sessionsList.length; i++) {
                                if (sessionsList[i].session_id === sid) { session = sessionsList[i]; break; }
                            }
                            if (!session) return;
                            var text = generatePingText(session);
                            if (navigator.clipboard && navigator.clipboard.writeText) {
                                navigator.clipboard.writeText(text).then(function() {
                                    btn.textContent = 'Copied!';
                                    setTimeout(function() { btn.textContent = 'Copy Ping'; }, 2000);
                                }).catch(function() {
                                    copyFallback(text, btn);
                                });
                            } else {
                                copyFallback(text, btn);
                            }
                        });
                    });

                    container.querySelectorAll('.session-delete-btn').forEach(function(btn) {
                        btn.addEventListener('click', function() { deleteSession(parseInt(btn.dataset.sid, 10), btn); });
                    });
                } catch (e) {
                    container.innerHTML = '<p style="color:#e74c3c;font-size:13px;">Failed to load study sessions.</p>';
                }
            }

            function copyFallback(text, btn) {
                var ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.left = '-9999px';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                btn.textContent = 'Copied!';
                setTimeout(function() { btn.textContent = 'Copy Ping'; }, 2000);
            }

            async function deleteSession(sessionId, btn) {
                if (!confirm('Delete this study session?')) return;
                btn.disabled = true;
                try {
                    var res = await fetch('/admin/api/study_sessions/' + sessionId, { method: 'DELETE' });
                    if (!res.ok) console.warn('Delete session failed:', res.status);
                    await loadSessions();
                    document.dispatchEvent(new CustomEvent('sessions-changed'));
                } catch (e) {
                    btn.disabled = false;
                }
            }

            loadSessions();
            document.addEventListener('sessions-changed', loadSessions);
        })();
