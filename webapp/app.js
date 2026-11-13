'use strict';

/* КБЖУ — мини-приложение Telegram. Без зависимостей: только DOM и fetch. */

/* ------------------------------------------------------------------ TG */

const tg = (window.Telegram && window.Telegram.WebApp) || null;
let initData = '';

/* -------------------------------------------------------------- утилиты */

const $ = (id) => document.getElementById(id);

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
));

function num(value, digits = 1) {
  const raw = Number(String(value == null ? '' : value).replace(',', '.'));
  if (!Number.isFinite(raw)) return '0';
  const factor = 10 ** digits;
  return String(Math.round(raw * factor) / factor).replace('.', ',');
}

function kcal(value) {
  const raw = Number(String(value == null ? '' : value).replace(',', '.'));
  return String(Number.isFinite(raw) ? Math.round(raw) : 0);
}

function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function haptic(kind = 'light') {
  try {
    if (!tg || !tg.HapticFeedback) return;
    if (kind === 'ok') tg.HapticFeedback.notificationOccurred('success');
    else if (kind === 'err') tg.HapticFeedback.notificationOccurred('error');
    else tg.HapticFeedback.impactOccurred(kind);
  } catch (err) { /* старые клиенты — не беда */ }
}

let toastTimer = 0;
function toast(message, isError = false) {
  const box = $('toast');
  box.textContent = message;
  box.className = 'toast' + (isError ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add('hidden'), isError ? 4200 : 2200);
}

/* -------------------------------------------------------------- запросы */

async function api(path, options = {}) {
  const { method = 'GET', body, query } = options;
  let url = path;
  if (query) {
    const qs = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') qs.set(key, value);
    });
    const tail = qs.toString();
    if (tail) url += (url.includes('?') ? '&' : '?') + tail;
  }
  const headers = {};
  if (initData) headers['X-Init-Data'] = initData;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    throw new Error('нет связи с сервером');
  }
  let data = null;
  try { data = await response.json(); } catch (err) { data = null; }
  if (!response.ok || !data || data.ok === false) {
    throw new Error((data && data.error) || `ошибка ${response.status}`);
  }
  return data;
}

/* --------------------------------------------------------------- состояние */

const state = {
  screen: 'today',
  today: '',
  date: '',
  day: null,
  session: null,
  demo: false,
  dishes: { query: '', items: [], loading: false },
  stats: { period: 7, data: null, loading: false, failed: false },
  ai: { status: null, items: [], text: '', coach: '', busy: false, photoBusy: false },
  pick: null,
  quickItems: [],
};

const TABS = [
  ['today', 'Сегодня'],
  ['dishes', 'Блюда'],
  ['stats', 'Статистика'],
  ['ai', 'ИИ'],
];

