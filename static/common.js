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
        try {
            const err = await resp.json();
            const detail = err.detail;
            if (typeof detail === 'string' && detail.trim()) msg = detail;
            else if (detail && typeof detail.message === 'string' && detail.message.trim()) msg = detail.message;
            else if (Array.isArray(detail)) {
                const messages = detail.map(item => String(item?.msg || '')).filter(Boolean);
                if (messages.length) msg = `请求参数有误：${messages.join('；')}`;
            }
            else if (detail != null) msg = JSON.stringify(detail);
        } catch {}
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

// ==================== 平台词表 + 生产上下文 ====================
const PLATFORMS = [
    { id: 'douyin',      name: '抖音',        color: '#000000' },
    { id: 'xiaohongshu', name: '小红书',      color: '#FF2442' },
    { id: 'wechat_mp',   name: '微信公众号',  color: '#07C160' },
    { id: 'facebook',    name: 'Facebook',    color: '#1877F2' },
    { id: 'twitter',     name: 'Twitter',     color: '#1DA1F2' },
    { id: 'reddit',      name: 'Reddit',      color: '#FF4500' },
];
const PLATFORM_CONTEXT_KEY = 'logiflowPlatformContext';

function getPlatformContext() {
    const v = localStorage.getItem(PLATFORM_CONTEXT_KEY);
    return PLATFORMS.some(p => p.id === v) ? v : 'douyin';
}
function setPlatformContext(id) {
    localStorage.setItem(PLATFORM_CONTEXT_KEY, id);
}
function platformName(id) {
    const p = PLATFORMS.find(p => p.id === id);
    return p ? p.name : id;
}

// ==================== Sidebar ====================
const NAV_ITEMS = [
    { section: '核心' },
    { id: 'chat', label: 'AI 对话', href: '/chat.html' },
    { id: 'hotspots', label: '热点审核台', href: '/hotspots.html' },
    { id: 'assets', label: '内容资产', href: '/assets.html' },
    { id: 'video-project', label: '视频项目', href: '/video-project.html' },
    { id: 'articles', label: '公众号图文', href: '/articles.html' },
    { id: 'editor', label: '内容编辑器', href: '/editor.html' },
    { id: 'queue', label: '发布队列', href: '/queue.html' },
    { section: '分析' },
    { id: 'home', label: '经营驾驶舱', href: '/home.html' },
    { id: 'calendar', label: '发布日历', href: '/calendar.html' },
    { id: 'ledger', label: '发布台账', href: '/ledger.html' },
    { section: '资源' },
    { id: 'knowledge', label: '企业知识库', href: '/knowledge.html' },
    { id: 'templates', label: 'Prompt 模板', href: '/templates.html' },
    { section: '运营' },
    { id: 'accounts', label: '账号管理', href: '/accounts.html' },
    { section: '设置' },
    { id: 'config', label: '平台配置', href: '/config.html' },
];

