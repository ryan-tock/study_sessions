const searchInput = document.getElementById('name-search');
const dropdown = document.getElementById('search-dropdown');
const userSelectSection = document.getElementById('user-select-section');
const selectedSection = document.getElementById('selected-section');

let fuse = null;
let loadingUsers = false;
const _avatarStatus = new Map(); // student_id -> 'loaded' | 'error'

async function ensureUsersLoaded() {
    if (fuse !== null) return;
    if (loadingUsers) {
        await new Promise(resolve => {
            const check = setInterval(() => {
                if (fuse !== null) { clearInterval(check); resolve(); }
            }, 30);
        });
        return;
    }
    loadingUsers = true;
    try {
        const res = await fetch('/api/users/all');
        const users = await res.json();
        fuse = new Fuse(users, {
            keys: [{ name: 'first_name', weight: 1 }, { name: 'last_name', weight: 1 }],
            threshold: 0.4,
            includeScore: true,
            useExtendedSearch: false,
            ignoreLocation: true,
        });
    } catch(e) {
        fuse = new Fuse([]);
    }
}

function filterUsers(query) {
    const q = query.trim();
    if (!q || !fuse) return [];
    return fuse.search(q, { limit: 8 }).map(r => r.item);
}

searchInput.addEventListener('input', async function () {
    const q = this.value.trim();
    if (q.length === 0) { hideDropdown(); return; }
    await ensureUsersLoaded();
    const currentQ = searchInput.value.trim();
    if (currentQ.length === 0) { hideDropdown(); return; }
    showDropdown(filterUsers(currentQ));
});

searchInput.addEventListener('keydown', function (e) {
    const items = dropdown.querySelectorAll('.search-item');
    const active = dropdown.querySelector('.search-item.active');
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        const next = active ? active.nextElementSibling : items[0];
        if (next) { active?.classList.remove('active'); next.classList.add('active'); }
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        const prev = active ? active.previousElementSibling : items[items.length - 1];
        if (prev) { active?.classList.remove('active'); prev.classList.add('active'); }
    } else if (e.key === 'Enter' && active) {
        e.preventDefault();
        active.click();
    } else if (e.key === 'Escape') {
        hideDropdown();
    }
});

function showDropdown(users) {
    dropdown.innerHTML = '';
    if (users.length === 0) { hideDropdown(); return; }
    users.forEach(user => {
        const item = document.createElement('div');
        item.className = 'search-item';
        const initials = (user.first_name[0] + (user.last_name?.[0] || '')).toUpperCase();

        if (!user.student_id) {
            const ph = document.createElement('div');
            ph.className = 'search-item-avatar search-item-avatar-placeholder';
            ph.textContent = initials;
            item.appendChild(ph);
        } else {
            const cached = _avatarStatus.get(user.student_id);
            if (cached === 'error') {
                // Known 404 — show placeholder directly, no request
                const ph = document.createElement('div');
                ph.className = 'search-item-avatar search-item-avatar-placeholder';
                ph.textContent = initials;
                item.appendChild(ph);
            } else if (cached === 'loaded') {
                // Known hit — show img, browser cache handles it
                const img = document.createElement('img');
                img.className = 'search-item-avatar';
                img.src = '/avatar/' + user.student_id;
                img.alt = '';
                item.appendChild(img);
            } else {
                // Unknown — try once; cache the outcome
                const img = document.createElement('img');
                img.className = 'search-item-avatar';
                img.alt = '';
                const ph = document.createElement('div');
                ph.className = 'search-item-avatar search-item-avatar-placeholder';
                ph.textContent = initials;
                ph.style.display = 'none';
                img.onload = () => { _avatarStatus.set(user.student_id, 'loaded'); };
                img.onerror = () => {
                    _avatarStatus.set(user.student_id, 'error');
                    img.style.display = 'none';
                    ph.style.display = 'flex';
                };
                img.src = '/avatar/' + user.student_id; // set after handlers
                item.appendChild(img);
                item.appendChild(ph);
            }
        }

        const nameSpan = document.createElement('span');
        nameSpan.textContent = `${user.first_name} ${user.last_name}`;
        item.appendChild(nameSpan);
        item.addEventListener('click', () => selectUser(user));
        dropdown.appendChild(item);
    });
    dropdown.classList.remove('hidden');
}

function hideDropdown() {
    dropdown.classList.add('hidden');
    dropdown.innerHTML = '';
}

async function selectUser(user) {
    document.getElementById('first_name').value = user.first_name;
    document.getElementById('last_name').value = user.last_name;
    document.getElementById('selected-name').textContent = `${user.first_name} ${user.last_name}`;

    const avatarImg = document.getElementById('avatar-img');
    const placeholder = document.getElementById('avatar-placeholder');
    avatarImg.classList.add('hidden');
    placeholder.classList.add('hidden');
    showInitialsPlaceholder(user, placeholder);

    userSelectSection.classList.add('hidden');
    selectedSection.classList.remove('hidden');
    hideDropdown();
    document.getElementById('password').focus();

    if (!user.student_id || !user.discord_id) return;

    function tryLoad(bust) {
        return new Promise(resolve => {
            avatarImg.onload = () => resolve(true);
            avatarImg.onerror = () => resolve(false);
            avatarImg.src = '/avatar/' + user.student_id + (bust ? '?t=' + Date.now() : '');
        });
    }

    let ok = await tryLoad(false);
    if (!ok) {
        try { await fetch('/api/avatar/fetch/' + user.student_id, { method: 'POST' }); } catch(e) {}
        ok = await tryLoad(true);
    }
    if (ok) {
        avatarImg.classList.remove('hidden');
        placeholder.classList.add('hidden');
        _avatarStatus.set(user.student_id, 'loaded');
    }
}

function showInitialsPlaceholder(user, el) {
    el.textContent = (user.first_name[0] + (user.last_name[0] || '')).toUpperCase();
    el.classList.remove('hidden');
}

document.getElementById('change-user-btn').addEventListener('click', function () {
    selectedSection.classList.add('hidden');
    userSelectSection.classList.remove('hidden');
    searchInput.value = '';
    searchInput.focus();
});

document.addEventListener('click', function (e) {
    if (!e.target.closest('#user-select-section')) hideDropdown();
});

function togglePass(id, btn) {
    const input = document.getElementById(id);
    input.type = input.type === 'password' ? 'text' : 'password';
    btn.textContent = input.type === 'password' ? 'Show' : 'Hide';
}

const prefillData = document.getElementById('prefill-data');
if (prefillData) {
    const p = JSON.parse(prefillData.textContent);
    ensureUsersLoaded().then(() => {
        const results = fuse ? fuse.search(p.first_name + ' ' + p.last_name, { limit: 1 }) : [];
        const match = results.length && results[0].item.first_name === p.first_name && results[0].item.last_name === p.last_name
            ? results[0].item
            : { first_name: p.first_name, last_name: p.last_name, student_id: null };
        selectUser(match);
    });
}
