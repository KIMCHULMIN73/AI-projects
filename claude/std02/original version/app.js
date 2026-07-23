// 확장 기능: 할 일에 시작 시간·소요 시간을 추가하고, 시간 경과에 따라
// 체크박스/텍스트 상태(예정/경고/기한 초과)를 실시간으로 반영한다.
// 입력 시 다른 항목과 시간이 겹치면 팝업으로 막는다.

const STORAGE_KEY = 'todos-app-v1';

const CATEGORY_LABELS = {
  work: '업무',
  personal: '개인',
  study: '공부',
};

const todoInput = document.getElementById('todoInput');
const categorySelect = document.getElementById('categorySelect');
const startTimeInput = document.getElementById('startTimeInput');
const durationInput = document.getElementById('durationInput');
const addBtn = document.getElementById('addBtn');
const filterTabs = document.querySelectorAll('.filter-tab');
const clearCompletedBtn = document.getElementById('clearCompletedBtn');
const todoList = document.getElementById('todoList');
const progressBarFill = document.getElementById('progressBarFill');
const progressText = document.getElementById('progressText');

let todos = [];
let editingId = null; // 현재 편집 중인 항목의 id (없으면 null)
let currentFilter = 'all'; // 'all' | 'work' | 'personal' | 'study'

function loadTodos() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (err) {
    console.error('할 일 데이터를 불러오지 못했습니다:', err);
    return [];
  }
}

function saveTodos() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(todos));
  } catch (err) {
    console.error('할 일 데이터를 저장하지 못했습니다:', err);
  }
}

// ── 시간/일정 유틸 ──────────────────────────────────────
// 모든 일정은 '오늘' 하루 기준이라고 가정한다(데일리 할 일 관리 앱).

function timeStringToDate(timeStr, base = new Date()) {
  const [h, m] = timeStr.split(':').map(Number);
  const d = new Date(base);
  d.setHours(h, m, 0, 0);
  return d;
}

function hasSchedule(todo) {
  return Boolean(todo.startTime) && Number.isFinite(todo.durationMinutes) && todo.durationMinutes > 0;
}

function getScheduleRange(todo) {
  const start = timeStringToDate(todo.startTime);
  const end = new Date(start.getTime() + todo.durationMinutes * 60000);
  return { start, end };
}

// 'none'(일정 미설정) | 'upcoming'(시작 전) | 'warning'(진행 중) | 'overdue'(소요시간 초과)
function getScheduleStatus(todo, now = new Date()) {
  if (!hasSchedule(todo)) return 'none';

  const { start, end } = getScheduleRange(todo);
  if (now < start) return 'upcoming';
  if (now < end) return 'warning';
  return 'overdue';
}

function findOverlappingTodo(startTime, durationMinutes, excludeId = null) {
  const start = timeStringToDate(startTime);
  const end = new Date(start.getTime() + durationMinutes * 60000);

  return todos.find((t) => {
    if (t.id === excludeId || !hasSchedule(t)) return false;
    const other = getScheduleRange(t);
    return start < other.end && other.start < end;
  });
}

// ── CRUD ──────────────────────────────────────

function addTodo() {
  const title = todoInput.value.trim();

  if (!title) {
    todoInput.focus();
    return;
  }

  const startTime = startTimeInput.value || null;
  const durationMinutes = durationInput.value ? Number(durationInput.value) : null;

  if (startTime && durationMinutes) {
    const conflict = findOverlappingTodo(startTime, durationMinutes);
    if (conflict) {
      alert(`다른 업무와 시간 중복\n"${conflict.title}"과(와) 시간이 겹칩니다.`);
      return;
    }
  }

  todos.push({
    id: Date.now(),
    title,
    category: categorySelect.value || 'personal',
    completed: false,
    createdAt: Date.now(),
    startTime: startTime && durationMinutes ? startTime : null,
    durationMinutes: startTime && durationMinutes ? durationMinutes : null,
  });

  todoInput.value = '';
  startTimeInput.value = '';
  durationInput.value = '';
  todoInput.focus();

  saveTodos();
  renderTodos();
}

function startEdit(id) {
  editingId = id;
  renderTodos();
}

