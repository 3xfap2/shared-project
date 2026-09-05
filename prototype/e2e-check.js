/* Проверка сквозного сценария S1 → S6 без браузера, на всех трёх языках.
   Подставляет минимальные заглушки DOM и прогоняет состояние по всем шагам.
   Запуск:  node e2e-check.js
*/
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const here = f => path.join(__dirname, f);
const I18N_SRC = fs.readFileSync(here('i18n.js'), 'utf8');
const APP_SRC = fs.readFileSync(here('app.js'), 'utf8');

function makeSandbox(lang) {
  const inputs = {};
  const nodes = {};
  const el = id => (nodes[id] ||= {
    id,
    get value() { return inputs[id] ?? ''; },
    set value(v) { inputs[id] = v; },
    innerHTML: '', textContent: '', style: {}, dataset: {},
    addEventListener() {}, removeEventListener() {},
    querySelectorAll: () => [], appendChild() {}, remove() {},
    closest: () => null,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 260 })
  });

  const store = { kb_lang: lang };
  const sandbox = {
    console, structuredClone, Math, Date, JSON,
    parseInt, parseFloat, Number, String, Array, Object, Set,
    document: {
      getElementById: el,
      addEventListener() {},
      createElement: () => el('tmp')
    },
    localStorage: {
      getItem: k => store[k] ?? null,
      setItem: (k, v) => { store[k] = v; }
    },
    __inputs: inputs
  };
  vm.createContext(sandbox);
  vm.runInContext(
    I18N_SRC + '\n' + APP_SRC +
    '\n;globalThis.__api={ACTIONS,getS:()=>S,seg,idx100,segName,MODULES,attentionIndex,t,LANG};',
    sandbox
  );
  return { api: sandbox.__api, inputs };
}

let totalFails = 0;

function runScenario(lang, langName) {
  const { api, inputs } = makeSandbox(lang);
  const A = api.ACTIONS;
  let fails = 0;

  const check = (name, cond, extra = '') => {
    if (!cond) { fails++; console.log(`  FAIL  ${name}${extra ? ' — ' + extra : ''}`); }
  };

  console.log(`\n── ${langName} (${lang}) ──`);

  // S1
  inputs['cityInput'] = 'Test City';
  A.setCity();
  check('город сохранён', api.getS().city === 'Test City');

  inputs['regName'] = 'Test User';
  inputs['regAge'] = '16';
  A.register();
  let S = api.getS();
  check('пользователь создан', S.user && S.user.name === 'Test User');
  check('несовершеннолетний определён', S.user.minor === true);
  check('стартовая роль student', S.user.role === 'student');

  // S2
  const idxStart = api.idx100(api.seg('ustie'));
  for (const m of api.MODULES) A.nextModule(m.id);
  S = api.getS();
  check('все 3 модуля засчитаны', api.MODULES.every(m => S.course[m.id]));
  check('роль повышена до observer', S.user.role === 'observer');

  // S3
  const cBefore = api.seg('ustie').c;
  A.annotate('dump');
  const tg = api.seg('ustie');
  check('разметка сохранена', !!api.getS().myAnnotation);
  check('консенсус 3/3', tg.votes === 3, `votes=${tg.votes}`);
  check('фактор C вырос', tg.c > cBefore);
  check('участок в очереди ООПТ', tg.inQueue === true);
  check('индекс вырос', api.idx100(tg) > idxStart, `${idxStart} → ${api.idx100(tg)}`);

  // S4
  A.approveSeg('ustie');
  check('участок подтверждён', api.seg('ustie').verified === true);
  check('статус work', api.seg('ustie').status === 'work');
  check('репутация выросла', api.getS().user.reputation > 1.0);

  A.createEvent('ustie');
  S = api.getS();
  check('акция создана', S.event && S.event.segId === 'ustie');
  const sorted = [...S.segments].sort((a, b) => api.attentionIndex(b) - api.attentionIndex(a));
  check('участок возглавил приоритеты', sorted[0].id === 'ustie');

  // S5
  A.enroll();
  check('волонтёр записан', api.getS().event.enrolled === true);
  A.sendConsent();
  check('согласие родителя получено', api.getS().event.consent === true);
  A.brief();
  check('инструктаж пройден', api.getS().event.briefed === true);

  // S6
  A.startReport();
  check('фото «до» зафиксировано', api.getS().report.status === 'draft');
  inputs['volume'] = '145';
  A.submitReport();
  check('отчёт на модерации', api.getS().report.status === 'pending');
  const idxBeforeClean = api.idx100(api.seg('ustie'));

  A.approveReport();
  S = api.getS();
  const after = api.seg('ustie');
  check('отчёт подтверждён', S.report.status === 'approved');
  check('статус clean', after.status === 'clean');
  check('фактор T обнулён', after.t === 0, `T=${after.t}`);
  check('индекс снизился', api.idx100(after) < idxBeforeClean, `${idxBeforeClean} → ${api.idx100(after)}`);
  const sortedAfter = [...S.segments].sort((a, b) => api.attentionIndex(b) - api.attentionIndex(a));
  check('участок ушёл вниз приоритетов', sortedAfter[0].id !== 'ustie');

  // Сверка с бэкендом. Эти же числа обязан выдать сквозной тест
  // backend/tests/test_cross_check.py — на совпадении двух независимых
  // реализаций строится проверяемость решения.
  check('индекс после консенсуса = 85 (как на бэкенде)', idxBeforeClean === 85,
        `получили ${idxBeforeClean}`);
  check('индекс после уборки = 48 (как на бэкенде)', api.idx100(after) === 48,
        `получили ${api.idx100(after)}`);

  // локализация
  check('язык активен', api.LANG === lang, `LANG=${api.LANG}`);
  const segLabel = api.segName(api.seg('ustie'));
  check('название участка локализовано', typeof segLabel === 'string' && segLabel.length > 0);
  const logLine = api.t(S.log[0].key, S.log[0].vars);
  check('журнал локализован', !logLine.startsWith('log.'), logLine);
  check('нет непереведённых ключей в журнале',
    S.log.every(l => !api.t(l.key, l.vars).startsWith('log.')));

  console.log(`  ${fails === 0 ? 'OK' : fails + ' FAIL'} · 27 проверок · участок: «${segLabel}» · индекс ${idxBeforeClean} → ${api.idx100(after)}`);
  console.log(`  журнал: ${logLine}`);
  totalFails += fails;
}

console.log('\n=== Сквозной сценарий КОСМОБЕРЕГ S1 → S6 ===');
runScenario('ru', 'Русский');
runScenario('en', 'English');
runScenario('zh', '中文');

console.log(`\n=== ${totalFails === 0 ? 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ НА ТРЁХ ЯЗЫКАХ' : totalFails + ' ПРОВЕРОК ПРОВАЛЕНО'} ===\n`);
process.exit(totalFails === 0 ? 0 : 1);
