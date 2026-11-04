/**
 * Company Employee Portal - Core Frontend Logic
 * Implements authentication, CSRF token propagation, dynamic RBAC navigation,
 * directory browsing, profile editing, provisioning, and admin workflows.
 */

// Helper to retrieve cookie by name
function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return null;
}

// Resolve the CSRF token cookie name from server-rendered meta tag, with fallback
function getCsrfCookieName() {
  const meta = document.querySelector('meta[name="csrf-cookie-name"]');
  if (meta && meta.content && !meta.content.startsWith('{{')) {
    return meta.content.trim();
  }
  return 'csrf_token';
}

// Wrapper around window.fetch with automatic credentials, CSRF header, and 401 handling
async function apiFetch(url, options = {}) {
  const config = {
    ...options,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  };

  // State-changing requests attach X-CSRF-Token from cookie
  const method = (config.method || 'GET').toUpperCase();
  if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(method)) {
    const configuredName = getCsrfCookieName();
    const csrfToken = getCookie(configuredName) || getCookie('csrf_token') || getCookie('csrf_token_staging') || getCookie('csrf_token_prod');
    if (csrfToken) {
      config.headers['X-CSRF-Token'] = csrfToken;
    }
  }

  try {
    const response = await fetch(url, config);

    // Unauthenticated handling
    if (response.status === 401 && !window.location.pathname.endsWith('/login')) {
      window.location.href = '/login?reason=session_expired';
      throw new Error('Session expired or unauthorized');
    }

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      let errorMsg = 'An unexpected error occurred.';
      if (typeof data.detail === 'string') {
        errorMsg = data.detail;
      } else if (Array.isArray(data.detail)) {
        errorMsg = data.detail.map(d => d.msg || `${d.loc?.join('.')}: invalid`).join(', ');
      }
      const err = new Error(errorMsg);
      err.status = response.status;
      err.data = data;
      throw err;
    }

    return data;
  } catch (err) {
    if (err.name === 'TypeError' && err.message.includes('fetch')) {
      throw new Error('Network error. Please check your internet connection or server status.');
    }
    throw err;
  }
}

// ============================================================================
// LOGIN PAGE CONTROLLER
// ============================================================================

function initLoginPage() {
  const loginForm = document.getElementById('loginForm');
  if (!loginForm) return;

  const emailInput = document.getElementById('emailInput');
  const passwordInput = document.getElementById('passwordInput');
  const togglePasswordBtn = document.getElementById('togglePasswordBtn');
  const loginBtn = document.getElementById('loginBtn');
  const loginBtnText = document.getElementById('loginBtnText');
  const loginBtnSpinner = document.getElementById('loginBtnSpinner');
  const alertContainer = document.getElementById('alertContainer');

  // Check URL parameters for session expiry notification
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('reason') === 'session_expired') {
    showAlert('Your session has expired. Please log in again.', 'warning');
  }

  // Password visibility toggle
  if (togglePasswordBtn) {
    togglePasswordBtn.addEventListener('click', () => {
      const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
      passwordInput.setAttribute('type', type);
      togglePasswordBtn.textContent = type === 'password' ? 'Show' : 'Hide';
    });
  }

  function showAlert(message, type = 'error') {
    alertContainer.innerHTML = `
      <div class="alert alert-${type}">
        <span>${message}</span>
      </div>
    `;
  }

  function clearAlert() {
    alertContainer.innerHTML = '';
  }

  function setLoading(loading) {
    loginBtn.disabled = loading;
    if (loading) {
      loginBtnText.style.display = 'none';
      loginBtnSpinner.style.display = 'inline-block';
    } else {
      loginBtnText.style.display = 'inline-block';
      loginBtnSpinner.style.display = 'none';
    }
  }

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearAlert();

    const email = emailInput.value.trim();
    const password = passwordInput.value;

    if (!email || !password) {
      showAlert('Please enter both your email address and password.');
      return;
    }

    setLoading(true);

    try {
      await apiFetch('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });

      // Authentication successful -> redirect to dashboard
      window.location.href = '/dashboard';
    } catch (err) {
      setLoading(false);
      showAlert(err.message || 'Login failed.');
    }
  });
}

// ============================================================================
// DASHBOARD CONTROLLER
// ============================================================================

let currentUser = null;
let directoryState = {
  skip: 0,
  limit: 10,
  search: '',
  department: '',
  role: '',
  total: 0,
};

async function initDashboard() {
  const dashboardRoot = document.getElementById('dashboardRoot');
  if (!dashboardRoot) return;

  // Load current authenticated user profile
  try {
    currentUser = await apiFetch('/auth/me');
    renderUserHeader(currentUser);
    setupRoleNavigation(currentUser.role);
    renderProfileView(currentUser);
  } catch (err) {
    console.error('Failed to load authenticated user profile:', err);
    return;
  }

  // Setup Global Listeners
  setupSidebarNavigation();
  setupLogout();
  startIstClock();
  loadTodayAttendance();
}

function renderUserHeader(user) {
  document.getElementById('headerUserName').textContent = user.name;
  document.getElementById('headerUserRole').textContent = user.role;
  document.getElementById('headerUserRole').className = `role-pill role-${user.role}`;

  const avatar = document.getElementById('headerUserAvatar');
  if (avatar) {
    avatar.textContent = (user.name || 'U').charAt(0).toUpperCase();
  }
}

function setupRoleNavigation(role) {
  const navContainer = document.getElementById('sidebarNavContainer');
  let navHtml = '';

  // Employee Navigation
  navHtml += `
    <div class="nav-section-title">Personal</div>
    <div class="nav-item active" data-target="profileSection">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path></svg>
      <span>My Profile</span>
    </div>
    <div class="nav-item" data-target="accountSection">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"></path></svg>
      <span>My Account</span>
    </div>
    <div class="nav-item" data-target="attendanceSection">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
      <span>Attendance</span>
    </div>
    <div class="nav-item" data-target="leavesSection">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
      <span>My Leaves</span>
    </div>
  `;

  // HR Navigation
  if (role === 'hr' || role === 'admin') {
    navHtml += `
      <div class="nav-section-title" style="margin-top: 16px;">HR Management</div>
      <div class="nav-item" data-target="directorySection">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
        <span>Employee Directory</span>
      </div>
      <div class="nav-item" data-target="provisionSection">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z"></path></svg>
        <span>Provision Employee</span>
      </div>
      <div class="nav-item" data-target="attendanceAdminSection">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path></svg>
        <span>Attendance Management</span>
      </div>
      <div class="nav-item" data-target="leavesAdminSection">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        <span>Leave Approvals</span>
      </div>
    `;
  }

  // Admin Navigation
  if (role === 'admin') {
    navHtml += `
      <div class="nav-section-title" style="margin-top: 16px;">Administration</div>
      <div class="nav-item" data-target="adminOverviewSection">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
        <span>Account & Role Ops</span>
      </div>
    `;
  }

  navContainer.innerHTML = navHtml;
}

function setupSidebarNavigation() {
  const navContainer = document.getElementById('sidebarNavContainer');
  navContainer.addEventListener('click', (e) => {
    const item = e.target.closest('.nav-item');
    if (!item) return;

    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    item.classList.add('active');

    const targetId = item.getAttribute('data-target');
    document.querySelectorAll('.content-section').forEach(sec => sec.classList.remove('active'));

    const activeSection = document.getElementById(targetId);
    if (activeSection) {
      activeSection.classList.add('active');
      document.getElementById('headerTitle').textContent = item.querySelector('span').textContent;

      if (targetId === 'directorySection') {
        loadDirectory();
      } else if (targetId === 'adminOverviewSection') {
        loadDirectory(true);
      } else if (targetId === 'attendanceSection') {
        loadTodayAttendance();
        loadMyAttendanceHistory();
      } else if (targetId === 'attendanceAdminSection') {
        loadCompanyAttendance();
      } else if (targetId === 'leavesSection') {
        loadLeaveBalances();
        loadMyLeavesHistory();
        loadMyCompoffClaims();
      } else if (targetId === 'leavesAdminSection') {
        loadAdminPendingLeaves();
        loadAdminCompoffClaims();
      }
    }
  });

  // Mobile menu toggle
  const mobileBtn = document.getElementById('mobileMenuBtn');
  const sidebar = document.getElementById('appSidebar');
  if (mobileBtn && sidebar) {
    mobileBtn.addEventListener('click', () => {
      sidebar.classList.toggle('open');
    });
  }
}

function setupLogout() {
  const logoutBtn = document.getElementById('logoutBtn');
  if (!logoutBtn) return;

  logoutBtn.addEventListener('click', async () => {
    try {
      await apiFetch('/auth/logout', { method: 'POST' });
    } catch (err) {
      console.warn('Logout notification error:', err);
    } finally {
      window.location.href = '/login';
    }
  });
}

