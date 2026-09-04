/* ============================================================
   КОСМОБЕРЕГ — прототип MVP
   Сквозной сценарий S1 → S6 с реальным пересчётом индекса внимания.
   Локализация: ru / en / zh (см. i18n.js)

   ВАЖНО: снимки ДЗЗ генерируются процедурно (демо-данные).
   В боевой системе на их месте — тайлы COG из конвейера E (см. архитектуру).
   ============================================================ */

/* ---------- 1. Детерминированный генератор «снимков» ---------- */

function rng(seed) {
  let t = seed >>> 0;
  return () => {
    t = (t + 0x6D2B79F5) >>> 0;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

const W = 400, H = 260;

/* Снимок берега: вода / песок / растительность + опциональная аномалия */
function tileSVG(seed, opts = {}) {
  const r = rng(seed);
  const { anomaly = null, dim = false } = opts;
  let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;

  s += `<defs>
    <linearGradient id="w${seed}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#123a5e"/><stop offset="1" stop-color="#1c5c86"/>
    </linearGradient>
    <linearGradient id="g${seed}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2f4a2b"/><stop offset="1" stop-color="#1d3320"/>
    </linearGradient>
  </defs>`;
  s += `<rect width="${W}" height="${H}" fill="url(#w${seed})"/>`;

  const pts = [];
  for (let i = 0; i <= 8; i++) {
    pts.push([i * (W / 8), 70 + Math.sin(i * 0.9 + seed) * 18 + r() * 16]);
  }
  const line = pts.map(p => p.join(',')).join(' ');

  s += `<polygon points="0,${H} ${line} ${W},${H}" fill="#c2a878"/>`;
  const veg = pts.map(p => [p[0], p[1] + 42 + r() * 10].join(',')).join(' ');
  s += `<polygon points="0,${H} ${veg} ${W},${H}" fill="url(#g${seed})"/>`;

  for (let i = 0; i < 26; i++) {
    const x = r() * W, y = r() * 60, w = 8 + r() * 26;
    s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="1.4" fill="#ffffff" opacity="${(0.04 + r() * 0.07).toFixed(2)}"/>`;
  }
  for (let i = 0; i < 60; i++) {
    const x = r() * W, y = 120 + r() * 140;
    s += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(2 + r() * 5).toFixed(1)}" fill="#274a28" opacity="${(0.3 + r() * 0.4).toFixed(2)}"/>`;
  }

  if (anomaly) {
    const ar = rng(seed + 999);
    for (let i = 0; i < 22; i++) {
      const a = ar() * Math.PI * 2, d = ar() * anomaly.r;
      const x = anomaly.x + Math.cos(a) * d, y = anomaly.y + Math.sin(a) * d;
      const c = ['#d8d2c4', '#b9b0a0', '#8f8a80', '#cfc6b3'][Math.floor(ar() * 4)];
      s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(2 + ar() * 5).toFixed(1)}" height="${(2 + ar() * 4).toFixed(1)}" fill="${c}" opacity="${(0.65 + ar() * 0.35).toFixed(2)}" transform="rotate(${(ar() * 90).toFixed(0)} ${x.toFixed(1)} ${y.toFixed(1)})"/>`;
    }
  }

  if (dim) s += `<rect width="${W}" height="${H}" fill="#050a14" opacity="0.35"/>`;
  return s + `</svg>`;
}

/* Наземное фото: берег с мусором / чистый берег */
function photoSVG(seed, dirty) {
  const r = rng(seed);
  let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;
  s += `<defs><linearGradient id="sky${seed}" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#7ba9c9"/><stop offset="1" stop-color="#cfe0ea"/></linearGradient></defs>`;
  s += `<rect width="${W}" height="110" fill="url(#sky${seed})"/>`;
  s += `<rect y="100" width="${W}" height="42" fill="#3f6f8d"/>`;
  s += `<rect y="140" width="${W}" height="${H - 140}" fill="#cbb188"/>`;
  for (let i = 0; i < 40; i++) {
    s += `<circle cx="${(r() * W).toFixed(1)}" cy="${(145 + r() * 110).toFixed(1)}" r="${(1 + r() * 2.5).toFixed(1)}" fill="#b39d76" opacity="0.6"/>`;
  }
  if (dirty) {
    for (let i = 0; i < 26; i++) {
      const x = r() * W, y = 150 + r() * 100;
      const c = ['#e2e2e2', '#9fd3f0', '#e8a0a0', '#cfcfc0', '#8fbf8f'][Math.floor(r() * 5)];
      s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(5 + r() * 12).toFixed(1)}" height="${(4 + r() * 8).toFixed(1)}" rx="2" fill="${c}" transform="rotate(${(r() * 120).toFixed(0)} ${x.toFixed(1)} ${y.toFixed(1)})"/>`;
    }
  }
  return s + `</svg>`;
}

