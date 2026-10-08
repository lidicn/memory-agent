/* 系统设置页：连接配置 / 大模型 / 存储 / 账号管理 / 健康检查 */

import { api } from '../api.js';
import { fmtNum, fmtTime, copyText } from '../util.js';

/* 测试结果面板。
 * 原先测试只弹一个转瞬即逝的 toast，用户根本没法判断「到底通没通」，
 * 更看不到向量库是卡在连接、写入还是检索。这里把结果常驻在按钮下方，
 * 并逐步骤展开耗时与错误明细。 */
const resultBox = (type) => `
  <div class="mt-3" x-show="testResult['${type}']" x-cloak>
    <div class="rounded-lg px-3 py-2 text-[11px] leading-relaxed border"
         :class="testResult['${type}'] && testResult['${type}'].ok
           ? 'bg-ok/10 border-ok/25 text-ok'
           : 'bg-danger/10 border-danger/25 text-danger'">
      <div class="flex items-start gap-1.5">
        <span class="shrink-0" x-text="testResult['${type}'].ok ? '✓' : '✕'"></span>
        <span class="flex-1 break-all" x-text="testResult['${type}'].message"></span>
        <span class="shrink-0 text-txt-3" x-text="testResult['${type}'].at"></span>
      </div>
      <template x-if="(testResult['${type}'].steps || []).length">
        <div class="mt-1.5 pt-1.5 space-y-0.5 border-t border-white/10">
          <template x-for="s in testResult['${type}'].steps" :key="s.step">
            <div class="flex items-center gap-1.5 font-mono text-[10px]">
              <span class="shrink-0" :class="s.ok ? 'text-ok' : 'text-danger'" x-text="s.ok ? '✓' : '✕'"></span>
              <span class="text-txt-2 shrink-0" x-text="s.step"></span>
              <span class="text-txt-3 shrink-0" x-text="s.ms + 'ms'"></span>
              <span class="text-txt-3 truncate" :title="s.detail" x-text="s.detail"></span>
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>`;

