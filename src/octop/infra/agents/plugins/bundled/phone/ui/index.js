/**
 * 小手机渲染器 —— 手机外壳 + App 模块。
 *
 * 结构分五层，自下而上：
 *   1. 基础件：元素构造 ``el``、取值 ``asText/asArray``、时钟；
 *   2. 设计层：``injectStyles`` 注入的作用域 CSS（iOS 令牌 / 材质 / 动效 /
 *      ``:active`` 与 ``@keyframes`` —— inline style 表达不了这些）+ 图标字形；
 *   3. 状态：工作区文件 ``phone/state.json``（角色用 read_file/write_file 维护）的读取、
 *      轮询与写回（``mutate`` 先重读再改，避免覆盖角色刚写的新内容）；
 *   4. App 模块：``APPS`` 里每个对象自带 dock/主屏位置、图标、角标与 render；
 *   5. 外壳 ``PhoneCard``：状态栏 / 主屏宫格 / dock / 返回主屏，负责导航与数据流。
 *
 * 视觉口径：按 iPhone 15 Pro（393×852pt）等比缩放到 340px 宽（k≈0.865），
 * 尺寸与配色对齐 iOS 设计语言；材质用 ``backdrop-filter`` 毛玻璃（Chromium 独有的
 * ``backdrop-filter: url()`` 折射不做，见仓库交接文档的取舍说明）。
 *
 * 新增一个 App 只需往 ``APPS`` 里加一项（并同步后端 ``main.py`` 的 ``APPS``），
 * 主屏或 dock 会自动出现图标。
 */

const React = window.__OCTOP_REACT__;
const { jsx: _jsx, jsxs: _jsxs } = window.__OCTOP_JSX__;
const { useCallback, useEffect, useRef, useState } = React;

const RENDERER_ID = "phone_card";
const DEFAULT_STATE_PATH = "phone/state.json";
/** 轮询间隔（毫秒）：角色改完状态文件后手机自动刷新。 */
const POLL_MS = 8000;
/** 玩家的动作（点赞 / 收藏）写进 likes 的名字，角色读到就知道是玩家做的。 */
const PLAYER_LABEL = "玩家";
/** 状态文件结构版本，与 ``main.py`` 的描述保持一致。 */
const SCHEMA_VERSION = 2;

/** 目标机身尺寸：340px 宽 × 340×(852/393)px 高，保持真机长宽比。 */
const DEVICE_W = 340;
const DEVICE_H = Math.round((DEVICE_W * 852) / 393); // 737
/** 屏幕四周的机身边框厚度。 */
const BEZEL = 6;
/** 状态栏高度（iPhone 15 Pro 约 54pt × k）。 */
const STATUS_H = 47;
/** App 标题栏高度（iOS 44pt × k）。 */
const NAV_H = 44;

const STYLE_ID = "octop-phone-ui-styles";

// ---------------------------------------------------------------- 基础件

/** 统一的元素构造器：children 单独传，key 放最后（jsx 运行时把它当 key）。 */
function el(tag, props, children, key) {
  if (children === undefined) return _jsx(tag, props, key);
  if (Array.isArray(children)) return _jsxs(tag, { ...props, children }, key);
  return _jsx(tag, { ...props, children }, key);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asText(value) {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function asNumber(value, fallback) {
  const num = typeof value === "number" ? value : Number(asText(value).trim());
  return Number.isFinite(num) ? num : fallback;
}

/** 状态文件由角色写，布尔字段可能是 true / "true" / 1，统一按真值理解。 */
function asBool(value) {
  if (value === true) return true;
  if (value == null || value === false) return false;
  const text = asText(value).trim().toLowerCase();
  return text === "true" || text === "1" || text === "yes";
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

/** className 拼接（很多元素只在特定状态下多带一个类）。 */
function cx(...names) {
  return names.filter(Boolean).join(" ");
}

function formatClock(date, timeZone, locale) {
  try {
    return date.toLocaleTimeString(locale === "en" ? "en-US" : "zh-CN", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return "";
  }
}

/** 日期（星期 / 日 / 月），与时钟同用服务端时区。 */
function dateParts(date, timeZone, locale) {
  const blank = { weekday: "", day: "", month: "" };
  try {
    const formatter = new Intl.DateTimeFormat(
      locale === "en" ? "en-US" : "zh-CN",
      { timeZone, month: "short", weekday: "short", day: "numeric" },
    );
    const out = { ...blank };
    for (const part of formatter.formatToParts(date)) {
      if (part.type !== "literal") out[part.type] = part.value;
    }
    return out;
  } catch {
    return blank;
  }
}

/**
 * 时钟：按服务端时区显示，不用浏览器本地时区。
 *
 * 返回整点时间与日期三段，状态栏与主屏日期组件共用同一次 tick。
 */
function useClock(timeZone, locale) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 20000);
    return () => clearInterval(timer);
  }, []);
  return { time: formatClock(now, timeZone, locale), ...dateParts(now, timeZone, locale) };
}

function fileUrl(agentId, path) {
  return (
    `/agents/${encodeURIComponent(agentId)}/workspace/file` +
    `?from_workspace=true&path=${encodeURIComponent(path)}`
  );
}

function isNotFound(err) {
  return /Request failed: 404/.test(String((err && err.message) || err));
}

function errText(err) {
  return String((err && err.message) || err || "未知错误");
}

// ---------------------------------------------------------------- 设计层

/**
 * 全部样式一次性注入到 document.head。
 *
 * 插件 UI 拿不到 CSS 文件，而 inline style 无法表达 ``:active`` / ``@keyframes`` /
 * ``@supports`` / 伪元素发丝线，所以这里注入一段作用域限定在
 * ``[data-octop-plugin-ui="phone"]`` 下的样式表。
 */
