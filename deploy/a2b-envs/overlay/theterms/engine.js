/**
 * TheTerms calculation engine (#136). Staging preview.
 *
 * No DOM. The officer's quote screen, the customer's quote page and the website
 * calculator all load this one file, so a quote reads the same on all three.
 *
 * Money is held in whole cents. Interest is rounded to the cent each period and
 * the last payment clears whatever is left, which is how the reference
 * calculators in docs/LOAN_CALCULATOR_RESEARCH.md arrive at their figures (a
 * level payment for 59 months and a last payment a few cents off). The APR is
 * the actuarial APR of Regulation Z, Appendix J.
 *
 * V1 per #134: Texas only, though state is an input; installment, balloon and
 * single-payment loans; monthly, quarterly, semi-annual and annual payments;
 * fees set by the bank; an insurance premium passed in rather than computed; no
 * refunds. The engine never invents a rate: every rate it uses is an input,
 * which on every surface comes from the bank's own settings.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TheTermsEngine = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var VERSION = '0.1.0';

  var STATES = { TX: { name: 'Texas' } };

  var FREQUENCIES = {
    monthly:    { label: 'Monthly',     months: 1,  perYear: 12, noun: 'month',   adverb: 'monthly' },
    quarterly:  { label: 'Quarterly',   months: 3,  perYear: 4,  noun: 'quarter', adverb: 'quarterly' },
    semiannual: { label: 'Semi-annual', months: 6,  perYear: 2,  noun: 'half-year', adverb: 'every six months' },
    annual:     { label: 'Annual',      months: 12, perYear: 1,  noun: 'year',    adverb: 'yearly' },
  };

  var LOAN_TYPES = { installment: 'Installment', balloon: 'Balloon', single: 'Single payment' };

  var SOLVE_FOR = {
    payment: 'Payment',
    amount: 'Loan amount',
    term: 'Number of payments',
    balloon: 'Balloon amount',
  };

  // Which Solve For targets each loan type can take.
  var SOLVABLE = {
    installment: ['payment', 'amount', 'term'],
    balloon: ['payment', 'amount', 'balloon'],
    single: ['payment', 'amount'],
  };

  var CALENDARS = {
    periodic: 'Equal periods (30/360)',
    actual365: 'Actual days, 365-day year',
    actual360: 'Actual days, 360-day year',
  };

  var MAX_PAYMENTS = 1200;

  /* ------------------------------------------------------------ dates */

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function parseDate(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || ''));
    if (!m) return null;
    var d = { y: +m[1], m: +m[2], d: +m[3] };
    if (d.m < 1 || d.m > 12 || d.d < 1 || d.d > daysInMonth(d.y, d.m)) return null;
    return d;
  }

  function iso(d) { return d.y + '-' + pad(d.m) + '-' + pad(d.d); }

  function daysInMonth(y, m) { return new Date(Date.UTC(y, m, 0)).getUTCDate(); }

  function dayNumber(d) { return Math.round(Date.UTC(d.y, d.m - 1, d.d) / 86400000); }

  function daysBetween(a, b) { return dayNumber(b) - dayNumber(a); }

  /** k months after d, on the anchor day or the month's last day if shorter. */
  function addMonths(d, k, anchorDay) {
    var idx = d.y * 12 + (d.m - 1) + k;
    var y = Math.floor(idx / 12);
    var m = idx - y * 12 + 1;
    var day = Math.min(anchorDay || d.d, daysInMonth(y, m));
    return { y: y, m: m, d: day };
  }

  function todayIso() {
    var t = new Date();
    return t.getFullYear() + '-' + pad(t.getMonth() + 1) + '-' + pad(t.getDate());
  }

  /**
   * Full unit-periods and the fraction left over between two dates, counted the
   * way Appendix J (b)(5) counts them: whole months measured back from the
   * later date, the remaining days forward from the earlier one.
   */
  function unitSpan(start, end, unitMonths, anchorDay) {
    var months = 0;
    while (dayNumber(addMonths(end, -(months + 1), anchorDay)) >= dayNumber(start)) months++;
    var edge = addMonths(end, -months, anchorDay);
    var days = daysBetween(start, edge);
    if (unitMonths === 1) return { t: months, f: days / 30 };
    if (unitMonths === 12) {
      var years = Math.floor(months / 12);
      var restMonths = months - years * 12;
      if (days === 0) return { t: years, f: restMonths / 12 };
      return { t: years, f: daysBetween(start, addMonths(end, -12 * years, anchorDay)) / 365 };
    }
    var total = 30 * months + days;
    var per = 30 * unitMonths;
    var t = Math.floor(total / per);
    return { t: t, f: (total - t * per) / per };
  }

  /* ------------------------------------------------------------ money */

  function cents(dollars) { return Math.round(Number(dollars) * 100); }

  function roundCents(x) { return Math.floor(x + 0.5 + 1e-9); }

  function formatMoney(c, opts) {
    var neg = c < 0;
    var v = Math.abs(Math.round(c));
    var whole = Math.floor(v / 100);
    var frac = v - whole * 100;
    var s = String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    var out = '$' + s + (opts && opts.noCents && frac === 0 ? '' : '.' + pad(frac));
    return neg ? '-' + out : out;
  }

  function formatRate(r, places) {
    if (r === null || r === undefined || !isFinite(r)) return '';
    var p = places === undefined ? 3 : places;
    return Number(r).toFixed(p) + '%';
  }

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function formatDate(s) {
    var d = typeof s === 'string' ? parseDate(s) : s;
    if (!d) return '';
    return MONTHS[d.m - 1] + ' ' + d.d + ', ' + d.y;
  }

  /* ------------------------------------------------------------ the schedule */

  /**
   * Interest rate for each period, as a fraction of the balance at its start.
   * Equal periods use the periodic rate, with the first period stretched or
   * shortened by its Appendix J fraction; the actual-day calendars use the days
   * in each period.
   */
  function periodRates(closing, dates, freq, calendar, annual, anchorDay) {
    var rates = [];
    var prev = closing;
    var r = annual / freq.perYear;
    for (var j = 0; j < dates.length; j++) {
      if (calendar === 'periodic') {
        if (j === 0) {
          var span = unitSpan(closing, dates[0], freq.months, anchorDay);
          rates.push(r * (span.t + span.f));
        } else {
          rates.push(r);
        }
      } else {
        var basis = calendar === 'actual360' ? 360 : 365;
        rates.push(annual * daysBetween(prev, dates[j]) / basis);
      }
      prev = dates[j];
    }
    return rates;
  }

  function paymentDates(first, count, freq, anchorDay) {
    var out = [];
    for (var j = 0; j < count; j++) out.push(addMonths(first, j * freq.months, anchorDay));
    return out;
  }

  /**
   * The balance left after the last payment when every payment is `pmt`, the
   * last one is `last(pmt)`, and nothing is rounded. Used to solve for a level
   * payment or a principal: the result is affine in both.
   */
  function endBalance(principal, rates, pmt, lastPayment) {
    var bal = principal;
    for (var j = 0; j < rates.length; j++) {
      var pay = j === rates.length - 1 ? lastPayment(pmt) : pmt;
      bal = bal * (1 + rates[j]) - pay;
    }
    return bal;
  }

  function solveLinear(fn) {
    var a = fn(0);
    var b = fn(1) - a;
    if (Math.abs(b) < 1e-15) return NaN;
    return -a / b;
  }

  /* ------------------------------------------------------------ APR (Appendix J) */

  /**
   * The actuarial APR for one advance of `amountFinanced` repaid by `flows`
   * ({amount, t, f}): the rate i per unit-period that solves
   *   A = sum P / ((1 + f i)(1 + i)^t)
   * times the unit-periods in a year.
   */
  function aprFromFlows(amountFinanced, flows, perYear) {
    if (!(amountFinanced > 0) || !flows.length) return null;
    function pv(i) {
      var s = 0;
      for (var k = 0; k < flows.length; k++) {
        s += flows[k].amount / ((1 + flows[k].f * i) * Math.pow(1 + i, flows[k].t));
      }
      return s;
    }
    var lo, hi;
    if (pv(0) >= amountFinanced) {
      lo = 0; hi = 1;
      while (pv(hi) > amountFinanced && hi < 1e6) { lo = hi; hi *= 2; }
    } else {
      lo = -0.99; hi = 0;
    }
    for (var n = 0; n < 200; n++) {
      var mid = (lo + hi) / 2;
      if (pv(mid) > amountFinanced) lo = mid; else hi = mid;
    }
    return ((lo + hi) / 2) * perYear * 100;
  }

  function scheduleApr(amountFinanced, rows, closing, freq, anchorDay) {
    var flows = rows.map(function (row) {
      var span = unitSpan(closing, parseDate(row.date), freq.months, anchorDay);
      return { amount: row.payment, t: span.t, f: span.f };
    });
    return aprFromFlows(amountFinanced, flows, freq.perYear);
  }

  /** Appendix J (b)(4)(ii), (b)(5)(vi)-(vii): one advance, one payment. */
  function singlePaymentApr(amountFinanced, payment, closing, maturity) {
    var span = unitSpan(closing, maturity, 1, maturity.d);
    var days = daysBetween(closing, maturity);
    if (days <= 0) return null;
    var wholeMonths = span.f === 0;
    if (wholeMonths && span.t <= 12) {
      return (payment / amountFinanced - 1) * (12 / span.t) * 100;
    }
    if (!wholeMonths && days <= 365) {
      return (payment / amountFinanced - 1) * (365 / days) * 100;
    }
    var years = unitSpan(closing, maturity, 12, maturity.d);
    return aprFromFlows(amountFinanced, [{ amount: payment, t: years.t, f: years.f }], 1);
  }

  /* ------------------------------------------------------------ inputs */

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = typeof v === 'number' ? v : Number(String(v).replace(/[$,%\s]/g, ''));
    return isFinite(n) ? n : null;
  }

  function normalize(input) {
    var i = input || {};
    var out = {
      state: String(i.state || 'TX').toUpperCase(),
      loanClass: i.loanClass === 'business' ? 'business' : 'consumer',
      loanType: LOAN_TYPES[i.loanType] ? i.loanType : 'installment',
      solveFor: SOLVE_FOR[i.solveFor] ? i.solveFor : 'payment',
      frequency: FREQUENCIES[i.frequency] ? i.frequency : 'monthly',
      amount: num(i.amount),
      payment: num(i.payment),
      rate: num(i.rate),
      payments: num(i.payments),
      termMonths: num(i.termMonths),
      balloonMethod: i.balloonMethod === 'amount' ? 'amount' : 'amortization',
      amortization: num(i.amortization),
      balloonAmount: num(i.balloonAmount),
      balloonStyle: i.balloonStyle === 'extra' ? 'extra' : 'last',
      closingDate: i.closingDate || todayIso(),
      firstPaymentDate: i.firstPaymentDate || '',
      calendar: CALENDARS[i.calendar] ? i.calendar : 'periodic',
      rounding: i.rounding === 'up' ? 'up' : 'nearest',
      oddDays: i.oddDays === 'level' ? 'level' : 'first',
      fees: (Array.isArray(i.fees) ? i.fees : []).map(function (f) {
        return {
          name: String(f.name || 'Fee'),
          kind: f.kind === 'percent' ? 'percent' : 'flat',
          value: num(f.value) || 0,
          financed: f.financed !== false,
          financeCharge: f.financeCharge !== false,
        };
      }),
      premium: num(i.premium) || 0,
      premiumRequired: !!i.premiumRequired,
    };
    if (out.loanType === 'single') out.frequency = 'monthly';
    return out;
  }

  function fail(errors) { return { ok: false, errors: errors }; }

  function checkInputs(q) {
    var e = [];
    if (!STATES[q.state]) e.push({ field: 'state', message: 'TheTerms is set up for Texas only so far.' });
    if (SOLVABLE[q.loanType].indexOf(q.solveFor) < 0) {
      e.push({ field: 'solveFor', message: LOAN_TYPES[q.loanType] + ' loans can\u2019t solve for ' + SOLVE_FOR[q.solveFor].toLowerCase() + '.' });
    }
    if (q.rate === null || q.rate < 0 || q.rate >= 100) e.push({ field: 'rate', message: 'Enter the interest rate.' });
    if (q.solveFor !== 'amount' && !(q.amount > 0)) e.push({ field: 'amount', message: 'Enter the loan amount.' });
    if ((q.solveFor === 'amount' || q.solveFor === 'term' || q.solveFor === 'balloon') && !(q.payment > 0)) {
      e.push({ field: 'payment', message: 'Enter the payment to solve from.' });
    }
    if (q.loanType === 'single') {
      if (!(q.termMonths >= 1) || q.termMonths !== Math.floor(q.termMonths)) e.push({ field: 'termMonths', message: 'Enter the term in whole months.' });
    } else if (q.solveFor !== 'term') {
      if (!(q.payments >= 1) || q.payments !== Math.floor(q.payments) || q.payments > MAX_PAYMENTS) {
        e.push({ field: 'payments', message: 'Enter the number of payments.' });
      }
    }
    if (q.loanType === 'balloon') {
      if (q.balloonMethod === 'amortization' && q.solveFor !== 'balloon') {
        if (!(q.amortization >= 1) || q.amortization !== Math.floor(q.amortization) || q.amortization > MAX_PAYMENTS) {
          e.push({ field: 'amortization', message: 'Enter the amortization in payments.' });
        } else if (q.payments && q.amortization <= q.payments) {
          e.push({ field: 'amortization', message: 'The amortization must be longer than the term, or there is no balloon.' });
        }
      }
      if (q.balloonMethod === 'amount' && q.solveFor !== 'balloon' && !(q.balloonAmount > 0)) {
        e.push({ field: 'balloonAmount', message: 'Enter the balloon amount.' });
      }
    }
    if (!parseDate(q.closingDate)) e.push({ field: 'closingDate', message: 'Enter the closing date.' });
    if (q.firstPaymentDate && !parseDate(q.firstPaymentDate)) e.push({ field: 'firstPaymentDate', message: 'Enter the first payment date.' });
    var c = parseDate(q.closingDate), f = parseDate(q.firstPaymentDate);
    if (c && f && dayNumber(f) <= dayNumber(c)) e.push({ field: 'firstPaymentDate', message: 'The first payment has to come after closing.' });
    if (q.premium < 0) e.push({ field: 'premium', message: 'The premium can\u2019t be negative.' });
    return e;
  }

  /* ------------------------------------------------------------ the quote */

  function feeLines(fees, amountCents) {
    return fees.map(function (f) {
      var c = f.kind === 'percent' ? roundCents(amountCents * f.value / 100) : cents(f.value);
      return { name: f.name, kind: f.kind, value: f.value, cents: c, financed: f.financed, financeCharge: f.financeCharge };
    });
  }

  /** The note amount: the cash to the borrower plus whatever is financed with it. */
  function principalFor(amountCents, q) {
    var fees = feeLines(q.fees, amountCents);
    var p = amountCents + cents(q.premium);
    fees.forEach(function (f) { if (f.financed) p += f.cents; });
    return { principal: p, fees: fees };
  }

  /** Inverse of principalFor: the loan amount that produces a note of `principal`. */
  function amountForPrincipal(principal, q) {
    var flat = cents(q.premium), pct = 0;
    q.fees.forEach(function (f) {
      if (!f.financed) return;
      if (f.kind === 'percent') pct += f.value / 100; else flat += cents(f.value);
    });
    return (principal - flat) / (1 + pct);
  }

  function frame(q) {
    var freq = FREQUENCIES[q.frequency];
    var closing = parseDate(q.closingDate);
    var first = q.firstPaymentDate ? parseDate(q.firstPaymentDate) : addMonths(closing, freq.months, closing.d);
    return { freq: freq, closing: closing, first: first, anchor: first.d, annual: q.rate / 100 };
  }

  /** Rates for solving: in 'first' mode the first period counts as a regular one. */
  function solvingRates(fr, q, count) {
    var dates = paymentDates(fr.first, count, fr.freq, fr.anchor);
    var start = q.oddDays === 'first' ? addMonths(fr.first, -fr.freq.months, fr.anchor) : fr.closing;
    return periodRates(start, dates, fr.freq, q.calendar, fr.annual, fr.anchor);
  }

  function roundPayment(x, q) {
    return q.rounding === 'up' ? Math.ceil(x - 1e-7) : roundCents(x);
  }

  /** The exact level payment for the loan's shape, before rounding. */
  function levelPayment(principal, fr, q) {
    if (q.loanType === 'balloon' && q.balloonMethod === 'amortization') {
      var ra = solvingRates(fr, q, q.amortization);
      return solveLinear(function (p) { return endBalance(principal, ra, p, function (x) { return x; }); });
    }
    var rates = solvingRates(fr, q, q.payments);
    var last = function (x) { return x; };
    if (q.loanType === 'balloon') {
      var b = cents(q.balloonAmount);
      last = q.balloonStyle === 'extra' ? function (x) { return x + b; } : function () { return b; };
    }
    return solveLinear(function (p) { return endBalance(principal, rates, p, last); });
  }

  /**
   * Runs the schedule in cents: interest rounded each period, the payment level,
   * the first payment carrying any odd-days interest in 'first' mode, and the
   * last payment clearing the balance. With `untilPaid`, it stops at whichever
   * payment clears the loan (Solve For: number of payments).
   */
  function runSchedule(principal, pmt, fr, q, count, untilPaid) {
    var n = untilPaid ? MAX_PAYMENTS : count;
    var dates = paymentDates(fr.first, n, fr.freq, fr.anchor);
    var actual = periodRates(fr.closing, dates, fr.freq, q.calendar, fr.annual, fr.anchor);
    var regularFirst = q.oddDays === 'first'
      ? periodRates(addMonths(fr.first, -fr.freq.months, fr.anchor), dates.slice(0, 1), fr.freq, q.calendar, fr.annual, fr.anchor)[0]
      : actual[0];
    var rows = [];
    var bal = principal;
    for (var j = 0; j < n; j++) {
      var interest = roundCents(bal * actual[j]);
      var pay = pmt;
      if (j === 0 && q.oddDays === 'first') pay += interest - roundCents(bal * regularFirst);
      var isLast = untilPaid ? bal + interest <= pay : j === n - 1;
      if (!isLast && !untilPaid && bal + interest <= pay) {
        return { error: 'At ' + formatMoney(pay) + ' the loan is paid off after ' + (j + 1) + ' payments, before the term ends.' };
      }
      if (isLast) pay = bal + interest;
      if (!isLast && pay <= interest) {
        return { error: 'A payment of ' + formatMoney(pay) + ' doesn\u2019t cover the interest of ' + formatMoney(interest) + '.' };
      }
      var princ = pay - interest;
      bal -= princ;
      rows.push({ n: j + 1, date: iso(dates[j]), payment: pay, interest: interest, principal: princ, balance: bal });
      if (isLast) break;
    }
    if (untilPaid && bal !== 0) return { error: 'That payment would take more than ' + MAX_PAYMENTS + ' payments.' };
    return { rows: rows };
  }

  function groupsOf(rows) {
    var out = [];
    rows.forEach(function (r) {
      var g = out[out.length - 1];
      if (g && g.amount === r.payment) { g.count++; g.last = r.date; } else out.push({ count: 1, amount: r.payment, first: r.date, last: r.date });
    });
    return out;
  }

  function disclosureTotals(rows, principal, fees, q) {
    var top = 0, interest = 0;
    rows.forEach(function (r) { top += r.payment; interest += r.interest; });
    var prepaid = 0;
    fees.forEach(function (f) { if (f.financeCharge) prepaid += f.cents; });
    if (q.premiumRequired) prepaid += cents(q.premium);
    var financedFC = 0;
    fees.forEach(function (f) { if (f.financeCharge && f.financed) financedFC += f.cents; });
    var cashFC = 0;
    fees.forEach(function (f) { if (f.financeCharge && !f.financed) cashFC += f.cents; });
    var requiredPremium = q.premiumRequired ? cents(q.premium) : 0;
    var amountFinanced = principal - financedFC - cashFC - requiredPremium;
    return {
      totalOfPayments: top,
      totalInterest: interest,
      prepaidFinanceCharge: prepaid,
      amountFinanced: amountFinanced,
      financeCharge: top - amountFinanced,
    };
  }

  function calculate(input) {
    var q = normalize(input);
    var errors = checkInputs(q);
    if (errors.length) return fail(errors);
    var fr = frame(q);
    var amountCents, principal, fees, rows, pmt;

    if (q.loanType === 'single') return calculateSingle(q, fr);

    if (q.solveFor === 'amount') {
      var target = cents(q.payment);
      var p0 = solveLinear(function (pr) {
        if (q.loanType === 'balloon' && q.balloonMethod === 'amortization') {
          return levelPayment(pr, fr, q) - target;
        }
        var rates = solvingRates(fr, q, q.payments);
        var b = q.loanType === 'balloon' ? cents(q.balloonAmount) : 0;
        var last = q.loanType !== 'balloon' ? function (x) { return x; }
          : (q.balloonStyle === 'extra' ? function (x) { return x + b; } : function () { return b; });
        return endBalance(pr, rates, target, last);
      });
      amountCents = Math.floor(amountForPrincipal(p0, q));
      for (var tries = 0; tries < 50; tries++) {
        var pf = principalFor(amountCents, q);
        var lp = roundPayment(levelPayment(pf.principal, fr, q), q);
        if (lp <= target) break;
        amountCents--;
      }
      if (!(amountCents > 0)) return fail([{ field: 'payment', message: 'That payment doesn\u2019t support a loan on these terms.' }]);
    } else {
      amountCents = cents(q.amount);
    }

    var pp = principalFor(amountCents, q);
    principal = pp.principal;
    fees = pp.fees;

    if (q.solveFor === 'term' || q.solveFor === 'balloon') {
      pmt = cents(q.payment);
    } else {
      var exact = levelPayment(principal, fr, q);
      if (!isFinite(exact) || exact <= 0) return fail([{ field: 'payment', message: 'These terms don\u2019t produce a payment.' }]);
      pmt = roundPayment(exact, q);
    }

    var run = runSchedule(principal, pmt, fr, q, q.payments, q.solveFor === 'term');
    if (run.error) return fail([{ field: 'payment', message: run.error }]);
    rows = run.rows;
    var last = rows[rows.length - 1];

    var balloon = 0;
    if (q.loanType === 'balloon') balloon = q.balloonStyle === 'extra' ? last.payment - pmt : last.payment;
    if (q.loanType === 'balloon' && balloon <= 0) {
      return fail([{ field: 'payment', message: 'At that payment the loan pays off before the balloon.' }]);
    }

    var totals = disclosureTotals(rows, principal, fees, q);
    var apr = scheduleApr(totals.amountFinanced, rows, fr.closing, fr.freq, fr.anchor);
    var solvedValue = { payment: pmt, amount: amountCents, term: rows.length, balloon: balloon }[q.solveFor];

    return finish(q, fr, {
      amount: amountCents, principal: principal, fees: fees, premium: cents(q.premium),
      payment: pmt, firstPayment: rows[0].payment, finalPayment: last.payment, balloon: balloon,
      count: rows.length, rows: rows, totals: totals, apr: apr, solved: solvedValue,
    });
  }

  function calculateSingle(q, fr) {
    var maturity = addMonths(fr.closing, q.termMonths, fr.closing.d);
    if (q.firstPaymentDate) maturity = parseDate(q.firstPaymentDate);
    var days = daysBetween(fr.closing, maturity);
    var factor;
    if (q.calendar === 'periodic') {
      var span = unitSpan(fr.closing, maturity, 1, maturity.d);
      factor = fr.annual * (span.t + span.f) / 12;
    } else {
      factor = fr.annual * days / (q.calendar === 'actual360' ? 360 : 365);
    }
    var amountCents;
    if (q.solveFor === 'amount') {
      var target = cents(q.payment);
      amountCents = Math.floor(amountForPrincipal(target / (1 + factor), q));
      for (var tries = 0; tries < 50; tries++) {
        var pf = principalFor(amountCents, q);
        if (pf.principal + roundCents(pf.principal * factor) <= target) break;
        amountCents--;
      }
      if (!(amountCents > 0)) return fail([{ field: 'payment', message: 'That payment doesn\u2019t support a loan on these terms.' }]);
    } else {
      amountCents = cents(q.amount);
    }
    var pp = principalFor(amountCents, q);
    var interest = roundCents(pp.principal * factor);
    var pay = pp.principal + interest;
    var rows = [{ n: 1, date: iso(maturity), payment: pay, interest: interest, principal: pp.principal, balance: 0 }];
    var totals = disclosureTotals(rows, pp.principal, pp.fees, q);
    var apr = singlePaymentApr(totals.amountFinanced, pay, fr.closing, maturity);
    return finish(q, fr, {
      amount: amountCents, principal: pp.principal, fees: pp.fees, premium: cents(q.premium),
      payment: pay, firstPayment: pay, finalPayment: pay, balloon: 0,
      count: 1, rows: rows, totals: totals, apr: apr,
      solved: q.solveFor === 'amount' ? amountCents : pay,
    });
  }

  function finish(q, fr, r) {
    var oddDays = 0;
    if (q.loanType !== 'single') {
      var regularStart = addMonths(fr.first, -fr.freq.months, fr.anchor);
      oddDays = daysBetween(fr.closing, regularStart);
    }
    return {
      ok: true,
      engine: VERSION,
      input: q,
      solved: { field: q.solveFor, label: SOLVE_FOR[q.solveFor], value: r.solved },
      amount: r.amount,
      principal: r.principal,
      fees: r.fees,
      premium: r.premium,
      payment: r.payment,
      firstPayment: r.firstPayment,
      finalPayment: r.finalPayment,
      balloon: r.balloon,
      count: r.count,
      closingDate: iso(fr.closing),
      firstPaymentDate: r.rows[0].date,
      maturityDate: r.rows[r.rows.length - 1].date,
      oddDays: oddDays,
      schedule: r.rows,
      groups: groupsOf(r.rows),
      totals: r.totals,
      apr: r.apr,
      regZ: q.loanClass === 'consumer',
    };
  }

  /* ------------------------------------------------------------ the bank's ranges */

  /**
   * Reasons a request falls outside what the bank set for the product (#137).
   * A request outside the range is refused with its reason, never adjusted.
   */
  function outsideRange(product, q, result, bankName) {
    var who = bankName || 'The bank';
    var name = product.name || 'this product';
    var out = [];
    var amt = result && result.ok ? result.amount / 100 : num(q.amount);
    if (product.amountMin != null && amt != null && amt < product.amountMin) {
      out.push(who + ' lends ' + rangeText(product.amountMin, product.amountMax) + ' on ' + name + '. ' + money(amt) + ' is less than that.');
    }
    if (product.amountMax != null && amt != null && amt > product.amountMax) {
      out.push(who + ' lends ' + rangeText(product.amountMin, product.amountMax) + ' on ' + name + '. ' + money(amt) + ' is more than that.');
    }
    var count = q.loanType === 'single' ? null : (result && result.ok ? result.count : num(q.payments));
    if (count != null && product.paymentsMin != null && count < product.paymentsMin) {
      out.push(name + ' runs ' + product.paymentsMin + ' to ' + product.paymentsMax + ' payments. ' + count + ' is fewer.');
    }
    if (count != null && product.paymentsMax != null && count > product.paymentsMax) {
      out.push(name + ' runs ' + product.paymentsMin + ' to ' + product.paymentsMax + ' payments. ' + count + ' is more.');
    }
    var rate = num(q.rate);
    if (rate != null && product.rateMin != null && rate < product.rateMin) {
      out.push(who + '\u2019s rate on ' + name + ' is ' + formatRate(product.rateMin, 2) + ' to ' + formatRate(product.rateMax, 2) + '. ' + formatRate(rate, 2) + ' is below it.');
    }
    if (rate != null && product.rateMax != null && rate > product.rateMax) {
      out.push(who + '\u2019s rate on ' + name + ' is ' + formatRate(product.rateMin, 2) + ' to ' + formatRate(product.rateMax, 2) + '. ' + formatRate(rate, 2) + ' is above it.');
    }
    if (Array.isArray(product.types) && product.types.indexOf(q.loanType) < 0) {
      out.push(name + ' isn\u2019t offered as a ' + LOAN_TYPES[q.loanType].toLowerCase() + ' loan.');
    }
    if (q.loanType !== 'single' && Array.isArray(product.frequencies) && product.frequencies.indexOf(q.frequency) < 0) {
      out.push(name + ' doesn\u2019t take ' + FREQUENCIES[q.frequency].label.toLowerCase() + ' payments.');
    }
    return out;
  }

  function money(d) { return formatMoney(Math.round(d * 100), { noCents: true }); }
  function rangeText(lo, hi) { return money(lo) + ' to ' + money(hi); }

  /* ------------------------------------------------------------ "say it" */

  var UNITS = {
    zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
    eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17,
    eighteen: 18, nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70,
    eighty: 80, ninety: 90,
  };
  var SCALES = { hundred: 100, thousand: 1000, million: 1000000 };

  /** "two hundred fifty thousand" -> "250000", "seven and a quarter" -> "7.25". */
  function wordsToDigits(text) {
    var tokens = text.split(/(\s+|-)/);
    var out = [];
    var i = 0;
    while (i < tokens.length) {
      var w = tokens[i];
      if (UNITS[w] === undefined) { out.push(w); i++; continue; }
      var total = 0, current = 0, j = i, used = i;
      while (j < tokens.length) {
        var t = tokens[j];
        if (/^(\s+|-)$/.test(t) || t === '') { j++; continue; }
        if (UNITS[t] !== undefined) { current += UNITS[t]; used = j; j++; continue; }
        if (t === 'hundred') { current *= 100; used = j; j++; continue; }
        if (t === 'thousand' || t === 'million') { total += current * SCALES[t]; current = 0; used = j; j++; continue; }
        if (t === 'and' && /^(\s+)$/.test(tokens[j + 1] || '') && UNITS[tokens[j + 2]] !== undefined && (current >= 100 || total > 0)) { j++; continue; }
        break;
      }
      var value = total + current;
      var k = used + 1;
      // "and a quarter" / "and a half" / "point two five"
      var rest = tokens.slice(k).join('');
      var frac = /^\s+and\s+(a\s+)?(quarter|half|three\s+quarters)\b/.exec(rest);
      if (frac) {
        value += frac[2] === 'half' ? 0.5 : (frac[2] === 'quarter' ? 0.25 : 0.75);
        var consumed = frac[0].length;
        while (consumed > 0 && k < tokens.length) { consumed -= tokens[k].length; k++; }
      } else {
        var pt = /^\s+point((\s+(zero|one|two|three|four|five|six|seven|eight|nine))+)/.exec(rest);
        if (pt) {
          var digits = pt[1].trim().split(/\s+/).map(function (d) { return UNITS[d]; }).join('');
          value = Number(String(value) + '.' + digits);
          var c2 = pt[0].length;
          while (c2 > 0 && k < tokens.length) { c2 -= tokens[k].length; k++; }
        }
      }
      out.push(String(value));
      i = k;
    }
    return out.join('');
  }

  /**
   * Reads loan terms out of a spoken or typed sentence, for the officer to
   * check before calculating (#139). Returns only what it heard.
   */
  function parseTerms(text) {
    var raw = String(text || '').toLowerCase();
    var s = ' ' + raw
      .replace(/[\u2013\u2014]/g, '-')
      .replace(/(\d),(?=\d{3}\b)/g, '$1')
      .replace(/,|\.(?!\d)|[;:!?]/g, ' ')
      .replace(/(\d+(?:\.\d+)?)\s*k\b/g, function (_, n) { return String(Math.round(Number(n) * 1000)); })
      .replace(/\$\s+/g, '$') + ' ';
    s = wordsToDigits(s);
    s = s.replace(/(\d+(?:\.\d+)?)\s*(thousand|million)\b/g, function (_, n, w) { return String(Number(n) * SCALES[w]); });
    var heard = {};
    function take(re, fn) {
      var m = re.exec(s);
      if (!m) return false;
      fn(m);
      s = s.slice(0, m.index) + ' '.repeat(m[0].length) + s.slice(m.index + m[0].length);
      return true;
    }
    var N = '(\\d+(?:\\.\\d+)?)';

    if (/\b(semi-?annual(ly)?|twice a year|every six months|every 6 months)\b/.test(s)) heard.frequency = 'semiannual';
    else if (/\b(quarterly|quarter|every three months|every 3 months)\b/.test(s)) heard.frequency = 'quarterly';
    else if (/\b(annual(ly)?|yearly|once a year)\b/.test(s)) heard.frequency = 'annual';
    else if (/\b(monthly|a month|per month|each month|every month)\b/.test(s)) heard.frequency = 'monthly';

    if (/\b(single payment|one payment|1 payment|lump sum|all at once|at maturity)\b/.test(raw + ' ' + s)) heard.loanType = 'single';
    else if (/\bballoon\b/.test(s)) heard.loanType = 'balloon';

    take(new RegExp(N + '[\\s-]*(years?|yrs?|months?)[\\s-]+amorti[sz]\\w*'), function (m) {
      heard.amortization = { value: +m[1], unit: unitOf(m[2]) };
    }) || take(new RegExp('amorti[sz]\\w*\\s+(?:over|of)\\s+' + N + '\\s*(years?|yrs?|months?|payments?)'), function (m) {
      heard.amortization = { value: +m[1], unit: unitOf(m[2]) };
    });

    take(new RegExp(N + '[\\s-]*(years?|yrs?|months?)[\\s-]+balloon'), function (m) {
      heard.term = { value: +m[1], unit: unitOf(m[2]) };
    });

    take(new RegExp('\\$?' + N + '\\s*(?:dollars?\\s*)?balloon'), function (m) {
      if (+m[1] >= 100) heard.balloonAmount = +m[1];
    }) || take(new RegExp('balloon(?:\\s+payment)?\\s+(?:of\\s+)?\\$?' + N), function (m) {
      if (+m[1] >= 100) heard.balloonAmount = +m[1];
    });

    take(new RegExp(N + '\\s*(?:%|percent|per cent)'), function (m) { heard.rate = +m[1]; });

    take(new RegExp('(?:payments?\\s+(?:of\\s+)?|pay(?:ing)?\\s+)\\$?' + N + '(?:\\s*(?:a|per|each|every)\\s+(month|quarter|year))?'), function (m) {
      heard.payment = +m[1];
      if (m[2]) heard.frequency = { month: 'monthly', quarter: 'quarterly', year: 'annual' }[m[2]];
    }) || take(new RegExp('\\$?' + N + '\\s*(?:dollars?\\s*)?(?:a|per|each|every)\\s+(month|quarter|year)\\b'), function (m) {
      if (+m[1] >= 20) {
        heard.payment = +m[1];
        heard.frequency = heard.frequency || { month: 'monthly', quarter: 'quarterly', year: 'annual' }[m[2]];
      }
    });

    if (!heard.term) {
      take(new RegExp('(?:due\\s+in\\s+|for\\s+|over\\s+)?' + N + '[\\s-]*(?:(?:monthly|quarterly|annual|yearly|semi-?annual)\\s+)?(years?|yrs?|months?|mos?|payments?|quarters?)\\b'), function (m) {
        heard.term = { value: +m[1], unit: unitOf(m[2]) };
      });
    }

    if (heard.rate === undefined) {
      take(new RegExp('\\bat\\s+' + N + '\\b'), function (m) { if (+m[1] < 30) heard.rate = +m[1]; });
    }

    var amounts = [];
    var re = /\$?(\d+(?:\.\d+)?)/g, mm;
    while ((mm = re.exec(s))) if (+mm[1] >= 500) amounts.push(+mm[1]);
    if (amounts.length) heard.amount = amounts[0];

    if (heard.payment !== undefined && heard.amount === undefined) heard.solveFor = 'amount';
    else if (heard.payment !== undefined && heard.amount !== undefined && heard.term === undefined) {
      heard.solveFor = heard.loanType === 'balloon' ? 'balloon' : 'term';
    }
    return heard;
  }

  function unitOf(w) {
    if (/^y/.test(w)) return 'years';
    if (/^m/.test(w)) return 'months';
    if (/^q/.test(w)) return 'quarters';
    return 'payments';
  }

  /** Converts a heard term ({value, unit}) to a number of payments at a frequency. */
  function paymentsFor(term, frequency) {
    if (!term) return null;
    var f = FREQUENCIES[frequency] || FREQUENCIES.monthly;
    if (term.unit === 'payments') return term.value;
    var months = term.unit === 'years' ? term.value * 12 : term.unit === 'quarters' ? term.value * 3 : term.value;
    return Math.round(months / f.months);
  }

  function monthsFor(term) {
    if (!term) return null;
    if (term.unit === 'years') return term.value * 12;
    if (term.unit === 'quarters') return term.value * 3;
    return term.value;
  }

  /* ------------------------------------------------------------ links */

  /** JSON in a URL fragment, so a quote link carries its terms and nothing else. */
  function pack(obj) {
    var json = JSON.stringify(obj);
    var b64 = btoa(unescape(encodeURIComponent(json)));
    return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function unpack(str) {
    var b64 = String(str || '').replace(/^#/, '').replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    return JSON.parse(decodeURIComponent(escape(atob(b64))));
  }

  return {
    VERSION: VERSION,
    STATES: STATES,
    FREQUENCIES: FREQUENCIES,
    LOAN_TYPES: LOAN_TYPES,
    SOLVE_FOR: SOLVE_FOR,
    SOLVABLE: SOLVABLE,
    CALENDARS: CALENDARS,
    calculate: calculate,
    outsideRange: outsideRange,
    parseTerms: parseTerms,
    paymentsFor: paymentsFor,
    monthsFor: monthsFor,
    aprFromFlows: aprFromFlows,
    unitSpan: function (a, b, unitMonths) {
      var e = parseDate(b);
      return unitSpan(parseDate(a), e, unitMonths, e.d);
    },
    addMonths: function (s, k) { var d = parseDate(s); return iso(addMonths(d, k, d.d)); },
    todayIso: todayIso,
    formatMoney: formatMoney,
    formatRate: formatRate,
    formatDate: formatDate,
    pack: pack,
    unpack: unpack,
  };
});
