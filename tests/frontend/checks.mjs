/* Drive app.js against a live GUI server and report what it did.
 *
 * Run by tests/test_frontend.py, which starts the server and passes its
 * address here. Every result is written to stdout as one JSON object per
 * line, so a crash halfway still leaves everything up to that point readable,
 * and the last line records whether the harness reached the end at all.
 *
 * Two rules keep this from becoming brittle. Nothing waits for a fixed
 * interval -- every check polls its own condition to a deadline, so a loaded
 * machine makes it slower rather than red. And expectations come from the
 * server wherever they can: the tabs are compared against the spec the page
 * was sent, the picker's listing against what /api/browse returns for the
 * same directory, and the previewed command line against what /api/preview
 * builds from the values the page is holding. Adding a release or an example
 * folder therefore changes nothing here, while the page failing to render
 * what it was given still fails.
 */

import fs from 'node:fs';
import vm from 'node:vm';

import { makeDocument, seed } from './dom.mjs';

const [, , base, token, appJsPath, indexHtmlPath] = process.argv;

/* Generous: every one of these is a loopback round trip or a local render.
 * The ceiling exists so a broken page fails instead of hanging. */
const WAIT_MS = Number(process.env.GUI_FRONTEND_TIMEOUT || 8000);
/* A run spawns a child interpreter, which dominates everything else here. */
const RUN_MS = Number(process.env.GUI_FRONTEND_RUN_TIMEOUT || 60000);

/* The directory the picker is pointed at. It is the one place a real path is
 * written out; the server is started on the repository root, and the test
 * that calls this harness skips when the example tree is absent. */
const BROWSE_AT = 'examples';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function record(group, name, ok, detail) {
  process.stdout.write(
    JSON.stringify({ group, name, ok, detail: detail === undefined ? null : detail }) + '\n');
}

/* Poll a condition to a deadline, then record the verdict.
 *
 * `describe` is called only when the check fails, so a diagnosis may be as
 * expensive to compute as it needs to be.
 */
async function check(group, name, condition, describe, timeout = WAIT_MS) {
  const deadline = Date.now() + timeout;
  let ok = false;
  for (;;) {
    try {
      ok = Boolean(condition());
    } catch (error) {
      ok = false;
    }
    if (ok || Date.now() >= deadline) break;
    await sleep(10);
  }
  let detail = null;
  if (!ok && describe) {
    try {
      detail = describe();
    } catch (error) {
      detail = `could not describe the failure: ${error.message}`;
    }
  }
  record(group, name, ok, detail);
  return ok;
}

/* Wait for a condition without recording anything, to sequence one step
 * after another. Checks assert; this only paces. */
async function settle(condition, timeout = WAIT_MS) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      if (condition()) return true;
    } catch (error) {
      /* Not ready yet. */
    }
    await sleep(10);
  }
  return false;
}

/* -- the page ----------------------------------------------------------- */

const byId = seed(indexHtmlPath);
const { document, missing, fireDocument } = makeDocument(byId);
const el = (id) => byId.get(id);

/* An unhandled error anywhere in app.js -- including inside a promise nothing
 * awaits -- must be a failure, not a silently degraded page. */
const crashes = [];
const remember = (error) => crashes.push(String((error && error.stack) || error));
process.on('uncaughtException', remember);
process.on('unhandledRejection', remember);

let scrubbed = false;
let clipboard = null;

const sandbox = {
  document,
  console,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  URLSearchParams,
  location: { search: `?token=${token}`, pathname: '/' },
  history: {
    replaceState() {
      scrubbed = true;
    },
  },
  getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
  navigator: {
    clipboard: {
      writeText: async (text) => {
        clipboard = text;
      },
    },
  },
  fetch: (path, options) => fetch(base + path, options),
};
sandbox.window = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);

/* app.js declares its state with const at the top level, which lands in the
 * context's lexical scope rather than on the sandbox object. Evaluating an
 * expression in the same context is the only way to read it -- and reading it
 * is what lets the preview be compared against the server rather than against
 * a string written out here. */
const peek = (expression) => vm.runInContext(expression, sandbox);
const peekJson = (expression) => JSON.parse(peek(`JSON.stringify(${expression})`));

let loaded = true;
let loadError = null;
try {
  vm.runInContext(fs.readFileSync(appJsPath, 'utf8'), sandbox, { filename: 'app.js' });
} catch (error) {
  loaded = false;
  loadError = String((error && error.stack) || error);
}

/* -- talking to the server directly ------------------------------------- */

async function api(path, options = {}) {
  const response = await fetch(base + path, {
    ...options,
    headers: { 'X-Auth-Token': token, ...(options.headers || {}) },
  });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

const post = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });

/* -- reading the rendered page ------------------------------------------- */