/* ---------- 2. Состояние ---------- */

function baseSegments() {
  return [
    { id: 'ustie', km: '2.4', s: 0.82, c: 0.55, t: 0.70, a: 0.80,
      status: 'problem', seed: 1042, anomaly: { x: 250, y: 96, r: 26 }, votes: 2, inQueue: false, verified: false },
    { id: 'kosa', km: '1.1', s: 0.61, c: 0.40, t: 0.55, a: 0.60,
      status: 'problem', seed: 2311, anomaly: { x: 130, y: 88, r: 20 }, votes: 1, inQueue: false, verified: false },
    { id: 'zaliv', km: '3.0', s: 0.34, c: 0.20, t: 0.35, a: 0.45,
      status: 'watch', seed: 3777, anomaly: null, votes: 0, inQueue: false, verified: false },
    { id: 'mys', km: '0.8', s: 0.55, c: 0.15, t: 0.80, a: 0.25,
      status: 'watch', seed: 4501, anomaly: { x: 300, y: 100, r: 14 }, votes: 0, inQueue: false, verified: false },
    { id: 'plyazh', km: '1.6', s: 0.22, c: 0.10, t: 0.20, a: 0.90,
      status: 'clean', seed: 5120, anomaly: null, votes: 0, inQueue: false, verified: false },
    { id: 'starica', km: '2.2', s: 0.48, c: 0.30, t: 0.60, a: 0.35,
      status: 'watch', seed: 6088, anomaly: { x: 180, y: 92, r: 16 }, votes: 0, inQueue: false, verified: false }
  ];
}

const INITIAL = {
  screen: 'S1',
  city: '',
  user: null,
  trainer: { done: false, hit: false, click: null },
  course: { m1: false, m2: false, m3: false, practice: {} },
  segments: baseSegments(),
  myAnnotation: null,
  event: null,
  report: null,
  log: []
};

let S = load();

function load() {
  try {
    const raw = localStorage.getItem('kb_state');
    if (raw) return JSON.parse(raw);
  } catch (e) {}
  return structuredClone(INITIAL);
}
function save() {
  try { localStorage.setItem('kb_state', JSON.stringify(S)); } catch (e) {}
}

/* Журнал хранит ключ и переменные — при смене языка он переводится заново */
function logEvent(key, vars) {
  S.log.unshift({ time: new Date().toLocaleTimeString(), key, vars: vars || {} });
  S.log = S.log.slice(0, 12);
}

/* ---------- 3. Доменная логика ---------- */

/* Индекс внимания: 0.40·S + 0.30·C + 0.20·T + 0.10·A (см. паспорт, 5.4) */
function attentionIndex(sg) {
  return 0.40 * sg.s + 0.30 * sg.c + 0.20 * sg.t + 0.10 * sg.a;
}
function idx100(sg) { return Math.round(attentionIndex(sg) * 100); }
function seg(id) { return S.segments.find(x => x.id === id); }
function segName(sg) { return t('seg.' + sg.id); }

function roleCode() { return S.user ? S.user.role : 'guest'; }
function roleName() { return t('role.' + roleCode()); }
function isObserver() { return S.user && S.user.role === 'observer'; }

/* ---------- 4. Роутер и навигация ---------- */

const STEPS = [
  { id: 'S1', open: () => true },
  { id: 'S2', open: () => !!S.user },
  { id: 'S3', open: () => isObserver() },
  { id: 'S4', open: () => true },
  { id: 'S5', open: () => isObserver() },
  { id: 'S6', open: () => S.event && S.event.briefed },
  { id: 'ME', open: () => !!S.user }
];

function renderChrome() {
  document.getElementById('appSubtitle').textContent = t('app.subtitle');
  document.getElementById('roleLabel').textContent = t('app.roleLabel');
  document.getElementById('resetBtn').textContent = t('btn.reset');
  document.getElementById('roleBadge').textContent = roleName();

  document.getElementById('langSwitch').innerHTML = Object.values(LANGS).map(l =>
    `<button data-act="setLang" data-v="${l.code}" class="${LANG === l.code ? 'active' : ''}" title="${l.name}">${l.label}</button>`
  ).join('');

  document.getElementById('scenarioNav').innerHTML = STEPS.map(st =>
    `<button data-act="go" data-v="${st.id}" class="${S.screen === st.id ? 'active' : ''}" ${st.open() ? '' : 'disabled'}>${t('nav.' + st.id)}</button>`
  ).join('');
}

