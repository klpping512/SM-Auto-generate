/**
 * SA-LogiFlow v2.0 - 前端公共模块
 * 提供：API 请求封装（自动带 JWT）、用户面板、登出
 */

// 带认证的 fetch 封装
async function apiFetch(url, options = {}) {
    const token = localStorage.getItem('token');
    if (!options.headers) options.headers = {};
    if (token) {
        options.headers['Authorization'] = `Bearer ${token}`;
    }
    const resp = await fetch(url, options);
    if (resp.status === 401) {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        window.location.href = '/login.html';
        throw new Error('登录已过期');
    }
    return resp;
}

// 获取当前用户信息
function getCurrentUser() {
    try {
        return JSON.parse(localStorage.getItem('user') || '{}');
    } catch {
        return {};
    }
}

// 登出
function logout() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    window.location.href = '/login.html';
}

// 初始化用户面板（侧边栏底部）
function initUserPanel() {
    const panel = document.getElementById('userPanel');
    if (!panel) return;

    const user = getCurrentUser();
    if (!user.username) {
        panel.innerHTML = `<a href="/login.html" class="block text-center text-sm text-blue-600 hover:underline py-2">登录</a>`;
        return;
    }

    const roleLabels = {admin: '管理员', editor: '运营专员', reviewer: '审核员'};
    const roleLabel = roleLabels[user.role] || user.role;
    const initials = (user.display_name || user.username).substring(0, 2).toUpperCase();

    panel.innerHTML = `
        <div class="flex items-center gap-3 p-2 bg-gray-50 rounded-lg mb-2">
            <div class="w-8 h-8 rounded-full bg-blue-500 flex items-center justify-center text-white text-xs font-bold">${initials}</div>
            <div class="flex-1 min-w-0">
                <p class="text-sm font-medium truncate">${user.display_name || user.username}</p>
                <p class="text-xs text-gray-500 truncate">${roleLabel}</p>
            </div>
        </div>
        <button onclick="logout()" class="w-full text-xs text-gray-500 hover:text-red-600 py-1 text-center">退出登录</button>
    `;
}

// 页面加载时检查登录状态并初始化用户面板
document.addEventListener('DOMContentLoaded', () => {
    initUserPanel();
});
