/* ===== 能耗与碳排管理系统 前端 SPA ===== */
(function () {
'use strict';

/* ---------- 全局状态 ---------- */
var S = {
  user: null,      // 当前登录用户 {name, role_code, dept_name, mock_mode}
  charts: [],      // 当前页面 ECharts 实例（切换页面时销毁）
  page: 'dashboard'
};

var ROLE_NAMES = {
  admin: '系统管理员', manager: '能碳管理员', reporter: '填报员',
  viewer: '管理层', auditor: '审计员'
};
var STATUS_MAP = {
  none: ['未填报', 'none'], draft: ['草稿', 'draft'], submitted: ['已提交', 'submitted'],
  approved: ['已审定', 'approved'], rejected: ['已驳回', 'rejected']
};
var SCOPES = ['范围一', '范围二', '范围三'];
var SCOPE_COLORS = { '范围一': '#e85d3a', '范围二': '#2563eb', '范围三': '#059669' };

/* ---------- 基础工具 ---------- */
function $(sel, root) { return (root || document).querySelector(sel); }
function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

/* HTML 转义，防注入 */
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

/* 数字格式化：千分位 + 指定小数位，空值显示 — */
function fmt(n, digits) {
  if (n === null || n === undefined || n === '' || isNaN(Number(n))) return '—';
  var d = digits === undefined ? 2 : digits;
  return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/* Toast 提示 */
function toast(msg, type) {
  var wrap = $('#toast-wrap');
  var div = document.createElement('div');
  div.className = 'toast' + (type ? ' ' + type : '');
  div.textContent = msg;
  wrap.appendChild(div);
  setTimeout(function () { div.remove(); }, type === 'error' ? 5000 : 2600);
}

/* 统一 API 调用：自动解析 JSON、处理 detail 错误、401 跳登录 */
async function api(path, opts) {
  opts = opts || {};
  var res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    toast('网络连接失败', 'error');
    throw e;
  }
  if (res.status === 401) {
    location.href = '/login.html';
    throw new Error('未登录或会话已过期');
  }
  if (!res.ok) {
    var msg = '请求失败（' + res.status + '）';
    try {
      var d = await res.json();
      if (d && d.detail) msg = d.detail;
    } catch (e) { /* 非 JSON 响应 */ }
    var err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  var ct = res.headers.get('content-type') || '';
  if (ct.indexOf('json') >= 0) return res.json();
  return res.text();
}

/* POST JSON 便捷方法 */
function postJson(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
}
function putJson(path, body) {
  return api(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
}

/* 状态徽章 HTML */
function statusBadge(st) {
  var m = STATUS_MAP[st] || [st || '—', 'none'];
  return '<span class="badge ' + m[1] + '">' + esc(m[0]) + '</span>';
}

/* 角色判断 */
function role() { return S.user.role_code; }
function canWrite() { return ['admin', 'manager', 'reporter'].indexOf(role()) >= 0; } // 录入类
function canAudit() { return ['admin', 'manager'].indexOf(role()) >= 0; }            // 审核/审定/设置
function isAdmin() { return role() === 'admin'; }
function readonly() { return !canWrite(); }                                          // viewer/auditor 只读

/* 年份下拉选项（近 8 年） */
function yearOptions(selected) {
  var cur = new Date().getFullYear();
  var html = '';
  for (var y = cur + 1; y >= cur - 7; y--) {
    html += '<option value="' + y + '"' + (y === selected ? ' selected' : '') + '>' + y + ' 年</option>';
  }
  return html;
}

/* ---------- 图表管理 ---------- */
function makeChart(el, option) {
  var c = echarts.init(el);
  c.setOption(option);
  S.charts.push(c);
  return c;
}
function disposeCharts() {
  S.charts.forEach(function (c) { c.dispose(); });
  S.charts = [];
}
window.addEventListener('resize', function () {
  S.charts.forEach(function (c) { c.resize(); });
});

/* ---------- 弹窗 ---------- */
function openModal(title, bodyHtml) {
  var mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = '<div class="modal"><h3>' + esc(title) + '</h3><div class="modal-body">' +
    bodyHtml + '</div><div class="modal-actions"><button class="btn" data-close>关 闭</button></div></div>';
  function close() { mask.remove(); }
  mask.addEventListener('click', function (ev) {
    if (ev.target === mask || ev.target.hasAttribute('data-close')) close();
  });
  document.body.appendChild(mask);
  return { el: mask, close: close };
}

/* ---------- 菜单与路由 ---------- */
var ALL_ROLES = ['admin', 'manager', 'reporter', 'viewer', 'auditor'];
var MENUS = [
  { key: 'dashboard', name: '工作台', icon: '▣', roles: ALL_ROLES },
  { key: 'energy', name: '能耗台账', icon: '⚡', roles: ALL_ROLES },
  { key: 'energyAnalysis', name: '能耗分析', icon: '↗', roles: ALL_ROLES },
  { key: 'energyConfig', name: '能源与单元配置', icon: '⚙', roles: ['admin'] },
  { key: 'carbonFill', name: '碳盘查填报', icon: '✎', roles: ALL_ROLES },
  { key: 'carbonAnalysis', name: '碳排分析', icon: '◎', roles: ALL_ROLES },
  { key: 'sourcesFactors', name: '科目库/因子库', icon: '☰', roles: ['admin'] },
  { key: 'targetsCarbon', name: '碳中和目标', icon: '🎯', roles: ALL_ROLES },
  { key: 'targetsAnnual', name: '年度计划', icon: '📅', roles: ALL_ROLES },
  { key: 'customers', name: '客户档案', icon: '👥', roles: ALL_ROLES },
  { key: 'allocation', name: '分摊账单', icon: '¥', roles: ALL_ROLES },
  { key: 'reports', name: '报表中心', icon: '📄', roles: ALL_ROLES },
  { key: 'imports', name: '数据导入', icon: '⬆', roles: ['admin', 'manager', 'reporter'] },
  { key: 'sys', name: '系统管理', icon: '🛠', roles: ['admin', 'auditor'] }
];

function visibleMenus() {
  return MENUS.filter(function (m) { return m.roles.indexOf(role()) >= 0; });
}

function renderMenu() {
  $('#menu').innerHTML = visibleMenus().map(function (m) {
    return '<div class="menu-item' + (m.key === S.page ? ' active' : '') + '" data-page="' + m.key + '">' +
      '<span class="icon">' + m.icon + '</span><span>' + m.name + '</span></div>';
  }).join('');
  $all('.menu-item').forEach(function (el) {
    el.addEventListener('click', function () {
      location.hash = el.getAttribute('data-page');
    });
  });
}

/* 页面渲染器注册表（在下方各页面段定义） */
var PAGES = {};

/* 切换页面：销毁旧图表、渲染新页面 */
async function go(page) {
  var menu = MENUS.filter(function (m) { return m.key === page; })[0];
  if (!menu || menu.roles.indexOf(role()) < 0) page = 'dashboard';
  S.page = page;
  disposeCharts();
  renderMenu();
  $('#page-title').textContent = (MENUS.filter(function (m) { return m.key === page; })[0] || {}).name || '';
  var el = $('#content');
  el.innerHTML = '<div class="loading">加载中…</div>';
  try {
    await PAGES[page](el);
  } catch (e) {
    if (e && e.status) {
      el.innerHTML = '<div class="card"><div class="alert-danger">' + esc(e.message) + '</div></div>';
    } else {
      el.innerHTML = '<div class="card"><div class="alert-danger">页面加载失败：' + esc(e.message || e) + '</div></div>';
    }
  }
}

/* ---------- 通用片段 ---------- */
/* 年度选择条 */
function yearBar(id, extraHtml) {
  return '<div class="card"><div class="form-row" style="margin-bottom:0">' +
    '<div class="form-item"><label>年度</label><select id="' + id + '">' +
    yearOptions(new Date().getFullYear()) + '</select></div>' + (extraHtml || '') + '</div></div>';
}
function kpiCard(label, value, unit, cls) {
  return '<div class="kpi-card"><div class="kpi-label">' + esc(label) + '</div>' +
    '<div class="kpi-value' + (cls ? ' ' + cls : '') + '">' + value +
    (unit ? '<span class="kpi-unit">' + esc(unit) + '</span>' : '') + '</div></div>';
}

/* ==================== 工作台 ==================== */
PAGES.dashboard = async function (el) {
  el.innerHTML = yearBar('dash-year') + '<div id="dash-body"><div class="loading">加载中…</div></div>';
  var sel = $('#dash-year');
  async function load() {
    disposeCharts();
    var body = $('#dash-body');
    body.innerHTML = '<div class="loading">加载中…</div>';
    var d = await api('/api/analysis/dashboard?year=' + sel.value);
    var k = d.kpi || {};
    var rate = k.target_rate === null || k.target_rate === undefined ? '—' : fmt(k.target_rate, 1) + '%';

    body.innerHTML =
      '<div class="kpi-grid">' +
      kpiCard('综合能耗', fmt(k.energy_tce), 'tce') +
      kpiCard('累计能耗费用', fmt(k.energy_cost), '元') +
      kpiCard('碳排总量', fmt(k.carbon_total), 'tCO₂e') +
      kpiCard('产值碳强度', fmt(k.intensity), 'tCO₂e/万美金') +
      kpiCard('目标完成率', rate, '') +
      '<div class="kpi-card"><div class="kpi-label">盘查状态</div><div style="margin-top:8px">' +
        statusBadge(k.inventory_status) + '</div></div>' +
      '</div>' +
      '<div class="chart-row">' +
      '<div class="card"><h3>近 12 月能耗趋势</h3><div class="chart" id="ch-energy"></div></div>' +
      '<div class="card"><h3>近 5 年碳排趋势</h3><div class="chart" id="ch-carbon"></div></div>' +
      '</div>' +
      '<div class="card"><h3>待办事项</h3><div id="todo-list"></div></div>';

    // 近 12 月能耗趋势（折线=用量，柱=费用）
    var months = (d.energy_monthly || []).map(function (m) { return m.month + '月'; });
    makeChart($('#ch-energy'), {
      tooltip: { trigger: 'axis' },
      legend: { data: ['用量', '费用'] },
      grid: { left: 60, right: 60, top: 40, bottom: 30 },
      xAxis: { type: 'category', data: months },
      yAxis: [{ type: 'value', name: '用量' }, { type: 'value', name: '费用(元)' }],
      series: [
        { name: '用量', type: 'line', smooth: true, data: (d.energy_monthly || []).map(function (m) { return m.qty; }), itemStyle: { color: '#2563eb' } },
        { name: '费用', type: 'bar', yAxisIndex: 1, data: (d.energy_monthly || []).map(function (m) { return m.amt; }), itemStyle: { color: '#93c5fd' } }
      ]
    });

    // 近 5 年碳排柱状图
    makeChart($('#ch-carbon'), {
      tooltip: { trigger: 'axis' },
      grid: { left: 70, right: 20, top: 30, bottom: 30 },
      xAxis: { type: 'category', data: (d.carbon_trend || []).map(function (t) { return t.year + '年'; }) },
      yAxis: { type: 'value', name: 'tCO₂e' },
      series: [{ name: '碳排总量', type: 'bar', barWidth: 36,
        data: (d.carbon_trend || []).map(function (t) { return t.total; }),
        itemStyle: { color: '#e85d3a', borderRadius: [4, 4, 0, 0] } }]
    });

    // 待办列表
    var todos = d.todos || [];
    $('#todo-list').innerHTML = todos.length
      ? '<ul style="padding-left:18px">' + todos.map(function (t) { return '<li>' + esc(t.text) + '</li>'; }).join('') + '</ul>'
      : '<div class="empty">暂无待办事项</div>';
  }
  sel.addEventListener('change', function () { load().catch(function (e) { toast(e.message, 'error'); }); });
  await load();
};

/* ==================== 能耗台账 ==================== */
PAGES.energy = async function (el) {
  // 能源类型与用能单元下拉数据（本页内缓存）
  var types = await api('/api/energy/types');
  var units = await api('/api/energy/units');
  var typeMap = {};
  types.forEach(function (t) { typeMap[t.code] = t; });
  var editingId = null; // 编辑中的记录 id

  var typeOpts = types.map(function (t) {
    return '<option value="' + esc(t.code) + '">' + esc(t.name) + '（' + esc(t.unit) + '）</option>';
  }).join('');
  var unitOpts = units.map(function (u) {
    return '<option value="' + u.id + '">' + esc(u.name) + '</option>';
  }).join('');

  el.innerHTML =
    '<div class="card"><div class="form-row" style="margin-bottom:0">' +
    '<div class="form-item"><label>年度</label><select id="er-year">' + yearOptions(new Date().getFullYear()) + '</select></div>' +
    '<div class="form-item"><label>月份</label><select id="er-month"><option value="">全部</option>' +
      (function () { var h = ''; for (var m = 1; m <= 12; m++) h += '<option value="' + m + '">' + m + ' 月</option>'; return h; })() +
    '</select></div>' +
    '<button class="btn btn-blue" id="er-query">查 询</button>' +
    '</div></div>' +

    (readonly() ? '' :
    '<div class="card"><h3 id="er-form-title">录入能耗记录</h3>' +
    '<div class="form-row">' +
    '<div class="form-item"><label>年度</label><input type="number" id="ef-year" value="' + new Date().getFullYear() + '" min="2000" max="2100"></div>' +
    '<div class="form-item"><label>月份</label><input type="number" id="ef-month" min="1" max="12" value="' + (new Date().getMonth() + 1) + '"></div>' +
    '<div class="form-item"><label>能源类型</label><select id="ef-type">' + typeOpts + '</select></div>' +
    '<div class="form-item"><label>用能单元</label><select id="ef-unit">' + unitOpts + '</select></div>' +
    '</div><div class="form-row">' +
    '<div class="form-item"><label>用量</label><input type="number" id="ef-qty" min="0" step="any"></div>' +
    '<div class="form-item"><label>金额（元）</label><input type="number" id="ef-amt" min="0" step="any"></div>' +
    '<div class="form-item elec-only" style="display:none"><label>总表电量</label><input type="number" id="ef-gross" min="0" step="any"></div>' +
    '<div class="form-item elec-only" style="display:none"><label>外租扣减</label><input type="number" id="ef-lease" min="0" step="any"></div>' +
    '<div class="form-item elec-only" style="display:none"><label>光伏电量</label><input type="number" id="ef-pv" min="0" step="any"></div>' +
    '<div class="form-item"><label>备注</label><input type="text" id="ef-remark" maxlength="200"></div>' +
    '</div><div>' +
    '<button class="btn btn-primary" id="ef-save">保 存</button>' +
    '<button class="btn" id="ef-cancel" style="display:none">取消编辑</button>' +
    '</div></div>') +

    '<div class="table-wrap"><table class="data-table"><thead><tr>' +
    '<th>年月</th><th>能源类型</th><th>用能单元</th><th>用量</th><th>金额(元)</th><th>状态</th><th>备注/驳回原因</th><th>操作</th>' +
    '</tr></thead><tbody id="er-tbody"><tr><td colspan="8" class="empty">加载中…</td></tr></tbody></table></div>';

  // 电力类型（ELEC）显示拆分字段
  if (!readonly()) {
    $('#ef-type').addEventListener('change', function () {
      var isElec = this.value === 'ELEC';
      $all('.elec-only').forEach(function (d) { d.style.display = isElec ? '' : 'none'; });
    });
    $('#ef-type').dispatchEvent(new Event('change'));

    $('#ef-cancel').addEventListener('click', resetForm);
    $('#ef-save').addEventListener('click', saveRecord);
  }
  $('#er-query').addEventListener('click', function () { loadList().catch(function (e) { toast(e.message, 'error'); }); });

  function numVal(id) {
    var v = $(id).value;
    return v === '' ? undefined : Number(v);
  }

  function resetForm() {
    editingId = null;
    $('#er-form-title').textContent = '录入能耗记录';
    $('#ef-cancel').style.display = 'none';
    ['#ef-qty', '#ef-amt', '#ef-gross', '#ef-lease', '#ef-pv', '#ef-remark'].forEach(function (id) { $(id).value = ''; });
  }

  /* 保存记录：新增 POST / 编辑 PUT；409 弹确认覆盖 */
  async function saveRecord() {
    var body = {
      year: Number($('#ef-year').value),
      month: Number($('#ef-month').value),
      energy_type_code: $('#ef-type').value,
      unit_id: Number($('#ef-unit').value),
      quantity: Number($('#ef-qty').value),
      amount: numVal('#ef-amt'),
      gross_qty: numVal('#ef-gross'),
      lease_deduct: numVal('#ef-lease'),
      pv_qty: numVal('#ef-pv'),
      remark: $('#ef-remark').value || undefined
    };
    if (isNaN(body.quantity)) { toast('请填写用量', 'error'); return; }
    try {
      await doSave(body, false);
    } catch (e) {
      if (e.status === 409 && !editingId && confirm(e.message + '\n\n是否覆盖已有记录？')) {
        try {
          await doSave(body, true);
        } catch (e2) { toast(e2.message, 'error'); return; }
      } else {
        if (e.status !== 409) toast(e.message, 'error');
        return;
      }
    }
    toast(editingId ? '修改成功' : '保存成功', 'success');
    resetForm();
    await loadList();
  }

  async function doSave(body, overwrite) {
    if (editingId) {
      await putJson('/api/energy/records/' + editingId, body);
    } else {
      if (overwrite) body.overwrite = true;
      await postJson('/api/energy/records', body);
    }
  }

  /* 加载台账列表 */
  async function loadList() {
    var q = '?year=' + $('#er-year').value;
    if ($('#er-month').value) q += '&month=' + $('#er-month').value;
    var list = await api('/api/energy/records' + q);
    var tb = $('#er-tbody');
    if (!list.length) {
      tb.innerHTML = '<tr><td colspan="8" class="empty">暂无数据</td></tr>';
      return;
    }
    tb.innerHTML = list.map(function (r) {
      var t = typeMap[r.energy_type_code] || {};
      var note = r.status === 'rejected' && r.reject_reason ? '驳回：' + r.reject_reason : (r.remark || '—');
      return '<tr>' +
        '<td>' + r.year + '-' + String(r.month).padStart(2, '0') + '</td>' +
        '<td>' + esc(r.energy_type_name || r.energy_type_code) + '</td>' +
        '<td>' + esc(r.unit_name || r.unit_id) + '</td>' +
        '<td>' + fmt(r.quantity) + ' ' + esc(t.unit || '') + '</td>' +
        '<td>' + fmt(r.amount) + '</td>' +
        '<td>' + statusBadge(r.status) + (r.status === 'approved' ? ' <span class="badge locked">🔒</span>' : '') + '</td>' +
        '<td>' + esc(note) + '</td>' +
        '<td>' + rowActions(r) + '</td></tr>';
    }).join('');
    bindRowActions(list);
  }

  /* 按状态与角色生成行操作按钮 */
  function rowActions(r) {
    var btns = [];
    if ((r.status === 'draft' || r.status === 'rejected') && canWrite()) {
      btns.push('<button class="btn btn-sm btn-blue" data-act="submit" data-id="' + r.id + '">提交</button>');
      btns.push('<button class="btn btn-sm" data-act="edit" data-id="' + r.id + '">编辑</button>');
    }
    if (r.status === 'submitted' && canAudit()) {
      btns.push('<button class="btn btn-sm btn-green" data-act="approve" data-id="' + r.id + '">通过</button>');
      btns.push('<button class="btn btn-sm btn-danger" data-act="reject" data-id="' + r.id + '">驳回</button>');
    }
    if (r.status === 'approved' && canAudit()) {
      btns.push('<button class="btn btn-sm" data-act="unlock" data-id="' + r.id + '">解锁</button>');
    }
    return btns.join('') || '<span class="text-muted">—</span>';
  }

  /* 绑定行操作事件 */
  function bindRowActions(list) {
    $all('#er-tbody button').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        var id = btn.getAttribute('data-id');
        var act = btn.getAttribute('data-act');
        var rec = list.filter(function (r) { return String(r.id) === id; })[0];
        try {
          if (act === 'submit') {
            await postJson('/api/energy/records/' + id + '/submit');
            toast('已提交审核', 'success');
          } else if (act === 'edit') {
            fillForm(rec);
            return;
          } else if (act === 'approve') {
            await postJson('/api/energy/records/' + id + '/audit', { approve: true });
            toast('已通过', 'success');
          } else if (act === 'reject') {
            var reason = prompt('请输入驳回原因：');
            if (!reason) return;
            await postJson('/api/energy/records/' + id + '/audit', { approve: false, reject_reason: reason });
            toast('已驳回', 'success');
          } else if (act === 'unlock') {
            if (!confirm('确定解锁该已审核记录？')) return;
            await postJson('/api/energy/records/' + id + '/unlock');
            toast('已解锁', 'success');
          }
          await loadList();
        } catch (e) { toast(e.message, 'error'); }
      });
    });
  }

  /* 编辑：回填表单 */
  function fillForm(r) {
    editingId = r.id;
    $('#er-form-title').textContent = '编辑能耗记录（#' + r.id + '）';
    $('#ef-cancel').style.display = '';
    $('#ef-year').value = r.year;
    $('#ef-month').value = r.month;
    $('#ef-type').value = r.energy_type_code;
    $('#ef-type').dispatchEvent(new Event('change'));
    $('#ef-unit').value = r.unit_id;
    $('#ef-qty').value = r.quantity;
    $('#ef-amt').value = r.amount === null ? '' : r.amount;
    $('#ef-gross').value = r.gross_qty === null ? '' : r.gross_qty;
    $('#ef-lease').value = r.lease_deduct === null ? '' : r.lease_deduct;
    $('#ef-pv').value = r.pv_qty === null ? '' : r.pv_qty;
    $('#ef-remark').value = r.remark || '';
    window.scrollTo(0, 0);
  }

  await loadList();
};