const CSS = `
[data-octop-plugin-ui="phone"] {
  --o-blue: #007aff;
  --o-red: #ff3b30;
  --o-green: #34c759;
  --o-orange: #ff9500;
  --o-page: #f2f2f7;
  --o-card: #ffffff;
  --o-elev: #ffffff;
  --o-label: #000000;
  --o-label2: rgba(60,60,67,.6);
  --o-label3: rgba(60,60,67,.3);
  --o-sep: rgba(60,60,67,.29);
  --o-fill: rgba(120,120,128,.12);
  --o-fill2: rgba(120,120,128,.2);
  --o-wall:
    radial-gradient(115% 62% at 8% -6%, #ffcda1 0%, rgba(255,205,161,0) 60%),
    radial-gradient(105% 58% at 96% 2%, #9ec2ff 0%, rgba(158,194,255,0) 64%),
    radial-gradient(130% 62% at 50% 106%, #c9abef 0%, rgba(201,171,239,0) 64%),
    linear-gradient(168deg, #9db4ee 0%, #8ba0e0 34%, #9d8ed2 66%, #c99fcd 100%);
  --o-glass: linear-gradient(180deg, rgba(255,255,255,.62), rgba(255,255,255,.44));
  --o-glass-hi: rgba(255,255,255,.9);
  --o-glass-lo: rgba(15,23,42,.07);
  --o-glass-shadow: 0 10px 26px rgba(15,23,42,.13);
  --o-body: linear-gradient(155deg, #fbfcfe 0%, #c6cbd4 30%, #878e99 60%, #dfe3e9 100%);
  --o-body-shadow: 0 26px 62px rgba(15,23,42,.34), 0 3px 10px rgba(15,23,42,.2);
  --o-fs-nav: 15px;
  --o-fs-body: 14.5px;
  --o-fs-sub: 13px;
  --o-fs-foot: 11.5px;
  --o-fs-cap: 10.5px;
  --o-ease: cubic-bezier(.32,.72,0,1);
}

[data-octop-plugin-ui="phone"][data-octop-theme="dark"] {
  --o-page: #000000;
  --o-card: #1c1c1e;
  --o-elev: #2c2c2e;
  --o-label: #ffffff;
  --o-label2: rgba(235,235,245,.6);
  --o-label3: rgba(235,235,245,.3);
  --o-sep: rgba(84,84,88,.65);
  --o-fill: rgba(120,120,128,.24);
  --o-fill2: rgba(120,120,128,.36);
  --o-wall:
    radial-gradient(115% 62% at 12% -4%, rgba(64,104,200,.85) 0%, rgba(64,104,200,0) 58%),
    radial-gradient(100% 56% at 94% 6%, rgba(136,78,196,.7) 0%, rgba(136,78,196,0) 62%),
    linear-gradient(172deg, #111a34 0%, #0a0e1c 48%, #05060d 100%);
  --o-glass: linear-gradient(180deg, rgba(62,62,68,.6), rgba(28,28,30,.5));
  --o-glass-hi: rgba(255,255,255,.16);
  --o-glass-lo: rgba(0,0,0,.5);
  --o-glass-shadow: 0 12px 30px rgba(0,0,0,.5);
  --o-body: linear-gradient(155deg, #565b64 0%, #2b2e35 34%, #14161a 64%, #454a53 100%);
  --o-body-shadow: 0 26px 62px rgba(0,0,0,.62), 0 3px 10px rgba(0,0,0,.5);
}

[data-octop-plugin-ui="phone"],
[data-octop-plugin-ui="phone"] * {
  box-sizing: border-box;
}
[data-octop-plugin-ui="phone"] button {
  font: inherit;
  color: inherit;
  border: 0;
  background: none;
  padding: 0;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

/* ---- 机身 ---- */
[data-octop-plugin-ui="phone"] .o-root {
  width: 100%;
  display: flex;
  justify-content: center;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter",
    "PingFang SC", "Microsoft YaHei", sans-serif;
  color: var(--o-label);
}
[data-octop-plugin-ui="phone"] .o-device {
  position: relative;
  width: ${DEVICE_W}px;
  height: ${DEVICE_H}px;
  flex: 0 0 auto;
}
[data-octop-plugin-ui="phone"] .o-body {
  position: absolute;
  inset: 0;
  border-radius: 54px;
  background: var(--o-body);
  box-shadow: var(--o-body-shadow);
}
[data-octop-plugin-ui="phone"] .o-key {
  position: absolute;
  width: 3px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--o-body) 70%, #6b7280);
  opacity: .85;
}
[data-octop-plugin-ui="phone"] .o-key-l { left: -3px; }
[data-octop-plugin-ui="phone"] .o-key-r { right: -3px; }
[data-octop-plugin-ui="phone"] .o-screen {
  position: absolute;
  inset: ${BEZEL}px;
  border-radius: 48px;
  overflow: hidden;
  background: #000;
  isolation: isolate;
}
[data-octop-plugin-ui="phone"] .o-wall {
  position: absolute;
  inset: 0;
  background: var(--o-wall);
}
[data-octop-plugin-ui="phone"] .o-wall::after {
  content: "";
  position: absolute;
  inset: 0;
  background: radial-gradient(140% 100% at 50% 100%, rgba(0,0,0,.14) 0%, rgba(0,0,0,0) 60%);
}

/* ---- 状态栏 ---- */
[data-octop-plugin-ui="phone"] .o-status {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: ${STATUS_H}px;
  z-index: 30;
  display: flex;
  align-items: center;
  padding: 0 22px;
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: .2px;
  color: var(--o-label);
  pointer-events: none;
}
[data-octop-theme="dark"] .o-status { color: #fff; }
/* 主屏状态栏压在壁纸上：跟真机一样用白字 + 阴影，进了 App 才回到正文色。 */
[data-octop-plugin-ui="phone"] .o-status-home {
  color: #fff;
  text-shadow: 0 1px 3px rgba(0,0,0,.42);
}
[data-octop-plugin-ui="phone"] .o-island {
  position: absolute;
  left: 50%;
  top: 12px;
  transform: translateX(-50%);
  width: 109px;
  height: 32px;
  border-radius: 16px;
  background: #000;
}
[data-octop-theme="dark"] .o-island { box-shadow: inset 0 0 0 .5px rgba(255,255,255,.08); }
[data-octop-plugin-ui="phone"] .o-status-r {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 5px;
}

/* ---- 玻璃材质 ---- */
[data-octop-plugin-ui="phone"] .o-glass {
  background: var(--o-glass);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  box-shadow:
    inset 0 .5px 0 var(--o-glass-hi),
    inset 0 -.5px 0 var(--o-glass-lo),
    var(--o-glass-shadow);
}

/* ---- 滚动舞台 ---- */
[data-octop-plugin-ui="phone"] .o-scroll {
  position: absolute;
  inset: 0;
  z-index: 10;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  background: var(--o-page);
}
[data-octop-plugin-ui="phone"] .o-scroll::-webkit-scrollbar { width: 0; height: 0; }
[data-octop-plugin-ui="phone"] .o-scroll-home {
  padding-top: ${STATUS_H}px;
  padding-bottom: 168px;
  background: transparent;
}
[data-octop-plugin-ui="phone"] .o-scroll-app { background: var(--o-page); }
[data-octop-plugin-ui="phone"] .o-stage { animation: o-zoom 380ms var(--o-ease) both; }
[data-octop-theme="dark"] .o-scroll-app { background: #000; }

/* ---- App 标题栏 ---- */
[data-octop-plugin-ui="phone"] .o-nav {
  position: sticky;
  top: 0;
  z-index: 20;
  padding-top: ${STATUS_H}px;
  background: var(--o-glass);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  box-shadow: inset 0 -.5px 0 var(--o-sep);
}
[data-octop-plugin-ui="phone"] .o-nav-inner {
  position: relative;
  height: ${NAV_H}px;
  display: flex;
  align-items: center;
  padding: 0 8px;
}
[data-octop-plugin-ui="phone"] .o-nav-btn {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: var(--o-blue);
  font-size: var(--o-fs-body);
  padding: 4px 4px;
  border-radius: 8px;
  transition: opacity 120ms linear;
}
[data-octop-plugin-ui="phone"] .o-nav-btn:active { opacity: .45; }
[data-octop-plugin-ui="phone"] .o-nav-title {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  font-size: var(--o-fs-nav);
  font-weight: 600;
  max-width: 168px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
[data-octop-plugin-ui="phone"] .o-nav-right { margin-left: auto; display: flex; }

/* ---- 主屏 ---- */
[data-octop-plugin-ui="phone"] .o-home {
  padding: 14px 16px 0;
  display: flex;
  flex-direction: column;
  gap: 22px;
}
[data-octop-plugin-ui="phone"] .o-widget {
  border-radius: 22px;
  padding: 13px 16px;
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--o-label);
}
[data-octop-theme="dark"] .o-widget { color: #fff; }
[data-octop-plugin-ui="phone"] .o-wdate { display: flex; flex-direction: column; line-height: 1; }
[data-octop-plugin-ui="phone"] .o-wdate-day { font-size: 34px; font-weight: 700; letter-spacing: -1px; }
[data-octop-plugin-ui="phone"] .o-wdate-sub {
  font-size: var(--o-fs-cap);
  font-weight: 600;
  opacity: .72;
  margin-top: 3px;
}
[data-octop-plugin-ui="phone"] .o-wdiv {
  width: .5px;
  align-self: stretch;
  background: var(--o-sep);
  margin: 2px 0;
}
[data-octop-plugin-ui="phone"] .o-wowner { display: flex; align-items: center; gap: 9px; min-width: 0; }
[data-octop-plugin-ui="phone"] .o-wavatar {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  background: var(--o-fill);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  flex: 0 0 auto;
}
[data-octop-plugin-ui="phone"] .o-wmeta { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
[data-octop-plugin-ui="phone"] .o-wname {
  font-size: var(--o-fs-sub);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
[data-octop-plugin-ui="phone"] .o-wsub {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: var(--o-fs-cap);
  opacity: .66;
  overflow: hidden;
  white-space: nowrap;
}
[data-octop-plugin-ui="phone"] .o-wsub > span { overflow: hidden; text-overflow: ellipsis; }
[data-octop-plugin-ui="phone"] .o-wdot { opacity: .55; }

/* ---- 主屏宫格 ---- */
[data-octop-plugin-ui="phone"] .o-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  row-gap: 20px;
  column-gap: 6px;
  align-content: start;
}
[data-octop-plugin-ui="phone"] .o-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 100%;
}
[data-octop-plugin-ui="phone"] .o-tile-ic {
  position: relative;
  width: 52px;
  height: 52px;
  border-radius: 22.37%;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 3px 8px rgba(15,23,42,.2), inset 0 .5px 0 rgba(255,255,255,.34);
  transition: transform 140ms var(--o-ease);
}
@supports (corner-shape: squircle) {
  [data-octop-plugin-ui="phone"] .o-tile-ic { corner-shape: squircle; }
}
[data-octop-plugin-ui="phone"] .o-tile:active .o-tile-ic { transform: scale(.9); }
[data-octop-plugin-ui="phone"] .o-tile-glyph {
  width: 29px;
  height: 29px;
  display: block;
}
[data-octop-plugin-ui="phone"] .o-tile-label {
  font-size: var(--o-fs-cap);
  font-weight: 500;
  color: #fff;
  text-shadow: 0 1px 2px rgba(0,0,0,.5), 0 0 7px rgba(0,0,0,.3);
  max-width: 74px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}
[data-octop-theme="dark"] .o-tile-label { color: #fff; }
[data-octop-plugin-ui="phone"] .o-badge {
  position: absolute;
  top: -5px;
  right: -6px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--o-red);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
  box-shadow: 0 0 0 1.5px rgba(0,0,0,.12);
}

/* ---- dock / 页码点 / 指示条 ---- */
[data-octop-plugin-ui="phone"] .o-dock {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 22px;
  z-index: 20;
  height: 80px;
  border-radius: 26px;
  display: flex;
  align-items: center;
  justify-content: space-evenly;
}
[data-octop-theme="dark"] .o-dock {
  background: linear-gradient(180deg, rgba(62,62,68,.44), rgba(28,28,30,.36));
}
[data-octop-plugin-ui="phone"] .o-dots {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 110px;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
}
[data-octop-plugin-ui="phone"] .o-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgba(255,255,255,.42);
  box-shadow: 0 1px 2px rgba(0,0,0,.2);
}
[data-octop-plugin-ui="phone"] .o-dot-on { background: rgba(255,255,255,.95); }
[data-octop-plugin-ui="phone"] .o-ind {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 40;
  height: 22px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding-bottom: 7px;
}
[data-octop-plugin-ui="phone"] .o-ind-bar {
  width: 132px;
  height: 5px;
  border-radius: 3px;
  background: rgba(255,255,255,.92);
  box-shadow: 0 0 3px rgba(0,0,0,.28);
}
[data-octop-theme="dark"] .o-ind-bar { background: rgba(255,255,255,.78); }

/* ---- 列表 ---- */
[data-octop-plugin-ui="phone"] .o-list { background: var(--o-card); }
[data-octop-plugin-ui="phone"] .o-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  text-align: left;
  padding: 10px 14px;
  background: var(--o-card);
  transition: background 90ms linear;
}
[data-octop-plugin-ui="phone"] .o-row:active { background: var(--o-fill); }
[data-octop-plugin-ui="phone"] .o-row::after {
  content: "";
  position: absolute;
  left: 55px;
  right: 0;
  bottom: 0;
  height: 1px;
  background: var(--o-sep);
  transform: scaleY(.5);
  transform-origin: bottom;
}
[data-octop-plugin-ui="phone"] .o-row:last-child::after { display: none; }
[data-octop-plugin-ui="phone"] .o-row-av {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: var(--o-fill2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 21px;
  flex: 0 0 auto;
}
[data-octop-plugin-ui="phone"] .o-row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
[data-octop-plugin-ui="phone"] .o-row-name {
  font-size: var(--o-fs-body);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
[data-octop-plugin-ui="phone"] .o-row-sub {
  font-size: var(--o-fs-sub);
  color: var(--o-label2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
[data-octop-plugin-ui="phone"] .o-row-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 5px;
  flex: 0 0 auto;
}
[data-octop-plugin-ui="phone"] .o-row-time { font-size: var(--o-fs-cap); color: var(--o-label2); }
[data-octop-plugin-ui="phone"] .o-row-badge {
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--o-red);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
}

/* ---- 会话气泡 ---- */
[data-octop-plugin-ui="phone"] .o-thread {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 12px 4px;
  min-height: 100%;
}
[data-octop-plugin-ui="phone"] .o-bubbles {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 7px;
  flex: 1;
}
[data-octop-plugin-ui="phone"] .o-bubbles > * { flex: 0 0 auto; }
[data-octop-plugin-ui="phone"] .o-bwrap { display: flex; }
[data-octop-plugin-ui="phone"] .o-bwrap-me { justify-content: flex-end; }
[data-octop-plugin-ui="phone"] .o-bubble {
  max-width: 76%;
  padding: 7px 12px;
  font-size: var(--o-fs-body);
  line-height: 1.35;
  word-break: break-word;
  border-radius: 18px 18px 18px 5px;
  background: var(--o-elev);
  color: var(--o-label);
  box-shadow: 0 .5px 1.5px rgba(15,23,42,.12);
}
[data-octop-theme="dark"] .o-bubble { background: #2c2c2e; }
[data-octop-plugin-ui="phone"] .o-bubble-me {
  border-radius: 18px 18px 5px 18px;
  background: var(--o-blue);
  color: #fff;
}
[data-octop-plugin-ui="phone"] .o-bubble-t {
  margin-top: 2px;
  font-size: 9.5px;
  text-align: right;
  opacity: .55;
}
[data-octop-plugin-ui="phone"] .o-hint {
  font-size: var(--o-fs-cap);
  color: var(--o-label2);
  text-align: center;
  padding: 10px 16px 14px;
  line-height: 1.6;
}

/* ---- 分组列表（设置 / 通用卡片） ---- */
[data-octop-plugin-ui="phone"] .o-pagebody {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-bottom: 28px;
}
[data-octop-plugin-ui="phone"] .o-group {
  border-radius: 12px;
  overflow: hidden;
  background: var(--o-card);
}
[data-octop-plugin-ui="phone"] .o-gtitle {
  font-size: var(--o-fs-foot);
  color: var(--o-label2);
  text-transform: uppercase;
  letter-spacing: .3px;
  padding: 0 4px 6px;
}
[data-octop-plugin-ui="phone"] .o-grow {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 9px 14px;
  font-size: var(--o-fs-body);
  background: var(--o-card);
}
[data-octop-plugin-ui="phone"] .o-grow::after {
  content: "";
  position: absolute;
  left: 14px;
  right: 0;
  bottom: 0;
  height: 1px;
  background: var(--o-sep);
  transform: scaleY(.5);
  transform-origin: bottom;
}
[data-octop-plugin-ui="phone"] .o-grow:last-child::after { display: none; }
[data-octop-plugin-ui="phone"] .o-glabel { flex: 0 0 auto; min-width: 58px; }
[data-octop-plugin-ui="phone"] .o-gval {
  flex: 1;
  min-width: 0;
  text-align: right;
  color: var(--o-label2);
  word-break: break-word;
}
[data-octop-plugin-ui="phone"] .o-gbody {
  padding: 10px 14px;
  font-size: var(--o-fs-foot);
  line-height: 1.65;
  color: var(--o-label2);
  background: var(--o-card);
}
[data-octop-plugin-ui="phone"] .o-gbody-strong {
  font-size: var(--o-fs-sub);
  color: var(--o-label);
  font-weight: 600;
  word-break: break-all;
}
[data-octop-plugin-ui="phone"] .o-track {
  height: 5px;
  border-radius: 3px;
  background: var(--o-fill2);
  overflow: hidden;
  margin-top: 5px;
}
[data-octop-plugin-ui="phone"] .o-track > span { display: block; height: 100%; background: var(--o-blue); }

/* ---- 相册 ---- */
[data-octop-plugin-ui="phone"] .o-photos {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 2px;
  padding: 2px;
}
[data-octop-plugin-ui="phone"] .o-photo {
  position: relative;
  aspect-ratio: 1 / 1;
  border-radius: 2px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 30px;
  /* 暗角：照片是 emoji + 渐变画出来的，加一层内阴影才不至于像纯色卡。
     （不要用 filter，那会把 emoji 一起调暗。） */
  box-shadow: inset 0 0 30px rgba(0,0,0,.2);
  transition: transform 140ms var(--o-ease);
}
[data-octop-plugin-ui="phone"] .o-photo:active { transform: scale(.96); }
[data-octop-plugin-ui="phone"] .o-photo-fav {
  position: absolute;
  right: 4px;
  bottom: 3px;
  font-size: 11px;
  filter: drop-shadow(0 1px 2px rgba(0,0,0,.5));
}
[data-octop-plugin-ui="phone"] .o-photo-zoom {
  height: 264px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 118px;
  box-shadow: inset 0 0 90px rgba(0,0,0,.24);
}

/* ---- 朋友圈 ---- */
[data-octop-plugin-ui="phone"] .o-feed { display: flex; flex-direction: column; }
[data-octop-plugin-ui="phone"] .o-post {
  display: flex;
  gap: 10px;
  padding: 14px;
  background: var(--o-card);
}
[data-octop-plugin-ui="phone"] .o-post + .o-post { box-shadow: inset 0 .5px 0 var(--o-sep); }
[data-octop-theme="dark"] .o-post + .o-post { box-shadow: inset 0 .5px 0 var(--o-sep); }
[data-octop-plugin-ui="phone"] .o-post-av {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: var(--o-fill2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  flex: 0 0 auto;
}
[data-octop-plugin-ui="phone"] .o-post-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
[data-octop-plugin-ui="phone"] .o-post-hd { display: flex; align-items: baseline; gap: 8px; }
[data-octop-plugin-ui="phone"] .o-post-name { font-size: var(--o-fs-sub); font-weight: 600; color: var(--o-blue); }
[data-octop-plugin-ui="phone"] .o-post-time { font-size: var(--o-fs-cap); color: var(--o-label2); margin-left: auto; }
[data-octop-plugin-ui="phone"] .o-post-tx {
  font-size: var(--o-fs-body);
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}
[data-octop-plugin-ui="phone"] .o-post-imgs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; max-width: 200px; }
[data-octop-plugin-ui="phone"] .o-post-img {
  aspect-ratio: 1 / 1;
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 21px;
}
[data-octop-plugin-ui="phone"] .o-chip {
  align-self: flex-start;
  font-size: var(--o-fs-cap);
  color: var(--o-label2);
  background: var(--o-fill);
  border-radius: 7px;
  padding: 3px 8px;
}
[data-octop-plugin-ui="phone"] .o-acts { display: flex; align-items: center; gap: 8px; margin-top: 2px; }
[data-octop-plugin-ui="phone"] .o-like {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--o-fs-foot);
  color: var(--o-label2);
  background: var(--o-fill);
  border-radius: 8px;
  padding: 4px 9px;
  transition: transform 140ms var(--o-ease);
}
[data-octop-plugin-ui="phone"] .o-like:active { transform: scale(.94); }
[data-octop-plugin-ui="phone"] .o-like-on { color: var(--o-blue); background: color-mix(in srgb, var(--o-blue) 14%, transparent); }
[data-octop-plugin-ui="phone"] .o-likers {
  font-size: var(--o-fs-cap);
  color: var(--o-label2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
[data-octop-plugin-ui="phone"] .o-cmts {
  margin-top: 2px;
  background: var(--o-fill);
  border-radius: 9px;
  padding: 7px 9px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
[data-octop-plugin-ui="phone"] .o-cmt { font-size: var(--o-fs-foot); line-height: 1.5; }
[data-octop-plugin-ui="phone"] .o-cmt b { color: var(--o-blue); font-weight: 600; }

/* ---- 空态 / 提示 ---- */
[data-octop-plugin-ui="phone"] .o-empty {
  padding: 44px 28px;
  text-align: center;
  font-size: var(--o-fs-sub);
  line-height: 1.7;
  color: var(--o-label2);
}
[data-octop-plugin-ui="phone"] .o-toast {
  margin: 12px 12px 0;
  border-radius: 14px;
  padding: 9px 13px;
  font-size: var(--o-fs-cap);
  line-height: 1.5;
  color: var(--o-label);
  word-break: break-word;
  animation: o-toast 260ms var(--o-ease) both;
}
[data-octop-theme="dark"] .o-toast { color: #fff; }
[data-octop-plugin-ui="phone"] .o-toast-err { color: var(--o-red); }

/* ---- 详情页 ---- */
[data-octop-plugin-ui="phone"] .o-detail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 9px;
  padding: 26px 20px;
}
[data-octop-plugin-ui="phone"] .o-detail-av {
  width: 84px;
  height: 84px;
  border-radius: 26px;
  background: var(--o-fill2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 42px;
}
[data-octop-plugin-ui="phone"] .o-detail-name { font-size: 20px; font-weight: 600; }
[data-octop-plugin-ui="phone"] .o-detail-meta { font-size: var(--o-fs-sub); color: var(--o-label2); }
[data-octop-plugin-ui="phone"] .o-detail-meta-bad { color: var(--o-red); }
[data-octop-plugin-ui="phone"] .o-cta {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-top: 10px;
  padding: 10px 26px;
  border-radius: 999px;
  background: var(--o-green);
  color: #fff;
  font-size: var(--o-fs-body);
  font-weight: 600;
  transition: transform 140ms var(--o-ease);
}
[data-octop-plugin-ui="phone"] .o-cta:active { transform: scale(.95); }
[data-octop-plugin-ui="phone"] .o-cta:disabled { opacity: .45; }
[data-octop-plugin-ui="phone"] .o-fav {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 16px;
  border-radius: 999px;
  font-size: var(--o-fs-sub);
  background: var(--o-fill);
  color: var(--o-label2);
  transition: transform 140ms var(--o-ease);
}
[data-octop-plugin-ui="phone"] .o-fav:active { transform: scale(.95); }
[data-octop-plugin-ui="phone"] .o-fav-on {
  color: var(--o-orange);
  background: color-mix(in srgb, var(--o-orange) 16%, transparent);
}
[data-octop-plugin-ui="phone"] .o-note { display: flex; flex-direction: column; gap: 4px; padding: 12px 14px; background: var(--o-card); }
[data-octop-plugin-ui="phone"] .o-note + .o-note { box-shadow: inset 0 .5px 0 var(--o-sep); }
[data-octop-plugin-ui="phone"] .o-note-hd { display: flex; align-items: baseline; gap: 8px; }
[data-octop-plugin-ui="phone"] .o-note-t { font-size: var(--o-fs-body); font-weight: 600; flex: 1; min-width: 0; }
[data-octop-plugin-ui="phone"] .o-note-time { font-size: var(--o-fs-cap); color: var(--o-label2); }
[data-octop-plugin-ui="phone"] .o-note-b { font-size: var(--o-fs-sub); line-height: 1.6; color: var(--o-label2); white-space: pre-wrap; word-break: break-word; }

/* ---- 动效 ---- */
@keyframes o-zoom {
  from { opacity: 0; transform: scale(.88); }
  to { opacity: 1; transform: none; }
}
@keyframes o-push {
  from { opacity: .4; transform: translateX(16%); }
  to { opacity: 1; transform: none; }
}
@keyframes o-pop {
  from { opacity: 0; transform: scale(.95); }
  to { opacity: 1; transform: none; }
}
@keyframes o-toast {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: none; }
}
[data-octop-plugin-ui="phone"] .o-anim-push { animation: o-push 300ms var(--o-ease) both; }
[data-octop-plugin-ui="phone"] .o-anim-pop { animation: o-pop 300ms var(--o-ease) both; }

@media (prefers-reduced-motion: reduce) {
  [data-octop-plugin-ui="phone"] *,
  [data-octop-plugin-ui="phone"] *::after {
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 1ms !important;
  }
  [data-octop-plugin-ui="phone"] .o-tile:active .o-tile-ic { transform: none; }
}
`;