function render() {
  renderChrome();
  const fn = { S1: viewS1, REG: viewReg, S2: viewS2, S3: viewS3, S4: viewS4, S5: viewS5, S6: viewS6, ME: viewMe }[S.screen];
  document.getElementById('app').innerHTML = fn ? fn() : '';
  bindDynamic();
  save();
}

/* ---------- 5. Экраны ---------- */

function viewS1() {
  if (S.user) {
    return card(`
      <div class="step-tag">${t('s1.passedTag')}</div>
      <h2>${t('s1.passedTitle')}</h2>
      <p>${t('s1.passedText', { name: S.user.name })}</p>
      <button class="btn" data-act="go" data-v="S2">${t('s1.passedBtn')}</button>
    `);
  }

  if (!S.city) {
    return card(`
      <div class="step-tag">${t('s1.tag1')}</div>
      <h2>${t('s1.title1')}</h2>
      <p>${t('s1.desc1')}</p>
      <label class="field" style="max-width:340px">
        <span>${t('s1.cityLabel')}</span>
        <input type="text" id="cityInput" placeholder="${t('s1.cityPh')}">
      </label>
      <button class="btn" data-act="setCity">${t('s1.cityBtn')}</button>
      <div class="demo-flag">${t('s1.demoFlag')}</div>
    `);
  }

  if (!S.trainer.done) {
    return card(`
      <div class="step-tag">${t('s1.tag2')}</div>
      <h2>${t('s1.title2', { city: S.city })}</h2>
      <p>${t('s1.desc2')}</p>
      <div class="tiles">
        ${tileBox(t('tile.before'), t('tile.dateBefore'), tileSVG(1042))}
        ${tileBox(t('tile.after'), t('tile.dateAfter'), tileSVG(1042, { anomaly: seg('ustie').anomaly }), true, 'trainerTile')}
      </div>
      <p class="tiny muted" style="margin-top:10px">${t('s1.noReg')}</p>
    `);
  }

  const hit = S.trainer.hit;
  return card(`
    <div class="step-tag">${t('s1.tag3')}</div>
    <h2>${hit ? t('s1.hitTitle') : t('s1.missTitle')}</h2>
    <div class="tiles">
      ${tileBox(t('tile.after'), t('tile.dateAfter'), tileSVG(1042, { anomaly: seg('ustie').anomaly }), false, '', markersHTML())}
      <div>
        <div class="note ${hit ? 'ok' : 'warn'}">${hit ? t('s1.hitText') : t('s1.missText')}</div>
        <p class="small">${t('s1.explain')}</p>
        <div class="note">
          <strong>${t('s1.nextTitle')}</strong> ${t('s1.nextText', { oopt: t('oopt.name') })}
        </div>
        <button class="btn" data-act="go" data-v="REG">${t('s1.cta')}</button>
      </div>
    </div>
  `);
}

function viewReg() {
  return card(`
    <div class="step-tag">${t('reg.tag')}</div>
    <h2>${t('reg.title')}</h2>
    <p>${t('reg.desc')}</p>
    <div class="grid-2">
      <div>
        <label class="field"><span>${t('reg.name')}</span><input type="text" id="regName" placeholder="${t('reg.namePh')}"></label>
        <label class="field"><span>${t('reg.age')}</span>
          <select id="regAge">
            <option value="16">${t('reg.age1417')}</option>
            <option value="20">${t('reg.age18')}</option>
          </select>
        </label>
        <button class="btn" data-act="register">${t('reg.btn')}</button>
      </div>
      <div class="note">
        <strong>${t('reg.minorTitle')}</strong>
        <ul class="clean tiny" style="margin-top:8px">
          <li>${t('reg.minor1')}</li><li>${t('reg.minor2')}</li><li>${t('reg.minor3')}</li>
        </ul>
      </div>
    </div>
  `);
}

const MODULES = [
  { id: 'm1', correct: 1 },
  { id: 'm2', correct: 1 },
  { id: 'm3', correct: 1 }
];