/* ==================== 能耗分析 ==================== */
PAGES.energyAnalysis = async function (el) {
  el.innerHTML = yearBar('ea-year') + '<div id="ea-body"><div class="loading">加载中…</div></div>';
  var sel = $('#ea-year');
  async function load() {
    disposeCharts();
    var d = await api('/api/analysis/energy?year=' + sel.value);
    var sum = d.summary || {};
    var items = sum.items || [];

    // 同比指标（±20% 异常红色高亮）
    function yoyHtml(y) {
      if (!y) return '<div class="kpi-card"><div class="kpi-label">同比</div><div class="kpi-value">—</div></div>';
      var cls = y.abnormal ? 'text-danger' : '';
      var arrow = y.rate > 0 ? '↑' : (y.rate < 0 ? '↓' : '');
      return '<div class="kpi-card"><div class="kpi-label">同比' + (y.abnormal ? '（异常波动）' : '') + '</div>' +
        '<div class="kpi-value ' + cls + '">' + arrow + fmt(Math.abs(y.rate), 1) + '<span class="kpi-unit">%</span></div></div>';
    }

    $('#ea-body').innerHTML =
      '<div class="kpi-grid">' +
      kpiCard('年度综合能耗', fmt(sum.total_tce), 'tce') +
      kpiCard('年度能耗费用', fmt(sum.total_cost), '元') +
      kpiCard('能耗强度', fmt(d.intensity_tce), 'tce/万美金') +
      kpiCard('费用强度', fmt(d.cost_intensity), '元/万美金') +
      '</div>' +
      '<div class="chart-row">' +
      '<div class="card"><h3>能源结构（tce 占比）</h3><div class="chart" id="ch-pie"></div></div>' +
      '<div class="card"><h3>月度能耗趋势</h3><div class="chart" id="ch-trend"></div></div>' +
      '</div>' +
      '<div class="kpi-grid" style="grid-template-columns:repeat(2,1fr)">' +
      '<div class="card" style="margin:0"><h3>综合能耗同比</h3>' + yoyHtml(d.yoy_tce) + '</div>' +
      '<div class="card" style="margin:0"><h3>能耗费用同比</h3>' + yoyHtml(d.yoy_cost) + '</div>' +
      '</div>';

    // 能源结构饼图
    makeChart($('#ch-pie'), {
      tooltip: { trigger: 'item', formatter: '{b}: {c} tce（{d}%）' },
      legend: { bottom: 0 },
      series: [{
        type: 'pie', radius: ['35%', '65%'], center: ['50%', '45%'],
        data: items.map(function (it) { return { name: it.name, value: it.tce }; }),
        label: { formatter: '{b}\n{d}%' }
      }]
    });

    // 月度趋势：按月份聚合各能源类型用量与费用
    var byMonth = {};
    (d.monthly || []).forEach(function (m) {
      if (!byMonth[m.month]) byMonth[m.month] = { qty: 0, amt: 0 };
      byMonth[m.month].qty += m.qty;
      byMonth[m.month].amt += m.amt;
    });
    var mkeys = Object.keys(byMonth).sort(function (a, b) { return a - b; });
    makeChart($('#ch-trend'), {
      tooltip: { trigger: 'axis' },
      legend: { data: ['用量', '费用'] },
      grid: { left: 60, right: 60, top: 40, bottom: 30 },
      xAxis: { type: 'category', data: mkeys.map(function (m) { return m + '月'; }) },
      yAxis: [{ type: 'value', name: '用量' }, { type: 'value', name: '费用(元)' }],
      series: [
        { name: '用量', type: 'line', smooth: true, data: mkeys.map(function (m) { return byMonth[m].qty; }), itemStyle: { color: '#2563eb' } },
        { name: '费用', type: 'bar', yAxisIndex: 1, data: mkeys.map(function (m) { return byMonth[m].amt; }), itemStyle: { color: '#93c5fd' } }
      ]
    });
  }
  sel.addEventListener('change', function () { load().catch(function (e) { toast(e.message, 'error'); }); });
  await load();
};

