/* 后端 API 客户端
 *
 * 统一约定：后端一律返回 {"ok": bool, ...}，HTTP 状态码也同步语义。
 * 这里把「HTTP 失败」与「ok:false」收敛成同一种异常，调用方只需 try/catch。
 */

const TOKEN_KEY = 'mw.token';

export const auth = {
  get token() {
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  },
  set(value) {
    try { value ? localStorage.setItem(TOKEN_KEY, value) : localStorage.removeItem(TOKEN_KEY); }
    catch (e) { /* 隐私模式下忽略 */ }
  },
  clear() { this.set(''); }
};

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload || {};
  }
}

function headers(hasBody) {
  const h = {};
  const t = auth.token;
  if (t) h['Authorization'] = 'Bearer ' + t;
  if (hasBody) h['Content-Type'] = 'application/json';
  return h;
}

function signalUnauthorized() {
  window.dispatchEvent(new CustomEvent('mw:unauthorized'));
}

async function request(method, url, body) {
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: headers(body !== undefined),
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  } catch (e) {
    throw new ApiError('网络不可达，请检查服务是否在线', 0, {});
  }

  const text = await res.text();
  let data = {};
  if (text) {
    try { data = JSON.parse(text); }
    catch (e) { data = { ok: false, error: text.slice(0, 300) }; }
  }

  if (res.status === 401) {
    signalUnauthorized();
    throw new ApiError(data.error || '登录已失效，请重新登录', 401, data);
  }
  if (!res.ok || data.ok === false) {
    throw new ApiError(data.error || data.message || ('请求失败 HTTP ' + res.status), res.status, data);
  }
  return data;
}

const get = (url) => request('GET', url);
const post = (url, body) => request('POST', url, body === undefined ? {} : body);
const put = (url, body) => request('PUT', url, body === undefined ? {} : body);
const del = (url, body) => request('DELETE', url, body === undefined ? undefined : body);

function qs(params) {
  const sp = new URLSearchParams();
  Object.keys(params || {}).forEach((k) => {
    const v = params[k];
    if (v !== undefined && v !== null && v !== '') sp.append(k, v);
  });
  const s = sp.toString();
  return s ? '?' + s : '';
}

/**
 * SSE 流式读取。
 * 不能用 EventSource —— 它既不支持 POST，也不支持自定义 Authorization 头。
 * onEvent(eventName, dataObject) 逐事件回调。
 */
export async function streamSSE(url, body, onEvent, signal) {
  const res = await fetch(url, {
    method: 'POST',
    headers: headers(true),
    body: JSON.stringify(body || {}),
    signal
  });

  if (res.status === 401) {
    signalUnauthorized();
    throw new ApiError('登录已失效，请重新登录', 401, {});
  }
  if (!res.ok) {
    let msg = '请求失败 HTTP ' + res.status;
    try {
      const t = await res.text();
      const j = JSON.parse(t);
      msg = j.error || j.message || msg;
    } catch (e) { /* 保持默认文案 */ }
    throw new ApiError(msg, res.status, {});
  }
  if (!res.body) throw new ApiError('当前浏览器不支持流式响应', 0, {});

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, '\n');

    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (!chunk.trim()) continue;

      let event = 'message';
      let dataText = '';
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataText += line.slice(5).trim();
      }
      let data = {};
      if (dataText) {
        try { data = JSON.parse(dataText); }
        catch (e) { data = { raw: dataText }; }
      }
      onEvent(event, data);
    }
  }
}