function viewS2() {
  const done = MODULES.filter(m => S.course[m.id]).length;

  if (done === 3) {
    return card(`
      <div class="step-tag">${t('s2.doneTag')}</div>
      <h2>${t('s2.doneTitle')}</h2>
      <div class="note ok">${t('s2.doneNote')}</div>
      <div class="grid-3" style="margin-top:16px">
        ${MODULES.map(m => `<div class="card" style="margin:0;padding:14px">
          <b class="small">${t(m.id + '.title')}</b>
          <div class="pill clean" style="margin-top:8px">${t('s2.passed')}</div></div>`).join('')}
      </div>
      <div class="row" style="margin-top:18px">
        <button class="btn" data-act="go" data-v="S3">${t('s2.toS3')}</button>
        <button class="btn ghost" data-act="go" data-v="ME">${t('s2.toCert')}</button>
      </div>
    `);
  }

  const cur = MODULES.find(m => !S.course[m.id]);
  const pr = S.course.practice[cur.id];

  return card(`
    <div class="step-tag">${t('s2.tag')}</div>
    <h2>${t('s2.title')}</h2>
    <p>${t('s2.moduleOf', { n: done + 1 })}</p>
    <div class="progress"><div style="width:${(done / 3) * 100}%"></div></div>

    <h3>${t(cur.id + '.title')}</h3>
    <p style="color:var(--text)">${t(cur.id + '.theory')}</p>

    <div class="note">${t('s2.practice', { q: t(cur.id + '.q') })}</div>
    ${[0, 1, 2].map(i => `
      <div class="seg-item" data-act="practice" data-v="${cur.id}" data-i="${i}"
           style="${pr !== undefined && pr === i ? (i === cur.correct ? 'border-color:var(--ok)' : 'border-color:var(--danger)') : ''}">
        <div class="meta"><b>${t(cur.id + '.o' + i)}</b></div>
      </div>`).join('')}

    ${pr !== undefined ? (pr === cur.correct
      ? `<div class="note ok">${t('s2.correct')}</div><button class="btn" data-act="nextModule" data-v="${cur.id}">${t('s2.next')}</button>`
      : `<div class="note danger">${t('s2.wrong')}</div>`)
      : ''}
  `);
}

function viewS3() {
  const tg = seg('ustie');

  if (S.myAnnotation) {
    const consensus = tg.votes >= 3;
    return card(`
      <div class="step-tag">${t('s3.doneTag')}</div>
      <h2>${t('s3.doneTitle')}</h2>
      <div class="note ${consensus ? 'ok' : ''}">
        ${t('s3.votes', { seg: segName(tg), n: tg.votes })}
        ${consensus ? t('s3.consensusOk') : t('s3.consensusWait')}
      </div>
      <h3>${t('s3.whatHappened')}</h3>
      <table>
        <tr><th>${t('s3.thComponent')}</th><th>${t('s3.thAction')}</th></tr>
        <tr><td class="mono">C3 Annotation</td><td>${t('s3.rowC3')}</td></tr>
        <tr><td class="mono">C6 Geo/Scoring</td><td>${t('s3.rowC6', { c: tg.c.toFixed(2), idx: idx100(tg) })}</td></tr>
        <tr><td class="mono">${t('s3.rowQueue')}</td><td>${consensus ? t('s3.rowQueueOk') : t('s3.rowQueueWait')}</td></tr>
      </table>
      <div class="note warn" style="margin-top:14px">${t('s3.keyPoint')}</div>
      <button class="btn" data-act="go" data-v="S4">${t('s3.toS4')}</button>
    `);
  }

  return card(`
    <div class="step-tag">${t('s3.tag')}</div>
    <h2>${t('s3.title', { seg: segName(tg) })}</h2>
    <p>${t('s3.desc', { oopt: t('oopt.name') })}</p>
    <div class="tiles">
      ${tileBox(t('tile.before'), t('tile.dateBefore'), tileSVG(tg.seed))}
      ${tileBox(t('tile.after'), t('tile.dateAfter'), tileSVG(tg.seed, { anomaly: tg.anomaly }), true, 'annotTile')}
    </div>
    <div class="row" style="margin-top:14px">
      <span class="small muted">${t('s3.verdict')}</span>
      <button class="btn sm" data-act="annotate" data-v="dump">${t('s3.vDump')}</button>
      <button class="btn ghost sm" data-act="annotate" data-v="litter">${t('s3.vLitter')}</button>
      <button class="btn ghost sm" data-act="annotate" data-v="none">${t('s3.vNone')}</button>
    </div>
    <div class="demo-flag">${t('s3.consensusHint', { n: tg.votes })}</div>
  `);
}