function injectStyles() {
  if (typeof document === "undefined") return;
  if (document.getElementById(STYLE_ID)) return;
  const node = document.createElement("style");
  node.id = STYLE_ID;
  node.textContent = CSS;
  (document.head || document.documentElement).appendChild(node);
}

// ---------------------------------------------------------------- 图标字形

/** 照片（相册）花瓣配色，对齐 iOS 相册图标的彩色花瓣。 */
const PETAL_COLORS = [
  "#f2503e", "#f2832e", "#f7c53b", "#5fbf4a",
  "#2fb6c4", "#3d7be0", "#5b4fd6", "#a44bd0",
];

/** App 图标底色渐变（不透明，取代原先的 tint 半透明底）。 */
const APP_TINT = {
  sms: "linear-gradient(160deg,#7df0a0,#12bd57)",
  calls: "linear-gradient(160deg,#63e58c,#34c759)",
  photos: "linear-gradient(160deg,#ffffff,#f0f1f4)",
  wechat: "linear-gradient(160deg,#4ce07a,#06ad4f)",
  moments: "linear-gradient(160deg,#ff9ec0,#f0508c)",
  notes: "linear-gradient(160deg,#ffe066,#f7c600)",
  settings: "linear-gradient(160deg,#ced2d9,#8e8e93)",
};