/* ==================== 能源与单元配置（仅 admin） ==================== */
PAGES.energyConfig = async function (el) {
  el.innerHTML =
    '<div class="card"><h3>能源类型</h3>' +
    '<div class="form-row">' +
    '<div class="form-item"><label>编码</label><input type="text" id="et-code"></div>' +
    '<div class="form-item"><label>名称</label><input type="text" id="et-name"></div>' +
    '<div class="form-item"><label>单位</label><input type="text" id="et-unit"></div>' +
    '<div class="form-item"><label>折标系数(tce)</label><input type="number" id="et-factor" step="any" min="0"></div>' +
    '<div class="form-item"><label>映射碳科目</label><input type="text" id="et-map"></div>' +
    '</div><div class="form-row">' +
    '<label style="font-size:13px"><input type="checkbox" id="et-carbon" checked> 计入碳排</label>' +
    '<label style="font-size:13px"><input type="checkbox" id="et-green"> 绿色能源</label>' +
    '<label style="font-size:13px"><input type="checkbox" id="et-enabled" checked> 启用</label>' +
    '<button class="btn btn-primary" id="et-save">保存类型</button>' +
    '</div>' +
    '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
    '<th>编码</th><th>名称</th><th>单位</th><th>折标系数</th><th>计入碳排</th><th>绿色能源</th><th>映射科目</th><th>启用</th><th>操作</th>' +
    '</tr></thead><tbody id="et-tbody"></tbody></table></div></div>' +

    '<div class="card"><h3>用能单元</h3>' +
    '<div class="form-row">' +
    '<div class="form-item"><label>编码</label><input type="text" id="eu-code"></div>' +
    '<div class="form-item"><label>名称</label><input type="text" id="eu-name"></div>' +
    '<div class="form-item"><label>上级单元</label><select id="eu-parent"><option value="">（无）</option></select></div>' +
    '<label style="font-size:13px"><input type="checkbox" id="eu-enabled" checked> 启用</label>' +
    '<button class="btn btn-primary" id="eu-save">保存单元</button>' +
    '</div>' +
    '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
    '<th>ID</th><th>编码</th><th>名称</th><th>上级</th><th>启用</th><th>操作</th>' +
    '</tr></thead><tbody id="eu-tbody"></tbody></table></div></div>';

  function yn(v) { return v ? '是' : '否'; }

  /* 能源类型列表 */
  async function loadTypes() {
    var list = await api('/api/energy/types');
    $('#et-tbody').innerHTML = list.length ? list.map(function (t) {
      return '<tr><td>' + esc(t.code) + '</td><td>' + esc(t.name) + '</td><td>' + esc(t.unit) + '</td>' +
        '<td>' + fmt(t.tce_factor, 4) + '</td><td>' + yn(t.in_carbon) + '</td><td>' + yn(t.is_green) + '</td>' +
        '<td>' + esc(t.map_source_code || '—') + '</td><td>' + yn(t.enabled) + '</td>' +
        '<td><button class="btn btn-sm" data-code="' + esc(t.code) + '">编辑</button></td></tr>';
    }).join('') : '<tr><td colspan="9" class="empty">暂无数据</td></tr>';
    $all('#et-tbody button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var t = list.filter(function (x) { return x.code === btn.getAttribute('data-code'); })[0];
        $('#et-code').value = t.code; $('#et-name').value = t.name; $('#et-unit').value = t.unit;
        $('#et-factor').value = t.tce_factor; $('#et-map').value = t.map_source_code || '';
        $('#et-carbon').checked = !!t.in_carbon; $('#et-green').checked = !!t.is_green; $('#et-enabled').checked = !!t.enabled;
      });
    });
  }

  /* 用能单元列表 */
  async function loadUnits() {
    var list = await api('/api/energy/units');
    var nameMap = {};
    list.forEach(function (u) { nameMap[u.id] = u.name; });
    $('#eu-parent').innerHTML = '<option value="">（无）</option>' + list.map(function (u) {
      return '<option value="' + u.id + '">' + esc(u.name) + '</option>';
    }).join('');
    $('#eu-tbody').innerHTML = list.length ? list.map(function (u) {
      return '<tr><td>' + u.id + '</td><td>' + esc(u.code) + '</td><td>' + esc(u.name) + '</td>' +
        '<td>' + esc(nameMap[u.parent_id] || '—') + '</td><td>' + yn(u.enabled) + '</td>' +
        '<td><button class="btn btn-sm" data-id="' + u.id + '">编辑</button></td></tr>';
    }).join('') : '<tr><td colspan="6" class="empty">暂无数据</td></tr>';
    $all('#eu-tbody button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var u = list.filter(function (x) { return String(x.id) === btn.getAttribute('data-id'); })[0];
        $('#eu-code').value = u.code; $('#eu-name').value = u.name;
        $('#eu-parent').value = u.parent_id || ''; $('#eu-enabled').checked = !!u.enabled;
      });
    });
  }

  $('#et-save').addEventListener('click', async function () {
    try {
      await postJson('/api/energy/types', {
        code: $('#et-code').value.trim(), name: $('#et-name').value.trim(),
        unit: $('#et-unit').value.trim(), tce_factor: Number($('#et-factor').value) || 0,
        map_source_code: $('#et-map').value.trim() || null,
        in_carbon: $('#et-carbon').checked, is_green: $('#et-green').checked, enabled: $('#et-enabled').checked
      });
      toast('保存成功', 'success');
      await loadTypes();
    } catch (e) { toast(e.message, 'error'); }
  });

  $('#eu-save').addEventListener('click', async function () {
    try {
      await postJson('/api/energy/units', {
        code: $('#eu-code').value.trim(), name: $('#eu-name').value.trim(),
        parent_id: $('#eu-parent').value ? Number($('#eu-parent').value) : null,
        enabled: $('#eu-enabled').checked
      });
      toast('保存成功', 'success');
      await loadUnits();
    } catch (e) { toast(e.message, 'error'); }
  });

  await Promise.all([loadTypes(), loadUnits()]);
};

/* ==================== 碳盘查填报 ==================== */
PAGES.carbonFill = async function (el) {
  el.innerHTML = '<div id="cf-head"></div><div id="cf-body"><div class="loading">加载中…</div></div>';
  var data = null;     // 当前年度盘查数据
  var curYear = new Date().getFullYear();

  function isLocked() {
    return readonly() || ['submitted', 'approved'].indexOf(data.inventory_status) >= 0;
  }

  /* 加载年度盘查数据并渲染 */
  async function load() {
    data = await api('/api/carbon/activities?year=' + curYear);
    renderHead();
    renderBody();
  }

  /* 顶部：年度选择 / 总产值 / 状态 / 操作按钮 / 提示 */
  function renderHead() {
    var locked = isLocked();
    var warnHtml = (data.warnings || []).map(function (w) {
      return '<div class="alert-warning">⚠ ' + esc(w) + '</div>';
    }).join('');

    // 台账映射参考
    var mappedArr = Object.keys(data.mapped || {}).map(function (k) {
      return esc(k) + ' = ' + fmt(data.mapped[k]);
    });
    var mapHint = mappedArr.length
      ? '<div class="alert-warning" style="background:#eff6ff;color:#1e40af">台账映射参考：' + mappedArr.join('；') +
        '；净电量 = ' + fmt(data.net_electricity) + ' kWh</div>'
      : '';

    var btns = '';
    if (!readonly()) {
      if (['none', 'draft'].indexOf(data.inventory_status) >= 0) {
        btns += '<button class="btn btn-primary" id="cf-save">保存填报</button>' +
          '<button class="btn btn-blue" id="cf-submit">提交盘查</button>';
      }
      if (data.inventory_status === 'submitted' && canAudit()) {
        btns += '<button class="btn btn-green" id="cf-approve">审定</button>';
      }
    }

    $('#cf-head').innerHTML =
      '<div class="card"><div class="form-row" style="margin-bottom:8px">' +
      '<div class="form-item"><label>年度</label><select id="cf-year">' + yearOptions(curYear) + '</select></div>' +
      '<div class="form-item"><label>年度总产值（万美金）</label><input type="number" id="cf-revenue" min="0" step="any" value="' +
        (data.total_revenue || '') + '"' + (locked ? ' disabled' : '') + '></div>' +
      '<div class="form-item"><label>盘查状态</label><div style="padding-top:6px">' + statusBadge(data.inventory_status) + '</div></div>' +
      '<div class="form-item"><label>碳排总量 / 强度</label><div style="padding-top:6px"><b>' + fmt(data.total) + '</b> tCO₂e　' +
        '<b>' + fmt(data.intensity) + '</b> tCO₂e/万美金</div></div>' +
      '</div><div>' + btns + '</div></div>' +
      warnHtml + mapHint;

    $('#cf-year').value = curYear;
    $('#cf-year').addEventListener('change', function () {
      curYear = Number(this.value);
      load().catch(function (e) { toast(e.message, 'error'); });
    });
    if ($('#cf-save')) $('#cf-save').addEventListener('click', save);
    if ($('#cf-submit')) $('#cf-submit').addEventListener('click', async function () {
      if (!confirm('提交后本年度填报将锁定，确认提交？')) return;
      try {
        await postJson('/api/carbon/inventory/' + curYear + '/submit');
        toast('已提交盘查', 'success');
        await load();
      } catch (e) { toast(e.message, 'error'); }
    });
    if ($('#cf-approve')) $('#cf-approve').addEventListener('click', async function () {
      if (!confirm('审定后数据将封存，确认审定？')) return;
      try {
        await postJson('/api/carbon/inventory/' + curYear + '/approve');
        toast('已审定', 'success');
        await load();
      } catch (e) { toast(e.message, 'error'); }
    });
  }

  /* 按范围分组渲染科目卡片 */
  function renderBody() {
    var locked = isLocked();
    var html = '';
    SCOPES.forEach(function (scope, idx) {
      var list = (data.details || []).filter(function (d) { return d.scope === scope; });
      if (!list.length) return;
      html += '<div class="scope-section"><div class="scope-title s' + (idx + 1) + '">' + scope +
        '　小计：' + fmt((data.groups || {})[scope]) + ' tCO₂e</div>';
      list.forEach(function (d) {
        var val = d.activity_value === null || d.activity_value === undefined ? '' : d.activity_value;
        html += '<div class="src-card" data-code="' + esc(d.source_code) + '" data-factor="' + (d.factor || 0) + '">' +
          '<div class="src-head"><div><span class="src-name">' + esc(d.name_zh) + '</span> ' +
          '<span class="src-meta">[' + esc(d.source_code) + ']　单位：' + esc(d.unit || '—') +
          '　因子：' + fmt(d.factor, 6) + (d.factor_ref ? '（' + esc(d.factor_ref) + '）' : '') + '</span></div>' +
          '<div>' + (d.pending ? '<span class="badge none">未填报</span> ' : '') +
          (d.guide ? '<button class="btn btn-sm" data-guide>填报指南</button>' : '') + '</div></div>' +
          '<div class="src-body">' +
          '<div class="form-item"><label>活动数据（' + esc(d.unit || '') + '）</label>' +
          '<input type="number" class="cf-input" min="0" step="any" value="' + val + '"' + (locked ? ' disabled' : '') + '></div>' +
          '<div class="preview">预计排放：<span class="cf-preview">' + fmt(val === '' ? 0 : val * (d.factor || 0)) + '</span> tCO₂e</div>' +
          (d.emission !== null && d.emission !== undefined ? '<div class="text-muted">已核算：' + fmt(d.emission) + ' tCO₂e</div>' : '') +
          '</div>' +
          (d.guide ? '<div class="src-guide" style="display:none">' + esc(d.guide) + '</div>' : '') +
          '</div>';
      });
      html += '</div>';
    });
    $('#cf-body').innerHTML = html || '<div class="card"><div class="empty">该年度暂无盘查科目</div></div>';

    // 输入联动：实时排放预览
    $all('.cf-input').forEach(function (input) {
      input.addEventListener('input', function () {
        var card = input.closest('.src-card');
        var factor = Number(card.getAttribute('data-factor')) || 0;
        var v = input.value === '' ? 0 : Number(input.value);
        card.querySelector('.cf-preview').textContent = fmt(v * factor);
      });
    });
    // 填报指南展开/折叠
    $all('[data-guide]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var g = btn.closest('.src-card').querySelector('.src-guide');
        g.style.display = g.style.display === 'none' ? '' : 'none';
      });
    });
  }

  /* 保存填报 */
  async function save() {
    var items = [];
    $all('.src-card').forEach(function (card) {
      var input = card.querySelector('.cf-input');
      if (input.value !== '') {
        items.push({ source_code: card.getAttribute('data-code'), activity_value: Number(input.value) });
      }
    });
    if (!items.length) { toast('没有需要保存的数据', 'error'); return; }
    var body = { year: curYear, items: items };
    var rev = $('#cf-revenue').value;
    if (rev !== '') body.total_revenue = Number(rev);
    try {
      await postJson('/api/carbon/activities', body);
      toast('保存成功', 'success');
      await load();
    } catch (e) { toast(e.message, 'error'); }
  }

  await load();
};