function viewS4() {
  const sorted = [...S.segments].sort((a, b) => attentionIndex(b) - attentionIndex(a));
  const queue = S.segments.filter(x => x.inQueue && !x.verified);
  const reportPending = S.report && S.report.status === 'pending';

  return `
    ${card(`
      <div class="step-tag">${t('s4.tag')}</div>
      <h2>${t('oopt.name')}</h2>
      <p>${t('s4.desc')}</p>
    `)}

    ${queue.length ? card(`
      <h3 style="margin-top:0">${t('s4.queueTitle', { n: queue.length })}</h3>
      ${queue.map(q => `
        <div class="card" style="margin:10px 0;background:var(--panel-2)">
          <div class="row">
            <b>${segName(q)}</b>
            <span class="pill problem">${t('s4.consensusPill', { n: q.votes })}</span>
            <span class="muted small">${t('s4.idxLabel', { n: idx100(q) })}</span>
          </div>
          <div class="tiles" style="margin:12px 0">
            ${tileBox(t('tile.before'), t('tile.dateBefore'), tileSVG(q.seed))}
            ${tileBox(t('tile.after'), t('tile.dateAfter'), tileSVG(q.seed, { anomaly: q.anomaly }))}
          </div>
          <div class="row">
            <button class="btn ok sm" data-act="approveSeg" data-v="${q.id}">${t('s4.approve')}</button>
            <button class="btn danger sm" data-act="rejectSeg" data-v="${q.id}">${t('s4.reject')}</button>
            <span class="tiny muted">${t('s4.rejectHint')}</span>
          </div>
        </div>`).join('')}
    `) : ''}

    ${reportPending ? card(`
      <h3 style="margin-top:0">${t('s4.reportQueue')}</h3>
      <p>${t('s4.reportInfo', { seg: segName(seg(S.report.segId)), kg: S.report.volume })}</p>
      <div class="tiles" style="margin:12px 0">
        ${tileBox(t('photo.before'), t('photo.geo'), photoSVG(77, true))}
        ${tileBox(t('photo.after'), t('photo.geo'), photoSVG(77, false))}
      </div>
      <div class="row">
        <button class="btn ok sm" data-act="approveReport">${t('s4.approveReport')}</button>
        <button class="btn danger sm" data-act="rejectReport">${t('s4.reject')}</button>
      </div>
    `) : ''}

    ${card(`
      <h3 style="margin-top:0">${t('s4.mapTitle')}</h3>
      <p class="small">${t('s4.mapFormula')}</p>
      ${sorted.map(x => {
        const canEvent = x.verified && x.status === 'work' && !S.event;
        return `
        <div class="seg-item">
          <div class="idx">${idx100(x)}</div>
          <div class="meta">
            <b>${segName(x)} <span class="pill ${x.status}">${t('status.' + x.status)}</span></b>
            <small>${t('s4.kmLine', { km: x.km + ' km' })}</small>
            <div class="factors">
              <span>S ${x.s.toFixed(2)}</span><span>C ${x.c.toFixed(2)}</span>
              <span>T ${x.t.toFixed(2)}</span><span>A ${x.a.toFixed(2)}</span>
            </div>
          </div>
          ${canEvent ? `<button class="btn sm" data-act="createEvent" data-v="${x.id}">${t('s4.createEvent')}</button>` : ''}
        </div>`;
      }).join('')}
      <div class="note" style="margin-top:14px">${t('s4.trustNote')}</div>
    `)}

    ${S.log.length ? card(`
      <h3 style="margin-top:0">${t('s4.logTitle')}</h3>
      <table>${S.log.map(l => `<tr><td class="mono tiny" style="width:88px">${l.time}</td><td class="small">${t(l.key, l.vars)}</td></tr>`).join('')}</table>
    `) : ''}
  `;
}