const TPL = `
<div class="grid lg:grid-cols-3 gap-5 items-start">

  <!-- 左：配置表单 -->
  <div class="lg:col-span-2 space-y-5">

    <!-- Home Assistant -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg grad-aqua grid place-items-center">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M3 12l9-9 9 9"/><path d="M5 10v10h14V10"/></svg>
          </div>
          <h3 class="font-semibold">Home Assistant</h3>
        </div>
        <button class="btn-ghost btn-xs" @click="test('ha')" :disabled="testing.ha">
          <span x-show="testing.ha" class="spinner"></span><span>测试连接</span>
        </button>
      </div>
      <div class="grid sm:grid-cols-2 gap-4">
        <div>
          <label class="lbl">服务地址</label>
          <input class="inp inp-mono" x-model="cfg.hass_server" placeholder="http://192.168.2.200:8123">
        </div>
        <div>
          <label class="lbl">长期访问令牌</label>
          <div class="flex gap-2">
            <input class="inp inp-mono" :type="revealed.hass_token ? 'text' : 'password'" x-model="cfg.hass_token">
            <button class="btn-ghost shrink-0 px-2.5" @click="reveal('hass_token')" title="查看明文">
              <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
        </div>
      </div>
      <p class="hint" x-text="'时区偏移 ' + (cfg.tz_offset_hours||0) + ' 小时 · 首次采集回溯 ' + (cfg.first_run_lookback_hours||0) + ' 小时'"></p>
      <div class="grid sm:grid-cols-2 gap-4 mt-3">
        <div><label class="lbl">时区偏移（小时）</label><input type="number" class="inp" x-model.number="cfg.tz_offset_hours"></div>
        <div><label class="lbl">首次采集回溯（小时）</label><input type="number" class="inp" x-model.number="cfg.first_run_lookback_hours"></div>
      </div>
      ${resultBox('ha')}
    </div>

    <!-- HA 数据库（方案B 采集源） -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg grad-aqua grid place-items-center">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>
          </div>
          <h3 class="font-semibold">HA 数据库（采集源直连）</h3>
        </div>
        <button class="btn-ghost btn-xs" @click="test('ha_db')" :disabled="testing.ha_db">
          <span x-show="testing.ha_db" class="spinner"></span><span>测试连接</span>
        </button>
      </div>
      <div class="mb-3 rounded-lg bg-brand/10 border border-brand/20 px-3 py-2 text-[11px] leading-relaxed text-txt-2">
        <span class="font-semibold text-brand">这是「采集数据源」切换，不是取代采集。</span>
        启用后，采集直接从 HA MariaDB 读取（更快更稳，支持分钟级同步）；
        数据<strong>仍会落本地库</strong>，供 LLM / MCP / Node-RED / 洞察使用。关闭则回退 REST API。
      </div>
      <label class="flex items-center gap-2.5 cursor-pointer mb-4">
        <div class="switch scale-90" :class="cfg.ha_db_enabled && 'on'" @click="cfg.ha_db_enabled = !cfg.ha_db_enabled"></div>
        <span class="text-xs text-txt-2">启用直连 MariaDB 作为采集源（关闭则回退 REST API）</span>
      </label>
      <div class="grid sm:grid-cols-2 gap-4">
        <div><label class="lbl">主机</label><input class="inp inp-mono" x-model="cfg.ha_db_host" placeholder="192.168.2.200"></div>
        <div><label class="lbl">端口</label><input type="number" class="inp" x-model.number="cfg.ha_db_port"></div>
        <div><label class="lbl">数据库名</label><input class="inp inp-mono" x-model="cfg.ha_db_name"></div>
        <div><label class="lbl">用户名</label><input class="inp inp-mono" x-model="cfg.ha_db_user"></div>
        <div class="sm:col-span-2">
          <label class="lbl">只读密码</label>
          <div class="flex gap-2">
            <input class="inp inp-mono" :type="revealed.ha_db_password ? 'text' : 'password'" x-model="cfg.ha_db_password">
            <button class="btn-ghost shrink-0 px-2.5" @click="reveal('ha_db_password')" title="查看明文">
              <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
          <p class="hint">仅授予 <span class="font-mono">SELECT</span> 权限的只读账号，切勿复用 recorder 写库账号</p>
        </div>
        <div><label class="lbl">查询批量（实体/批）</label><input type="number" class="inp" x-model.number="cfg.ha_db_query_batch"></div>
        <div><label class="lbl">查询超时（秒）</label><input type="number" class="inp" x-model.number="cfg.ha_db_query_timeout"></div>
      </div>
      ${resultBox('ha_db')}
    </div>

    <!-- 大模型代理池 -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg grad-violet grid place-items-center">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M12 2a5 5 0 015 5v1a4 4 0 010 8v1a5 5 0 01-10 0v-1a4 4 0 010-8V7a5 5 0 015-5z"/></svg>
          </div>
          <div>
            <h3 class="font-semibold">内置大模型代理池</h3>
            <p class="text-[11px] text-txt-3">按列表顺序依次尝试，遇限流 / 超时自动切换下一个（解决智谱 429 等）</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button class="btn-ghost btn-xs" @click="test('llm')" :disabled="testing.llm">
            <span x-show="testing.llm" class="spinner"></span><span>测试全部</span>
          </button>
          <button class="btn-soft btn-xs" @click="addBackend()">+ 新增后端</button>
        </div>
      </div>

      <div class="space-y-3">
        <template x-for="(b, i) in (cfg.llm_backends || [])" :key="'b'+i">
          <div class="rounded-lg border border-white/10 p-3 space-y-3" :class="!b.enabled && 'opacity-60'">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2 min-w-0">
                <span class="badge badge-mute shrink-0" x-text="'#' + (i+1)"></span>
                <span class="text-xs font-medium truncate" x-text="b.name || b.model || '未命名后端'"></span>
                <span class="badge shrink-0" :class="b.enabled ? 'badge-ok' : 'badge-mute'" x-text="b.enabled ? '启用' : '停用'"></span>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <button class="icon-btn" @click="moveBackend(i, -1)" :disabled="i===0" title="上移"><svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 15l-6-6-6 6"/></svg></button>
                <button class="icon-btn" @click="moveBackend(i, 1)" :disabled="i===(cfg.llm_backends||[]).length-1" title="下移"><svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg></button>
                <button class="icon-btn hover:!text-danger" @click="removeBackend(i)" title="删除"><svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg></button>
              </div>
            </div>
            <div class="grid sm:grid-cols-2 gap-3">
              <div><label class="lbl">显示名</label><input class="inp" x-model="b.name" placeholder="主用 / 备用 …"></div>
              <div><label class="lbl">供应商标识</label><input class="inp" x-model="b.provider" placeholder="zhipu / deepseek …"></div>
              <div><label class="lbl">模型名</label><input class="inp inp-mono" x-model="b.model" placeholder="glm-4.5"></div>
              <div><label class="lbl">API 地址</label><input class="inp inp-mono" x-model="b.api_url" placeholder="https://open.bigmodel.cn/api/paas/v4"></div>
              <div class="sm:col-span-2">
                <label class="lbl">API Key</label>
                <div class="flex gap-2">
                  <input class="inp inp-mono" :type="b.__revealed ? 'text' : 'password'" x-model="b.api_key" placeholder="留空/掩码=不改动">
                  <button class="btn-ghost shrink-0 px-2.5" @click="revealBackend(i)" title="查看明文"><svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg></button>
                </div>
              </div>
              <div><label class="lbl">温度</label><input type="number" step="0.1" min="0" max="2" class="inp" x-model.number="b.temperature"></div>
              <div><label class="lbl">最大输出 tokens</label><input type="number" class="inp" x-model.number="b.max_tokens"></div>
              <div><label class="lbl">超时（秒）</label><input type="number" class="inp" x-model.number="b.timeout"></div>
              <div class="flex items-center gap-2.5 pt-5">
                <div class="switch scale-90" :class="b.enabled && 'on'" @click="b.enabled = !b.enabled"></div>
                <span class="text-xs text-txt-2">启用此后端</span>
                <button class="btn-soft btn-xs ml-auto" @click="testBackend(i)" :disabled="testing['llm_b'+i]">
                  <span x-show="testing['llm_b'+i]" class="spinner"></span><span>测试这条</span>
                </button>
              </div>
            </div>
          </div>
        </template>
        <p x-show="!(cfg.llm_backends || []).length" class="text-xs text-txt-3">尚未配置任何后端，请点击「+ 新增后端」。</p>
      </div>

      ${resultBox('llm')}
      <template x-if="testResult.llm && testResult.llm.backends">
        <div class="mt-2 space-y-1">
          <template x-for="bd in testResult.llm.backends" :key="bd.model + (bd.endpoint||'')">
            <div class="text-[11px] flex items-center gap-2" :class="bd.connected ? 'text-ok' : 'text-danger'">
              <span class="shrink-0" x-text="bd.connected ? '✓' : '✕'"></span>
              <span class="shrink-0 font-medium" x-text="bd.name || bd.model"></span>
              <span class="text-txt-3 shrink-0" x-text="bd.model"></span>
              <span class="text-txt-3 flex-1 truncate text-right" x-text="bd.error || bd.endpoint"></span>
            </div>
          </template>
        </div>
      </template>
    </div>

    <!-- Node-RED + 向量库 -->
    <div class="grid sm:grid-cols-2 gap-5">
      <div class="card p-5">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold text-sm">Node-RED</h3>
          <button class="btn-ghost btn-xs" @click="test('nr')" :disabled="testing.nr">
            <span x-show="testing.nr" class="spinner"></span><span>测试</span>
          </button>
        </div>
        <div class="space-y-3">
          <div><label class="lbl">地址</label><input class="inp inp-mono" x-model="cfg.nr_url" placeholder="http://192.168.2.200:1880"></div>
          <div class="grid grid-cols-2 gap-3">
            <div><label class="lbl">用户名</label><input class="inp" x-model="cfg.nr_user"></div>
            <div>
              <label class="lbl">密码</label>
              <input class="inp" :type="revealed.nr_pass ? 'text' : 'password'" x-model="cfg.nr_pass"
                     @dblclick="reveal('nr_pass')" title="双击查看明文">
            </div>
          </div>
        </div>
        ${resultBox('nr')}
      </div>

      <div class="card p-5">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold text-sm">向量库（可选）</h3>
          <button class="btn-ghost btn-xs" @click="test('chroma')" :disabled="testing.chroma">
            <span x-show="testing.chroma" class="spinner"></span><span>测试</span>
          </button>
        </div>
        <div class="space-y-3">
          <div class="grid grid-cols-3 gap-3">
            <div class="col-span-2"><label class="lbl">Chroma 主机</label><input class="inp inp-mono" x-model="cfg.chroma_host"></div>
            <div><label class="lbl">端口</label><input type="number" class="inp" x-model.number="cfg.chroma_port"></div>
          </div>
          <label class="flex items-center gap-2.5 cursor-pointer pt-1">
            <div class="switch scale-90" :class="cfg.chroma_mirror && 'on'" @click="cfg.chroma_mirror = !cfg.chroma_mirror"></div>
            <span class="text-xs text-txt-2">同步镜像事件到向量库（供语义检索）</span>
          </label>
          <p class="hint">测试会真实执行「写入探针 → 语义检索 → 清理」往返，能确认向量库是否真的在工作</p>
        </div>
        ${resultBox('chroma')}
      </div>
    </div>

    <!-- 存储 -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <h3 class="font-semibold text-sm">存储</h3>
        <button class="btn-ghost btn-xs" @click="test('db')" :disabled="testing.db">
          <span x-show="testing.db" class="spinner"></span><span>检查</span>
        </button>
      </div>
      <div class="grid sm:grid-cols-2 gap-4">
        <div>
          <label class="lbl">事件数据库路径</label>
          <input class="inp inp-mono" readonly :value="cfg.db_path || ''">
        </div>
        <div>
          <label class="lbl">数据保留天数（0＝永久）</label>
          <input type="number" min="0" class="inp" x-model.number="cfg.data_retention_days">
        </div>
      </div>
      ${resultBox('db')}
    </div>

    <!-- 家庭成员与行为画像 -->
    <div class="card p-5">
      <div class="flex items-center gap-2.5 mb-3">
        <div class="w-8 h-8 rounded-lg grad-brand grid place-items-center text-white">
          <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg>
        </div>
        <h3 class="font-semibold text-sm">家庭成员与行为画像</h3>
      </div>
      <label class="flex items-center gap-2.5 cursor-pointer">
        <div class="switch scale-90" :class="cfg.auto_discover_persona && 'on'" @click="cfg.auto_discover_persona = !cfg.auto_discover_persona"></div>
        <span class="text-xs text-txt-2">主动推送生活习惯发现（关闭则仅记录，需用户确认才存档）</span>
      </label>
      <p class="hint">开启后，AI 助手在对话中发现某成员的行为偏好（如夜猫子🦉）时会主动询问是否写入「生活习惯档案」；无论开关状态，写回前都会先征得确认。</p>
    </div>

    <!-- 系统 / 在线更新 -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg grad-brand grid place-items-center">
            <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M21 2v6h-6M3 22v-6h6"/><path d="M21 8a9 9 0 00-15-3.36L3 8M3 16a9 9 0 0015 3.36L21 16"/></svg>
          </div>
          <div>
            <h3 class="font-semibold">系统 / 在线更新</h3>
            <p class="text-[11px] text-txt-3" x-text="'当前：' + (sysVer.commit || '未知') + (sysVer.dirty ? ' · 有未提交改动' : '')"></p>
          </div>
        </div>
        <button class="btn-ghost btn-xs" @click="checkUpdate()" :disabled="updating || checking">
          <span x-show="checking" class="spinner"></span><span>检查更新</span>
        </button>
      </div>
      <div class="text-[11px] text-txt-2 space-y-1">
        <div class="flex justify-between gap-3"><span class="text-txt-3 shrink-0">分支</span><span class="font-mono truncate" x-text="sysVer.branch || '—'"></span></div>
        <div class="flex justify-between gap-3"><span class="text-txt-3 shrink-0">提交</span><span class="font-mono truncate" x-text="sysVer.commit || '—'"></span></div>
        <div class="flex justify-between gap-3"><span class="text-txt-3 shrink-0">标签</span><span class="font-mono truncate" x-text="sysVer.tag || '—'"></span></div>
        <div class="flex justify-between gap-3"><span class="text-txt-3 shrink-0">远端</span><span class="font-mono truncate" x-text="sysVer.update_repo_url || '—'"></span></div>
      </div>
      <template x-if="updateInfo && updateInfo.has_update === true">
        <div class="mt-3 rounded-lg bg-ok/10 border border-ok/25 px-3 py-2 text-[11px] text-ok">
          有可用更新：本地 <span class="font-mono" x-text="updateInfo.local_commit"></span> → 远端 <span class="font-mono" x-text="updateInfo.latest_commit"></span>
        </div>
      </template>
      <template x-if="updateInfo && updateInfo.has_update === false">
        <div class="mt-3 rounded-lg bg-white/5 px-3 py-2 text-[11px] text-txt-3">已是最新版本</div>
      </template>
      <div class="mt-4">
        <button class="btn-primary w-full" @click="doUpdate()" :disabled="!updateInfo || !updateInfo.has_update || updating"
                x-text="updating ? '更新并重启中…' : '更新并重启'"></button>
        <p class="hint mt-2">从 GitHub 拉取最新代码（fast-forward，且工作树需干净），随后重启服务；不触碰 /data 数据。</p>
      </div>
    </div>

    <div class="flex items-center gap-3">
      <button class="btn-primary" @click="save()" :disabled="saving">
        <span x-show="saving" class="spinner"></span>
        <span x-text="saving ? '保存中…' : '保存全部配置'"></span>
      </button>
      <button class="btn-ghost" @click="load()">放弃修改</button>
      <span class="text-[11px] text-txt-3">密钥留空或保持掩码即不改动</span>
    </div>
  </div>

  <!-- 右：健康 + 账号 -->
  <div class="space-y-5">

    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <h3 class="font-semibold text-sm">系统健康</h3>
        <button class="icon-btn" @click="loadHealth()" :class="healthLoading && 'animate-spin'">
          <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>
        </button>
      </div>
      <div class="space-y-2">
        <template x-for="row in healthRows" :key="row.key">
          <div class="flex items-center gap-2.5 py-1.5">
            <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="row.on ? 'bg-ok' : 'bg-danger'"></span>
            <span class="text-xs w-[76px] shrink-0" x-text="row.label"></span>
            <span class="text-[11px] text-txt-3 truncate flex-1 text-right" x-text="row.detail"></span>
          </div>
        </template>
      </div>
      <div class="grid grid-cols-2 gap-3 mt-4 pt-4 border-t border-white/5">
        <div><div class="text-[10px] text-txt-3">事件总量</div><div class="text-base font-semibold" x-text="fmtNum(store.total_events)"></div></div>
        <div><div class="text-[10px] text-txt-3">覆盖天数</div><div class="text-base font-semibold" x-text="fmtNum(store.days_covered)"></div></div>
      </div>
    </div>

    <div class="card p-5">
      <h3 class="font-semibold text-sm mb-4">修改密码</h3>
      <div class="space-y-3">
        <div><label class="lbl">当前密码</label><input type="password" class="inp" x-model="pwd.old"></div>
        <div><label class="lbl">新密码（至少 6 位）</label><input type="password" class="inp" x-model="pwd.next"></div>
        <div><label class="lbl">确认新密码</label><input type="password" class="inp" x-model="pwd.confirm"
                @keydown.enter.prevent="changePassword()"></div>
        <button class="btn-soft w-full justify-center" @click="changePassword()" :disabled="changingPwd">
          <span x-show="changingPwd" class="spinner"></span><span>更新密码</span>
        </button>
      </div>
    </div>

    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <h3 class="font-semibold text-sm">账号</h3>
        <span class="badge badge-mute" x-text="users.length + ' 个'"></span>
      </div>
      <div class="space-y-1.5">
        <template x-for="u in users" :key="u.username">
          <div class="card-flat px-3 py-2 flex items-center gap-2">
            <div class="w-7 h-7 rounded-lg grad-brand grid place-items-center text-[11px] font-semibold shrink-0"
                 x-text="(u.username||'?').slice(0,1).toUpperCase()"></div>
            <div class="min-w-0 flex-1">
              <div class="text-xs truncate" x-text="u.username"></div>
              <div class="text-[10px] text-txt-3" x-text="fmtTime(u.created_at)"></div>
            </div>
            <span class="badge badge-brand" x-show="u.is_admin">管理员</span>
            <button class="icon-btn hover:!text-danger" x-show="$store.app.user.is_admin && u.username !== $store.app.user.username"
                    @click="removeUser(u)">
              <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
            </button>
          </div>
        </template>
      </div>
    </div>

    <!-- v0.6：应用令牌（TVPilot / DeskPilot）多令牌管理与吊销 -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4">
        <h3 class="font-semibold text-sm">应用令牌（TVPilot / DeskPilot）</h3>
        <span class="badge badge-mute" x-text="appTokens.length + ' 个'"></span>
      </div>
      <p class="hint mb-3">每个客户端（电视 / PC）一个独立令牌，可单独吊销；遗留单 APP_TOKEN 仍作为兜底。</p>

      <template x-if="appTokenSecret">
        <div class="mb-3 rounded-lg bg-ok/10 border border-ok/25 px-3 py-2 text-[11px]">
          <div class="flex items-center justify-between gap-2">
            <span class="text-ok font-semibold">新令牌已生成（仅显示一次）</span>
            <button class="btn-ghost btn-xs" @click="copy(appTokenSecret)">复制</button>
          </div>
          <code class="block mt-1 break-all font-mono text-txt-2" x-text="appTokenSecret"></code>
        </div>
      </template>

      <div class="space-y-1.5">
        <template x-for="t in appTokens" :key="t.name">
          <div class="card-flat px-3 py-2 flex items-center gap-2">
            <div class="min-w-0 flex-1">
              <div class="text-xs truncate font-medium" x-text="t.name"></div>
              <div class="text-[10px] text-txt-3 flex items-center gap-2">
                <span class="font-mono" x-text="t.prefix + '…'"></span>
                <span x-show="t.source" x-text="'来源: ' + t.source"></span>
                <span x-text="'使用 ' + (t.use_count||0) + ' 次'"></span>
              </div>
            </div>
            <button class="icon-btn hover:!text-danger" @click="revokeAppToken(t.name)">
              <svg viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
            </button>
          </div>
        </template>
        <p x-show="!appTokens.length" class="text-xs text-txt-3">尚无应用令牌，请在下方创建。</p>
      </div>

      <div class="grid grid-cols-2 gap-2 mt-3">
        <input class="inp" x-model="newAppName" placeholder="令牌名称（如 tvpilot）">
        <input class="inp inp-mono" x-model="newAppSource" placeholder="来源标识（可选）">
      </div>
      <button class="btn-soft w-full justify-center mt-2" @click="createAppToken()" :disabled="!newAppName.trim()">
        生成新令牌
      </button>
    </div>
  </div>
</div>
`;