/* ==================== 碳排分析 ==================== */
PAGES.carbonAnalysis = async function (el) {
  el.innerHTML = yearBar('ca-year') + '<div id="ca-body"><div class="loading">加载中…</div></div>';
  var sel = $('#ca-year');
  var data = null;
  var tab = '总量';

  async function load() {
    disposeCharts();
    data = await api('/api/analysis/carbon?year=' + sel.value);
    render();
  }

  function render() {
    var ov = data.overview || {};
    var g = ov.groups || {};
    var yoyTxt = ov.yoy ? ((ov.yoy.rate > 0 ? '+' : '') + fmt(ov.yoy.rate, 1) + '%') : '—';
    var baseline = data.baseline;
    var target = data.target;

    // ── KPI 概览卡 ──
    var kpiHtml =
      kpiCard('碳排总量', fmt(ov.total), 'tCO₂e') +
      kpiCard('范围一', fmt(g['范围一']), 'tCO₂e') +
      kpiCard('范围二', fmt(g['范围二']), 'tCO₂e') +
      kpiCard('范围三', fmt(g['范围三']), 'tCO₂e') +
      kpiCard('绿电减排', fmt(ov.green_reduction), 'tCO₂e') +
      kpiCard('产值碳强度', fmt(ov.intensity), 'tCO₂e/万美金') +
      kpiCard('同比', yoyTxt, '');
    if (baseline) {
      kpiHtml += kpiCard('距' + baseline.year + '基准年',
        (baseline.rate >= 0 ? '↓' : '↑') + fmt(Math.abs(baseline.rate), 1), '%');
    }
    if (target && target.progress_rate !== null && target.progress_rate !== undefined) {
      kpiHtml += kpiCard('碳目标完成率', fmt(target.progress_rate, 1), '%');
    }

    // ── 规则引擎洞察 ──
    var insights = data.insights || [];
    var insightHtml = insights.length
      ? '<div class="card"><h3>分析洞察</h3><ul class="insight-list">' +
        insights.map(function (it) {
          return '<li class="insight insight-' + esc(it.level) + '">' + esc(it.text) + '</li>';
        }).join('') + '</ul></div>' : '';

    // ── 目标 / 基准进度条 ──
    function progressRow(label, pct, text) {
      var w = pct === null || pct === undefined ? 0 : Math.max(0, Math.min(100, pct));
      return '<div class="progress-row"><div class="progress-label">' + esc(label) + '</div>' +
        '<div class="progress-track"><div class="progress-fill" style="width:' + w + '%"></div></div>' +
        '<div class="progress-text">' + esc(text) + '</div></div>';
    }
    var progRows = '';
    if (target && target.total_reduction) {
      var pr = target.progress_rate;
      progRows += progressRow(
        '碳中和目标（' + target.base_year + ' → ' + target.target_year +
        '，累计减排 ' + fmt(target.total_reduction) + ' tCO₂e）',
        pr, pr === null || pr === undefined ? '本年暂无盘查数据' : fmt(pr, 1) + '%');
    }
    if (target && target.carbon_goal) {
      var gap = target.goal_gap;
      progRows += progressRow(
        data.year + ' 年度碳目标 ' + fmt(target.carbon_goal) + ' tCO₂e',
        gap === null || gap === undefined ? null
          : Math.max(0, Math.min(100, (ov.total || 0) / target.carbon_goal * 100)),
        gap === null || gap === undefined ? '本年暂无盘查数据'
          : '当前 ' + fmt(ov.total) + ' tCO₂e（' + (gap > 0 ? '超出 ' : '余量 ') + fmt(Math.abs(gap)) + '）');
    }
    if (baseline) {
      progRows += progressRow(
        '距 ' + baseline.year + ' 基准年（' + fmt(baseline.total) + ' tCO₂e）降幅',
        baseline.rate, (baseline.rate >= 0 ? '下降 ' : '上升 ') + fmt(Math.abs(baseline.rate), 1) + '%');
    }
    var progHtml = progRows
      ? '<div class="card"><h3>目标与基准进度</h3>' + progRows + '</div>' : '';

    $('#ca-body').innerHTML =
      '<div class="kpi-grid">' + kpiHtml + '</div>' +
      insightHtml +
      '<div class="chart-row">' +
      '<div class="card"><h3>多年度趋势（范围结构 × 强度）</h3><div class="chart" id="ch-trend"></div></div>' +
      '<div class="card"><h3>三范围占比</h3><div class="chart" id="ch-donut"></div></div>' +
      '</div>' +
      '<div class="card"><h3>月度碳排估算（基于已审核台账 × 当期因子' +
      (data.has_data ? '' : '；本年度盘查尚未填报，以下为估算口径') + '）</h3>' +
      '<div class="chart" id="ch-monthly"></div></div>' +
      '<div class="chart-row">' +
      '<div class="card"><h3>排放热点帕累托</h3><div class="chart" id="ch-pareto"></div></div>' +
      '<div class="card"><h3>分单元电力碳排结构</h3><div class="chart" id="ch-unit"></div></div>' +
      '</div>' +
      progHtml +
      '<div class="card"><h3>排放明细（点击行查看溯源）</h3>' +
      '<div class="tabs" id="ca-tabs">' +
      ['总量', '范围一', '范围二', '范围三'].map(function (t) {
        return '<div class="tab' + (t === tab ? ' active' : '') + '" data-tab="' + t + '">' + t + '</div>';
      }).join('') + '</div>' +
      '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
      '<th>科目</th><th>范围</th><th>活动数据</th><th>因子</th><th>排放量(tCO₂e)</th><th>数据来源</th>' +
      '</tr></thead><tbody id="ca-tbody"></tbody></table></div></div>';

    // Tab 切换（过滤明细表）
    $all('#ca-tabs .tab').forEach(function (t) {
      t.addEventListener('click', function () {
        tab = t.getAttribute('data-tab');
        render();
      });
    });

    // 多年度趋势：范围堆叠柱 + 强度双轴折线
    var trend = data.trend || [];
    makeChart($('#ch-trend'), {
      tooltip: { trigger: 'axis' },
      legend: { bottom: 0 },
      grid: { left: 70, right: 70, top: 30, bottom: 55 },
      xAxis: { type: 'category', data: trend.map(function (t) { return t.year + '年'; }) },
      yAxis: [
        { type: 'value', name: 'tCO₂e' },
        { type: 'value', name: 'tCO₂e/万美金', splitLine: { show: false } }
      ],
      series: SCOPES.map(function (s) {
        return { name: s, type: 'bar', stack: 'total', barWidth: 26,
          itemStyle: { color: SCOPE_COLORS[s] },
          data: trend.map(function (t) { return (t.groups || {})[s] || 0; }) };
      }).concat([{ name: '产值碳强度', type: 'line', yAxisIndex: 1, smooth: true,
        itemStyle: { color: '#9333ea' },
        data: trend.map(function (t) { return t.intensity; }) }])
    });

    // 三范围占比环形图
    makeChart($('#ch-donut'), {
      tooltip: { trigger: 'item', formatter: '{b}: {c} tCO₂e（{d}%）' },
      legend: { bottom: 0 },
      series: [{
        type: 'pie', radius: ['40%', '68%'], center: ['50%', '45%'],
        data: SCOPES.map(function (s) {
          return { name: s, value: g[s] || 0, itemStyle: { color: SCOPE_COLORS[s] } };
        }),
        label: { formatter: '{b}\n{d}%' }
      }]
    });

    // 月度碳排估算：范围一/二堆叠柱 + 去年同期虚线
    var mon = data.monthly_estimate || [];
    var monPrev = data.monthly_estimate_prev;
    var monSeries = [
      { name: '范围一（估算）', type: 'bar', stack: 'm', barWidth: 14,
        itemStyle: { color: SCOPE_COLORS['范围一'] },
        data: mon.map(function (m) { return m.scope1; }) },
      { name: '范围二（估算）', type: 'bar', stack: 'm',
        itemStyle: { color: SCOPE_COLORS['范围二'] },
        data: mon.map(function (m) { return m.scope2; }) }
    ];
    if (monPrev) {
      monSeries.push({ name: (data.year - 1) + '年同期（估算）', type: 'line', smooth: true,
        itemStyle: { color: '#94a3b8' }, lineStyle: { type: 'dashed' },
        data: monPrev.map(function (m) { return m.total; }) });
    }
    makeChart($('#ch-monthly'), {
      tooltip: { trigger: 'axis' },
      legend: { bottom: 0 },
      grid: { left: 70, right: 20, top: 30, bottom: 55 },
      xAxis: { type: 'category', data: mon.map(function (m) { return m.month + '月'; }) },
      yAxis: { type: 'value', name: 'tCO₂e' },
      series: monSeries
    });

    // 帕累托热点：排放量柱 + 累计占比线 + 80% 参考线
    var par = data.pareto || [];
    makeChart($('#ch-pareto'), {
      tooltip: { trigger: 'axis' },
      grid: { left: 60, right: 55, top: 30, bottom: 85 },
      xAxis: { type: 'category', data: par.map(function (p) { return p.name_zh; }),
        axisLabel: { rotate: 28 } },
      yAxis: [
        { type: 'value', name: 'tCO₂e' },
        { type: 'value', name: '累计%', max: 100, splitLine: { show: false } }
      ],
      series: [
        { name: '排放量', type: 'bar', barWidth: 18, itemStyle: { color: '#e85d3a' },
          data: par.map(function (p) { return p.emission; }) },
        { name: '累计占比', type: 'line', yAxisIndex: 1, itemStyle: { color: '#2563eb' },
          data: par.map(function (p) { return p.cumulative; }),
          markLine: { symbol: 'none', data: [{ yAxis: 80 }],
            lineStyle: { type: 'dashed', color: '#f59e0b' } } }
      ]
    });

    // 分单元电力碳排结构（分表口径，占比为全厂总表口径的百分比）
    var units = data.unit_breakdown || [];
    makeChart($('#ch-unit'), {
      tooltip: { trigger: 'item',
        formatter: function (p) {
          var u = units[p.dataIndex];
          return esc(u.unit_name) + '：' + fmt(u.emission) + ' tCO₂e（' + u.share + '%）<br/>' +
            '用电量 ' + fmt(u.quantity) + ' kWh';
        } },
      grid: { left: 90, right: 70, top: 20, bottom: 30 },
      xAxis: { type: 'value', name: 'tCO₂e' },
      yAxis: { type: 'category', inverse: true,
        data: units.map(function (u) { return u.unit_name; }) },
      series: [{ type: 'bar', barWidth: 16, itemStyle: { color: '#2563eb' },
        label: { show: true, position: 'right',
          formatter: function (p) { return units[p.dataIndex].share + '%'; } },
        data: units.map(function (u) { return u.emission; }) }]
    });

    // 明细表（点击溯源）
    var details = (data.details || []).filter(function (d) { return tab === '总量' || d.scope === tab; })
      .slice().sort(function (a, b) { return (b.emission || 0) - (a.emission || 0); });
    $('#ca-tbody').innerHTML = details.length ? details.map(function (d) {
      return '<tr data-code="' + esc(d.source_code) + '" style="cursor:pointer">' +
        '<td>' + esc(d.name_zh) + '</td><td>' + esc(d.scope) + '</td>' +
        '<td>' + fmt(d.activity_value) + ' ' + esc(d.unit || '') + '</td>' +
        '<td>' + fmt(d.factor, 6) + '</td><td><b>' + fmt(d.emission) + '</b></td>' +
        '<td>' + esc(d.data_origin || '—') + '</td></tr>';
    }).join('') : '<tr><td colspan="6" class="empty">暂无数据</td></tr>';
    $all('#ca-tbody tr[data-code]').forEach(function (tr) {
      tr.addEventListener('click', function () { showTrace(tr.getAttribute('data-code')); });
    });
  }

  /* 溯源详情弹窗 */
  async function showTrace(sourceCode) {
    try {
      var t = await api('/api/analysis/trace?year=' + sel.value + '&source_code=' + encodeURIComponent(sourceCode));
      var src = t.source || {};
      var act = t.activity || {};
      var fv = t.factor_version || {};
      var inv = t.inventory || {};
      openModal('溯源详情 - ' + (src.name_zh || sourceCode),
        '<table class="data-table"><tbody>' +
        '<tr><th>科目编码</th><td>' + esc(src.code) + '</td></tr>' +
        '<tr><th>所属范围</th><td>' + esc(src.scope) + '</td></tr>' +
        '<tr><th>活动数据</th><td>' + fmt(act.activity_value) + ' ' + esc(src.unit || '') + '</td></tr>' +
        '<tr><th>数据来源</th><td>' + esc(act.data_origin || '—') + '</td></tr>' +
        '<tr><th>排放因子</th><td>' + fmt(fv.factor, 6) + '</td></tr>' +
        '<tr><th>因子有效期</th><td>' + (fv.year_from || '—') + ' ~ ' + (fv.year_to || '至今') + '</td></tr>' +
        '<tr><th>因子出处</th><td>' + esc(fv.ref_source || '—') + '</td></tr>' +
        '<tr><th>排放量</th><td><b>' + fmt(act.emission) + ' tCO₂e</b></td></tr>' +
        '<tr><th>填报人</th><td>' + esc(t.reporter_name || '—') + '</td></tr>' +
        '<tr><th>填报时间</th><td>' + esc(act.reported_at || '—') + '</td></tr>' +
        '<tr><th>审定人</th><td>' + esc(t.approver_name || '—') + '</td></tr>' +
        '<tr><th>盘查状态</th><td>' + statusBadge(inv.status || 'none') + '</td></tr>' +
        '</tbody></table>');
    } catch (e) { toast(e.message, 'error'); }
  }

  sel.addEventListener('change', function () { load().catch(function (e) { toast(e.message, 'error'); }); });
  await load();
};