function renderProfileView(user) {
  // My Profile
  document.getElementById('profName').textContent = user.name;
  document.getElementById('profEmail').textContent = user.email;
  document.getElementById('profCode').textContent = user.employee_code;
  document.getElementById('profDept').textContent = user.department || 'Unassigned';
  document.getElementById('profRole').textContent = user.role;
  document.getElementById('profRole').className = `role-pill role-${user.role}`;
  document.getElementById('profStatus').textContent = user.is_active ? 'Active' : 'Inactive';
  document.getElementById('profStatus').className = `status-pill status-${user.is_active ? 'active' : 'inactive'}`;
  const profJoining = document.getElementById('profJoiningDate');
  if (profJoining) {
    profJoining.textContent = user.date_of_joining || '—';
  }
  document.getElementById('profCreated').textContent = new Date(user.created_at).toLocaleDateString();

  // Self-Service Edit Form
  const selfEditForm = document.getElementById('selfEditForm');
  const selfNameInput = document.getElementById('selfNameInput');
  if (selfEditForm && selfNameInput) {
    selfNameInput.value = user.name;
    selfEditForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const newName = selfNameInput.value.trim();
      const alertBox = document.getElementById('selfEditAlert');
      alertBox.innerHTML = '';

      if (!newName) {
        alertBox.innerHTML = '<div class="alert alert-error">Name cannot be empty.</div>';
        return;
      }

      try {
        const updated = await apiFetch(`/employees/${user.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ name: newName }),
        });
        currentUser.name = updated.name;
        renderUserHeader(currentUser);
        document.getElementById('profName').textContent = updated.name;
        alertBox.innerHTML = '<div class="alert alert-success">Profile updated successfully.</div>';
      } catch (err) {
        alertBox.innerHTML = `<div class="alert alert-error">${err.message}</div>`;
      }
    });
  }

  // Provisioning Form Role Option setup
  const provRoleSelect = document.getElementById('provRoleSelect');
  if (provRoleSelect) {
    if (user.role === 'hr') {
      provRoleSelect.innerHTML = `
        <option value="employee">Employee</option>
        <option value="hr">Human Resources (HR)</option>
      `;
    } else if (user.role === 'admin') {
      provRoleSelect.innerHTML = `
        <option value="employee">Employee</option>
        <option value="hr">Human Resources (HR)</option>
        <option value="admin">Administrator</option>
      `;
    }
  }
}

// ============================================================================
// DIRECTORY & MANAGEMENT CONTROLLER
// ============================================================================

async function loadDirectory(isAdminView = false) {
  const tableBody = document.getElementById('directoryTableBody');
  const paginationInfo = document.getElementById('paginationInfo');
  const prevBtn = document.getElementById('prevPageBtn');
  const nextBtn = document.getElementById('nextPageBtn');

  tableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 30px;"><div class="spinner" style="border-top-color: var(--primary); display: inline-block;"></div> Loading employee directory...</td></tr>`;

  const params = new URLSearchParams({
    skip: directoryState.skip,
    limit: directoryState.limit,
  });
  if (directoryState.search) params.append('search', directoryState.search);
  if (directoryState.department) params.append('department', directoryState.department);
  if (directoryState.role) params.append('role', directoryState.role);

  try {
    const data = await apiFetch(`/employees?${params.toString()}`);
    directoryState.total = data.total;

    if (!data.items || data.items.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 30px; color: var(--text-muted);">No employee records found matching criteria.</td></tr>`;
      paginationInfo.textContent = 'Showing 0 of 0';
      prevBtn.disabled = true;
      nextBtn.disabled = true;
      return;
    }

    tableBody.innerHTML = data.items.map(emp => `
      <tr>
        <td><strong>${emp.employee_code}</strong></td>
        <td>${escapeHtml(emp.name)}</td>
        <td>${escapeHtml(emp.email)}</td>
        <td>${escapeHtml(emp.department || '—')}</td>
        <td><span class="role-pill role-${emp.role}">${emp.role}</span></td>
        <td><span class="status-pill status-${emp.is_active ? 'active' : 'inactive'}">${emp.is_active ? 'Active' : 'Inactive'}</span></td>
        <td>
          <div class="table-actions">
            <button class="btn-secondary" onclick="viewEmployeeDetail(${emp.id})">View</button>
            <button class="btn-secondary" onclick="openEditEmployeeModal(${emp.id}, '${escapeHtml(emp.name)}', '${escapeHtml(emp.department || '')}')">Edit</button>
            ${currentUser.role === 'admin' ? `
              <button class="btn-secondary" onclick="openRoleModal(${emp.id}, '${emp.role}')">Role</button>
              <button class="${emp.is_active ? 'btn-danger' : 'btn-secondary'}" onclick="openStatusModal(${emp.id}, '${escapeHtml(emp.name)}', ${emp.is_active})">
                ${emp.is_active ? 'Deactivate' : 'Activate'}
              </button>
            ` : ''}
          </div>
        </td>
      </tr>
    `).join('');

    const start = directoryState.skip + 1;
    const end = Math.min(directoryState.skip + directoryState.limit, data.total);
    paginationInfo.textContent = `Showing ${start}–${end} of ${data.total}`;

    prevBtn.disabled = directoryState.skip === 0;
    nextBtn.disabled = directoryState.skip + directoryState.limit >= data.total;

  } catch (err) {
    tableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 24px; color: var(--error);">${err.message}</td></tr>`;
  }
}

function setupDirectoryFilters() {
  const searchInput = document.getElementById('dirSearchInput');
  const deptSelect = document.getElementById('dirDeptSelect');
  const roleSelect = document.getElementById('dirRoleSelect');
  const prevBtn = document.getElementById('prevPageBtn');
  const nextBtn = document.getElementById('nextPageBtn');

  if (searchInput) {
    let debounceTimer;
    searchInput.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        directoryState.search = searchInput.value.trim();
        directoryState.skip = 0;
        loadDirectory();
      }, 350);
    });
  }

  if (deptSelect) {
    deptSelect.addEventListener('change', () => {
      directoryState.department = deptSelect.value;
      directoryState.skip = 0;
      loadDirectory();
    });
  }

  if (roleSelect) {
    roleSelect.addEventListener('change', () => {
      directoryState.role = roleSelect.value;
      directoryState.skip = 0;
      loadDirectory();
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      if (directoryState.skip >= directoryState.limit) {
        directoryState.skip -= directoryState.limit;
        loadDirectory();
      }
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      if (directoryState.skip + directoryState.limit < directoryState.total) {
        directoryState.skip += directoryState.limit;
        loadDirectory();
      }
    });
  }
}

// Modal Controllers
async function viewEmployeeDetail(employeeId) {
  try {
    const emp = await apiFetch(`/employees/${employeeId}`);
    document.getElementById('detailEmpCode').textContent = emp.employee_code;
    document.getElementById('detailEmpName').textContent = emp.name;
    document.getElementById('detailEmpEmail').textContent = emp.email;
    document.getElementById('detailEmpDept').textContent = emp.department || 'None';
    document.getElementById('detailEmpRole').textContent = emp.role;
    document.getElementById('detailEmpStatus').textContent = emp.is_active ? 'Active' : 'Inactive';
    const detailJoining = document.getElementById('detailEmpJoiningDate');
    if (detailJoining) {
      detailJoining.textContent = emp.date_of_joining || '—';
    }
    document.getElementById('detailEmpCreated').textContent = new Date(emp.created_at).toLocaleString();
    openModal('detailModal');
  } catch (err) {
    alert('Failed to load employee details: ' + err.message);
  }
}

function openEditEmployeeModal(employeeId, name, department) {
  document.getElementById('editEmpId').value = employeeId;
  document.getElementById('editEmpName').value = name;
  document.getElementById('editEmpDept').value = department;
  document.getElementById('editEmpAlert').innerHTML = '';
  openModal('editEmployeeModal');
}

function setupEditEmployeeForm() {
  const form = document.getElementById('editEmployeeForm');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('editEmpId').value;
    const name = document.getElementById('editEmpName').value.trim();
    const dept = document.getElementById('editEmpDept').value.trim();
    const alertBox = document.getElementById('editEmpAlert');
    alertBox.innerHTML = '';

    try {
      await apiFetch(`/employees/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name, department: dept }),
      });
      closeModal('editEmployeeModal');
      loadDirectory();
    } catch (err) {
      alertBox.innerHTML = `<div class="alert alert-error">${err.message}</div>`;
    }
  });
}

function openRoleModal(employeeId, currentRole) {
  document.getElementById('roleEmpId').value = employeeId;
  document.getElementById('roleSelect').value = currentRole;
  document.getElementById('roleAlert').innerHTML = '';
  openModal('roleModal');
}

function setupRoleForm() {
  const form = document.getElementById('roleForm');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('roleEmpId').value;
    const role = document.getElementById('roleSelect').value;
    const alertBox = document.getElementById('roleAlert');
    alertBox.innerHTML = '';

    try {
      await apiFetch(`/admin/employees/${id}/role`, {
        method: 'PATCH',
        body: JSON.stringify({ role }),
      });
      closeModal('roleModal');
      loadDirectory();
    } catch (err) {
      alertBox.innerHTML = `<div class="alert alert-error">${err.message}</div>`;
    }
  });
}

function openStatusModal(employeeId, name, currentIsActive) {
  document.getElementById('statusEmpId').value = employeeId;
  document.getElementById('statusTargetState').value = (!currentIsActive).toString();
  document.getElementById('statusPromptText').textContent = currentIsActive
    ? `Are you sure you want to DEACTIVATE ${name}? All active sessions for this account will be terminated immediately.`
    : `Are you sure you want to ACTIVATE ${name}?`;
  document.getElementById('statusAlert').innerHTML = '';
  openModal('statusModal');
}

function setupStatusForm() {
  const form = document.getElementById('statusForm');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('statusEmpId').value;
    const targetState = document.getElementById('statusTargetState').value === 'true';
    const alertBox = document.getElementById('statusAlert');
    alertBox.innerHTML = '';

    try {
      await apiFetch(`/admin/employees/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: targetState }),
      });
      closeModal('statusModal');
      loadDirectory();
    } catch (err) {
      alertBox.innerHTML = `<div class="alert alert-error">${err.message}</div>`;
    }
  });
}