/** 齿轮路径：外圈用梯形齿，中心挖一个圆孔（evenodd 出洞）。 */
function gearPath(teeth, tipHalfDeg, rTip, rRoot, rHole) {
  const step = 360 / teeth;
  const rotate = -90;
  const parts = [];
  for (let i = 0; i < teeth; i += 1) {
    const a0 = rotate + i * step;
    for (const [deg, r] of [
      [a0 - tipHalfDeg, rRoot],
      [a0 - tipHalfDeg, rTip],
      [a0 + tipHalfDeg, rTip],
      [a0 + tipHalfDeg, rRoot],
    ]) {
      const rad = (deg * Math.PI) / 180;
      const x = (12 + Math.cos(rad) * r).toFixed(2);
      const y = (12 + Math.sin(rad) * r).toFixed(2);
      parts.push(`${parts.length ? "L" : "M"}${x} ${y}`);
    }
  }
  parts.push("Z");
  parts.push(
    `M${12 - rHole} 12a${rHole} ${rHole} 0 1 0 ${rHole * 2} 0` +
      `a${rHole} ${rHole} 0 1 0 ${-rHole * 2} 0Z`,
  );
  return parts.join("");
}

const GEAR_D = gearPath(8, 13, 10.1, 7.5, 3.2);

function svg(props, children, key) {
  return el(
    "svg",
    { viewBox: "0 0 24 24", xmlns: "http://www.w3.org/2000/svg", ...props },
    children,
    key,
  );
}

/**
 * App 图标里的白色字形。
 *
 * 统一用「实心剪影 + evenodd 挖洞」或 SVG mask，避免依赖外部图片资源
 * （插件是自包含 ESM，运行时读不到相对路径的静态资源）。
 */
function appGlyph(id) {
  switch (id) {
    case "sms":
      // 短信：带小尾巴的实心气泡。
      return svg(
        { className: "o-tile-glyph" },
        el("path", {
          d:
            "M12 3.1C6.8 3.1 2.6 6.5 2.6 10.7c0 2.3 1.3 4.4 3.3 5.8v3.3c0 .6.7.9 1.1.5l3-2.2c.6.1 1.3.2 2 .2 5.2 0 9.4-3.4 9.4-7.6S17.2 3.1 12 3.1Z",
          fill: "#fff",
        }),
      );
    case "wechat":
      // 微信：两个气泡叠在一起（大泡挖出两个对话点，小泡实心压在下层）。
      // 不用 SVG mask：mask 引用一旦解析不到，元素会被整块隐藏。
      return svg({ className: "o-tile-glyph" }, [
        el("path", {
          key: "big",
          fill: "#fff",
          fillRule: "evenodd",
          d:
            "M9.3 3.5c-4 0-7.2 2.7-7.2 6.1 0 1.9 1 3.6 2.7 4.7v2.6c0 .5.6.8 1 .5l2.4-1.7c.4 0 .7.1 1.1.1 4 0 7.2-2.7 7.2-6.2S13.3 3.5 9.3 3.5Z" +
            "M7.1 7.4a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6Z" +
            "M11.9 7.4a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6Z",
        }),
        el("path", {
          key: "small",
          fill: "#fff",
          d:
            "M17.4 11.1c-2.9 0-5.2 2-5.2 4.4 0 2.4 2.3 4.4 5.2 4.4.6 0 1.2-.1 1.7-.3l2 1.2-.5-1.9c1.3-.8 2.1-2 2.1-3.4 0-2.4-2.4-4.4-5.3-4.4Z",
        }),
      ]);
    case "calls":
      // 通话：听筒剪影。
      return svg(
        { className: "o-tile-glyph" },
        el("path", {
          d:
            "M6.5 3.1c.7-.7 1.8-.6 2.3.2l2 2.7c.4.6.4 1.4-.1 1.9l-1.1 1.1c-.3.3-.4.8-.2 1.2.7 1.4 1.8 2.5 3.2 3.2.4.2.9.1 1.2-.2l1.1-1.1c.5-.5 1.3-.5 1.9-.1l2.7 2c.8.5.9 1.6.2 2.3l-1.2 1.2c-.8.8-2.1 1.1-3.2.7-2.5-.9-4.7-2.4-6.4-4.1-1.7-1.7-3.2-3.9-4.1-6.4-.4-1.1-.1-2.4.7-3.2l1-1Z",
          fill: "#fff",
        }),
      );
    case "photos":
      // 相册：八瓣彩色风车（iOS 相册图标）。
      return svg(
        { className: "o-tile-glyph" },
        PETAL_COLORS.map((color, index) =>
          el("ellipse", {
            key: `p${index}`,
            cx: 12,
            cy: 7.3,
            rx: 2.6,
            ry: 4.6,
            fill: color,
            transform: `rotate(${index * 45} 12 12)`,
          }),
        ),
      );
    case "moments":
      // 朋友圈：一圈实心圆点 + 中心点（朋友圈图标的「爆发」感）。
      return svg(
        { className: "o-tile-glyph" },
        el(
          "g",
          { fill: "#fff" },
          Array.from({ length: 8 }, (_unused, index) => {
            const rad = ((index * 45 - 90) * Math.PI) / 180;
            return el("circle", {
              key: `d${index}`,
              cx: (12 + Math.cos(rad) * 6.4).toFixed(2),
              cy: (12 + Math.sin(rad) * 6.4).toFixed(2),
              r: 2.1,
            });
          }).concat(el("circle", { key: "core", cx: 12, cy: 12, r: 2.6 })),
        ),
      );
    case "notes":
      // 备忘录：纸张剪影 + 三条挖空的横线。
      return svg(
        { className: "o-tile-glyph" },
        [
          el("path", {
            key: "paper",
            fillRule: "evenodd",
            fill: "#fff",
            d:
              "M6.4 2.6h11.2a3.8 3.8 0 0 1 3.8 3.8v11.2a3.8 3.8 0 0 1-3.8 3.8H6.4a3.8 3.8 0 0 1-3.8-3.8V6.4a3.8 3.8 0 0 1 3.8-3.8Z" +
              "M7.2 7.4h9.6v1.5H7.2Z" +
              "M7.2 11.2h9.6v1.5H7.2Z" +
              "M7.2 15h6v1.5h-6Z",
          }),
        ],
      );
    case "settings":
      // 设置：齿轮（外圈梯形齿，中心圆孔）。
      return svg(
        { className: "o-tile-glyph" },
        el("path", { d: GEAR_D, fill: "#fff", fillRule: "evenodd" }),
      );
    default:
      return svg(
        { className: "o-tile-glyph" },
        el("path", {
          d: "M12 3.4a8.6 8.6 0 1 0 0 17.2 8.6 8.6 0 0 0 0-17.2Z",
          fill: "#fff",
        }),
      );
  }
}

