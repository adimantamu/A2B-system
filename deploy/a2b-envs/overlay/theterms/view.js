/**
 * How a TheTerms quote is shown. Shared by the officer's screen, the customer's
 * quote page and the website calculator, so the same loan reads the same way on
 * all three: the payment and its repayment terms, the APR beside them, the
 * Truth in Lending box and the amortization table.
 */
(function (root) {
  'use strict';
  var E = root.TheTermsEngine;

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  var money = E.formatMoney;
  function apr(r) { return r === null || r === undefined ? '\u2014' : E.formatRate(r, 3); }

  var PER = { monthly: 'a month', quarterly: 'a quarter', semiannual: 'every six months', annual: 'a year' };

  function hexOk(h) { return /^#[0-9a-f]{6}$/i.test(String(h || '')); }

  /** Black or white, whichever reads better on `hex`. */
  function inkOn(hex) {
    if (!hexOk(hex)) return '#fff';
    var n = parseInt(hex.slice(1), 16);
    var r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    var lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
    return lum > 0.6 ? '#111827' : '#ffffff';
  }

  /** The bank's colors as CSS variables, falling back to Lenni's. */
  function brandVars(brand) {
    var b = brand || {};
    var p = hexOk(b.primary) ? b.primary : '#203048';
    var a = hexOk(b.accent) ? b.accent : '#209D76';
    return '--tt-primary:' + p + ';--tt-on-primary:' + inkOn(p) + ';--tt-accent:' + a + ';--tt-on-accent:' + inkOn(a) + ';';
  }

  /** "$1,169.09" "a month" "59 payments, then $49,999.70 on Oct 5, 2031". */
  function summary(r) {
    var q = r.input;
    if (q.loanType === 'single') {
      return { amount: money(r.payment), per: 'once', line: 'One payment on ' + E.formatDate(r.maturityDate) };
    }
    var g = r.groups;
    var per = PER[q.frequency];
    var lines = [];
    if (g.length === 1) {
      lines.push(g[0].count + ' ' + E.FREQUENCIES[q.frequency].adverb + ' payments from ' + E.formatDate(g[0].first));
    } else {
      var first = g[0], rest = g.slice(1);
      lines.push((first.count === 1 ? 'First payment ' + money(first.amount) + ' on ' + E.formatDate(first.first)
        : first.count + ' payments of ' + money(first.amount) + ' from ' + E.formatDate(first.first)));
      rest.forEach(function (x, idx) {
        var lastOne = idx === rest.length - 1;
        if (lastOne && x.count === 1) {
          lines.push((q.loanType === 'balloon' ? 'then a balloon of ' : 'then ') + money(x.amount) + ' on ' + E.formatDate(x.first));
        } else {
          lines.push('then ' + x.count + ' of ' + money(x.amount));
        }
      });
    }
    // The headline is the payment most of the schedule pays.
    var main = g.reduce(function (a, b) { return b.count > a.count ? b : a; }, g[0]);
    return { amount: money(main.amount), per: per, line: lines.join(', ') };
  }

  function paymentRows(r) {
    var freq = E.FREQUENCIES[r.input.frequency];
    return r.groups.map(function (g) {
      var when = g.count === 1 ? E.formatDate(g.first)
        : freq.label + ', ' + E.formatDate(g.first) + ' to ' + E.formatDate(g.last);
      return { count: g.count, amount: money(g.amount), when: when };
    });
  }

  /**
   * The Truth in Lending box, with the itemization under it. `o` carries the
   * text the bank set: the coverage option's name and notice, its disclosure.
   */
  function tila(r, o) {
    o = o || {};
    var t = r.totals;
    var fees = r.fees || [];
    var financedOther = fees.filter(function (f) { return f.financed && !f.financeCharge; });
    var prepaid = fees.filter(function (f) { return f.financeCharge; });
    var atClosing = fees.filter(function (f) { return !f.financed; });
    var h = '';
    h += '<div class="tt-tila' + (r.regZ ? '' : ' is-business') + '">';
    if (!r.regZ) {
      h += '<p class="tt-tila-note">Business-purpose loan. Truth in Lending doesn\u2019t apply, so these figures are for comparison only.</p>';
    }
    h += '<div class="tt-tila-grid">';
    h += box('Annual percentage rate', 'The cost of the credit as a yearly rate.', apr(r.apr), true);
    h += box('Finance charge', 'The dollar amount the credit will cost.', money(t.financeCharge), true);
    h += box('Amount financed', 'The amount of credit provided to the borrower or on their behalf.', money(t.amountFinanced));
    h += box('Total of payments', 'What the borrower will have paid after making every payment as scheduled.', money(t.totalOfPayments));
    h += '</div>';
    h += '<table class="tt-paysched"><thead><tr><th>Number of payments</th><th>Amount of payments</th><th>When payments are due</th></tr></thead><tbody>';
    paymentRows(r).forEach(function (p) {
      h += '<tr><td>' + p.count + '</td><td>' + esc(p.amount) + '</td><td>' + esc(p.when) + '</td></tr>';
    });
    h += '</tbody></table>';
    h += '<dl class="tt-items">';
    h += item('Amount given to the borrower', money(r.amount));
    financedOther.forEach(function (f) { h += item(f.name + ', paid to others', money(f.cents)); });
    if (r.premium) h += item((o.optionName || 'Insurance') + ' premium' + (r.input.premiumRequired ? ' (required)' : ''), money(r.premium));
    h += item('Note amount', money(r.principal), true);
    prepaid.forEach(function (f) {
      h += item(f.name + ', prepaid finance charge' + (f.financed ? '' : ', paid at closing'), money(f.cents));
    });
    atClosing.filter(function (f) { return !f.financeCharge; }).forEach(function (f) {
      h += item(f.name + ', paid at closing', money(f.cents));
    });
    if (r.oddDays > 0 && r.firstPayment !== r.payment) {
      h += item('First payment includes interest for ' + r.oddDays + ' extra day' + (r.oddDays === 1 ? '' : 's'), money(r.firstPayment - r.payment));
    }
    h += '</dl>';
    if (r.premium && o.insuranceNotice) h += '<p class="tt-notice">' + esc(o.insuranceNotice) + '</p>';
    if (o.optionText) h += '<p class="tt-notice">' + esc(o.optionText) + '</p>';
    h += '</div>';
    return h;
  }

  function box(label, help, value, strong) {
    return '<div class="tt-tila-box' + (strong ? ' strong' : '') + '"><div class="tt-tila-label">' + esc(label) +
      '</div><div class="tt-tila-help">' + esc(help) + '</div><div class="tt-tila-value">' + esc(value) + '</div></div>';
  }

  function item(label, value, strong) {
    return '<div class="tt-item' + (strong ? ' strong' : '') + '"><dt>' + esc(label) + '</dt><dd>' + esc(value) + '</dd></div>';
  }

  /** The amortization table. `limit` rows from the top, then the last, unless `all`. */
  function schedule(r, opts) {
    opts = opts || {};
    var rows = r.schedule;
    var limit = opts.all ? rows.length : (opts.limit || 12);
    var shown = rows.length > limit + 1 ? rows.slice(0, limit) : rows;
    var gap = rows.length > shown.length;
    var h = '<div class="tt-amort-wrap"><table class="tt-amort"><thead><tr><th>#</th><th>Date</th><th>Payment</th><th>Interest</th><th>Principal</th><th>Balance</th></tr></thead><tbody>';
    function row(x) {
      return '<tr><td>' + x.n + '</td><td>' + esc(E.formatDate(x.date)) + '</td><td>' + money(x.payment) + '</td><td>' +
        money(x.interest) + '</td><td>' + money(x.principal) + '</td><td>' + money(x.balance) + '</td></tr>';
    }
    shown.forEach(function (x) { h += row(x); });
    if (gap) {
      h += '<tr class="tt-gap"><td colspan="6">' + (rows.length - shown.length - 1) + ' more payments</td></tr>';
      h += row(rows[rows.length - 1]);
    }
    h += '</tbody><tfoot><tr><td colspan="2">Total</td><td>' + money(r.totals.totalOfPayments) + '</td><td>' +
      money(r.totals.totalInterest) + '</td><td>' + money(r.principal) + '</td><td></td></tr></tfoot></table></div>';
    return h;
  }

  root.TheTermsView = {
    esc: esc,
    money: money,
    apr: apr,
    PER: PER,
    hexOk: hexOk,
    inkOn: inkOn,
    brandVars: brandVars,
    summary: summary,
    paymentRows: paymentRows,
    tila: tila,
    schedule: schedule,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