/* ==================== 科目库/因子库（仅 admin） ==================== */
PAGES.sourcesFactors = async function (el) {
  var sources = [];

  el.innerHTML =
    '<div class="card"><h3>排放科目库</h3>' +
    '<div class="form-row">' +
    '<div class="form-item"><label>编码</label><input type="text" id="sf-code"></div>' +
    '<div class="form-item"><label>中文名</label><input type="text" id="sf-namezh"></div>' +
    '<div class="form-item"><label>英文名</label><input type="text" id="sf-nameen"></div>' +
    '<div class="form-item"><label>范围</label><select id="sf-scope">' +
      SCOPES.map(function (s) { return '<option>' + s + '</option>'; }).join('') + '</select></div>' +
    '<div class="form-item"><label>单位</label><input type="text" id="sf-unit"></div>' +
    '</div><div class="form-row">' +
    '<div class="form-item"><label>类别</label><input type="text" id="sf-type"></div>' +
    '<div class="form-item"><label>责任部门编码</label><input type="text" id="sf-dept"></div>' +
    '<div class="form-item"><label>因子出处</label><input type="text" id="sf-fref"></div>' +
    '<div class="form-item"><label>映射能源类型</label><input type="text" id="sf-map"></div>' +
    '<div class="form-item"><label>换算系数</label><input type="number" id="sf-conv" step="any" value="1"></div>' +
    '<div class="form-item"><label>排序号</label><input type="number" id="sf-sort" value="0"></div>' +
    '<label style="font-size:13px"><input type="checkbox" id="sf-enabled" checked> 启用</label>' +
    '</div><div class="form-row">' +
    '<div class="form-item" style="flex:1"><label>填报指南</label><input type="text" id="sf-guide" style="width:100%"></div>' +
    '<button class="btn btn-primary" id="sf-save">保存科目</button>' +
    '</div>' +
    '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
    '<th>编码</th><th>名称</th><th>范围</th><th>单位</th><th>责任部门</th><th>映射能源</th><th>启用</th><th>操作</th>' +
    '</tr></thead><tbody id="sf-tbody"></tbody></table></div></div>' +

    '<div class="card"><h3>排放因子库</h3>' +
    '<div class="form-row">' +
    '<div class="form-item"><label>科目</label><select id="fc-source"></select></div>' +
    '<div class="form-item"><label>因子值</label><input type="number" id="fc-factor" step="any" min="0"></div>' +
    '<div class="form-item"><label>生效年(起)</label><input type="number" id="fc-from" min="2000" max="2100"></div>' +
    '<div class="form-item"><label>生效年(止)</label><input type="number" id="fc-to" min="2000" max="2100" placeholder="留空=长期"></div>' +
    '<div class="form-item"><label>出处</label><input type="text" id="fc-ref"></div>' +
    '<div class="form-item"><label>变更原因（必填）</label><input type="text" id="fc-reason"></div>' +
    '<button class="btn btn-primary" id="fc-save">保存因子</button>' +
    '<button class="btn" id="fc-cancel" style="display:none">取消编辑</button>' +
    '</div>' +
    '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
    '<th>科目</th><th>因子值</th><th>生效区间</th><th>出处</th><th>变更原因</th><th>创建时间</th><th>操作</th>' +
    '</tr></thead><tbody id="fc-tbody"></tbody></table></div></div>';

  var editingFactorId = null;

  /* 科目列表 */
  async function loadSources() {
    sources = await api('/api/carbon/sources?all=true');
    $('#sf-tbody').innerHTML = sources.length ? sources.map(function (s) {
      return '<tr><td>' + esc(s.code) + '</td><td>' + esc(s.name_zh) + '</td><td>' + esc(s.scope) + '</td>' +
        '<td>' + esc(s.unit) + '</td><td>' + esc(s.dept_code || '—') + '</td><td>' + esc(s.map_energy_type || '—') + '</td>' +
        '<td>' + (s.enabled ? '是' : '否') + '</td>' +
        '<td><button class="btn btn-sm" data-code="' + esc(s.code) + '">编辑</button></td></tr>';
    }).join('') : '<tr><td colspan="8" class="empty">暂无数据</td></tr>';
    $('#fc-source').innerHTML = sources.map(function (s) {
      return '<option value="' + esc(s.code) + '">' + esc(s.name_zh) + '（' + esc(s.code) + '）</option>';
    }).join('');
    $all('#sf-tbody button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var s = sources.filter(function (x) { return x.code === btn.getAttribute('data-code'); })[0];
        $('#sf-code').value = s.code; $('#sf-namezh').value = s.name_zh; $('#sf-nameen').value = s.name_en || '';
        $('#sf-scope').value = s.scope; $('#sf-unit').value = s.unit; $('#sf-type').value = s.source_type || '';
        $('#sf-dept').value = s.dept_code || ''; $('#sf-fref').value = s.factor_ref || '';
        $('#sf-map').value = s.map_energy_type || ''; $('#sf-conv').value = s.map_convert;
        $('#sf-sort').value = s.sort_no; $('#sf-guide').value = s.guide || ''; $('#sf-enabled').checked = !!s.enabled;
      });
    });
  }

  /* 因子列表 */
  async function loadFactors() {
    var list = await api('/api/carbon/factors');
    $('#fc-tbody').innerHTML = list.length ? list.map(function (f) {
      return '<tr><td>' + esc(f.source_code) + '</td><td><b>' + fmt(f.factor, 6) + '</b></td>' +
        '<td>' + f.year_from + ' ~ ' + (f.year_to || '至今') + '</td><td>' + esc(f.ref_source) + '</td>' +
        '<td>' + esc(f.change_reason) + '</td><td>' + esc(f.created_at || '—') + '</td>' +
        '<td><button class="btn btn-sm" data-id="' + f.id + '">编辑</button></td></tr>';
    }).join('') : '<tr><td colspan="7" class="empty">暂无数据</td></tr>';
    $all('#fc-tbody button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var f = list.filter(function (x) { return String(x.id) === btn.getAttribute('data-id'); })[0];
        editingFactorId = f.id;
        $('#fc-cancel').style.display = '';
        $('#fc-source').value = f.source_code; $('#fc-factor').value = f.factor;
        $('#fc-from').value = f.year_from; $('#fc-to').value = f.year_to || '';
        $('#fc-ref').value = f.ref_source; $('#fc-reason').value = f.change_reason;
      });
    });
  }

  $('#sf-save').addEventListener('click', async function () {
    try {
      await postJson('/api/carbon/sources', {
        code: $('#sf-code').value.trim(), name_zh: $('#sf-namezh').value.trim(),
        name_en: $('#sf-nameen').value.trim() || null, scope: $('#sf-scope').value,
        unit: $('#sf-unit').value.trim(), source_type: $('#sf-type').value.trim() || null,
        dept_code: $('#sf-dept').value.trim() || null, factor_ref: $('#sf-fref').value.trim() || null,
        map_energy_type: $('#sf-map').value.trim() || null,
        map_convert: Number($('#sf-conv').value) || 1, sort_no: Number($('#sf-sort').value) || 0,
        guide: $('#sf-guide').value || null, enabled: $('#sf-enabled').checked
      });
      toast('保存成功', 'success');
      await loadSources();
    } catch (e) { toast(e.message, 'error'); }
  });

  $('#fc-save').addEventListener('click', async function () {
    var body = {
      source_code: $('#fc-source').value, factor: Number($('#fc-factor').value),
      year_from: Number($('#fc-from').value),
      year_to: $('#fc-to').value === '' ? null : Number($('#fc-to').value),
      ref_source: $('#fc-ref').value.trim(), change_reason: $('#fc-reason').value.trim()
    };
    try {
      if (editingFactorId) {
        await putJson('/api/carbon/factors/' + editingFactorId, body);
      } else {
        await postJson('/api/carbon/factors', body);
      }
      toast('保存成功', 'success');
      editingFactorId = null;
      $('#fc-cancel').style.display = 'none';
      await loadFactors();
    } catch (e) { toast(e.message, 'error'); }
  });
  $('#fc-cancel').addEventListener('click', function () {
    editingFactorId = null;
    this.style.display = 'none';
  });

  await Promise.all([loadSources(), loadFactors()]);
};

/* ==================== 碳中和目标 ==================== */
PAGES.targetsCarbon = async function (el) {
  el.innerHTML = '<div id="tc-body"><div class="loading">加载中…</div></div>';

  async function load() {
    disposeCharts();
    var d = await api('/api/targets/carbon');
    var t = d.target || {};

    $('#tc-body').innerHTML =
      '<div class="kpi-grid">' +
      kpiCard('目标年', t.target_year || '—', '') +
      kpiCard('基准年', t.base_year || '—', '') +
      kpiCard('基准年排放量', fmt(t.base_emission), 'tCO₂e') +
      kpiCard('总减碳目标', fmt(t.total_reduction), 'tCO₂e') +
      kpiCard('当前排放量', fmt(d.current_emission), 'tCO₂e') +
      kpiCard('目标完成率', d.progress === null || d.progress === undefined ? '—' : fmt(d.progress, 1) + '%', '') +
      '</div>' +
      '<div class="card"><h3>目标路径（虚线=计划，实线=实际）</h3><div class="chart" id="ch-target"></div></div>' +
      (canAudit() ?
      '<div class="card"><h3>设置碳中和目标</h3><div class="form-row" style="margin-bottom:0">' +
      '<div class="form-item"><label>目标年</label><input type="number" id="tg-year" min="2000" max="2100"></div>' +
      '<div class="form-item"><label>基准年</label><input type="number" id="tg-base" min="2000" max="2100"></div>' +
      '<div class="form-item"><label>总减碳目标（tCO₂e）</label><input type="number" id="tg-red" min="0" step="any"></div>' +
      '<button class="btn btn-primary" id="tg-save">保 存</button>' +
      '</div></div>' : '');

    // 趋势图：planned 虚线 + actual 实线
    var trend = d.trend || [];
    makeChart($('#ch-target'), {
      tooltip: { trigger: 'axis' },
      legend: { data: ['计划路径', '实际排放'] },
      grid: { left: 70, right: 20, top: 40, bottom: 30 },
      xAxis: { type: 'category', data: trend.map(function (x) { return x.year + '年'; }) },
      yAxis: { type: 'value', name: 'tCO₂e' },
      series: [
        { name: '计划路径', type: 'line', data: trend.map(function (x) { return x.planned; }),
          lineStyle: { type: 'dashed' }, itemStyle: { color: '#64748b' } },
        { name: '实际排放', type: 'line', data: trend.map(function (x) { return x.actual; }),
          itemStyle: { color: '#e85d3a' }, connectNulls: false }
      ]
    });

    if ($('#tg-save')) {
      $('#tg-year').value = t.target_year || '';
      $('#tg-base').value = t.base_year || '';
      $('#tg-red').value = t.total_reduction || '';
      $('#tg-save').addEventListener('click', async function () {
        try {
          await postJson('/api/targets/carbon', {
            target_year: Number($('#tg-year').value),
            base_year: Number($('#tg-base').value),
            total_reduction: Number($('#tg-red').value)
          });
          toast('目标已保存', 'success');
          await load();
        } catch (e) { toast(e.message, 'error'); }
      });
    }
  }

  await load();
};