// ---------------------------------------------------------------- 状态栏小件

/** 信号格、Wi-Fi、电量，全部画成 SVG（真机状态栏的组成部分）。 */
function statusGlyphColor() {
  return "currentColor";
}

function signalIcon(level) {
  const filled = clamp(Math.round(asNumber(level, 4)), 0, 4);
  return svg(
    { width: 17, height: 11, viewBox: "0 0 17 11", fill: statusGlyphColor() },
    [1, 2, 3, 4].map((bar) =>
      el("rect", {
        key: `b${bar}`,
        x: (bar - 1) * 4.3,
        y: 11 - bar * 2.4,
        width: 2.9,
        height: bar * 2.4,
        rx: 1,
        opacity: bar <= filled ? 1 : 0.32,
      }),
    ),
  );
}

function wifiIcon() {
  return svg(
    {
      width: 16,
      height: 11,
      viewBox: "0 0 22 16",
      fill: "none",
      stroke: statusGlyphColor(),
      strokeWidth: 2.2,
      strokeLinecap: "round",
    },
    [
      el("path", { key: "a", d: "M2 6.4Q11 .2 20 6.4", opacity: 1 }),
      el("path", { key: "b", d: "M5 9.6Q11 4.6 17 9.6", opacity: .9 }),
      el("circle", { key: "c", cx: 11, cy: 13.4, r: 1.7, fill: statusGlyphColor(), stroke: "none" }),
    ],
  );
}

function batteryIcon(percent, charging) {
  const level = clamp(Math.round(asNumber(percent, 100)), 0, 100);
  const low = level <= 20;
  const color = low ? "#ff3b30" : statusGlyphColor();
  return svg(
    { width: 25, height: 12, viewBox: "0 0 25 12", fill: "none" },
    [
      el("rect", {
        key: "shell",
        x: .6,
        y: .6,
        width: 21,
        height: 10.8,
        rx: 3.6,
        stroke: color,
        strokeOpacity: low ? 1 : .38,
        strokeWidth: 1.1,
      }),
      el("rect", {
        key: "fill",
        x: 2.3,
        y: 2.3,
        width: Math.max(0.8, (17.6 * level) / 100),
        height: 7.4,
        rx: 2,
        fill: color,
      }),
      el("path", {
        key: "nub",
        d: "M23.2 4.1v3.8a1.9 1.9 0 0 0 0-3.8Z",
        fill: color,
        opacity: low ? 1 : .45,
      }),
      charging
        ? el("path", {
            key: "bolt",
            d: "M12.9 1.6 8.7 7h2.6l-.8 3.6L15.3 5h-2.6l.2-3.4Z",
            fill: "#fff",
          })
        : null,
    ],
  );
}

// ---------------------------------------------------------------- 状态模板

/** 首次打开手机时写入的模板：同时充当给角色的 schema 示例。 */
function templateState() {
  return {
    version: SCHEMA_VERSION,
    owner: { name: "我", avatar: "📱" },
    device: {
      model: "iPhone 15 Pro",
      os: "iOS 17.4",
      carrier: "中国移动",
      signal: 4,
      battery: 76,
      charging: false,
      storage_used_gb: 118,
      storage_total_gb: 256,
    },
    wechat: {
      chats: [
        {
          id: "demo",
          name: "示例联系人",
          avatar: "🙂",
          unread: 0,
          messages: [
            { side: "them", text: "这是模板示例，可以覆盖或删掉。", time: "20:58" },
            { side: "me", text: "side=me 表示手机主人自己发的。", time: "20:59" },
          ],
        },
      ],
    },
    moments: [
      {
        id: "demo",
        time: "20:30",
        text: "模板示例：新动态插到 moments 数组头部。",
        images: [],
        likes: [],
        comments: [],
      },
    ],
    notes: [
      {
        id: "demo",
        title: "模板示例",
        body: "角色可以把日记、待办、想记的事写在这里。",
        time: "19:30",
      },
    ],
    sms: [
      {
        id: "demo",
        name: "运营商",
        avatar: "📮",
        unread: 1,
        messages: [
          { side: "them", text: "模板示例：短信和微信一样按 side 区分收发。", time: "09:12" },
        ],
      },
    ],
    calls: [
      {
        id: "demo-in",
        name: "示例联系人",
        avatar: "🙂",
        direction: "in",
        time: "昨天 21:30",
        duration: "3分12秒",
      },
      {
        id: "demo-missed",
        name: "示例联系人",
        avatar: "🙂",
        direction: "missed",
        time: "昨天 12:04",
        duration: "",
      },
    ],
    photos: [
      {
        id: "demo-1",
        emoji: "🌇",
        tone: "sunset",
        caption: "模板示例：相册用 emoji + tone 画一张照片。",
        date: "2026-09-14",
        place: "天台",
        favorite: false,
      },
      { id: "demo-2", emoji: "🌊", tone: "sea", caption: "", date: "2026-09-10", place: "", favorite: false },
      { id: "demo-3", emoji: "🍜", tone: "ink", caption: "", date: "2026-09-02", place: "", favorite: false },
    ],
  };
}

// ---------------------------------------------------------------- 通用小件

/** 内置的照片色调；未知 tone 回落到 ink。 */
const TONES = {
  sunset: ["#ffb26b", "#ff6b6b"],
  sea: ["#6bd5ff", "#3a7bd5"],
  forest: ["#9be15d", "#00c2a8"],
  night: ["#2b3a67", "#0f1116"],
  rose: ["#ffb6c1", "#d16ba5"],
  snow: ["#eef1f6", "#b9c6da"],
  ink: ["#4b5563", "#111827"],
};

function toneGradient(name) {
  const pair = TONES[asText(name).trim().toLowerCase()] || TONES.ink;
  return `linear-gradient(150deg,${pair[0]},${pair[1]})`;
}

/** 朋友圈配图的示意底色：状态文件里只有 emoji，按序循环一组柔和渐变当作照片。 */
const IMG_TONES = [
  "linear-gradient(150deg,#ffd7ac,#f79f6a)",
  "linear-gradient(150deg,#bdd5ff,#8aa6e6)",
  "linear-gradient(150deg,#d2ead0,#8cc2a4)",
  "linear-gradient(150deg,#e6d6ff,#ab8fdd)",
  "linear-gradient(150deg,#ffdbe4,#e6a0ba)",
  "linear-gradient(150deg,#d3e8f5,#87b6d2)",
];

function imgTone(seed) {
  return IMG_TONES[((seed % IMG_TONES.length) + IMG_TONES.length) % IMG_TONES.length];
}

/** 主屏 / dock 上的 App 图标（squircle + 矢量字形 + 角标 + 文字）。 */
function appTile(mod, ctx, options) {
  const showLabel = !options || options.label !== false;
  const badge = mod.badge ? mod.badge(ctx) : null;
  const icon = el(
    "span",
    {
      className: "o-tile-ic",
      style: { background: APP_TINT[mod.id] || APP_TINT.settings },
    },
    [
      appGlyph(mod.id),
      badge
        ? el("span", { className: "o-badge" }, badge > 99 ? "99+" : badge, "badge")
        : null,
    ],
    "icon",
  );
  return el(
    "button",
    {
      type: "button",
      className: "o-tile",
      "aria-label": mod.label,
      onClick: () => ctx.openApp(mod.id),
    },
    [
      icon,
      showLabel ? el("span", { className: "o-tile-label" }, mod.label, "label") : null,
    ],
    mod.id,
  );
}

/** App 标题栏：玻璃面板 + 无边框返回按钮 + 居中标题。 */
function navBar(mod, ctx, options) {
  const backLabel = ctx.param ? mod.label : "主屏";
  return el(
    "div",
    { className: "o-nav" },
    el(
      "div",
      { className: "o-nav-inner" },
      [
        el(
          "button",
          {
            type: "button",
            className: "o-nav-btn",
            "aria-label": `返回${backLabel}`,
            onClick: () => (ctx.param ? ctx.setParam(null) : ctx.goHome()),
          },
          [`‹ ${backLabel}`],
          "back",
        ),
        el("span", { className: "o-nav-title" }, options && options.title ? options.title : mod.label, "title"),
        el(
          "div",
          { className: "o-nav-right" },
          el(
            "button",
            {
              type: "button",
              className: "o-nav-btn",
              title: "刷新",
              "aria-label": "刷新",
              onClick: ctx.refresh,
            },
            "⟳",
            "refresh",
          ),
        ),
      ],
    ),
  );
}

/** 会话列表（微信与短信共用：两者数据结构一致）。 */
function threadList(threads, ctx, options) {
  if (!threads.length) return emptyHint(options.emptyText);
  return el(
    "div",
    { className: "o-list" },
    threads.map((thread, index) => {
      const messages = asArray(thread && thread.messages);
      const last = messages.length ? messages[messages.length - 1] : null;
      const unread = Math.max(0, Math.round(asNumber(thread && thread.unread, 0)));
      return el(
        "button",
        {
          type: "button",
          className: "o-row",
          onClick: () =>
            options.onOpen(asText(thread && thread.id) || `${options.keyPrefix}-${index}`),
        },
        [
          el("span", { className: "o-row-av" }, asText(thread && thread.avatar) || "🙂", "avatar"),
          el("span", { className: "o-row-main" }, [
            el("span", { className: "o-row-name" }, asText(thread && thread.name) || "未命名", "name"),
            el(
              "span",
              { className: "o-row-sub" },
              last ? asText(last.text) : "暂无消息",
              "preview",
            ),
          ]),
          el("span", { className: "o-row-meta" }, [
            el("span", { className: "o-row-time" }, last ? asText(last.time) : "", "time"),
            unread ? el("span", { className: "o-row-badge" }, unread > 99 ? "99+" : unread, "unread") : null,
          ]),
        ],
        `${options.keyPrefix}-${index}`,
      );
    }),
  );
}