export function settingsPage() {
  return {
    tpl: TPL,
    cfg: {},
    users: [],
    appTokens: [],
    newAppName: '',
    newAppSource: '',
    appTokenSecret: '',
    revealed: {},
    testing: {},
    testResult: {},
    saving: false,
    healthLoading: false,
    changingPwd: false,
    pwd: { old: '', next: '', confirm: '' },

    // 在线更新状态
    sysVer: { commit: '', branch: '', tag: '', dirty: null, update_repo_url: '', update_branch: '' },
    updateInfo: null,
    updating: false,
    checking: false,

    fmtNum, fmtTime,

    get store() { return (this.$store.app.health || {}).store || {}; },

    get healthRows() {
      const h = this.$store.app.health || {};
      return [
        { key: 'ha', label: 'Home Assistant', on: !!(h.ha && h.ha.connected), detail: (h.ha && (h.ha.url || h.ha.error)) || '未配置' },
        { key: 'llm', label: '大模型', on: !!(h.llm && h.llm.connected), detail: (h.llm && (h.llm.model || h.llm.error)) || '未配置' },
        { key: 'nodered', label: 'Node-RED', on: !!(h.nodered && h.nodered.connected), detail: (h.nodered && (h.nodered.url || h.nodered.error)) || '未配置' },
        { key: 'chroma', label: '向量库', on: !!(h.chroma && h.chroma.connected), detail: h.chroma && h.chroma.connected ? (h.chroma.documents || 0) + ' 文档' : ((h.chroma && h.chroma.error) || '未启用') },
        { key: 'ha_db', label: 'HA 数据库', on: !!(h.ha_db && h.ha_db.connected), detail: (h.ha_db && h.ha_db.connected) ? ('schema=' + (h.ha_db.mode || '?')) : ((h.ha_db && h.ha_db.enabled) ? (h.ha_db.error || '未连接') : '未启用') },
        { key: 'mqtt', label: 'MQTT 推送', on: !!(h.mqtt && h.mqtt.enabled && h.mqtt.connected), detail: h.mqtt ? (h.mqtt.enabled ? (h.mqtt.connected ? '已连接' : (h.mqtt.retry_after > 0 ? `重连中（${Math.ceil(h.mqtt.retry_after)}s 后重试）` : '未连接')) : '未启用') : '—' },
        { key: 'lag', label: '采集滞后', on: (h.collect_lag_seconds ?? null) !== null && (h.collect_lag_seconds < 3600), detail: (h.collect_lag_seconds ?? null) === null ? '无事件' : (h.collect_lag_seconds < 60 ? '刚刚' : `${Math.round(h.collect_lag_seconds / 60)} 分钟前`) },
        { key: 'embedding', label: '嵌入模型', on: !(h.embedding && h.embedding.configured && h.embedding.error), detail: h.embedding ? (h.embedding.configured ? (h.embedding.error ? ('错误: ' + h.embedding.error) : (`${h.embedding.model} · ${h.embedding.dimension || '?'}维`)) : (h.embedding.reason || '默认 MiniLM')) : '—' },
        { key: 'tokens', label: 'MCP 凭证', on: (h.tokens || 0) > 0, detail: (h.tokens || 0) + ' 个有效' }
      ];
    },

    init() {
      this.load();
      this.loadUsers();
      this.loadAppTokens();
      this.loadHealth();
      this.loadVersion();
    },

    async loadAppTokens() {
      try {
        const d = await api.listAppTokens();
        this.appTokens = d.tokens || [];
      } catch (e) { /* 非管理员或无权限静默 */ }
    },

    async createAppToken() {
      const name = (this.newAppName || '').trim();
      if (!name) { this.$store.app.warn('请填写令牌名称'); return; }
      try {
        const d = await api.createAppToken(name, (this.newAppSource || '').trim());
        this.appTokenSecret = d.token || '';
        this.newAppName = '';
        this.newAppSource = '';
        await this.loadAppTokens();
        this.$store.app.ok('已生成应用令牌（明文仅显示一次，请立即复制）');
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    async revokeAppToken(name) {
      const yes = await this.$store.app.ask('吊销令牌', '确定吊销「' + name + '」？该客户端将立即失去访问权限。', '吊销');
      if (!yes) return;
      try {
        await api.revokeAppToken(name);
        this.appTokenSecret = '';
        await this.loadAppTokens();
        this.$store.app.ok('已吊销：' + name);
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    async load() {
      try {
        this.cfg = await api.getConfig();
        this.revealed = {};
      } catch (e) {
        this.$store.app.err('配置加载失败：' + e.message);
      }
    },

    async loadUsers() {
      try {
        const d = await api.users();
        this.users = d.users || [];
      } catch (e) { /* 非管理员可能无权限，静默 */ }
    },

    async loadHealth() {
      this.healthLoading = true;
      try {
        this.$store.app.health = await api.health();
      } catch (e) {
        this.$store.app.err('健康检查失败：' + e.message);
      } finally {
        this.healthLoading = false;
      }
    },

    async loadVersion() {
      try {
        this.sysVer = await api.systemVersion();
      } catch (e) { /* 非 git 环境可忽略 */ }
    },

    async checkUpdate() {
      this.checking = true;
      this.updateInfo = null;
      try {
        const d = await api.systemUpdateCheck();
        if (!d.ok) { this.$store.app.err(d.error || '检查更新失败'); return; }
        this.updateInfo = d;
        this.$store.app.ok(d.has_update ? ('发现新版本：' + d.latest_commit) : '已是最新');
      } catch (e) {
        this.$store.app.err('检查更新失败：' + e.message);
      } finally {
        this.checking = false;
      }
    },

    async doUpdate() {
      if (!this.updateInfo || !this.updateInfo.has_update) return;
      if (!confirm('确认从 GitHub 拉取最新代码并重启服务？更新不会删除 /data 数据。')) return;
      this.updating = true;
      try {
        const d = await api.systemUpdate();
        if (!d.ok) { this.$store.app.err(d.error || '更新失败'); return; }
        this.$store.app.ok('更新完成，正在重启…页面将在数秒后自动刷新');
        setTimeout(() => location.reload(), 4000);
      } catch (e) {
        this.$store.app.err('更新失败：' + e.message);
      } finally {
        this.updating = false;
      }
    },

    async save() {
      this.saving = true;
      try {
        // rooms/excluded_entities 由采集页维护，这里不提交，避免互相覆盖
        const patch = Object.assign({}, this.cfg);
        delete patch.rooms;
        delete patch.excluded_entities;
        delete patch.db_path;
        delete patch.last_poll_time;
        delete patch.secrets_set;
        delete patch.ok;

        const d = await api.saveConfig(patch);
        const n = (d.changed || []).length;
        this.$store.app.ok(n ? ('已更新 ' + n + ' 项配置') : '配置无变化');
        await this.load();
        this.loadHealth();
      } catch (e) {
        this.$store.app.err(e.message);
      } finally {
        this.saving = false;
      }
    },

    async reveal(field) {
      if (this.revealed[field]) { this.revealed[field] = false; return; }
      try {
        const d = await api.revealSecret(field);
        this.cfg[field] = d.value || '';
        this.revealed[field] = true;
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    // ── 大模型代理池管理 ─────────────────────────────
    addBackend() {
      if (!Array.isArray(this.cfg.llm_backends)) this.cfg.llm_backends = [];
      this.cfg.llm_backends.push({
        name: '', provider: '', model: '', api_url: '',
        api_key: '', temperature: 0.7, max_tokens: 4096, timeout: 120, enabled: true
      });
    },

    removeBackend(i) {
      if (Array.isArray(this.cfg.llm_backends)) this.cfg.llm_backends.splice(i, 1);
    },

    moveBackend(i, dir) {
      const arr = this.cfg.llm_backends;
      if (!Array.isArray(arr)) return;
      const j = i + dir;
      if (j < 0 || j >= arr.length) return;
      const t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    },

    async revealBackend(i) {
      const b = (this.cfg.llm_backends || [])[i];
      if (!b) return;
      if (b.__revealed) { b.__revealed = false; return; }
      try {
        const d = await api.revealSecret('llm_backend_key', { backend: b });
        b.api_key = d.value || '';
        b.__revealed = true;
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    async testBackend(i) {
      const key = 'llm_b' + i;
      this.testing = { ...this.testing, [key]: true };
      const at = new Date().toLocaleTimeString('zh-CN', { hour12: false });
      let result;
      try {
        // 直接把表单里的后端对象发过去；若 api_key 是掩码（未改动），
        // 服务端会按 name+model+api_url 身份从已保存配置还原真实 key，
        // 不依赖前端位置索引，避免陈旧索引导致发掩码 key 而 401。
        const backend = JSON.parse(JSON.stringify((this.cfg.llm_backends || [])[i] || {}));
        if (!backend.model || !backend.api_key) {
          result = { ok: false, message: '请先填写 model 与 API Key（或保存配置后重试）', at };
          this.$store.app.err(result.message);
        } else {
          const d = await api.testConnection({ type: 'llm_backend', backend });
          result = { ok: true, message: d.message || '连接成功', at };
          this.$store.app.ok(result.message);
        }
      } catch (e) {
        const pj = (e && e.payload) || {};
        const p = pj.extra || {};
        const msg = p.message || pj.error || e.message || '测试失败';
        result = { ok: false, message: msg, at };
        this.$store.app.err(msg);
      } finally {
        this.testResult = { ...this.testResult, [key]: result };
        this.testing = { ...this.testing, [key]: false };
      }
    },

    async test(type) {
      this.testing = { ...this.testing, [type]: true };
      const at = new Date().toLocaleTimeString('zh-CN', { hour12: false });
      let result;
      try {
        // 带上表单当前值，未保存也能先测（尤其向量库端口填错时能立刻发现）
        const body = { type };
        if (type === 'chroma') {
          body.chroma_host = this.cfg.chroma_host;
          body.chroma_port = this.cfg.chroma_port;
        }
        if (type === 'ha_db') {
          body.ha_db_host = this.cfg.ha_db_host;
          body.ha_db_port = this.cfg.ha_db_port;
          body.ha_db_name = this.cfg.ha_db_name;
          body.ha_db_user = this.cfg.ha_db_user;
          body.ha_db_password = this.cfg.ha_db_password;
          body.ha_db_query_batch = this.cfg.ha_db_query_batch;
          body.ha_db_query_timeout = this.cfg.ha_db_query_timeout;
        }
        const d = await api.testConnection(body);
        result = { ok: true, message: d.message || '连接正常', steps: d.steps || [], at, backends: d.backends || [] };
        this.$store.app.ok(result.message);
      } catch (e) {
        const pj = (e && e.payload) || {};
        const p = pj.extra || {};
        const msg = p.message || pj.error || e.message || '测试失败';
        result = { ok: false, message: msg, steps: p.steps || [], at, backends: p.backends || [] };
        this.$store.app.err(msg);
      } finally {
        // 整体替换而非改属性，确保 Alpine 一定能侦测到变化
        this.testResult = { ...this.testResult, [type]: result };
        this.testing = { ...this.testing, [type]: false };
      }
    },

    async changePassword() {
      const app = this.$store.app;
      if (this.pwd.next.length < 6) { app.warn('新密码至少 6 位'); return; }
      if (this.pwd.next !== this.pwd.confirm) { app.warn('两次输入的新密码不一致'); return; }
      this.changingPwd = true;
      try {
        const d = await api.changePassword(this.pwd.old, this.pwd.next);
        app.ok(d.message || '密码已修改');
        this.pwd = { old: '', next: '', confirm: '' };
      } catch (e) {
        app.err(e.message);
      } finally {
        this.changingPwd = false;
      }
    },

    async removeUser(u) {
      const yes = await this.$store.app.ask('删除用户', '确定删除账号「' + u.username + '」？', '删除');
      if (!yes) return;
      try {
        const d = await api.deleteUser(u.username);
        this.$store.app.ok(d.message || '已删除');
        this.loadUsers();
      } catch (e) {
        this.$store.app.err(e.message);
      }
    },

    async copy(text) {
      const okFlag = await copyText(text);
      okFlag ? this.$store.app.ok('已复制') : this.$store.app.err('复制失败');
    }
  };
}
