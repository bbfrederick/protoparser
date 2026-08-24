/* The GUI's behaviour.
 *
 * Two rules shape this file. Nothing here knows what any command line flag
 * means: forms are generated from the specification the server sends, and the
 * command line shown is built by the server from the same code that builds the
 * arguments the tool is actually run with. And nothing is fetched from the
 * network: the page is served by a loopback server, so there is no framework,
 * no bundler and no external font.
 */

'use strict';

/* -- session ------------------------------------------------------------ */

/* The token arrives once in the URL and is then scrubbed from the address
 * bar, so it does not survive into a bookmark, a screenshot or a pasted link.
 * It is sent as a header from here on, which also forces a preflight on any
 * cross-origin attempt -- and nothing on the server approves one. */
const TOKEN = new URLSearchParams(location.search).get('token') || '';
history.replaceState(null, '', location.pathname);

const state = {
  spec: null,
  sep: '/',
  commands: new Map(),
  groups: [],
  group: null,
  command: null,
  values: new Map(),
  job: null,
  since: 0,
  dropped: 0,
  timer: null,
  previewTimer: null,
};

const el = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'X-Auth-Token': TOKEN, ...(options.headers || {}) },
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = { error: `${response.status} ${response.statusText}` };
  }
  if (!response.ok) {
    throw new Error((payload && payload.error) || `${response.status}`);
  }
  return payload;
}

const post = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });

/* -- start-up ----------------------------------------------------------- */

async function start() {
  let spec;
  try {
    spec = await api('/api/spec');
  } catch (error) {
    el('panel').innerHTML =
      '<p class="loading">Could not reach the server. ' +
      'If this page was reloaded from a bookmark, its session token is gone: ' +
      'restart <code>mr-protocol-gui</code> and use the URL it prints.</p>';
    return;
  }

  state.spec = spec;
  state.sep = spec.sep || '/';
  el('version').textContent = spec.version;
  el('cwd').textContent = spec.cwd;
  el('cwd').title = `Relative paths resolve against ${spec.cwd}`;

  for (const command of spec.commands) {
    state.commands.set(command.name, command);
    if (!state.groups.includes(command.group)) state.groups.push(command.group);
    const values = {};
    for (const field of command.fields) values[field.name] = field.default;
    state.values.set(command.name, values);
  }

  buildTabs();
  selectGroup(state.groups[0]);
  wire();
}

function buildTabs() {
  const tabs = el('tabs');
  tabs.textContent = '';
  for (const group of state.groups) {
    const button = document.createElement('button');
    button.type = 'button';
    button.role = 'tab';
    button.textContent = group;
    button.addEventListener('click', () => selectGroup(group));
    button.dataset.group = group;
    tabs.appendChild(button);
  }
}

function selectGroup(group) {
  state.group = group;
  for (const button of el('tabs').children) {
    button.setAttribute('aria-selected', button.dataset.group === group ? 'true' : 'false');
  }
  const members = state.spec.commands.filter((command) => command.group === group);
  selectCommand(members[0].name);
}

function selectCommand(name) {
  state.command = name;
  renderPanel();
  refreshPreview();
}

/* -- form rendering ----------------------------------------------------- */

function renderPanel() {
  const command = state.commands.get(state.command);
  const panel = el('panel');
  panel.textContent = '';

  const members = state.spec.commands.filter((item) => item.group === command.group);
  if (members.length > 1) {
    const row = document.createElement('div');
    row.className = 'subcommands';
    for (const member of members) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = member.title;
      button.setAttribute('aria-pressed', member.name === command.name ? 'true' : 'false');
      button.addEventListener('click', () => selectCommand(member.name));
      row.appendChild(button);
    }
    panel.appendChild(row);
  }

  const summary = document.createElement('p');
  summary.className = 'summary';
  summary.textContent = command.summary;
  panel.appendChild(summary);

  if (!command.fields.length) return;

  const fields = document.createElement('div');
  fields.className = 'fields';
  for (const field of command.fields) fields.appendChild(renderField(command, field));
  panel.appendChild(fields);
}

function renderField(command, field) {
  const values = state.values.get(command.name);
  const wrapper = document.createElement('div');
  wrapper.className = field.kind === 'flag' ? 'field flag' : 'field';

  const label = document.createElement('label');
  label.textContent = field.label;
  label.htmlFor = `f-${command.name}-${field.name}`;
  if (field.required) {
    const mark = document.createElement('span');
    mark.className = 'required';
    mark.textContent = '*';
    mark.title = 'Required';
    label.appendChild(mark);
  }
  wrapper.appendChild(label);

  const control = document.createElement('div');
  control.className = 'control';
  control.append(...buildControl(command, field, values));
  wrapper.appendChild(control);

  const help = document.createElement('p');
  help.className = 'help';
  help.textContent = field.help;
  wrapper.appendChild(help);
  return wrapper;
}