/* ==================== 年度计划 ==================== */
PAGES.targetsAnnual = async function (el) {
  el.innerHTML = yearBar('ta-year') + '<div id="ta-body"><div class="loading">加载中…</div></div>';
  var sel = $('#ta-year');

  async function load() {
    var d = await api('/api/targets/annual?year=' + sel.value);
    var p = d.plan || {};
    var a = d.actual || {};
    var warnHtml = (d.warnings || []).map(function (w) {
      var names = { carbon_goal: '碳排目标', energy_goal_tce: '能耗目标', cost_budget: '费用预算' };
      return '<div class="alert-danger">⚠ 预警：' + (names[w.item] || w.item) + ' 实际进度已达 ' + fmt(w.rate, 1) + '%（≥90%）</div>';
    }).join('');

    function vsRow(label, plan, actual, unit) {
      return '<div class="kpi-card"><div class="kpi-label">' + label + '</div>' +
        '<div class="kpi-value">' + fmt(plan) + '<span class="kpi-unit">' + unit + '（目标）</span></div>' +
        '<div style="margin-top:4px;color:var(--text-sub)">实际：' + fmt(actual) + ' ' + unit + '</div></div>';
    }

    $('#ta-body').innerHTML =
      warnHtml +
      '<div class="kpi-grid" style="grid-template-columns:repeat(3,1fr)">' +
      vsRow('碳排目标', p.carbon_goal, a.carbon, 'tCO₂e') +
      vsRow('能耗目标', p.energy_goal_tce, a.energy_tce, 'tce') +
      vsRow('费用预算', p.cost_budget, a.cost, '元') +
      '</div>' +
      (canAudit() ?
      '<div class="card"><h3>设置年度计划</h3><div class="form-row" style="margin-bottom:0">' +
      '<div class="form-item"><label>年度</label><input type="number" id="ap-year" value="' + sel.value + '" min="2000" max="2100"></div>' +
      '<div class="form-item"><label>碳排目标（tCO₂e）</label><input type="number" id="ap-carbon" min="0" step="any" value="' + (p.carbon_goal || '') + '"></div>' +
      '<div class="form-item"><label>能耗目标（tce）</label><input type="number" id="ap-energy" min="0" step="any" value="' + (p.energy_goal_tce || '') + '"></div>' +
      '<div class="form-item"><label>费用预算（元）</label><input type="number" id="ap-cost" min="0" step="any" value="' + (p.cost_budget || '') + '"></div>' +
      '<button class="btn btn-primary" id="ap-save">保 存</button>' +
      '</div></div>' : '');

    if ($('#ap-save')) {
      $('#ap-save').addEventListener('click', async function () {
        function nv(id) { var v = $(id).value; return v === '' ? undefined : Number(v); }
        try {
          await postJson('/api/targets/annual', {
            year: Number($('#ap-year').value),
            carbon_goal: nv('#ap-carbon'), energy_goal_tce: nv('#ap-energy'), cost_budget: nv('#ap-cost')
          });
          toast('年度计划已保存', 'success');
          await load();
        } catch (e) { toast(e.message, 'error'); }
      });
    }
  }

  sel.addEventListener('change', function () { load().catch(function (e) { toast(e.message, 'error'); }); });
  await load();
};

/* ==================== 客户档案 ==================== */
PAGES.customers = async function (el) {
  var writable = canAudit(); // admin/manager 可写
  el.innerHTML =
    (writable ?
    '<div class="card"><h3>客户信息</h3><div class="form-row">' +
    '<div class="form-item"><label>客户编码</label><input type="text" id="cu-code"></div>' +
    '<div class="form-item"><label>中文名</label><input type="text" id="cu-namezh"></div>' +
    '<div class="form-item"><label>英文名</label><input type="text" id="cu-nameen"></div>' +
    '<div class="form-item"><label>联系方式</label><input type="text" id="cu-contact"></div>' +
    '<label style="font-size:13px"><input type="checkbox" id="cu-enabled" checked> 启用</label>' +
    '<button class="btn btn-primary" id="cu-save">保存客户</button>' +
    '</div></div>' +
    '<div class="card"><h3>年度营收</h3><div class="form-row" style="margin-bottom:0">' +
    '<div class="form-item"><label>年度</label><input type="number" id="rv-year" value="' + new Date().getFullYear() + '" min="2000" max="2100"></div>' +
    '<div class="form-item"><label>客户</label><select id="rv-code"></select></div>' +
    '<div class="form-item"><label>营收（万美金）</label><input type="number" id="rv-revenue" min="0" step="any"></div>' +
    '<button class="btn btn-primary" id="rv-save">保存营收</button>' +
    '</div></div>' : '') +
    '<div class="table-wrap"><table class="data-table"><thead><tr>' +
    '<th>编码</th><th>中文名</th><th>英文名</th><th>联系方式</th><th>启用</th><th>年度营收（万美金）</th>' +
    (writable ? '<th>操作</th>' : '') +
    '</tr></thead><tbody id="cu-tbody"><tr><td colspan="7" class="empty">加载中…</td></tr></tbody></table></div>';

  async function load() {
    var list = await api('/api/customers');
    if (writable) {
      $('#rv-code').innerHTML = list.map(function (c) {
        return '<option value="' + esc(c.code) + '">' + esc(c.name_zh) + '</option>';
      }).join('');
    }
    $('#cu-tbody').innerHTML = list.length ? list.map(function (c) {
      var revs = (c.revenues || []).map(function (r) { return r.year + '年：' + fmt(r.revenue); }).join('；') || '—';
      return '<tr><td>' + esc(c.code) + '</td><td>' + esc(c.name_zh) + '</td><td>' + esc(c.name_en || '—') + '</td>' +
        '<td>' + esc(c.contact || '—') + '</td><td>' + (c.enabled ? '是' : '否') + '</td><td>' + esc(revs) + '</td>' +
        (writable ? '<td><button class="btn btn-sm" data-code="' + esc(c.code) + '">编辑</button></td>' : '') + '</tr>';
    }).join('') : '<tr><td colspan="7" class="empty">暂无客户</td></tr>';

    if (writable) {
      $all('#cu-tbody button').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var c = list.filter(function (x) { return x.code === btn.getAttribute('data-code'); })[0];
          $('#cu-code').value = c.code; $('#cu-namezh').value = c.name_zh;
          $('#cu-nameen').value = c.name_en || ''; $('#cu-contact').value = c.contact || '';
          $('#cu-enabled').checked = !!c.enabled;
        });
      });
    }
  }

  if (writable) {
    $('#cu-save').addEventListener('click', async function () {
      try {
        await postJson('/api/customers', {
          code: $('#cu-code').value.trim(), name_zh: $('#cu-namezh').value.trim(),
          name_en: $('#cu-nameen').value.trim() || null,
          contact: $('#cu-contact').value.trim() || null,
          enabled: $('#cu-enabled').checked
        });
        toast('保存成功', 'success');
        await load();
      } catch (e) { toast(e.message, 'error'); }
    });
    $('#rv-save').addEventListener('click', async function () {
      try {
        await postJson('/api/customers/revenue', {
          year: Number($('#rv-year').value),
          customer_code: $('#rv-code').value,
          revenue: Number($('#rv-revenue').value)
        });
        toast('营收已保存', 'success');
        await load();
      } catch (e) { toast(e.message, 'error'); }
    });
  }

  await load();
};

/* ==================== 分摊账单 ==================== */
PAGES.allocation = async function (el) {
  el.innerHTML = yearBar('al-year') + '<div id="al-body"><div class="loading">加载中…</div></div>';
  var sel = $('#al-year');

  async function load() {
    var body = $('#al-body');
    var d;
    try {
      d = await api('/api/allocation?year=' + sel.value);
    } catch (e) {
      // 未审定等 400 错误直接页面内提示
      body.innerHTML = '<div class="card"><div class="alert-warning">' + esc(e.message) + '</div></div>';
      return;
    }
    var g = d.groups || {};

    var html = '';
    if (d.warning) html += '<div class="alert-danger">⚠ ' + esc(d.warning) + '</div>';
    html += '<div class="kpi-grid">' +
      kpiCard('审定碳排总量', fmt(d.total_emission), 'tCO₂e') +
      kpiCard('客户营收合计', fmt(d.total_revenue), '万美金') +
      kpiCard('分摊比例合计', fmt((d.share_sum || 0) * 100, 1), '%') +
      '</div>';

    if (!(d.items || []).length) {
      html += '<div class="card"><div class="empty">暂无客户分摊数据</div></div>';
    } else {
      // 每客户一张账单卡（含范围构成）
      d.items.forEach(function (it) {
        html += '<div class="card"><h3>' + esc(it.name_zh) +
          (it.name_en ? ' <span class="text-muted" style="font-weight:400">' + esc(it.name_en) + '</span>' : '') + '</h3>' +
          '<div class="kpi-grid" style="margin-bottom:10px">' +
          kpiCard('年度营收', fmt(it.revenue), '万美金') +
          kpiCard('分摊比例', fmt((it.share || 0) * 100, 2), '%') +
          kpiCard('分摊碳排', fmt(it.allocated), 'tCO₂e') +
          kpiCard('营收碳强度', fmt(it.intensity), 'tCO₂e/万美金') +
          '</div>' +
          '<div class="form-row" style="margin-bottom:0">' +
          SCOPES.map(function (s) {
            return '<span class="badge" style="background:' + SCOPE_COLORS[s] + '1a;color:' + SCOPE_COLORS[s] + '">' +
              s + '：' + fmt((it.share || 0) * (g[s] || 0)) + ' tCO₂e</span>';
          }).join('　') +
          '</div></div>';
      });
    }
    body.innerHTML = html;
  }

  sel.addEventListener('change', function () { load().catch(function (e) { toast(e.message, 'error'); }); });
  await load();
};

/* ==================== 报表中心 ==================== */
PAGES.reports = async function (el) {
  el.innerHTML =
    '<div class="card"><div class="form-row" style="margin-bottom:0">' +
    '<div class="form-item"><label>年度</label><select id="rp-year">' + yearOptions(new Date().getFullYear()) + '</select></div>' +
    '<button class="btn btn-primary" id="rp-excel">导出 Excel 总账</button>' +
    '<button class="btn btn-blue" id="rp-pdf">导出 PDF 报告</button>' +
    '</div><div class="text-muted" style="margin-top:8px;font-size:12px">PDF 报告仅限已审定年度导出；导出的文件保留 30 天。</div></div>' +
    '<div class="card"><h3>导出记录</h3>' +
    '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
    '<th>类型</th><th>年度</th><th>文件名</th><th>大小</th><th>导出时间</th><th>操作人</th><th>操作</th>' +
    '</tr></thead><tbody id="rp-tbody"><tr><td colspan="7" class="empty">加载中…</td></tr></tbody></table></div></div>';

  function exportFile(kind) {
    // 浏览器直接打开下载链接（自动携带 Cookie）
    window.open('/api/export/' + kind + '?year=' + $('#rp-year').value);
    setTimeout(function () { loadLogs().catch(function () {}); }, 1500);
  }
  $('#rp-excel').addEventListener('click', function () { exportFile('excel'); });
  $('#rp-pdf').addEventListener('click', function () { exportFile('pdf'); });

  function sizeStr(bytes) {
    if (!bytes && bytes !== 0) return '—';
    if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1024).toFixed(1) + ' KB';
  }

  async function loadLogs() {
    var list = await api('/api/export/logs');
    $('#rp-tbody').innerHTML = list.length ? list.map(function (l) {
      return '<tr><td>' + esc(l.file_type) + '</td><td>' + esc(l.year) + '</td>' +
        '<td>' + esc(l.file_path || '—') + '</td><td>' + sizeStr(l.file_size) + '</td>' +
        '<td>' + esc(l.created_at || '—') + '</td><td>' + esc(l.created_by || '—') + '</td>' +
        '<td>' + (l.downloadable
          ? '<a href="/api/export/file/' + l.id + '">下载</a>'
          : '<span class="text-muted">已过期</span>') + '</td></tr>';
    }).join('') : '<tr><td colspan="7" class="empty">暂无导出记录</td></tr>';
  }

  await loadLogs();
};

