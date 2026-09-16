/* 导航单一真源：侧栏菜单、移动端 Tab、路由白名单全部来自这里。
 *
 * 历史坑：路由白名单原本在 store.js 里另写一份 ROUTES 数组，与 main.js 的 NAV
 * 手工同步；新增 tab 时漏加白名单会把页面静默 fallback 到 dashboard（用户手册
 * 就是这么"跳回概览"的）。这里把两者收敛成一份，杜绝再次漏加。
 */

const ic = (path) =>
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
  'stroke-linecap="round" stroke-linejoin="round" class="w-full h-full">' + path + '</svg>';

export const NAV = [
  {
    id: 'dashboard', label: '概览', desc: '家庭记忆中枢运行全貌',
    icon: ic('<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>')
  },
  {
    id: 'collect', label: '数据采集', desc: '从 Home Assistant 抓取并沉淀行为事件',
    icon: ic('<path d="M21 12a9 9 0 11-6.22-8.56"/><path d="M12 7v5l3 2"/><path d="M17 3l4 1-1 4"/>')
  },
  {
    id: 'assistant', label: 'AI 助手', desc: '与内置大模型对话、生成行为洞察',
    icon: ic('<path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/><circle cx="9" cy="10" r="1"/><circle cx="12.5" cy="10" r="1"/><circle cx="16" cy="10" r="1"/>')
  },
  {
    id: 'insights', label: '行为洞察', desc: '沉淀下来的规律与自动化建议',
    icon: ic('<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"/>')
  },
  {
    id: 'researcher', label: '定向洞察', desc: '记忆研究员：LLM 按方向/区域/对象定期自动挖洞察',
    icon: ic('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/><path d="M8 11h6M11 8v6"/>')
  },
  {
    id: 'devices', label: '设备身份', desc: '逻辑设备与失效实体治理（HA 动荡自愈）',
    icon: ic('<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M12 22V12"/><path d="M3 7l9 5 9-5"/>')
  },
  {
    id: 'members', label: '家庭成员', desc: '为每个家人建立生活习惯档案',
    icon: ic('<path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/>')
  },
  {
    id: 'mcp', label: 'MCP 接入', desc: '为 AI Agent 签发访问凭证',
    icon: ic('<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/><path d="M2 13h20"/>')
  },
  {
    id: 'acp', label: 'ACP 接入', desc: '与 autoflow 双向互通（拓扑 X）',
    icon: ic('<path d="M12 2a3 3 0 00-3 3v1H6a3 3 0 000 6h3v1a3 3 0 006 0v-1h3a3 3 0 000-6h-3V5a3 3 0 00-3-3z"/><circle cx="12" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/>')
  },
  {
    id: 'agent_memory', label: 'Agent 记忆', desc: '参与式写回的向量记忆库',
    icon: ic('<path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"/>')
  },
  {
    id: 'signal_rules', label: '信号规则', desc: '学习策略：教系统别把自动化信号误判',
    icon: ic('<path d="M12 2l9 4.5v5c0 5-3.4 8.5-9 11-5.6-2.5-9-6-9-11v-5z"/><path d="M9 12l2 2 4-4"/>')
  },
  {
    id: 'vision', label: '视觉识别', desc: '摄像头行为识别与多模态设置',
    icon: ic('<path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/>')
  },
  {
    id: 'manual', label: '用户手册', desc: 'MCP 全功能问答指南：每条 Q 都能直接对 AI 说',
    icon: ic('<path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/><path d="M9 7h7M9 11h5"/>')
  },
  {
    id: 'settings', label: '系统设置', desc: '连接、模型与账号管理',
    icon: ic('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 008 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06A1.65 1.65 0 004.6 15a1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06A1.65 1.65 0 009 4.6a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06-.06A1.65 1.65 0 0019.4 9c.14.35.4.64.73.83.3.17.64.26.98.26H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"/>')
  }
];

/** 合法路由集合（由 NAV 派生，不再手工维护第二份白名单） */
export const ROUTE_IDS = NAV.map((n) => n.id);

/** 底部 Tab「更多」入口图标 */
export const MORE_ICON = ic('<circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="19" r="1.4"/>');