/** 会话详情：气泡列表，按 side=me/them 分左右（微信与短信共用）。 */
function threadView(thread, ctx, options) {
  const messages = asArray(thread && thread.messages);
  return el(
    "div",
    { className: "o-thread" },
    [
      el(
        "div",
        { className: "o-bubbles" },
        messages.length
          ? messages.map((msg, index) => {
              const mine = asText(msg && msg.side) === "me";
              return el(
                "div",
                { className: cx("o-bwrap", mine && "o-bwrap-me") },
                el(
                  "div",
                  { className: cx("o-bubble", mine && "o-bubble-me") },
                  [
                    el("div", {}, asText(msg && msg.text), "text"),
                    el("div", { className: "o-bubble-t" }, asText(msg && msg.time), "time"),
                  ],
                  "bubble",
                ),
                `msg-${index}`,
              );
            })
          : el("div", { className: "o-empty" }, "还没有消息。"),
        "thread",
      ),
      // 手机里不打字：玩家想回复就走主聊天窗口，这里明确告诉他。
      el(
        "div",
        { className: "o-hint" },
        options.hint || "手机里不打字，想回话直接在聊天窗口发消息。",
        "hint",
      ),
      el("div", { ref: ctx.endRef, style: { height: 1 } }, undefined, "anchor"),
    ],
  );
}

function emptyHint(text) {
  return el("div", { className: "o-empty" }, text);
}

/** 分组卡片里的一行：左标签 + 右值。 */
function groupRow(label, value, key) {
  return el(
    "div",
    { className: "o-grow" },
    [
      el("span", { className: "o-glabel" }, label, "label"),
      el("span", { className: "o-gval" }, value, "value"),
    ],
    `row-${key || label}`,
  );
}

// ---------------------------------------------------------------- App 模块
/**
 * App 模块契约（新增 App 只实现这个对象）：
 *   id        稳定标识，同时是 ``show_phone(app=…)`` 的参数
 *   label     图标下与标题栏的文字
 *   tint      图标底色（见 ``APP_TINT``）
 *   placement "dock"（底部常驻）| "home"（主屏宫格）
 *   badge(ctx) 角标数字，返回 0/null 不显示
 *   render(ctx) App 界面；ctx 额外带 param / setParam
 */
const APPS = [
  {
    id: "sms",
    label: "短信",
    placement: "dock",
    badge: (ctx) =>
      asArray(ctx.state && ctx.state.sms).reduce(
        (sum, thread) => sum + Math.max(0, Math.round(asNumber(thread && thread.unread, 0))),
        0,
      ),
    render: (ctx) => {
      const threads = asArray(ctx.state && ctx.state.sms);
      const active = ctx.param ? threads.find((item) => item && asText(item.id) === ctx.param) : null;
      if (active) return threadView(active, ctx, {});
      return threadList(threads, ctx, {
        keyPrefix: "sms",
        emptyText: "还没有短信。让角色往 sms 数组里写。",
        onOpen: (id) => {
          // 真机上打开会话就等于读过了：顺手把未读清零写回，角色能看到玩家已经读过。
          void ctx.mutate((next) => {
            const thread = asArray(next.sms).find((item) => item && asText(item.id) === id);
            if (!thread || !asNumber(thread.unread, 0)) return false;
            thread.unread = 0;
            return true;
          });
          ctx.setParam(id);
        },
      });
    },
  },
  {
    id: "calls",
    label: "通话",
    placement: "dock",
    badge: (ctx) =>
      asArray(ctx.state && ctx.state.calls).filter((call) => asText(call && call.direction) === "missed").length,
    render: (ctx) => {
      const calls = asArray(ctx.state && ctx.state.calls);
      const active = ctx.param ? calls.find((item) => item && asText(item.id) === ctx.param) : null;
      if (active) return callDetail(active, ctx);
      if (!calls.length) return emptyHint("还没有通话记录。让角色往 calls 数组里写。");

      const meta = {
        in: { icon: "↙", label: "呼入", bad: false },
        out: { icon: "↗", label: "呼出", bad: false },
        missed: { icon: "↘", label: "未接", bad: true },
      };
      return el(
        "div",
        { className: "o-list" },
        calls.map((call, index) => {
          const info = meta[asText(call && call.direction)] || meta.in;
          return el(
            "button",
            {
              type: "button",
              className: "o-row",
              "aria-label": `${info.label} ${asText(call && call.name) || "未知号码"}`,
              onClick: () => ctx.setParam(asText(call && call.id) || `call-${index}`),
            },
            [
              el("span", { className: "o-row-av" }, asText(call && call.avatar) || "🙂", "avatar"),
              el("span", { className: "o-row-main" }, [
                el(
                  "span",
                  {
                    className: "o-row-name",
                    style: info.bad ? { color: "var(--o-red)" } : undefined,
                  },
                  `${info.icon} ${asText(call && call.name) || "未知号码"}`,
                  "name",
                ),
                el(
                  "span",
                  { className: "o-row-sub" },
                  asText(call && call.duration) || "未接通",
                  "duration",
                ),
              ]),
              el("span", { className: "o-row-meta" }, el("span", { className: "o-row-time" }, asText(call && call.time), "time"), "meta"),
            ],
            `call-${index}`,
          );
        }),
      );
    },
  },
  {
    id: "photos",
    label: "相册",
    placement: "dock",
    render: (ctx) => {
      const photos = asArray(ctx.state && ctx.state.photos);
      const active = ctx.param ? photos.find((item) => item && asText(item.id) === ctx.param) : null;
      if (active) return photoDetail(active, ctx);
      if (!photos.length) {
        return emptyHint("相册还是空的。让角色往 photos 数组里写 emoji + tone + caption。");
      }
      return el(
        "div",
        { className: "o-photos" },
        photos.map((photo, index) => {
          const caption = asText(photo && photo.caption);
          return el(
            "button",
            {
              type: "button",
              className: "o-photo",
              "aria-label": caption || `照片 ${index + 1}`,
              style: { background: toneGradient(photo && photo.tone) },
              onClick: () => ctx.setParam(asText(photo && photo.id) || `photo-${index}`),
            },
            [
              el("span", {}, asText(photo && photo.emoji) || "📷", "glyph"),
              asBool(photo && photo.favorite)
                ? el("span", { className: "o-photo-fav" }, "⭐", "fav")
                : null,
            ],
            `photo-${index}`,
          );
        }),
      );
    },
  },
  {
    id: "wechat",
    label: "微信",
    placement: "home",
    badge: (ctx) =>
      asArray(ctx.state && ctx.state.wechat && ctx.state.wechat.chats).reduce(
        (sum, chat) => sum + Math.max(0, Math.round(asNumber(chat && chat.unread, 0))),
        0,
      ),
    render: (ctx) => {
      const chats = asArray(ctx.state && ctx.state.wechat && ctx.state.wechat.chats);
      const active = ctx.param ? chats.find((item) => item && asText(item.id) === ctx.param) : null;
      if (active) return threadView(active, ctx, {});
      return threadList(chats, ctx, {
        keyPrefix: "chat",
        emptyText: "微信里还没有会话。让角色往 wechat.chats 里写。",
        onOpen: ctx.setParam,
      });
    },
  },
  {
    id: "moments",
    label: "朋友圈",
    placement: "home",
    render: (ctx) => {
      const posts = asArray(ctx.state && ctx.state.moments);
      if (!posts.length) return emptyHint("还没有朋友圈动态。让角色往 moments 数组里写。");
      return el(
        "div",
        { className: "o-feed" },
        posts.map((post, index) => {
          const likes = asArray(post && post.likes).map(asText);
          const liked = likes.indexOf(PLAYER_LABEL) >= 0;
          const images = asArray(post && post.images).map(asText);
          const emojiImages = images.filter((item) => item && item.length <= 4);
          const comments = asArray(post && post.comments);
          return el(
            "div",
            { className: "o-post" },
            [
              el("span", { className: "o-post-av" }, ctx.ownerAvatar, "avatar"),
              el("div", { className: "o-post-body" }, [
                el("div", { className: "o-post-hd" }, [
                  el("span", { className: "o-post-name" }, ctx.ownerName, "name"),
                  el("span", { className: "o-post-time" }, asText(post && post.time), "time"),
                ]),
                asText(post && post.text)
                  ? el("div", { className: "o-post-tx" }, asText(post.text), "text")
                  : null,
                emojiImages.length
                  ? el(
                      "div",
                      { className: "o-post-imgs" },
                      emojiImages.slice(0, 9).map((emoji, ii) =>
                        el(
                          "span",
                          {
                            className: "o-post-img",
                            style: { background: imgTone(index * 3 + ii + 1) },
                          },
                          emoji,
                          `img-${ii}`,
                        ),
                      ),
                      "images",
                    )
                  : images.length
                    ? el("span", { className: "o-chip" }, `🖼 ${images.length} 张图`, "images")
                    : null,
                el("div", { className: "o-acts" }, [
                  el(
                    "button",
                    {
                      type: "button",
                      className: cx("o-like", liked && "o-like-on"),
                      disabled: ctx.busy,
                      "aria-label": liked ? "取消点赞" : "点赞",
                      onClick: () =>
                        void ctx.mutate((next) => {
                          const target = asArray(next.moments).find(
                            (item) =>
                              item &&
                              asText(item.id) === (asText(post && post.id) || `post-${index}`),
                          );
                          if (!target) return false;
                          const names = asArray(target.likes).map(asText);
                          const at = names.indexOf(PLAYER_LABEL);
                          if (at >= 0) names.splice(at, 1);
                          else names.push(PLAYER_LABEL);
                          target.likes = names;
                          return true;
                        }),
                    },
                    likes.length ? `👍 ${likes.length}` : "👍 点赞",
                    "like",
                  ),
                  likes.length
                    ? el("span", { className: "o-likers" }, likes.join("、"), "likes")
                    : null,
                ]),
                comments.length
                  ? el(
                      "div",
                      { className: "o-cmts" },
                      comments.map((comment, ci) =>
                        el(
                          "div",
                          { className: "o-cmt" },
                          [
                            el("b", {}, asText(comment && comment.name), "who"),
                            el("span", {}, `：${asText(comment && comment.text)}`, "what"),
                          ],
                          `comment-${ci}`,
                        ),
                      ),
                      "comments",
                    )
                  : null,
              ]),
            ],
            `post-${index}`,
          );
        }),
      );
    },
  },
  {
    id: "notes",
    label: "备忘录",
    placement: "home",
    render: (ctx) => {
      const notes = asArray(ctx.state && ctx.state.notes);
      if (!notes.length) return emptyHint("还没有备忘录。让角色往 notes 数组里写。");
      return el(
        "div",
        { className: "o-list" },
        notes.map((item, index) =>
          el(
            "div",
            { className: "o-note" },
            [
              el("div", { className: "o-note-hd" }, [
                el("span", { className: "o-note-t" }, asText(item && item.title) || "无标题", "title"),
                el("span", { className: "o-note-time" }, asText(item && item.time), "time"),
              ]),
              asText(item && item.body)
                ? el("div", { className: "o-note-b" }, asText(item.body), "body")
                : null,
            ],
            `note-${index}`,
          ),
        ),
      );
    },
  },
  {
    id: "settings",
    label: "设置",
    placement: "home",
    render: (ctx) => {
      const device = (ctx.state && ctx.state.device) || {};
      const used = asNumber(device.storage_used_gb, 0);
      const total = asNumber(device.storage_total_gb, 0);
      const percent = total > 0 ? clamp(Math.round((used / total) * 100), 0, 100) : 0;
      return el(
        "div",
        { className: "o-pagebody" },
        [
          el("div", {}, [
            el("div", { className: "o-gtitle" }, "关于本机", "title"),
            el("div", { className: "o-group" }, [
              groupRow("机主", ctx.ownerName, "owner"),
              groupRow("机型", asText(device.model) || "未知机型", "model"),
              groupRow("系统", asText(device.os) || "未知系统", "os"),
              groupRow("运营商", asText(device.carrier) || "无服务", "carrier"),
              groupRow(
                "电量",
                `${clamp(Math.round(asNumber(device.battery, 100)), 0, 100)}%${asBool(device.charging) ? "（充电中）" : ""}`,
                "battery",
              ),
              total > 0
                ? el(
                    "div",
                    { className: "o-grow", style: { flexDirection: "column", alignItems: "stretch", gap: 4 } },
                    [
                      el("span", { style: { display: "flex", justifyContent: "space-between", fontSize: "var(--o-fs-foot)", color: "var(--o-label2)" } }, [
                        el("span", {}, `存储 ${used} / ${total} GB`, "used"),
                        el("span", {}, `${percent}%`, "percent"),
                      ]),
                      el(
                        "div",
                        { className: "o-track" },
                        el("span", { style: { width: `${percent}%` } }, undefined, "bar"),
                        "track",
                      ),
                    ],
                    "storage",
                  )
                : null,
            ], "device"),
          ]),
          el("div", {}, [
            el("div", { className: "o-gtitle" }, "手机内容", "title"),
            el("div", { className: "o-group" }, [
              el("div", { className: "o-gbody" }, [
                el("div", {}, "手机里的内容由角色维护在工作区文件：", "lead"),
                el("div", { className: "o-gbody-strong" }, ctx.statePath, "path"),
                el(
                  "div",
                  { style: { marginTop: 7 } },
                  "玩家能做的：点赞朋友圈、收藏照片、打开短信会话（未读会清零）、回拨电话。",
                  "actions",
                ),
              ], "body"),
            ], "about"),
          ]),
        ],
      );
    },
  },
];

