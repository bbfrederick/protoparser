/* A minimal DOM, enough to execute the GUI's app.js under node:vm.
 *
 * This is deliberately not a browser. It implements only what app.js touches,
 * and it takes its element map from the real index.html rather than from a
 * list written out here -- so renaming an id in the markup without renaming it
 * in the script is a failing check rather than a page that quietly does
 * nothing. For the same reason getElementById records the ids it could not
 * find instead of returning null and letting the failure surface later as a
 * TypeError inside some callback.
 */

import fs from 'node:fs';

/* Attributes that are present-or-absent in HTML but boolean in the DOM. */
const BOOLEAN_ATTRIBUTES = ['checked', 'disabled', 'open'];

export class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.style = {};
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.open = false;
    this._text = '';
  }

  get children() {
    return this.childNodes.filter((node) => node instanceof El);
  }

  appendChild(node) {
    this.childNodes.push(node);
    node.parentNode = this;
    return node;
  }

  append(...nodes) {
    for (const node of nodes) this.appendChild(node);
  }

  setAttribute(key, value) {
    this.attributes[key] = String(value);
    if (key === 'id') this.id = String(value);
  }

  getAttribute(key) {
    return this.attributes[key];
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }

  /* Deliver an event. The listener list is copied first: a handler that
   * re-renders can replace this element's listeners while we iterate. */
  fire(type, event = {}) {
    for (const handler of [...(this.listeners[type] || [])]) {
      handler({ preventDefault() {}, stopPropagation() {}, ...event });
    }
  }

  click() {
    this.fire('click');
  }

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
  }

  /* Assigning textContent replaces every child, which is how app.js empties a
   * container before rebuilding it; reading it walks the subtree. */
  set textContent(value) {
    this._text = String(value);
    this.childNodes = [];
  }

  get textContent() {
    return this._text + this.childNodes.map((node) => node.textContent).join('');
  }

  set innerHTML(value) {
    this._text = String(value);
    this.childNodes = [];
  }

  get innerHTML() {
    return this._text;
  }

  set className(value) {
    this.attributes.class = String(value);
  }

  get className() {
    return this.attributes.class || '';
  }

  /* Every node in this subtree, including this one, satisfying a predicate. */
  all(predicate, found = []) {
    if (predicate(this)) found.push(this);
    for (const child of this.children) child.all(predicate, found);
    return found;
  }

  first(predicate) {
    return this.all(predicate)[0] || null;
  }
}

/* Build the id map from the page's own markup.
 *
 * A regex rather than a parser because the page is fixed, small and hand
 * written: every tag it contains is well formed and no attribute value holds
 * a '>'. Boolean attributes are honoured because two of them are load-bearing
 * -- Follow starts checked and Stop starts disabled, and a shim that ignored
 * them would let a broken enable/disable cycle pass.
 */
export function seed(indexHtmlPath) {
  const html = fs.readFileSync(indexHtmlPath, 'utf8');
  const byId = new Map();
  for (const [, tag, attributes] of html.matchAll(/<([A-Za-z][\w-]*)([^>]*)>/g)) {
    const identifier = /\bid="([^"]*)"/.exec(attributes);
    if (!identifier) continue;
    const node = new El(tag);
    node.id = identifier[1];
    for (const [, key, value] of attributes.matchAll(/\b([A-Za-z][\w-]*)(?:="([^"]*)")?/g)) {
      node.attributes[key] = value ?? '';
    }
    for (const key of BOOLEAN_ATTRIBUTES) node[key] = key in node.attributes;
    if (node.attributes.value !== undefined) node.value = node.attributes.value;
    byId.set(node.id, node);
  }
  return byId;
}

/* A document backed by that map.
 *
 * `missing` accumulates every id looked up that the markup does not define,
 * and `fireDocument` delivers the page-level events app.js binds on the
 * document itself -- which is where the keyboard shortcut lives.
 */
export function makeDocument(byId) {
  const missing = [];
  const listeners = {};
  const body = new El('body');
  const document = {
    body,
    getElementById(id) {
      const node = byId.get(id);
      if (!node && !missing.includes(id)) missing.push(id);
      return node ?? null;
    },
    createElement: (tag) => new El(tag),
    createRange: () => ({ selectNodeContents() {} }),
    addEventListener(type, handler) {
      (listeners[type] ||= []).push(handler);
    },
  };
  const fireDocument = (type, event = {}) => {
    for (const handler of [...(listeners[type] || [])]) {
      handler({ preventDefault() {}, stopPropagation() {}, ...event });
    }
  };
  return { document, missing, fireDocument };
}