// 内联 SVG 路径（24x24 viewBox, fill="currentColor"）— 不再依赖 iconify CDN
const NAV_ICONS = {
    'AI 对话':     '<path d="M4 3h16a2 2 0 012 2v11a2 2 0 01-2 2H9l-5 4v-4a2 2 0 01-2-2V5a2 2 0 012-2zm3 6h10V7H7v2zm0 4h7v-2H7v2z"/>',
    '热点审核台':  '<path d="M12 2a7 7 0 00-4 12.74V18h8v-3.26A7 7 0 0012 2zm2 11.5-.9.52V16h-2.2v-1.98l-.9-.52A5 5 0 1114 13.5zM9 20h6v2H9v-2z"/>',
    '经营驾驶舱': '<path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z"/>',
    '内容编辑器':  '<path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>',
    '视频项目':    '<path d="M4 4h11a2 2 0 012 2v3l5-2.5v11L17 15v3a2 2 0 01-2 2H4a2 2 0 01-2-2V6a2 2 0 012-2zm2 3v10h9V7H6z"/>',
    '公众号图文':  '<path d="M4 3h12a2 2 0 012 2v6a2 2 0 01-2 2H9l-4 4v-4H4a2 2 0 01-2-2V5a2 2 0 012-2zm2 2v4h12V5H6zm10 7h2v3h-2v-3zM2 15h2v3h3v2H2v-5zm15 1h4a1 1 0 011 1v2h2v2h-2v1a1 1 0 01-1 1h-4a1 1 0 01-1-1v-5a1 1 0 011-1zM7 9h8V7H7v2z"/>',
    '内容资产':    '<path d="M6 2a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6H6zm0 2h7v5h5v11H6V4z"/>',
    '发布日历':    '<path d="M19 4h-1V2h-2v2H8V2H6v2H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V6a2 2 0 00-2-2zm0 16H5V10h14v10zM5 8V6h14v2H5zm2 4h2v2H7v-2zm4 0h2v2h-2v-2zm-4 4h2v2H7v-2zm4 0h2v2h-2v-2z"/>',
    '发布台账':    '<path d="M19 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zm-9 14l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>',
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
    const isAdmin = user.role === 'admin';

    let navHtml = '';
    for (const item of NAV_ITEMS) {
        if (item.section) {
            navHtml += `<div class="nav-section-title">${escapeHtml(item.section)}</div>`;
        } else {
            if (item.id === 'hotspots' && !isAdmin) continue;
            const active = item.id === activeId ? ' active' : '';
            const svg = NAV_ICONS[item.label] || '';
            const badge = item.id === 'video-project'
                ? '<b class="nav-badge" id="videoProjectNavBadge" hidden>0</b>'
                : '';
            navHtml += `<a class="nav-item${active}" href="${item.href}" title="${escapeHtml(item.label)}">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">${svg}</svg>
                <span>${escapeHtml(item.label)}</span>${badge}
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
        <div class="sidebar-platform-context" style="padding:8px 12px 12px;">
            <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;">生产平台</div>
            <select id="sidebarPlatformSelect" class="select" style="width:100%;font-size:12px;padding:6px 8px;"
                    onchange="setPlatformContext(this.value); location.reload();" title="当前生产平台：全站生产上下文">
                ${PLATFORMS.map(p => `<option value="${p.id}" ${getPlatformContext() === p.id ? 'selected' : ''}>${p.name}</option>`).join('')}
            </select>
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

// ==================== Formal Douyin script / TTS helpers ====================
const FORMAL_TARGET_SECONDS = 60;
const FORMAL_MIN_SCENES = 7;
const FORMAL_MAX_SCENES = 10;
const FORMAL_MIN_DURATION_MS = 50_000;
const FORMAL_MAX_DURATION_MS = 90_000;

function defaultVoiceOptions() {
    return [
        {provider: 'mimo', id: 'mimo_default', label: 'MiMo 默认', available: true, preview_supported: true},
    ];
}

function voiceOptionValue(option) {
    return `${option.provider}:${option.id}`;
}

function parseVoiceSelection(value) {
    const raw = String(value || '').trim();
    if (!raw) return {tts_provider: 'mimo', voice: 'mimo_default'};
    const splitAt = raw.indexOf(':');
    if (splitAt <= 0) {
        // 历史遗留音色（如 Cherry）照原样交给后端归一到 MiMo 默认。
        return {tts_provider: 'mimo', voice: raw || 'mimo_default'};
    }
    return {tts_provider: raw.slice(0, splitAt), voice: raw.slice(splitAt + 1)};
}

function providerGroupLabel(provider) {
    const key = String(provider || '').toLowerCase();
    if (key === 'mimo') return 'MiMo';
    return key.toUpperCase() || '其他';
}

function voiceSelectMarkup(options, selectedValue, selectId, selectAttrs) {
    const opts = (Array.isArray(options) && options.length) ? options : defaultVoiceOptions();
    const ordered = [...opts];
    const selected = String(selectedValue || voiceOptionValue(ordered[0]));
    const attrs = selectAttrs || '';
    const groups = new Map();
    ordered.forEach((option) => {
        const key = String(option.provider || 'other').toLowerCase();
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(option);
    });
    const groupOrder = [...groups.keys()];
    const optionsHtml = groupOrder.map((key) => {
        const items = groups.get(key).map((option) => {
            const value = voiceOptionValue(option);
            const disabled = option.available === false;
            const reason = option.disabled_reason ? `（${option.disabled_reason}）` : '';
            return `<option value="${escapeHtml(value)}" ${value === selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${escapeHtml((option.label || `${option.provider} ${option.id}`) + reason)}</option>`;
        }).join('');
        return `<optgroup label="${escapeHtml(providerGroupLabel(key))}">${items}</optgroup>`;
    }).join('');
    return `<select id="${escapeHtml(selectId)}" ${attrs}>${optionsHtml}</select>`;
}

function voicePickerMarkup(options, selectedValue, selectId, {selectAttrs = 'class="select"', previewButtonId = '', hintId = ''} = {}) {
    const select = voiceSelectMarkup(options, selectedValue, selectId, selectAttrs);
    const previewId = previewButtonId || `${selectId}Preview`;
    const statusId = hintId || `${selectId}Hint`;
    return `<div class="voice-picker">
      ${select}
      <button type="button" class="btn btn-secondary btn-sm voice-preview-btn" id="${escapeHtml(previewId)}" data-voice-select="${escapeHtml(selectId)}" data-voice-hint="${escapeHtml(statusId)}">试听</button>
      <span class="voice-preview-hint" id="${escapeHtml(statusId)}" role="status"></span>
    </div>`;
}

async function previewSelectedVoice(selectId, hintId, text) {
    const select = document.getElementById(selectId);
    const hint = document.getElementById(hintId);
    if (!select) return;
    const selection = parseVoiceSelection(select.value);
    if (hint) hint.textContent = '试听生成中…';
    try {
        const response = await apiFetch('/api/media/tts-preview', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                text: text || '西开普发货前先确认路况与承运时效，再答复客户。',
                tts_provider: selection.tts_provider,
                voice: selection.voice,
            }),
        });
        const payload = await response.json();
        if (!payload?.audio_url) throw new Error('试听音频未返回');
        const audio = new Audio(payload.audio_url);
        await audio.play();
        if (hint) hint.textContent = `试听：${selection.tts_provider} / ${selection.voice}`;
    } catch (error) {
        if (hint) hint.textContent = error.message || '试听失败';
        showToast(error.message || '试听失败', 'error');
    }
}

function isLegacyVideoDraft(projectOrPayload) {
    const project = projectOrPayload || {};
    const payload = project.current_revision?.payload || project.payload || project;
    const scenes = Array.isArray(payload.scenes) ? payload.scenes : [];
    const targetMs = Number(
        project.target_duration_ms
        || payload.target_duration_ms
        || payload.duration_target_ms
        || (payload.duration_target ? Number(payload.duration_target) * 1000 : 0)
        || 0
    );
    const targetSec = Math.round(targetMs / 1000);
    if (targetSec > 0 && targetSec < 50) return true;
    if (scenes.length > 0 && scenes.length < FORMAL_MIN_SCENES) return true;
    return false;
}

function classifyDouyinScriptState({
    durationTarget,
    scenes,
    videoWorkflow,
    importedFromChat,
    isFormalPlan,
} = {}) {
    const sceneCount = Array.isArray(scenes) ? scenes.length : 0;
    const durationSec = Math.round(Number(durationTarget || 0));
    const workflowReady = videoWorkflow?.status === 'ready';
    const formalTargetSec = Math.round(Number(
        videoWorkflow?.target_duration_ms || FORMAL_TARGET_SECONDS * 1000
    ) / 1000) || FORMAL_TARGET_SECONDS;

    if (isFormalPlan) {
        const ok = sceneCount >= FORMAL_MIN_SCENES
            && sceneCount <= FORMAL_MAX_SCENES
            && formalTargetSec >= 50
            && formalTargetSec <= 90;
        return {
            mode: 'formal',
            label: `${formalTargetSec} 秒正式分镜`,
            canProduce: ok,
            blockReason: ok ? '' : `正式生产需要 ${FORMAL_MIN_SCENES}～${FORMAL_MAX_SCENES} 个镜头，且时长在 50～90 秒`,
            targetSeconds: formalTargetSec,
        };
    }

    if (importedFromChat) {
        if (durationSec > 0 && durationSec < 50) {
            return {
                mode: 'legacy',
                label: '历史预览，不可直接生产',
                canProduce: false,
                blockReason: '旧 30 秒草稿不能直接生产；请重新发起 60 秒双素材成片任务',
                targetSeconds: FORMAL_TARGET_SECONDS,
            };
        }
        return {
            mode: 'chat_preview',
            label: '聊天预览脚本（正式成片将按 60 秒双素材重新规划）',
            canProduce: workflowReady,
            blockReason: workflowReady
                ? ''
                : (videoWorkflow?.block_reason || '正式成片需要强相关热点 Hook；品牌素材不足时将自适应降级出片'),
            targetSeconds: FORMAL_TARGET_SECONDS,
        };
    }

    if ((durationSec > 0 && durationSec < 50) || (sceneCount > 0 && sceneCount < FORMAL_MIN_SCENES)) {
        return {
            mode: 'legacy',
            label: '历史预览，不可直接生产',
            canProduce: false,
            blockReason: '旧短分镜/旧时长草稿不能直接生产；正式成片需 7～10 镜且 50～90 秒',
            targetSeconds: FORMAL_TARGET_SECONDS,
        };
    }

    const ok = sceneCount >= FORMAL_MIN_SCENES
        && sceneCount <= FORMAL_MAX_SCENES
        && durationSec >= 50
        && durationSec <= 90;
    return {
        mode: ok ? 'formal' : 'chat_preview',
        label: ok ? `${durationSec || FORMAL_TARGET_SECONDS} 秒正式分镜` : '聊天预览脚本',
        canProduce: ok,
        blockReason: ok ? '' : `正式生产需要 ${FORMAL_MIN_SCENES}～${FORMAL_MAX_SCENES} 个镜头，且时长在 50～90 秒`,
        targetSeconds: durationSec >= 50 ? durationSec : FORMAL_TARGET_SECONDS,
    };
}

function formatRenderProvenance(qualityReport, fallbackScenes) {
    const report = qualityReport || {};
    const finalQuality = report.final_quality || report.preview_quality || report;
    const tts = finalQuality.tts || report.tts || {};
    const scenes = tts.scenes || [];
    const actualProviders = [...new Set(scenes.map(item => item.provider).filter(Boolean))];
    const actualVoices = [...new Set(scenes.map(item => item.voice).filter(Boolean))];
    const requestedProvider = tts.requested_provider || actualProviders[0] || '';
    const providerText = actualProviders.length
        ? actualProviders.join(' / ')
        : (requestedProvider || '未知');
    const voiceText = actualVoices.length ? actualVoices.join(' / ') : '未知';
    const usage = finalQuality.source_usage || report.source_usage || {};
    const sceneList = Array.isArray(fallbackScenes) ? fallbackScenes : [];
    const hotspotCount = Number(usage.hotspot_parent_count);
    const ownedCount = Number(usage.owned_asset_count);
    const hotspotFallback = sceneList.filter(scene => scene?.event_clip_id || scene?.evidence_type === 'hotspot_video').length;
    const ownedFallback = sceneList.filter(scene => scene?.asset_id && !scene?.event_clip_id && scene?.evidence_type !== 'hotspot_video').length;
    const sourceText = `热点 Hook 母片 ${Number.isFinite(hotspotCount) ? hotspotCount : hotspotFallback} · Buffalo 自有 ${Number.isFinite(ownedCount) ? ownedCount : ownedFallback}`;
    const adaptation = report.adaptation || finalQuality.adaptation || {};
    const adaptText = adaptation.adapted
        ? `<br/>自适应降级：${escapeHtml(adaptation.message || (adaptation.strategies || []).join('、') || '按现有库存成片')}`
        : '';
    return `<div class="render-review-summary" style="margin-top:6px;">素材来源：${escapeHtml(sourceText)}${adaptText}<br/>TTS：${escapeHtml(providerText)} · 音色 ${escapeHtml(voiceText)}</div>`;
}

// ==================== Video project nav badge (no floating panel) ====================
const VIDEO_STAGE_LABELS = {
    queued: '等待处理', topic_brief: '整理主题简报', hook_locking: '锁定热点 Hook',
    scripting: '生成正式脚本', project_building: '建项入库',
    planning: '整理脚本', script_quality_check: '检查脚本',
    asset_matching: '匹配素材', match_quality_check: '检查素材匹配',
    preview_rendering: '生成预览', preview_quality_check: '检查预览',
    final_rendering: '生成高清成片', final_quality_check: '最终质量检查', manual_accepted: '人工验收已记录（未发布）',
    succeeded: '已完成', needs_review: '需要确认', cancel_requested: '正在停止',
    canceled: '已取消', failed: '失败'
};
let videoTaskPollTimer = null;
let videoTaskPollFailures = 0;

function updateVideoProjectNavBadge(count) {
    const badge = document.getElementById('videoProjectNavBadge');
    if (!badge) return;
    const n = Number(count || 0);
    badge.textContent = String(n);
    badge.hidden = n <= 0;
}

async function refreshVideoTaskBadge() {
    if (!localStorage.getItem('token')) return [];
    try {
        const response = await apiFetch('/api/video-generation/jobs/active');
        const jobs = await response.json();
        videoTaskPollFailures = 0;
        updateVideoProjectNavBadge(jobs.length);
        const hasMovingJob = jobs.some(job => ['pending', 'running', 'cancel_requested'].includes(job.status));
        if (hasMovingJob && !videoTaskPollTimer) {
            videoTaskPollTimer = window.setInterval(refreshVideoTaskBadge, 2000);
        } else if (!hasMovingJob && videoTaskPollTimer) {
            window.clearInterval(videoTaskPollTimer);
            videoTaskPollTimer = null;
        }
        window.dispatchEvent(new CustomEvent('video-tasks-updated', {detail: jobs}));
        return jobs;
    } catch (error) {
        videoTaskPollFailures += 1;
        if (videoTaskPollFailures >= 3 && videoTaskPollTimer) {
            window.clearInterval(videoTaskPollTimer);
            videoTaskPollTimer = null;
        }
        return [];
    }
}

/** @deprecated Use refreshVideoTaskBadge — kept for callers during migration */
async function refreshVideoTaskCenter() {
    return refreshVideoTaskBadge();
}

async function cancelVideoGeneration(jobId) {
    try {
        await apiFetch(`/api/video-generation/jobs/${encodeURIComponent(jobId)}/cancel`, {method: 'POST'});
        showToast('已提交取消请求', 'info');
        await refreshVideoTaskBadge();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function initVideoTaskBadge() {
    if (!localStorage.getItem('token')) return;
    refreshVideoTaskBadge();
}

// 页面初始化
document.addEventListener('DOMContentLoaded', () => {
    if (!document.querySelector('link[href*="design-system.css"]')) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/static/design-system.css?v=5';
        document.head.prepend(link);
    }
    initVideoTaskBadge();
    document.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-voice-select]');
        if (!button) return;
        event.preventDefault();
        previewSelectedVoice(button.getAttribute('data-voice-select'), button.getAttribute('data-voice-hint'));
    });
});
