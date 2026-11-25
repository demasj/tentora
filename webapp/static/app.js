/* Tentora — browser exam client (vanilla JS, no build step).
 *
 * The server owns the exam: it shuffles, keeps the correct answers to itself,
 * runs the clock and grades via scripts/score.py. This client only renders
 * questions, records answers (autosaved to the server) and shows the report.
 * All UI wording lives in I18N below (English default, Dutch available).
 */
'use strict';

const app = document.getElementById('app');
const modalRoot = document.getElementById('modal-root');

// ------------------------------------------------------------------ utils --

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/** Minimal, safe markdown: `code`, **bold**, *italic*, line breaks. */
function md(text) {
  return String(text ?? '').split(/(`[^`]+`)/g).map((part) => {
    if (part.length > 1 && part.startsWith('`') && part.endsWith('`')) {
      return `<code>${esc(part.slice(1, -1))}</code>`;
    }
    return esc(part)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, '$1<em>$2</em>')
      .replace(/\n/g, '<br>');
  }).join('');
}

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ------------------------------------------------------------------- i18n --

const LANGS = { en: 'English', nl: 'Nederlands' };

/* Values are strings or functions of a params object. Strings marked
 * "(html)" may contain markup; everything interpolated is escaped by callers. */
const I18N = {
  en: {
    locale: 'en-GB',
    loading: 'Loading…',
    brand: 'Tentora Test Centre',
    language: 'Language',
    errorTitle: 'Something went wrong',
    backHome: 'Back to start',
    // home
    welcome: 'Welcome, candidate',
    homeIntro: 'Choose an exam. You will see the instructions first; the clock only starts when you click <b>Start exam</b>.',
    inProgress: 'Exam in progress',
    inProgressText: (p) => `${p.title} — ${p.answered}/${p.questions} answered, <b>${p.left}</b> remaining. The clock keeps running while this window is closed.`,
    resume: 'Resume',
    available: 'Available exams',
    poolMixed: (p) => `Full exam — ${p.questions} questions mixed from ${p.banks} banks (${p.size} unique questions, ${p.fresh} not seen yet this round)`,
    poolBank: (p) => `Single bank: ${p.file}`,
    poolRetry: (p) => `The ${p.n} questions you got wrong in your last attempt`,
    retryTitle: 'Retry — wrong answers',
    nQuestions: (n) => `${n} questions`,
    nMinutes: (n) => `${n} minutes`,
    passFrom: (n) => `pass at ${n}%`,
    dateBank: (d) => `generated ${d}`,
    dateMixed: (d) => `newest bank ${d}`,
    dateRetry: (d) => `created ${d}`,
    select: 'Select',
    noBanks: (slug) => `No question banks yet. Generate some with <code>make generate EXAM=${slug}</code>.`,
    noExams: 'No exams found in <code>exams/</code>. Add one with <code>make profile</code>.',
    guideVersion: (v) => `Exam guide ${v}`,
    history: 'Previous attempts',
    thDate: 'Date', thExam: 'Exam', thScore: 'Score', thResult: 'Result', thCorrect: 'Correct', thTime: 'Time',
    report: 'Report',
    delete: 'Delete',
    deleteTitle: 'Delete this attempt?',
    deleteText: (p) => `${p.title} — ${p.date}, score <b>${p.score}%</b>. The attempt and its score report are removed from the history. This cannot be undone.`,
    confirmDelete: 'Yes, delete',
    PASS: 'PASS', FAIL: 'FAIL',
    noAttempts: 'No exams taken yet.',
    // instructions
    statQuestions: 'Questions', statTime: 'Time', statPass: 'Passing score', statType: 'Question type',
    plusUnscored: (n) => `+ up to ${n} unscored`,
    unscoredOpt: (n) => `Include unscored items, like the real exam (up to ${n} extra questions that do not count; they are not identified during the exam)`,
    unscoredRule: 'This exam may include unscored items. They are not identified and do not affect your score; the time limit already accounts for them.',
    multipleChoice: 'Multiple choice',
    min: 'min',
    rulesTitle: 'Exam rules',
    rules: [
      'The clock starts as soon as you click <b>Start exam</b> and cannot be paused. It keeps running if you close the window.',
      'When time runs out the exam ends automatically and your answers are graded as they stand.',
      'You will <b>not</b> see whether an answer is correct during the exam. Explanations and correct answers follow in the score report.',
      'Unanswered questions count as wrong — guessing costs nothing.',
      'You can <b>mark questions for review</b> and return to any question from the review screen.',
      'For questions with several answers the question states how many to choose. Only a fully correct answer counts.',
      'The score is weighted per exam section (official blueprint). Questions and answer order are shuffled every attempt.',
    ],
    controlsTitle: 'Controls',
    keyChoose: 'choose an answer / toggle it',
    keyNav: 'previous / next question',
    keyMark: 'mark for review',
    keyOverview: 'review screen',
    rightClick: 'Right-click',
    keyStrike: 'strike out an answer (process of elimination)',
    agree: 'I have read the instructions and understand that the clock starts immediately.',
    cancel: 'Cancel',
    start: 'Start exam',
    preparing: 'Preparing exam…',
    // exam
    questionOf: (p) => `Question ${p.i} of ${p.n}`,
    overview: 'Review',
    remaining: 'Remaining',
    remainingTitle: 'Time remaining',
    close: 'Close',
    timeWarning: (m) => `Attention: ${m} ${m === 1 ? 'minute' : 'minutes'} remaining.`,
    markReview: 'Mark for review',
    answeredOf: (p) => `${p.a} of ${p.n} answered`,
    reviewPos: (p) => ` · review ${p.i}/${p.n}`,
    smaller: 'Smaller', larger: 'Larger',
    question: (i) => `Question ${i}`,
    hintMulti: (n) => `Choose ${n} answers`,
    hintTF: 'True or false',
    hintSingle: 'Choose one answer',
    multiFull: (n) => `You have chosen ${n} answers. Untick one to choose a different answer.`,
    tipStrike: 'Tip: right-click an answer to strike it out.',
    prev: '◀ Previous',
    next: 'Next ▶',
    toOverview: 'Review ▶',
    ovTitle: 'Review your answers',
    ovIntro: 'Click a question number to go to it, or step through a selection. You can change answers until you end the exam.',
    answered: 'Answered', unanswered: 'Unanswered', marked: 'Marked',
    tileTitle: (p) => `Question ${p.i}: ${p.answered ? 'answered' : 'unanswered'}${p.marked ? ', marked' : ''}`,
    revAll: 'Review all',
    revUnanswered: (n) => `Review unanswered (${n})`,
    revMarked: (n) => `Review marked (${n})`,
    backTo: (i) => `◀ Back to question ${i}`,
    endExam: 'End exam',
    endTitle: 'End the exam?',
    endUnanswered: (n) => `<b>${n}</b> ${n === 1 ? 'question is' : 'questions are'} unanswered and ${n === 1 ? 'counts' : 'count'} as wrong.`,
    endAllAnswered: 'All questions have been answered.',
    endMarked: (n) => `<b>${n}</b> ${n === 1 ? 'question is' : 'questions are'} marked for review.`,
    endFinal: 'After ending the exam you can no longer change your answers.',
    backToExam: 'Back to exam',
    confirmEnd: 'Yes, end exam',
    timeUpTitle: 'Time is up',
    timeUpText: 'Your exam time has expired. Your answers have been submitted and graded.',
    viewReport: 'View score report',
    // results
    toStart: 'Back to start',
    scoreReport: 'Score report',
    yourScore: 'Your score',
    passMarkNote: (n) => `Passing score ${n}% · weighted per section`,
    statScore: 'Score',
    unscoredNote: (n) => `${n} unscored ${n === 1 ? 'item was' : 'items were'} included and did not count.`,
    unscoredTag: ' · unscored',
    objective: 'Objective',
    sources: 'Documentation',
    reportQ: 'Report a problem with this question',
    reported: '⚑ Reported — excluded from future exams (click to undo)',
    reportPrompt: 'What is wrong with this question? (it will be excluded from future exams)',
    timedOut: 'The exam ended automatically because time ran out.',
    statCorrect: 'Correct', statWrong: 'Wrong', statUnanswered: 'Unanswered', statSpent: 'Time spent',
    bySection: 'Results by section',
    thSection: 'Section', thWeight: 'Weight',
    barNote: (n) => `The tick in each bar is the passing score (${n}%).`,
    retryWrong: 'Retry wrong answers',
    showAnswers: 'Show answers and explanations',
    hideAnswers: 'Hide answers',
    answersTitle: 'Answers and explanations',
    tabWrong: (n) => `Wrong (${n})`, tabMarked: (n) => `Marked (${n})`, tabAll: (n) => `All (${n})`,
    noneInSel: 'No questions in this selection.',
    yourAnswer: 'your answer',
    markedTag: ' · ⚑ marked',
    vRight: '✓ Correct', vWrong: '✗ Wrong', vUnanswered: '✗ Not answered',
    explanation: 'Explanation',
    dur: (p) => (p.m ? `${p.m} min ${p.s} s` : `${p.s} s`),
  },

  nl: {
    locale: 'nl-NL',
    loading: 'Laden…',
    brand: 'Tentora Testcentrum',
    language: 'Taal',
    errorTitle: 'Er ging iets mis',
    backHome: 'Terug naar start',
    welcome: 'Welkom, kandidaat',
    homeIntro: 'Kies een examen. Je krijgt eerst de instructies; de tijd start pas als je op <b>Examen starten</b> klikt.',
    inProgress: 'Examen in uitvoering',
    inProgressText: (p) => `${p.title} — ${p.answered}/${p.questions} beantwoord, nog <b>${p.left}</b> resterend. De klok loopt door, ook als dit venster dicht is.`,
    resume: 'Hervatten',
    available: 'Beschikbare examens',
    poolMixed: (p) => `Volledig examen — ${p.questions} vragen gemengd uit ${p.banks} banken (${p.size} unieke vragen, ${p.fresh} nog niet gehad deze ronde)`,
    poolBank: (p) => `Eén bank: ${p.file}`,
    poolRetry: (p) => `De ${p.n} vragen die je bij je laatste poging fout had`,
    retryTitle: 'Herkansing — foute vragen',
    nQuestions: (n) => `${n} vragen`,
    nMinutes: (n) => `${n} minuten`,
    passFrom: (n) => `slagen vanaf ${n}%`,
    dateBank: (d) => `gegenereerd ${d}`,
    dateMixed: (d) => `nieuwste bank ${d}`,
    dateRetry: (d) => `aangemaakt ${d}`,
    select: 'Selecteren',
    noBanks: (slug) => `Nog geen vragenbanken. Genereer ze met <code>make generate EXAM=${slug}</code>.`,
    noExams: 'Geen examens gevonden in <code>exams/</code>. Voeg er een toe met <code>make profile</code>.',
    guideVersion: (v) => `Examengids ${v}`,
    history: 'Eerdere pogingen',
    thDate: 'Datum', thExam: 'Examen', thScore: 'Score', thResult: 'Uitslag', thCorrect: 'Goed', thTime: 'Duur',
    report: 'Rapport',
    delete: 'Verwijderen',
    deleteTitle: 'Deze poging verwijderen?',
    deleteText: (p) => `${p.title} — ${p.date}, score <b>${p.score}%</b>. De poging en het scorerapport worden uit de geschiedenis verwijderd. Dit kan niet ongedaan worden gemaakt.`,
    confirmDelete: 'Ja, verwijderen',
    PASS: 'GESLAAGD', FAIL: 'GEZAKT',
    noAttempts: 'Nog geen examens afgelegd.',
    statQuestions: 'Vragen', statTime: 'Tijd', statPass: 'Slagingsgrens', statType: 'Vraagtype',
    plusUnscored: (n) => `+ max. ${n} ongescoord`,
    unscoredOpt: (n) => `Ongescoorde vragen opnemen, zoals bij het echte examen (max. ${n} extra vragen die niet meetellen; je ziet tijdens het examen niet welke)`,
    unscoredRule: 'Dit examen kan ongescoorde vragen bevatten. Ze worden niet aangeduid en tellen niet mee; de tijdslimiet houdt er al rekening mee.',
    multipleChoice: 'Meerkeuze',
    min: 'min',
    rulesTitle: 'Examenregels',
    rules: [
      'De tijd begint zodra je op <b>Examen starten</b> klikt en kan niet worden gepauzeerd. De klok loopt door als je het venster sluit.',
      'Als de tijd om is, wordt het examen automatisch beëindigd en worden je antwoorden zoals ze op dat moment zijn nagekeken.',
      'Je ziet tijdens het examen <b>niet</b> of een antwoord goed is. Uitleg en juiste antwoorden volgen in het scorerapport.',
      'Onbeantwoorde vragen tellen als fout — gokken kost niets.',
      'Je kunt vragen <b>markeren voor review</b> en via het overzichtsscherm naar elke vraag terugkeren.',
      'Bij vragen met meerdere antwoorden staat in de vraag hoeveel je er moet kiezen. Alleen helemaal goed telt.',
      'De score is gewogen per examensectie (officiële blueprint). Vragen en antwoordvolgorde worden elke poging gehusseld.',
    ],
    controlsTitle: 'Bediening',
    keyChoose: 'antwoord kiezen / aan- of uitvinken',
    keyNav: 'vorige / volgende vraag',
    keyMark: 'markeren voor review',
    keyOverview: 'overzichtsscherm',
    rightClick: 'Rechtermuisklik',
    keyStrike: 'antwoord doorstrepen (wegstreepmethode)',
    agree: 'Ik heb de instructies gelezen en begrijp dat de tijd direct begint te lopen.',
    cancel: 'Annuleren',
    start: 'Examen starten',
    preparing: 'Examen wordt voorbereid…',
    questionOf: (p) => `Vraag ${p.i} van ${p.n}`,
    overview: 'Overzicht',
    remaining: 'Resterend',
    remainingTitle: 'Resterende tijd',
    close: 'Sluiten',
    timeWarning: (m) => `Let op: nog ${m} ${m === 1 ? 'minuut' : 'minuten'} resterend.`,
    markReview: 'Markeren voor review',
    answeredOf: (p) => `${p.a} van ${p.n} beantwoord`,
    reviewPos: (p) => ` · review ${p.i}/${p.n}`,
    smaller: 'Kleiner', larger: 'Groter',
    question: (i) => `Vraag ${i}`,
    hintMulti: (n) => `Kies ${n} antwoorden`,
    hintTF: 'Waar of niet waar',
    hintSingle: 'Kies één antwoord',
    multiFull: (n) => `Je hebt ${n} antwoorden gekozen. Vink er één uit om een ander antwoord te kiezen.`,
    tipStrike: 'Tip: rechtermuisklik op een antwoord om het door te strepen.',
    prev: '◀ Vorige',
    next: 'Volgende ▶',
    toOverview: 'Naar overzicht ▶',
    ovTitle: 'Overzicht van je antwoorden',
    ovIntro: 'Klik op een vraagnummer om ernaartoe te gaan, of loop een selectie door. Je kunt antwoorden wijzigen tot je het examen beëindigt.',
    answered: 'Beantwoord', unanswered: 'Onbeantwoord', marked: 'Gemarkeerd',
    tileTitle: (p) => `Vraag ${p.i}: ${p.answered ? 'beantwoord' : 'onbeantwoord'}${p.marked ? ', gemarkeerd' : ''}`,
    revAll: 'Alle vragen doorlopen',
    revUnanswered: (n) => `Onbeantwoorde doorlopen (${n})`,
    revMarked: (n) => `Gemarkeerde doorlopen (${n})`,
    backTo: (i) => `◀ Terug naar vraag ${i}`,
    endExam: 'Examen beëindigen',
    endTitle: 'Examen beëindigen?',
    endUnanswered: (n) => `<b>${n}</b> ${n === 1 ? 'vraag is' : 'vragen zijn'} nog niet beantwoord en ${n === 1 ? 'telt' : 'tellen'} als fout.`,
    endAllAnswered: 'Alle vragen zijn beantwoord.',
    endMarked: (n) => `<b>${n}</b> ${n === 1 ? 'vraag staat' : 'vragen staan'} gemarkeerd voor review.`,
    endFinal: 'Na het beëindigen kun je je antwoorden niet meer wijzigen.',
    backToExam: 'Terug naar examen',
    confirmEnd: 'Ja, examen beëindigen',
    timeUpTitle: 'De tijd is om',
    timeUpText: 'Je examentijd is verstreken. Je antwoorden zijn ingeleverd en nagekeken.',
    viewReport: 'Scorerapport bekijken',
    toStart: 'Naar start',
    scoreReport: 'Scorerapport',
    yourScore: 'Jouw score',
    passMarkNote: (n) => `Slagingsgrens ${n}% · gewogen per sectie`,
    statScore: 'Score',
    unscoredNote: (n) => `Er ${n === 1 ? 'zat' : 'zaten'} ${n} ongescoorde ${n === 1 ? 'vraag' : 'vragen'} in die niet ${n === 1 ? 'telde' : 'telden'}.`,
    unscoredTag: ' · ongescoord',
    objective: 'Doelstelling',
    sources: 'Documentatie',
    reportQ: 'Probleem met deze vraag melden',
    reported: '⚑ Gemeld — komt niet meer in examens (klik om te herstellen)',
    reportPrompt: 'Wat klopt er niet aan deze vraag? (hij komt dan niet meer in examens)',
    timedOut: 'Het examen is automatisch beëindigd omdat de tijd om was.',
    statCorrect: 'Goed', statWrong: 'Fout', statUnanswered: 'Onbeantwoord', statSpent: 'Bestede tijd',
    bySection: 'Resultaat per sectie',
    thSection: 'Sectie', thWeight: 'Gewicht',
    barNote: (n) => `Het streepje in elke balk is de slagingsgrens (${n}%).`,
    retryWrong: 'Foute vragen herkansen',
    showAnswers: 'Antwoorden en uitleg bekijken',
    hideAnswers: 'Antwoorden verbergen',
    answersTitle: 'Antwoorden en uitleg',
    tabWrong: (n) => `Fout (${n})`, tabMarked: (n) => `Gemarkeerd (${n})`, tabAll: (n) => `Alle (${n})`,
    noneInSel: 'Geen vragen in deze selectie.',
    yourAnswer: 'jouw antwoord',
    markedTag: ' · ⚑ gemarkeerd',
    vRight: '✓ Goed', vWrong: '✗ Fout', vUnanswered: '✗ Niet beantwoord',
    explanation: 'Uitleg',
    dur: (p) => (p.m ? `${p.m} min ${p.s} s` : `${p.s} s`),
  },
};

let lang = store.get('lang', 'en');
if (!I18N[lang]) lang = 'en';

/** Translate `key`; function entries are called with `arg`. */
function t(key, arg) {
  const v = I18N[lang][key] ?? I18N.en[key] ?? key;
  return typeof v === 'function' ? v(arg) : v;
}

function setLang(next) {
  if (!I18N[next] || next === lang) return;
  lang = next;
  store.set('lang', lang);
  document.documentElement.lang = lang;
  document.title = t('brand');
  rerender?.();
}

function langSelect() {
  return `<label class="lang" title="${esc(t('language'))}"><span aria-hidden="true">🌐</span>
    <select id="lang" aria-label="${esc(t('language'))}">${Object.entries(LANGS).map(([k, name]) =>
      `<option value="${k}" ${k === lang ? 'selected' : ''}>${name}</option>`).join('')}</select></label>`;
}
// One delegated listener survives every re-render.
document.addEventListener('change', (e) => { if (e.target.id === 'lang') setLang(e.target.value); });

function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return [h, m, s].map((n) => String(n).padStart(2, '0')).join(':');
}
const fmtDuration = (sec) => t('dur', { m: Math.floor(sec / 60), s: Math.round(sec % 60) });
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString(t('locale'), { dateStyle: 'medium', timeStyle: 'short' });
const fmtDay = (ts) => new Date(ts * 1000).toLocaleDateString(t('locale'), { day: 'numeric', month: 'short', year: 'numeric' });
const examTitle = (x) => (x.pool === 'retry' ? t('retryTitle') : x.title);

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function modal(html, bind) {
  modalRoot.innerHTML = `<div class="backdrop"><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`;
  const root = modalRoot.firstElementChild;
  bind?.(root, closeModal);
  root.querySelector('[data-autofocus]')?.focus();
}
function closeModal() { modalRoot.innerHTML = ''; }

function topbar(right = '') {
  return `<header class="topbar"><div class="brand"><span class="logo">✓</span> ${esc(t('brand'))}</div>
    <div class="spacer"></div>${right}${langSelect()}</header>`;
}

function showError(err) {
  rerender = () => showError(err);
  app.innerHTML = `${topbar()}<main class="page"><div class="panel">
    <h2>${esc(t('errorTitle'))}</h2><p>${esc(err.message || err)}</p>
    <div class="actions"><a class="btn" href="#/">${esc(t('backHome'))}</a></div></div></main>`;
}

// ----------------------------------------------------------------- router --

let teardown = null;   // cleanup of the current screen (timers, listeners)
let rerender = null;   // redraw the current screen (used on language change)

function route() {
  teardown?.(); teardown = null; rerender = null; closeModal();
  const [, view, id, sub] = location.hash.replace(/^#/, '').split('/');
  const run = view === 'exam' ? examScreen(id)
    : view === 'result' ? resultScreen(id)
    : view === 'start' ? instructionsScreen(decodeURIComponent(id || ''), decodeURIComponent(sub || ''))
    : homeScreen();
  Promise.resolve(run).catch(showError);
}
window.addEventListener('hashchange', route);
const go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };

function poolSubtitle(p) {
  if (p.kind === 'mixed') return t('poolMixed', { questions: p.questions, banks: p.bankCount, size: p.poolSize, fresh: p.poolFresh });
  if (p.kind === 'retry') return t('poolRetry', { n: p.questions });
  return t('poolBank', { file: p.file });
}
function poolDate(p) {
  if (!p.createdAt) return '';
  const key = p.kind === 'mixed' ? 'dateMixed' : p.kind === 'retry' ? 'dateRetry' : 'dateBank';
  return `<span title="${esc(fmtDate(p.createdAt))}">📅 ${esc(t(key, fmtDay(p.createdAt)))}</span>`;
}
const poolTitle = (p) => (p.kind === 'retry' ? t('retryTitle') : p.title);
const startHref = (p) => `#/start/${encodeURIComponent(p.exam)}/${encodeURIComponent(p.id)}`;

// ------------------------------------------------------------------- home --

let catalogCache = null;

async function homeScreen() {
  const { exams, attempts } = catalogCache = await api('GET', '/api/catalog');
  const running = attempts.filter((a) => a.status === 'running');
  const finished = attempts.filter((a) => a.status === 'finished');

  rerender = () => {
    app.innerHTML = `${topbar()}<main class="page">
    <h1>${esc(t('welcome'))}</h1>
    <p class="muted">${t('homeIntro')}</p>

    ${running.map((a) => `<div class="panel resume">
      <h2>${esc(t('inProgress'))}</h2>
      <p>${t('inProgressText', { title: esc(examTitle(a)), answered: a.answered, questions: a.questions, left: fmtClock(a.remainingSeconds) })}</p>
      <div class="actions"><a class="btn primary" href="#/exam/${a.id}">${esc(t('resume'))}</a></div>
    </div>`).join('')}

    ${exams.length ? exams.map((x) => `<div class="panel">
      <h2>${esc(x.title)}</h2>
      ${x.guideVersion ? `<p class="muted" style="margin-top:-6px">${esc(t('guideVersion', x.guideVersion))}</p>` : ''}
      ${x.pools.length ? x.pools.map((p) => `<div class="pool">
        <div class="info">
          <div class="name">${esc(poolTitle(p))}</div>
          <div class="meta">${esc(poolSubtitle(p))}</div>
          <div class="facts"><span>${esc(t('nQuestions', p.questions))}</span><span>${esc(t('nMinutes', p.minutes))}</span><span>${esc(t('passFrom', p.passingScore))}</span>${poolDate(p)}</div>
        </div>
        <a class="btn ${p.id === 'mixed' ? 'primary' : ''}" href="${startHref(p)}">${esc(t('select'))}</a>
      </div>`).join('') : `<p class="muted">${t('noBanks', esc(x.slug))}</p>`}
    </div>`).join('') : `<div class="panel"><p class="muted">${t('noExams')}</p></div>`}

    <div class="panel">
      <h2>${esc(t('history'))}</h2>
      ${finished.length ? `<div class="table-wrap"><table class="attempts">
        <thead><tr><th>${esc(t('thDate'))}</th><th>${esc(t('thExam'))}</th><th class="num">${esc(t('thScore'))}</th><th>${esc(t('thResult'))}</th>
          <th class="num">${esc(t('thCorrect'))}</th><th class="num">${esc(t('thTime'))}</th><th></th></tr></thead>
        <tbody>${finished.map((a) => `<tr>
          <td>${fmtDate(a.startedAt)}</td><td>${esc(examTitle(a))}</td>
          <td class="num"><b>${a.score.toFixed(1)}%</b></td>
          <td><span class="badge ${a.passed ? 'pass' : 'fail'}">${esc(t(a.passed ? 'PASS' : 'FAIL'))}</span></td>
          <td class="num">${a.rawCorrect}/${a.scored ?? a.questions}</td><td class="num">${fmtDuration(a.durationSeconds)}</td>
          <td class="row-actions"><a class="btn small" href="#/result/${a.id}">${esc(t('report'))}</a>
            <button class="btn small ghost" data-delete="${a.id}" title="${esc(t('delete'))}" aria-label="${esc(t('delete'))}">🗑</button></td></tr>`).join('')}</tbody>
      </table></div>` : `<p class="muted">${esc(t('noAttempts'))}</p>`}
    </div>
  </main>`;
    app.querySelectorAll('[data-delete]').forEach((el) =>
      el.addEventListener('click', () => confirmDelete(finished.find((a) => a.id === el.dataset.delete))));
  };
  rerender();
}

function confirmDelete(a) {
  modal(`<h2>${esc(t('deleteTitle'))}</h2>
    <p>${t('deleteText', { title: esc(examTitle(a)), date: esc(fmtDate(a.startedAt)), score: a.score.toFixed(1) })}</p>
    <p class="error" id="del-error" hidden></p>
    <div class="actions"><button class="btn" data-autofocus id="cancel">${esc(t('cancel'))}</button>
      <button class="btn danger" id="confirm">${esc(t('confirmDelete'))}</button></div>`,
  (root, close) => {
    root.querySelector('#cancel').onclick = close;
    root.querySelector('#confirm').onclick = async (e) => {
      e.target.disabled = true;
      try {
        await api('DELETE', `/api/sessions/${a.id}`);
        close();
        route();
      } catch (err) {
        const box = root.querySelector('#del-error');
        box.textContent = err.message; box.hidden = false;
        e.target.disabled = false;
      }
    };
  });
}

// ----------------------------------------------------------- instructions --

async function instructionsScreen(slug, poolId) {
  const { exams } = catalogCache || await api('GET', '/api/catalog');
  const pool = exams.find((x) => x.slug === slug)?.pools.find((p) => p.id === poolId);
  if (!pool) { go('#/'); return; }
  let agreed = false;
  const canUnscored = pool.kind === 'mixed' && pool.unscoredMax > 0;
  let unscored = canUnscored && store.get('unscored', true);

  rerender = () => {
    app.innerHTML = `${topbar()}<main class="page"><div class="panel">
    <h1>${esc(poolTitle(pool))}</h1>
    <p class="muted">${esc(poolSubtitle(pool))}</p>
    <div class="stats">
      <div class="stat"><div class="k">${esc(t('statQuestions'))}</div><div class="v">${pool.questions}${unscored ? `<small class="muted" style="display:block;font-size:12px;font-weight:400">${esc(t('plusUnscored', pool.unscoredMax))}</small>` : ''}</div></div>
      <div class="stat"><div class="k">${esc(t('statTime'))}</div><div class="v">${pool.minutes} ${esc(t('min'))}</div></div>
      <div class="stat"><div class="k">${esc(t('statPass'))}</div><div class="v">${pool.passingScore}%</div></div>
      <div class="stat"><div class="k">${esc(t('statType'))}</div><div class="v">${esc(t('multipleChoice'))}</div></div>
    </div>

    <h2 style="margin-top:24px">${esc(t('rulesTitle'))}</h2>
    <ul class="rules">${t('rules').map((r) => `<li>${r}</li>`).join('')}${unscored ? `<li>${esc(t('unscoredRule'))}</li>` : ''}</ul>

    <h2 style="margin-top:24px">${esc(t('controlsTitle'))}</h2>
    <div class="keys">
      <span><kbd>A</kbd>–<kbd>F</kbd></span><span>${esc(t('keyChoose'))}</span>
      <span><kbd>←</kbd> <kbd>→</kbd></span><span>${esc(t('keyNav'))}</span>
      <span><kbd>M</kbd></span><span>${esc(t('keyMark'))}</span>
      <span><kbd>O</kbd></span><span>${esc(t('keyOverview'))}</span>
      <span>${esc(t('rightClick'))}</span><span>${esc(t('keyStrike'))}</span>
    </div>

    ${canUnscored ? `<label class="agree"><input type="checkbox" id="unscored" ${unscored ? 'checked' : ''}>
      <span>${esc(t('unscoredOpt', pool.unscoredMax))}</span></label>` : ''}
    <label class="agree"><input type="checkbox" id="agree" ${agreed ? 'checked' : ''}>
      <span>${esc(t('agree'))}</span></label>
    <div class="actions">
      <a class="btn" href="#/">${esc(t('cancel'))}</a>
      <button class="btn primary" id="start" ${agreed ? '' : 'disabled'}>${esc(t('start'))}</button>
    </div>
  </div></main>`;

    const agree = app.querySelector('#agree'), start = app.querySelector('#start');
    agree.addEventListener('change', () => { agreed = agree.checked; start.disabled = !agreed; });
    app.querySelector('#unscored')?.addEventListener('change', (e) => {
      unscored = e.target.checked; store.set('unscored', unscored); rerender();
    });
    start.addEventListener('click', async () => {
      start.disabled = true;
      start.textContent = t('preparing');
      try {
        const s = await api('POST', '/api/sessions', { exam: pool.exam, pool: pool.id, unscored });
        go(`#/exam/${s.id}`);
      } catch (e) { showError(e); }
    });
  };
  rerender();
}

// ------------------------------------------------------------------- exam --

async function examScreen(id) {
  const s = await api('GET', `/api/sessions/${id}`);
  if (s.status === 'finished') { go(`#/result/${id}`); return; }

  const qs = s.questions;
  const answers = s.answers || {};
  const flagged = new Set(s.flagged || []);
  const struckKey = `struck:${id}`;
  const struck = store.get(struckKey, {});           // { qid: [choiceId] } — visual only
  let zoom = store.get('zoom', 1);
  let idx = Math.min(s.current || 0, qs.length - 1);
  let mode = 'question';                              // 'question' | 'overview'
  let sequence = null;                                // filtered review path (array of indexes)
  let deadline = Date.now() + s.remainingSeconds * 1000;
  const warned = new Set();
  let bannerMins = 0;                                 // minutes in the active time warning
  let finishing = false;

  document.documentElement.style.setProperty('--zoom', zoom);

  // -- persistence ------------------------------------------------------- //
  let saveTimer = null, saving = Promise.resolve();
  const payload = () => ({ answers, flagged: [...flagged], current: idx });
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saving = saving.then(() => api('POST', `/api/sessions/${id}/progress`, payload()))
        .then((r) => { if (r.status === 'finished') onFinished('timeout'); })
        .catch(() => { /* retried on next change / submit */ });
    }, 250);
  }

  async function submit(reason) {
    if (finishing) return;
    finishing = true;
    clearTimeout(saveTimer);
    try {
      await saving;
      await api('POST', `/api/sessions/${id}/submit`, payload());
    } catch { /* server auto-grades on timeout anyway */ }
    onFinished(reason);
  }

  function onFinished(reason) {
    finishing = true;
    window.removeEventListener('beforeunload', beforeUnload);
    if (reason === 'timeout') {
      modal(`<h2>${esc(t('timeUpTitle'))}</h2>
        <p>${esc(t('timeUpText'))}</p>
        <div class="actions"><button class="btn primary" data-autofocus id="ok">${esc(t('viewReport'))}</button></div>`,
      (root, close) => { root.querySelector('#ok').onclick = () => { close(); go(`#/result/${id}`); }; });
    } else {
      go(`#/result/${id}`);
    }
  }

  // -- answering --------------------------------------------------------- //
  const isAnswered = (q) => {
    const a = answers[q.id];
    return Array.isArray(a) ? a.length > 0 : a != null;
  };

  function choose(q, cid) {
    if (q.type === 'multiple') {
      const cur = new Set(answers[q.id] || []);
      if (cur.has(cid)) cur.delete(cid);
      else if (cur.size < q.selectCount) cur.add(cid);
      else return;                                     // already at the limit
      if (cur.size) answers[q.id] = [...cur]; else delete answers[q.id];
    } else {
      answers[q.id] = cid;
    }
    // Picking a choice un-strikes it.
    if (struck[q.id]?.includes(cid)) {
      struck[q.id] = struck[q.id].filter((c) => c !== cid);
      store.set(struckKey, struck);
    }
    save();
    render();
  }

  function toggleStrike(q, cid) {
    const list = new Set(struck[q.id] || []);
    if (list.has(cid)) list.delete(cid); else list.add(cid);
    struck[q.id] = [...list];
    store.set(struckKey, struck);
    render();
  }

  function toggleFlag() {
    const q = qs[idx];
    if (flagged.has(q.id)) flagged.delete(q.id); else flagged.add(q.id);
    save();
    render();
  }

  // -- navigation -------------------------------------------------------- //
  function goTo(i) { idx = i; mode = 'question'; save(); render(); app.querySelector('.exam-body')?.scrollTo(0, 0); }
  function step(dir) {
    if (sequence) {
      const pos = sequence.indexOf(idx) + dir;
      if (pos < 0) return;
      if (pos >= sequence.length) { openOverview(); return; }
      goTo(sequence[pos]);
      return;
    }
    const next = idx + dir;
    if (next < 0) return;
    if (next >= qs.length) { openOverview(); return; }
    goTo(next);
  }
  function openOverview() { mode = 'overview'; sequence = null; save(); render(); }
  function startSequence(indexes) {
    if (!indexes.length) return;
    sequence = indexes;
    goTo(indexes[0]);
  }

  // -- timer ------------------------------------------------------------- //
  const totalSec = s.timeLimitMinutes * 60;
  function tick() {
    const left = (deadline - Date.now()) / 1000;
    const el = app.querySelector('#timer');
    if (el) {
      el.querySelector('span').textContent = fmtClock(left);
      el.classList.toggle('crit', left <= 300);
      el.classList.toggle('warn', left > 300 && left <= 900);
    }
    for (const mins of [15, 5, 1]) {
      if (totalSec > mins * 60 * 2 && left <= mins * 60 && !warned.has(mins)) {
        warned.add(mins);
        bannerMins = mins;
        render();
      }
    }
    if (left <= 0) submit('timeout');
  }
  const timerId = setInterval(tick, 500);

  // Re-sync with the server clock when the tab becomes visible again.
  async function resync() {
    if (document.visibilityState !== 'visible' || finishing) return;
    try {
      const fresh = await api('GET', `/api/sessions/${id}`);
      if (fresh.status === 'finished') onFinished('timeout');
      else deadline = Date.now() + fresh.remainingSeconds * 1000;
    } catch { /* offline: keep local clock */ }
  }
  document.addEventListener('visibilitychange', resync);

  function beforeUnload(e) { e.preventDefault(); e.returnValue = ''; }
  window.addEventListener('beforeunload', beforeUnload);

  // -- keyboard ---------------------------------------------------------- //
  function onKey(e) {
    if (finishing || modalRoot.firstChild || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.matches?.('input[type=text], textarea, select')) return;
    const k = e.key.toLowerCase();
    if (mode !== 'question') return;
    const q = qs[idx];
    const n = 'abcdef'.indexOf(k);
    if (k.length === 1 && n >= 0 && n < q.choices.length) { e.preventDefault(); choose(q, q.choices[n].id); return; }
    if (k === 'arrowright') { e.preventDefault(); step(1); return; }
    if (k === 'arrowleft') { e.preventDefault(); step(-1); return; }
    if (k === 'm') { e.preventDefault(); toggleFlag(); return; }
    if (k === 'o') { e.preventDefault(); openOverview(); }
  }
  document.addEventListener('keydown', onKey);

  teardown = () => {
    clearInterval(timerId);
    clearTimeout(saveTimer);
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('visibilitychange', resync);
    window.removeEventListener('beforeunload', beforeUnload);
    document.documentElement.style.setProperty('--zoom', 1);
  };

  // -- rendering --------------------------------------------------------- //
  function header() {
    const left = (deadline - Date.now()) / 1000;
    const counter = mode === 'question' ? t('questionOf', { i: idx + 1, n: qs.length }) : t('overview');
    return `<header class="topbar">
      <div class="brand"><span class="logo">✓</span></div>
      <div class="exam-title">${esc(examTitle(s))}</div>
      <div class="spacer"></div>
      <div class="qcounter">${esc(counter)}</div>
      <div class="timer" id="timer" title="${esc(t('remainingTitle'))}"><small>${esc(t('remaining'))}</small><span>${fmtClock(left)}</span></div>
      ${langSelect()}
    </header>
    ${bannerMins ? `<div class="banner"><span>⏱ ${esc(t('timeWarning', bannerMins))}</span><button class="btn small ghost" id="dismiss">${esc(t('close'))}</button></div>` : ''}`;
  }

  function questionView() {
    const q = qs[idx];
    const multi = q.type === 'multiple';
    const picked = new Set(multi ? (answers[q.id] || []) : [answers[q.id]]);
    const full = multi && picked.size >= q.selectCount;
    const hint = multi ? t('hintMulti', q.selectCount) : q.type === 'trueFalse' ? t('hintTF') : t('hintSingle');
    const answeredCount = qs.filter(isAnswered).length;
    const struckSet = new Set(struck[q.id] || []);
    const inSeq = sequence ? t('reviewPos', { i: sequence.indexOf(idx) + 1, n: sequence.length }) : '';
    const atStart = sequence ? sequence.indexOf(idx) <= 0 : idx === 0;
    const atEnd = sequence ? sequence.indexOf(idx) === sequence.length - 1 : idx === qs.length - 1;

    return `<div class="toolbar">
        <label class="flagbox ${flagged.has(q.id) ? 'on' : ''}"><input type="checkbox" id="flag" ${flagged.has(q.id) ? 'checked' : ''}> ⚑ ${esc(t('markReview'))}</label>
        <div class="spacer" style="flex:1"></div>
        <span class="progress-text">${esc(t('answeredOf', { a: answeredCount, n: qs.length }) + inSeq)}</span>
        <div class="zoom"><button class="btn small" id="zoomout" title="${esc(t('smaller'))}">A−</button><button class="btn small" id="zoomin" title="${esc(t('larger'))}">A+</button></div>
      </div>
      <div class="exam-body"><div class="question">
        <div class="qhead"><span>${esc(t('question', idx + 1))}</span><span class="qhint">${esc(hint)}</span></div>
        <div class="prompt">${md(q.prompt)}</div>
        <div class="choices" role="${multi ? 'group' : 'radiogroup'}">
          ${q.choices.map((c, i) => {
            const sel = picked.has(c.id);
            const dis = full && !sel;
            return `<label class="choice ${sel ? 'selected' : ''} ${struckSet.has(c.id) ? 'struck' : ''} ${dis ? 'disabled' : ''}" data-cid="${esc(c.id)}">
              <input type="${multi ? 'checkbox' : 'radio'}" name="ans" ${sel ? 'checked' : ''} ${dis ? 'disabled' : ''}>
              <span class="letter">${LETTERS[i]}.</span><span class="text">${md(c.text)}</span></label>`;
          }).join('')}
        </div>
        ${multi && full ? `<p class="tip">${esc(t('multiFull', q.selectCount))}</p>` : ''}
        <p class="tip">${esc(t('tipStrike'))}</p>
      </div></div>
      <footer class="footer">
        <button class="btn" id="prev" ${atStart ? 'disabled' : ''}>${esc(t('prev'))}</button>
        <div class="spacer"></div>
        <button class="btn" id="overview">${esc(t('overview'))}</button>
        <div class="spacer"></div>
        <button class="btn primary" id="next">${esc(t(atEnd ? 'toOverview' : 'next'))}</button>
      </footer>`;
  }

  function overviewView() {
    const unanswered = qs.map((q, i) => (isAnswered(q) ? -1 : i)).filter((i) => i >= 0);
    const marked = qs.map((q, i) => (flagged.has(q.id) ? i : -1)).filter((i) => i >= 0);
    return `<div class="exam-body"><div class="overview">
        <h2>${esc(t('ovTitle'))}</h2>
        <p class="muted">${esc(t('ovIntro'))}</p>
        <div class="legend">
          <span><i class="dot answered"></i> ${esc(t('answered'))} (${qs.length - unanswered.length})</span>
          <span><i class="dot"></i> ${esc(t('unanswered'))} (${unanswered.length})</span>
          <span><i class="dot flagged"></i> ${esc(t('marked'))} (${marked.length})</span>
        </div>
        <div class="grid">${qs.map((q, i) => `<button class="tile ${isAnswered(q) ? 'answered' : ''} ${flagged.has(q.id) ? 'flagged' : ''}" data-i="${i}"
          title="${esc(t('tileTitle', { i: i + 1, answered: isAnswered(q), marked: flagged.has(q.id) }))}">${i + 1}</button>`).join('')}</div>
        <div class="review-buttons">
          <button class="btn" id="rev-all">${esc(t('revAll'))}</button>
          <button class="btn" id="rev-unanswered" ${unanswered.length ? '' : 'disabled'}>${esc(t('revUnanswered', unanswered.length))}</button>
          <button class="btn" id="rev-marked" ${marked.length ? '' : 'disabled'}>${esc(t('revMarked', marked.length))}</button>
        </div>
      </div></div>
      <footer class="footer">
        <button class="btn" id="back">${esc(t('backTo', idx + 1))}</button>
        <div class="spacer"></div>
        <button class="btn danger" id="end">${esc(t('endExam'))}</button>
      </footer>`;
  }

  function confirmEnd() {
    const unanswered = qs.filter((q) => !isAnswered(q)).length;
    const marked = flagged.size;
    modal(`<h2>${esc(t('endTitle'))}</h2>
      <p>${unanswered ? t('endUnanswered', unanswered) : esc(t('endAllAnswered'))}</p>
      ${marked ? `<p>${t('endMarked', marked)}</p>` : ''}
      <p>${esc(t('endFinal'))}</p>
      <div class="actions"><button class="btn" data-autofocus id="cancel">${esc(t('backToExam'))}</button>
        <button class="btn danger" id="confirm">${esc(t('confirmEnd'))}</button></div>`,
    (root, close) => {
      root.querySelector('#cancel').onclick = close;
      root.querySelector('#confirm').onclick = () => { close(); submit('submitted'); };
    });
  }

  function render() {
    app.innerHTML = `<div class="exam">${header()}${mode === 'question' ? questionView() : overviewView()}</div>`;
    tick();
    app.querySelector('#dismiss')?.addEventListener('click', () => { bannerMins = 0; render(); });

    if (mode === 'question') {
      const q = qs[idx];
      app.querySelectorAll('.choice').forEach((el) => {
        const cid = el.dataset.cid;
        el.addEventListener('click', (e) => { e.preventDefault(); if (!el.classList.contains('disabled')) choose(q, cid); });
        el.addEventListener('contextmenu', (e) => { e.preventDefault(); toggleStrike(q, cid); });
      });
      app.querySelector('#flag').addEventListener('change', toggleFlag);
      app.querySelector('#prev').addEventListener('click', () => step(-1));
      app.querySelector('#next').addEventListener('click', () => step(1));
      app.querySelector('#overview').addEventListener('click', openOverview);
      app.querySelector('#zoomin').addEventListener('click', () => setZoom(zoom + 0.1));
      app.querySelector('#zoomout').addEventListener('click', () => setZoom(zoom - 0.1));
    } else {
      app.querySelectorAll('.tile').forEach((el) => el.addEventListener('click', () => { sequence = null; goTo(Number(el.dataset.i)); }));
      app.querySelector('#rev-all').addEventListener('click', () => startSequence(qs.map((_, i) => i)));
      app.querySelector('#rev-unanswered').addEventListener('click', () =>
        startSequence(qs.map((q, i) => (isAnswered(q) ? -1 : i)).filter((i) => i >= 0)));
      app.querySelector('#rev-marked').addEventListener('click', () =>
        startSequence(qs.map((q, i) => (flagged.has(q.id) ? i : -1)).filter((i) => i >= 0)));
      app.querySelector('#back').addEventListener('click', () => goTo(idx));
      app.querySelector('#end').addEventListener('click', confirmEnd);
    }
  }

  function setZoom(z) {
    zoom = Math.min(1.6, Math.max(0.8, Math.round(z * 10) / 10));
    store.set('zoom', zoom);
    document.documentElement.style.setProperty('--zoom', zoom);
  }

  rerender = () => { if (!finishing) render(); };
  render();
}