const tabs = () => el('tabs').children;
const actions = () =>
  el('panel').all(
    (node) => node.tagName === 'BUTTON' && node.getAttribute('aria-pressed') !== undefined);
const controlFor = (command, field) =>
  el('panel').first((node) => node.id === `f-${command}-${field}`);
const listed = () => el('picker-list').children.map((item) => item.children[0].textContent);

/* Bring one command on screen the way a user would: click its tab, then its
 * action button if that tab holds more than one command. */
async function show(spec, name) {
  const wanted = spec.commands.find((command) => command.name === name);
  const members = spec.commands.filter((command) => command.group === wanted.group);
  tabs().find((button) => button.dataset.group === wanted.group).click();
  await settle(() => peek('state.group') === wanted.group);
  if (members.length > 1) {
    await settle(() => actions().length === members.length);
    actions()[members.findIndex((command) => command.name === name)].click();
  }
  await settle(() => peek('state.command') === name);
  return wanted;
}

/* -- checks -------------------------------------------------------------- */

async function main() {
  await check('startup', 'app.js loads without throwing', () => loaded, () => loadError);
  if (!loaded) return;

  const spec = await api('/api/spec');
  const groups = [];
  for (const command of spec.commands) {
    if (!groups.includes(command.group)) groups.push(command.group);
  }

  await check('startup', 'the page reaches the server and renders a form',
    () => el('panel').all((node) => node.className === 'fields').length === 1,
    () => el('panel').textContent.slice(0, 200));
  await check('startup', 'the session token is scrubbed from the address bar', () => scrubbed);
  await check('startup', 'the header shows the version the server reported',
    () => el('version').textContent === spec.version,
    () => ({ shown: el('version').textContent, expected: spec.version }));
  await check('startup', 'the header shows the directory commands will run in',
    () => el('cwd').textContent === spec.cwd,
    () => ({ shown: el('cwd').textContent, expected: spec.cwd }));

  /* -- tabs -------------------------------------------------------------- */

  await check('tabs', 'one tab per group in the spec, in the spec order',
    () => JSON.stringify(tabs().map((button) => button.textContent)) === JSON.stringify(groups),
    () => ({ shown: tabs().map((button) => button.textContent), expected: groups }));
  await check('tabs', 'exactly one tab is selected, and it is the first',
    () => tabs().filter((button) => button.getAttribute('aria-selected') === 'true').length === 1
      && tabs()[0].getAttribute('aria-selected') === 'true',
    () => tabs().map((button) => button.getAttribute('aria-selected')));

  /* Walk every command through the interface and collect what failed to
   * render. One pass, four checks, so a missing control names itself rather
   * than showing up as a mysteriously empty panel later. */
  const missed = { controls: [], choices: [], help: [], summary: [], actions: [] };
  for (const command of spec.commands) {
    await show(spec, command.name);
    const panel = el('panel');
    const members = spec.commands.filter((item) => item.group === command.group);
    const offered = members.length > 1 ? actions().map((button) => button.textContent) : [];
    if (JSON.stringify(offered) !== JSON.stringify(members.length > 1 ? members.map((item) => item.title) : [])) {
      missed.actions.push({ group: command.group, offered });
    }
    if (!panel.textContent.includes(command.summary)) missed.summary.push(command.name);
    for (const field of command.fields) {
      const control = controlFor(command.name, field.name);
      if (!control) {
        missed.controls.push(`${command.name}.${field.name}`);
        continue;
      }
      if (field.kind === 'pair' && !controlFor(command.name, `${field.name}-2`)) {
        missed.controls.push(`${command.name}.${field.name}-2`);
      }
      if (field.kind === 'choice') {
        const values = control.children.map((option) => option.value);
        if (JSON.stringify(values) !== JSON.stringify(field.choices)) {
          missed.choices.push({ field: `${command.name}.${field.name}`, values, expected: field.choices });
        }
      }
      if (field.help && !panel.textContent.includes(field.help)) {
        missed.help.push(`${command.name}.${field.name}`);
      }
    }
  }

  await check('forms', 'every field of every command gets a control',
    () => missed.controls.length === 0, () => missed.controls);
  await check('forms', 'every choice control offers exactly the choices in the spec',
    () => missed.choices.length === 0, () => missed.choices);
  await check('forms', "every field's help text reaches the page",
    () => missed.help.length === 0, () => missed.help);
  await check('forms', 'every command shows its summary',
    () => missed.summary.length === 0, () => missed.summary);
  await check('tabs', 'a tab holding several commands offers each of them by title',
    () => missed.actions.length === 0, () => missed.actions);

  const bare = spec.commands.find((command) => command.fields.length === 0);
  if (bare) {
    await show(spec, bare.name);
    await check('forms', 'a command with no options renders no controls',
      () => el('panel').all(
        (node) => node.tagName === 'INPUT' || node.tagName === 'SELECT').length === 0,
      () => el('panel').all((node) => node.tagName === 'INPUT').map((node) => node.id));
  }

  const parse = await show(spec, 'parse');
  const required = parse.fields.filter((field) => field.required).map((field) => field.name);
  await check('forms', 'a required field is marked as one',
    () => required.length > 0 && required.every((name) => {
      const wrapper = controlFor('parse', name).parentNode.parentNode;
      return wrapper.all((node) => node.className === 'required').length === 1;
    }),
    () => required);

  /* -- the command line preview ------------------------------------------ */

  await check('preview', 'a required field left empty is reported before Run is pressed',
    () => el('status').textContent.toLowerCase().includes('required'),
    () => el('status').textContent);

  const pathField = parse.fields.find((field) => field.picker === 'any');
  const box = controlFor('parse', pathField.name);
  box.value = BROWSE_AT;
  box.fire('input');
  await settle(() => el('command').textContent.includes(BROWSE_AT));

  const held = peekJson('state.values.get(state.command)');
  const built = await post('/api/preview', { command: 'parse', values: held });
  await check('preview', 'the previewed command line is the one the server builds',
    () => el('command').textContent === built.display,
    () => ({ shown: el('command').textContent, server: built.display }));
  await check('preview', 'the status clears once the form is valid',
    () => el('status').textContent === '', () => el('status').textContent);

  const choice = parse.fields.find((field) => field.kind === 'choice' && field.choices.length > 1);
  const other = choice.choices.find((value) => value !== choice.default);
  const select = controlFor('parse', choice.name);
  select.value = other;
  select.fire('change');
  await check('preview', 'changing a control puts its flag on the command line',
    () => el('command').textContent.includes(`${choice.flag} ${other}`),
    () => ({ shown: el('command').textContent, wanted: `${choice.flag} ${other}` }));

  select.value = choice.default;
  select.fire('change');
  await check('preview', 'returning a control to its default takes the flag off again',
    () => !el('command').textContent.includes(choice.flag),
    () => el('command').textContent);

  await show(spec, 'diff');
  await show(spec, 'parse');
  await check('preview', 'a typed value survives leaving the tab and coming back',
    () => controlFor('parse', pathField.name).value === BROWSE_AT,
    () => controlFor('parse', pathField.name).value);

  /* -- the file picker ---------------------------------------------------- */

  const browse = controlFor('parse', pathField.name).parentNode.children.find(
    (node) => node.tagName === 'BUTTON');
  const accept = pathField.accept.join(',');
  const here = await api(`/api/browse?path=${encodeURIComponent(BROWSE_AT)}&accept=${accept}`);
  const names = here.entries.map((entry) => (entry.dir ? `${entry.name}${spec.sep}` : entry.name));
  const folder = here.entries.find((entry) => entry.dir);
  const inside = await api(`/api/browse?path=${encodeURIComponent(folder.path)}&accept=${accept}`);
  const within = inside.entries.map((entry) => (entry.dir ? `${entry.name}${spec.sep}` : entry.name));

  /* Every step waits for the listing to become the one the server returns for
   * the directory it moved to, and every step moves between two directories
   * whose listings differ. Waiting on the path box instead would race -- it is
   * written before the fetch is sent when a path is typed -- and so would
   * waiting for a listing the pane is already showing, which is why the picker
   * is opened once here and driven to the end rather than reopened: a closed
   * dialog keeps its contents, so every "has it arrived yet" test on a freshly
   * reopened picker is answered by the previous session's entries.
   */
  browse.click();
  await check('picker', 'Browse opens the picker at the path the field holds',
    () => el('picker').open === true && el('picker-current').value === here.path,
    () => ({ open: el('picker').open, at: el('picker-current').value, wanted: here.path }));
  await check('picker', 'the listing is what the server returned for that directory',
    () => JSON.stringify(listed()) === JSON.stringify(names),
    () => ({ shown: listed(), server: names }));
  await check('picker', 'directories are listed even under a suffix filter',
    () => here.entries.some((entry) => entry.dir)
      && listed().some((name) => name.endsWith(spec.sep)),
    () => listed());

  el('picker-list').children[names.indexOf(`${folder.name}${spec.sep}`)].children[0].click();
  await check('picker', 'clicking a directory descends into it',
    () => el('picker-current').value === folder.path
      && JSON.stringify(listed()) === JSON.stringify(within),
    () => ({ at: el('picker-current').value, shown: listed(), server: within }));
  await check('picker', 'the files listed inside it all pass the filter',
    () => listed().length > 0 && listed().every((name) => name.endsWith(spec.sep)
      || pathField.accept.some((suffix) => name.toLowerCase().endsWith(suffix))),
    () => listed());

  el('picker-up').click();
  await check('picker', 'Up climbs to the parent directory',
    () => el('picker-current').value === here.path
      && JSON.stringify(listed()) === JSON.stringify(names),
    () => ({ at: el('picker-current').value, shown: listed(), server: names }));

  el('picker-current').value = folder.path;
  el('picker-go').click();
  await check('picker', 'typing a path and pressing Go navigates there',
    () => JSON.stringify(listed()) === JSON.stringify(within),
    () => ({ shown: listed(), server: within }));

  el('picker-current').value = `${here.path}${spec.sep}no-such-directory-here`;
  el('picker-go').click();
  await check('picker', 'an unreachable path is reported', 
    () => el('picker-error').textContent.length > 0,
    () => ({ error: el('picker-error').textContent, at: el('picker-current').value }));
  await check('picker', 'a failed navigation leaves the pane showing where it was',
    () => JSON.stringify(listed()) === JSON.stringify(within),
    () => ({ shown: listed(), server: within }));

  const fileAt = within.findIndex((name) => !name.endsWith(spec.sep));
  await check('picker', 'that folder holds at least one selectable file',
    () => fileAt >= 0, () => within);
  if (fileAt >= 0) {
    const chosen = within[fileAt];
    el('picker-list').children[fileAt].children[0].click();
    await check('picker', 'choosing a file closes the picker and fills the field',
      () => el('picker').open === false
        && controlFor('parse', pathField.name).value.endsWith(chosen),
      () => ({ open: el('picker').open, value: controlFor('parse', pathField.name).value }));
    await check('picker', 'the command line picks the chosen file up',
      () => el('command').textContent.includes(chosen), () => el('command').textContent);
  }

  /* -- running ------------------------------------------------------------ */

  /* The command with no options: it needs no valid form, and what it prints
   * is the release list, which the spec also carries -- so the output can be
   * checked against the server rather than against a pasted transcript. */
  const releases = parse.fields.find((field) => field.name === 'release').choices.filter(
    (name) => name !== 'auto');
  const echoed = `$ siemens-protocol-tool ${bare.name}`;

  await show(spec, bare.name);
  el('run').click();
  await check('run', 'Run disables itself and enables Stop while the command is live',
    () => el('run').disabled === true && el('stop').disabled === false,
    () => ({ run: el('run').disabled, stop: el('stop').disabled }));

  /* The one long wait: everything past here is about what the finished run
   * left on screen, and giving those checks the subprocess timeout too would
   * turn a page that never re-enables Run into minutes of waiting per check
   * instead of one. */
  await settle(() => el('run').disabled === false, RUN_MS);

  await check('run', 'the command line that ran is echoed into the output',
    () => el('log').textContent.includes(echoed), () => el('log').textContent.slice(0, 200));
  await check('run', "the command's own output arrives in the pane",
    () => releases.length > 0 && releases.every((name) => el('log').textContent.includes(name)),
    () => ({ wanted: releases, log: el('log').textContent.slice(0, 400) }));
  await check('run', 'the exit status is reported and Run comes back',
    () => el('log').textContent.includes('(exit status 0)')
      && el('status').textContent === 'Finished'
      && el('run').disabled === false && el('stop').disabled === true,
    () => ({ status: el('status').textContent, tail: el('log').textContent.slice(-200) }));

  const runsSoFar = el('log').textContent.split(echoed).length;
  fireDocument('keydown', { key: 'Enter', metaKey: true });
  await check('run', 'the keyboard shortcut starts a run as well',
    () => el('log').textContent.split(echoed).length === runsSoFar + 1,
    () => el('log').textContent.slice(-200));
  await settle(() => el('run').disabled === false, RUN_MS);

  el('clear').click();
  await check('run', 'Clear empties the output pane',
    () => el('log').textContent === '', () => el('log').textContent.slice(0, 100));

  /* -- the remaining controls --------------------------------------------- */

  el('copy').click();
  await check('controls', 'Copy puts the shown command line on the clipboard',
    () => clipboard === el('command').textContent && el('status').textContent.includes('copied'),
    () => ({ clipboard, shown: el('command').textContent, status: el('status').textContent }));

  await check('page', 'app.js asks only for element ids the page defines',
    () => missing.length === 0, () => missing);
  await check('page', 'nothing in app.js threw along the way',
    () => crashes.length === 0, () => crashes.slice(0, 3));

  /* Last, because it tells the server this session is over. */
  el('quit').click();
  await check('controls', 'Quit shuts the session down and says so',
    () => document.body.innerHTML.includes('shut down'),
    () => document.body.innerHTML.slice(0, 120));
}

try {
  await main();
  record('harness', 'the harness ran every check to the end', true, null);
} catch (error) {
  record('harness', 'the harness ran every check to the end', false,
    String((error && error.stack) || error));
}
