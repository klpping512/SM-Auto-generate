/**
 * SA-LogiFlow v2.0 - 公共模块
 * 提供：API 请求、侧边栏渲染、Toast、XSS 防护
 */

// ==================== XSS 防护 ====================
function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ==================== API ====================
async function apiFetch(url, options = {}) {
    const token = localStorage.getItem('token');
    if (!options.headers) options.headers = {};
    if (token) options.headers['Authorization'] = `Bearer ${token}`;
    const resp = await fetch(url, options);
    if (resp.status === 401) {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        window.location.href = '/login.html';
        throw new Error('登录已过期');
    }
    if (!resp.ok) {
        let msg = `请求失败 (${resp.status})`;
        try { const err = await resp.json(); msg = err.detail || msg; } catch {}
        throw new Error(msg);
    }
    return resp;
}

function getCurrentUser() {
    try { return JSON.parse(localStorage.getItem('user') || '{}'); }
    catch { return {}; }
}

function logout() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    window.location.href = '/login.html';
}

// ==================== Toast ====================
function showToast(message, type = 'success') {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icons = { success: 'mdi:check-circle', error: 'mdi:alert-circle', info: 'mdi:information' };
    toast.textContent = message;
    const icon = document.createElement('iconify-icon');
    icon.setAttribute('icon', icons[type] || icons.info);
    toast.prepend(icon);
    container.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; toast.style.transform = 'translateX(20px)'; setTimeout(() => toast.remove(), 300); }, 3000);
}

// ==================== Sidebar ====================
const NAV_ITEMS = [
    { section: '工作台' },
    { id: 'home', label: '经营驾驶舱', icon: 'mdi:view-dashboard', href: '/home.html' },
    { id: 'editor', label: 'AI 工作区', icon: 'mdi:square-edit-outline', href: '/editor.html' },
    { id: 'assets', label: '内容资产', icon: 'mdi:file-document-outline', href: '/assets.html' },
    { id: 'calendar', label: '发布日历', icon: 'mdi:calendar-month', href: '/calendar.html' },
    { section: '资源库' },
    { id: 'knowledge', label: '企业知识库', icon: 'mdi:bookshelf', href: '/knowledge.html' },
    { id: 'templates', label: 'Prompt 模板', icon: 'mdi:text-box-multiple-outline', href: '/templates.html' },
    { section: '运营' },
    { id: 'queue', label: '发布队列', icon: 'mdi:clock-list', href: '/queue.html' },
    { id: 'review', label: '审核中心', icon: 'mdi:clipboard-check', href: '/review.html' },
    { id: 'accounts', label: '账号管理', icon: 'mdi:account-group', href: '/accounts.html' },
    { section: '系统' },
    { id: 'config', label: '平台配置', icon: 'mdi:cog-outline', href: '/config.html' },
];

function renderSidebar(activeId) {
    const user = getCurrentUser();
    const roleLabels = { admin: '管理员', editor: '运营专员', reviewer: '审核员' };
    const displayName = escapeHtml(user.display_name || user.username || '未登录');
    const roleLabel = escapeHtml(roleLabels[user.role] || '');
    const initials = displayName.substring(0, 1).toUpperCase();

    let navHtml = '';
    for (const item of NAV_ITEMS) {
        if (item.section) {
            navHtml += `<div class="nav-section-title">${escapeHtml(item.section)}</div>`;
        } else {
            const active = item.id === activeId ? ' active' : '';
            navHtml += `<a class="nav-item${active}" href="${item.href}">
                <iconify-icon icon="${item.icon}"></iconify-icon>
                <span>${escapeHtml(item.label)}</span>
            </a>`;
        }
    }

    return `
    <aside class="sidebar">
        <div class="sidebar-header">
            <div class="sidebar-logo">
                <iconify-icon icon="mdi:truck-fast" width="20"></iconify-icon>
            </div>
            <span class="sidebar-brand">SA-LogiFlow</span>
        </div>
        <nav class="sidebar-nav">${navHtml}</nav>
        <div class="sidebar-footer">
            <div style="display:flex;align-items:center;gap:10px;padding:8px;background:var(--bg-secondary);border-radius:var(--radius-md);margin-bottom:8px;">
                <div style="width:32px;height:32px;border-radius:50%;background:var(--primary);color:white;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0;">${initials}</div>
                <div style="flex:1;min-width:0;">
                    <div style="font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${displayName}</div>
                    <div style="font-size:11px;color:var(--text-muted);">${roleLabel}</div>
                </div>
            </div>
            <button onclick="logout()" title="退出登录" style="width:100%;font-size:12px;color:var(--text-muted);background:none;border:none;cursor:pointer;padding:4px;">
                <iconify-icon icon="mdi:logout" width="18"></iconify-icon>
            </button>
        </div>
    </aside>`;
}

// 页面初始化
document.addEventListener('DOMContentLoaded', () => {
    if (!document.querySelector('link[href*="design-system.css"]')) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/static/design-system.css';
        document.head.prepend(link);
    }
});