function viewS5() {
  if (!S.event) {
    return card(`
      <div class="step-tag">${t('s5.tag')}</div>
      <h2>${t('s5.noEventTitle')}</h2>
      <p>${t('s5.noEventDesc')}</p>
      <div class="note">${t('s5.noEventNote')}</div>
      <button class="btn" data-act="go" data-v="S4">${t('s5.toS4')}</button>
    `);
  }

  const sg = seg(S.event.segId);
  const mine = S.myAnnotation && S.myAnnotation.segId === S.event.segId;

  if (!S.event.enrolled) {
    return card(`
      <div class="step-tag">${t('s5.enrollTag')}</div>
      <h2>${t('s5.expedition', { seg: segName(sg) })}</h2>
      ${mine ? `<div class="note ok">${t('s5.mineNote')}</div>` : ''}
      <div class="kv"><span>${t('s5.date')}</span><b>${t('s5.dateValue')}</b></div>
      <div class="kv"><span>${t('s5.organizer')}</span><b>${t('oopt.name')}</b></div>
      <div class="kv"><span>${t('s5.seats')}</span><b>${t('s5.seatsValue')}</b></div>
      <div class="kv"><span>${t('s5.segIdx')}</span><b>${idx100(sg)}</b></div>
      <button class="btn" style="margin-top:16px" data-act="enroll">${t('s5.enroll')}</button>
    `);
  }

  if (!S.event.consent && S.user.minor) {
    return card(`
      <div class="step-tag">${t('s5.consentTag')}</div>
      <h2>${t('s5.consentTitle')}</h2>
      <div class="note warn">${t('s5.consentNote', { age: S.user.age })}</div>
      <label class="field" style="max-width:360px">
        <span>${t('s5.parentContact')}</span>
        <input type="text" id="parentContact" placeholder="${t('s5.parentPh')}">
      </label>
      <button class="btn" data-act="sendConsent">${t('s5.sendConsent')}</button>
    `);
  }

  if (!S.event.briefed) {
    return card(`
      <div class="step-tag">${t('s5.briefTag')}</div>
      <h2>${t('s5.briefTitle')}</h2>
      ${S.user.minor ? `<div class="note ok">${t('s5.consentOk')}</div>` : ''}
      <p>${t('s5.briefDesc')}</p>
      <ul class="clean">
        <li>${t('s5.brief1')}</li><li>${t('s5.brief2')}</li><li>${t('s5.brief3')}</li>
        <li>${t('s5.brief4')}</li><li>${t('s5.brief5')}</li>
      </ul>
      <button class="btn" data-act="brief">${t('s5.briefBtn')}</button>
    `);
  }

  return card(`
    <div class="step-tag">${t('s5.doneTag')}</div>
    <h2>${t('s5.doneTitle')}</h2>
    <div class="note ok">${t('s5.doneNote', { consent: S.user.minor ? t('s5.doneConsent') : '' })}</div>
    <div class="kv"><span>${t('s5.segment')}</span><b>${segName(sg)}</b></div>
    <div class="kv"><span>${t('s5.date')}</span><b>${t('s5.dateValue')}</b></div>
    <button class="btn" style="margin-top:16px" data-act="go" data-v="S6">${t('s5.toS6')}</button>
  `);
}

function viewS6() {
  const sg = seg(S.event.segId);

  if (!S.report) {
    return card(`
      <div class="step-tag">${t('s6.tag')}</div>
      <h2>${t('s6.title', { seg: segName(sg) })}</h2>
      <p>${t('s6.step1')}</p>
      <div class="tile-box" style="max-width:400px">
        <span class="tile-label">${t('photo.before')}</span>
        <span class="tile-date">${t('photo.geo')}</span>
        ${photoSVG(77, true)}
      </div>
      <button class="btn" style="margin-top:14px" data-act="startReport">${t('s6.startBtn')}</button>
      <div class="demo-flag">${t('s6.demoFlag')}</div>
    `);
  }

  if (S.report.status === 'draft') {
    return card(`
      <div class="step-tag">${t('s6.tag2')}</div>
      <h2>${t('s6.title2')}</h2>
      <div class="tiles">
        ${tileBox(t('photo.before'), '10:42', photoSVG(77, true))}
        ${tileBox(t('photo.after'), '13:20', photoSVG(77, false))}
      </div>
      <label class="field" style="max-width:240px;margin-top:14px">
        <span>${t('s6.volume')}</span>
        <input type="number" id="volume" value="145">
      </label>
      <button class="btn" data-act="submitReport">${t('s6.submit')}</button>
    `);
  }

  if (S.report.status === 'pending') {
    return card(`
      <div class="step-tag">${t('s6.pendingTag')}</div>
      <h2>${t('s6.pendingTitle')}</h2>
      <div class="note">${t('s6.pendingNote')}</div>
      <button class="btn" data-act="go" data-v="S4">${t('s6.toS4')}</button>
    `);
  }

  return card(`
    <div class="step-tag">${t('s6.doneTag')}</div>
    <h2>${t('s6.doneTitle')}</h2>
    <div class="note ok">${t('s6.doneNote', { seg: segName(sg), a: S.report.idxBefore, b: idx100(sg) })}</div>
    <h3>${t('s6.sliderTitle')}</h3>
    ${beforeAfter(photoSVG(77, true), photoSVG(77, false))}
    <div class="grid-3" style="margin-top:18px">
      <div class="card" style="margin:0"><span class="muted small">${t('s6.hours')}</span><h2>4</h2></div>
      <div class="card" style="margin:0"><span class="muted small">${t('s6.collected')}</span><h2>${S.report.volume} kg</h2></div>
      <div class="card" style="margin:0"><span class="muted small">${t('s6.subscribed')}</span><h2>✓</h2></div>
    </div>
    <div class="note" style="margin-top:16px">${t('s6.returnNote')}</div>
    <button class="btn" data-act="go" data-v="ME">${t('s6.toMe')}</button>
  `);
}