function setupProvisionForm() {
  const form = document.getElementById('provisionForm');
  if (!form) return;

  const joiningInput = document.getElementById('provJoiningDateInput');
  if (joiningInput) {
    const today = new Date().toISOString().split('T')[0];
    joiningInput.max = today;
    if (!joiningInput.value) {
      joiningInput.value = today;
    }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const alertBox = document.getElementById('provisionAlert');
    alertBox.innerHTML = '';

    const joiningInputEl = document.getElementById('provJoiningDateInput');
    const joiningVal = joiningInputEl ? joiningInputEl.value : '';
    if (!joiningVal) {
      alertBox.innerHTML = '<div class="alert alert-error">Please select a valid date of joining.</div>';
      return;
    }

    const payload = {
      employee_code: document.getElementById('provCodeInput').value.trim(),
      name: document.getElementById('provNameInput').value.trim(),
      email: document.getElementById('provEmailInput').value.trim(),
      department: document.getElementById('provDeptInput').value.trim() || null,
      date_of_joining: joiningVal,
      role: document.getElementById('provRoleSelect').value,
      password: document.getElementById('provPasswordInput').value,
    };

    try {
      const result = await apiFetch('/hr/employees', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      alertBox.innerHTML = `
        <div class="alert alert-success">
          Account for <strong>${escapeHtml(result.name)}</strong> (${result.employee_code}) provisioned successfully!
        </div>
      `;
      form.reset();
      if (joiningInputEl) {
        joiningInputEl.value = new Date().toISOString().split('T')[0];
      }
    } catch (err) {
      alertBox.innerHTML = `<div class="alert alert-error">${err.message}</div>`;
    }
  });
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('open');
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('open');
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ============================================================================
// ATTENDANCE CONTROLLERS & WORKFLOWS
// ============================================================================

let istClockInterval = null;

function startIstClock() {
  if (istClockInterval) clearInterval(istClockInterval);

  function tick() {
    const clockEl = document.getElementById('istLiveClock');
    if (!clockEl) return;
    try {
      const now = new Date();
      const options = {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
      };
      clockEl.textContent = new Intl.DateTimeFormat('en-US', options).format(now) + ' IST';
    } catch {
      const now = new Date();
      const utcMs = now.getTime() + (now.getTimezoneOffset() * 60000);
      const istTime = new Date(utcMs + (5.5 * 3600000));
      clockEl.textContent = istTime.toLocaleTimeString('en-US') + ' IST';
    }
  }

  tick();
  istClockInterval = setInterval(tick, 1000);
}

function formatDuration(startIso, endIso) {
  if (!startIso) return '—';
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : new Date();
  if (isNaN(start.getTime()) || isNaN(end.getTime())) return '—';
  const diffMs = end - start;
  if (diffMs < 0) return '—';
  const diffMins = Math.floor(diffMs / 60000);
  const hours = Math.floor(diffMins / 60);
  const mins = diffMins % 60;
  return `${hours}h ${mins}m`;
}

function toIstDatetimeLocal(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return '';
  const istOffsetMs = 5.5 * 60 * 60 * 1000;
  const istDate = new Date(d.getTime() + istOffsetMs);
  return istDate.toISOString().slice(0, 16);
}

// ----------------------------------------------------------------------------
// Today Shift Card Controller
// ----------------------------------------------------------------------------

let todayAttendanceState = null;
let isPunchPending = false;

async function loadTodayAttendance() {
  const badge = document.getElementById('todayStatusBadge');
  const dateText = document.getElementById('todayWorkDateText');
  const checkInText = document.getElementById('todayCheckInTime');
  const checkOutText = document.getElementById('todayCheckOutTime');
  const durationText = document.getElementById('todayDuration');
  const dayTypeText = document.getElementById('todayDayType');
  const btnIn = document.getElementById('btnCheckIn');
  const btnOut = document.getElementById('btnCheckOut');

  if (!badge) return;

  try {
    const data = await apiFetch('/attendance/today');
    todayAttendanceState = data;

    if (data.work_date) {
      dateText.textContent = data.record?.work_date_formatted || data.work_date;
    }

    dayTypeText.textContent = data.is_weekend ? 'Weekend Workday' : 'Regular Workday';

    if (!data.has_checked_in) {
      badge.textContent = 'Not Checked In';
      badge.className = 'status-pill status-inactive';
      checkInText.textContent = '—';
      checkOutText.textContent = '—';
      durationText.textContent = '—';
      if (btnIn) btnIn.disabled = false;
      if (btnOut) btnOut.disabled = true;
    } else if (data.has_checked_in && !data.has_checked_out) {
      badge.textContent = 'Checked In (Active)';
      badge.className = 'status-pill status-present';
      checkInText.textContent = data.record?.check_in_ist || '—';
      checkOutText.textContent = '—';
      durationText.textContent = formatDuration(data.record?.check_in_at, null) + ' (ongoing)';
      if (btnIn) btnIn.disabled = true;
      if (btnOut) btnOut.disabled = false;
    } else {
      badge.textContent = 'Checked Out (Completed)';
      badge.className = 'status-pill status-present';
      checkInText.textContent = data.record?.check_in_ist || '—';
      checkOutText.textContent = data.record?.check_out_ist || '—';
      durationText.textContent = formatDuration(data.record?.check_in_at, data.record?.check_out_at);
      if (btnIn) btnIn.disabled = true;
      if (btnOut) btnOut.disabled = true;
    }

    if (data.current_status === 'incomplete') {
      badge.textContent = 'Incomplete Shift';
      badge.className = 'status-pill status-incomplete';
    }
  } catch (err) {
    console.error('Failed to load today attendance state:', err);
    showPunchAlert(err.message || 'Failed to load attendance state.', 'error');
  }
}

function showPunchAlert(message, type = 'error') {
  const container = document.getElementById('punchAlert');
  if (!container) return;
  container.innerHTML = `
    <div class="alert alert-${type}">
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

function clearPunchAlert() {
  const container = document.getElementById('punchAlert');
  if (container) container.innerHTML = '';
}

function setupAttendancePunchListeners() {
  const btnIn = document.getElementById('btnCheckIn');
  const btnOut = document.getElementById('btnCheckOut');
  const notesInput = document.getElementById('punchNotesInput');

  if (btnIn) {
    btnIn.addEventListener('click', async () => {
      if (isPunchPending) return;
      isPunchPending = true;
      btnIn.disabled = true;
      clearPunchAlert();

      const notes = notesInput ? notesInput.value.trim() : '';

      try {
        await apiFetch('/attendance/check-in', {
          method: 'POST',
          body: JSON.stringify({ notes: notes || null }),
        });
        showPunchAlert('Check-in recorded successfully in IST!', 'success');
        if (notesInput) notesInput.value = '';
        await loadTodayAttendance();
        await loadMyAttendanceHistory();
      } catch (err) {
        showPunchAlert(err.message || 'Failed to record check-in.', 'error');
        await loadTodayAttendance();
      } finally {
        isPunchPending = false;
      }
    });
  }

  if (btnOut) {
    btnOut.addEventListener('click', async () => {
      if (isPunchPending) return;
      isPunchPending = true;
      btnOut.disabled = true;
      clearPunchAlert();

      const notes = notesInput ? notesInput.value.trim() : '';

      try {
        await apiFetch('/attendance/check-out', {
          method: 'POST',
          body: JSON.stringify({ notes: notes || null }),
        });
        showPunchAlert('Check-out recorded successfully! Shift completed.', 'success');
        if (notesInput) notesInput.value = '';
        await loadTodayAttendance();
        await loadMyAttendanceHistory();
      } catch (err) {
        showPunchAlert(err.message || 'Failed to record check-out.', 'error');
        await loadTodayAttendance();
      } finally {
        isPunchPending = false;
      }
    });
  }
}

// ----------------------------------------------------------------------------
// Personal Attendance History Controller
// ----------------------------------------------------------------------------

let myAttendanceState = {
  page: 1,
  page_size: 10,
  start_date: '',
  end_date: '',
  total: 0,
};

async function loadMyAttendanceHistory() {
  const tbody = document.getElementById('myAttendanceTableBody');
  const infoEl = document.getElementById('myAttPaginationInfo');
  const prevBtn = document.getElementById('myAttPrevBtn');
  const nextBtn = document.getElementById('myAttNextBtn');

  if (!tbody) return;

  tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">Loading attendance records...</td></tr>`;

  const params = new URLSearchParams({
    page: myAttendanceState.page,
    page_size: myAttendanceState.page_size,
  });
  if (myAttendanceState.start_date) params.append('start_date', myAttendanceState.start_date);
  if (myAttendanceState.end_date) params.append('end_date', myAttendanceState.end_date);

  try {
    const data = await apiFetch(`/attendance/my?${params.toString()}`);
    myAttendanceState.total = data.total;

    if (!data.items || data.items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 32px;">No personal attendance records found.</td></tr>`;
      if (infoEl) infoEl.textContent = 'Showing 0 of 0 records';
      if (prevBtn) prevBtn.disabled = true;
      if (nextBtn) nextBtn.disabled = true;
      return;
    }

    let rowsHtml = '';
    data.items.forEach(rec => {
      const duration = formatDuration(rec.check_in_at, rec.check_out_at);
      const dayTypeBadge = rec.is_weekend
        ? `<span class="badge-weekend">Weekend</span>`
        : `<span class="badge-weekday">Weekday</span>`;
      const statusClass = `status-${rec.status}`;
      const statusLabel = rec.status.replace('_', ' ').toUpperCase();
      const correctedBadge = rec.is_corrected
        ? ` <span class="badge-corrected" title="Audited correction applied">Corrected</span>`
        : '';

      rowsHtml += `
        <tr>
          <td><strong>${escapeHtml(rec.work_date_formatted || rec.work_date)}</strong></td>
          <td>${escapeHtml(rec.check_in_ist || '—')}</td>
          <td>${escapeHtml(rec.check_out_ist || '—')}</td>
          <td>${escapeHtml(duration)}</td>
          <td><span class="status-pill ${statusClass}">${escapeHtml(statusLabel)}</span>${correctedBadge}</td>
          <td>${dayTypeBadge}</td>
          <td>${escapeHtml(rec.notes || '—')}</td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;

    const startIdx = (data.page - 1) * data.page_size + 1;
    const endIdx = Math.min(data.page * data.page_size, data.total);
    if (infoEl) {
      infoEl.textContent = `Showing ${startIdx}–${endIdx} of ${data.total} records (Page ${data.page} of ${data.total_pages || 1})`;
    }
    if (prevBtn) prevBtn.disabled = data.page <= 1;
    if (nextBtn) nextBtn.disabled = data.page >= data.total_pages;
  } catch (err) {
    console.error('Failed to load my attendance history:', err);
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--error); padding: 24px;">${escapeHtml(err.message)}</td></tr>`;
  }
}

function setupMyAttendanceFilters() {
  const startInput = document.getElementById('myAttStartDate');
  const endInput = document.getElementById('myAttEndDate');
  const filterBtn = document.getElementById('btnFilterMyAtt');
  const resetBtn = document.getElementById('btnResetMyAtt');
  const prevBtn = document.getElementById('myAttPrevBtn');
  const nextBtn = document.getElementById('myAttNextBtn');

  if (filterBtn) {
    filterBtn.addEventListener('click', () => {
      myAttendanceState.start_date = startInput.value;
      myAttendanceState.end_date = endInput.value;
      myAttendanceState.page = 1;
      loadMyAttendanceHistory();
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      startInput.value = '';
      endInput.value = '';
      myAttendanceState.start_date = '';
      myAttendanceState.end_date = '';
      myAttendanceState.page = 1;
      loadMyAttendanceHistory();
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      if (myAttendanceState.page > 1) {
        myAttendanceState.page--;
        loadMyAttendanceHistory();
      }
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      myAttendanceState.page++;
      loadMyAttendanceHistory();
    });
  }
}

// ----------------------------------------------------------------------------
// Company Attendance (HR & Admin) Controller
// ----------------------------------------------------------------------------

let companyAttendanceState = {
  page: 1,
  page_size: 10,
  employee_id: '',
  department: '',
  start_date: '',
  end_date: '',
  status: '',
  total: 0,
};

async function loadCompanyAttendance() {
  const tbody = document.getElementById('companyAttendanceTableBody');
  const infoEl = document.getElementById('companyAttPaginationInfo');
  const prevBtn = document.getElementById('companyAttPrevBtn');
  const nextBtn = document.getElementById('companyAttNextBtn');

  if (!tbody) return;

  tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">Loading company attendance records...</td></tr>`;

  const params = new URLSearchParams({
    page: companyAttendanceState.page,
    page_size: companyAttendanceState.page_size,
  });
  if (companyAttendanceState.employee_id) params.append('employee_id', companyAttendanceState.employee_id);
  if (companyAttendanceState.department) params.append('department', companyAttendanceState.department);
  if (companyAttendanceState.start_date) params.append('start_date', companyAttendanceState.start_date);
  if (companyAttendanceState.end_date) params.append('end_date', companyAttendanceState.end_date);
  if (companyAttendanceState.status) params.append('status', companyAttendanceState.status);

  try {
    const data = await apiFetch(`/attendance?${params.toString()}`);
    companyAttendanceState.total = data.total;

    if (!data.items || data.items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 32px;">No company attendance records found matching filters.</td></tr>`;
      if (infoEl) infoEl.textContent = 'Showing 0 of 0 records';
      if (prevBtn) prevBtn.disabled = true;
      if (nextBtn) nextBtn.disabled = true;
      return;
    }

    let rowsHtml = '';
    data.items.forEach(rec => {
      const duration = formatDuration(rec.check_in_at, rec.check_out_at);
      const dayTypeBadge = rec.is_weekend
        ? `<span class="badge-weekend">Weekend</span>`
        : `<span class="badge-weekday">Weekday</span>`;
      const statusClass = `status-${rec.status}`;
      const statusLabel = rec.status.replace('_', ' ').toUpperCase();
      const auditBadge = rec.is_corrected
        ? `<span class="badge-corrected" title="Audited correction applied (${rec.corrections?.length || 1} logs)">Corrected</span>`
        : `<span style="color: var(--text-light); font-size: 12px;">Original</span>`;

      const recJsonAttr = escapeHtml(JSON.stringify(rec));

      rowsHtml += `
        <tr>
          <td>
            <strong>${escapeHtml(rec.employee_name || '—')}</strong>
            <div style="font-size: 11.5px; color: var(--text-muted);">${escapeHtml(rec.employee_code || '')} (ID: ${rec.employee_id})</div>
          </td>
          <td>${escapeHtml(rec.department || '—')}</td>
          <td>${escapeHtml(rec.work_date_formatted || rec.work_date)}</td>
          <td>${escapeHtml(rec.check_in_ist || '—')}</td>
          <td>${escapeHtml(rec.check_out_ist || '—')}</td>
          <td>${escapeHtml(duration)}</td>
          <td><span class="status-pill ${statusClass}">${escapeHtml(statusLabel)}</span></td>
          <td>${dayTypeBadge}</td>
          <td>${auditBadge}</td>
          <td>
            <button
              class="btn-secondary"
              style="padding: 4px 10px; font-size: 12px;"
              onclick="openCorrectionModalFromData(this)"
              data-record="${recJsonAttr}"
              title="Apply audited manual correction"
            >
              Correct
            </button>
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;

    const startIdx = (data.page - 1) * data.page_size + 1;
    const endIdx = Math.min(data.page * data.page_size, data.total);
    if (infoEl) {
      infoEl.textContent = `Showing ${startIdx}–${endIdx} of ${data.total} records (Page ${data.page} of ${data.total_pages || 1})`;
    }
    if (prevBtn) prevBtn.disabled = data.page <= 1;
    if (nextBtn) nextBtn.disabled = data.page >= data.total_pages;
  } catch (err) {
    console.error('Failed to load company attendance report:', err);
    tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--error); padding: 24px;">${escapeHtml(err.message)}</td></tr>`;
  }
}

function setupCompanyAttendanceFilters() {
  const empIdInput = document.getElementById('admAttEmpId');
  const deptInput = document.getElementById('admAttDept');
  const startInput = document.getElementById('admAttStartDate');
  const endInput = document.getElementById('admAttEndDate');
  const statusSelect = document.getElementById('admAttStatus');
  const filterBtn = document.getElementById('btnFilterAdmAtt');
  const resetBtn = document.getElementById('btnResetAdmAtt');
  const prevBtn = document.getElementById('companyAttPrevBtn');
  const nextBtn = document.getElementById('companyAttNextBtn');

  if (filterBtn) {
    filterBtn.addEventListener('click', () => {
      companyAttendanceState.employee_id = empIdInput.value.trim();
      companyAttendanceState.department = deptInput.value.trim();
      companyAttendanceState.start_date = startInput.value;
      companyAttendanceState.end_date = endInput.value;
      companyAttendanceState.status = statusSelect.value;
      companyAttendanceState.page = 1;
      loadCompanyAttendance();
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      empIdInput.value = '';
      deptInput.value = '';
      startInput.value = '';
      endInput.value = '';
      statusSelect.value = '';
      companyAttendanceState.employee_id = '';
      companyAttendanceState.department = '';
      companyAttendanceState.start_date = '';
      companyAttendanceState.end_date = '';
      companyAttendanceState.status = '';
      companyAttendanceState.page = 1;
      loadCompanyAttendance();
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      if (companyAttendanceState.page > 1) {
        companyAttendanceState.page--;
        loadCompanyAttendance();
      }
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      companyAttendanceState.page++;
      loadCompanyAttendance();
    });
  }
}

// ----------------------------------------------------------------------------
// Audited Correction Modal Controller
// ----------------------------------------------------------------------------

function openCorrectionModalFromData(btn) {
  try {
    const raw = btn.getAttribute('data-record');
    const record = JSON.parse(raw);
    document.getElementById('correctRecordId').value = record.id;
    document.getElementById('correctModalEmpName').textContent = record.employee_name || '—';
    document.getElementById('correctModalEmpCode').textContent = record.employee_code || '';
    document.getElementById('correctModalWorkDate').textContent = record.work_date_formatted || record.work_date;

    document.getElementById('correctCheckInInput').value = toIstDatetimeLocal(record.check_in_at);
    document.getElementById('correctCheckOutInput').value = toIstDatetimeLocal(record.check_out_at);
    document.getElementById('correctStatusSelect').value = record.status || 'present';
    document.getElementById('correctReasonInput').value = '';
    document.getElementById('correctNotesInput').value = record.notes || '';
    document.getElementById('correctAttAlert').innerHTML = '';

    openModal('correctAttendanceModal');
  } catch (err) {
    console.error('Failed to open correction modal:', err);
  }
}

function setupCorrectionForm() {
  const form = document.getElementById('correctAttendanceForm');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const alertBox = document.getElementById('correctAttAlert');
    const submitBtn = document.getElementById('btnSubmitCorrection');
    alertBox.innerHTML = '';

    const recordId = document.getElementById('correctRecordId').value;
    const checkInVal = document.getElementById('correctCheckInInput').value;
    const checkOutVal = document.getElementById('correctCheckOutInput').value;
    const statusVal = document.getElementById('correctStatusSelect').value;
    const reasonVal = document.getElementById('correctReasonInput').value.trim();
    const notesVal = document.getElementById('correctNotesInput').value.trim();

    if (!reasonVal || reasonVal.length < 3) {
      alertBox.innerHTML = `<div class="alert alert-error">Please provide a valid explanation of at least 3 characters.</div>`;
      return;
    }

    const confirmed = window.confirm('Are you sure you want to apply this attendance correction? This action is permanently logged.');
    if (!confirmed) return;

    submitBtn.disabled = true;
    submitBtn.textContent = 'Applying...';

    const payload = {
      reason: reasonVal,
      status: statusVal,
      notes: notesVal || null,
      check_in_at: checkInVal ? checkInVal + ':00' : null,
      check_out_at: checkOutVal ? checkOutVal + ':00' : null,
    };

    try {
      await apiFetch(`/attendance/${recordId}/correct`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      });

      closeModal('correctAttendanceModal');
      await loadCompanyAttendance();
      await loadTodayAttendance();
      await loadMyAttendanceHistory();
    } catch (err) {
      alertBox.innerHTML = `<div class="alert alert-error">${escapeHtml(err.message)}</div>`;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Apply & Audit Correction';
    }
  });
}

// ============================================================================
// LEAVE MANAGEMENT CONTROLLERS (Employee Self-Service)
// ============================================================================

let leaveBalancesState = null;
let isLeaveBalanceLoading = false;

let myLeavesState = {
  page: 1,
  page_size: 10,
  total: 0,
};
let isMyLeavesLoading = false;

// Preview state
let previewDebounceTimer = null;
let activeCalculationSeq = 0;
let isCurrentCalculationValid = false;
let lastCalculatedDays = null;

function showLeaveSectionAlert(message, type = 'success') {
  const container = document.getElementById('leaveSectionAlert');
  if (!container) return;
  container.innerHTML = `
    <div class="alert alert-${type}">
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

function clearLeaveSectionAlert() {
  const container = document.getElementById('leaveSectionAlert');
  if (container) container.innerHTML = '';
}

function showApplyLeaveAlert(message, type = 'error') {
  const container = document.getElementById('applyLeaveAlert');
  if (!container) return;
  container.innerHTML = `
    <div class="alert alert-${type}">
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

function clearApplyLeaveAlert() {
  const container = document.getElementById('applyLeaveAlert');
  if (container) container.innerHTML = '';
}

// ----------------------------------------------------------------------------
// A. Leave Balances Dashboard
// ----------------------------------------------------------------------------

async function loadLeaveBalances() {
  if (isLeaveBalanceLoading) return;
  isLeaveBalanceLoading = true;

  // Placeholder loading states
  const cards = ['CL', 'SL', 'EL', 'CO'];
  cards.forEach(code => {
    const balEl = document.getElementById(`balVal${code}`);
    const usedEl = document.getElementById(`usedVal${code}`);
    const resEl = document.getElementById(`resVal${code}`);
    if (balEl) balEl.textContent = '...';
    if (usedEl) usedEl.textContent = '...';
    if (resEl) resEl.textContent = '...';
  });

  try {
    const balances = await apiFetch('/leaves/balance');
    leaveBalancesState = Array.isArray(balances) ? balances : [];
    renderLeaveBalances(leaveBalancesState);
  } catch (err) {
    console.error('Failed to load leave balances:', err);
    renderLeaveBalancesError(err.message || 'Unable to retrieve balances.');
  } finally {
    isLeaveBalanceLoading = false;
  }
}

function renderLeaveBalances(balances) {
  const mapping = {
    CASUAL: 'CL',
    CL: 'CL',
    SICK: 'SL',
    SL: 'SL',
    EARNED: 'EL',
    EL: 'EL',
    COMP_OFF: 'CO',
    CO: 'CO',
  };

  const found = { CL: false, SL: false, EL: false, CO: false };

  balances.forEach(b => {
    const cardCode = mapping[b.category_code];
    if (cardCode) {
      found[cardCode] = true;
      const balEl = document.getElementById(`balVal${cardCode}`);
      const usedEl = document.getElementById(`usedVal${cardCode}`);
      const resEl = document.getElementById(`resVal${cardCode}`);
      if (balEl) balEl.textContent = b.available_days != null ? String(b.available_days) : '0';
      if (usedEl) usedEl.textContent = b.used_days != null ? String(b.used_days) : '0';
      if (resEl) resEl.textContent = b.reserved_days != null ? String(b.reserved_days) : '0';
    }
  });

  ['CL', 'SL', 'EL', 'CO'].forEach(code => {
    if (!found[code]) {
      const balEl = document.getElementById(`balVal${code}`);
      const usedEl = document.getElementById(`usedVal${code}`);
      const resEl = document.getElementById(`resVal${code}`);
      if (balEl && balEl.textContent === '...') balEl.textContent = '0';
      if (usedEl && usedEl.textContent === '...') usedEl.textContent = '0';
      if (resEl && resEl.textContent === '...') resEl.textContent = '0';
    }
  });
}

function renderLeaveBalancesError(errorMessage) {
  ['CL', 'SL', 'EL', 'CO'].forEach(code => {
    const balEl = document.getElementById(`balVal${code}`);
    const usedEl = document.getElementById(`usedVal${code}`);
    const resEl = document.getElementById(`resVal${code}`);
    if (balEl) balEl.textContent = '—';
    if (usedEl) usedEl.textContent = '—';
    if (resEl) resEl.textContent = '—';
  });
  showLeaveSectionAlert(errorMessage, 'error');
}

// ----------------------------------------------------------------------------
// B. Leave History & Cancellation
// ----------------------------------------------------------------------------

async function loadMyLeavesHistory() {
  const tbody = document.getElementById('myLeavesTableBody');
  const infoEl = document.getElementById('myLeavesPaginationInfo');
  const prevBtn = document.getElementById('myLeavesPrevBtn');
  const nextBtn = document.getElementById('myLeavesNextBtn');

  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 24px;">Loading leave requests...</td></tr>`;

  try {
    const params = new URLSearchParams({
      page: myLeavesState.page,
      page_size: myLeavesState.page_size,
    });

    const requests = await apiFetch(`/leaves/?${params.toString()}`);
    const items = Array.isArray(requests) ? requests : [];
    myLeavesState.total = items.length;

    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px;">No leave requests found. Click &quot;Apply for Leave&quot; to submit a new application.</td></tr>`;
      if (infoEl) infoEl.textContent = 'Showing 0 of 0';
      if (prevBtn) prevBtn.disabled = true;
      if (nextBtn) nextBtn.disabled = true;
      return;
    }

    let rowsHtml = '';
    items.forEach(rec => {
      let datesText = escapeHtml(rec.start_date);
      if (rec.start_date !== rec.end_date) {
        datesText += ` to ${escapeHtml(rec.end_date)}`;
      } else if (rec.is_half_day) {
        datesText += ` (${escapeHtml(rec.half_day_period || 'half-day')})`;
      }

      const daysCount = Number(rec.total_days);
      const daysText = `${rec.total_days} ${daysCount === 1 ? 'day' : 'days'}`;
      const appliedDate = rec.created_at ? new Date(rec.created_at).toLocaleDateString() : '—';
      const statusLower = (rec.status || 'PENDING').toLowerCase();
      const statusPill = `<span class="status-pill status-${statusLower}">${escapeHtml(rec.status)}</span>`;

      let actionHtml = `<span style="color: var(--text-light); font-size: 12px;">—</span>`;
      if (rec.status === 'PENDING') {
        actionHtml = `
          <button
            type="button"
            class="btn-secondary btn-cancel-leave"
            data-leave-id="${rec.id}"
            style="padding: 4px 10px; font-size: 12px; color: var(--error); border-color: #fecaca;"
            title="Cancel this pending leave request"
          >
            Cancel
          </button>
        `;
      }

      rowsHtml += `
        <tr>
          <td><strong>${escapeHtml(rec.category_name || rec.category_code)}</strong></td>
          <td>${datesText}</td>
          <td>${escapeHtml(daysText)}</td>
          <td>${escapeHtml(appliedDate)}</td>
          <td>${statusPill}</td>
          <td>${actionHtml}</td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;

    if (infoEl) {
      infoEl.textContent = `Showing ${items.length} records (Page ${myLeavesState.page})`;
    }
    if (prevBtn) prevBtn.disabled = myLeavesState.page <= 1;
    if (nextBtn) nextBtn.disabled = items.length < myLeavesState.page_size;

  } catch (err) {
    console.error('Failed to load my leave history:', err);
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--error); padding: 24px;">${escapeHtml(err.message || 'Failed to load leave history.')}</td></tr>`;
  }
}

async function handleCancelLeave(leaveId) {
  if (!leaveId) return;

  const confirmed = window.confirm(`Are you sure you want to cancel leave request #${leaveId}? This will release any reserved days back to your balance.`);
  if (!confirmed) return;

  const promptReason = window.prompt("Please provide a reason for cancellation (min 3 characters):", "Cancelled by applicant");
  if (promptReason === null) return;

  const reason = promptReason.trim();
  if (reason.length < 3) {
    alert("Cancellation reason must be at least 3 characters.");
    return;
  }

  try {
    await apiFetch(`/leaves/${leaveId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });

    showLeaveSectionAlert(`Leave request #${leaveId} was successfully cancelled.`, 'success');
    await loadLeaveBalances();
    await loadMyLeavesHistory();
  } catch (err) {
    alert(`Failed to cancel leave request: ${err.message}`);
  }
}

// ----------------------------------------------------------------------------
// C & D. Live Calculation Preview Controller
// ----------------------------------------------------------------------------

function debouncePreviewCalculation() {
  if (previewDebounceTimer) {
    clearTimeout(previewDebounceTimer);
  }

  const categorySelect = document.getElementById('leaveCategorySelect');
  const startDateInput = document.getElementById('leaveStartDate');
  const endDateInput = document.getElementById('leaveEndDate');
  const isHalfDayCheckbox = document.getElementById('leaveIsHalfDay');
  const halfDayPeriodSelect = document.getElementById('leaveHalfDayPeriod');
  const previewBox = document.getElementById('leavePreviewContainer');

  if (!categorySelect || !startDateInput || !endDateInput || !previewBox) return;

  const categoryCode = categorySelect.value;
  const startDate = startDateInput.value;
  const isHalfDay = isHalfDayCheckbox ? isHalfDayCheckbox.checked : false;
  const endDate = isHalfDay ? startDate : endDateInput.value;
  const halfDayPeriod = isHalfDay ? (halfDayPeriodSelect ? halfDayPeriodSelect.value : 'morning') : null;

  if (!categoryCode || !startDate || !endDate) {
    previewBox.style.display = 'none';
    previewBox.innerHTML = '';
    isCurrentCalculationValid = false;
    return;
  }

  if (endDate < startDate) {
    previewBox.style.display = 'block';
    previewBox.innerHTML = `
      <div class="alert alert-warning" style="margin-bottom: 0;">
        <span>End date cannot be earlier than start date.</span>
      </div>
    `;
    isCurrentCalculationValid = false;
    return;
  }

  previewDebounceTimer = setTimeout(async () => {
    activeCalculationSeq++;
    const currentSeq = activeCalculationSeq;

    previewBox.style.display = 'block';
    previewBox.innerHTML = `
      <div class="leave-calc-box" style="opacity: 0.7;">
        <div class="leave-calc-title">
          <span>Calculating day deduction...</span>
        </div>
      </div>
    `;

    const payload = {
      category_code: categoryCode,
      start_date: startDate,
      end_date: endDate,
      is_half_day: isHalfDay,
      half_day_period: halfDayPeriod,
    };

    try {
      const data = await apiFetch('/leaves/calculate', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (currentSeq !== activeCalculationSeq) return;

      isCurrentCalculationValid = true;
      lastCalculatedDays = data.total_days;

      let notesHtml = '';
      if (data.breakdown_notes && data.breakdown_notes.length > 0) {
        notesHtml = `
          <div class="leave-calc-notes">
            ${data.breakdown_notes.map(n => `<div>&bull; ${escapeHtml(n)}</div>`).join('')}
          </div>
        `;
      }

      let sandwichWarning = '';
      if (data.sandwiched_weekend_days > 0 || data.sandwiched_holiday_days > 0) {
        sandwichWarning = `
          <div style="color: var(--warning-text); font-weight: 600; margin-top: 4px;">
            Includes ${data.sandwiched_weekend_days} sandwiched weekend and ${data.sandwiched_holiday_days} holiday day(s).
          </div>
        `;
      }

      previewBox.innerHTML = `
        <div class="leave-calc-box">
          <div class="leave-calc-title">
            <span>Deduction Summary</span>
            <span class="leave-calc-metric">${escapeHtml(String(data.total_days))} Days Charged</span>
          </div>
          <div class="leave-calc-details">
            <div>Calendar Span: <strong>${data.calendar_days}</strong> days &nbsp;|&nbsp; Working Days: <strong>${data.working_days}</strong></div>
            ${sandwichWarning}
          </div>
          ${notesHtml}
        </div>
      `;
    } catch (err) {
      if (currentSeq !== activeCalculationSeq) return;
      isCurrentCalculationValid = false;
      previewBox.innerHTML = `
        <div class="alert alert-error" style="margin-bottom: 0;">
          <span>${escapeHtml(err.message || 'Cannot calculate leave for selected dates.')}</span>
        </div>
      `;
    }
  }, 250);
}

// ----------------------------------------------------------------------------
// E. Application Submission Controller
// ----------------------------------------------------------------------------

function setupLeaveControllers() {
  const btnApply = document.getElementById('btnApplyLeave');
  const closeBtn = document.getElementById('closeApplyLeaveModalBtn');
  const cancelBtn = document.getElementById('btnCancelApplyLeave');
  const form = document.getElementById('applyLeaveForm');

  const catSelect = document.getElementById('leaveCategorySelect');
  const startDateInput = document.getElementById('leaveStartDate');
  const endDateInput = document.getElementById('leaveEndDate');
  const isHalfDayCheckbox = document.getElementById('leaveIsHalfDay');
  const halfDayGroup = document.getElementById('halfDayPeriodGroup');
  const halfDaySelect = document.getElementById('leaveHalfDayPeriod');
  const reasonInput = document.getElementById('leaveReason');
  const previewBox = document.getElementById('leavePreviewContainer');

  const prevPageBtn = document.getElementById('myLeavesPrevBtn');
  const nextPageBtn = document.getElementById('myLeavesNextBtn');
  const tableBody = document.getElementById('myLeavesTableBody');

  if (btnApply) {
    btnApply.addEventListener('click', () => {
      if (form) form.reset();
      clearApplyLeaveAlert();
      if (previewBox) {
        previewBox.style.display = 'none';
        previewBox.innerHTML = '';
      }
      isCurrentCalculationValid = false;

      const todayIso = new Date().toISOString().slice(0, 10);
      if (startDateInput) startDateInput.value = todayIso;
      if (endDateInput) {
        endDateInput.value = todayIso;
        endDateInput.disabled = false;
      }
      if (halfDayGroup) halfDayGroup.style.display = 'none';

      openModal('applyLeaveModal');
    });
  }

  if (closeBtn) closeBtn.addEventListener('click', () => closeModal('applyLeaveModal'));
  if (cancelBtn) cancelBtn.addEventListener('click', () => closeModal('applyLeaveModal'));

  if (isHalfDayCheckbox) {
    isHalfDayCheckbox.addEventListener('change', () => {
      const isChecked = isHalfDayCheckbox.checked;
      if (halfDayGroup) {
        halfDayGroup.style.display = isChecked ? 'block' : 'none';
      }
      if (endDateInput) {
        if (isChecked) {
          endDateInput.value = startDateInput.value;
          endDateInput.disabled = true;
        } else {
          endDateInput.disabled = false;
        }
      }
      debouncePreviewCalculation();
    });
  }

  if (startDateInput) {
    startDateInput.addEventListener('change', () => {
      if (isHalfDayCheckbox && isHalfDayCheckbox.checked) {
        if (endDateInput) endDateInput.value = startDateInput.value;
      } else if (endDateInput && endDateInput.value < startDateInput.value) {
        endDateInput.value = startDateInput.value;
      }
      debouncePreviewCalculation();
    });
  }

  if (endDateInput) {
    endDateInput.addEventListener('change', () => {
      debouncePreviewCalculation();
    });
  }

  if (catSelect) {
    catSelect.addEventListener('change', () => {
      debouncePreviewCalculation();
    });
  }

  if (halfDaySelect) {
    halfDaySelect.addEventListener('change', () => {
      debouncePreviewCalculation();
    });
  }

  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearApplyLeaveAlert();

      const categoryCode = catSelect ? catSelect.value : '';
      const startDate = startDateInput ? startDateInput.value : '';
      const isHalfDay = isHalfDayCheckbox ? isHalfDayCheckbox.checked : false;
      const endDate = isHalfDay ? startDate : (endDateInput ? endDateInput.value : '');
      const halfDayPeriod = isHalfDay ? (halfDaySelect ? halfDaySelect.value : 'morning') : null;
      const reason = reasonInput ? reasonInput.value.trim() : '';

      if (!categoryCode) {
        showApplyLeaveAlert('Please select a leave category.');
        return;
      }
      if (!startDate || !endDate) {
        showApplyLeaveAlert('Please specify valid start and end dates.');
        return;
      }
      if (endDate < startDate) {
        showApplyLeaveAlert('End date cannot be earlier than start date.');
        return;
      }
      if (!reason || reason.length < 3) {
        showApplyLeaveAlert('Please provide a reason of at least 3 characters.');
        return;
      }
      if (!isCurrentCalculationValid) {
        showApplyLeaveAlert('Please resolve the calculation or date issues before submitting.');
        return;
      }

      const submitBtn = document.getElementById('btnSubmitLeave');
      const submitText = document.getElementById('btnSubmitLeaveText');
      const submitSpinner = document.getElementById('btnSubmitLeaveSpinner');

      if (submitBtn) submitBtn.disabled = true;
      if (submitText) submitText.style.display = 'none';
      if (submitSpinner) submitSpinner.style.display = 'inline-block';

      const payload = {
        category_code: categoryCode,
        start_date: startDate,
        end_date: endDate,
        is_half_day: isHalfDay,
        half_day_period: halfDayPeriod,
        reason: reason,
      };

      try {
        const response = await apiFetch('/leaves/', {
          method: 'POST',
          body: JSON.stringify(payload),
        });

        closeModal('applyLeaveModal');
        showLeaveSectionAlert(
          `Leave application for ${response.category_name || response.category_code} (${response.total_days} days) submitted successfully!`,
          'success'
        );

        await loadLeaveBalances();
        await loadMyLeavesHistory();
      } catch (err) {
        let msg = err.message || 'Failed to submit leave application.';
        if (err.status === 409) {
          msg = `Conflict: ${err.message}`;
        } else if (err.status === 400) {
          msg = `Validation error: ${err.message}`;
        }
        showApplyLeaveAlert(msg, 'error');
      } finally {
        if (submitBtn) submitBtn.disabled = false;
        if (submitText) submitText.style.display = 'inline-block';
        if (submitSpinner) submitSpinner.style.display = 'none';
      }
    });
  }

  if (tableBody) {
    tableBody.addEventListener('click', (e) => {
      const cancelBtn = e.target.closest('.btn-cancel-leave');
      if (cancelBtn) {
        const leaveId = cancelBtn.getAttribute('data-leave-id');
        handleCancelLeave(leaveId);
      }
    });
  }

  if (prevPageBtn) {
    prevPageBtn.addEventListener('click', () => {
      if (myLeavesState.page > 1) {
        myLeavesState.page--;
        loadMyLeavesHistory();
      }
    });
  }

  if (nextPageBtn) {
    nextPageBtn.addEventListener('click', () => {
      myLeavesState.page++;
      loadMyLeavesHistory();
    });
  }
}

// ============================================================================
// EMPLOYEE COMP-OFF CLAIM CONTROLLERS (Self-Service)
// ============================================================================

let myCompoffClaimsState = [];
let isMyCompoffLoading = false;

function showClaimCompoffAlert(message, type = 'error') {
  const container = document.getElementById('claimCompoffAlert');
  if (!container) return;
  container.innerHTML = `
    <div class="alert alert-${type}">
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

function clearClaimCompoffAlert() {
  const container = document.getElementById('claimCompoffAlert');
  if (container) container.innerHTML = '';
}

async function loadMyCompoffClaims() {
  const tbody = document.getElementById('myCompoffTableBody');
  const infoEl = document.getElementById('myCompoffPaginationInfo');
  if (!tbody) return;

  if (isMyCompoffLoading) return;
  isMyCompoffLoading = true;

  tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 24px;">Loading Comp-Off claims...</td></tr>`;

  try {
    const data = await apiFetch('/leaves/compoff/claims');
    const items = Array.isArray(data) ? data : [];
    renderMyCompoffClaims(items);
  } catch (err) {
    console.error('Failed to load my Comp-Off claims:', err);
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--error); padding: 24px;">${escapeHtml(err.message || 'Failed to load claims.')}</td></tr>`;
    if (infoEl) infoEl.textContent = 'Failed to load claims';
  } finally {
    isMyCompoffLoading = false;
  }
}

function renderMyCompoffClaims(items) {
  const tbody = document.getElementById('myCompoffTableBody');
  const infoEl = document.getElementById('myCompoffPaginationInfo');
  if (!tbody) return;

  if (!items || items.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px;">
          No compensatory off claims submitted. Click &quot;Claim Comp-Off&quot; to submit a claim for weekend or holiday work.
        </td>
      </tr>
    `;
    if (infoEl) infoEl.textContent = 'Showing 0 claims';
    return;
  }

  let rowsHtml = '';
  items.forEach(c => {
    const statusLower = (c.status || 'pending').toLowerCase();
    const statusPill = `<span class="status-pill status-${statusLower}">${escapeHtml(c.status || 'PENDING')}</span>`;
    const daysClaimedText = `${escapeHtml(String(c.days_claimed))} day`;
    const daysCreditedText = c.days_approved != null
      ? `<strong>${escapeHtml(String(c.days_approved))} day</strong>`
      : `<span style="color: var(--text-light); font-size: 12px;">—</span>`;
    const reviewRemarks = c.review_notes
      ? escapeHtml(c.review_notes)
      : `<span style="color: var(--text-light); font-size: 12px;">—</span>`;

    rowsHtml += `
      <tr>
        <td><strong>${escapeHtml(c.work_date)}</strong></td>
        <td>${daysClaimedText}</td>
        <td>${daysCreditedText}</td>
        <td style="max-width: 240px; word-break: break-word;">${escapeHtml(c.reason)}</td>
        <td>${statusPill}</td>
        <td style="max-width: 200px; word-break: break-word;">${reviewRemarks}</td>
      </tr>
    `;
  });

  tbody.innerHTML = rowsHtml;
  if (infoEl) {
    infoEl.textContent = `Showing ${items.length} claim${items.length === 1 ? '' : 's'}`;
  }
}

function setupCompoffClaimControllers() {
  const btnClaim = document.getElementById('btnClaimCompoff');
  const closeBtn = document.getElementById('closeClaimCompoffModalBtn');
  const cancelBtn = document.getElementById('btnCancelClaimCompoff');
  const form = document.getElementById('claimCompoffForm');
  const dateInput = document.getElementById('claimWorkDate');
  const daysSelect = document.getElementById('claimDaysSelect');
  const reasonInput = document.getElementById('claimReasonInput');
  const refreshBtn = document.getElementById('btnRefreshMyCompoff');

  if (btnClaim) {
    btnClaim.addEventListener('click', () => {
      if (form) form.reset();
      clearClaimCompoffAlert();

      // Prohibit future dates: set max attribute to today's date
      const todayIso = new Date().toISOString().slice(0, 10);
      if (dateInput) {
        dateInput.max = todayIso;
        dateInput.value = todayIso;
      }

      openModal('claimCompoffModal');
    });
  }

  if (closeBtn) closeBtn.addEventListener('click', () => closeModal('claimCompoffModal'));
  if (cancelBtn) cancelBtn.addEventListener('click', () => closeModal('claimCompoffModal'));

  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      await loadMyCompoffClaims();
      showLeaveSectionAlert('Comp-Off claims refreshed.', 'success');
    });
  }

  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearClaimCompoffAlert();

      const workDate = dateInput ? dateInput.value : '';
      const daysClaimedVal = daysSelect ? daysSelect.value : '1.0';
      const reason = reasonInput ? reasonInput.value.trim() : '';

      if (!workDate) {
        showClaimCompoffAlert('Please specify the date extra work was performed.');
        return;
      }

      const todayIso = new Date().toISOString().slice(0, 10);
      if (workDate > todayIso) {
        showClaimCompoffAlert('Cannot claim compensatory off for future work dates.');
        return;
      }

      const daysClaimed = parseFloat(daysClaimedVal);
      if (daysClaimed !== 0.5 && daysClaimed !== 1.0) {
        showClaimCompoffAlert('Days claimed must be either 0.5 or 1.0.');
        return;
      }

      if (!reason || reason.length < 3) {
        showClaimCompoffAlert('Please provide a work description of at least 3 characters.');
        return;
      }

      const submitBtn = document.getElementById('btnSubmitClaimCompoff');
      const submitText = document.getElementById('btnSubmitClaimText');
      const submitSpinner = document.getElementById('btnSubmitClaimSpinner');

      if (submitBtn) submitBtn.disabled = true;
      if (submitText) submitText.style.display = 'none';
      if (submitSpinner) submitSpinner.style.display = 'inline-block';

      // Safe client payload: never trust client-supplied employee_id, reviewer_id, or approval status
      const payload = {
        work_date: workDate,
        days_claimed: daysClaimed,
        reason: reason,
      };

      try {
        const response = await apiFetch('/leaves/compoff/claims', {
          method: 'POST',
          body: JSON.stringify(payload),
        });

        closeModal('claimCompoffModal');
        showLeaveSectionAlert(
          `Comp-Off claim for ${response.work_date} (${response.days_claimed} day) submitted successfully and is pending review!`,
          'success'
        );

        await loadMyCompoffClaims();
        await loadLeaveBalances();
      } catch (err) {
        let msg = err.message || 'Failed to submit compensatory off claim.';
        if (err.status === 409) {
          msg = `Conflict: ${err.message}`;
        } else if (err.status === 400) {
          msg = `Invalid claim: ${err.message}`;
        }
        showClaimCompoffAlert(msg, 'error');
      } finally {
        if (submitBtn) submitBtn.disabled = false;
        if (submitText) submitText.style.display = 'inline-block';
        if (submitSpinner) submitSpinner.style.display = 'none';
      }
    });
  }
}

// ============================================================================
// HR / ADMIN LEAVE MANAGEMENT CONTROLLERS
// ============================================================================

let adminPendingLeavesState = {
  page: 1,
  page_size: 20,
  total: 0,
};

let adminCompoffState = {
  page: 1,
  page_size: 20,
  total: 0,
};

function showAdminLeaveAlert(message, type = 'success') {
  const container = document.getElementById('leaveAdminAlert');
  if (!container) return;
  container.innerHTML = `
    <div class="alert alert-${type}">
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

function clearAdminLeaveAlert() {
  const container = document.getElementById('leaveAdminAlert');
  if (container) container.innerHTML = '';
}

// ----------------------------------------------------------------------------
// A. Pending Leave Approvals Queue
// ----------------------------------------------------------------------------

async function loadAdminPendingLeaves() {
  const tbody = document.getElementById('pendingLeavesTableBody');
  const infoEl = document.getElementById('pendingLeavesPaginationInfo');
  const prevBtn = document.getElementById('pendingLeavesPrevBtn');
  const nextBtn = document.getElementById('pendingLeavesNextBtn');

  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">Loading pending leave requests...</td></tr>`;

  try {
    const params = new URLSearchParams({
      page: adminPendingLeavesState.page,
      page_size: adminPendingLeavesState.page_size,
    });

    const requests = await apiFetch(`/leaves/admin/pending?${params.toString()}`);
    const items = Array.isArray(requests) ? requests : [];
    adminPendingLeavesState.total = items.length;

    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 32px;">No pending leave requests awaiting approval.</td></tr>`;
      if (infoEl) infoEl.textContent = 'Showing 0 of 0';
      if (prevBtn) prevBtn.disabled = true;
      if (nextBtn) nextBtn.disabled = true;
      return;
    }

    let rowsHtml = '';
    items.forEach(rec => {
      let datesText = escapeHtml(rec.start_date);
      if (rec.start_date !== rec.end_date) {
        datesText += ` to ${escapeHtml(rec.end_date)}`;
      } else if (rec.is_half_day) {
        datesText += ` (${escapeHtml(rec.half_day_period || 'half-day')})`;
      }

      const daysCount = Number(rec.total_days);
      const daysText = `${rec.total_days} ${daysCount === 1 ? 'day' : 'days'}`;
      const appliedDate = rec.created_at ? new Date(rec.created_at).toLocaleDateString() : '—';
      const statusLower = (rec.status || 'pending').toLowerCase();
      const statusPill = `<span class="status-pill status-${statusLower}">${escapeHtml(rec.status)}</span>`;

      const isSelf = currentUser && rec.employee_id === currentUser.id;
      const selfBadge = isSelf ? `<div style="font-size: 11px; color: var(--warning-text); font-weight: 600;">(Your Request)</div>` : '';

      const recJsonAttr = escapeHtml(JSON.stringify(rec));

      rowsHtml += `
        <tr>
          <td>
            <strong>${escapeHtml(rec.employee_name || '—')}</strong>
            <div style="font-size: 11.5px; color: var(--text-muted);">${escapeHtml(rec.employee_code || '')} (ID: ${rec.employee_id})</div>
            ${selfBadge}
          </td>
          <td><strong>${escapeHtml(rec.category_name || rec.category_code)}</strong></td>
          <td>${datesText}</td>
          <td>${escapeHtml(daysText)}</td>
          <td>${escapeHtml(appliedDate)}</td>
          <td>${statusPill}</td>
          <td>
            <button
              type="button"
              class="btn-secondary btn-review-leave"
              data-record="${recJsonAttr}"
              style="padding: 4px 12px; font-size: 12px;"
            >
              Review
            </button>
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;

    if (infoEl) {
      infoEl.textContent = `Showing ${items.length} pending requests (Page ${adminPendingLeavesState.page})`;
    }
    if (prevBtn) prevBtn.disabled = adminPendingLeavesState.page <= 1;
    if (nextBtn) nextBtn.disabled = items.length < adminPendingLeavesState.page_size;

  } catch (err) {
    console.error('Failed to load pending leaves queue:', err);
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--error); padding: 24px;">${escapeHtml(err.message || 'Failed to load pending requests.')}</td></tr>`;
  }
}

// ----------------------------------------------------------------------------
// B. Leave Request Review Modal Controller
// ----------------------------------------------------------------------------

function openReviewLeaveModal(rec) {
  document.getElementById('revLeaveId').value = rec.id;
  document.getElementById('revLeaveEmpId').value = rec.employee_id;
  document.getElementById('revEmpName').textContent = rec.employee_name || '—';
  document.getElementById('revEmpCode').textContent = rec.employee_code || 'ID: ' + rec.employee_id;
  document.getElementById('revCategory').textContent = rec.category_name || rec.category_code;

  let datesText = rec.start_date;
  if (rec.start_date !== rec.end_date) {
    datesText += ` to ${rec.end_date}`;
  } else if (rec.is_half_day) {
    datesText += ` (${rec.half_day_period || 'half-day'})`;
  }
  document.getElementById('revDates').textContent = datesText;
  document.getElementById('revTotalDays').textContent = `${rec.total_days} days`;
  document.getElementById('revReason').textContent = rec.reason || '—';
  document.getElementById('reviewNotesInput').value = '';
  document.getElementById('reviewLeaveAlert').innerHTML = '';

  const isSelf = currentUser && rec.employee_id === currentUser.id;
  const warnEl = document.getElementById('selfApprovalWarning');
  const btnApprove = document.getElementById('btnApproveLeave');
  const btnReject = document.getElementById('btnRejectLeave');

  if (warnEl) warnEl.style.display = isSelf ? 'block' : 'none';
  if (btnApprove) btnApprove.disabled = isSelf;
  if (btnReject) btnReject.disabled = isSelf;

  openModal('reviewLeaveModal');
}

async function handleLeaveReviewDecision(isApprove) {
  const leaveId = document.getElementById('revLeaveId').value;
  const empId = parseInt(document.getElementById('revLeaveEmpId').value, 10);
  const notesInput = document.getElementById('reviewNotesInput');
  const notes = notesInput ? notesInput.value.trim() : '';
  const alertBox = document.getElementById('reviewLeaveAlert');
  alertBox.innerHTML = '';

  if (currentUser && empId === currentUser.id) {
    alertBox.innerHTML = `<div class="alert alert-error">Anti-Self-Approval violation: You cannot approve or reject your own leave request.</div>`;
    return;
  }

  if (!isApprove && notes.length < 3) {
    alertBox.innerHTML = `<div class="alert alert-error">Please provide a meaningful rejection reason (minimum 3 characters).</div>`;
    return;
  }

  const actionText = isApprove ? 'APPROVE' : 'REJECT';
  const confirmed = window.confirm(`Are you sure you want to ${actionText} leave application #${leaveId}?`);
  if (!confirmed) return;

  const btnApprove = document.getElementById('btnApproveLeave');
  const btnReject = document.getElementById('btnRejectLeave');
  const appSpinner = document.getElementById('btnApproveLeaveSpinner');
  const appText = document.getElementById('btnApproveLeaveText');
  const rejSpinner = document.getElementById('btnRejectLeaveSpinner');
  const rejText = document.getElementById('btnRejectLeaveText');

  btnApprove.disabled = true;
  btnReject.disabled = true;
  if (isApprove) {
    if (appSpinner) appSpinner.style.display = 'inline-block';
    if (appText) appText.style.display = 'none';
  } else {
    if (rejSpinner) rejSpinner.style.display = 'inline-block';
    if (rejText) rejText.style.display = 'none';
  }

  const endpoint = isApprove
    ? `/leaves/admin/${leaveId}/approve`
    : `/leaves/admin/${leaveId}/reject`;

  try {
    await apiFetch(endpoint, {
      method: 'POST',
      body: JSON.stringify({ review_notes: notes || null }),
    });

    closeModal('reviewLeaveModal');
    showAdminLeaveAlert(
      `Leave request #${leaveId} was successfully ${isApprove ? 'approved' : 'rejected'}.`,
      'success'
    );

    await loadAdminPendingLeaves();
    if (currentUser && currentUser.id === empId) {
      await loadLeaveBalances();
    }
  } catch (err) {
    let msg = err.message || `Failed to ${isApprove ? 'approve' : 'reject'} leave.`;
    if (err.status === 400) {
      msg = `Action rejected: ${err.message}`;
    } else if (err.status === 403) {
      msg = `Forbidden: ${err.message}`;
    }
    alertBox.innerHTML = `<div class="alert alert-error">${escapeHtml(msg)}</div>`;
  } finally {
    btnApprove.disabled = false;
    btnReject.disabled = false;
    if (appSpinner) appSpinner.style.display = 'none';
    if (appText) appText.style.display = 'inline-block';
    if (rejSpinner) rejSpinner.style.display = 'none';
    if (rejText) rejText.style.display = 'inline-block';
  }
}

// ----------------------------------------------------------------------------
// C. Comp-Off Claim Management Controller
// ----------------------------------------------------------------------------

async function loadAdminCompoffClaims() {
  const tbody = document.getElementById('adminCompoffTableBody');
  const infoEl = document.getElementById('adminCompoffPaginationInfo');
  const prevBtn = document.getElementById('adminCompoffPrevBtn');
  const nextBtn = document.getElementById('adminCompoffNextBtn');

  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 24px;">Loading Comp-Off claims...</td></tr>`;

  try {
    const params = new URLSearchParams({
      page: adminCompoffState.page,
      page_size: adminCompoffState.page_size,
    });

    const claims = await apiFetch(`/leaves/admin/compoff/claims?${params.toString()}`);
    const items = Array.isArray(claims) ? claims : [];
    adminCompoffState.total = items.length;

    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px;">No compensatory off claims awaiting review.</td></tr>`;
      if (infoEl) infoEl.textContent = 'Showing 0 of 0';
      if (prevBtn) prevBtn.disabled = true;
      if (nextBtn) nextBtn.disabled = true;
      return;
    }

    let rowsHtml = '';
    items.forEach(c => {
      const isSelf = currentUser && c.employee_id === currentUser.id;
      const selfBadge = isSelf ? `<div style="font-size: 11px; color: var(--warning-text); font-weight: 600;">(Your Claim)</div>` : '';
      const statusLower = (c.status || 'pending').toLowerCase();
      const statusPill = `<span class="status-pill status-${statusLower}">${escapeHtml(c.status)}</span>`;

      const claimJsonAttr = escapeHtml(JSON.stringify(c));

      let actionHtml = `<span style="color: var(--text-light); font-size: 12px;">Reviewed</span>`;
      if (c.status === 'PENDING') {
        actionHtml = `
          <button
            type="button"
            class="btn-secondary btn-review-compoff"
            data-record="${claimJsonAttr}"
            style="padding: 4px 12px; font-size: 12px;"
          >
            Review
          </button>
        `;
      }

      rowsHtml += `
        <tr>
          <td>
            <strong>${escapeHtml(c.employee_name || '—')}</strong>
            <div style="font-size: 11.5px; color: var(--text-muted);">${escapeHtml(c.employee_code || '')} (ID: ${c.employee_id})</div>
            ${selfBadge}
          </td>
          <td>${escapeHtml(c.work_date)}</td>
          <td>${escapeHtml(String(c.days_claimed))} day</td>
          <td>${escapeHtml(c.reason)}</td>
          <td>${statusPill}</td>
          <td>${actionHtml}</td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;

    if (infoEl) {
      infoEl.textContent = `Showing ${items.length} claims (Page ${adminCompoffState.page})`;
    }
    if (prevBtn) prevBtn.disabled = adminCompoffState.page <= 1;
    if (nextBtn) nextBtn.disabled = items.length < adminCompoffState.page_size;

  } catch (err) {
    console.error('Failed to load comp-off claims:', err);
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--error); padding: 24px;">${escapeHtml(err.message || 'Failed to load claims.')}</td></tr>`;
  }
}

function openReviewCompoffModal(c) {
  document.getElementById('revClaimId').value = c.id;
  document.getElementById('revClaimEmpId').value = c.employee_id;
  document.getElementById('coRevEmpName').textContent = c.employee_name || '—';
  document.getElementById('coRevEmpCode').textContent = c.employee_code || 'ID: ' + c.employee_id;
  document.getElementById('coRevWorkDate').textContent = c.work_date;
  document.getElementById('coRevDaysClaimed').textContent = `${c.days_claimed} day`;
  document.getElementById('coRevReason').textContent = c.reason || '—';
  document.getElementById('coReviewNotesInput').value = '';
  document.getElementById('reviewCompoffAlert').innerHTML = '';

  const daysSelect = document.getElementById('coDaysApprovedSelect');
  if (daysSelect) {
    daysSelect.value = String(c.days_claimed || '1.0');
  }

  const isSelf = currentUser && c.employee_id === currentUser.id;
  const warnEl = document.getElementById('coSelfApprovalWarning');
  const btnApprove = document.getElementById('btnApproveCompoff');
  const btnReject = document.getElementById('btnRejectCompoff');

  if (warnEl) warnEl.style.display = isSelf ? 'block' : 'none';
  if (btnApprove) btnApprove.disabled = isSelf;
  if (btnReject) btnReject.disabled = isSelf;

  openModal('reviewCompoffModal');
}

async function handleCompoffReviewDecision(isApprove) {
  const claimId = document.getElementById('revClaimId').value;
  const empId = parseInt(document.getElementById('revClaimEmpId').value, 10);
  const notesInput = document.getElementById('coReviewNotesInput');
  const notes = notesInput ? notesInput.value.trim() : '';
  const daysSelect = document.getElementById('coDaysApprovedSelect');
  const daysApproved = isApprove ? parseFloat(daysSelect ? daysSelect.value : '1.0') : null;
  const alertBox = document.getElementById('reviewCompoffAlert');
  alertBox.innerHTML = '';

  if (currentUser && empId === currentUser.id) {
    alertBox.innerHTML = `<div class="alert alert-error">Anti-Self-Review violation: You cannot approve or reject your own Comp-Off claim.</div>`;
    return;
  }

  if (!isApprove && notes.length < 3) {
    alertBox.innerHTML = `<div class="alert alert-error">Please provide a review note (minimum 3 characters) explaining the rejection.</div>`;
    return;
  }

  const actionText = isApprove ? 'APPROVE and grant credit for' : 'REJECT';
  const confirmed = window.confirm(`Are you sure you want to ${actionText} Comp-Off claim #${claimId}?`);
  if (!confirmed) return;

  const btnApprove = document.getElementById('btnApproveCompoff');
  const btnReject = document.getElementById('btnRejectCompoff');
  const appSpinner = document.getElementById('btnApproveCompoffSpinner');
  const appText = document.getElementById('btnApproveCompoffText');
  const rejSpinner = document.getElementById('btnRejectCompoffSpinner');
  const rejText = document.getElementById('btnRejectCompoffText');

  btnApprove.disabled = true;
  btnReject.disabled = true;
  if (isApprove) {
    if (appSpinner) appSpinner.style.display = 'inline-block';
    if (appText) appText.style.display = 'none';
  } else {
    if (rejSpinner) rejSpinner.style.display = 'inline-block';
    if (rejText) rejText.style.display = 'none';
  }

  const endpoint = isApprove
    ? `/leaves/admin/compoff/claims/${claimId}/approve`
    : `/leaves/admin/compoff/claims/${claimId}/reject`;

  const payload = {
    days_approved: daysApproved,
    review_notes: notes || null,
  };

  try {
    await apiFetch(endpoint, {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    closeModal('reviewCompoffModal');
    showAdminLeaveAlert(
      `Comp-Off claim #${claimId} was successfully ${isApprove ? 'approved' : 'rejected'}.`,
      'success'
    );

    await loadAdminCompoffClaims();
  } catch (err) {
    alertBox.innerHTML = `<div class="alert alert-error">${escapeHtml(err.message || 'Failed to review claim.')}</div>`;
  } finally {
    btnApprove.disabled = false;
    btnReject.disabled = false;
    if (appSpinner) appSpinner.style.display = 'none';
    if (appText) appText.style.display = 'inline-block';
    if (rejSpinner) rejSpinner.style.display = 'none';
    if (rejText) rejText.style.display = 'inline-block';
  }
}

// ----------------------------------------------------------------------------
// D. Leave Balance Adjustment Controller
// ----------------------------------------------------------------------------

async function loadEmployeeOptionsForAdjustment() {
  const select = document.getElementById('adjEmployeeSelect');
  if (!select) return;

  try {
    const data = await apiFetch('/employees?limit=100');
    const items = data && Array.isArray(data.items) ? data.items : [];

    select.innerHTML = '<option value="">Select an employee...</option>' + items.map(e => `
      <option value="${e.id}">
        ${escapeHtml(e.name)} (${escapeHtml(e.employee_code || 'ID: ' + e.id)}) &mdash; ${escapeHtml(e.department || 'General')}
      </option>
    `).join('');
  } catch (err) {
    select.innerHTML = `<option value="">Failed to load employees (${escapeHtml(err.message)})</option>`;
  }
}

// ----------------------------------------------------------------------------
// E. Admin Controllers Setup
// ----------------------------------------------------------------------------

function setupAdminLeaveControllers() {
  const btnOpenAdjust = document.getElementById('btnOpenAdjustBalanceModal');
  const btnRefresh = document.getElementById('btnRefreshAdminLeaves');

  const closeReviewLeaveBtn = document.getElementById('closeReviewLeaveModalBtn');
  const cancelReviewLeaveBtn = document.getElementById('btnCancelReviewLeave');
  const btnApproveLeave = document.getElementById('btnApproveLeave');
  const btnRejectLeave = document.getElementById('btnRejectLeave');

  const closeReviewCompoffBtn = document.getElementById('closeReviewCompoffModalBtn');
  const cancelReviewCompoffBtn = document.getElementById('btnCancelReviewCompoff');
  const btnApproveCompoff = document.getElementById('btnApproveCompoff');
  const btnRejectCompoff = document.getElementById('btnRejectCompoff');

  const closeAdjustBtn = document.getElementById('closeAdjustBalanceModalBtn');
  const cancelAdjustBtn = document.getElementById('btnCancelAdjustBalance');
  const adjustForm = document.getElementById('adjustBalanceForm');

  const pendingTable = document.getElementById('pendingLeavesTableBody');
  const compoffTable = document.getElementById('adminCompoffTableBody');

  const pendingPrevBtn = document.getElementById('pendingLeavesPrevBtn');
  const pendingNextBtn = document.getElementById('pendingLeavesNextBtn');
  const compoffPrevBtn = document.getElementById('adminCompoffPrevBtn');
  const compoffNextBtn = document.getElementById('adminCompoffNextBtn');

  // Open Adjustment Modal
  if (btnOpenAdjust) {
    btnOpenAdjust.addEventListener('click', async () => {
      const form = document.getElementById('adjustBalanceForm');
      if (form) form.reset();
      const alertBox = document.getElementById('adjustBalanceAlert');
      if (alertBox) alertBox.innerHTML = '';
      const yearInput = document.getElementById('adjYearInput');
      if (yearInput) yearInput.value = new Date().getFullYear();

      await loadEmployeeOptionsForAdjustment();
      openModal('adjustBalanceModal');
    });
  }

  // Refresh Queues
  if (btnRefresh) {
    btnRefresh.addEventListener('click', async () => {
      await loadAdminPendingLeaves();
      await loadAdminCompoffClaims();
      showAdminLeaveAlert('Leave and Comp-Off queues refreshed.', 'success');
    });
  }

  // Modal Closers
  if (closeReviewLeaveBtn) closeReviewLeaveBtn.addEventListener('click', () => closeModal('reviewLeaveModal'));
  if (cancelReviewLeaveBtn) cancelReviewLeaveBtn.addEventListener('click', () => closeModal('reviewLeaveModal'));

  if (closeReviewCompoffBtn) closeReviewCompoffBtn.addEventListener('click', () => closeModal('reviewCompoffModal'));
  if (cancelReviewCompoffBtn) cancelReviewCompoffBtn.addEventListener('click', () => closeModal('reviewCompoffModal'));

  if (closeAdjustBtn) closeAdjustBtn.addEventListener('click', () => closeModal('adjustBalanceModal'));
  if (cancelAdjustBtn) cancelAdjustBtn.addEventListener('click', () => closeModal('adjustBalanceModal'));

  // Review Decisions
  if (btnApproveLeave) {
    btnApproveLeave.addEventListener('click', () => handleLeaveReviewDecision(true));
  }
  if (btnRejectLeave) {
    btnRejectLeave.addEventListener('click', () => handleLeaveReviewDecision(false));
  }

  if (btnApproveCompoff) {
    btnApproveCompoff.addEventListener('click', () => handleCompoffReviewDecision(true));
  }
  if (btnRejectCompoff) {
    btnRejectCompoff.addEventListener('click', () => handleCompoffReviewDecision(false));
  }

  // Table Event Delegation for Review Buttons
  if (pendingTable) {
    pendingTable.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn-review-leave');
      if (btn) {
        try {
          const record = JSON.parse(btn.getAttribute('data-record'));
          openReviewLeaveModal(record);
        } catch (err) {
          console.error('Failed to parse leave request record:', err);
        }
      }
    });
  }

  if (compoffTable) {
    compoffTable.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn-review-compoff');
      if (btn) {
        try {
          const record = JSON.parse(btn.getAttribute('data-record'));
          openReviewCompoffModal(record);
        } catch (err) {
          console.error('Failed to parse comp-off claim record:', err);
        }
      }
    });
  }

  // Adjustment Form Submit
  if (adjustForm) {
    adjustForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const alertBox = document.getElementById('adjustBalanceAlert');
      alertBox.innerHTML = '';

      const empIdVal = document.getElementById('adjEmployeeSelect').value;
      const catCode = document.getElementById('adjCategorySelect').value;
      const bucket = document.getElementById('adjBucketSelect').value;
      const yearVal = document.getElementById('adjYearInput').value;
      const amountVal = document.getElementById('adjAmountInput').value;
      const reasonVal = document.getElementById('adjReasonInput').value.trim();

      if (!empIdVal) {
        alertBox.innerHTML = '<div class="alert alert-error">Please select a target employee.</div>';
        return;
      }
      if (!catCode || !bucket) {
        alertBox.innerHTML = '<div class="alert alert-error">Please select category and bucket.</div>';
        return;
      }
      const amount = parseFloat(amountVal);
      if (isNaN(amount) || amount === 0) {
        alertBox.innerHTML = '<div class="alert alert-error">Adjustment amount must be a non-zero number (e.g. 1.0 or -1.0).</div>';
        return;
      }
      if (reasonVal.length < 5) {
        alertBox.innerHTML = '<div class="alert alert-error">Mandatory justification must be at least 5 characters.</div>';
        return;
      }

      const confirmed = window.confirm(
        `Confirm administrative adjustment?\n\nTarget Employee ID: ${empIdVal}\nCategory: ${catCode}\nBucket: ${bucket}\nDelta: ${amount > 0 ? '+' : ''}${amount} days\nYear: ${yearVal}\n\nThis permanently records an audited ledger transaction.`
      );
      if (!confirmed) return;

      const submitBtn = document.getElementById('btnSubmitAdjustBalance');
      const submitText = document.getElementById('btnSubmitAdjustText');
      const submitSpinner = document.getElementById('btnSubmitAdjustSpinner');

      submitBtn.disabled = true;
      if (submitText) submitText.style.display = 'none';
      if (submitSpinner) submitSpinner.style.display = 'inline-block';

      const payload = {
        employee_id: parseInt(empIdVal, 10),
        category_code: catCode,
        year: parseInt(yearVal, 10),
        bucket: bucket,
        amount: amount,
        reason: reasonVal,
      };

      try {
        await apiFetch('/leaves/admin/balances/adjust', {
          method: 'POST',
          body: JSON.stringify(payload),
        });

        closeModal('adjustBalanceModal');
        showAdminLeaveAlert(
          `Balance adjustment of ${amount > 0 ? '+' : ''}${amount} days successfully applied for employee ID ${empIdVal}.`,
          'success'
        );

        if (currentUser && currentUser.id === parseInt(empIdVal, 10)) {
          await loadLeaveBalances();
        }
      } catch (err) {
        alertBox.innerHTML = `<div class="alert alert-error">${escapeHtml(err.message || 'Failed to apply balance adjustment.')}</div>`;
      } finally {
        submitBtn.disabled = false;
        if (submitText) submitText.style.display = 'inline-block';
        if (submitSpinner) submitSpinner.style.display = 'none';
      }
    });
  }

  // Pagination Handlers
  if (pendingPrevBtn) {
    pendingPrevBtn.addEventListener('click', () => {
      if (adminPendingLeavesState.page > 1) {
        adminPendingLeavesState.page--;
        loadAdminPendingLeaves();
      }
    });
  }
  if (pendingNextBtn) {
    pendingNextBtn.addEventListener('click', () => {
      adminPendingLeavesState.page++;
      loadAdminPendingLeaves();
    });
  }

  if (compoffPrevBtn) {
    compoffPrevBtn.addEventListener('click', () => {
      if (adminCompoffState.page > 1) {
        adminCompoffState.page--;
        loadAdminCompoffClaims();
      }
    });
  }
  if (compoffNextBtn) {
    compoffNextBtn.addEventListener('click', () => {
      adminCompoffState.page++;
      loadAdminCompoffClaims();
    });
  }
}

// Global initialization
document.addEventListener('DOMContentLoaded', () => {
  initLoginPage();
  initDashboard();
  setupDirectoryFilters();
  setupEditEmployeeForm();
  setupRoleForm();
  setupStatusForm();
  setupProvisionForm();
  setupAttendancePunchListeners();
  setupMyAttendanceFilters();
  setupCompanyAttendanceFilters();
  setupCorrectionForm();
  setupLeaveControllers();
  setupCompoffClaimControllers();
  setupAdminLeaveControllers();

  // Modal close handlers on backdrop or close buttons
  document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) {
        backdrop.classList.remove('open');
      }
    });
  });
});
