// Tafel: every chalk line on a page is drawn here, from plain HTML and SVG.
//
// Load it on every page that uses styles.css: <script type="module" src="/chalk.js">.
// It reads data attributes and a few kit classes (see KIT in styles.css):
//
//   .btn                         a hand-drawn box; .btn--solid a block of chalk
//   [data-chalk="underline"]     one fast stroke under the text (data-acc: accent,
//                                data-w: width, data-last: only the last line)
//   [data-chalk="box"]           a chalk box around the element (panels, menus)
//   [data-chalk="frame"]         a heavier box (screenshots)
//   [data-chalk="rule"]          a stroke along the element's bottom edge (<hr>, headings)
//   [data-chalk="grid"]          lines between the children of a grid (rows, columns)
//   [data-chalk="divider"]       a vertical stroke left of a column that sits beside another
//   [data-chalk="ticks"]         a tick before every <li>; [data-chalk="tick"] one tick
//   [data-chalk="route"]         an <ol> of .stop circles joined by one winding stroke
//   [data-chalk="arrow"]         an accent arrow from this note to data-to (a selector,
//                                looked up inside the nearest [data-chalk-host] or parent);
//                                data-ring="RXxRY" on the target adds an ellipse around it;
//                                data-place="after" puts the note right of its target
//   .field input / textarea      a chalk line under, a chalk box around
//   svg.drawing                  plain geometry roughened into chalk strokes; <text
//                                data-max="W"> shrinks to fit W user units, data-wrap
//                                allows two lines (translations)
//
// Strokes draw themselves once when they scroll into view; with reduced
// motion they are simply there. Without JS the CSS fallbacks show (borders,
// ✓, outlines). window.Chalk.draw(el) redraws a subtree that was hidden
// (menus, dialogs); the page redraws itself on width changes.

const NS = 'http://www.w3.org/2000/svg';
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const root = document.documentElement;