function buildControl(command, field, values) {
  const id = `f-${command.name}-${field.name}`;
  const commit = (value) => {
    values[field.name] = value;
    refreshPreview();
  };

  if (field.kind === 'flag') {
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.id = id;
    box.checked = Boolean(values[field.name]);
    box.addEventListener('change', () => commit(box.checked));
    return [box];
  }

  if (field.kind === 'choice') {
    const select = document.createElement('select');
    select.id = id;
    for (const choice of field.choices) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice === '' ? 'all' : choice;
      select.appendChild(option);
    }
    select.value = values[field.name] ?? field.default;
    select.addEventListener('change', () => commit(select.value));
    return [select];
  }

  if (field.kind === 'pair') {
    const pair = Array.isArray(values[field.name]) ? values[field.name] : ['', ''];
    values[field.name] = pair;
    const nodes = [];
    for (const index of [0, 1]) {
      const box = textBox(index === 0 ? id : `${id}-2`, pair[index]);
      box.placeholder = index === 0 ? 'left' : 'right';
      box.addEventListener('input', () => {
        pair[index] = box.value;
        refreshPreview();
      });
      nodes.push(box, browseButton(field, () => box.value, (chosen) => {
        box.value = chosen;
        pair[index] = chosen;
        refreshPreview();
      }));
    }
    return nodes;
  }

  const box = textBox(id, values[field.name] ?? '');
  if (field.kind === 'int') {
    box.type = 'number';
    box.step = '1';
  }
  box.addEventListener('input', () => commit(box.value));
  if (field.kind !== 'path') return [box];

  return [box, browseButton(field, () => box.value, (chosen) => {
    box.value = chosen;
    commit(chosen);
  })];
}

function textBox(id, value) {
  const box = document.createElement('input');
  box.type = 'text';
  box.id = id;
  box.spellcheck = false;
  box.value = value ?? '';
  return box;
}

function browseButton(field, read, write) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'ghost';
  button.textContent = 'Browse';
  button.addEventListener('click', () => openPicker(field, read(), write));
  return button;
}

/* -- command line preview ----------------------------------------------- */

function refreshPreview() {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(sendPreview, 120);
}

async function sendPreview() {
  const command = state.commands.get(state.command);
  const values = state.values.get(command.name);
  try {
    const result = await post('/api/preview', { command: command.name, values });
    el('command').textContent = result.display;
    setStatus('', '');
  } catch (error) {
    /* A required field left empty is the usual reason, and saying so before
     * Run is pressed is more useful than refusing afterwards. */
    el('command').textContent = `mr-protocol-tool ${command.argv.join(' ')} …`;
    setStatus(error.message, 'busy');
  }
}

function setStatus(text, kind) {
  const node = el('status');
  node.textContent = text;
  node.className = `status ${kind || ''}`.trim();
}

/* -- running ------------------------------------------------------------ */

function appendLine(line, kind) {
  const log = el('log');
  const node = document.createElement('span');
  node.textContent = `${line}\n`;
  if (kind) node.className = kind;
  log.appendChild(node);
  if (el('follow').checked) log.scrollTop = log.scrollHeight;
}

function classify(line) {
  if (/^\s*warning:/i.test(line)) return 'warn';
  if (/^(could not|no PDFs found|usage:|.*: error:)/i.test(line)) return 'bad';
  return '';
}

async function run() {
  const command = state.commands.get(state.command);
  const values = state.values.get(command.name);
  let started;
  try {
    started = await post('/api/run', { command: command.name, values });
  } catch (error) {
    setStatus(error.message, 'bad');
    return;
  }

  state.job = started.id;
  state.since = 0;
  state.dropped = 0;
  el('run').disabled = true;
  el('stop').disabled = false;
  setStatus('Running…', 'busy');
  appendLine(`$ ${started.display}`, 'meta');
  poll();
}

async function poll() {
  clearTimeout(state.timer);
  let snapshot;
  try {
    snapshot = await api(`/api/job?id=${state.job}&since=${state.since}`);
  } catch (error) {
    finish(null, error.message);
    return;
  }

  /* Report a drop the first time one is seen. Keying this off the poll
   * position would miss it, because a run long enough to overflow has
   * always been polled well before it does. */
  if (snapshot.dropped > state.dropped) {
    appendLine(
      `(${snapshot.dropped - state.dropped} earlier lines dropped: this run produced more ` +
      'than the pane keeps. Write to a file instead of showing it here.)',
      'meta');
    state.dropped = snapshot.dropped;
  }
  for (const line of snapshot.lines) appendLine(line, classify(line));
  state.since = snapshot.next;

  if (snapshot.done) {
    finish(snapshot.returncode, null, snapshot.cancelled);
    return;
  }
  state.timer = setTimeout(poll, 250);
}