function commitEdit(id, newTitle, newCategory, newStartTime, newDurationMinutes) {
  const title = newTitle.trim();

  if (!title) {
    editingId = null;
    renderTodos();
    return;
  }

  if (newStartTime && newDurationMinutes) {
    const conflict = findOverlappingTodo(newStartTime, newDurationMinutes, id);
    if (conflict) {
      alert(`다른 업무와 시간 중복\n"${conflict.title}"과(와) 시간이 겹칩니다.`);
      return;
    }
  }

  const todo = todos.find((t) => t.id === id);
  if (todo) {
    todo.title = title;
    todo.category = newCategory;
    todo.startTime = newStartTime && newDurationMinutes ? newStartTime : null;
    todo.durationMinutes = newStartTime && newDurationMinutes ? newDurationMinutes : null;
  }

  editingId = null;
  saveTodos();
  renderTodos();
}

function cancelEdit() {
  editingId = null;
  renderTodos();
}

function toggleComplete(id) {
  const todo = todos.find((t) => t.id === id);
  if (!todo) return;

  if (getScheduleStatus(todo) === 'overdue') return; // 기한 초과 항목은 완료 처리 불가

  todo.completed = !todo.completed;
  saveTodos();
  renderTodos();
}

function setFilter(filter) {
  currentFilter = filter;

  filterTabs.forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.filter === filter);
  });

  renderTodos();
}

function deleteTodo(id) {
  todos = todos.filter((t) => t.id !== id);
  if (editingId === id) {
    editingId = null;
  }
  saveTodos();
  renderTodos();
}

function clearCompleted() {
  todos = todos.filter((t) => !t.completed);
  saveTodos();
  renderTodos();
}

// 진행률은 필터와 무관하게 항상 전체 todos 기준으로 계산한다.
function updateProgress() {
  const total = todos.length;
  const completedCount = todos.filter((t) => t.completed).length;
  const percent = total === 0 ? 0 : Math.round((completedCount / total) * 100);

  progressBarFill.style.width = `${percent}%`;
  progressText.textContent = `완료 ${completedCount} / 전체 ${total}`;
}

// 기한을 넘긴 항목은 완료 상태로 둘 수 없도록 강제한다.
function enforceOverdueRules() {
  let changed = false;

  todos.forEach((todo) => {
    if (getScheduleStatus(todo) === 'overdue' && todo.completed) {
      todo.completed = false;
      changed = true;
    }
  });

  if (changed) saveTodos();
}

function formatScheduleLabel(todo) {
  const { start, end } = getScheduleRange(todo);
  const fmt = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return `${fmt(start)}~${fmt(end)}`;
}

// ── 렌더링 ──────────────────────────────────────

function createEditRow(todo) {
  const item = document.createElement('div');
  item.className = 'todo-item';

  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'todo-checkbox';
  checkbox.checked = todo.completed;
  checkbox.disabled = true; // 편집 중에는 완료 토글 비활성화(제목/카테고리/시간만 편집)

  const titleInput = document.createElement('input');
  titleInput.type = 'text';
  titleInput.className = 'todo-edit-input';
  titleInput.value = todo.title;

  const categorySelectEl = document.createElement('select');
  categorySelectEl.className = 'todo-edit-select';
  Object.entries(CATEGORY_LABELS).forEach(([value, label]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (value === todo.category) option.selected = true;
    categorySelectEl.appendChild(option);
  });

  const startTimeEl = document.createElement('input');
  startTimeEl.type = 'time';
  startTimeEl.className = 'todo-edit-select';
  startTimeEl.value = todo.startTime || '';

  const durationEl = document.createElement('input');
  durationEl.type = 'number';
  durationEl.className = 'todo-edit-select todo-edit-duration';
  durationEl.min = '1';
  durationEl.placeholder = '소요(분)';
  durationEl.value = todo.durationMinutes || '';

  const commit = () => commitEdit(
    todo.id,
    titleInput.value,
    categorySelectEl.value,
    startTimeEl.value || null,
    durationEl.value ? Number(durationEl.value) : null,
  );

  [titleInput, startTimeEl, durationEl].forEach((el) => {
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') commit();
      if (e.key === 'Escape') cancelEdit();
    });
  });

  const actions = document.createElement('div');
  actions.className = 'todo-actions';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'text-btn save-btn';
  saveBtn.textContent = '저장';
  saveBtn.addEventListener('click', commit);

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'text-btn cancel-btn';
  cancelBtn.textContent = '취소';
  cancelBtn.addEventListener('click', cancelEdit);

  actions.appendChild(saveBtn);
  actions.appendChild(cancelBtn);

  item.appendChild(checkbox);
  item.appendChild(titleInput);
  item.appendChild(categorySelectEl);
  item.appendChild(startTimeEl);
  item.appendChild(durationEl);
  item.appendChild(actions);
  return { item, focus: () => titleInput.focus() };
}

