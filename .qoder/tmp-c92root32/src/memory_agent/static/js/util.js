/* 通用工具：格式化、剪贴板、下载、Markdown 渲染 */

export const pad2 = (n) => String(n).padStart(2, '0');

export function fmtNum(n) {
  const v = Number(n || 0);
  if (!isFinite(v)) return '0';
  return v.toLocaleString('zh-CN');
}

export function fmtCompact(n) {
  const v = Number(n || 0);
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿';
  if (v >= 1e4) return (v / 1e4).toFixed(1) + '万';
  return String(v);
}

export function fmtDuration(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm' + pad2(s % 60) + 's';
  return Math.floor(s / 3600) + 'h' + pad2(Math.floor((s % 3600) / 60)) + 'm';
}

/** 后端返回的时间统一是本地 ISO 串，这里只做展示裁剪，不做时区换算 */
export function fmtTime(iso, withSec) {
  if (!iso) return '—';
  const t = String(iso).replace('T', ' ');
  return withSec ? t.slice(0, 19) : t.slice(0, 16);
}

export function fmtDay(iso) {
  return iso ? String(iso).slice(0, 10) : '—';
}

export function todayStr(offsetDays) {
  const d = new Date();
  if (offsetDays) d.setDate(d.getDate() + offsetDays);
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

export function monthStr(date) {
  const d = date || new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1);
}

/** 月份字符串加减，返回 'YYYY-MM' */
export function shiftMonth(month, delta) {
  const [y, m] = String(month).split('-').map(Number);
  const d = new Date(y, (m - 1) + delta, 1);
  return monthStr(d);
}

/** 该月 1 号是星期几（0=周日），用于日历补空格 */
export function monthFirstWeekday(month) {
  const [y, m] = String(month).split('-').map(Number);
  return new Date(y, m - 1, 1).getDay();
}

export function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * Markdown 渲染。
 * marked 不带净化能力，这里在输出侧剥掉可执行内容 ——
 * 模型输出终归是不可信输入，不能直接塞进 innerHTML。
 */
export function renderMarkdown(text) {
  const raw = String(text || '');
  if (!raw.trim()) return '';
  let html;
  if (window.marked && typeof window.marked.parse === 'function') {
    try {
      html = window.marked.parse(raw, { breaks: true, gfm: true });
    } catch (e) {
      html = '<p>' + escapeHtml(raw).replace(/\n/g, '<br>') + '</p>';
    }
  } else {
    html = '<p>' + escapeHtml(raw).replace(/\n/g, '<br>') + '</p>';
  }
  return html
    .replace(/<\s*(script|iframe|object|embed|style|link|meta)\b[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi, '')
    .replace(/<\s*(script|iframe|object|embed|style|link|meta)\b[^>]*\/?>/gi, '')
    .replace(/\son\w+\s*=\s*"[^"]*"/gi, '')
    .replace(/\son\w+\s*=\s*'[^']*'/gi, '')
    .replace(/\son\w+\s*=\s*[^\s>]+/gi, '')
    .replace(/(href|src)\s*=\s*(["'])\s*javascript:[^"']*\2/gi, '$1="#"');
}

/** 内网多为 http，navigator.clipboard 常不可用，必须留降级路径 */
export async function copyText(text) {
  const value = String(text == null ? '' : text);
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (e) { /* 落到 execCommand */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    const okFlag = document.execCommand('copy');
    document.body.removeChild(ta);
    return okFlag;
  } catch (e) {
    return false;
  }
}

export function downloadJSON(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: 'application/json;charset=utf-8'
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

export function debounce(fn, wait) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait || 250);
  };
}

export function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, Number(n) || 0));
}

/** 事件量 → 热力等级 0..4 */
export function heatLevel(count, max) {
  const c = Number(count) || 0;
  if (c <= 0) return 0;
  const m = Number(max) || 1;
  const r = c / m;
  if (r <= 0.25) return 1;
  if (r <= 0.5) return 2;
  if (r <= 0.8) return 3;
  return 4;
}
