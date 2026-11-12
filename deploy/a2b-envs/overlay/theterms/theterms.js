/**
 * TheTerms by Lenni inside the demo workspace (#134). Staging only.
 *
 * The demo lists TheTerms as a product and opens it to a placeholder card. On
 * staging, this file replaces that card with a working calculator, so Doak can
 * try it before it goes anywhere else:
 *
 *   Quote     the loan officer's quote, phone first (#139): one of the bank's
 *             products, typed or spoken, coverage options side by side, the
 *             Truth in Lending box and the amortization table, then a link
 *             for the customer's phone
 *   Sent      who sent which quote to whom
 *   Website   the bank's website calculator (#140) with its embed code
 *   Settings  the bank's products, ranges, fees, coverage and disclosures (#137)
 *
 * The numbers come from engine.js (#136), the same file the customer's quote
 * page and the website calculator load. Branding is the bank's logo and colors
 * from Admin -> General (#130); there is no second brand setup.
 *
 * What is staged rather than real: the bank's settings and the sent-quote log
 * live in this browser, texting shows the message instead of sending it (#143),
 * and the AI endpoint is shown as the response it would give (#138).
 *
 * Nothing here edits a file in the document root, for the same reason as the
 * other overlays: staging is re-mirrored from sandbox every minute. nginx serves
 * this from /opt/a2b-envs/overlay/staging/theterms and injects it on staging
 * only.
 */