function createViewRow(todo) {
  const status = getScheduleStatus(todo); // 'none' | 'upcoming' | 'warning' | 'overdue'

  const item = document.createElement('div');
  item.className = 'todo-item';
  item.classList.toggle('completed', todo.completed);
  item.classList.toggle('status-none', status === 'none');
  item.classList.toggle('status-overdue', status === 'overdue');

  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'todo-checkbox';
  checkbox.classList.toggle('status-warning', status === 'warning');
  checkbox.classList.toggle('status-overdue', status === 'overdue');
  checkbox.checked = todo.completed;
  checkbox.disabled = status === 'overdue';
  checkbox.addEventListener('change', () => toggleComplete(todo.id));

  const title = document.createElement('span');
  title.className = 'todo-title';
  title.textContent = todo.title;
  title.addEventListener('dblclick', () => startEdit(todo.id));

  const badge = document.createElement('span');
  badge.className = `category-badge category-${todo.category}`;
  badge.textContent = CATEGORY_LABELS[todo.category] || todo.category;

  const actions = document.createElement('div');
  actions.className = 'todo-actions';

  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.className = 'icon-btn edit-btn';
  editBtn.textContent = '✎';
  editBtn.addEventListener('click', () => startEdit(todo.id));

  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'icon-btn delete-btn';
  deleteBtn.textContent = '✕';
  deleteBtn.addEventListener('click', () => deleteTodo(todo.id));

  actions.appendChild(editBtn);
  actions.appendChild(deleteBtn);

  item.appendChild(checkbox);
  item.appendChild(title);

  if (hasSchedule(todo)) {
    const scheduleLabel = document.createElement('span');
    scheduleLabel.className = 'schedule-label';
    scheduleLabel.textContent = formatScheduleLabel(todo);
    item.appendChild(scheduleLabel);
  }

  item.appendChild(badge);
  item.appendChild(actions);
  return item;
}

function renderTodos() {
  enforceOverdueRules();

  todoList.innerHTML = '';

  if (todos.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty-message';
    empty.textContent = '할 일을 추가해보세요';
    todoList.appendChild(empty);
    updateProgress();
    return;
  }

  let rowToFocus = null;

  // 필터는 화면 표시만 바꾸고, todos 배열(데이터) 자체는 그대로 유지한다.
  const visibleTodos = currentFilter === 'all'
    ? todos
    : todos.filter((todo) => todo.category === currentFilter);

  visibleTodos.forEach((todo) => {
    if (todo.id === editingId) {
      const { item, focus } = createEditRow(todo);
      todoList.appendChild(item);
      rowToFocus = focus;
    } else {
      todoList.appendChild(createViewRow(todo));
    }
  });

  if (rowToFocus) rowToFocus();
  updateProgress();
}

addBtn.addEventListener('click', addTodo);
todoInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') addTodo();
});

filterTabs.forEach((tab) => {
  tab.addEventListener('click', () => setFilter(tab.dataset.filter));
});

clearCompletedBtn.addEventListener('click', clearCompleted);

// 시작/종료 시각 경과에 따른 상태 전환(예정→경고→기한 초과)을 실시간으로 반영한다.
// 편집 중일 때는 다시 그리지 않아 입력 포커스를 유지한다.
setInterval(() => {
  if (editingId === null) renderTodos();
}, 1000);

function init() {
  todos = loadTodos();
  renderTodos();
}

init();