/* ==================== 数据导入 ==================== */
PAGES.imports = async function (el) {
  var pendingTaskId = null;

  el.innerHTML =
    '<div class="card"><h3>下载导入模板</h3><div class="form-row" style="margin-bottom:0">' +
    '<button class="btn" data-tpl="energy">能耗台账模板</button>' +
    '<button class="btn" data-tpl="carbon">碳盘查模板</button>' +
    (isAdmin() ? '<button class="btn" data-tpl="factor">排放因子模板</button>' : '') +
    '</div></div>' +

    '<div class="card"><h3>上传导入</h3><div class="form-row">' +
    '<div class="form-item"><label>业务类型</label><select id="im-biz">' +
    '<option value="energy">能耗台账</option><option value="carbon">碳盘查</option>' +
    (isAdmin() ? '<option value="factor">排放因子</option>' : '') + '</select></div>' +
    '<div class="form-item"><label>冲突策略</label><select id="im-strategy">' +
    '<option value="skip">跳过重复</option><option value="overwrite">覆盖</option></select></div>' +
    '<div class="form-item"><label>Excel 文件（.xlsx，≤10MB）</label><input type="file" id="im-file" accept=".xlsx"></div>' +
    '<button class="btn btn-blue" id="im-upload">上传并预览</button>' +
    '</div><div id="im-preview"></div></div>' +

    '<div class="card"><h3>导入任务历史</h3>' +
    '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
    '<th>ID</th><th>业务</th><th>文件名</th><th>策略</th><th>总数</th><th>成功</th><th>失败</th><th>状态</th><th>时间</th><th>操作</th>' +
    '</tr></thead><tbody id="im-tbody"><tr><td colspan="10" class="empty">加载中…</td></tr></tbody></table></div></div>';

  // 模板下载
  $all('[data-tpl]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      window.open('/api/import/template?type=' + btn.getAttribute('data-tpl'));
    });
  });

  /* 上传预览（FormData，字段名 file） */
  $('#im-upload').addEventListener('click', async function () {
    var file = $('#im-file').files[0];
    if (!file) { toast('请选择文件', 'error'); return; }
    var fd = new FormData();
    fd.append('file', file);
    try {
      var d = await api('/api/import/upload?biz_type=' + $('#im-biz').value +
        '&strategy=' + $('#im-strategy').value, { method: 'POST', body: fd });
      pendingTaskId = d.task_id;
      renderPreview(d);
      await loadTasks();
    } catch (e) { toast(e.message, 'error'); }
  });

  /* 预览结果与确认入库 */
  function renderPreview(d) {
    var errRows = (d.error_detail || []).map(function (r) {
      return '<tr><td>' + esc(r.row_no) + '</td><td>' + esc(r.reason) + '</td></tr>';
    }).join('');
    $('#im-preview').innerHTML =
      '<div class="alert-warning">预览结果：新增 <b>' + d.new + '</b> 条，冲突 <b>' + d.conflict +
      '</b> 条，错误 <b>' + d.errors + '</b> 条。确认无误后点击「确认入库」。</div>' +
      (errRows ? '<div class="table-wrap" style="box-shadow:none;margin-bottom:10px"><table class="data-table">' +
        '<thead><tr><th>行号</th><th>错误原因</th></tr></thead><tbody>' + errRows + '</tbody></table></div>' : '') +
      (d.error_file ? '<div style="margin-bottom:10px"><a href="/api/import/tasks/' + d.task_id + '/errors">下载错误标注文件</a></div>' : '') +
      '<button class="btn btn-primary" id="im-confirm">确认入库</button>';

    $('#im-confirm').addEventListener('click', async function () {
      try {
        var r = await postJson('/api/import/confirm', { task_id: pendingTaskId });
        toast('入库完成：新增 ' + r.added + ' 条，覆盖 ' + r.overwritten + ' 条，跳过 ' + r.skipped + ' 条', 'success');
        $('#im-preview').innerHTML = '';
        pendingTaskId = null;
        await loadTasks();
      } catch (e) { toast(e.message, 'error'); }
    });
  }

  /* 任务历史 */
  var BIZ_NAMES = { energy: '能耗台账', carbon: '碳盘查', factor: '排放因子' };
  async function loadTasks() {
    var list = await api('/api/import/tasks');
    $('#im-tbody').innerHTML = list.length ? list.map(function (t) {
      return '<tr><td>' + t.id + '</td><td>' + esc(BIZ_NAMES[t.biz_type] || t.biz_type) + '</td>' +
        '<td>' + esc(t.file_name || '—') + '</td><td>' + esc(t.strategy === 'overwrite' ? '覆盖' : '跳过') + '</td>' +
        '<td>' + t.total + '</td><td>' + t.success + '</td><td>' + t.failed + '</td>' +
        '<td>' + esc(t.status === 'confirmed' ? '已入库' : '待确认') + '</td>' +
        '<td>' + esc(t.created_at || '—') + '</td>' +
        '<td>' + (t.error_file ? '<a href="/api/import/tasks/' + t.id + '/errors">错误文件</a>' : '<span class="text-muted">—</span>') + '</td></tr>';
    }).join('') : '<tr><td colspan="10" class="empty">暂无导入任务</td></tr>';
  }

  await loadTasks();
};

