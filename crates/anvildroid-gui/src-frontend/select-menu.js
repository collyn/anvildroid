/* Themed dropdowns for every <select>.
 *
 * WebKitGTK draws a native <select> popup with GTK, which ignores page CSS
 * and the app's light/dark theme. This module keeps each <select> as the
 * source of truth (value, disabled state, focus, `change` events), and only
 * replaces its popup with a themed listbox. Pages need no changes: selects
 * created later are handled through event delegation.
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnvilSelectMenu = api;
})(typeof window !== 'undefined' ? window : globalThis, root => {
  'use strict';

  /** Items shown in the menu, in document order. Hidden options are skipped. */
  function itemsFor(select) {
    const items = [];
    const add = (option, group) => {
      if (option.hidden) return;
      items.push({kind: 'option', option, label: option.label || option.textContent, disabled: option.disabled || Boolean(group?.disabled), grouped: Boolean(group)});
    };
    for (const child of select.children) {
      if (child.tagName === 'OPTGROUP') {
        if (child.hidden) continue;
        items.push({kind: 'group', label: child.label});
        for (const option of child.children) if (option.tagName === 'OPTION') add(option, child);
      } else if (child.tagName === 'OPTION') add(child, null);
    }
    return items;
  }

  /** Next enabled option index from `from` in direction `step`, or `from`. */
  function nextEnabled(items, from, step) {
    for (let index = from + step; index >= 0 && index < items.length; index += step) {
      if (items[index].kind === 'option' && !items[index].disabled) return index;
    }
    return from;
  }

  function create(doc = root.document) {
    let open = null; // {select, menu, items, active, frame}

    function eligible(select) {
      return select && select.tagName === 'SELECT' && !select.multiple && !(select.size > 1) && !select.disabled;
    }

    function close(restoreFocus = false) {
      if (!open) return;
      const {select, menu, frame} = open;
      open = null;
      root.cancelAnimationFrame?.(frame);
      menu.remove();
      select.removeAttribute('aria-expanded');
      if (restoreFocus && select.isConnected) select.focus({preventScroll: true});
    }

    function choose(index) {
      if (!open) return;
      const item = open.items[index];
      if (!item || item.kind !== 'option' || item.disabled) return;
      const {select} = open;
      const changed = !item.option.selected;
      close(true);
      if (!changed) return;
      item.option.selected = true;
      select.dispatchEvent(new Event('input', {bubbles: true}));
      select.dispatchEvent(new Event('change', {bubbles: true}));
    }

    function setActive(index, reveal = true) {
      if (!open) return;
      open.active = index;
      for (const node of open.menu.querySelectorAll('.select-menu-option')) {
        const on = Number(node.dataset.index) === index;
        node.classList.toggle('active', on);
        if (on && reveal) node.scrollIntoView?.({block: 'nearest'});
      }
    }

    function place(select, menu) {
      const rect = select.getBoundingClientRect();
      const viewport = {width: root.innerWidth || doc.documentElement.clientWidth, height: root.innerHeight || doc.documentElement.clientHeight};
      const gap = 4, margin = 8;
      menu.style.minWidth = `${Math.max(rect.width, 160)}px`;
      menu.style.maxWidth = `${Math.max(240, viewport.width - 2 * margin)}px`;
      const below = viewport.height - rect.bottom - gap - margin;
      const above = rect.top - gap - margin;
      const natural = Math.min(menu.scrollHeight, 360);
      const upward = below < Math.min(natural, 200) && above > below;
      const room = Math.max(120, upward ? above : below);
      menu.style.maxHeight = `${Math.min(360, room)}px`;
      const height = Math.min(natural, room);
      menu.style.top = `${upward ? rect.top - gap - height : rect.bottom + gap}px`;
      const width = menu.offsetWidth || rect.width;
      menu.style.left = `${Math.max(margin, Math.min(rect.left, viewport.width - width - margin))}px`;
    }

    function show(select) {
      close();
      const items = itemsFor(select);
      if (!items.some(item => item.kind === 'option')) return;
      const menu = doc.createElement('div');
      menu.className = 'select-menu';
      menu.setAttribute('role', 'listbox');
      menu.setAttribute('aria-label', select.getAttribute('aria-label') || select.closest('label')?.textContent?.trim() || 'Options');
      let selectedIndex = -1;
      items.forEach((item, index) => {
        const node = doc.createElement('div');
        node.textContent = item.label;
        if (item.kind === 'group') {
          node.className = 'select-menu-group';
          node.setAttribute('role', 'presentation');
        } else {
          node.className = 'select-menu-option' + (item.grouped ? ' grouped' : '');
          node.setAttribute('role', 'option');
          node.dataset.index = String(index);
          node.setAttribute('aria-selected', String(item.option.selected));
          if (item.option.selected) { node.classList.add('selected'); selectedIndex = index; }
          if (item.disabled) { node.classList.add('disabled'); node.setAttribute('aria-disabled', 'true'); }
        }
        menu.append(node);
      });
      // Inside a modal <dialog>, only its top layer is above the page.
      (select.closest('dialog[open]') || doc.body).append(menu);
      const first = nextEnabled(items, -1, 1);
      open = {select, menu, items, active: selectedIndex >= 0 ? selectedIndex : first, frame: 0};
      select.setAttribute('aria-expanded', 'true');
      place(select, menu);
      setActive(open.active);
      // A re-render may replace the <select>; never leave a stale menu behind.
      const watch = () => {
        if (!open || open.select !== select) return;
        if (!select.isConnected || select.disabled) return close();
        open.frame = root.requestAnimationFrame?.(watch) || 0;
      };
      open.frame = root.requestAnimationFrame?.(watch) || 0;
      menu.addEventListener('mousedown', event => event.preventDefault()); // keep focus on the select
      menu.addEventListener('click', event => {
        const node = event.target.closest?.('.select-menu-option');
        if (node && !node.classList.contains('disabled')) choose(Number(node.dataset.index));
      });
      menu.addEventListener('mousemove', event => {
        const node = event.target.closest?.('.select-menu-option');
        if (node && !node.classList.contains('disabled') && Number(node.dataset.index) !== open?.active) setActive(Number(node.dataset.index), false);
      });
    }

    function onMouseDown(event) {
      if (open && !open.menu.contains(event.target) && event.target !== open.select) close();
      // Chips wrap their select in a label; the icon opens the menu too.
      const select = event.target.closest?.('select') || event.target.closest?.('.runtime-quick-chip')?.querySelector('select');
      if (event.button !== 0 || !eligible(select)) return;
      event.preventDefault(); // suppress the GTK popup
      if (open?.select === select) return close(true);
      select.focus({preventScroll: true});
      show(select);
    }

    function onKeyDown(event) {
      const select = event.target;
      if (open && open.select === select) {
        const {items, active} = open;
        const keys = {
          ArrowDown: () => setActive(nextEnabled(items, active, 1)),
          ArrowUp: () => setActive(nextEnabled(items, active, -1)),
          Home: () => setActive(nextEnabled(items, -1, 1)),
          End: () => setActive(nextEnabled(items, items.length, -1)),
          PageDown: () => { let index = active; for (let i = 0; i < 8; i++) index = nextEnabled(items, index, 1); setActive(index); },
          PageUp: () => { let index = active; for (let i = 0; i < 8; i++) index = nextEnabled(items, index, -1); setActive(index); },
          Enter: () => choose(active),
          ' ': () => choose(active),
          Escape: () => close(true),
          Tab: () => { close(); return true; },
        };
        if (keys[event.key]) {
          if (keys[event.key]() !== true) { event.preventDefault(); event.stopPropagation(); }
          return;
        }
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
          const letter = event.key.toLowerCase();
          const order = items.map((_, i) => (active + 1 + i) % items.length);
          const match = order.find(i => items[i].kind === 'option' && !items[i].disabled && String(items[i].label).trim().toLowerCase().startsWith(letter));
          if (match !== undefined) setActive(match);
          event.preventDefault();
        }
        return;
      }
      if (!eligible(select)) return;
      if (event.key === ' ' || event.key === 'Enter' || event.key === 'F4' || (event.altKey && (event.key === 'ArrowDown' || event.key === 'ArrowUp'))) {
        event.preventDefault();
        show(select);
      }
    }

    function onFocusOut(event) {
      if (open && event.target === open.select) close();
    }
    function onViewportChange(event) {
      if (open && !(event?.type === 'scroll' && open.menu.contains(event.target))) close();
    }

    doc.addEventListener('mousedown', onMouseDown, true);
    doc.addEventListener('keydown', onKeyDown, true);
    doc.addEventListener('focusout', onFocusOut, true);
    root.addEventListener?.('resize', onViewportChange);
    doc.addEventListener('scroll', onViewportChange, true);
    root.addEventListener?.('blur', () => close());
    return {close, isOpen: () => Boolean(open), get state() { return open; }};
  }

  const api = {create, itemsFor, nextEnabled};
  if (root.document && !root.AnvilSelectMenuInstance) {
    const start = () => { root.AnvilSelectMenuInstance = create(root.document); };
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', start, {once: true});
    else start();
  }
  return api;
});