// ---------------------------------------------------------------- results --

async function resultScreen(id) {
  const s = await api('GET', `/api/sessions/${id}`);
  if (s.status !== 'finished') { go(`#/exam/${id}`); return; }
  const r = s.result;
  const wrong = r.review.filter((x) => !x.correct);
  const flaggedItems = r.review.filter((x) => x.flagged);
  let filter = 'wrong';
  let showReview = false;

  function sectionRows() {
    return r.sections.map((row) => `<tr>
      <td>${esc(row.section)}</td>
      <td class="num">${row.weight}%</td>
      <td style="width:34%"><div class="bar" title="${row.pct.toFixed(1)}%"><i class="${row.pct < r.passingScore ? 'low' : ''}" style="width:${row.pct}%"></i>
        <span class="mark" style="left:${r.passingScore}%"></span></div></td>
      <td class="num"><b>${row.pct.toFixed(0)}%</b></td>
      <td class="num">${row.correct}/${row.total}</td></tr>`).join('');
  }

  async function toggleReport(x) {
    let reason = null;
    if (!x.reported) {
      reason = window.prompt(t('reportPrompt'), '');
      if (reason === null) return;                       // cancelled
    }
    const res = await api('POST', `/api/sessions/${id}/report`, { question: x.id, reported: !x.reported, reason });
    x.reported = res.reported;
    render();
  }

  function reviewItem(x) {
    const given = new Set(x.given);
    const choices = x.choices.map((c, i) => {
      const picked = given.has(c.id);
      const cls = c.correct ? 'correct' : picked ? 'picked-wrong' : '';
      const mk = c.correct ? '✓' : picked ? '✗' : '';
      return `<div class="rchoice ${cls}"><span class="mk">${mk}</span><span class="letter"><b>${LETTERS[i]}.</b></span>
        <span class="text">${md(c.text)}</span>${picked ? `<span class="yours">${esc(t('yourAnswer'))}</span>` : ''}</div>`;
    }).join('');
    return `<article class="ritem ${x.correct ? 'right' : 'wrong'}">
      <div class="rhead"><span>${esc(t('question', x.number))} · ${esc(x.section)}${x.unscored ? esc(t('unscoredTag')) : ''}${x.flagged ? esc(t('markedTag')) : ''}</span>
        <span class="verdict-tag">${esc(t(x.correct ? 'vRight' : x.answered ? 'vWrong' : 'vUnanswered'))}</span></div>
      <div class="prompt">${md(x.prompt)}</div>
      ${choices}
      ${x.explanation ? `<div class="explain"><b>${esc(t('explanation'))}</b>${md(x.explanation)}</div>` : ''}
      ${x.objective || x.sources?.length ? `<div class="explain muted" style="font-size:13px">
        ${x.objective ? `<div><b>${esc(t('objective'))}</b> ${esc(x.objective)}</div>` : ''}
        ${x.sources?.length ? `<div><b>${esc(t('sources'))}</b> ${x.sources.filter((u) => /^https:\/\//.test(u)).map((u) =>
          `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u.replace(/^https:\/\//, ''))}</a>`).join(' · ')}</div>` : ''}
      </div>` : ''}
      <div class="actions" style="margin-top:8px"><button class="btn small ghost" data-report="${esc(x.id)}">${esc(t(x.reported ? 'reported' : 'reportQ'))}</button></div>
    </article>`;
  }

  function render() {
    const items = filter === 'wrong' ? wrong : filter === 'flagged' ? flaggedItems : r.review;
    const passed = r.passed;
    app.innerHTML = `${topbar(`<a class="btn small" href="#/">${esc(t('toStart'))}</a>`)}<main class="page">
      <div class="panel">
        <p class="muted" style="margin:0 0 4px">${esc(t('scoreReport'))} · ${fmtDate(s.startedAt)}</p>
        <h1>${esc(examTitle(s))}</h1>
        <div class="verdict" style="margin-top:14px">
          <div class="big ${passed ? 'pass' : 'fail'}">${esc(t(passed ? 'PASS' : 'FAIL'))}</div>
          <div class="scorebox"><span class="muted">${esc(t('passMarkNote', r.passingScore))}</span></div>
        </div>
        ${r.reason === 'timeout' ? `<p class="muted">${esc(t('timedOut'))}</p>` : ''}
        ${r.unscored ? `<p class="muted">${esc(t('unscoredNote', r.unscored))}</p>` : ''}
      </div>

      <div class="panel">
        <h2>${esc(t('bySection'))}</h2>
        <div class="table-wrap"><table class="sections">
          <thead><tr><th>${esc(t('thSection'))}</th><th class="num">${esc(t('thWeight'))}</th><th></th><th class="num">${esc(t('thScore'))}</th><th class="num">${esc(t('thCorrect'))}</th></tr></thead>
          <tbody>${sectionRows()}</tbody></table></div>
        <p class="muted" style="font-size:13px;margin-bottom:0">${esc(t('barNote', r.passingScore))}</p>
        <div class="stats">
          <div class="stat"><div class="k">${esc(t('statScore'))}</div><div class="v">${r.weightedScore.toFixed(1)}%</div></div>
          <div class="stat"><div class="k">${esc(t('statCorrect'))}</div><div class="v">${r.rawCorrect} / ${r.total}</div></div>
          <div class="stat"><div class="k">${esc(t('statWrong'))}</div><div class="v">${r.total - r.rawCorrect}</div></div>
          <div class="stat"><div class="k">${esc(t('statUnanswered'))}</div><div class="v">${r.total - r.answered}</div></div>
          <div class="stat"><div class="k">${esc(t('statSpent'))}</div><div class="v">${fmtDuration(r.durationSeconds)}</div></div>
        </div>
        <div class="actions" style="margin-top:18px">
          ${wrong.length ? `<a class="btn" href="#/start/${encodeURIComponent(s.exam)}/retry">${esc(t('retryWrong'))}</a>` : ''}
          <button class="btn primary" id="toggle">${esc(t(showReview ? 'hideAnswers' : 'showAnswers'))}</button>
        </div>
      </div>

      ${showReview ? `<div class="panel" id="review">
        <h2>${esc(t('answersTitle'))}</h2>
        <div class="tabs">
          <button class="tab ${filter === 'wrong' ? 'on' : ''}" data-f="wrong">${esc(t('tabWrong', wrong.length))}</button>
          <button class="tab ${filter === 'flagged' ? 'on' : ''}" data-f="flagged">${esc(t('tabMarked', flaggedItems.length))}</button>
          <button class="tab ${filter === 'all' ? 'on' : ''}" data-f="all">${esc(t('tabAll', r.review.length))}</button>
        </div>
        ${items.length ? items.map(reviewItem).join('') : `<p class="muted">${esc(t('noneInSel'))}</p>`}
      </div>` : ''}
    </main>`;

    app.querySelector('#toggle').addEventListener('click', () => {
      showReview = !showReview; render();
      if (showReview) app.querySelector('#review')?.scrollIntoView({ behavior: 'smooth' });
    });
    app.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => { filter = tab.dataset.f; render(); }));
    app.querySelectorAll('[data-report]').forEach((el) => el.addEventListener('click', () =>
      toggleReport(r.review.find((x) => x.id === el.dataset.report)).catch(showError)));
  }

  catalogCache = null;   // retry bank may have changed
  rerender = render;
  render();
}

document.documentElement.lang = lang;
document.title = t('brand');
route();