function screenFromHash() {
  const raw = String(window.location.hash || '').replace(/^#/, '');
  return TABS.some(([key]) => key === raw) ? raw : 'today';
}

const ICONS = {
  today: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 1.8"/>',
  dishes: '<path d="M4 7h16M4 12h16M4 17h10"/>',
  stats: '<path d="M5 19V10M12 19V5M19 19v-6"/>',
  ai: '<path d="M12 3.5l1.8 4.4 4.4 1.8-4.4 1.8L12 15.9l-1.8-4.4L5.8 9.7l4.4-1.8L12 3.5z"/><path d="M18 16.4l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
};

function icon(name, cls = '') {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}

/* --------------------------------------------------------------- разметка */

function renderTabs() {
  $('tabbar').innerHTML = TABS.map(([key, label]) => `
    <button class="tab ${state.screen === key ? 'on' : ''}" data-act="tab" data-tab="${key}"
            aria-label="${esc(label)}" ${state.screen === key ? 'aria-current="page"' : ''}>
      ${icon(key)}
      <span>${esc(label)}</span>
    </button>`).join('');
}

function render() {
  renderTabs();
  const view = $('view');
  switch (state.screen) {
    case 'dishes': view.innerHTML = screenDishes(); break;
    case 'stats': view.innerHTML = screenStats(); break;
    case 'ai': view.innerHTML = screenAI(); break;
    default: view.innerHTML = screenToday();
  }
  window.scrollTo(0, 0);
}

/* ---------------------------------------------------------------- сегодня */

function mealRow(meal) {
  const grams = meal.grams ? ` · ${num(meal.grams, 0)} г` : '';
  const origin = meal.dish_id ? '' : ' · разовая';
  return `
    <li class="meal">
      <div class="meal-main">
        <span class="meal-name">${esc(meal.name)}</span>
        <span class="meal-sub">${esc(meal.time)}${grams}${origin}</span>
      </div>
      <span class="meal-kcal">${kcal(meal.kcal)}</span>
      <button class="icon-btn" data-act="mealDel" data-id="${meal.id}" aria-label="Удалить ${esc(meal.name)}">✕</button>
    </li>`;
}

function screenToday() {
  const day = state.day;
  if (!day) return '<div class="empty">Загружаю день…</div>';

  const totals = day.totals || {};
  const goal = day.goal;
  const goalKcal = goal ? Number(goal.kcal) || 0 : 0;
  const eaten = Number(totals.kcal) || 0;
  const percent = goalKcal ? Math.min(100, (eaten / goalKcal) * 100) : 0;
  const over = goalKcal > 0 && eaten > goalKcal;
  const note = goalKcal
    ? (over ? `перебор ${kcal(eaten - goalKcal)} ккал` : `осталось ${kcal(goalKcal - eaten)} ккал`)
    : 'цель по калориям не задана';

  const macros = [
    ['Белки', totals.protein, goal && goal.protein],
    ['Жиры', totals.fat, goal && goal.fat],
    ['Углеводы', totals.carb, goal && goal.carb],
  ].map(([name, value, target]) => `
      <div class="macro">
        <div class="macro-name">${name}</div>
        <div class="macro-val">${num(value)}${Number(target) > 0 ? `<small> / ${num(target)}</small>` : ''}</div>
      </div>`).join('');

  const meals = day.meals.length
    ? `<ul class="list">${day.meals.map(mealRow).join('')}</ul>`
    : '<div class="empty">Пока пусто — добавь первую запись</div>';

  const demoBanner = state.demo
    ? '<div class="card tight"><div class="ai-status"><span class="dot off"></span><span>Демо-режим: открыто без Telegram, запись включена</span></div></div>'
    : '';

  return `
    <div class="screen-head">
      <div class="date-nav">
        <button class="icon-btn" data-act="dayStep" data-step="-1" aria-label="Прошлый день">‹</button>
        <div class="grow">
          <div class="h1">${esc(day.label)}</div>
          <div class="hint">${esc(day.short)}${day.is_today ? '' : ' · <a href="#" class="link" data-act="dayToday">к сегодня</a>'}</div>
        </div>
        <button class="icon-btn" data-act="dayStep" data-step="1" aria-label="Следующий день" ${day.is_today ? 'disabled' : ''}>›</button>
      </div>
    </div>

    ${demoBanner}

    <section class="card kcal-card">
      <div class="spread">
        <div>
          <div class="big-num">${kcal(eaten)} <small>/ ${goalKcal ? kcal(goalKcal) : '—'} ккал</small></div>
          <div class="hint">${esc(note)}</div>
        </div>
        <button class="icon-btn" data-act="goalEdit" aria-label="Изменить цель">⚙</button>
      </div>
      <div class="bar"><div class="bar-fill ${over ? 'over' : ''}" style="width:${percent}%"></div></div>
      <div class="spread hint">
        <span>${day.meals.length} ${plural(day.meals.length, 'запись', 'записи', 'записей')}</span>
        <span>${goalKcal ? `${Math.round((eaten / goalKcal) * 100)}% цели` : ''}</span>
      </div>
    </section>

    <section class="grid3" style="margin-bottom:12px">${macros}</section>

    <section class="card tight">
      <button class="workout ${day.workout ? 'on' : ''}" data-act="workout">
        <span class="emoji">${day.workout ? '🏋️' : '🛋️'}</span>
        <span class="grow" style="text-align:left">
          <span style="display:block;font-weight:600">${day.workout ? 'День тренировки' : 'Без тренировки'}</span>
          <span class="hint">${day.note ? esc(day.note) : (day.workout ? 'нажми, чтобы снять отметку' : 'нажми, чтобы отметить')}</span>
        </span>
        <span class="hint">${day.workout ? '✓' : '+'}</span>
      </button>
      <button class="btn ghost small wide" data-act="note" style="margin-top:8px">${day.note ? 'Изменить заметку' : 'Добавить заметку'}</button>
    </section>

    <section class="card">
      <div class="spread" style="margin-bottom:6px">
        <span class="h2" style="margin:0">Съедено</span>
        <span class="hint">Б ${num(totals.protein)} · Ж ${num(totals.fat)} · У ${num(totals.carb)}</span>
      </div>
      ${meals}
      <button class="btn primary wide" data-act="add" style="margin-top:14px">${icon('plus')} Добавить еду</button>
    </section>

    <div class="footer-note">Одна база с ботом — можно вести дневник и там, и здесь</div>`;
}

/* ----------------------------------------------------------------- блюда */

function dishRow(dish) {
  const uses = Number(dish.use_count) || 0;
  return `
    <li class="dish">
      <div class="dish-main">
        <span class="dish-name">${esc(dish.name)}</span>
        <span class="dish-sub">Б ${num(dish.protein)} · Ж ${num(dish.fat)} · У ${num(dish.carb)}${uses ? ` · ${uses} ${plural(uses, 'раз', 'раза', 'раз')}` : ''}</span>
      </div>
      <button class="btn small" data-act="dishPick" data-id="${dish.id}">${kcal(dish.kcal)}<span class="hint"> ккал</span></button>
      <button class="icon-btn" data-act="dishDel" data-id="${dish.id}" data-name="${esc(dish.name)}" aria-label="Удалить блюдо">✕</button>
    </li>`;
}

function dishListHtml() {
  const list = state.dishes;
  if (list.loading) return '<div class="empty">Ищу…</div>';
  if (!list.items.length) {
    return `<div class="empty">${list.query ? 'Ничего не нашлось — добавь блюдо вручную' : 'База пуста — добавь первое блюдо'}</div>`;
  }
  return `<ul class="list">${list.items.map(dishRow).join('')}</ul>`;
}

function screenDishes() {
  const list = state.dishes;
  return `
    <div class="screen-head">
      <div>
        <div class="h1">Блюда</div>
        <div class="hint" id="dishCount">${list.items.length ? `${list.items.length} в списке` : 'база на 100 г'}</div>
      </div>
      <button class="btn small primary" data-act="dishNew">${icon('plus')} Новое</button>
    </div>

    <div class="search-wrap">
      ${icon('search')}
      <input class="input" id="dishSearch" type="search" placeholder="Поиск по названию"
             value="${esc(list.query)}" autocomplete="off" enterkeyhint="search">
    </div>

    <section class="card" id="dishList">${dishListHtml()}</section>
    <div class="footer-note">Цифры на 100 г. Нажми на калории, чтобы записать блюдо в день</div>`;
}

/* ------------------------------------------------------------ статистика */

function screenStats() {
  const stats = state.stats;
  if (!stats.data) {
    if (!stats.loading && !stats.failed) {
      stats.loading = true;
      setTimeout(loadStats, 0);
    }
    return '<div class="empty">Считаю статистику…</div>';
  }
  const data = stats.data;
  const goalKcal = data.goal ? Number(data.goal.kcal) || 0 : 0;
  const peak = Math.max(1, ...data.by_day.map((entry) => entry.kcal));
  const scale = goalKcal ? Math.max(goalKcal * 1.25, peak) : peak;
  const plot = 130; // высота области столбиков в px (см. app.css)

  const bars = data.by_day.map((entry) => {
    const height = entry.kcal > 0 ? Math.max(3, Math.min(100, (entry.kcal / scale) * 100)) : 0;
    const fill = entry.kcal > goalKcal && goalKcal ? 'over' : '';
    return `
      <div class="bar-col" title="${esc(entry.short)}: ${kcal(entry.kcal)} ккал${entry.workout ? ' · тренировка' : ''}">
        <div class="bar-track">
          <div class="bar-fill ${fill}" style="height:${height}%"></div>
        </div>
      </div>`;
  }).join('');

  const step = data.by_day.length > 14 ? Math.ceil(data.by_day.length / 7) : 1;
  const labels = data.by_day
    .map((entry, index) => `<span>${index % step === 0 || index === data.by_day.length - 1 ? esc(entry.weekday) : ''}</span>`)
    .join('');

  const goalLine = goalKcal
    ? `<div class="chart-goal" style="bottom:${20 + Math.min(1, goalKcal / scale) * plot}px"><span>${kcal(goalKcal)}</span></div>`
    : '';

  const weeks = data.weeks.length
    ? `<ul class="list">${data.weeks.map((week) => `
        <li class="week-row">
          <span class="week-label">${esc(week.label)}<br><span class="hint">${week.days} ${plural(week.days, 'день', 'дня', 'дней')} · ${week.workouts} ${plural(week.workouts, 'тренировка', 'тренировки', 'тренировок')}</span></span>
          <span class="week-val">${kcal(week.avg_kcal)}<small> ккал/день</small></span>
        </li>`).join('')}</ul>`
    : '<div class="empty">Пока нечего сравнивать</div>';

  const periods = [[7, '7 дней'], [30, '30 дней'], [90, '90 дней'], [0, 'всё время']]
    .map(([value, label]) => `<button class="chip ${stats.period === value ? 'on' : ''}" data-act="period" data-period="${value}">${label}</button>`)
    .join('');

  return `
    <div class="screen-head">
      <div>
        <div class="h1">Статистика</div>
        <div class="hint">${esc(data.start)} — ${esc(data.end)}</div>
      </div>
    </div>

    <div class="chips" style="margin-bottom:12px">${periods}</div>

    <section class="summary-grid">
      <div class="summary">
        <div class="macro-name">Среднее в день</div>
        <div class="val">${kcal(data.avg.kcal)}<small> ккал</small></div>
      </div>
      <div class="summary">
        <div class="macro-name">Дней с записями</div>
        <div class="val">${data.days_filled}<small> / ${data.days}</small></div>
      </div>
      <div class="summary">
        <div class="macro-name">Тренировок</div>
        <div class="val">${data.workouts}<small> ${plural(data.workouts, 'раз', 'раза', 'раз')}</small></div>
      </div>
      <div class="summary">
        <div class="macro-name">Всего за период</div>
        <div class="val">${kcal(data.totals.kcal)}<small> ккал</small></div>
      </div>
    </section>

    <section class="card">
      <div class="spread" style="margin-bottom:10px">
        <span class="h2" style="margin:0">По дням</span>
        <span class="hint">${goalKcal ? `пунктир — цель ${kcal(goalKcal)}` : 'без цели'}</span>
      </div>
      <div class="chart">
        <div class="chart-bars">${bars}${goalLine}</div>
        <div class="chart-labels">${labels}</div>
      </div>
    </section>

    <section class="card">
      <div class="h2">Недельные срезы</div>
      ${weeks}
    </section>

    <section class="card">
      <div class="spread" style="margin-bottom:10px">
        <span class="h2" style="margin:0">Цель</span>
        <button class="btn ghost small" data-act="goalEdit">Изменить</button>
      </div>
      ${data.goal
        ? `<div class="hint">На день: ${kcal(data.goal.kcal)} ккал · Б ${num(data.goal.protein)} · Ж ${num(data.goal.fat)} · У ${num(data.goal.carb)}</div>`
        : '<div class="hint">Цель не задана — средние считаются без ориентира</div>'}
    </section>

    <section class="card tight">
      <button class="btn ghost wide" data-act="download">Скачать CSV</button>
      <div class="hint" style="margin-top:8px">Если файл не открылся, в боте есть команда /export</div>
    </section>`;
}

/* -------------------------------------------------------------------- ИИ */

function aiItemsHtml() {
  const items = state.ai.items;
  if (!items.length) return '';
  return `
    <ul class="list" style="margin:4px 0 2px">
      ${items.map((item, index) => `
        <li class="ai-item ${item.on === false ? 'off' : ''}">
          <input type="checkbox" data-act="aiToggle" data-idx="${index}" ${item.on === false ? '' : 'checked'}
                 aria-label="Записать ${esc(item.name)}">
          <div class="meal-main">
            <span class="meal-name">${esc(item.name)}</span>
            <span class="meal-sub">${item.dish_id ? 'из базы' : 'оценка ИИ'}</span>
          </div>
          <input class="input grams" type="number" inputmode="decimal" min="1" max="5000"
                 data-act="aiGrams" data-idx="${index}" value="${num(item.grams, 0)}" aria-label="Граммы">
          <span class="meal-kcal js-kcal">${kcal(item.kcal)}</span>
        </li>`).join('')}
    </ul>`;
}

function screenAI() {
  const ai = state.ai;
  const status = ai.status || { enabled: false, model: '' };
  const enabled = Boolean(status.enabled);

  const statusCard = `
    <section class="card tight">
      <div class="ai-status">
        <span class="dot ${enabled ? 'on' : 'off'}"></span>
        <span class="grow">${enabled
          ? `ИИ включён: <b>${esc(status.model || 'модель')}</b>`
          : 'ИИ выключен — работает ручной ввод'}</span>
      </div>
      ${enabled ? '' : '<div class="hint" style="margin-top:8px">Включить можно в config.json разделом ai, в том числе бесплатным Ollama на этом же сервере. Дневник и бот работают и без модели.</div>'}
    </section>`;

  const parseCard = `
    <section class="card">
      <div class="h2">Описать словами</div>
      <textarea class="input" id="aiText" placeholder="съел 200 г гречки и куриную грудку" ${enabled ? '' : 'disabled'}>${esc(ai.text)}</textarea>
      <div class="sheet-actions">
        <button class="btn primary" data-act="aiParse" ${enabled && !ai.busy ? '' : 'disabled'}>
          ${ai.busy ? '<span class="spinner"></span>' : 'Разобрать'}
        </button>
        <label class="btn ghost" style="margin:0">
          ${ai.photoBusy ? '<span class="spinner"></span>' : 'Фото'}
          <input type="file" id="aiPhoto" accept="image/*" class="hidden" ${enabled ? '' : 'disabled'}>
        </label>
      </div>
      <div class="hint" style="margin-top:8px">Фото тарелки тоже можно — модель оценит порции.</div>
    </section>`;

  const reviewCard = ai.items.length ? `
    <section class="card">
      <div class="spread" style="margin-bottom:4px">
        <span class="h2" style="margin:0">Проверь и запиши</span>
        <button class="icon-btn" data-act="aiClear" aria-label="Очистить">✕</button>
      </div>
      <div class="hint">Граммы можно поправить — калории пересчитаются.</div>
      ${aiItemsHtml()}
      <button class="btn primary wide" data-act="aiApply" style="margin-top:12px">Записать в дневник</button>
    </section>` : '';

  const coachCard = `
    <section class="card">
      <div class="h2">Разбор недели</div>
      <div class="hint">Тренер посмотрит средние, цель и тренировки за 7 дней.</div>
      <button class="btn wide" data-act="aiCoach" style="margin-top:10px" ${enabled && !ai.busy ? '' : 'disabled'}>
        ${ai.busy ? '<span class="spinner"></span>' : 'Что улучшить?'}
      </button>
      ${ai.coach ? `<div class="coach" style="margin-top:12px">${esc(ai.coach)}</div>` : ''}
    </section>`;

  return `
    <div class="screen-head">
      <div>
        <div class="h1">Помощник</div>
        <div class="hint">разбор еды и итоги недели</div>
      </div>
    </div>
    ${statusCard}
    ${parseCard}
    ${reviewCard}
    ${coachCard}`;
}

/* ---------------------------------------------------------------- шторка */

let pendingConfirm = null;

function settleConfirm(value) {
  const resolve = pendingConfirm;
  pendingConfirm = null;
  if (resolve) resolve(value);
}

function openSheet(html, focusSelector) {
  const sheet = $('sheet');
  $('sheetPanel').innerHTML = html;
  sheet.classList.remove('hidden');
  sheet.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  try { if (tg && tg.BackButton) tg.BackButton.show(); } catch (err) { /* noop */ }
  if (focusSelector) {
    setTimeout(() => { const node = document.querySelector(focusSelector); if (node) node.focus(); }, 140);
  }
}

function closeSheet() {
  const sheet = $('sheet');
  settleConfirm(false);
  if (sheet.classList.contains('hidden')) return;
  sheet.classList.add('hidden');
  sheet.setAttribute('aria-hidden', 'true');
  $('sheetPanel').innerHTML = '';
  document.body.style.overflow = '';
  try { if (tg && tg.BackButton) tg.BackButton.hide(); } catch (err) { /* noop */ }
}

function sheetHead(title, subtitle = '') {
  return `
    <div class="sheet-head">
      <div>
        <h2>${esc(title)}</h2>
        ${subtitle ? `<div class="hint">${esc(subtitle)}</div>` : ''}
      </div>
      <button class="icon-btn" data-act="close" aria-label="Закрыть">✕</button>
    </div>`;
}

function askConfirm(options) {
  return new Promise((resolve) => {
    pendingConfirm = resolve;
    openSheet(`
      ${sheetHead(options.title, options.text || '')}
      <div class="sheet-actions">
        <button class="btn ghost" data-act="confirmNo">Отмена</button>
        <button class="btn ${options.danger ? 'danger' : 'primary'}" data-act="confirmYes">${esc(options.ok || 'Удалить')}</button>
      </div>`);
  });
}

/* ------------------------------------------------------- добавление записи */

function openGrams(dish) {
  state.pick = { dish, grams: 100 };
  const chips = [50, 100, 150, 200, 250].map((grams) => `
    <button class="chip ${grams === 100 ? 'on' : ''}" data-act="pickGram" data-grams="${grams}">${grams} г</button>`).join('');
  openSheet(`
    ${sheetHead(dish.name, `${kcal(dish.kcal)} ккал · Б ${num(dish.protein)} · Ж ${num(dish.fat)} · У ${num(dish.carb)} на 100 г`)}
    <div class="chips" style="margin-bottom:12px">${chips}</div>
    <input class="input big" id="pickGrams" type="number" inputmode="decimal" min="1" max="5000" value="100" aria-label="Граммы">
    <div class="spread" style="margin:12px 2px 0">
      <span class="hint">Порция</span>
      <span class="num js-pick-kcal" style="font-size:20px;font-weight:650">${kcal(dish.kcal)} ккал</span>
    </div>
    <div class="sheet-actions">
      <button class="btn ghost" data-act="close">Отмена</button>
      <button class="btn primary" data-act="pickSave">Записать</button>
    </div>`, '#pickGrams');
}

function updatePickPreview() {
  const pick = state.pick;
  const field = $('pickGrams');
  if (!pick || !field) return;
  const grams = Number(String(field.value).replace(',', '.')) || 0;
  pick.grams = grams;
  const value = document.querySelector('.js-pick-kcal');
  if (value) value.textContent = `${kcal((pick.dish.kcal * grams) / 100)} ккал`;
}

function openManual() {
  openSheet(`
    ${sheetHead('Разовая запись', 'если блюда нет в базе')}
    <label class="field"><span class="label">Название</span>
      <input class="input" id="mName" type="text" placeholder="например, шаурма" autocomplete="off"></label>
    <label class="field"><span class="label">Калории, ккал</span>
      <input class="input" id="mKcal" type="number" inputmode="decimal" placeholder="450"></label>
    <div class="grid3">
      <label class="field"><span class="label">Белки</span><input class="input" id="mProtein" type="number" inputmode="decimal" placeholder="0"></label>
      <label class="field"><span class="label">Жиры</span><input class="input" id="mFat" type="number" inputmode="decimal" placeholder="0"></label>
      <label class="field"><span class="label">Углеводы</span><input class="input" id="mCarb" type="number" inputmode="decimal" placeholder="0"></label>
    </div>
    <div class="sheet-actions">
      <button class="btn ghost" data-act="close">Отмена</button>
      <button class="btn primary" data-act="manualSave">Записать</button>
    </div>`, '#mName');
}

function openDishNew() {
  openSheet(`
    ${sheetHead('Новое блюдо', 'КБЖУ на 100 г — так его потом удобно считать')}
    <label class="field"><span class="label">Название</span>
      <input class="input" id="dName" type="text" placeholder="например, овсянка на молоке" autocomplete="off"></label>
    <label class="field"><span class="label">Калории на 100 г</span>
      <input class="input" id="dKcal" type="number" inputmode="decimal" placeholder="110"></label>
    <div class="grid3">
      <label class="field"><span class="label">Белки</span><input class="input" id="dProtein" type="number" inputmode="decimal" placeholder="0"></label>
      <label class="field"><span class="label">Жиры</span><input class="input" id="dFat" type="number" inputmode="decimal" placeholder="0"></label>
      <label class="field"><span class="label">Углеводы</span><input class="input" id="dCarb" type="number" inputmode="decimal" placeholder="0"></label>
    </div>
    <div class="hint">Если такое название уже есть, цифры обновятся.</div>
    <div class="sheet-actions">
      <button class="btn ghost" data-act="close">Отмена</button>
      <button class="btn primary" data-act="dishSave">Сохранить</button>
    </div>`, '#dName');
}

function openGoal() {
  const goal = (state.day && state.day.goal) || {};
  openSheet(`
    ${sheetHead('Цель на день', 'КБЖУ, к которым идём')}
    <label class="field"><span class="label">Калории</span>
      <input class="input" id="gKcal" type="number" inputmode="decimal" value="${goal.kcal ? kcal(goal.kcal) : ''}" placeholder="2000"></label>
    <div class="grid3">
      <label class="field"><span class="label">Белки</span><input class="input" id="gProtein" type="number" inputmode="decimal" value="${goal.protein ? num(goal.protein) : ''}" placeholder="120"></label>
      <label class="field"><span class="label">Жиры</span><input class="input" id="gFat" type="number" inputmode="decimal" value="${goal.fat ? num(goal.fat) : ''}" placeholder="70"></label>
      <label class="field"><span class="label">Углеводы</span><input class="input" id="gCarb" type="number" inputmode="decimal" value="${goal.carb ? num(goal.carb) : ''}" placeholder="220"></label>
    </div>
    <div class="sheet-actions">
      <button class="btn ghost" data-act="close">Отмена</button>
      <button class="btn primary" data-act="goalSave">Сохранить</button>
    </div>`, '#gKcal');
}

function openNote() {
  openSheet(`
    ${sheetHead('Заметка к дню', state.day ? state.day.label : '')}
    <textarea class="input" id="nText" placeholder="например, тренировка ноги + 40 минут ходьбы">${esc(state.day ? state.day.note : '')}</textarea>
    <div class="sheet-actions">
      <button class="btn ghost" data-act="close">Отмена</button>
      <button class="btn primary" data-act="noteSave">Сохранить</button>
    </div>`, '#nText');
}

/* -------------------------------------------------------------- загрузки */

async function loadDay(date) {
  try {
    const data = await api('/api/day', { query: { date } });
    state.day = data.day;
    state.date = data.day.date;
    render();
  } catch (err) {
    toast(err.message, true);
  }
}

function paintDishes() {
  const box = $('dishList');
  if (box) {
    box.innerHTML = dishListHtml();
    const count = $('dishCount');
    if (count) count.textContent = state.dishes.items.length ? `${state.dishes.items.length} в списке` : 'база на 100 г';
    return;
  }
  if (state.screen === 'dishes') render();
}

async function loadDishes() {
  state.dishes.loading = true;
  paintDishes();
  try {
    const data = await api('/api/dishes', { query: { q: state.dishes.query } });
    state.dishes.items = data.items;
  } catch (err) {
    state.dishes.items = [];
    toast(err.message, true);
  } finally {
    state.dishes.loading = false;
    paintDishes();
  }
}

async function loadStats() {
  state.stats.loading = true;
  try {
    const data = await api('/api/stats', { query: { period: state.stats.period } });
    state.stats.data = data.stats;
    state.stats.failed = false;
  } catch (err) {
    state.stats.failed = true;
    toast(err.message, true);
  } finally {
    state.stats.loading = false;
    if (state.screen === 'stats') render();
  }
}

/* -------------------------------------------------------------- действия */

async function saveMeal(payload) {
  try {
    const data = await api('/api/meals', { method: 'POST', body: { date: state.date, ...payload } });
    state.day = data.day;
    closeSheet();
    render();
    haptic('ok');
    toast('Записано');
  } catch (err) {
    toast(err.message, true);
    haptic('err');
  }
}

const actions = {
  close: () => closeSheet(),

  confirmYes: () => { settleConfirm(true); closeSheet(); },
  confirmNo: () => { settleConfirm(false); closeSheet(); },

  tab: (node) => {
    const tab = node.dataset.tab;
    if (state.screen === tab) return;
    state.screen = tab;
    haptic('light');
    try { history.replaceState(null, '', `#${tab}`); } catch (err) { /* noop */ }
    render();
    if (tab === 'dishes' && !state.dishes.items.length && !state.dishes.loading) loadDishes();
  },

  dayStep: (node) => {
    const step = Number(node.dataset.step) || 0;
    if (!step) return;
    const current = new Date(`${state.date}T12:00:00`);
    current.setDate(current.getDate() + step);
    const iso = current.toISOString().slice(0, 10);
    if (iso > state.today) return;
    loadDay(iso);
  },

  dayToday: () => loadDay(state.today),

  add: () => {
    openSheet(`
      ${sheetHead('Добавить еду', state.day ? state.day.label : '')}
      <div class="search-wrap" style="margin-bottom:10px">
        ${icon('search')}
        <input class="input" id="quickSearch" type="search" placeholder="Найти блюдо в базе" autocomplete="off">
      </div>
      <div id="quickList"><div class="empty">Начни вводить название</div></div>
      <div class="sheet-actions">
        <button class="btn ghost" data-act="manual">Вписать вручную</button>
      </div>`, '#quickSearch');
    quickSearch('');
  },

  manual: () => openManual(),

  pickGram: (node) => {
    const grams = node.dataset.grams;
    const field = $('pickGrams');
    if (field) field.value = grams;
    document.querySelectorAll('.chip[data-act="pickGram"]').forEach((chip) => {
      chip.classList.toggle('on', chip.dataset.grams === grams);
    });
    updatePickPreview();
  },

  pickSave: () => {
    const pick = state.pick;
    if (!pick) return;
    const field = $('pickGrams');
    const grams = Number(String(field ? field.value : '0').replace(',', '.'));
    if (!grams || grams <= 0) { toast('Нужно число граммов', true); return; }
    saveMeal({ dish_id: pick.dish.id, grams });
  },

  manualSave: () => {
    const name = (($('mName') || {}).value || '').trim();
    const kcalValue = Number(String((($('mKcal') || {}).value) || '').replace(',', '.'));
    if (!name) { toast('Нужно название', true); return; }
    if (!kcalValue || kcalValue <= 0) { toast('Нужны калории', true); return; }
    saveMeal({
      name,
      kcal: kcalValue,
      protein: (($('mProtein') || {}).value) || 0,
      fat: (($('mFat') || {}).value) || 0,
      carb: (($('mCarb') || {}).value) || 0,
    });
  },

  mealDel: async (node) => {
    try {
      const data = await api(`/api/meals/${node.dataset.id}`, { method: 'DELETE' });
      state.day = data.day;
      render();
      haptic('ok');
      toast('Удалено');
    } catch (err) {
      toast(err.message, true);
    }
  },

  workout: async () => {
    if (!state.day) return;
    const next = !state.day.workout;
    try {
      const data = await api(`/api/day/${state.day.date}/workout`, {
        method: 'POST', body: { workout: next },
      });
      state.day = data.day;
      render();
      haptic('ok');
    } catch (err) {
      toast(err.message, true);
    }
  },

  note: () => openNote(),

  noteSave: async () => {
    if (!state.day) return;
    const field = $('nText');
    const note = field ? field.value : '';
    try {
      const data = await api(`/api/day/${state.day.date}/workout`, {
        method: 'POST', body: { workout: state.day.workout, note },
      });
      state.day = data.day;
      closeSheet();
      render();
      toast('Заметка сохранена');
    } catch (err) {
      toast(err.message, true);
    }
  },

  goalEdit: () => openGoal(),

  goalSave: async () => {
    const field = $('gKcal');
    const value = Number(String(field ? field.value : '').replace(',', '.'));
    if (!value || value <= 0) { toast('Ккал цели должно быть больше нуля', true); return; }
    try {
      const data = await api('/api/goal', {
        method: 'POST',
        body: {
          kcal: value,
          protein: ($('gProtein') || {}).value || 0,
          fat: ($('gFat') || {}).value || 0,
          carb: ($('gCarb') || {}).value || 0,
        },
      });
      if (state.day) state.day.goal = data.goal;
      if (state.stats.data) state.stats.data.goal = data.goal;
      closeSheet();
      render();
      toast('Цель сохранена');
    } catch (err) {
      toast(err.message, true);
    }
  },

  period: (node) => {
    state.stats.period = Number(node.dataset.period) || 0;
    state.stats.data = null;
    state.stats.failed = false;
    render();
  },

  dishPick: (node) => {
    const dish = state.dishes.items.find((item) => String(item.id) === node.dataset.id);
    if (dish) openGrams(dish);
  },

  dishDel: async (node) => {
    const name = node.dataset.name || 'блюдо';
    const ok = await askConfirm({
      title: `Удалить «${name}»?`,
      text: 'Из базы блюдо исчезнет, записи в дневнике останутся.',
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/dishes/${node.dataset.id}`, { method: 'DELETE' });
      state.dishes.items = state.dishes.items.filter((item) => String(item.id) !== node.dataset.id);
      paintDishes();
      toast('Блюдо удалено');
    } catch (err) {
      toast(err.message, true);
    }
  },

  dishNew: () => openDishNew(),

  dishSave: async () => {
    const name = ((($('dName') || {}).value) || '').trim();
    const value = Number(String((($('dKcal') || {}).value) || '').replace(',', '.'));
    if (!name) { toast('Нужно название', true); return; }
    if (!value) { toast('Нужны калории на 100 г', true); return; }
    try {
      await api('/api/dishes', {
        method: 'POST',
        body: {
          name,
          kcal: value,
          protein: ($('dProtein') || {}).value || 0,
          fat: ($('dFat') || {}).value || 0,
          carb: ($('dCarb') || {}).value || 0,
        },
      });
      state.dishes.query = '';
      closeSheet();
      toast('Блюдо сохранено');
      loadDishes();
    } catch (err) {
      toast(err.message, true);
    }
  },

  download: async () => {
    try {
      const headers = initData ? { 'X-Init-Data': initData } : {};
      const response = await fetch('/api/export.csv', { headers });
      if (!response.ok) throw new Error('экспорт не отдался');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'kkal.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      toast('Файл kkal.csv готов');
    } catch (err) {
      toast(err.message, true);
    }
  },

  aiParse: async () => {
    const field = $('aiText');
    const text = ((field ? field.value : '') || '').trim();
    if (!text) { toast('Напиши, что съел', true); return; }
    state.ai.text = text;
    state.ai.busy = true;
    render();
    try {
      const data = await api('/api/ai/parse', { method: 'POST', body: { text } });
      setAiItems(data.items);
      haptic('ok');
      toast(data.items.length ? 'Проверь позиции' : 'Не понял, что там было');
    } catch (err) {
      toast(err.message, true);
    } finally {
      state.ai.busy = false;
      render();
    }
  },

  aiToggle: (node) => {
    const item = state.ai.items[Number(node.dataset.idx)];
    if (!item) return;
    item.on = node.checked;
    const row = node.closest('.ai-item');
    if (row) row.classList.toggle('off', !node.checked);
  },

  aiClear: () => {
    state.ai.items = [];
    render();
  },

  aiApply: async () => {
    const picked = state.ai.items.filter((item) => item.on !== false);
    if (!picked.length) { toast('Все позиции сняты', true); return; }
    try {
      const data = await api('/api/ai/apply', {
        method: 'POST',
        body: { date: state.date, items: picked.map(toPayload) },
      });
      state.day = data.day;
      state.ai.items = [];
      state.ai.text = '';
      render();
      haptic('ok');
      toast(`Записано: ${data.saved}`);
    } catch (err) {
      toast(err.message, true);
    }
  },

  aiCoach: async () => {
    state.ai.busy = true;
    render();
    try {
      const data = await api('/api/ai/coach', { method: 'POST', body: { period: 7 } });
      state.ai.coach = data.text;
      haptic('ok');
    } catch (err) {
      toast(err.message, true);
    } finally {
      state.ai.busy = false;
      render();
    }
  },
};

function toPayload(item) {
  return {
    dish_id: item.dish_id,
    name: item.name,
    grams: item.grams,
    kcal: item.kcal,
    protein: item.protein,
    fat: item.fat,
    carb: item.carb,
  };
}

/* ----------------------------------------------------- позиции ИИ и картинка */

function setAiItems(items) {
  state.ai.items = (items || []).map((item) => {
    const grams = Number(item.grams) || 100;
    const base = {
      grams,
      kcal: Number(item.kcal) || 0,
      protein: Number(item.protein) || 0,
      fat: Number(item.fat) || 0,
      carb: Number(item.carb) || 0,
    };
    return { ...item, ...base, base, on: true };
  });
}

function resizeItem(item, grams) {
  if (!item.base || !item.base.grams || grams <= 0) return;
  const factor = grams / item.base.grams;
  ['kcal', 'protein', 'fat', 'carb'].forEach((key) => { item[key] = item.base[key] * factor; });
  item.grams = grams;
}

async function sendPhoto(file) {
  state.ai.photoBusy = true;
  render();
  try {
    const dataUrl = await shrinkImage(file);
    const data = await api('/api/ai/photo', { method: 'POST', body: { image: dataUrl } });
    setAiItems(data.items);
    haptic('ok');
    toast(data.items.length ? 'Проверь позиции' : 'На фото не разобрать еду');
  } catch (err) {
    toast(err.message, true);
  } finally {
    state.ai.photoBusy = false;
    render();
  }
}

function shrinkImage(file) {
  return new Promise((resolve, reject) => {
    if (!file || !String(file.type).startsWith('image/')) {
      reject(new Error('это не картинка'));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('не прочитал файл'));
    reader.onload = () => {
      const image = new Image();
      image.onerror = () => reject(new Error('не открыл картинку'));
      image.onload = () => {
        const limit = 1280;
        const scale = Math.min(1, limit / Math.max(image.width || 1, image.height || 1));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round((image.width || 1) * scale));
        canvas.height = Math.max(1, Math.round((image.height || 1) * scale));
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL('image/jpeg', 0.85));
      };
      image.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

/* ------------------------------------------------------- поиск в шторке */

let searchTimer = 0;

function quickSearch(text) {
  const container = $('quickList');
  if (!container) return;
  container.innerHTML = '<div class="empty">Ищу…</div>';
  api('/api/dishes', { query: { q: text } })
    .then((data) => {
      const box = $('quickList');
      if (!box) return;
      state.quickItems = data.items;
      if (!data.items.length) {
        box.innerHTML = '<div class="empty">Ничего не нашлось — впиши вручную</div>';
        return;
      }
      box.innerHTML = `<ul class="list">${data.items.map((dish) => `
        <li class="dish">
          <div class="dish-main">
            <span class="dish-name">${esc(dish.name)}</span>
            <span class="dish-sub">${kcal(dish.kcal)} ккал · Б ${num(dish.protein)} · Ж ${num(dish.fat)} · У ${num(dish.carb)}</span>
          </div>
          <button class="btn small" data-act="quickPick" data-id="${dish.id}">＋</button>
        </li>`).join('')}</ul>`;
    })
    .catch((err) => {
      const box = $('quickList');
      if (box) box.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    });
}

/* --------------------------------------------------------------- события */

document.addEventListener('click', async (event) => {
  const node = event.target.closest('[data-act]');
  if (!node) return;
  const act = node.dataset.act;
  const isField = node.matches('input, textarea, select');

  if (act === 'quickPick') {
    event.preventDefault();
    const dish = state.quickItems.find((item) => String(item.id) === node.dataset.id);
    if (dish) openGrams(dish);
    return;
  }

  const handler = actions[act];
  if (!handler) return;
  if (!isField) event.preventDefault();
  try {
    await handler(node, event);
  } catch (err) {
    toast(err.message || 'Что-то пошло не так', true);
  }
});

document.addEventListener('input', (event) => {
  const node = event.target;

  if (node.id === 'pickGrams') { updatePickPreview(); return; }

  if (node.id === 'dishSearch') {
    state.dishes.query = node.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadDishes, 280);
    return;
  }

  if (node.id === 'quickSearch') {
    clearTimeout(searchTimer);
    const text = node.value;
    searchTimer = setTimeout(() => quickSearch(text), 250);
    return;
  }

  if (node.dataset && node.dataset.act === 'aiGrams') {
    const item = state.ai.items[Number(node.dataset.idx)];
    if (!item) return;
    resizeItem(item, Number(String(node.value).replace(',', '.')) || 0);
    const row = node.closest('.ai-item');
    const value = row && row.querySelector('.js-kcal');
    if (value) value.textContent = kcal(item.kcal);
  }
});

document.addEventListener('change', (event) => {
  const node = event.target;
  if (node.id === 'aiPhoto' && node.files && node.files[0]) {
    const file = node.files[0];
    node.value = '';
    sendPhoto(file);
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeSheet();
  if (event.key === 'Enter' && event.target.id === 'quickSearch') {
    event.preventDefault();
    quickSearch(event.target.value);
  }
});

/* ------------------------------------------------------------------ старт */

function showFatal(message) {
  $('fatal').classList.remove('hidden');
  $('fatal').innerHTML = `
    <h2>Не открылось</h2>
    <p class="hint">${esc(message)}</p>
    <p class="hint">Открой мини-приложение кнопкой в боте — Telegram подпишет вход сам.</p>`;
  $('view').innerHTML = '';
  $('tabbar').innerHTML = '';
}

async function boot() {
  state.screen = screenFromHash();
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      initData = tg.initData || '';
      if (tg.setHeaderColor) tg.setHeaderColor('secondary_bg_color');
      if (tg.setBackgroundColor) tg.setBackgroundColor('bg_color');
      if (tg.BackButton) tg.BackButton.onClick(() => closeSheet());
    } catch (err) { /* старый клиент */ }
  }

  try {
    const session = await api('/api/session');
    state.session = session;
    state.today = session.today;
    state.date = session.today;
    state.day = session.day;
    state.ai.status = session.ai;
    state.demo = Boolean(session.demo);
    render();
    if (state.screen === 'dishes') loadDishes();
  } catch (err) {
    showFatal(err.message);
  }
}

boot();