(function () {
  'use strict';

  var E = window.TheTermsEngine;
  var V = window.TheTermsView;
  if (!E || !V) return;

  var LOG = '[theterms]';
  var BASE = '/theterms/';
  var PANEL = 'loan-calculator';
  var STORE = 'theterms.v1.';
  // Loaded after mock-api.js and before the sign-in overlay, so this is the
  // demo's own API. Organisation reads here must stay inside the demo.
  var demoFetch = window.fetch;

  var esc = V.esc;
  var money = V.money;

  var ctx = null;      // the organisation and the person signed in
  var data = null;     // { settings, log, sent } for that organisation
  var draft = null;    // settings being edited
  var form = null;     // the quote being built
  var calc = null;     // { product, options: [...], refusals, errors }
  var stale = false;
  var tab = 'quote';
  var showAll = false;
  var host = null;
  var mounting = false;

  function warn() { try { console.warn.apply(console, [LOG].concat([].slice.call(arguments))); } catch (e) {} }
  function $(sel, root) { return (root || host || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || host || document).querySelectorAll(sel)); }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function uid() { return Math.random().toString(36).slice(2, 10); }
  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(String(v).replace(/[$,%\s]/g, ''));
    return isFinite(n) ? n : null;
  }
  function fmtNum(n) { return n === null || n === undefined || n === '' ? '' : String(n); }
  function fmtMoney(n) {
    var v = num(n);
    return v === null ? fmtNum(n) : v.toLocaleString('en-US', { maximumFractionDigits: 2 });
  }
  function dollars(n) { return n === null || n === undefined || n === '' ? '' : money(Math.round(Number(n) * 100), { noCents: Number(n) % 1 === 0 }); }
  function stamp(iso) {
    var d = new Date(iso);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  /* ------------------------------------------------------------ the demo */

  function demoJson(path) {
    return Promise.resolve(window.__A2B_MOCK_READY__).then(function () {
      return demoFetch.call(window, path, { headers: { 'Content-Type': 'application/json' } });
    }).then(function (r) {
      if (!r.ok) throw new Error(path + ' answered ' + r.status);
      return r.json();
    });
  }

  function pathParts() { return location.pathname.split('/').filter(Boolean).map(decodeURIComponent); }
  function onPanel() { var p = pathParts(); return p.length >= 2 && p[p.length - 1] === PANEL; }
  function orgSlug() { var p = pathParts(); return p[p.length - 2]; }
  function panelPath(panel) {
    var p = location.pathname.split('/');
    p[p.length - 1] = panel;
    return p.join('/');
  }

  function brandOf(vendor) {
    var b = (vendor && vendor.brand) || {};
    var css = getComputedStyle(document.documentElement);
    var logo = b.logo || (function () {
      var img = document.querySelector('#ws-org-foot-mark img');
      return img ? img.getAttribute('src') : '';
    })();
    return {
      logo: logo || '',
      primary: b.primary || css.getPropertyValue('--org-primary').trim(),
      accent: b.accent || css.getPropertyValue('--org-accent').trim(),
    };
  }

  /**
   * Who is signed in and which organisation this is. Only the demo's reads
   * with no side effects: opening the organisation itself writes to its
   * activity log, so its offerings come from agents-profile instead.
   */
  function loadContext(slug) {
    return demoJson('/api/auth/me').then(function (me) {
      if (!me || !me.user) throw new Error('Sign in to use TheTerms.');
      var mine = (me.vendors || []).filter(function (v) { return v.slug === slug; })[0];
      var find = mine ? Promise.resolve(mine) : !me.staff ? Promise.resolve(null)
        : demoJson('/api/vendors?all=1').then(function (d) {
          return (d.vendors || []).filter(function (v) { return v.slug === slug; })[0] || null;
        });
      return find.then(function (v) {
        if (!v) throw new Error('This organisation isn\u2019t open to you.');
        return demoJson('/api/vendors/' + encodeURIComponent(v.id) + '/agents-profile').then(function (prof) {
          var vendor = prof.vendor || v;
          return {
            slug: slug,
            vendor: vendor,
            user: me.user,
            staff: !!me.staff,
            role: mine ? mine.role : 'staff',
            canEdit: !!me.staff || !!(mine && mine.role === 'admin'),
            offerings: (prof.products || []).map(function (p) {
              return { name: p.name, slug: p.slug || p.id, status: p.status };
            }),
            brand: brandOf(vendor),
          };
        });
      });
    });
  }

  /* ------------------------------------------------------------ the bank's settings */

  var BASE_PRODUCT = {
    name: 'Loan', offering: '', loanClass: 'consumer',
    types: ['installment'], defaultType: 'installment',
    frequencies: ['monthly'], defaultFrequency: 'monthly',
    rate: 7, rateMin: 5, rateMax: 12,
    amount: 10000, amountMin: 1000, amountMax: 100000,
    payments: 36, paymentsMin: 6, paymentsMax: 84,
    termMonths: 12, termMonthsMin: 3, termMonthsMax: 24,
    balloonMethod: 'amortization', amortization: 240, balloonAmount: '', balloonStyle: 'last',
    calendar: 'periodic', rounding: 'nearest', oddDays: 'first',
    fees: [], insurance: false, coverage: [], website: false,
  };

  // Sample terms the demo bank starts with, by the kind of loan an offering
  // is. The bank changes them in Settings; nothing here is anyone's real rate.
  var SAMPLES = [
    { test: /auto|vehicle|\bcar\b/i, p: {
      loanClass: 'consumer', rate: 6.49, rateMin: 5.49, rateMax: 9.99,
      amount: 28000, amountMin: 5000, amountMax: 75000, payments: 60, paymentsMin: 24, paymentsMax: 84,
      fees: [{ name: 'Loan processing fee', kind: 'flat', value: 95, financed: true, financeCharge: true }],
      insurance: true, coverage: ['life', 'life-disability'], website: true } },
    { test: /multifamily|apartment/i, p: {
      loanClass: 'business', types: ['balloon'], defaultType: 'balloon', rate: 6.75, rateMin: 6, rateMax: 8.25,
      amount: 2000000, amountMin: 500000, amountMax: 10000000, payments: 120, paymentsMin: 60, paymentsMax: 120, amortization: 360,
      fees: [{ name: 'Loan fee', kind: 'percent', value: 0.5, financed: false, financeCharge: true }] } },
    { test: /\bcre\b|real estate|owner.occupied/i, p: {
      loanClass: 'business', types: ['balloon', 'installment'], defaultType: 'balloon', rate: 7, rateMin: 6.25, rateMax: 8.75,
      amount: 750000, amountMin: 100000, amountMax: 5000000, payments: 60, paymentsMin: 36, paymentsMax: 300, amortization: 240,
      fees: [{ name: 'Loan fee', kind: 'percent', value: 0.75, financed: false, financeCharge: true }] } },
    { test: /construction/i, p: {
      loanClass: 'consumer', types: ['single'], defaultType: 'single', rate: 8.25, rateMin: 7.5, rateMax: 9.75,
      amount: 300000, amountMin: 50000, amountMax: 750000, termMonths: 12, termMonthsMin: 6, termMonthsMax: 18,
      fees: [{ name: 'Construction loan fee', kind: 'flat', value: 500, financed: false, financeCharge: true }] } },
    { test: /c&i|commercial and industrial|equipment|business|general|working capital/i, p: {
      loanClass: 'business', types: ['balloon', 'installment'], defaultType: 'balloon',
      frequencies: ['quarterly', 'monthly', 'semiannual', 'annual'], defaultFrequency: 'quarterly',
      rate: 7.25, rateMin: 6.5, rateMax: 9.5, amount: 250000, amountMin: 25000, amountMax: 2000000,
      payments: 20, paymentsMin: 4, paymentsMax: 84, amortization: 40,
      fees: [
        { name: 'Loan fee', kind: 'percent', value: 0.5, financed: true, financeCharge: true },
        { name: 'UCC filing fee', kind: 'flat', value: 45, financed: false, financeCharge: false },
      ],
      insurance: true, coverage: ['life'] } },
    { test: /\bag\b|agri|farm|ranch|cattle|livestock|crop/i, p: {
      loanClass: 'business', types: ['balloon', 'installment', 'single'], defaultType: 'balloon',
      frequencies: ['annual', 'semiannual', 'quarterly'], defaultFrequency: 'annual',
      rate: 7.5, rateMin: 6.75, rateMax: 9.5, amount: 150000, amountMin: 10000, amountMax: 1500000,
      payments: 5, paymentsMin: 1, paymentsMax: 20, amortization: 10 } },
    { test: /personal|consumer|signature|installment/i, p: {
      loanClass: 'consumer', rate: 9.75, rateMin: 8.5, rateMax: 17.99, amount: 10000, amountMin: 1000, amountMax: 50000,
      payments: 36, paymentsMin: 12, paymentsMax: 60,
      fees: [{ name: 'Origination fee', kind: 'flat', value: 75, financed: true, financeCharge: true }],
      insurance: true, coverage: ['life', 'life-disability'], website: true } },
  ];

  function product(over) {
    var p = clone(BASE_PRODUCT);
    Object.keys(over || {}).forEach(function (k) { p[k] = clone(over[k]); });
    if (!p.id) p.id = 'p_' + uid();
    return p;
  }

  function seedSettings() {
    var bank = ctx.vendor.name;
    var products = [];
    ctx.offerings.forEach(function (o) {
      var s = SAMPLES.filter(function (x) { return x.test.test(o.name); })[0];
      if (s) products.push(product(Object.assign({ name: o.name, offering: o.slug }, s.p)));
    });
    if (!products.length) {
      products.push(product(Object.assign({ name: 'Consumer installment loan' }, SAMPLES[6].p)));
      products.push(product(Object.assign({ name: 'Commercial balloon loan' }, SAMPLES[4].p)));
    }
    var site = String(ctx.vendor.website || '').replace(/^https?:\/\//, '').replace(/\/$/, '');
    return {
      v: 1,
      state: 'TX',
      sample: true,
      coverage: [
        { id: 'life', name: 'Credit life', text: 'Pays off the loan if the borrower dies before it is repaid.', required: false },
        { id: 'life-disability', name: 'Credit life and disability', text: 'Pays off the loan if the borrower dies, and makes the payments while the borrower is disabled and can\u2019t work.', required: false },
      ],
      insuranceNotice: 'Credit insurance is not required to get this loan. It is not provided unless the borrower signs for it and agrees to pay the premium.',
      disclosure: 'This quote uses ' + bank + '\u2019s current terms for the loan shown. It is not a commitment to lend. All loans are subject to credit approval. Rates, fees and terms can change without notice.',
      website: { cta: 'Talk to a banker', url: site ? 'https://' + site : '' },
      products: products,
    };
  }

  function storeKey() { return STORE + ctx.vendor.id; }

  function loadData() {
    try {
      var raw = localStorage.getItem(storeKey());
      if (raw) {
        var d = JSON.parse(raw);
        if (d && d.settings && Array.isArray(d.settings.products)) {
          d.log = d.log || []; d.sent = d.sent || [];
          return d;
        }
      }
    } catch (e) { warn('could not read saved settings', e); }
    return { settings: seedSettings(), log: [], sent: [] };
  }

  function saveData() {
    try { localStorage.setItem(storeKey(), JSON.stringify(data)); } catch (e) { warn('could not save', e); }
  }

  function products() { return data.settings.products; }
  function productById(id) { return products().filter(function (p) { return p.id === id; })[0] || products()[0]; }
  function coverageById(id) { return data.settings.coverage.filter(function (c) { return c.id === id; })[0]; }
  function bankName() { return ctx.vendor.name; }
  function savedBy() {
    var l = data.log[0];
    return l ? 'saved ' + stamp(l.at) + ' by ' + l.by : 'sample settings, not yet saved by the bank';
  }

  /* ------------------------------------------------------------ the quote form */

  function freshForm(p) {
    var f = {
      productId: p.id,
      loanType: p.defaultType,
      solveFor: 'payment',
      frequency: p.defaultFrequency,
      amount: p.amount,
      payment: '',
      rate: p.rate,
      payments: p.payments,
      termMonths: p.termMonths,
      balloonMethod: p.balloonMethod,
      amortization: p.amortization,
      balloonAmount: p.balloonAmount,
      balloonStyle: p.balloonStyle,
      closingDate: E.todayIso(),
      firstPaymentDate: '',
      cov: {},
      selected: 'none',
      heard: '',
    };
    (p.insurance ? p.coverage : []).forEach(function (id) { f.cov[id] = { on: true, premium: '' }; });
    return f;
  }

  function sel(id, options, value, attrs) {
    return '<select id="' + id + '"' + (attrs || '') + '>' + options.map(function (o) {
      return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(value) ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
    }).join('') + '</select>';
  }

  function field(id, label, inner, hint, wide) {
    return '<div class="tt-f' + (wide ? ' tt-wide' : '') + '" data-for="' + id + '"><label for="' + id + '">' + esc(label) + '</label>' +
      inner + (hint ? '<div class="tt-hint">' + hint + '</div>' : '') + '</div>';
  }

  function moneyInput(id, value, ph) {
    return '<div class="tt-unit pre"><span>$</span><input id="' + id + '" inputmode="decimal" autocomplete="off" value="' + esc(fmtMoney(value)) + '"' +
      (ph ? ' placeholder="' + esc(ph) + '"' : '') + '></div>';
  }

  function pctInput(id, value) {
    return '<div class="tt-unit post"><span>%</span><input id="' + id + '" inputmode="decimal" autocomplete="off" value="' + esc(fmtNum(value)) + '"></div>';
  }

  function intInput(id, value, ph) {
    return '<input id="' + id + '" inputmode="numeric" autocomplete="off" value="' + esc(fmtNum(value)) + '"' + (ph ? ' placeholder="' + esc(ph) + '"' : '') + '>';
  }

  function yearsText(count, frequency) {
    var f = E.FREQUENCIES[frequency];
    var months = count * f.months;
    if (!(count > 0)) return '';
    if (months % 12 === 0) return (months / 12) + ' year' + (months === 12 ? '' : 's');
    return months + ' months';
  }

  function productLine(p) {
    var bits = [p.loanClass === 'business' ? 'Business' : 'Consumer'];
    bits.push(p.types.map(function (t) { return E.LOAN_TYPES[t]; }).join(' or '));
    if (p.types.some(function (t) { return t !== 'single'; })) {
      bits.push(p.frequencies.map(function (x) { return E.FREQUENCIES[x].label.toLowerCase(); }).join(', '));
    }
    bits.push(E.formatRate(p.rate, 2));
    return bits.join(' \u00b7 ');
  }

  function renderQuote() {
    var p = productById(form.productId);
    var f = form;
    var single = f.loanType === 'single';
    var balloon = f.loanType === 'balloon';
    var solvable = E.SOLVABLE[f.loanType];
    var mic = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    var freqOpts = p.frequencies.map(function (x) { return [x, E.FREQUENCIES[x].label]; });

    var h = '<div class="tt-quote"><div class="tt-col-form">';

    h += '<div class="tt-card"><h2>Quote a loan</h2><p class="tt-sub">On ' + esc(bankName()) + '\u2019s own terms. Pick the product, then type or say the loan.</p>';
    h += field('tt-product', 'Product', sel('tt-product', products().map(function (x) { return [x.id, x.name]; }), p.id),
      esc(productLine(p)) + ' \u00b7 set in <button type="button" class="tt-link" data-tab="settings">Settings</button>');
    h += '<div class="tt-f"><label for="tt-say">Say or type it</label><div class="tt-say">' +
      '<input id="tt-say" autocomplete="off" enterkeyhint="go" placeholder="250k, 10 year amortization, 5 year balloon, quarterly">' +
      (mic ? '<button type="button" class="tt-mic" id="tt-mic" aria-label="Speak the loan">' + MIC_SVG + '</button>' : '') +
      '</div><div class="tt-heard" id="tt-heard" role="status">' + esc(f.heard || '') + '</div></div>';
    h += '</div>';

    h += '<div class="tt-card"><h3>Loan settings</h3><div class="tt-grid">';
    h += field('tt-state', 'State', sel('tt-state', Object.keys(E.STATES).map(function (k) { return [k, E.STATES[k].name]; }), data.settings.state, ' disabled'), 'Texas only for now.');
    h += field('tt-class', 'Loan class', sel('tt-class', [['consumer', 'Consumer'], ['business', 'Business']], p.loanClass, ' disabled'), 'Set by the product.');
    h += field('tt-type', 'Loan type', sel('tt-type', p.types.map(function (t) { return [t, E.LOAN_TYPES[t]]; }), f.loanType, p.types.length < 2 ? ' disabled' : ''));
    h += field('tt-frequency', 'Payment frequency', sel('tt-frequency', single ? [['monthly', 'At maturity']] : freqOpts, single ? 'monthly' : f.frequency,
      single || freqOpts.length < 2 ? ' disabled' : ''));
    h += '<div class="tt-f tt-wide"><span class="tt-l">Solve for</span><div class="tt-seg" role="group" aria-label="Solve for">' +
      Object.keys(E.SOLVE_FOR).map(function (k) {
        var ok = solvable.indexOf(k) >= 0;
        return ok ? '<button type="button" data-solve="' + k + '" aria-pressed="' + (f.solveFor === k) + '">' + esc(E.SOLVE_FOR[k]) + '</button>' : '';
      }).join('') + '</div></div>';
    h += '</div></div>';

    h += '<div class="tt-card"><h3>Loan information</h3><div class="tt-grid">';
    var amtHint = esc(p.name) + ': ' + dollars(p.amountMin) + ' to ' + dollars(p.amountMax);
    if (f.solveFor !== 'amount') h += field('tt-amount', 'Loan amount', moneyInput('tt-amount', f.amount), amtHint);
    if (f.solveFor !== 'payment') {
      var payLabel = single ? 'Payment at maturity' : 'Payment';
      h += field('tt-payment', payLabel, moneyInput('tt-payment', f.payment, '500'),
        f.solveFor === 'amount' ? 'TheTerms finds the most the borrower can borrow at this payment.'
          : f.solveFor === 'term' ? 'TheTerms finds how many payments it takes.' : 'TheTerms finds the balloon this payment leaves.');
    }
    h += field('tt-rate', 'Interest rate', pctInput('tt-rate', f.rate),
      esc(bankName()) + '\u2019s rate is ' + E.formatRate(p.rate, 2) + ' (' + E.formatRate(p.rateMin, 2) + ' to ' + E.formatRate(p.rateMax, 2) + ')');
    if (single) {
      var matures = '';
      try { matures = E.formatDate(E.addMonths(f.closingDate, num(f.termMonths) || 0)); } catch (e) {}
      h += field('tt-termMonths', 'Term in months', intInput('tt-termMonths', f.termMonths),
        (matures ? 'Matures ' + matures + '. ' : '') + p.termMonthsMin + ' to ' + p.termMonthsMax + ' months');
    } else if (f.solveFor !== 'term') {
      h += field('tt-payments', balloon ? 'Payments until the balloon' : 'Number of payments', intInput('tt-payments', f.payments),
        esc(yearsText(num(f.payments), f.frequency)) + ' (' + p.paymentsMin + ' to ' + p.paymentsMax + ')');
    }
    if (balloon) {
      h += '<div class="tt-f tt-wide"><span class="tt-l">Balloon set by</span><div class="tt-seg" role="group" aria-label="Balloon set by">' +
        (f.solveFor === 'balloon' ? '<button type="button" aria-pressed="true" disabled>The payment</button>'
          : '<button type="button" data-bmethod="amortization" aria-pressed="' + (f.balloonMethod === 'amortization') + '">Amortization</button>' +
            '<button type="button" data-bmethod="amount" aria-pressed="' + (f.balloonMethod === 'amount') + '">Balloon amount</button>') +
        '</div></div>';
      if (f.solveFor !== 'balloon' && f.balloonMethod === 'amortization') {
        h += field('tt-amortization', 'Amortized over (payments)', intInput('tt-amortization', f.amortization), esc(yearsText(num(f.amortization), f.frequency)));
      }
      if (f.solveFor !== 'balloon' && f.balloonMethod === 'amount') {
        h += field('tt-balloonAmount', 'Balloon amount', moneyInput('tt-balloonAmount', f.balloonAmount));
      }
      h += field('tt-balloonStyle', 'The balloon', sel('tt-balloonStyle', [['last', 'is the last payment'], ['extra', 'comes on top of the last payment']], f.balloonStyle), '', true);
    }
    h += field('tt-closingDate', 'Closing date', '<input type="date" id="tt-closingDate" value="' + esc(f.closingDate) + '">');
    if (!single) {
      var defFirst = '';
      try { defFirst = E.addMonths(f.closingDate, E.FREQUENCIES[f.frequency].months); } catch (e) {}
      h += field('tt-firstPaymentDate', 'First payment date',
        '<input type="date" id="tt-firstPaymentDate" value="' + esc(f.firstPaymentDate || defFirst) + '">',
        p.oddDays === 'first' ? 'A later date adds odd-days interest to the first payment.' : 'A later date spreads the extra interest over the payments.');
    }
    h += '</div>';

    var fees = p.fees || [];
    h += '<h3 style="margin:16px 0 6px;font-size:14px;">Fees</h3>';
    h += fees.length ? '<ul class="tt-fees">' + fees.map(function (x) {
      var amt = x.kind === 'percent' ? E.formatRate(x.value, 2) + ' of the loan' : dollars(x.value);
      return '<li><span>' + esc(x.name) + ' <span class="tt-muted tt-small">' + (x.financed ? 'financed' : 'paid at closing') +
        (x.financeCharge ? '' : ', not a finance charge') + '</span></span><span>' + esc(amt) + '</span></li>';
    }).join('') + '</ul>' : '<p class="tt-muted tt-small">No fees on this product.</p>';
    h += '<p class="tt-muted tt-small" style="margin-top:6px;">The bank sets these once, in Settings. Nobody retypes them.</p>';

    if (p.insurance && p.coverage.length) {
      h += '<h3 style="margin:16px 0 6px;font-size:14px;">Coverage options</h3>';
      h += '<p class="tt-muted tt-small" style="margin-bottom:8px;">Compared side by side with no coverage. V1 takes the premium from the carrier\u2019s quote; it doesn\u2019t price it.</p>';
      p.coverage.forEach(function (id) {
        var c = coverageById(id);
        if (!c) return;
        var st = f.cov[id] || { on: false, premium: '' };
        h += '<div class="tt-cov"><label class="tt-check"><input type="checkbox" data-cov="' + esc(id) + '"' + (st.on ? ' checked' : '') + '>' +
          '<span><strong>' + esc(c.name) + '</strong>' + (c.required ? ' <span class="tt-muted">(required)</span>' : '') + '</span></label>' +
          '<div class="tt-cov-text">' + esc(c.text) + '</div>' +
          '<div class="tt-cov-premium tt-f" data-for="tt-premium-' + esc(id) + '"><label for="tt-premium-' + esc(id) + '">Premium</label>' +
          moneyInput('tt-premium-' + id, st.premium, '0.00') + '</div></div>';
      });
    }
    h += '<div style="margin-top:14px;"><button type="button" class="tt-btn primary block" id="tt-calc">Calculate</button></div>';
    h += '</div>';

    h += '</div><div class="tt-col-result" id="tt-result">' + renderResult() + '</div></div>';
    return h;
  }

  var MIC_SVG = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
    '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';

  /** Copies what is on screen into `form`. */
  function readForm() {
    function val(id) { var el = document.getElementById(id); return el ? el.value : undefined; }
    var keys = ['amount', 'payment', 'rate', 'payments', 'termMonths', 'amortization', 'balloonAmount', 'balloonStyle', 'closingDate', 'firstPaymentDate'];
    keys.forEach(function (k) { var v = val('tt-' + k); if (v !== undefined) form[k] = v; });
    var t = val('tt-type'); if (t) form.loanType = t;
    var fr = val('tt-frequency'); if (fr && form.loanType !== 'single') form.frequency = fr;
    $$('[data-cov]').forEach(function (cb) {
      var id = cb.getAttribute('data-cov');
      form.cov[id] = { on: cb.checked, premium: val('tt-premium-' + id) || '' };
    });
  }

  function engineInput(p, f, cov) {
    var single = f.loanType === 'single';
    var first = single ? '' : f.firstPaymentDate;
    // An untouched first payment date is the regular one, so it isn't sent.
    try { if (first && first === E.addMonths(f.closingDate, E.FREQUENCIES[f.frequency].months)) first = ''; } catch (e) {}
    return {
      state: data.settings.state,
      loanClass: p.loanClass,
      loanType: f.loanType,
      solveFor: f.solveFor,
      frequency: single ? 'monthly' : f.frequency,
      amount: num(f.amount),
      payment: num(f.payment),
      rate: num(f.rate),
      payments: num(f.payments),
      termMonths: num(f.termMonths),
      balloonMethod: f.balloonMethod,
      amortization: num(f.amortization),
      balloonAmount: num(f.balloonAmount),
      balloonStyle: f.balloonStyle,
      closingDate: f.closingDate,
      firstPaymentDate: first || '',
      calendar: p.calendar,
      rounding: p.rounding,
      oddDays: p.oddDays,
      fees: clone(p.fees || []),
      premium: cov ? num(cov.premium) || 0 : 0,
      premiumRequired: !!(cov && cov.required),
    };
  }

  /** The options to compare: no coverage, then each coverage option with a premium. */
  function optionsFor(p, f) {
    var out = [{ id: 'none', name: 'No coverage', text: '', premium: 0, required: false }];
    if (p.insurance) {
      p.coverage.forEach(function (id) {
        var c = coverageById(id);
        var st = f.cov[id];
        if (c && st && st.on && num(st.premium) > 0) {
          out.push({ id: id, name: c.name, text: c.text, premium: num(st.premium), required: !!c.required });
        }
      });
    }
    return out;
  }

  function calculate() {
    readForm();
    var p = productById(form.productId);
    var options = optionsFor(p, form);
    var results = options.map(function (o) {
      var input = engineInput(p, form, o.id === 'none' ? null : o);
      var r = E.calculate(input);
      return { option: o, input: input, result: r, refusals: r.ok ? E.outsideRange(p, input, r, bankName()) : [] };
    });
    var base = results[0];
    calc = { product: p, options: results, at: new Date().toISOString() };
    if (!results.some(function (x) { return x.option.id === form.selected; })) form.selected = 'none';
    stale = false;
    showAll = false;
    paintResult();
    markErrors(base.result.ok ? [] : base.result.errors);
    if (window.matchMedia('(max-width: 1099px)').matches) {
      var el = document.getElementById('tt-result');
      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function markErrors(errors) {
    $$('.tt-f.has-error').forEach(function (x) { x.classList.remove('has-error'); });
    (errors || []).forEach(function (e) {
      var w = $('[data-for="tt-' + e.field + '"]');
      if (w) w.classList.add('has-error');
    });
  }

  function usable(x) { return x.result.ok && !x.refusals.length; }

  function selected() {
    if (!calc) return null;
    return calc.options.filter(function (x) { return x.option.id === form.selected && usable(x); })[0] || calc.options[0];
  }

  /* ------------------------------------------------------------ results */

  function renderResult() {
    if (!calc) {
      return '<div class="tt-card"><h3>The quote shows here</h3><p class="tt-sub" style="margin:4px 0 0;">The payment with its APR and full repayment terms, ' +
        'each coverage option side by side, the Truth in Lending box and the amortization table. Then send it to the customer\u2019s phone.</p></div>';
    }
    var base = calc.options[0];
    if (!base.result.ok) {
      return '<div class="tt-card"><div class="tt-alert error"><strong>Check the loan.</strong><ul>' +
        base.result.errors.map(function (e) { return '<li>' + esc(e.message) + '</li>'; }).join('') + '</ul></div></div>';
    }
    if (base.refusals.length) {
      return '<div class="tt-card"><div class="tt-alert error"><strong>' + esc(bankName()) + ' doesn\u2019t offer these terms.</strong><ul>' +
        base.refusals.map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('') + '</ul></div>' +
        '<p class="tt-muted tt-small">TheTerms refuses a request outside the bank\u2019s ranges rather than adjusting it. The ranges are in Settings.</p></div>';
    }
    var s = selected();
    var r = s.result;
    var p = calc.product;
    var h = '';
    if (stale) h += '<div class="tt-alert warn">The loan changed. Calculate again before sending.</div>';
    h += '<div class="tt-card tt-result" style="' + V.brandVars(ctx.brand) + '">';
    h += bankBar(p.name + ' \u00b7 quoted by ' + (ctx.user.name || ctx.user.email) + ' \u00b7 ' + E.formatDate(E.todayIso()));
    if (r.input.solveFor !== 'payment') h += '<div class="tt-alert ok">' + solvedText(r) + '</div>';
    h += '<div class="tt-wedge" role="group" aria-label="Coverage options">' + calc.options.map(optionCard).join('') + '</div>';
    if (calc.options.length === 1 && p.insurance && p.coverage.length) {
      h += '<p class="tt-muted tt-small" style="margin:-4px 0 10px;">Enter a premium under Coverage options to compare it here.</p>';
    }
    h += '<h3 style="font-size:14px;margin:4px 0 8px;">Disclosure \u00b7 ' + esc(s.option.name) + '</h3>';
    h += V.tila(r, { optionName: s.option.name, optionText: s.option.text, insuranceNotice: data.settings.insuranceNotice });
    h += '<p class="tt-disclosure">' + esc(data.settings.disclosure) + '</p>';
    h += '</div>';

    h += '<div class="tt-card"><h3>Amortization schedule</h3><p class="tt-sub">' + r.count + ' payment' + (r.count === 1 ? '' : 's') + ' \u00b7 ' + esc(s.option.name) + '</p>';
    h += V.schedule(r, { all: showAll, limit: 12 });
    if (r.schedule.length > 13) {
      h += '<div style="margin-top:10px;"><button type="button" class="tt-btn ghost sm" id="tt-all">' + (showAll ? 'Show fewer' : 'Show all ' + r.count + ' payments') + '</button></div>';
    }
    h += '</div>';

    h += renderShare(s);
    h += renderAi(s);
    return h;
  }

  function bankBar(meta) {
    var b = ctx.brand;
    return '<div class="tt-bankbar">' + (b.logo ? '<img src="' + esc(b.logo) + '" alt="">' : '') +
      '<div><div class="tt-bankname">' + esc(bankName()) + '</div><div class="tt-meta">' + esc(meta) + '</div></div></div>';
  }

  function solvedText(r) {
    var f = r.input.solveFor;
    if (f === 'amount') return 'At ' + money(r.payment) + ' ' + V.PER[r.input.frequency] + ', the borrower can borrow up to <strong>' + money(r.amount) + '</strong>.';
    if (f === 'term') return 'At ' + money(r.payment) + ', the loan is repaid in <strong>' + r.count + ' payments</strong> (' + esc(yearsText(r.count, r.input.frequency)) + ').';
    if (f === 'balloon') return 'At ' + money(r.payment) + ', the balloon is <strong>' + money(r.balloon) + '</strong>.';
    return '';
  }

  function optionCard(x) {
    var o = x.option, r = x.result;
    var on = o.id === form.selected;
    if (!r.ok || x.refusals.length) {
      var why = !r.ok ? r.errors.map(function (e) { return e.message; }).join(' ') : x.refusals.join(' ');
      return '<div class="tt-opt is-refused"><span class="tt-opt-name">' + esc(o.name) + '</span><span class="tt-refused">' + esc(why) + '</span></div>';
    }
    var s = V.summary(r);
    var h = '<button type="button" class="tt-opt" data-opt="' + esc(o.id) + '" aria-pressed="' + on + '">';
    h += '<span class="tt-opt-name">' + esc(o.name) + '</span>';
    h += '<span class="tt-pay">' + esc(s.amount) + ' <small>' + esc(s.per) + '</small></span>';
    h += '<span class="tt-line">' + esc(s.line) + '</span>';
    h += '<span class="tt-apr">APR ' + esc(V.apr(r.apr)) + '</span>';
    h += kv('Finance charge', money(r.totals.financeCharge));
    h += kv('Amount financed', money(r.totals.amountFinanced));
    h += kv('Total of payments', money(r.totals.totalOfPayments));
    if (o.premium) h += kv('Premium', money(r.premium));
    h += '<span class="tt-pick">' + (on ? '\u2713 In the quote' : 'Use this one') + '</span>';
    return h + '</button>';
  }

  function kv(k, v) { return '<span class="tt-kv"><span>' + esc(k) + '</span><span>' + esc(v) + '</span></span>'; }

  function paintResult() {
    var el = document.getElementById('tt-result');
    if (el) el.innerHTML = renderResult();
  }

  /* ------------------------------------------------------------ sending */

  function startUrl(p) {
    if (!p.offering) return '';
    return location.origin + '/v/' + encodeURIComponent(ctx.slug) + '/start/' + encodeURIComponent(p.offering);
  }

  /** The customer's link carries the loan terms and the bank's text, never the customer. */
  function quoteLink(s) {
    var p = calc.product;
    var payload = {
      v: 1,
      bank: { name: bankName(), logo: ctx.brand.logo, primary: ctx.brand.primary, accent: ctx.brand.accent, website: data.settings.website.url },
      by: { name: ctx.user.name || '', email: ctx.user.email || '' },
      product: { name: p.name },
      options: calc.options.filter(usable).map(function (x) {
        return { id: x.option.id, name: x.option.name, text: x.option.text, input: x.input };
      }),
      selected: s.option.id,
      insuranceNotice: data.settings.insuranceNotice,
      disclosure: data.settings.disclosure,
      start: startUrl(p),
      at: calc.at,
    };
    return location.origin + BASE + 'quote.html#' + E.pack(payload);
  }

  function renderShare(s) {
    var h = '<div class="tt-card" id="tt-share"><h3>Send it to the customer</h3>';
    h += '<p class="tt-sub">The link opens on the customer\u2019s phone and looks like ' + esc(bankName()) +
      '. Only the loan terms travel in it: no name, no phone number, nothing about the customer.</p>';
    h += '<div class="tt-grid"><div class="tt-f tt-wide" data-for="tt-phone"><label for="tt-phone">Customer\u2019s mobile number</label>' +
      '<input id="tt-phone" type="tel" inputmode="tel" autocomplete="off" placeholder="(512) 555-0142"></div>';
    h += '<div class="tt-wide"><label class="tt-check"><input type="checkbox" id="tt-consent"><span>The customer asked me to text them this quote.</span></label></div></div>';
    h += '<div class="tt-actions" style="margin-top:12px;"><button type="button" class="tt-btn primary" id="tt-text" disabled>Text the quote</button>' +
      '<a class="tt-btn ghost" id="tt-open" target="_blank" rel="noopener" href="' + esc(quoteLink(s)) + '">Open as the customer</a>' +
      '<button type="button" class="tt-btn ghost" id="tt-copy">Copy link</button>' +
      '<a class="tt-btn ghost" id="tt-email" href="' + esc(mailto(s)) + '">Email</a>' +
      '<a class="tt-btn ghost" id="tt-print" target="_blank" rel="noopener" href="' + esc(quoteLink(s).replace('quote.html#', 'quote.html?print=1#')) + '">Print or PDF</a></div>';
    h += '<div id="tt-sms"></div></div>';
    return h;
  }

  function mailto(s) {
    var subject = 'Your loan quote from ' + bankName();
    var body = 'Here is the ' + calc.product.name + ' quote you asked for:\n\n' + quoteLink(s) + '\n\n' + (ctx.user.name || '');
    return 'mailto:?subject=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body);
  }

  function digits(s) { return String(s || '').replace(/\D/g, ''); }
  function phoneOk(s) { var d = digits(s); return d.length === 10 || (d.length === 11 && d[0] === '1'); }

  function sendText() {
    var s = selected();
    var phone = document.getElementById('tt-phone').value;
    if (!phoneOk(phone) || !document.getElementById('tt-consent').checked) return;
    var link = quoteLink(s);
    var sum = V.summary(s.result);
    var first = String(ctx.user.name || '').split(' ')[0] || 'your banker';
    var d = digits(phone).slice(-4);
    data.sent.unshift({
      id: uid(), at: new Date().toISOString(), by: ctx.user.name || ctx.user.email,
      to: '(\u2022\u2022\u2022) \u2022\u2022\u2022-' + d, product: calc.product.name,
      quote: sum.amount + ' ' + sum.per + ', APR ' + V.apr(s.result.apr), option: s.option.name, link: link, consent: true,
    });
    saveData();
    var shown = location.host + BASE + 'quote.html#\u2026';
    document.getElementById('tt-sms').innerHTML =
      '<div class="tt-phone" aria-label="The text the customer gets"><div class="tt-phone-from">' + esc(bankName()) + '</div>' +
      '<div class="tt-bubble">' + esc(bankName()) + ': here is the loan quote you asked for, from ' + esc(first) + '. ' +
      '<a href="' + esc(link) + '" target="_blank" rel="noopener">' + esc(shown) + '</a> Reply STOP to opt out.</div></div>' +
      '<p class="tt-sent-note">Staging preview: no text was sent. Texting turns on with #143. The quote is in Sent.</p>';
    paintTabs();
  }

  /* ------------------------------------------------------------ for the customer's AI */

  function renderAi(s) {
    var r = s.result, p = calc.product;
    var rows = r.schedule;
    var amort = rows.length <= 6 ? rows : rows.slice(0, 3).concat([{ note: (rows.length - 5) + ' more payments' }], rows.slice(-2));
    var out = {
      request: {
        method: 'POST',
        url: location.origin + '/api/theterms/' + ctx.slug + '/quote',
        body: {
          product: p.name, amount: r.amount / 100, rate: r.input.rate, loan_type: r.input.loanType,
          payments: r.input.loanType === 'single' ? undefined : r.count, frequency: r.input.loanType === 'single' ? undefined : r.input.frequency,
          term_months: r.input.loanType === 'single' ? r.input.termMonths : undefined, coverage: s.option.id === 'none' ? null : s.option.name,
        },
      },
      response: {
        bank: bankName(),
        product: p.name,
        source: bankName() + '\u2019s TheTerms settings for Texas, ' + savedBy(),
        payment: r.payment / 100,
        payment_schedule: r.groups.map(function (g) { return { count: g.count, amount: g.amount / 100, first: g.first, last: g.last }; }),
        apr: r.apr === null ? null : Math.round(r.apr * 1000) / 1000,
        finance_charge: r.totals.financeCharge / 100,
        amount_financed: r.totals.amountFinanced / 100,
        total_of_payments: r.totals.totalOfPayments / 100,
        fees: r.fees.map(function (f) { return { name: f.name, amount: f.cents / 100, financed: f.financed, finance_charge: f.financeCharge }; }),
        coverage: s.option.id === 'none' ? null : { name: s.option.name, premium: r.premium / 100, text: s.option.text },
        amortization: amort.map(function (x) {
          return x.note ? x.note : { n: x.n, date: x.date, payment: x.payment / 100, interest: x.interest / 100, principal: x.principal / 100, balance: x.balance / 100 };
        }),
        disclosures: data.settings.disclosure,
      },
    };
    return '<div class="tt-card"><details class="tt-details"><summary>What the customer\u2019s AI would get</summary>' +
      '<p class="tt-sub">Preview of #138, which isn\u2019t live yet. Asked about this loan, Claude or ChatGPT would get these numbers from ' +
      esc(bankName()) + '\u2019s settings instead of working out its own.</p><pre class="tt-json">' + esc(JSON.stringify(out, null, 2)) + '</pre></details></div>';
  }

  /* ------------------------------------------------------------ say it */

  function applySaid(text) {
    readForm();
    var heard = E.parseTerms(text);
    var p = productById(form.productId);
    var said = [], skipped = [];
    if (heard.loanType) {
      if (p.types.indexOf(heard.loanType) >= 0) { form.loanType = heard.loanType; said.push(E.LOAN_TYPES[heard.loanType].toLowerCase()); }
      else skipped.push(p.name + ' isn\u2019t offered as a ' + E.LOAN_TYPES[heard.loanType].toLowerCase() + ' loan');
    }
    if (heard.frequency && form.loanType !== 'single') {
      if (p.frequencies.indexOf(heard.frequency) >= 0) { form.frequency = heard.frequency; said.push(E.FREQUENCIES[heard.frequency].label.toLowerCase()); }
      else skipped.push(p.name + ' doesn\u2019t take ' + E.FREQUENCIES[heard.frequency].label.toLowerCase() + ' payments');
    }
    if (heard.amount !== undefined) { form.amount = heard.amount; said.push(dollars(heard.amount)); }
    if (heard.rate !== undefined) { form.rate = heard.rate; said.push(E.formatRate(heard.rate, 2)); }
    if (heard.payment !== undefined) { form.payment = heard.payment; said.push('payment ' + dollars(heard.payment)); }
    if (heard.solveFor && E.SOLVABLE[form.loanType].indexOf(heard.solveFor) >= 0) form.solveFor = heard.solveFor;
    if (heard.term) {
      if (form.loanType === 'single') { form.termMonths = E.monthsFor(heard.term); said.push(form.termMonths + ' months'); }
      else { form.payments = E.paymentsFor(heard.term, form.frequency); said.push(form.payments + ' payments'); }
    }
    if (heard.amortization && form.loanType === 'balloon') {
      form.balloonMethod = 'amortization';
      form.amortization = E.paymentsFor(heard.amortization, form.frequency);
      said.push('amortized over ' + form.amortization);
    }
    if (heard.balloonAmount !== undefined && form.loanType === 'balloon') {
      if (!heard.amortization) form.balloonMethod = 'amount';
      form.balloonAmount = heard.balloonAmount;
      said.push('balloon ' + dollars(heard.balloonAmount));
    }
    form.heard = said.length
      ? 'Heard: ' + said.join(', ') + '. Check it, then Calculate.' + (skipped.length ? ' ' + skipped.join('. ') + '.' : '')
      : 'Didn\u2019t catch any loan terms in that. Try \u201c50,000 at 7.25% for 5 years, quarterly\u201d.';
    if (calc) stale = true;
    paintBody();
    var say = document.getElementById('tt-say');
    if (say) say.value = text;
  }

  var listening = null;
  function listen() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    var mic = document.getElementById('tt-mic');
    if (listening) { try { listening.stop(); } catch (e) {} return; }
    var rec = new SR();
    rec.lang = 'en-US';
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = function (e) {
      var text = e.results[0][0].transcript;
      applySaid(text);
    };
    rec.onerror = function (e) {
      var el = document.getElementById('tt-heard');
      if (el) el.textContent = e.error === 'not-allowed' ? 'The browser blocked the microphone. Type the loan instead.' : 'Didn\u2019t hear that. Try again, or type it.';
    };
    rec.onend = function () {
      listening = null;
      var m = document.getElementById('tt-mic');
      if (m) m.classList.remove('listening');
    };
    listening = rec;
    if (mic) mic.classList.add('listening');
    var el = document.getElementById('tt-heard');
    if (el) el.textContent = 'Listening\u2026';
    try { rec.start(); } catch (e) { listening = null; if (mic) mic.classList.remove('listening'); }
  }

  /* ------------------------------------------------------------ sent */

  function renderSent() {
    var h = '<div class="tt-card"><h2>Sent quotes</h2><p class="tt-sub">Who sent which quote to whom, for the bank. Only the last four digits of the number are kept.</p>';
    if (!data.sent.length) {
      h += '<p class="tt-muted">No quotes sent yet. Build one under Quote and text it to a customer.</p>';
    } else {
      h += '<div class="tt-table-wrap"><table class="tt-table"><thead><tr><th>When</th><th>Product</th><th>Quote</th><th>To</th><th>By</th><th></th></tr></thead><tbody>';
      data.sent.forEach(function (x) {
        h += '<tr><td>' + esc(stamp(x.at)) + '</td><td>' + esc(x.product) + '<div class="tt-muted tt-small">' + esc(x.option) + '</div></td><td>' + esc(x.quote) +
          '</td><td>' + esc(x.to) + '<div class="tt-muted tt-small">asked for it</div></td><td>' + esc(x.by) + '</td><td><a class="tt-link" target="_blank" rel="noopener" href="' +
          esc(x.link) + '">Open</a></td></tr>';
      });
      h += '</tbody></table></div>';
    }
    h += '<p class="tt-muted tt-small" style="margin-top:10px;">Staging preview: this list is kept in this browser. The real record lives with the bank.</p></div>';
    return h;
  }

  /* ------------------------------------------------------------ website */

  function widgetConfig() {
    var s = data.settings;
    return {
      v: 1,
      bank: { name: bankName(), logo: ctx.brand.logo, primary: ctx.brand.primary, accent: ctx.brand.accent },
      cta: { label: s.website.cta, url: s.website.url },
      insuranceNotice: s.insuranceNotice,
      disclosure: s.disclosure,
      state: s.state,
      saved: savedBy(),
      products: s.products.filter(function (p) { return p.website; }).map(function (p) {
        var c = clone(p);
        c.coverage = p.insurance ? p.coverage.map(coverageById).filter(Boolean) : [];
        c.start = startUrl(p);
        delete c.website;
        return c;
      }),
    };
  }

  function widgetUrl() { return location.origin + BASE + 'widget.html#' + E.pack(widgetConfig()); }

  function embedCode() {
    return '<iframe src="' + widgetUrl() + '" title="Loan calculator from ' + bankName().replace(/"/g, '') +
      '" style="width:100%;max-width:520px;height:900px;border:0;" loading="lazy"></iframe>';
  }

  var frameWide = false;
  function renderWebsite() {
    var s = data.settings;
    var on = s.products.filter(function (p) { return p.website; });
    var ro = ctx.canEdit ? '' : ' disabled';
    var h = '<div class="tt-website"><div>';
    h += '<div class="tt-card"><h2>Website calculator</h2><p class="tt-sub">A drop-in for Dinkytown (#140): ' + esc(bankName()) +
      '\u2019s products, rates, fees and disclosures, in its colors, with a next step into the bank.</p>';
    h += '<span class="tt-l" style="font-size:12.5px;font-weight:700;color:var(--tt-navy);">On the website</span><div style="margin:6px 0 12px;">';
    s.products.forEach(function (p) {
      h += '<label class="tt-check" style="margin-bottom:8px;"><input type="checkbox" data-web="' + esc(p.id) + '"' + (p.website ? ' checked' : '') + ro + '>' +
        '<span>' + esc(p.name) + ' <span class="tt-muted tt-small">' + esc(productLine(p)) + '</span></span></label>';
    });
    h += '</div><p class="tt-muted tt-small" style="margin-bottom:12px;">A product on the website shows the rate the bank set for it. The customer can\u2019t go outside the bank\u2019s ranges.</p>';
    h += '<div class="tt-grid">' + field('tt-cta', 'Button', '<input id="tt-cta" value="' + esc(s.website.cta) + '"' + ro + '>') +
      field('tt-cta-url', 'Button goes to', '<input id="tt-cta-url" inputmode="url" value="' + esc(s.website.url) + '"' + ro + '>') + '</div>';
    if (ctx.canEdit) h += '<div class="tt-actions" style="margin-top:10px;"><button type="button" class="tt-btn ghost sm" id="tt-web-save">Save website settings</button><span id="tt-web-ok" class="tt-small tt-muted"></span></div>';
    h += '</div>';
    h += '<div class="tt-card"><h3>Embed code</h3><p class="tt-sub">Paste it into any page on the bank\u2019s site. No build step.</p>';
    h += on.length ? '<textarea class="tt-code" id="tt-embed" readonly>' + esc(embedCode()) + '</textarea>' +
      '<div class="tt-actions" style="margin-top:8px;"><button type="button" class="tt-btn ghost sm" id="tt-embed-copy">Copy embed code</button>' +
      '<a class="tt-btn ghost sm" target="_blank" rel="noopener" href="' + esc(widgetUrl()) + '">Open it full page</a></div>'
      : '<p class="tt-muted">Put at least one product on the website first.</p>';
    h += '<p class="tt-muted tt-small" style="margin-top:10px;">Staging preview: the settings travel inside this link, so copy it again after a change. ' +
      'The live version reads them from TheTerms settings, the way the A2B embed updates itself (#133).</p></div>';
    h += '</div><div class="tt-card"><div class="tt-actions" style="justify-content:space-between;margin-bottom:10px;"><h3 style="margin:0;">Preview</h3>' +
      '<div class="tt-seg"><button type="button" data-frame="phone" aria-pressed="' + !frameWide + '">Phone</button><button type="button" data-frame="wide" aria-pressed="' + frameWide + '">Wide</button></div></div>';
    h += on.length ? '<iframe class="tt-frame' + (frameWide ? ' wide' : '') + '" id="tt-frame" title="Website calculator preview" src="' + esc(widgetUrl()) + '"></iframe>'
      : '<p class="tt-muted">Nothing on the website yet.</p>';
    h += '</div></div>';
    return h;
  }

  /* ------------------------------------------------------------ settings */

  function opt(v, label, cur) { return '<option value="' + esc(v) + '"' + (String(v) === String(cur) ? ' selected' : '') + '>' + esc(label) + '</option>'; }

  function sInput(path, value, attrs) {
    return '<input data-k="' + path + '" value="' + esc(fmtNum(value)) + '"' + (attrs || '') + '>';
  }

  function sSelect(path, options, cur) {
    return '<select data-k="' + path + '">' + options.map(function (o) { return opt(o[0], o[1], cur); }).join('') + '</select>';
  }

  function sField(label, inner, hint, wide) {
    return '<div class="tt-f' + (wide ? ' tt-wide' : '') + '"><label>' + esc(label) + '</label>' + inner + (hint ? '<div class="tt-hint">' + esc(hint) + '</div>' : '') + '</div>';
  }

  function sChecks(path, options, cur) {
    return '<div class="tt-checks">' + options.map(function (o) {
      return '<label class="tt-check"><input type="checkbox" data-arr="' + path + '" value="' + esc(o[0]) + '"' + (cur.indexOf(o[0]) >= 0 ? ' checked' : '') + '><span>' + esc(o[1]) + '</span></label>';
    }).join('') + '</div>';
  }

  function sToggle(path, on, label) {
    return '<label class="tt-check"><input type="checkbox" data-b="' + path + '"' + (on ? ' checked' : '') + '><span>' + esc(label) + '</span></label>';
  }

  function renderSettings() {
    var s = draft;
    var b = ctx.brand;
    var h = '';
    if (!ctx.canEdit) h += '<div class="tt-alert warn">Only ' + esc(bankName()) + '\u2019s admins can change these. You can see what every quote uses.</div>';
    if (s.sample) h += '<div class="tt-alert warn">These are sample terms for the demo bank. A bank sets its own products, rates, fees and coverage here once, and every quote follows them.</div>';

    h += '<div class="tt-card"><h2>Brand</h2><p class="tt-sub">Quotes, the customer\u2019s page and the website calculator use the logo and colors from Admin \u2192 General. TheTerms has no brand settings of its own.</p>';
    h += '<div class="tt-brandrow">' + (b.logo ? '<img src="' + esc(b.logo) + '" alt="">' : '<span class="tt-muted">No logo yet</span>') +
      swatch('Primary', b.primary) + swatch('Accent', b.accent) +
      '<button type="button" class="tt-btn ghost sm" id="tt-general">Change in Admin \u2192 General</button></div></div>';

    h += '<div class="tt-card"><h2>State</h2><div class="tt-grid">' + sField('State', sSelect('state', [['TX', 'Texas']], s.state),
      'V1 is Texas only. Settings are kept per state so a second state can come later.') + '</div></div>';

    h += '<div class="tt-card"><h2>Loan products</h2><p class="tt-sub">What a loan officer can quote, and what the website and the customer\u2019s AI calculate with. A request outside a range is refused, not adjusted.</p>';
    s.products.forEach(function (p, i) { h += productEditor(p, i, s); });
    if (ctx.canEdit) h += '<button type="button" class="tt-btn ghost sm" data-act="add-product">Add a product</button>';
    h += '</div>';

    h += '<div class="tt-card"><h2>Coverage options</h2><p class="tt-sub">Shown next to the numbers on every quote for the products that offer them. V1 shows your text and takes the premium from the carrier\u2019s quote; it doesn\u2019t price coverage.</p>';
    s.coverage.forEach(function (c, i) {
      h += '<div class="tt-feerow"><div class="tt-f">' + '<label>Name</label>' + sInput('coverage.' + i + '.name', c.name) + '</div>' +
        '<div class="tt-f tt-wide" style="grid-column:1/-1;"><label>What it covers, as the customer sees it</label><textarea data-k="coverage.' + i + '.text">' + esc(c.text) + '</textarea></div>' +
        '<div>' + sToggle('coverage.' + i + '.required', c.required, 'Required (counted as a finance charge)') + '</div>' +
        (ctx.canEdit ? '<div><button type="button" class="tt-btn danger sm" data-act="del-coverage" data-i="' + i + '">Remove</button></div>' : '') + '</div>';
    });
    if (ctx.canEdit) h += '<button type="button" class="tt-btn ghost sm" data-act="add-coverage">Add a coverage option</button>';
    h += '<div class="tt-f" style="margin-top:12px;"><label>Insurance notice</label><textarea data-k="insuranceNotice">' + esc(s.insuranceNotice) + '</textarea></div>';
    h += '</div>';

    h += '<div class="tt-card"><h2>Disclosures</h2><p class="tt-sub">Your text, shown with every quote on every surface.</p>' +
      '<div class="tt-f"><textarea data-k="disclosure" style="min-height:110px;">' + esc(s.disclosure) + '</textarea></div></div>';

    h += '<div class="tt-card" id="tt-changes"><h2>Changes</h2>';
    h += data.log.length ? '<div class="tt-table-wrap"><table class="tt-table"><thead><tr><th>When</th><th>Who</th><th>What</th></tr></thead><tbody>' +
      data.log.slice(0, 15).map(function (l) { return '<tr><td>' + esc(stamp(l.at)) + '</td><td>' + esc(l.by) + '</td><td>' + esc(l.what) + '</td></tr>'; }).join('') +
      '</tbody></table></div>' : '<p class="tt-muted">No changes yet. Every save records who made it and when.</p>';
    h += '</div>';

    if (ctx.canEdit) {
      h += '<div class="tt-savebar"><div class="tt-actions"><button type="button" class="tt-btn primary" data-act="save">Save settings</button>' +
        '<button type="button" class="tt-btn ghost" data-act="reset">Start over with sample terms</button><span id="tt-save-msg" class="tt-small" role="status"></span></div>' +
        '<div id="tt-save-err"></div></div>';
    }
    return '<div class="tt-settings">' + h + '</div>';
  }

  function swatch(label, hex) {
    return '<span class="tt-swatch"><i style="background:' + esc(V.hexOk(hex) ? hex : '#fff') + ';"></i>' + esc(label) + ' ' + esc(hex || 'not set') + '</span>';
  }

  function productEditor(p, i, s) {
    var k = 'products.' + i + '.';
    var h = '<details class="tt-prod"' + (i === 0 ? ' open' : '') + '><summary><span><strong>' + esc(p.name) + '</strong><br><span class="tt-muted">' + esc(productLine(p)) +
      (p.website ? ' \u00b7 on the website' : '') + '</span></span></summary><div class="tt-prod-body">';
    h += '<div class="tt-grid">' + sField('Name', sInput(k + 'name', p.name), '', true);
    h += sField('Loan class', sSelect(k + 'loanClass', [['consumer', 'Consumer'], ['business', 'Business']], p.loanClass), 'Business loans are outside Regulation Z.');
    h += sField('A2B offering', sSelect(k + 'offering', [['', 'None']].concat(ctx.offerings.map(function (o) { return [o.slug, o.name]; })), p.offering), 'Where \u201cStart your application\u201d goes.');
    h += '</div>';
    h += '<h4>Loan types and payments</h4><div class="tt-grid">';
    h += sField('Loan types', sChecks(k + 'types', Object.keys(E.LOAN_TYPES).map(function (t) { return [t, E.LOAN_TYPES[t]]; }), p.types), '', true);
    h += sField('Payment frequencies', sChecks(k + 'frequencies', Object.keys(E.FREQUENCIES).map(function (f) { return [f, E.FREQUENCIES[f].label]; }), p.frequencies), '', true);
    h += sField('Starts as', sSelect(k + 'defaultType', Object.keys(E.LOAN_TYPES).map(function (t) { return [t, E.LOAN_TYPES[t]]; }), p.defaultType));
    h += sField('Paid', sSelect(k + 'defaultFrequency', Object.keys(E.FREQUENCIES).map(function (f) { return [f, E.FREQUENCIES[f].label]; }), p.defaultFrequency));
    h += '</div>';
    h += '<h4>Rate and ranges</h4><div class="tt-grid three">';
    h += sField('Rate %', sInput(k + 'rate', p.rate, ' inputmode="decimal"')) + sField('Lowest %', sInput(k + 'rateMin', p.rateMin, ' inputmode="decimal"')) + sField('Highest %', sInput(k + 'rateMax', p.rateMax, ' inputmode="decimal"'));
    h += sField('Amount $', sInput(k + 'amount', p.amount, ' inputmode="decimal"')) + sField('Least $', sInput(k + 'amountMin', p.amountMin, ' inputmode="decimal"')) + sField('Most $', sInput(k + 'amountMax', p.amountMax, ' inputmode="decimal"'));
    h += sField('Payments', sInput(k + 'payments', p.payments, ' inputmode="numeric"')) + sField('Fewest', sInput(k + 'paymentsMin', p.paymentsMin, ' inputmode="numeric"')) + sField('Most', sInput(k + 'paymentsMax', p.paymentsMax, ' inputmode="numeric"'));
    h += sField('Single payment: months', sInput(k + 'termMonths', p.termMonths, ' inputmode="numeric"')) + sField('Fewest', sInput(k + 'termMonthsMin', p.termMonthsMin, ' inputmode="numeric"')) + sField('Most', sInput(k + 'termMonthsMax', p.termMonthsMax, ' inputmode="numeric"'));
    h += '</div>';
    h += '<h4>Balloon</h4><div class="tt-grid">';
    h += sField('Set by', sSelect(k + 'balloonMethod', [['amortization', 'Amortization'], ['amount', 'Balloon amount']], p.balloonMethod));
    h += sField('Amortized over (payments)', sInput(k + 'amortization', p.amortization, ' inputmode="numeric"'));
    h += sField('The balloon', sSelect(k + 'balloonStyle', [['last', 'is the last payment'], ['extra', 'comes on top of the last payment']], p.balloonStyle), '', true);
    h += '</div>';
    h += '<h4>How interest is figured</h4><div class="tt-grid">';
    h += sField('Interest calendar', sSelect(k + 'calendar', Object.keys(E.CALENDARS).map(function (c) { return [c, E.CALENDARS[c]]; }), p.calendar));
    h += sField('Payment rounding', sSelect(k + 'rounding', [['nearest', 'To the nearest cent'], ['up', 'Up to the next cent']], p.rounding));
    h += sField('Odd first period', sSelect(k + 'oddDays', [['first', 'Extra interest on the first payment'], ['level', 'Spread over level payments']], p.oddDays), '', true);
    h += '</div>';
    h += '<h4>Fees</h4>';
    (p.fees || []).forEach(function (f, j) {
      var fk = k + 'fees.' + j + '.';
      h += '<div class="tt-feerow"><div class="tt-f tt-wide"><label>Fee</label>' + sInput(fk + 'name', f.name) + '</div>' +
        '<div class="tt-f"><label>Kind</label>' + sSelect(fk + 'kind', [['flat', 'Dollars'], ['percent', '% of loan']], f.kind) + '</div>' +
        '<div class="tt-f"><label>Amount</label>' + sInput(fk + 'value', f.value, ' inputmode="decimal"') + '</div>' +
        '<div>' + sToggle(fk + 'financed', f.financed, 'Financed') + '</div>' +
        '<div>' + sToggle(fk + 'financeCharge', f.financeCharge, 'Finance charge') + '</div>' +
        (ctx.canEdit ? '<div><button type="button" class="tt-btn danger sm" data-act="del-fee" data-i="' + i + '" data-j="' + j + '">Remove</button></div>' : '') + '</div>';
    });
    if (ctx.canEdit) h += '<button type="button" class="tt-btn ghost sm" data-act="add-fee" data-i="' + i + '">Add a fee</button>';
    h += '<h4>Coverage</h4>' + sToggle(k + 'insurance', p.insurance, 'Offer coverage on this product');
    h += '<div style="margin:8px 0 0 29px;">' + sChecks(k + 'coverage', s.coverage.map(function (c) { return [c.id, c.name]; }), p.coverage || []) + '</div>';
    h += '<h4>Website</h4>' + sToggle(k + 'website', p.website, 'Show on the website calculator, with this rate');
    if (ctx.canEdit) h += '<div style="margin-top:14px;"><button type="button" class="tt-btn danger sm" data-act="del-product" data-i="' + i + '">Remove this product</button></div>';
    h += '</div></details>';
    return h;
  }

  function setPath(obj, path, value) {
    var parts = path.split('.');
    var o = obj;
    for (var i = 0; i < parts.length - 1; i++) o = o[/^\d+$/.test(parts[i]) ? +parts[i] : parts[i]];
    o[parts[parts.length - 1]] = value;
  }

  var NUMERIC = /\.(rate|rateMin|rateMax|amount|amountMin|amountMax|payments|paymentsMin|paymentsMax|termMonths|termMonthsMin|termMonthsMax|amortization|value)$/;

  /** Copies the settings form into `draft`. */
  function readSettings() {
    if (!host || !$('.tt-settings')) return;
    $$('[data-k]').forEach(function (el) {
      var k = el.getAttribute('data-k');
      setPath(draft, k, NUMERIC.test(k) ? num(el.value) : el.value);
    });
    $$('[data-b]').forEach(function (el) { setPath(draft, el.getAttribute('data-b'), el.checked); });
    var arrays = {};
    $$('[data-arr]').forEach(function (el) {
      var k = el.getAttribute('data-arr');
      arrays[k] = arrays[k] || [];
      if (el.checked) arrays[k].push(el.value);
    });
    Object.keys(arrays).forEach(function (k) { setPath(draft, k, arrays[k]); });
  }

  function settingsProblems(s) {
    var out = [];
    s.products.forEach(function (p) {
      var n = p.name || 'A product';
      if (!String(p.name || '').trim()) out.push('Every product needs a name.');
      if (!p.types.length) out.push(n + ': pick at least one loan type.');
      if (!p.frequencies.length) out.push(n + ': pick at least one payment frequency.');
      if (p.types.length && p.types.indexOf(p.defaultType) < 0) out.push(n + ': it starts as a loan type it doesn\u2019t offer.');
      if (p.frequencies.length && p.frequencies.indexOf(p.defaultFrequency) < 0) out.push(n + ': it starts on a payment frequency it doesn\u2019t offer.');
      [['rate', 'rate'], ['amount', 'amount'], ['payments', 'number of payments'], ['termMonths', 'single-payment term']].forEach(function (x) {
        var v = p[x[0]], lo = p[x[0] + 'Min'], hi = p[x[0] + 'Max'];
        var show = x[0] === 'rate' ? function (r) { return E.formatRate(r, 2); }
          : x[0] === 'amount' ? dollars : x[0] === 'termMonths' ? function (m) { return m + ' months'; } : String;
        if (v === null || lo === null || hi === null) out.push(n + ': fill in the ' + x[1] + ' and its range.');
        else if (!(lo <= hi)) out.push(n + ': the lowest ' + x[1] + ' (' + show(lo) + ') is above the highest (' + show(hi) + ').');
        else if (!(lo <= v && v <= hi)) out.push(n + ': the ' + x[1] + ' it starts with, ' + show(v) + ', is outside its range of ' + show(lo) + ' to ' + show(hi) + '.');
      });
      if (p.types.indexOf('balloon') >= 0 && p.balloonMethod === 'amortization' && !(p.amortization > p.payments)) {
        out.push(n + ': the amortization has to be longer than the payments until the balloon.');
      }
      (p.fees || []).forEach(function (f) {
        if (!String(f.name || '').trim() || f.value === null || f.value < 0) out.push(n + ': every fee needs a name and an amount.');
      });
    });
    s.coverage.forEach(function (c) { if (!String(c.name || '').trim()) out.push('Every coverage option needs a name.'); });
    return out.filter(function (x, i, a) { return a.indexOf(x) === i; });
  }

  var FIELD_NAMES = {
    name: 'name', offering: 'offering', loanClass: 'class', types: 'loan types', defaultType: 'default loan type',
    frequencies: 'frequencies', defaultFrequency: 'default frequency', rate: 'rate', rateMin: 'lowest rate', rateMax: 'highest rate',
    amount: 'default amount', amountMin: 'smallest amount', amountMax: 'largest amount',
    payments: 'default payments', paymentsMin: 'fewest payments', paymentsMax: 'most payments',
    termMonths: 'default term', termMonthsMin: 'shortest term', termMonthsMax: 'longest term',
    balloonMethod: 'balloon set by', amortization: 'amortization', balloonAmount: 'balloon amount', balloonStyle: 'balloon',
    calendar: 'interest calendar', rounding: 'payment rounding', oddDays: 'odd days', insurance: 'coverage',
    coverage: 'coverage options', website: 'website',
  };

  function shown(k, v) {
    if (v === '' || v === null || v === undefined) return 'blank';
    if (/^rate/.test(k)) return E.formatRate(v, 2);
    if (/^amount|^balloonAmount$/.test(k)) return dollars(v);
    if (Array.isArray(v)) return v.join(', ') || 'none';
    if (typeof v === 'boolean') return v ? 'on' : 'off';
    return String(v);
  }

  function feeShown(x) { return x.kind === 'percent' ? E.formatRate(x.value, 2) : dollars(x.value); }

  function productDiff(a, b) {
    var out = [];
    Object.keys(FIELD_NAMES).forEach(function (k) {
      if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) out.push(FIELD_NAMES[k] + ' ' + shown(k, a[k]) + ' \u2192 ' + shown(k, b[k]));
    });
    for (var i = 0; i < Math.max(a.fees.length, b.fees.length); i++) {
      var x = a.fees[i], y = b.fees[i];
      if (!x) out.push('added ' + y.name);
      else if (!y) out.push('removed ' + x.name);
      else if (feeShown(x) !== feeShown(y)) out.push(y.name + ' ' + feeShown(x) + ' \u2192 ' + feeShown(y));
      else if (JSON.stringify(x) !== JSON.stringify(y)) out.push(y.name + ' changed');
    }
    return out;
  }

  function describeChanges(before, after) {
    var out = [];
    var bp = {}, ap = {};
    before.products.forEach(function (p) { bp[p.id] = p; });
    after.products.forEach(function (p) { ap[p.id] = p; });
    after.products.forEach(function (p) {
      if (!bp[p.id]) { out.push('added ' + p.name); return; }
      var d = productDiff(bp[p.id], p);
      if (d.length) out.push(p.name + ': ' + d.slice(0, 4).join('; ') + (d.length > 4 ? '; and ' + (d.length - 4) + ' more' : ''));
    });
    before.products.forEach(function (p) { if (!ap[p.id]) out.push('removed ' + p.name); });
    if (JSON.stringify(before.coverage) !== JSON.stringify(after.coverage)) out.push('changed the coverage options');
    if (before.insuranceNotice !== after.insuranceNotice) out.push('changed the insurance notice');
    if (before.disclosure !== after.disclosure) out.push('changed the disclosures');
    if (before.state !== after.state) out.push('state ' + before.state + ' \u2192 ' + after.state);
    if (JSON.stringify(before.website) !== JSON.stringify(after.website)) out.push('changed the website button');
    return out.length ? out.join(' \u00b7 ') : 'Saved with no changes';
  }

  function commit(next, what) {
    next.sample = false;
    var line = what || describeChanges(data.settings, next);
    data.settings = next;
    data.log.unshift({ at: new Date().toISOString(), by: ctx.user.name || ctx.user.email, what: line });
    saveData();
    draft = clone(data.settings);
    if (form && !data.settings.products.some(function (p) { return p.id === form.productId; })) form = freshForm(products()[0]);
    if (calc) stale = true;
    return line;
  }

  function settingsAction(act, i, j) {
    readSettings();
    if (act === 'add-product') { draft.products.push(product({ name: 'New product' })); }
    if (act === 'del-product') { draft.products.splice(i, 1); }
    if (act === 'add-fee') { draft.products[i].fees.push({ name: 'Fee', kind: 'flat', value: 0, financed: true, financeCharge: true }); }
    if (act === 'del-fee') { draft.products[i].fees.splice(j, 1); }
    if (act === 'add-coverage') { draft.coverage.push({ id: 'c_' + uid(), name: 'Coverage', text: '', required: false }); }
    if (act === 'del-coverage') {
      var gone = draft.coverage.splice(i, 1)[0];
      draft.products.forEach(function (p) { p.coverage = (p.coverage || []).filter(function (id) { return id !== gone.id; }); });
    }
    if (act === 'reset') {
      if (!window.confirm('Replace these settings with the sample terms?')) return;
      draft = seedSettings();
      var line = commit(draft, 'Started over with the sample terms');
      data.settings.sample = true;
      saveData();
      paintBody();
      flash(line);
      return;
    }
    if (act === 'save') {
      var problems = settingsProblems(draft);
      var err = document.getElementById('tt-save-err');
      if (problems.length) {
        if (err) err.innerHTML = '<div class="tt-alert error" style="margin-top:8px;"><strong>Not saved.</strong><ul>' + problems.map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('') + '</ul></div>';
        return;
      }
      var msg = commit(clone(draft));
      paintBody();
      flash(msg + '. The next quote uses it.');
      return;
    }
    paintBody();
  }

  function flash(text) {
    var m = document.getElementById('tt-save-msg');
    if (m) { m.textContent = text; m.className = 'tt-small tt-muted'; }
  }

  /* ------------------------------------------------------------ the frame */

  function paintTabs() {
    var el = $('.tt-tabs');
    if (!el) return;
    el.innerHTML = [['quote', 'Quote'], ['sent', 'Sent'], ['website', 'Website'], ['settings', 'Settings']].map(function (t) {
      var count = t[0] === 'sent' && data.sent.length ? '<span class="tt-count">' + data.sent.length + '</span>' : '';
      return '<button type="button" role="tab" class="tt-tab" data-tab="' + t[0] + '" aria-selected="' + (tab === t[0]) + '">' + t[1] + count + '</button>';
    }).join('');
  }

  function paintBody() {
    var body = $('.tt-body');
    if (!body) return;
    if (tab === 'quote') body.innerHTML = renderQuote();
    else if (tab === 'sent') body.innerHTML = renderSent();
    else if (tab === 'website') body.innerHTML = renderWebsite();
    else body.innerHTML = renderSettings();
    if (tab === 'settings' && !ctx.canEdit) $$('.tt-settings input, .tt-settings select, .tt-settings textarea').forEach(function (x) { x.disabled = true; });
    paintTabs();
  }

  function frame() {
    return '<div class="tt-app" data-tt-org="' + esc(ctx.slug) + '">' +
      '<div class="tt-head"><div><h1>TheTerms by Lenni</h1><p class="sub">Quotes on ' + esc(bankName()) + '\u2019s own terms: for its loan officers, its website and its customers\u2019 AI.</p></div>' +
      '<span class="tt-flag" title="Served on a2b-staging only, for review">Staging preview</span></div>' +
      '<div class="tt-tabs" role="tablist"></div><div class="tt-body"></div></div>';
  }

  function onClick(e) {
    var t = e.target.closest('button, a');
    if (!t || !host.contains(t)) return;
    if (t.hasAttribute('data-tab')) {
      if (tab === 'settings') readSettings();
      if (tab === 'quote') readForm();
      tab = t.getAttribute('data-tab');
      if (tab === 'settings') draft = clone(data.settings);
      paintBody();
      return;
    }
    if (t.id === 'tt-calc') { calculate(); return; }
    if (t.id === 'tt-mic') { listen(); return; }
    if (t.id === 'tt-all') { showAll = !showAll; paintResult(); return; }
    if (t.id === 'tt-text') { sendText(); return; }
    if (t.id === 'tt-copy') { copy(quoteLink(selected()), t); return; }
    if (t.id === 'tt-embed-copy') { copy(embedCode(), t); return; }
    if (t.id === 'tt-general') {
      history.pushState({}, '', panelPath('general'));
      window.dispatchEvent(new PopStateEvent('popstate'));
      return;
    }
    if (t.id === 'tt-web-save') {
      var next = clone(data.settings);
      $$('[data-web]').forEach(function (cb) {
        next.products.forEach(function (p) { if (p.id === cb.getAttribute('data-web')) p.website = cb.checked; });
      });
      next.website = { cta: document.getElementById('tt-cta').value, url: document.getElementById('tt-cta-url').value };
      var line = commit(next);
      paintBody();
      var ok = document.getElementById('tt-web-ok');
      if (ok) ok.textContent = line + '.';
      return;
    }
    if (t.hasAttribute('data-frame')) { frameWide = t.getAttribute('data-frame') === 'wide'; paintBody(); return; }
    if (t.hasAttribute('data-solve')) { readForm(); form.solveFor = t.getAttribute('data-solve'); if (calc) stale = true; paintBody(); return; }
    if (t.hasAttribute('data-bmethod')) { readForm(); form.balloonMethod = t.getAttribute('data-bmethod'); if (calc) stale = true; paintBody(); return; }
    if (t.hasAttribute('data-opt')) { form.selected = t.getAttribute('data-opt'); paintResult(); return; }
    if (t.hasAttribute('data-act')) {
      settingsAction(t.getAttribute('data-act'), +t.getAttribute('data-i'), +t.getAttribute('data-j'));
    }
  }

  function onChange(e) {
    var t = e.target;
    if (!host.contains(t)) return;
    if (t.id === 'tt-product') {
      form = freshForm(productById(t.value));
      calc = null; stale = false;
      paintBody();
      return;
    }
    if (t.id === 'tt-type') {
      readForm();
      if (E.SOLVABLE[form.loanType].indexOf(form.solveFor) < 0) form.solveFor = 'payment';
      if (calc) stale = true;
      paintBody();
      return;
    }
    if (t.id === 'tt-frequency' || t.id === 'tt-closingDate' || t.id === 'tt-termMonths') {
      readForm();
      if (t.id !== 'tt-termMonths') form.firstPaymentDate = '';
      if (calc) stale = true;
      paintBody();
      return;
    }
    if (t.closest('.tt-col-form') && calc && !stale) { stale = true; paintResult(); }
  }

  function onInput(e) {
    var t = e.target;
    if (!host.contains(t)) return;
    if (t.id === 'tt-phone' || t.id === 'tt-consent') {
      var btn = document.getElementById('tt-text');
      if (btn) btn.disabled = !(phoneOk(document.getElementById('tt-phone').value) && document.getElementById('tt-consent').checked);
      return;
    }
    if (t.closest('.tt-col-form') && t.id !== 'tt-say' && calc && !stale) { stale = true; paintResult(); }
  }

  function onKey(e) {
    if (e.target && e.target.id === 'tt-say' && e.key === 'Enter') { e.preventDefault(); applySaid(e.target.value); }
  }

  function copy(text, btn) {
    var done = function () { var was = btn.textContent; btn.textContent = 'Copied'; setTimeout(function () { btn.textContent = was; }, 1400); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { window.prompt('Copy this:', text); });
    else window.prompt('Copy this:', text);
  }

  function ensureStyles() {
    if (document.getElementById('tt-css')) return;
    var l = document.createElement('link');
    l.id = 'tt-css';
    l.rel = 'stylesheet';
    l.href = BASE + 'theterms.css';
    document.head.appendChild(l);
  }

  function mount(el) {
    var slug = orgSlug();
    mounting = true;
    ensureStyles();
    el.innerHTML = '<div class="tt-app"><p class="tt-muted" style="padding:8px 0;">Opening TheTerms\u2026</p></div>';
    var same = ctx && ctx.slug === slug;
    (same ? Promise.resolve(ctx) : loadContext(slug)).then(function (c) {
      // The officer may have moved on while the organisation loaded.
      if (!onPanel() || orgSlug() !== slug || !el.classList.contains('on')) return;
      if (!same) {
        ctx = c;
        data = loadData();
        form = freshForm(products()[0]);
        calc = null; stale = false; tab = 'quote';
      } else {
        ctx.brand = brandOf(ctx.vendor);
      }
      host = el;
      el.innerHTML = frame();
      if (!el.dataset.ttWired) {
        el.dataset.ttWired = '1';
        el.addEventListener('click', onClick);
        el.addEventListener('change', onChange);
        el.addEventListener('input', onInput);
        el.addEventListener('keydown', onKey);
      }
      paintBody();
    }).catch(function (err) {
      warn(err);
      el.innerHTML = '<div class="tt-app"><div class="tt-alert error">' + esc(err.message || 'TheTerms could not open.') + '</div></div>';
    }).then(function () { mounting = false; });
  }

  function check() {
    if (mounting || !onPanel()) return;
    var el = document.getElementById('ws-sku');
    if (!el || !el.classList.contains('on')) return;
    if (el.querySelector('.tt-app')) return;
    if (!el.querySelector('.page-head, .card')) return;
    mount(el);
  }

  // A timer rather than requestAnimationFrame, which never fires in a tab
  // opened in the background.
  var queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    setTimeout(function () { queued = false; check(); }, 0);
  }

  function start() {
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    window.addEventListener('popstate', schedule);
    schedule();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
