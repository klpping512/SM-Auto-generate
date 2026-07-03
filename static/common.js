/**
 * SA-LogiFlow v3.0 - 公共模块
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
    { section: '核心' },
    { id: 'chat', label: 'AI 对话', href: '/chat.html' },
    { id: 'editor', label: '内容编辑器', href: '/editor.html' },
    { id: 'queue', label: '发布队列', href: '/queue.html' },
    { section: '分析' },
    { id: 'home', label: '经营驾驶舱', href: '/home.html' },
    { id: 'assets', label: '内容资产', href: '/assets.html' },
    { id: 'calendar', label: '发布日历', href: '/calendar.html' },
    { section: '资源' },
    { id: 'knowledge', label: '企业知识库', href: '/knowledge.html' },
    { id: 'templates', label: 'Prompt 模板', href: '/templates.html' },
    { section: '运营' },
    { id: 'review', label: '审核中心', href: '/review.html' },
    { id: 'accounts', label: '账号管理', href: '/accounts.html' },
    { section: '设置' },
    { id: 'config', label: '平台配置', href: '/config.html' },
];

// 内联 SVG 路径（24x24 viewBox, fill="currentColor"）— 不再依赖 iconify CDN
const NAV_ICONS = {
    'AI 对话':     '<path d="M4 3h16a2 2 0 012 2v11a2 2 0 01-2 2H9l-5 4v-4a2 2 0 01-2-2V5a2 2 0 012-2zm3 6h10V7H7v2zm0 4h7v-2H7v2z"/>',
    '经营驾驶舱': '<path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z"/>',
    '内容编辑器':  '<path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>',
    '内容资产':    '<path d="M6 2a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6H6zm0 2h7v5h5v11H6V4z"/>',
    '发布日历':    '<path d="M19 4h-1V2h-2v2H8V2H6v2H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V6a2 2 0 00-2-2zm0 16H5V10h14v10zM5 8V6h14v2H5zm2 4h2v2H7v-2zm4 0h2v2h-2v-2zm-4 4h2v2H7v-2zm4 0h2v2h-2v-2z"/>',
    '企业知识库':  '<path d="M6 22v-4.3l-2.6-2.6L2 16.5V22h4zm14 0v-5.5l-1.4-1.4-2.6 2.6V22h4zM12 2L2 7v1h20V7l-10-5zM2 8v2.5l1.4 1.4 2.6-2.6-4-1.3zm10 3l-5-1.6 3.4-3.4L15 9.4l-3-1.6V11zm10-1l-4 1.3 2.6 2.6L22 10.5V8zm0-1h-5l3-3-2-1-4 4-4-4-2 1 3 3H2v1l5 1.6 3 1v4h4v-4l3-1 5-1.6V9z"/>',
    'Prompt 模板':'<path d="M4 6H2v14a2 2 0 002 2h14v-2H4V6zm16-4H8a2 2 0 00-2 2v12a2 2 0 002 2h12a2 2 0 002-2V4a2 2 0 00-2-2zm0 14H8V4h12v12zm-3-7H11v2h6V9zm0-3h-6v2h6V6zm0 6h-6v2h6v-2z"/>',
    '发布队列':    '<path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67V7z"/>',
    '审核中心':    '<path d="M19 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zm-9 14l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>',
    '账号管理':    '<path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>',
    '平台配置':    '<path d="M19.14 12.94a8.003 8.003 0 000-1.88l1.86-1.41a.45.45 0 00.1-.56l-1.76-3.04a.45.45 0 00-.55-.19l-2.22.89a7.003 7.003 0 00-1.62-.94l-.34-2.33a.45.45 0 00-.44-.37h-3.53a.45.45 0 00-.44.37l-.34 2.33c-.6.24-1.15.56-1.63.95l-2.22-.89a.45.45 0 00-.55.19l-1.76 3.04a.45.45 0 00.1.56l1.86 1.41c-.05.62-.05 1.25 0 1.88l-1.86 1.41a.45.45 0 00-.1.56l1.76 3.04c.12.2.36.28.55.19l2.22-.89a7.01 7.01 0 001.63.95l.34 2.33c.04.22.24.37.44.37h3.53c.2 0 .4-.15.44-.37l.34-2.33c.6-.24 1.15-.56 1.63-.95l2.22.89c.2.09.43.01.55-.19l1.76-3.04a.45.45 0 00-.1-.56l-1.86-1.41zM12 15c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3z"/>',
};

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
            const svg = NAV_ICONS[item.label] || '';
            navHtml += `<a class="nav-item${active}" href="${item.href}" title="${escapeHtml(item.label)}">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">${svg}</svg>
                <span>${escapeHtml(item.label)}</span>
            </a>`;
        }
    }

    return `
    <aside class="sidebar">
        <div class="sidebar-header">
            <a class="sidebar-logo-lockup" href="/chat.html" aria-label="Buffalo SA-LogiFlow 首页">
                <img class="sidebar-logo-image" src="/static/icons/buffalo_logo_header.png?v=1" alt="Buffalo Logo"/>
                <span class="sidebar-product-name">SA-LOGIFLOW · CONTENT OPERATIONS</span>
            </a>
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
                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10 17v-2h4V9h-4V7l5 5-5 5zM4 3h7a2 2 0 012 2v2h-2V5H4v14h7v-2h2v2a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2z"/></svg>
            </button>
        </div>
    </aside>`;
}

// 页面初始化
document.addEventListener('DOMContentLoaded', () => {
    if (!document.querySelector('link[href*="design-system.css"]')) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/static/design-system.css?v=5';
        document.head.prepend(link);
    }
});
