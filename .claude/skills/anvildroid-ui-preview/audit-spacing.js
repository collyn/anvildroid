// Paste as the `function` of Chrome DevTools MCP evaluate_script (or run in the
// console) on a preview page. It checks the page that is showing now:
//  - every visible .btn has >= 8px between it and the nearest thing above it
//  - buttons sharing a row share the same top (no 1-2px steps)
//  - items of a horizontal flex row (text, tags, buttons) share one centre line
// Returns [] when clean. Zero-size elements and closed <details> content are skipped.
() => {
  const scope = document.querySelector('.page.active') || document.body;
  // Chrome still reports boxes for content of a closed <details>; treat it as hidden.
  const inClosedDetails = el => {
    for (let d = el.closest('details'); d; d = d.parentElement && d.parentElement.closest('details'))
      if (!d.open && el.closest('summary')?.parentElement !== d) return true;
    return false;
  };
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !inClosedDetails(el); };
  const label = el => (el.textContent.trim() || el.title || el.className).slice(0, 24);
  const blocks = [...scope.querySelectorAll('*')].filter(el => visible(el) &&
    (el.children.length === 0 || /^(INPUT|SELECT|BUTTON|LABEL|P|TEXTAREA)$/.test(el.tagName)));
  const buttons = [...scope.querySelectorAll('button.btn')].filter(el => visible(el) && !el.closest('summary, .settings-tabs-nav'));
  const problems = [];
  for (const button of buttons) {
    const r = button.getBoundingClientRect();
    let nearest = null;
    for (const el of blocks) {
      if (el === button || button.contains(el) || el.contains(button)) continue;
      const q = el.getBoundingClientRect();
      if (q.bottom <= r.top + 1 && q.right > r.left && q.left < r.right) {
        const gap = r.top - q.bottom;
        if (!nearest || gap < nearest.gap) nearest = {gap, el};
      }
    }
    if (nearest && nearest.gap < 8) problems.push(`tight: "${label(button)}" ${Math.round(nearest.gap)}px under "${label(nearest.el)}"`);
  }
  for (let i = 0; i < buttons.length; i++) for (let j = i + 1; j < buttons.length; j++) {
    const a = buttons[i].getBoundingClientRect(), b = buttons[j].getBoundingClientRect();
    if (a.top < b.bottom && b.top < a.bottom && Math.abs(a.top - b.top) > 2)
      problems.push(`misaligned: "${label(buttons[i])}" vs "${label(buttons[j])}" (${Math.round(b.top - a.top)}px)`);
  }
  // Horizontal rows: children that overlap vertically should share a centre line.
  for (const row of scope.querySelectorAll('*')) {
    const cs = getComputedStyle(row);
    if (!visible(row) || !/flex/.test(cs.display) || cs.flexDirection !== 'row' || /baseline|start|end|stretch/.test(cs.alignItems) && cs.alignItems !== 'center' && cs.alignItems !== 'normal') continue;
    const kids = [...row.children].filter(visible);
    for (let i = 1; i < kids.length; i++) {
      const a = kids[i - 1].getBoundingClientRect(), b = kids[i].getBoundingClientRect();
      if (a.top < b.bottom && b.top < a.bottom) {
        const d = Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2);
        if (d > 3) problems.push(`row off-centre: "${label(kids[i - 1])}" vs "${label(kids[i])}" (${Math.round(d)}px) in .${String(row.className).split(' ')[0]}`);
      }
    }
  }
  return problems;
}