const APP_BY_ID = new Map(APPS.map((app) => [app.id, app]));
const APP_IDS = new Set(APPS.map((app) => app.id));
const DOCK_APPS = APPS.filter((app) => app.placement === "dock");
const HOME_APPS = APPS.filter((app) => app.placement !== "dock");

function initialApp(data) {
  const app = asText(data && data.app).trim().toLowerCase();
  return APP_IDS.has(app) ? app : null;
}

/** 通话详情：大头像 + 记录 + 「回拨」。 */
function callDetail(call, ctx) {
  const meta = {
    in: { icon: "↙", label: "呼入" },
    out: { icon: "↗", label: "呼出" },
    missed: { icon: "↘", label: "未接" },
  };
  const info = meta[asText(call && call.direction)] || meta.in;
  const missed = asText(call && call.direction) === "missed";
  return el(
    "div",
    { className: "o-detail" },
    [
      el("span", { className: "o-detail-av" }, asText(call && call.avatar) || "🙂", "avatar"),
      el("div", { className: "o-detail-name" }, asText(call && call.name) || "未知号码", "name"),
      el(
        "div",
        { className: cx("o-detail-meta", missed && "o-detail-meta-bad") },
        `${info.icon} ${info.label} · ${asText(call && call.time)}${asText(call && call.duration) ? ` · ${asText(call.duration)}` : ""}`,
        "meta",
      ),
      el(
        "button",
        {
          type: "button",
          className: "o-cta",
          disabled: ctx.busy,
          "aria-label": "回拨",
          onClick: () =>
            void ctx.mutate((next) => {
              if (!Array.isArray(next.calls)) next.calls = [];
              next.calls.unshift({
                id: `player-call-${Date.now()}`,
                name: asText(call && call.name) || "未知号码",
                avatar: asText(call && call.avatar) || "🙂",
                direction: "out",
                time: ctx.clock,
                duration: "未接通",
              });
              return true;
            }),
        },
        ["📞 回拨"],
        "redial",
      ),
      el(
        "div",
        { className: "o-hint" },
        "回拨会在通话记录顶部留下一条「呼出·未接通」，角色下一次读文件就能看到。",
        "hint",
      ),
    ],
  );
}

