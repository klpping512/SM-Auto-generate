(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.EditorTransfer = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    function tags(value) {
        if (Array.isArray(value)) return value.map(String).map(s => s.trim().replace(/^#/, '')).filter(Boolean);
        return String(value || '').split(',').map(s => s.trim().replace(/^#/, '')).filter(Boolean);
    }

    function content(item, fallbackPlatform) {
        if (!item) return null;
        const platform = item.platform || fallbackPlatform;
        const title = item.title || '';
        const body = item.body || item.content || '';
        if (!platform || (!title && !body)) return null;
        return { platform, title, body, hashtags: tags(item.hashtags) };
    }

    function buildDraft(result, activeIndex, fallbackPlatforms) {
        const outputs = Array.isArray(result && result.outputs) && result.outputs.length ? result.outputs : [result];
        const contents = outputs.map((item, index) => content(item, (fallbackPlatforms || [])[index])).filter(Boolean);
        if (!contents.length) return null;
        const requested = content(outputs[activeIndex], (fallbackPlatforms || [])[activeIndex]);
        const selected = requested && contents.find(item => item === requested || item.platform === requested.platform) || contents[0];
        return {
            version: 2,
            source: 'chat',
            activePlatform: selected.platform,
            contents,
            title: selected.title,
            body: selected.body,
            hashtags: selected.hashtags,
            platforms: contents.map(item => item.platform),
        };
    }

    function normalizeDraft(draft, validPlatforms, fallbackPlatform) {
        if (!draft || typeof draft !== 'object') return { valid: false, contents: [], activePlatform: fallbackPlatform, importedFromChat: false };
        const allowed = new Set(validPlatforms || []);
        let contents = Array.isArray(draft.contents)
            ? draft.contents.map(item => content(item)).filter(item => item && allowed.has(item.platform))
            : [];
        if (!contents.length && (draft.title || draft.body)) {
            const targets = Array.isArray(draft.platforms) && draft.platforms.length ? draft.platforms : [fallbackPlatform];
            contents = targets.filter(platform => allowed.has(platform)).map(platform => content({
                platform, title: draft.title, body: draft.body, hashtags: draft.hashtags,
            })).filter(Boolean);
        }
        const available = contents.map(item => item.platform);
        const activePlatform = available.includes(draft.activePlatform)
            ? draft.activePlatform
            : (available.includes(fallbackPlatform) ? fallbackPlatform : (available[0] || fallbackPlatform));
        return { valid: contents.length > 0, contents, activePlatform, importedFromChat: draft.source === 'chat' };
    }

    return { buildDraft, normalizeDraft };
});