export const api = {
  // ── 鉴权 ───────────────────────────────────────────────
  authStatus: () => get('/api/auth/status'),
  login: (username, password) => post('/api/auth/login', { username, password }),
  register: (username, password) => post('/api/auth/register', { username, password }),
  logout: () => post('/api/auth/logout'),
  me: () => get('/api/auth/me'),
  changePassword: (old_password, new_password) =>
    post('/api/auth/change-password', { old_password, new_password }),
  users: () => get('/api/users'),
  deleteUser: (username) => post('/api/users/delete', { username }),

  // ── 配置 / 健康 ────────────────────────────────────────
  getConfig: () => get('/api/config'),
  saveConfig: (patch) => post('/api/config', patch),
  revealSecret: (field, payload) => post('/api/config/reveal', payload != null ? { field, ...payload } : { field }),
  testConnection: (body) => post('/api/config/test', body),

  // ── 应用令牌（v0.6 多 app_token 管理）────────────────────
  listAppTokens: () => get('/api/config/app-tokens'),
  createAppToken: (name, source) => post('/api/config/app-tokens', { name, source }),
  revokeAppToken: (name) => del('/api/config/app-tokens/' + encodeURIComponent(name)),
  health: () => get('/api/health'),

  // ── 采集 ───────────────────────────────────────────────
  collectStatus: () => get('/api/collect/status'),
  collectProgress: () => get('/api/collect/progress'),
  collectTrigger: () => post('/api/collect/trigger'),
  collectCancel: () => post('/api/collect/cancel'),
  collectBackfill: (payload) => post('/api/collect/backfill', payload),
  collectEnable: (enabled) => post('/api/collect/enable', { enabled }),
  collectConfig: (patch) => post('/api/collect/config', patch),
  collectJobs: (limit) => get('/api/collect/jobs' + qs({ limit })),
  collectJob: (id) => get('/api/collect/jobs/' + encodeURIComponent(id)),
  collectCalendar: (month) => get('/api/collect/calendar' + qs({ month })),
  collectStats: () => get('/api/collect/stats'),
  collectEvents: (params) => get('/api/collect/events' + qs(params)),

  // ── Home Assistant ─────────────────────────────────────
  haRooms: () => get('/api/ha/rooms'),
  haDiscover: () => post('/api/ha/discover'),
  haSaveRooms: (rooms, excluded_entities) =>
    post('/api/ha/rooms', { rooms, excluded_entities }),
  haState: (entity_id) => get('/api/ha/state' + qs({ entity_id })),

  // ── 行为洞察 / 模板 ────────────────────────────────────
  insights: (params) => get('/api/insights' + qs(params)),
  saveTemplate: (tpl) => post('/api/templates', tpl),
  deleteTemplate: (id) => post('/api/templates/delete', { template_id: id }),
  insightQuery: (body) => post('/api/insights/query', body),
  // v0.7 模板校验 / 停用 / 修复
  validateTemplates: (body) => post('/api/templates/validate', body || {}),
  disableTemplate: (id, disabled) => post('/api/templates/disable', { template_id: id, disabled: !!disabled }),
  fixTemplate: (id, entityIndex, opts) => post('/api/templates/fix', Object.assign({ template_id: id, entity_index: entityIndex }, opts || {})),

  // ── 记忆研究员（v0.8 定向洞察）─────────────────────────────
  researcherJobs: () => get('/api/researcher/jobs'),
  researcherSaveJob: (job) => post('/api/researcher/jobs', job),
  researcherDeleteJob: (jobId) => post('/api/researcher/jobs/delete', { job_id: jobId }),
  researcherToggleJob: (jobId, enabled) => post('/api/researcher/jobs/toggle', { job_id: jobId, enabled: !!enabled }),
  researcherRunNow: (jobId) => post('/api/researcher/run', jobId ? { job_id: jobId } : {}),
  researcherRuns: (params) => get('/api/researcher/runs' + qs(params)),

  // ── 实体身份 / 设备健康（v0.3，HA 实体动荡治理）────────
  identityDevices: () => get('/api/identity/devices'),
  identityHealth: (state) => get('/api/identity/health' + qs({ state: state || '' })),
  listMerges: () => get('/api/identity/merges'),
  splitMerge: (stable_id) => post('/api/identity/merges/split', { stable_id }),

  // ── MCP ────────────────────────────────────────────────
  mcpInfo: () => get('/api/mcp/info'),
  mcpTokens: () => get('/api/mcp/tokens'),
  mcpCreateToken: (name, kind, prefix, scopes) => post('/api/mcp/tokens', { name, kind, prefix, scopes }),
  mcpRevokeToken: (name) => post('/api/mcp/tokens/revoke', { name }),
  mcpUpdateScopes: (name, scopes) => post('/api/mcp/tokens/scopes', { name, scopes }),
  mcpAudit: (params) => get('/api/mcp/audit' + qs(params)),
  mcpSelftest: () => post('/api/mcp/selftest'),

  // ── ACP（Agent Client Protocol，拓扑 X peer-to-peer）────
  acpInfo: () => get('/api/acp/info'),
  acpSelftest: () => post('/api/acp/selftest'),
  acpTestOutbound: (payload) => post('/api/acp/test-outbound', payload),
  acpTokens: () => get('/api/acp/tokens'),
  acpCreateToken: (name) => post('/api/acp/tokens', { name }),
  acpRevokeToken: (name) => del('/api/acp/tokens', { name }),

  // ── Agent 记忆（参与式写回）─────────────────────────────
  agentMemories: (params) => get('/api/agent/memories' + qs(params)),

  // ── 家庭成员 / 生活习惯档案 ──────────────────────────────
  members: () => get('/api/members'),
  createMember: (body) => post('/api/members', body),
  updateMember: (id, body) => put('/api/members/' + encodeURIComponent(id), body),
  deleteMember: (id) => del('/api/members/' + encodeURIComponent(id)),
  mergeMember: (sourceId, targetId) =>
    post('/api/members/' + encodeURIComponent(sourceId) + '/merge', { target_id: targetId }),
  assignMemberRooms: (id, rooms) => put('/api/members/' + encodeURIComponent(id) + '/rooms', { rooms }),
  assignMemberDevices: (id, entity_ids) => put('/api/members/' + encodeURIComponent(id) + '/devices', { entity_ids }),
  addMemberTag: (id, body) => post('/api/members/' + encodeURIComponent(id) + '/tags', body),
  deleteMemberTag: (id, tag) => del('/api/members/' + encodeURIComponent(id) + '/tags/' + encodeURIComponent(tag)),
  saveAppearance: (id, appearance) => put('/api/members/' + encodeURIComponent(id) + '/appearance', { appearance }),
  agentMemoryHealth: () => get('/api/agent/memories/health'),
  agentMemoryPromote: (body) => post('/api/agent/memories/promote', body),
  agentMemoryRevoke: (memory_id) => post('/api/agent/memories/revoke', { memory_id }),
  agentMemoryRollback: (session_id) => post('/api/agent/memories/rollback', { session_id }),
  agentMemoryFeedback: (memory_id, useful) => post('/api/agent/memories/feedback', { memory_id, useful }),
  agentMemorySweep: () => post('/api/agent/memories/sweep'),
  agentMemoryRetrieve: (body) => post('/api/agent/memories/retrieve', body),
  memberInsightFeedback: (memberId) => get('/api/members/' + memberId + '/insight-feedback'),
  researcherDirectionFeedback: () => get('/api/researcher/direction-feedback'),
  arenaAnalytics: (arenaId) => get('/api/arena/analytics' + (arenaId ? '?arena_id=' + encodeURIComponent(arenaId) : '')),

  // ── 信号规则（学习策略）───────────────────────────────
  signalRules: (params) => get('/api/signal-rules' + qs(params)),
  signalTeach: (body) => post('/api/signal-rules/teach', body),
  signalRevoke: (exclusion_id) => post('/api/signal-rules/revoke', { exclusion_id }),

  // ── 视觉识别（多模态行为识别）─────────────────────────
  visionStatus: () => get('/api/vision/status'),
  visionLights: (room) => get('/api/vision/lights' + qs({ room })),
  visionCameraTest: (payload) => post('/api/vision/cameras/test', payload),
  visionTestLlm: (payload) => post('/api/vision/test-llm', payload),
  visionAnalyze: (room, force, wait) => post('/api/vision/analyze', { room, force, wait }),
  behaviors: (params) => get('/api/behaviors' + qs(params)),

  // ── 系统 / 在线更新（从 GitHub 拉取并重启）─────────────────
  systemVersion: () => get('/api/system/version'),
  systemUpdateCheck: () => get('/api/system/update/check'),
  systemUpdate: () => post('/api/system/update', {}),

  // ── LLM ────────────────────────────────────────────────
  llmModels: () => get('/api/llm/models'),
  llmPreview: (payload) => post('/api/llm/analyze/preview', payload),
  llmSaveInsight: (insight) => post('/api/llm/analyze/save', { insight }),

  chatStream: (payload, onEvent, signal) => streamSSE('/api/llm/chat', payload, onEvent, signal),
  analyzeStream: (payload, onEvent, signal) => streamSSE('/api/llm/analyze', payload, onEvent, signal)
};