/* ---------- random ---------- */
function rng(seed) {
  let s = seed >>> 0;
  return () => { s = (s + 0x6D2B79F5) | 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
let seedN = 7;
const nextSeed = () => (seedN = (seedN * 48271) % 2147483647);

/* ---------- geometry ---------- */
const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
function polyLen(p) { let L = 0; for (let i = 1; i < p.length; i++) L += dist(p[i - 1], p[i]); return L; }
function subdivide(pts, step) {
  const out = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], n = Math.max(1, Math.round(dist(a, b) / step));
    for (let k = 1; k <= n; k++) out.push([a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n, k === n ? b[2] : 0]);
  }
  return out;
}
function cubic(p0, p1, p2, p3, n) {
  const out = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    out.push([u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0], u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]]);
  }
  return out;
}
// Wobble a polyline: a slow sideways drift plus a small hand tremor, ends nudged.
function wobble(pts, r, amp) {
  const L = polyLen(pts) || 1, f = Math.max(.6, L / 240), ph = r() * 6.28, ph2 = r() * 6.28;
  let acc = 0;
  return pts.map((p, i) => {
    if (i) acc += dist(pts[i - 1], p);
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
    let nx = -(b[1] - a[1]), ny = b[0] - a[0]; const nl = Math.hypot(nx, ny) || 1; nx /= nl; ny /= nl;
    const t = acc / L;
    const o = amp * (.65 * Math.sin(6.283 * f * t + ph) + .35 * Math.sin(6.283 * f * 2.7 * t + ph2)) + amp * .35 * (r() - .5);
    const e = (i === 0 || i === pts.length - 1) ? amp * .8 : 0;
    return [p[0] + nx * o + (r() - .5) * e, p[1] + ny * o + (r() - .5) * e, p[2]];
  });
}
// Catmull-Rom through the points; corners (p[2] === 2) stay sharp.
function toPath(p) {
  const f = (v) => v.toFixed(1);
  if (p.length < 3) return `M${f(p[0][0])} ${f(p[0][1])}L${f(p[1][0])} ${f(p[1][1])}`;
  let d = `M${f(p[0][0])} ${f(p[0][1])}`;
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[i - 1] || p[i], p1 = p[i], p2 = p[i + 1], p3 = p[i + 2] || p2;
    const k1 = p1[2] === 2 ? 0 : 1, k2 = p2[2] === 2 ? 0 : 1;
    d += `C${f(p1[0] + k1 * (p2[0] - p0[0]) / 6)} ${f(p1[1] + k1 * (p2[1] - p0[1]) / 6)} ${f(p2[0] - k2 * (p3[0] - p1[0]) / 6)} ${f(p2[1] - k2 * (p3[1] - p1[1]) / 6)} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d;
}

/* ---------- one chalk stroke = two passes + deposits ---------- */
function el(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
/**
 * A chalk stroke along a polyline (user units). Real corners carry p[2] = 2.
 * Options: w (width), amp (wobble), step (resample), cut (share of the second
 * pass), op (opacity), seed. Returns {g, len}; its paths are drawable (.rs).
 */
function stroke(parent, pts, o = {}) {
  const r = rng(o.seed ?? nextSeed());
  const w = o.w ?? 3, amp = o.amp ?? Math.min(2.2, w * .55), step = o.step ?? 9;
  const corners = pts.filter((p, i) => i > 0 && i < pts.length - 1 && p[2] === 2);
  const dense = subdivide(pts, step);
  const g = el('g', { class: 'stk' }, parent);
  const len = polyLen(dense);
  const p1 = wobble(dense, r, amp);
  el('path', { d: toPath(p1), class: 'rs', pathLength: 1, 'stroke-width': w.toFixed(2), opacity: o.op ?? .92, stroke: 'currentColor' }, g);
  // second, thinner pass: starts with the first, lifts off before the end
  const cut = Math.max(2, Math.round(dense.length * (o.cut ?? (.7 + r() * .25))));
  const p2 = wobble(dense.slice(0, cut), r, amp * 1.25).map((p) => [p[0] + (r() - .5) * w * .5, p[1] + (r() - .5) * w * .5, p[2]]);
  if (p2.length > 1) el('path', { d: toPath(p2), class: 'rs', pathLength: 1, 'stroke-width': (w * .58).toFixed(2), opacity: .5, stroke: 'currentColor' }, g);
  // heavier deposit where the chalk lands, and at each change of direction
  el('ellipse', { class: 'dep', cx: p1[0][0].toFixed(1), cy: p1[0][1].toFixed(1), rx: (w * .78).toFixed(2), ry: (w * .62).toFixed(2), fill: 'currentColor', opacity: .55 }, g);
  for (const c of corners) el('circle', { class: 'dep', cx: c[0], cy: c[1], r: (w * .55).toFixed(2), fill: 'currentColor', opacity: .38 }, g);
  return { g, len };
}
function arrowHead(parent, a, b, size, w, seed) {
  const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
  return [-1, 1].map((s) => {
    const t = ang + Math.PI + s * .48;
    return stroke(parent, [[b[0], b[1]], [b[0] + Math.cos(t) * size, b[1] + Math.sin(t) * size]], { w, amp: .5, seed: seed + s, cut: 1 });
  });
}
function loop(cx, cy, rx, ry, r, turns = 1.1) {
  const pts = [], a0 = -2.2 + r() * .6, n = 48;
  for (let i = 0; i <= n; i++) {
    const t = a0 + 6.283 * turns * i / n, k = 1 + .05 * (i / n - .5);
    pts.push([cx + Math.cos(t) * rx * k, cy + Math.sin(t) * ry * k]);
  }
  return pts;
}
// Four separate strokes that overshoot at the corners.
function boxStrokes(parent, x, y, w, h, o = {}) {
  const r = rng(o.seed ?? nextSeed()), ov = o.over ?? Math.min(9, Math.max(4, Math.min(w, h) * .12)), sw = o.w ?? 2.4;
  const j = () => (r() - .5) * (o.j ?? 2.2);
  const sides = [
    [[x - ov * r(), y + j()], [x + w + ov * (.4 + r() * .6), y + j()]],
    [[x + w + j(), y - ov * r()], [x + w + j(), y + h + ov * (.3 + r() * .7)]],
    [[x + w + ov * r(), y + h + j()], [x - ov * (.3 + r() * .7), y + h + j()]],
    [[x + j(), y + h + ov * r()], [x + j(), y - ov * (.3 + r() * .7)]],
  ];
  return sides.map((s) => stroke(parent, s, { w: sw, amp: o.amp ?? 1.1, seed: Math.floor(r() * 1e9) }));
}
// A block of chalk laid on with the side of the stick: solid, ragged edges.
function solidFill(svg, w, h) {
  const r = rng(nextSeed()), pts = [], j = (a) => (r() - .5) * a;
  for (let x = 0; x <= w; x += 7) pts.push([x, 1 + j(2.6)]);
  for (let y = 0; y <= h; y += 7) pts.push([w - 1 + j(2.6), y]);
  for (let x = w; x >= 0; x -= 7) pts.push([x, h - 1 + j(2.6)]);
  for (let y = h; y >= 0; y -= 7) pts.push([1 + j(2.6), y]);
  el('path', { d: 'M' + pts.map((p) => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('L') + 'Z', fill: 'currentColor', class: 'fill' }, svg);
  // a few lighter drag marks along the stroke direction, kept off the label
  for (let i = 0; i < 3; i++) {
    const y = i === 0 ? 3 + r() * 3 : i === 1 ? h - 4 - r() * 3 : 2 + r() * 2;
    el('path', { d: `M${(r() * w * .2).toFixed(1)} ${y.toFixed(1)}L${(w * (.6 + r() * .4)).toFixed(1)} ${(y + j(2)).toFixed(1)}`, stroke: '#1b1f22', 'stroke-width': 1.2, opacity: .25, fill: 'none' }, svg);
  }
}

/* ---------- drawings authored as plain SVG geometry ---------- */
let measure;
function pathPoints(src) {
  if (!measure) { measure = el('svg', { width: 0, height: 0, style: 'position:absolute;visibility:hidden;left:0;top:0', 'aria-hidden': 'true' }); document.body.appendChild(measure); }
  const p = el('path', { d: src.getAttribute('d') }, measure);
  const L = p.getTotalLength(), n = Math.max(4, Math.round(L / 10)), pts = [];
  for (let i = 0; i <= n; i++) { const q = p.getPointAtLength(L * i / n); pts.push([q.x, q.y]); }
  p.remove();
  return pts;
}
// Translations run long: shrink a label to its data-max width, or break it
// into two lines first when data-wrap allows (data-maxh caps their height).
function fitTexts(svg) {
  for (const t of svg.querySelectorAll('text[data-max]')) {
    const max = parseFloat(t.dataset.max);
    let len = t.getComputedTextLength();
    if (len <= max) continue;
    const words = t.textContent.trim().split(/\s+/);
    if (t.hasAttribute('data-wrap') && words.length > 1) {
      let best = null;
      for (let i = 1; i < words.length; i++) {
        const a = words.slice(0, i).join(' '), b = words.slice(i).join(' ');
        const m = Math.max(a.length, b.length);
        if (!best || m < best.m) best = { a, b, m };
      }
      const x = t.getAttribute('x');
      t.textContent = '';
      el('tspan', { x, dy: '-.5em' }, t).textContent = best.a;
      el('tspan', { x, dy: '1.05em' }, t).textContent = best.b;
      len = Math.max(...[...t.children].map((s) => s.getComputedTextLength()));
    }
    let fs = parseFloat(getComputedStyle(t).fontSize);
    if (len > max) fs *= max / len;
    // data-maxh: the height two wrapped lines may take (in viewBox units)
    if (t.children.length && t.dataset.maxh) fs = Math.min(fs, parseFloat(t.dataset.maxh) / 2.05);
    if (len > max || t.children.length) t.style.fontSize = fs.toFixed(1) + 'px';
  }
}
function roughenDrawing(svg) {
  const seq = [];
  let k = 0;
  for (const grp of svg.querySelectorAll(':scope > g')) {
    const out = el('g', { class: grp.getAttribute('class') + ' rough' });
    grp.after(out);
    for (const s of grp.children) {
      const tag = s.tagName.toLowerCase(), seed = 1000 + (k++) * 97;
      const sw = parseFloat(getComputedStyle(s).strokeWidth) || 3.2;
      if (tag === 'text') { const t = s.cloneNode(true); out.appendChild(t); seq.push({ text: t }); continue; }
      let pts, strokes = [];
      const num = (a) => parseFloat(s.getAttribute(a));
      if (tag === 'rect') strokes = boxStrokes(out, num('x'), num('y'), num('width'), num('height'), { w: sw, seed, amp: 1.6, over: 9, j: 3 });
      else if (tag === 'circle' || tag === 'ellipse') {
        const rx = tag === 'circle' ? num('r') : num('rx'), ry = tag === 'circle' ? num('r') : num('ry');
        strokes = [stroke(out, loop(num('cx'), num('cy'), rx, ry, rng(seed), tag === 'ellipse' ? 1.12 : 1.06), { w: sw, seed, amp: 1.4, step: 7 })];
      } else {
        if (tag === 'line') pts = [[num('x1'), num('y1')], [num('x2'), num('y2')]];
        else if (tag === 'polyline') pts = s.getAttribute('points').trim().split(/\s+/).map((q) => { const [x, y] = q.split(',').map(Number); return [x, y, 2]; });
        else if (tag === 'path') pts = pathPoints(s);
        else continue;
        strokes = [stroke(out, pts, { w: sw, seed, amp: 1.7, step: tag === 'path' ? 10 : 12 })];
        if (s.hasAttribute('data-arrow')) strokes.push(...arrowHead(out, pts[pts.length - 2], pts[pts.length - 1], 15, sw, seed + 5));
      }
      seq.push({ strokes });
    }
    grp.classList.add('src');
  }
  svg._seq = seq;
}

/* ---------- playing strokes in order ---------- */
function play(target, seq, speed = 1, maxT = 2.4) {
  // durations first, then squeeze the whole drawing into maxT seconds
  const items = [];
  let t = 0;
  for (const item of seq) {
    if (item.text) {
      const n = item.text.textContent.length, d = Math.min(.5, .1 + n * .02) / speed;
      items.push({ text: item.text, d, t }); t += d * .8; continue;
    }
    for (const s of item.strokes) {
      const d = Math.min(.55, Math.max(.1, s.len / 1300)) / speed;
      items.push({ s, d, t }); t += d * .75;
    }
  }
  const k = t > maxT ? maxT / t : 1;
  for (const it of items) {
    const d = (it.d * Math.max(k, .45)).toFixed(3), at = (it.t * k).toFixed(3);
    if (it.text) { it.text.style.transition = `clip-path ${d}s linear ${at}s`; continue; }
    for (const p of it.s.g.querySelectorAll('.rs')) p.style.transition = `stroke-dashoffset ${d}s cubic-bezier(.35,.1,.45,1) ${at}s`;
    for (const p of it.s.g.querySelectorAll('.dep')) p.style.transition = `opacity .15s ${at}s`;
  }
  requestAnimationFrame(() => requestAnimationFrame(() => target.classList.remove('pending')));
}
const io = 'IntersectionObserver' in window ? new IntersectionObserver((es) => {
  for (const e of es) if (e.isIntersecting) { io.unobserve(e.target); e.target._play?.(); }
}, { rootMargin: '0px 0px -12% 0px', threshold: .15 }) : null;
let animOn = true;   // only the first drawing of the page animates
function schedule(target, seq, speed) {
  if (reduce || !io || !animOn) return;
  target.classList.add('pending');
  target._play = () => play(target, seq, speed);
  io.observe(target);
}

/* ---------- overlays on HTML elements ---------- */
// An svg overlay inside host, padded by pad px. One per (host, kind).
function ov(host, kind, pad = 14, top = false) {
  host.querySelector(`:scope > .ch-ov[data-k="${kind}"]`)?.remove();
  host.classList.add('ch-host');
  if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
  const w = host.offsetWidth, h = host.offsetHeight;
  const svg = el('svg', { class: 'ch-ov' + (top ? ' ch-top' : ''), 'data-k': kind, width: w + 2 * pad, height: h + 2 * pad, viewBox: `${-pad} ${-pad} ${w + 2 * pad} ${h + 2 * pad}`, 'aria-hidden': 'true', focusable: 'false' });
  svg.style.left = svg.style.top = -pad + 'px';
  svg.style.color = 'var(--chalk)';
  host.appendChild(svg);
  return { svg, w, h };
}
const visible = (e) => e.offsetWidth > 0 && e.getClientRects().length > 0;
const all = (scope, sel) => [...(scope.matches?.(sel) ? [scope] : []), ...scope.querySelectorAll(sel)].filter(visible);

function drawUnderlines(scope) {
  for (const t of all(scope, '[data-chalk="underline"]')) {
    const host = t.parentElement, hb = host.getBoundingClientRect();
    const { svg } = ov(host, 'u', 24, true);
    if (t.hasAttribute('data-acc')) svg.style.color = 'var(--acc)';
    const w = parseFloat(t.dataset.w || 3.2), seq = [];
    const rg = document.createRange(); rg.selectNodeContents(t);
    let rects = [...rg.getClientRects()].filter((q) => q.width > 4);
    if (t.hasAttribute('data-last')) rects = rects.slice(-1);
    // merge rects of the same line (inline children)
    const lines = [];
    for (const q of rects) {
      const l = lines.find((L) => Math.abs(L.bottom - q.bottom) < 4);
      if (l) { l.left = Math.min(l.left, q.left); l.right = Math.max(l.right, q.right); } else lines.push({ left: q.left, right: q.right, bottom: q.bottom, height: q.height });
    }
    for (const rc of lines) {
      const x0 = rc.left - hb.left, x1 = rc.right - hb.left;
      const y = rc.bottom - hb.top - rc.height * (t.closest('blockquote') ? .12 : .2);
      const r = rng(nextSeed());
      const ext = Math.min(24, (x1 - x0) * .05);
      const pts = [[x0 - ext * r(), y + 2 + r() * 2], [Math.min(x1 + ext * (.6 + r()), hb.width + 8), y - 3 - r() * 4]];
      seq.push({ strokes: [stroke(svg, pts, { w, amp: 1.4, step: 14, cut: .55 })] });
    }
    schedule(svg, seq, 1.6);
  }
}
function drawButtons(scope) {
  for (const b of all(scope, '.btn')) {
    const { svg, w, h } = ov(b, 'b', 12);
    if (b.classList.contains('btn--solid')) { svg.classList.add('ch-solid'); solidFill(svg, w, h); }
    boxStrokes(svg, 1, 1, w - 2, h - 2, { w: b.classList.contains('btn--sm') ? 2 : 2.4, over: 6, j: 2.4 });
  }
}
function drawTicks(scope) {
  for (const li of all(scope, '[data-chalk="ticks"] > li')) {
    const { svg } = ov(li, 't', 10, true);
    const r = rng(nextSeed()), y = 13;
    const s = stroke(svg, [[6 + r() * 2, y + 1], [12, y + 8, 2], [27 + r() * 2, y - 9 - r() * 2]], { w: 2.6, amp: .5, step: 5 });
    schedule(svg, [{ strokes: [s] }], 2);
  }
  for (const sp of all(scope, '[data-chalk="tick"]')) {
    const { svg } = ov(sp, 't', 10, true);
    const s = stroke(svg, [[1, 9], [6, 15, 2], [16, 1]], { w: 2.2, amp: .4, step: 4 });
    schedule(svg, [{ strokes: [s] }], 2);
  }
}
function drawBoxes(scope) {
  for (const b of all(scope, '[data-chalk="box"]')) {
    const { svg, w, h } = ov(b, 'x', 16);
    if (b.hasAttribute('data-acc')) svg.style.color = 'var(--acc)';
    // a box that clips its content (the console) gets its strokes inside
    const i = getComputedStyle(b).overflow === 'hidden' ? 4 : 0;
    const ss = boxStrokes(svg, i, i, w - 2 * i, h - 2 * i, { w: 2.6, over: i ? 0 : Math.min(14, Math.min(w, h) * .1), j: Math.min(5, h * .03 + 1) });
    schedule(svg, [{ strokes: ss }], 1.4);
  }
  for (const f of all(scope, '[data-chalk="frame"]')) {
    const { svg, w, h } = ov(f, 'f', 18);
    const ss = boxStrokes(svg, 2, 2, w - 4, h - 4, { w: 2.8, over: 16, j: 4 });
    schedule(svg, [{ strokes: ss }], 1.6);
  }
}
function drawRules(scope) {
  for (const e of all(scope, '[data-chalk="rule"]')) {
    const { svg, w, h } = ov(e, 'r', 10, true);
    const r = rng(nextSeed());
    const s = stroke(svg, [[-2, h + 1 + r() * 2], [w + 4, h - 1 - r() * 2]], { w: 2.4, amp: 1.2, step: 16, cut: .5, op: .8 });
    schedule(svg, [{ strokes: [s] }], 1.6);
  }
}
function drawGrids(scope) {
  for (const ul of all(scope, '[data-chalk="grid"]')) {
    const items = [...ul.children].filter(visible);
    if (!items.length) continue;
    const { svg } = ov(ul, 'g', 20), ub = ul.getBoundingClientRect(), seq = [];
    const tops = [...new Set(items.map((li) => Math.round(li.getBoundingClientRect().top - ub.top)))].sort((a, b) => a - b);
    const lefts = [...new Set(items.map((li) => Math.round(li.getBoundingClientRect().left - ub.left)))].sort((a, b) => a - b);
    for (const y of tops) seq.push({ strokes: [stroke(svg, [[-6, y + 2], [ub.width + 4, y - 2]], { w: 2.2, amp: 2, step: 18, op: .62 })] });
    for (const x of lefts.slice(1)) seq.push({ strokes: [stroke(svg, [[x + 1, tops[0] + 22], [x - 2, ub.height - 18]], { w: 2.2, amp: 2, step: 18, op: .55 })] });
    schedule(svg, seq, 1.2);
  }
}
function drawDividers(scope) {
  for (const col of all(scope, '[data-chalk="divider"]')) {
    const parent = col.parentElement, pb = parent.getBoundingClientRect(), cb = col.getBoundingClientRect();
    parent.querySelector(':scope > .ch-ov[data-k="d"]')?.remove();
    if (cb.left - pb.left < 80) continue;            // stacked: no divider
    const { svg } = ov(parent, 'd', 20);
    const prev = col.previousElementSibling?.getBoundingClientRect();
    const gap = prev ? cb.left - prev.right : 60;
    const x = cb.left - pb.left - gap / 2;
    schedule(svg, [{ strokes: [stroke(svg, [[x, 12], [x - 3, pb.height - 14]], { w: 2.2, amp: 1.7, step: 16, op: .5, cut: .4 })] }], 1);
  }
}
function drawRoutes(scope) {
  for (const ol of all(scope, '[data-chalk="route"]')) {
    const stops = [...ol.querySelectorAll('.stop')].filter(visible);
    if (!stops.length) continue;
    const { svg } = ov(ol, 'p', 30), ob = ol.getBoundingClientRect(), seq = [];
    const cs = stops.map((s) => { const b = s.getBoundingClientRect(); return [b.left - ob.left + b.width / 2, b.top - ob.top + b.height / 2, b.width / 2]; });
    cs.forEach((c, i) => {
      const r = rng(nextSeed());
      const ring = stroke(svg, loop(c[0], c[1], c[2] + 1, c[2] - 1, r, 1.1), { w: 2.6, amp: 1, step: 6 });
      const dot = stroke(svg, loop(c[0], c[1], 3.5, 3, r, 1.6), { w: 3.6, amp: .3, step: 3 });
      seq.push({ strokes: [ring, dot] });
      const n = cs[i + 1];
      if (n) {
        const a = [c[0], c[1] + c[2] + 7], b = [n[0], n[1] - n[2] - 9], dy = b[1] - a[1];
        const pts = cubic(a, [a[0] + (r() - .5) * 20, a[1] + dy * .55], [b[0] + (r() - .5) * 20, b[1] - dy * .55], b, 18);
        seq.push({ strokes: [stroke(svg, pts, { w: 2.6, amp: 1.3, step: 9 }), ...arrowHead(svg, pts[pts.length - 3], b, 10, 2.4, nextSeed())] });
      }
    });
    schedule(svg, seq, 1.3);
  }
}
// Margin notes with arrows. A note with data-place="after" is first put to
// the right of its target, and hidden when there is no room for it.
function placeNotes(scope) {
  for (const note of [...(scope.matches?.('[data-place]') ? [scope] : []), ...scope.querySelectorAll('[data-chalk="arrow"][data-place="after"]')]) {
    const host = note.closest('[data-chalk-host]') || note.parentElement, target = host.querySelector(note.dataset.to);
    note.style.display = ''; note.style.width = ''; note.style.whiteSpace = '';
    if (!target || !visible(target)) { note.style.display = 'none'; continue; }
    const hb = host.getBoundingClientRect(), tb = target.getBoundingClientRect();
    const left = tb.right - hb.left + (parseFloat(note.dataset.gap) || 80), top = tb.top - hb.top - 18;
    note.style.left = left + 'px'; note.style.top = top + 'px';
    // room up to the host's right edge (plus a little of the gap beside it);
    // a long translation wraps onto two lines, and with no room at all the
    // note stays out
    const room = hb.width + (parseFloat(note.dataset.spill) || 24) - left;
    if (note.offsetWidth > room) {
      if (room < 150) { note.style.display = 'none'; continue; }
      note.style.whiteSpace = 'normal'; note.style.width = room + 'px';
    }
  }
}
function drawArrows(scope) {
  const hosts = new Set(all(scope, '[data-chalk="arrow"]').map((n) => n.closest('[data-chalk-host]') || n.parentElement));
  for (const host of hosts) {
    const notes = [...host.querySelectorAll('[data-chalk="arrow"]')].filter(visible);
    if (!notes.length) { host.querySelector(':scope > .ch-ov[data-k="a"]')?.remove(); continue; }
    const { svg } = ov(host, 'a', 0, true), hb = host.getBoundingClientRect(), seq = [];
    svg.style.color = 'var(--acc)';
    for (const note of notes) {
      const target = host.querySelector(note.dataset.to);
      if (!target) continue;
      const nb = note.getBoundingClientRect(), tb = target.getBoundingClientRect();
      const tc = [tb.left - hb.left + tb.width / 2, tb.top - hb.top + tb.height / 2];
      const r = rng(nextSeed()), strokes = [];
      // optional ring around the target, the arrow ends on its edge
      let rx = tb.width / 2, ry = tb.height / 2;
      if (target.dataset.ring) {
        // "RXxRY" in px, or in % of the box the pin sits in
        const box = target.offsetParent || host;
        [rx, ry] = target.dataset.ring.split('x').map((v, i) => (v.endsWith('%') ? parseFloat(v) / 100 * (i ? box.offsetHeight : box.offsetWidth) : Number(v)));
        strokes.push(stroke(svg, loop(tc[0], tc[1], rx, ry, r, 1.12), { w: 2.4, amp: .9, step: 7 }));
        rx += 6; ry += 6;
      }
      // start on the side of the note that faces the target
      const nc = [nb.left - hb.left + nb.width / 2, nb.top - hb.top + nb.height / 2];
      const dx = tc[0] - nc[0], dy = tc[1] - nc[1];
      let a, ca;
      if (Math.abs(dx) * nb.height > Math.abs(dy) * nb.width) { a = [dx < 0 ? nb.left - hb.left - 8 : nb.right - hb.left + 8, nc[1]]; ca = [Math.sign(dx), 0]; }
      else { a = [nc[0] + (dx > 0 ? 1 : -1) * Math.min(nb.width * .25, Math.abs(dx)), dy < 0 ? nb.top - hb.top - 6 : nb.bottom - hb.top + 6]; ca = [0, Math.sign(dy)]; }
      // end on the target's edge (ellipse or box), facing the start
      const ang = Math.atan2(a[1] - tc[1], a[0] - tc[0]);
      const b = [tc[0] + Math.cos(ang) * (rx + 4), tc[1] + Math.sin(ang) * (ry + 4)];
      const L = dist(a, b), k = Math.min(70, L * .45);
      const pts = cubic(a, [a[0] + ca[0] * k, a[1] + ca[1] * k], [b[0] + Math.cos(ang) * k * .7, b[1] + Math.sin(ang) * k * .7], b, 16);
      strokes.push(stroke(svg, pts, { w: 2.4, amp: .8, step: 8 }), ...arrowHead(svg, pts[pts.length - 3], b, 11, 2.2, nextSeed()));
      seq.push({ strokes });
    }
    schedule(svg, seq, 1.2);
  }
}
function drawFields(scope) {
  for (const f of all(scope, '.field input:not([type="checkbox"]):not([type="radio"]), .field select')) {
    const host = f.parentElement, hb = host.getBoundingClientRect(), fb = f.getBoundingClientRect();
    const { svg } = ov(host, 'i', 8); svg.style.opacity = .7;
    const y = fb.bottom - hb.top - 1, r = rng(nextSeed());
    stroke(svg, [[fb.left - hb.left - 2, y + r()], [fb.right - hb.left + 2, y - r() * 2]], { w: 2.2, amp: .9, step: 14, cut: .5 });
  }
  for (const t of all(scope, '.field textarea')) {
    const host = t.parentElement, hb = host.getBoundingClientRect(), tb = t.getBoundingClientRect();
    const { svg } = ov(host, 'i', 12); svg.style.opacity = .7;
    boxStrokes(svg, tb.left - hb.left, tb.top - hb.top, tb.width, tb.height, { w: 2.2, over: 7, j: 2.4 });
  }
}

function drawAll(scope = document) {
  if (scope === document) seedN = 7;
  const s = scope === document ? document.body : scope;
  drawUnderlines(s); drawButtons(s); drawTicks(s); drawBoxes(s); drawRules(s); drawGrids(s);
  drawDividers(s); drawRoutes(s); placeNotes(s); drawArrows(s); drawFields(s);
  sizeLayer();
}

/* ---------- hover strokes and focus rings (page layer) ---------- */
let layer;
function sizeLayer() {
  if (!layer) { layer = el('svg', { class: 'ch-layer', 'aria-hidden': 'true', focusable: 'false' }); document.body.appendChild(layer); }
  const W = root.clientWidth, H = root.scrollHeight;
  layer.setAttribute('width', W); layer.setAttribute('height', H); layer.setAttribute('viewBox', `0 0 ${W} ${H}`);
}
function underlineLink(a) {
  const g = el('g', { class: 'hov' }, layer), seq = [];
  g.style.color = 'var(--acc)';
  for (const rc of a.getClientRects()) {
    if (rc.width < 2) continue;
    const y = rc.bottom + scrollY - rc.height * .04, x0 = rc.left + scrollX, x1 = rc.right + scrollX, r = rng(nextSeed());
    seq.push({ strokes: [stroke(g, [[x0 - 2, y + 1 + r() * 1.5], [x1 + 3, y - 1 - r() * 2]], { w: 2.4, amp: .9, step: 14, cut: .6 })] });
  }
  if (!reduce) { g.classList.add('pending'); play(g, seq, 3); }
  return g;
}
function focusBox(t) {
  const rc = t.getBoundingClientRect(), pad = 6;
  const g = el('g', { class: 'focus' }, layer);
  g.style.color = 'var(--acc)';
  boxStrokes(g, rc.left + scrollX - pad, rc.top + scrollY - pad, rc.width + 2 * pad, rc.height + 2 * pad, { w: 2.4, over: 7, j: 2.6 });
  return g;
}
// Things that are fixed or modal keep the CSS ring and plain hovers.
const LAYERLESS = 'dialog, .con, .con-tab, .site-nav.open, .lang[open]';
function wireLayer() {
  const links = 'a:not(.btn):not(.shot-link), .link-btn, summary';
  document.addEventListener('pointerover', (e) => {
    const a = e.target.closest?.(links); if (!a || a._u || a.closest(LAYERLESS)) return;
    a.classList.add('ch-uline'); a._u = underlineLink(a);
  });
  document.addEventListener('pointerout', (e) => {
    const a = e.target.closest?.(links); if (!a || a.contains(e.relatedTarget)) return;
    a._u?.remove(); a._u = null;
  });
  let ring;
  document.addEventListener('focusin', (e) => {
    ring?.remove(); ring = null;
    const t = e.target;
    if (t.closest(LAYERLESS) || t === document.getElementById('main')) return;
    let fv = false; try { fv = t.matches(':focus-visible'); } catch { fv = true; }
    if (fv) ring = focusBox(t);
  });
  document.addEventListener('focusout', () => { ring?.remove(); ring = null; });
}
// Hover redraws a second, fresh box over a button: the line thickens.
function wireButtonHover() {
  const add = (b) => {
    const svg = b.querySelector(':scope > .ch-ov[data-k="b"]');
    if (b._hov || !svg) return;
    const g = el('g', { class: 'hov' }, svg);
    const ss = boxStrokes(g, 0, 0, b.offsetWidth, b.offsetHeight, { w: 2.2, over: 8, j: 3.4 });
    b._hov = g;
    if (!reduce) { g.classList.add('pending'); play(g, [{ strokes: ss }], 2.4); }
  };
  const rm = (b) => { b._hov?.remove(); b._hov = null; };
  document.addEventListener('pointerover', (e) => { const b = e.target.closest?.('.btn'); if (b && !b.disabled) add(b); });
  document.addEventListener('pointerout', (e) => { const b = e.target.closest?.('.btn'); if (b && !b.contains(e.relatedTarget)) rm(b); });
}

/* ---------- start ---------- */
window.Chalk = { draw: (scope) => { drawAll(scope || document); }, stroke, boxStrokes };
await document.fonts.ready;
for (const svg of document.querySelectorAll('svg.drawing')) {
  fitTexts(svg); roughenDrawing(svg); schedule(svg, svg._seq, 1);
}
drawAll();
animOn = false;
root.classList.add('chalk-ready');
wireButtonHover(); wireLayer();
// Things that open later: draw them when they appear.
document.addEventListener('toggle', (e) => { if (e.target.matches?.('details')) requestAnimationFrame(() => drawAll()); }, true);
// Boxes that change size after they were drawn (the console's resize grip,
// a late image): redraw that part when its size really changes.
if ('ResizeObserver' in window) {
  const sizes = new WeakMap();
  const ro = new ResizeObserver((es) => {
    for (const e of es) {
      const t = e.target, w = t.offsetWidth, h = t.offsetHeight, old = sizes.get(t);
      sizes.set(t, [w, h]);
      if (!old || (Math.abs(old[0] - w) < 2 && Math.abs(old[1] - h) < 2)) continue;
      clearTimeout(t._rt); t._rt = setTimeout(() => drawAll(t), 120);
    }
  });
  for (const w of document.querySelectorAll('[data-chalk-watch], [data-chalk="frame"], [data-chalk="grid"], [data-chalk="box"]')) ro.observe(w);
}
let lastW = innerWidth, tmr;
addEventListener('resize', () => {
  clearTimeout(tmr);
  tmr = setTimeout(() => { if (innerWidth !== lastW) { lastW = innerWidth; drawAll(); } else sizeLayer(); }, 160);
});
// Late layout shifts (status values arriving, images without size) settle
// once more after load.
addEventListener('load', () => setTimeout(() => drawAll(), 50));
document.addEventListener('site:status', () => requestAnimationFrame(() => drawAll()), { once: true });