function finish(returncode, message, cancelled) {
  el('run').disabled = false;
  el('stop').disabled = true;
  state.job = null;

  if (message) {
    appendLine(`(lost contact with the server: ${message})`, 'bad');
    setStatus(message, 'bad');
    return;
  }
  if (cancelled) {
    appendLine('(stopped)', 'meta');
    setStatus('Stopped', 'busy');
    return;
  }
  /* A non-zero status is not always a failure here: diff exits 1 when it
   * found differences, and check exits 1 when a rule was violated. Both are
   * successful runs that answered the question asked, so the status line
   * reports the code and leaves the reading to the output above it. */
  const ok = returncode === 0;
  appendLine(`(exit status ${returncode})`, 'meta');
  setStatus(ok ? 'Finished' : `Finished with exit status ${returncode}`, ok ? 'good' : 'busy');
}

/* -- file picker -------------------------------------------------------- */

const picker = { field: null, write: null, path: '', accept: [] };

function joinPath(directory, name) {
  const sep = state.sep;
  if (!directory) return name;
  return directory.endsWith(sep) ? `${directory}${name}` : `${directory}${sep}${name}`;
}

function openPicker(field, current, write) {
  picker.field = field;
  picker.write = write;
  picker.accept = field.accept || [];

  el('picker-title').textContent = {
    dir: 'Choose a directory',
    save: 'Choose where to write',
    any: 'Choose a file or directory',
    file: 'Choose a file',
  }[field.picker] || 'Choose a file';

  el('picker-name').style.display = field.picker === 'dir' ? 'none' : '';
  el('picker-name').value = '';
  el('picker-error').textContent = '';

  const places = el('picker-places');
  places.textContent = '';
  for (const place of state.spec.shortcuts) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = place.label;
    button.title = place.path;
    button.addEventListener('click', () => navigate(place.path));
    places.appendChild(button);
  }

  el('picker').showModal();
  navigate(current || state.spec.cwd);
}

async function navigate(path) {
  let result;
  try {
    result = await api(`/api/browse?path=${encodeURIComponent(path)}&accept=${picker.accept.join(',')}`);
  } catch (error) {
    el('picker-error').textContent = error.message;
    return;
  }

  picker.path = result.path;
  el('picker-current').value = result.path;
  el('picker-up').disabled = !result.parent;
  el('picker-error').textContent = result.error || '';

  const list = el('picker-list');
  list.textContent = '';
  if (!result.entries.length) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = picker.accept.length
      ? `Nothing here matching ${picker.accept.join(' or ')}`
      : 'Nothing here';
    list.appendChild(empty);
    return;
  }

  for (const entry of result.entries) {
    const item = document.createElement('li');
    if (entry.dir) item.className = 'dir';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = entry.dir ? `${entry.name}${state.sep}` : entry.name;
    button.title = entry.path;
    button.addEventListener('click', () => {
      if (entry.dir && picker.field.picker !== 'dir') navigate(entry.path);
      else if (entry.dir) { el('picker-current').value = entry.path; navigate(entry.path); }
      else choose(entry.path);
    });
    item.appendChild(button);
    list.appendChild(item);
  }
}

function choose(path) {
  picker.write(path);
  el('picker').close();
}

function chooseCurrent() {
  const mode = picker.field.picker;
  const name = el('picker-name').value.trim();
  if (mode === 'dir' || (!name && mode === 'any')) {
    choose(picker.path);
    return;
  }
  if (!name) {
    el('picker-error').textContent = 'Pick a file from the list, or type a name.';
    return;
  }
  choose(name.includes(state.sep) ? name : joinPath(picker.path, name));
}

/* -- wiring ------------------------------------------------------------- */

function wire() {
  el('run').addEventListener('click', run);
  el('stop').addEventListener('click', () => post('/api/stop').catch(() => {}));
  el('clear').addEventListener('click', () => { el('log').textContent = ''; });

  el('copy').addEventListener('click', async () => {
    const text = el('command').textContent;
    try {
      await navigator.clipboard.writeText(text);
      setStatus('Command line copied', 'good');
    } catch (error) {
      /* Clipboard access can be refused; selecting the text is a fair fallback. */
      const range = document.createRange();
      range.selectNodeContents(el('command'));
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      setStatus('Copy with your keyboard: the command line is selected', 'busy');
    }
  });

  el('quit').addEventListener('click', async () => {
    await post('/api/quit').catch(() => {});
    document.body.innerHTML =
      '<main><section class="panel"><p class="loading">The GUI has been shut down. ' +
      'You can close this tab.</p></section></main>';
  });

  el('picker-up').addEventListener('click', () => {
    api(`/api/browse?path=${encodeURIComponent(picker.path)}`).then((result) => {
      if (result.parent) navigate(result.parent);
    });
  });
  el('picker-go').addEventListener('click', () => navigate(el('picker-current').value));
  el('picker-current').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); navigate(el('picker-current').value); }
  });
  el('picker-choose').addEventListener('click', chooseCurrent);
  el('picker-name').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); chooseCurrent(); }
  });

  document.addEventListener('keydown', (event) => {
    /* Run from the keyboard, the way a terminal user expects to. */
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !el('run').disabled) {
      event.preventDefault();
      run();
    }
  });
}

start();