/** 照片大图：色调底 + emoji + 说明 + 收藏。 */
function photoDetail(photo, ctx) {
  const favorite = asBool(photo && photo.favorite);
  return el(
    "div",
    { className: "o-pagebody" },
    [
      el(
        "div",
        {
          className: "o-photo-zoom",
          style: { background: toneGradient(photo && photo.tone), borderRadius: 18, overflow: "hidden" },
        },
        asText(photo && photo.emoji) || "📷",
        "glyph",
      ),
      el(
        "div",
        { style: { fontSize: "var(--o-fs-body)", lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word" } },
        [
          el("div", {}, asText(photo && photo.caption) || "（没有说明）", "caption"),
          el(
            "div",
            { style: { marginTop: 5, fontSize: "var(--o-fs-cap)", color: "var(--o-label2)" } },
            [asText(photo && photo.date), asText(photo && photo.place)].filter(Boolean).join(" · "),
            "meta",
          ),
        ],
        "info",
      ),
      el(
        "button",
        {
          type: "button",
          className: cx("o-fav", favorite && "o-fav-on"),
          disabled: ctx.busy,
          "aria-label": favorite ? "取消收藏" : "收藏",
          onClick: () =>
            void ctx.mutate((next) => {
              const target = asArray(next.photos).find(
                (item) => item && asText(item.id) === asText(photo && photo.id),
              );
              if (!target) return false;
              target.favorite = !asBool(target.favorite);
              return true;
            }),
        },
        favorite ? "⭐ 已收藏" : "☆ 收藏",
        "fav",
      ),
      el(
        "div",
        { style: { fontSize: "var(--o-fs-cap)", color: "var(--o-label2)", lineHeight: 1.7 } },
        "相册里的「照片」目前是角色用 emoji + tone 画出来的，真实图片还不在支持范围内。",
        "hint",
      ),
    ],
  );
}

// ---------------------------------------------------------------- 外壳

function PhoneCard(props) {
  const host = props.host;
  // props.host 每次渲染都是新对象，effect 依赖它会造成死循环，统一走 ref。
  const hostRef = useRef(host);
  hostRef.current = host;

  const context = host.getToolContext();
  const agentId = asText(props.agentId || context.agentId).trim() || null;
  const locale = asText(context.locale) || "zh";
  const dark = context.theme === "dark";
  const statePath = asText(props.data && props.data.state_path).trim() || DEFAULT_STATE_PATH;

  const [phone, setPhone] = useState(null);
  const [phase, setPhase] = useState(agentId ? "loading" : "no-agent");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [app, setApp] = useState(() => initialApp(props.data));
  const [param, setParamState] = useState(null);
  const [anim, setAnim] = useState({ seq: 0, dir: "zoom" });
  const [owner, setOwner] = useState("");
  const [timezone, setTimezone] = useState("UTC");

  const phoneRef = useRef(null);
  phoneRef.current = phone;
  const lastJsonRef = useRef("");
  const writingRef = useRef(false);
  const screenRef = useRef(null);
  const endRef = useRef(null);

  const clockInfo = useClock(timezone, locale);
  const clock = clockInfo.time;

  injectStyles();

  const writeState = useCallback(
    async (next) => {
      await hostRef.current.request(fileUrl(agentId, statePath), {
        method: "PUT",
        body: JSON.stringify({ content: JSON.stringify(next, null, 2) }),
      });
    },
    [agentId, statePath],
  );

  /** 读状态文件并解析；不存在或为空返回 null（由调用方决定是否补模板）。 */
  const readRaw = useCallback(async () => {
    try {
      const res = await hostRef.current.request(fileUrl(agentId, statePath));
      const content = typeof res === "string" ? res : asText(res && res.content);
      if (!content.trim()) return null;
      try {
        return JSON.parse(content);
      } catch {
        throw new Error(`${statePath} 不是合法 JSON，请让角色用 write_file 写回合法 JSON。`);
      }
    } catch (err) {
      if (isNotFound(err)) return null;
      throw err;
    }
  }, [agentId, statePath]);

  // 文件不存在或为空时写一份模板，让角色有地方下笔。
  const readOrSeed = useCallback(async () => {
    const state = await readRaw();
    if (state) return { state, seeded: false, readOnly: false };
    const template = templateState();
    try {
      await writeState(template);
    } catch {
      // 角色没在运行就落不了盘（PUT 需要角色在跑）：照样把空手机画出来，先能看。
      return { state: template, seeded: false, readOnly: true };
    }
    return { state: template, seeded: true, readOnly: false };
  }, [readRaw, writeState]);

  const applyLoaded = useCallback(
    (next, seeded, readOnly) => {
      const json = JSON.stringify(next);
      if (json !== lastJsonRef.current) {
        lastJsonRef.current = json;
        setPhone(next);
      }
      setPhase("ready");
      setError("");
      if (seeded) setNote(`已生成模板 ${statePath}，角色可以直接往里写内容。`);
      else if (readOnly) setNote("角色未运行，手机暂时只读。");
    },
    [statePath],
  );

  /**
   * 玩家动作的统一写回口：先重读文件、再在最新内容上改、最后整文件写回。
   *
   * 手机状态文件同时被角色（read_file/write_file）和玩家（点赞/收藏等）写，
   * 而工作区文件接口没有版本号 / If-Match，因此只能靠「写前重读」把
   * 覆盖窗口压到最小——直接用轮询拿到的旧快照写回会丢掉角色刚写的内容。
   * ``fn(next)`` 返回 false 表示无需写入。
   */
  const mutate = useCallback(
    async (fn) => {
      if (!agentId) return false;
      writingRef.current = true;
      setBusy(true);
      try {
        const fresh = (await readRaw()) || phoneRef.current;
        if (!fresh) return false;
        const next = JSON.parse(JSON.stringify(fresh));
        if (fn(next) === false) return true;
        await writeState(next);
        lastJsonRef.current = JSON.stringify(next);
        setPhone(next);
        setError("");
        return true;
      } catch (err) {
        setError(`写回失败：${errText(err)}`);
        return false;
      } finally {
        writingRef.current = false;
        setBusy(false);
      }
    },
    [agentId, readRaw, writeState],
  );

  // 手机主人名 + 服务端时区（拿不到就用默认值，不影响手机可用）。
  useEffect(() => {
    if (!agentId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const row = await hostRef.current.request(`/agents/${encodeURIComponent(agentId)}`);
        const name = asText(row && row.name).trim();
        if (!cancelled && name) setOwner(name);
      } catch {
        /* 拿不到角色名就只显示「手机」 */
      }
      try {
        const settings = await hostRef.current.request("/settings/timezone");
        const tz = asText(settings && settings.timezone).trim();
        if (!cancelled && tz) setTimezone(tz);
      } catch {
        /* 退回 UTC */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  useEffect(() => {
    if (!agentId) {
      setPhase("no-agent");
      return undefined;
    }
    let cancelled = false;

    const load = async (force) => {
      if (!force) {
        if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
        if (writingRef.current) return;
      }
      try {
        const { state: next, seeded, readOnly } = await readOrSeed();
        if (cancelled) return;
        applyLoaded(next, seeded, readOnly);
      } catch (err) {
        if (cancelled) return;
        setPhase("error");
        setError(errText(err));
      }
    };

    void load(true);
    const timer = setInterval(() => void load(false), POLL_MS);
    const onFocus = () => void load(true);
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [agentId, statePath, readOrSeed, applyLoaded]);

  // 换 App / 打开子页面时回到顶部；会话详情则滚到底部。
  useEffect(() => {
    const node = screenRef.current;
    if (!node) return;
    node.scrollTop = 0;
  }, [app, param]);

  useEffect(() => {
    const node = endRef.current;
    if (param && node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "end" });
    }
  }, [param, phone]);

  const refresh = useCallback(() => {
    setNote("");
    if (!agentId) return;
    (async () => {
      try {
        const { state: next, seeded, readOnly } = await readOrSeed();
        applyLoaded(next, seeded, readOnly);
      } catch (err) {
        setPhase("error");
        setError(errText(err));
      }
    })();
  }, [agentId, readOrSeed, applyLoaded]);

  /** 每次导航都换一个 key，让 CSS 入场动画重新播放。 */
  const bump = useCallback((dir) => {
    setAnim((prev) => ({ seq: prev.seq + 1, dir }));
  }, []);

  const openApp = useCallback(
    (appId) => {
      setApp(APP_IDS.has(appId) ? appId : null);
      setParamState(null);
      bump("zoom");
    },
    [bump],
  );

  const goHome = useCallback(() => {
    setApp(null);
    setParamState(null);
    bump("pop");
  }, [bump]);

  const setParam = useCallback(
    (value) => {
      setParamState(value);
      bump(value ? "push" : "pop");
    },
    [bump],
  );

  const goBack = useCallback(() => {
    if (param) setParam(null);
    else goHome();
  }, [param, setParam, goHome]);

  const ownerName = owner || asText(phone && phone.owner && phone.owner.name) || "角色";
  const ownerAvatar = asText(phone && phone.owner && phone.owner.avatar) || "📱";
  const device = (phone && phone.device) || {};
  const mod = app ? APP_BY_ID.get(app) : null;

  const ctx = {
    state: phone,
    ownerName,
    ownerAvatar,
    clock,
    busy,
    mutate,
    setParam,
    openApp,
    goHome,
    refresh,
    agentId,
    locale,
    statePath,
    endRef,
    param,
  };

  const statusBar = el(
    "div",
    { className: cx("o-status", !mod && "o-status-home") },
    [
      el("span", {}, clock, "clock"),
      el("span", { className: "o-island" }, undefined, "island"),
      el(
        "span",
        { className: "o-status-r" },
        [
          signalIcon(asNumber(device.signal, 4)),
          wifiIcon(),
          batteryIcon(asNumber(device.battery, 100), asBool(device.charging)),
        ],
        "status-right",
      ),
    ],
  );

  const banner = error || note;
  const bannerBar = banner
    ? el(
        "div",
        { className: cx("o-toast", "o-glass", error && "o-toast-err") },
        el("span", {}, banner),
      )
    : null;

  function renderHome() {
    const hasDate = Boolean(clockInfo.day);
    return el(
      "div",
      { className: "o-home" },
      [
        el(
          "div",
          { className: cx("o-widget", "o-glass") },
          [
            hasDate
              ? el("div", { className: "o-wdate" }, [
                  el("span", { className: "o-wdate-day" }, clockInfo.day, "day"),
                  el(
                    "span",
                    { className: "o-wdate-sub" },
                    `${clockInfo.month} ${clockInfo.weekday}`.trim(),
                    "sub",
                  ),
                ], "date")
              : null,
            hasDate ? el("span", { className: "o-wdiv" }, undefined, "div") : null,
            el(
              "div",
              { className: "o-wowner" },
              [
                el("span", { className: "o-wavatar" }, ownerAvatar, "avatar"),
                el("div", { className: "o-wmeta" }, [
                  el("span", { className: "o-wname" }, `${ownerName} 的手机`, "name"),
                  // 机型 / 运营商放在主屏组件里：真机状态栏不显示运营商，
                  // 而 iOS 的灵动岛会占掉状态栏中段，没有它的位置。
                  el(
                    "span",
                    { className: "o-wsub" },
                    [
                      el("span", {}, asText(device.model) || "未知机型", "model"),
                      asText(device.carrier)
                        ? el("span", { className: "o-wdot" }, "·", "dot")
                        : null,
                      asText(device.carrier)
                        ? el("span", {}, asText(device.carrier), "carrier")
                        : null,
                    ],
                    "sub",
                  ),
                ]),
              ],
              "owner",
            ),
          ],
          "widget",
        ),
        el(
          "div",
          { className: "o-grid" },
          HOME_APPS.map((item) => appTile(item, ctx)),
          "grid",
        ),
      ],
    );
  }

  const screenBody =
    phase === "no-agent"
      ? el("div", { className: "o-empty" }, "无法定位角色工作区，手机打不开。")
      : phase === "loading"
        ? el("div", { className: "o-empty" }, "正在打开手机…")
        : el(
            "div",
            { className: cx("o-stage", `o-anim-${anim.dir}`), key: anim.seq },
            [
              mod ? navBar(mod, ctx, {}) : null,
              bannerBar,
              mod ? mod.render(ctx) : renderHome(),
            ],
          );

  const dock = el(
    "div",
    { className: cx("o-dock", "o-glass") },
    DOCK_APPS.map((item) => appTile(item, ctx, { label: false })),
    "dock",
  );

  const pageDots = el(
    "div",
    { className: "o-dots" },
    [
      el("span", { className: "o-dot o-dot-on" }, undefined, "d1"),
      el("span", { className: "o-dot" }, undefined, "d2"),
    ],
    "dots",
  );

  const homeIndicator = el(
    "button",
    {
      type: "button",
      className: "o-ind",
      "aria-label": "回到主屏",
      onClick: goHome,
      ref: undefined,
    },
    el("span", { className: "o-ind-bar" }, undefined, "bar"),
    "ind",
  );

  return el(
    "div",
    {
      "data-octop-plugin-ui": "phone",
      "data-octop-theme": dark ? "dark" : "light",
      className: "o-root",
    },
    el(
      "div",
      { className: "o-device" },
      [
        el("div", { className: "o-body" }, undefined, "body"),
        // 侧边实体键：让机身一眼是真机而不是一个圆角卡片。
        el("span", { className: "o-key o-key-l", style: { top: 118, height: 30 } }, undefined, "mute"),
        el("span", { className: "o-key o-key-l", style: { top: 168, height: 58 } }, undefined, "vol-up"),
        el("span", { className: "o-key o-key-l", style: { top: 236, height: 58 } }, undefined, "vol-dn"),
        el("span", { className: "o-key o-key-r", style: { top: 190, height: 84 } }, undefined, "power"),
        el(
          "div",
          { className: "o-screen" },
          [
            el("div", { className: "o-wall" }, undefined, "wall"),
            el(
              "div",
              { className: cx("o-scroll", mod ? "o-scroll-app" : "o-scroll-home"), ref: screenRef },
              screenBody,
              "scroll",
            ),
            app ? null : dock,
            app ? null : pageDots,
            statusBar,
            homeIndicator,
          ],
          "screen",
        ),
      ],
      "device",
    ),
  );
}

export function setup(host) {
  injectStyles();
  host.registerRenderer({
    id: RENDERER_ID,
    tools: ["show_phone"],
    component: PhoneCard,
  });
}