function viewMe() {
  const u = S.user;
  const doneCourse = MODULES.every(m => S.course[m.id]);
  const approved = S.report && S.report.status === 'approved';
  return card(`
    <div class="step-tag">${t('me.tag')}</div>
    <h2>${u.name}</h2>
    <div class="kv"><span>${t('me.role')}</span><b>${roleName()}</b></div>
    <div class="kv"><span>${t('me.ageCat')}</span><b>${u.minor ? t('me.minor') : t('me.adult')}</b></div>
    <div class="kv"><span>${t('me.course')}</span><b>${doneCourse ? t('me.courseDone') : t('me.courseWip')}</b></div>
    <div class="kv"><span>${t('me.cert')}</span><b>${doneCourse ? 'KB-2026-0417 ✓' : '—'}</b></div>
    <div class="kv"><span>${t('me.annotations')}</span><b>${S.myAnnotation ? 1 : 0}</b></div>
    <div class="kv"><span>${t('me.hours')}</span><b>${approved ? 4 : 0}</b></div>
    <div class="kv"><span>${t('me.reputation')}</span><b>${u.reputation.toFixed(2)}</b></div>
    <div class="kv"><span>${t('me.subs')}</span><b>${approved ? segName(seg(S.report.segId)) : '—'}</b></div>
    ${u.minor ? `<div class="note warn" style="margin-top:14px">${t('me.minorNote')}</div>` : ''}
    ${approved ? `<div class="note ok" style="margin-top:14px">${t('me.allDone')}</div>` : ''}
  `);
}

/* ---------- 6. Вспомогательная разметка ---------- */

function card(inner) { return `<div class="card">${inner}</div>`; }

function tileBox(label, date, svg, clickable = false, id = '', extra = '') {
  return `<div class="tile-box ${clickable ? 'clickable' : ''}" ${id ? `id="${id}"` : ''}>
    <span class="tile-label">${label}</span>
    <span class="tile-date">${date}</span>
    ${svg}${extra}
  </div>`;
}

function markersHTML() {
  const a = seg('ustie').anomaly;
  const c = S.trainer.click;
  const truth = `<div class="marker truth" style="left:${(a.x / W) * 100}%;top:${(a.y / H) * 100}%;width:56px;height:56px"></div>`;
  const mine = c ? `<div class="marker ${S.trainer.hit ? 'hit' : ''}" style="left:${c.xp}%;top:${c.yp}%"></div>` : '';
  return truth + mine;
}

function beforeAfter(svgA, svgB) {
  return `<div class="ba-wrap" id="baWrap">
    ${svgA}
    <div class="after-layer" id="baAfter" style="width:50%">${svgB}</div>
    <div class="handle" id="baHandle" style="left:50%"></div>
  </div>`;
}

/* ---------- 7. Действия ---------- */