/* ==================== 系统管理（admin；日志页 admin+auditor） ==================== */
PAGES.sys = async function (el) {
  // auditor 仅见日志 Tab
  var tabs = isAdmin()
    ? [['users', '用户管理'], ['depts', '部门管理'], ['logs', '日志查询'], ['backups', '备份恢复'], ['ding', '钉钉配置']]
    : [['logs', '日志查询']];
  var cur = tabs[0][0];

  el.innerHTML = '<div class="card"><div class="tabs" id="sys-tabs">' +
    tabs.map(function (t) { return '<div class="tab" data-tab="' + t[0] + '">' + t[1] + '</div>'; }).join('') +
    '</div><div id="sys-body"></div></div>';

  $all('#sys-tabs .tab').forEach(function (t) {
    t.addEventListener('click', function () {
      cur = t.getAttribute('data-tab');
      $all('#sys-tabs .tab').forEach(function (x) { x.classList.toggle('active', x === t); });
      renderTab().catch(function (e) { toast(e.message, 'error'); });
    });
  });
  $all('#sys-tabs .tab')[0].classList.add('active');

  async function renderTab() {
    if (cur === 'users') return renderUsers();
    if (cur === 'depts') return renderDepts();
    if (cur === 'logs') return renderLogs();
    if (cur === 'backups') return renderBackups();
    if (cur === 'ding') return renderDing();
  }

  /* ---- 用户管理 ---- */
  async function renderUsers() {
    var body = $('#sys-body');
    var depts = await api('/api/sys/depts');
    body.innerHTML =
      '<div class="form-row">' +
      '<div class="form-item"><label>姓名（登录名）</label><input type="text" id="su-name"></div>' +
      '<div class="form-item"><label>手机号</label><input type="text" id="su-mobile"></div>' +
      '<div class="form-item"><label>角色</label><select id="su-role">' +
      Object.keys(ROLE_NAMES).map(function (r) { return '<option value="' + r + '">' + ROLE_NAMES[r] + '</option>'; }).join('') +
      '</select></div>' +
      '<div class="form-item"><label>部门</label><select id="su-dept"><option value="">（无）</option>' +
      depts.map(function (d) { return '<option value="' + d.id + '">' + esc(d.name) + '</option>'; }).join('') +
      '</select></div>' +
      '<div class="form-item"><label>钉钉 unionid</label><input type="text" id="su-union"></div>' +
      '</div><div class="form-row">' +
      '<div class="form-item"><label>状态</label><select id="su-status">' +
      '<option value="active">启用</option><option value="disabled">停用</option></select></div>' +
      '<div class="form-item"><label>密码（留空不修改）</label><input type="password" id="su-pwd" autocomplete="new-password"></div>' +
      '<button class="btn btn-primary" id="su-save">保存用户</button>' +
      '</div>' +
      '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
      '<th>ID</th><th>姓名</th><th>角色</th><th>部门</th><th>手机号</th><th>unionid</th><th>状态</th><th>操作</th>' +
      '</tr></thead><tbody id="su-tbody"></tbody></table></div>';

    async function loadUsers() {
      var list = await api('/api/sys/users');
      $('#su-tbody').innerHTML = list.length ? list.map(function (u) {
        return '<tr><td>' + u.id + '</td><td>' + esc(u.name) + '</td>' +
          '<td>' + esc(ROLE_NAMES[u.role_code] || u.role_code) + '</td><td>' + esc(u.dept_name || '—') + '</td>' +
          '<td>' + esc(u.mobile || '—') + '</td><td>' + esc(u.ding_unionid || '—') + '</td>' +
          '<td>' + (u.status === 'active' ? '<span class="badge approved">启用</span>' : '<span class="badge rejected">停用</span>') + '</td>' +
          '<td><button class="btn btn-sm" data-act="edit" data-id="' + u.id + '">编辑</button> ' +
          (u.ding_unionid
            ? '<button class="btn btn-sm" data-act="unbind" data-id="' + u.id + '">解绑钉钉</button> '
            : '<button class="btn btn-sm btn-primary" data-act="bind" data-id="' + u.id + '">扫码绑定</button> ') +
          '<button class="btn btn-sm btn-danger" data-act="revoke" data-id="' + u.id + '">强制下线</button></td></tr>';
      }).join('') : '<tr><td colspan="8" class="empty">暂无用户</td></tr>';

      $all('#su-tbody button').forEach(function (btn) {
        btn.addEventListener('click', async function () {
          var u = list.filter(function (x) { return String(x.id) === btn.getAttribute('data-id'); })[0];
          if (btn.getAttribute('data-act') === 'edit') {
            $('#su-name').value = u.name; $('#su-mobile').value = u.mobile || '';
            $('#su-role').value = u.role_code; $('#su-dept').value = u.dept_id || '';
            $('#su-union').value = u.ding_unionid || ''; $('#su-status').value = u.status; $('#su-pwd').value = '';
          } else if (btn.getAttribute('data-act') === 'bind') {
            showBindModal(u);
          } else if (btn.getAttribute('data-act') === 'unbind') {
            if (!confirm('确定解除用户「' + u.name + '」的钉钉绑定？')) return;
            try {
              await postJson('/api/sys/users/' + u.id + '/ding-unbind');
              toast('已解绑', 'success');
              await loadUsers();
            } catch (e) { toast(e.message, 'error'); }
          } else {
            if (!confirm('确定强制下线用户「' + u.name + '」？')) return;
            try {
              await postJson('/api/sys/users/' + u.id + '/revoke');
              toast('已强制下线', 'success');
            } catch (e) { toast(e.message, 'error'); }
          }
        });
      });
    }

    /* 扫码绑定弹窗：生成一次性绑定链接，展示二维码供成员用钉钉扫码 */
    async function showBindModal(u) {
      try {
        var d = await postJson('/api/sys/users/' + u.id + '/ding-bind');
        var url = d.url.charAt(0) === '/' ? location.origin + d.url : d.url;
        var m = openModal('钉钉扫码绑定 - ' + u.name,
          '<div style="text-align:center">' +
          '<div id="bind-qr" style="display:inline-block;padding:8px;background:#fff"></div>' +
          '<div class="sub" style="margin-top:8px">方式一：成员用<b>钉钉 App</b> 扫码确认（手机须连接公司内网 Wi-Fi）</div>' +
          '<div class="sub" style="margin-top:4px">方式二：成员在已登录钉钉客户端的 <b>PC</b> 上打开链接，点击授权一键完成</div>' +
          '<div style="margin-top:8px;word-break:break-all;font-size:12px;color:#888" id="bind-url"></div>' +
          '<div style="margin-top:8px">' +
          '<button class="btn btn-sm" id="bind-copy">复制链接</button> ' +
          '<button class="btn btn-sm btn-primary" id="bind-open">本机打开授权</button>' +
          '</div>' +
          '<div class="sub" style="margin-top:6px;color:#c00">链接 10 分钟内有效、一次性使用；绑定结果页可关闭后回到本页刷新</div>' +
          '</div>');
        $('#bind-url', m.el).textContent = url;
        if (window.QRCode) {
          new QRCode($('#bind-qr', m.el), { text: url, width: 180, height: 180 });
        }
        $('#bind-copy', m.el).addEventListener('click', function () {
          navigator.clipboard.writeText(url).then(function () { toast('链接已复制', 'success'); });
        });
        $('#bind-open', m.el).addEventListener('click', function () {
          window.open(url, '_blank');  // 钉钉页面识别 PC 客户端登录态，可点击授权
        });
      } catch (e) { toast(e.message, 'error'); }
    }

    $('#su-save').addEventListener('click', async function () {
      var bodyData = {
        name: $('#su-name').value.trim(), mobile: $('#su-mobile').value.trim() || null,
        role_code: $('#su-role').value,
        dept_id: $('#su-dept').value ? Number($('#su-dept').value) : null,
        ding_unionid: $('#su-union').value.trim() || null,
        status: $('#su-status').value
      };
      if ($('#su-pwd').value) bodyData.password = $('#su-pwd').value;
      try {
        await postJson('/api/sys/users', bodyData);
        toast('用户已保存', 'success');
        await loadUsers();
      } catch (e) { toast(e.message, 'error'); }
    });

    await loadUsers();
  }

  /* ---- 部门管理 ---- */
  async function renderDepts() {
    var body = $('#sys-body');
    body.innerHTML =
      '<div class="form-row">' +
      '<div class="form-item"><label>编码</label><input type="text" id="dp-code"></div>' +
      '<div class="form-item"><label>名称</label><input type="text" id="dp-name"></div>' +
      '<div class="form-item"><label>上级部门</label><select id="dp-parent"><option value="">（无）</option></select></div>' +
      '<label style="font-size:13px"><input type="checkbox" id="dp-enabled" checked> 启用</label>' +
      '<button class="btn btn-primary" id="dp-save">保存部门</button>' +
      '</div>' +
      '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
      '<th>ID</th><th>编码</th><th>名称</th><th>上级</th><th>启用</th><th>操作</th>' +
      '</tr></thead><tbody id="dp-tbody"></tbody></table></div>';

    async function loadDepts() {
      var list = await api('/api/sys/depts');
      var nameMap = {};
      list.forEach(function (d) { nameMap[d.id] = d.name; });
      $('#dp-parent').innerHTML = '<option value="">（无）</option>' + list.map(function (d) {
        return '<option value="' + d.id + '">' + esc(d.name) + '</option>';
      }).join('');
      $('#dp-tbody').innerHTML = list.length ? list.map(function (d) {
        return '<tr><td>' + d.id + '</td><td>' + esc(d.code) + '</td><td>' + esc(d.name) + '</td>' +
          '<td>' + esc(nameMap[d.parent_id] || '—') + '</td><td>' + (d.enabled ? '是' : '否') + '</td>' +
          '<td><button class="btn btn-sm" data-id="' + d.id + '">编辑</button></td></tr>';
      }).join('') : '<tr><td colspan="6" class="empty">暂无部门</td></tr>';
      $all('#dp-tbody button').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var d = list.filter(function (x) { return String(x.id) === btn.getAttribute('data-id'); })[0];
          $('#dp-code').value = d.code; $('#dp-name').value = d.name;
          $('#dp-parent').value = d.parent_id || ''; $('#dp-enabled').checked = !!d.enabled;
        });
      });
    }

    $('#dp-save').addEventListener('click', async function () {
      try {
        await postJson('/api/sys/depts', {
          code: $('#dp-code').value.trim(), name: $('#dp-name').value.trim(),
          parent_id: $('#dp-parent').value ? Number($('#dp-parent').value) : null,
          enabled: $('#dp-enabled').checked
        });
        toast('部门已保存', 'success');
        await loadDepts();
      } catch (e) { toast(e.message, 'error'); }
    });

    await loadDepts();
  }

  /* ---- 日志查询 ---- */
  async function renderLogs() {
    var body = $('#sys-body');
    var logType = 'op';
    body.innerHTML =
      '<div class="form-row">' +
      '<div class="form-item"><label>日志类型</label><select id="lg-type">' +
      '<option value="op">操作日志</option><option value="login">登录日志</option></select></div>' +
      '<div class="form-item"><label>关键字</label><input type="text" id="lg-kw" placeholder="操作/用户/IP"></div>' +
      '<button class="btn btn-blue" id="lg-query">查 询</button>' +
      '</div>' +
      '<div class="table-wrap" style="box-shadow:none"><table class="data-table" id="lg-table"></table></div>';

    async function loadLogs() {
      var q = '?type=' + logType;
      if ($('#lg-kw').value.trim()) q += '&keyword=' + encodeURIComponent($('#lg-kw').value.trim());
      var list = await api('/api/sys/logs' + q);
      var head, rows;
      if (logType === 'op') {
        head = '<thead><tr><th>时间</th><th>用户</th><th>操作</th><th>对象</th><th>详情</th><th>IP</th></tr></thead>';
        rows = list.map(function (l) {
          return '<tr><td>' + esc(l.created_at) + '</td><td>' + esc(l.user_name || l.user_id) + '</td>' +
            '<td>' + esc(l.action) + '</td><td>' + esc((l.object_type || '') + (l.object_id ? ' #' + l.object_id : '')) + '</td>' +
            '<td style="white-space:normal;max-width:320px">' + esc(l.detail || '—') + '</td><td>' + esc(l.ip || '—') + '</td></tr>';
        }).join('');
      } else {
        head = '<thead><tr><th>时间</th><th>用户</th><th>unionid</th><th>动作</th><th>结果</th><th>IP</th><th>UA</th></tr></thead>';
        rows = list.map(function (l) {
          return '<tr><td>' + esc(l.created_at) + '</td><td>' + esc(l.user_name || '—') + '</td>' +
            '<td>' + esc(l.unionid || '—') + '</td><td>' + esc(l.action) + '</td><td>' + esc(l.result) + '</td>' +
            '<td>' + esc(l.ip || '—') + '</td>' +
            '<td style="white-space:normal;max-width:220px">' + esc(l.ua || '—') + '</td></tr>';
        }).join('');
      }
      $('#lg-table').innerHTML = head + '<tbody>' +
        (rows || '<tr><td colspan="7" class="empty">暂无日志</td></tr>') + '</tbody>';
    }

    $('#lg-type').addEventListener('change', function () {
      logType = this.value;
      loadLogs().catch(function (e) { toast(e.message, 'error'); });
    });
    $('#lg-query').addEventListener('click', function () {
      loadLogs().catch(function (e) { toast(e.message, 'error'); });
    });
    await loadLogs();
  }

  /* ---- 备份与恢复 ---- */
  async function renderBackups() {
    var body = $('#sys-body');
    body.innerHTML =
      '<div class="form-row">' +
      '<button class="btn btn-primary" id="bk-now">立即备份</button>' +
      '<div class="form-item"><label>恢复（上传 .db 备份文件）</label><input type="file" id="bk-file" accept=".db"></div>' +
      '<button class="btn btn-danger" id="bk-restore">上传并恢复</button>' +
      '</div>' +
      '<div class="alert-warning">恢复操作会先自动备份当前数据库，再以确认方式覆盖，请谨慎操作。</div>' +
      '<div class="table-wrap" style="box-shadow:none"><table class="data-table"><thead><tr>' +
      '<th>文件名</th><th>大小</th><th>创建时间</th><th>操作</th>' +
      '</tr></thead><tbody id="bk-tbody"></tbody></table></div>';

    async function loadBackups() {
      var list = await api('/api/sys/backups');
      $('#bk-tbody').innerHTML = list.length ? list.map(function (b) {
        var size = b.size > 1048576 ? (b.size / 1048576).toFixed(1) + ' MB' : (b.size / 1024).toFixed(1) + ' KB';
        return '<tr><td>' + esc(b.name) + '</td><td>' + size + '</td><td>' + esc(b.created || '—') + '</td>' +
          '<td><a href="/api/sys/backups/' + encodeURIComponent(b.name) + '/download">下载</a></td></tr>';
      }).join('') : '<tr><td colspan="4" class="empty">暂无备份</td></tr>';
    }

    $('#bk-now').addEventListener('click', async function () {
      try {
        var r = await postJson('/api/sys/backup');
        toast('备份成功：' + (r.file || ''), 'success');
        await loadBackups();
      } catch (e) { toast(e.message, 'error'); }
    });

    /* 恢复：先 confirm=false 校验，返回 need_confirm 后用户确认再以 confirm=true 重发 */
    $('#bk-restore').addEventListener('click', async function () {
      var file = $('#bk-file').files[0];
      if (!file) { toast('请选择备份文件', 'error'); return; }
      async function upload(confirmFlag) {
        var fd = new FormData();
        fd.append('file', file);
        return api('/api/sys/restore?confirm=' + confirmFlag, { method: 'POST', body: fd });
      }
      try {
        var r = await upload(false);
        if (r.need_confirm) {
          if (!confirm((r.message || '校验通过') + '\n\n确定执行恢复？当前库会先自动备份。')) return;
          r = await upload(true);
        }
        toast(r.message || '恢复完成', 'success');
        await loadBackups();
      } catch (e) { toast(e.message, 'error'); }
    });

    await loadBackups();
  }

  /* ---- 钉钉配置 ---- */
  async function renderDing() {
    var body = $('#sys-body');
    var cfg = await api('/api/sys/ding-config');
    body.innerHTML =
      '<div class="form-row">' +
      '<div class="form-item"><label>Corp ID</label><input type="text" id="dg-corp" value="' + esc(cfg.corp_id || '') + '"></div>' +
      '<div class="form-item"><label>扫码 App ID</label><input type="text" id="dg-qrid" value="' + esc(cfg.qr_app_id || '') + '"></div>' +
      '<div class="form-item"><label>扫码 App Secret</label><input type="password" id="dg-qrsecret" placeholder="' +
        (cfg.qr_app_secret_enc ? '******' : '未配置') + '"></div>' +
      '</div><div class="form-row">' +
      '<div class="form-item"><label>应用 App Key</label><input type="text" id="dg-key" value="' + esc(cfg.app_key || '') + '"></div>' +
      '<div class="form-item"><label>应用 App Secret</label><input type="password" id="dg-secret" placeholder="' +
        (cfg.app_secret_enc ? '******' : '未配置') + '"></div>' +
      '<div class="form-item"><label>管理员联系方式</label><input type="text" id="dg-contact" value="' + esc(cfg.admin_contact || '') + '"></div>' +
      '</div><div>' +
      '<button class="btn btn-primary" id="dg-save">保存配置</button>' +
      '<button class="btn btn-blue" id="dg-test">连通性测试</button>' +
      '<span class="text-muted" style="margin-left:10px;font-size:12px">密钥留空表示不更新；当前为' +
      (cfg.mock_mode ? ' Mock 模拟环境' : '生产环境') + '</span>' +
      '</div>';

    $('#dg-save').addEventListener('click', async function () {
      var bodyData = {
        corp_id: $('#dg-corp').value.trim(), qr_app_id: $('#dg-qrid').value.trim(),
        app_key: $('#dg-key').value.trim(), admin_contact: $('#dg-contact').value.trim()
      };
      // 密钥字段留空则不提交（不更新）
      if ($('#dg-qrsecret').value) bodyData.qr_app_secret = $('#dg-qrsecret').value;
      if ($('#dg-secret').value) bodyData.app_secret = $('#dg-secret').value;
      try {
        await postJson('/api/sys/ding-config', bodyData);
        toast('配置已保存', 'success');
        await renderDing();
      } catch (e) { toast(e.message, 'error'); }
    });

    $('#dg-test').addEventListener('click', async function () {
      try {
        var r = await postJson('/api/sys/ding-config/test');
        toast(r.message || '连通性正常', 'success');
      } catch (e) { toast(e.message, 'error'); }
    });
  }

  await renderTab();
};

/* ==================== 启动 ==================== */
async function boot() {
  var me;
  try {
    me = await api('/api/auth/me');
  } catch (e) {
    // api() 已处理 401 跳转；其余错误给出提示
    if (e.status !== 401) {
      $('#boot-loading').textContent = '加载失败：' + e.message;
    }
    return;
  }
  S.user = me;

  // 顶部用户栏
  $('#user-name').textContent = me.name;
  $('#user-role').textContent = ROLE_NAMES[me.role_code] || me.role_code;
  $('#user-dept').textContent = me.dept_name || '';
  if (me.mock_mode) $('#user-mock').style.display = '';
  $('#btn-logout').addEventListener('click', async function () {
    try { await postJson('/api/auth/logout'); } catch (e) { /* 忽略 */ }
    location.href = '/login.html';
  });

  // hash 路由
  window.addEventListener('hashchange', function () {
    var p = location.hash.replace('#', '') || 'dashboard';
    if (p !== S.page) go(p);
  });

  $('#boot-loading').style.display = 'none';
  $('#layout').style.display = '';
  renderMenu();
  await go(location.hash.replace('#', '') || 'dashboard');
}

boot();
})();