const ACTIONS = {
  go(v) { S.screen = v; render(); },

  setLang(v) { setLang(v); render(); },

  setCity() {
    S.city = document.getElementById('cityInput').value.trim() || t('s1.defaultCity');
    render();
  },

  register() {
    const name = document.getElementById('regName').value.trim() || t('reg.defaultName');
    const age = parseInt(document.getElementById('regAge').value, 10);
    S.user = { name, age, minor: age < 18, role: 'student', reputation: 1.0 };
    logEvent('log.register', { cat: age < 18 ? '14–17' : '18+' });
    S.screen = 'S2';
    render();
  },

  practice(id, i) {
    S.course.practice[id] = parseInt(i, 10);
    render();
  },

  nextModule(id) {
    S.course[id] = true;
    logEvent('log.module', { m: id.toUpperCase() });
    if (MODULES.every(m => S.course[m.id])) {
      S.user.role = 'observer';
      logEvent('log.courseDone');
    }
    render();
  },

  annotate(verdict) {
    const tg = seg('ustie');
    S.myAnnotation = { segId: 'ustie', verdict };
    if (verdict !== 'none') {
      tg.votes += 1;
      tg.c = Math.min(1, tg.c + 0.18);
      logEvent('log.annotate', { v: t('s3.v' + (verdict === 'dump' ? 'Dump' : 'Litter')), n: tg.votes });
      if (tg.votes >= 3) {
        tg.inQueue = true;
        logEvent('log.consensus', { c: tg.c.toFixed(2), idx: idx100(tg) });
        logEvent('log.queued');
      }
    } else {
      logEvent('log.annotateNone');
    }
    render();
  },

  approveSeg(id) {
    const x = seg(id);
    x.verified = true;
    x.status = 'work';
    if (S.user) S.user.reputation = Math.min(1.5, S.user.reputation + 0.1);
    logEvent('log.approveSeg', { seg: segName(x) });
    logEvent('log.repUp');
    render();
  },

  rejectSeg(id) {
    const x = seg(id);
    x.inQueue = false;
    x.c = Math.max(0, x.c - 0.25);
    if (S.user) S.user.reputation = Math.max(0.2, S.user.reputation - 0.2);
    logEvent('log.rejectSeg', { seg: segName(x) });
    render();
  },

  createEvent(id) {
    S.event = { segId: id, enrolled: false, briefed: false, consent: false };
    logEvent('log.createEvent', { seg: segName(seg(id)) });
    S.screen = 'S5';
    render();
  },

  enroll() { S.event.enrolled = true; logEvent('log.enroll'); render(); },
  sendConsent() { S.event.consent = true; logEvent('log.consent'); render(); },
  brief() { S.event.briefed = true; logEvent('log.brief'); render(); },

  startReport() {
    S.report = { segId: S.event.segId, volume: 0, status: 'draft' };
    logEvent('log.photoBefore');
    render();
  },

  submitReport() {
    const v = parseInt(document.getElementById('volume').value, 10) || 0;
    S.report.volume = v;
    S.report.status = 'pending';
    S.report.idxBefore = idx100(seg(S.report.segId));
    logEvent('log.submitReport', { kg: v });
    render();
  },

  approveReport() {
    const x = seg(S.report.segId);
    S.report.status = 'approved';
    x.status = 'clean';
    x.t = 0.05;
    x.c = Math.max(0, x.c - 0.35);
    x.s = Math.max(0, x.s - 0.30);
    logEvent('log.approveReport', { seg: segName(x) });
    logEvent('log.recalc', { idx: idx100(x) });
    logEvent('log.hours');
    S.screen = 'S6';
    render();
  },

  rejectReport() {
    S.report.status = 'draft';
    logEvent('log.rejectReport');
    render();
  }
};

/* ---------- 8. Обработчики ---------- */

document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act;
  if (ACTIONS[act]) {
    e.preventDefault();
    ACTIONS[act](el.dataset.v, el.dataset.i);
  }
});

document.getElementById('resetBtn').addEventListener('click', () => {
  S = structuredClone(INITIAL);
  save();
  render();
});

function bindDynamic() {
  const trainer = document.getElementById('trainerTile');
  if (trainer) {
    trainer.addEventListener('click', ev => {
      const r = trainer.getBoundingClientRect();
      const xp = ((ev.clientX - r.left) / r.width) * 100;
      const yp = ((ev.clientY - r.top) / r.height) * 100;
      const a = seg('ustie').anomaly;
      const dx = (xp / 100) * W - a.x, dy = (yp / 100) * H - a.y;
      S.trainer = { done: true, hit: Math.hypot(dx, dy) < a.r + 22, click: { xp, yp } };
      render();
    });
  }

  const annot = document.getElementById('annotTile');
  if (annot) {
    annot.addEventListener('click', ev => {
      const r = annot.getBoundingClientRect();
      const xp = ((ev.clientX - r.left) / r.width) * 100;
      const yp = ((ev.clientY - r.top) / r.height) * 100;
      annot.querySelectorAll('.marker').forEach(m => m.remove());
      const m = document.createElement('div');
      m.className = 'marker';
      m.style.left = xp + '%';
      m.style.top = yp + '%';
      annot.appendChild(m);
    });
  }

  const wrap = document.getElementById('baWrap');
  if (wrap) {
    const after = document.getElementById('baAfter');
    const handle = document.getElementById('baHandle');
    let drag = false;
    const move = cx => {
      const r = wrap.getBoundingClientRect();
      const p = Math.max(0, Math.min(100, ((cx - r.left) / r.width) * 100));
      after.style.width = p + '%';
      handle.style.left = p + '%';
    };
    handle.addEventListener('mousedown', () => drag = true);
    document.addEventListener('mouseup', () => drag = false);
    wrap.addEventListener('mousemove', e => drag && move(e.clientX));
    wrap.addEventListener('click', e => move(e.clientX));
  }
}

/* ---------- 9. Старт ---------- */
render();
