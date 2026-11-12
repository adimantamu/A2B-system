"""TheTerms' engine (#136), checked against figures it didn't produce.

The officer's quote screen, the customer's quote page and the bank's website
calculator all load one JavaScript file, so that file is what runs here: under
Node where it is installed, or JavaScriptCore, which every Mac has. Without
either, these tests skip.

Where a reference calculator publishes a figure (docs/LOAN_CALCULATOR_RESEARCH.md,
#135), that figure is the expected value. Otherwise the expected value comes
from a schedule built below in Decimal, the way those calculators build theirs:
the payment rounded to the cent, interest rounded to the cent each period, and
the last payment clearing whatever is left.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "deploy/a2b-envs/overlay/theterms/engine.js"
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")
RUNTIME = shutil.which("node") or (str(JSC) if JSC.exists() else None)

pytestmark = pytest.mark.skipif(RUNTIME is None, reason="needs Node, or JavaScriptCore on a Mac")

CLOSING = "2026-10-05"

_DRIVER = """
var E = typeof require === 'function' ? require(%(engine)s) : (load(%(engine)s), globalThis.TheTermsEngine);
var out = (function (E) { %(body)s })(E);
(typeof print === 'function' ? print : console.log)(JSON.stringify(out));
"""


def run(body: str):
    """Runs `body` with the engine as `E` and returns what it returns."""
    with tempfile.TemporaryDirectory() as d:
        script = Path(d) / "run.js"
        script.write_text(_DRIVER % {"engine": json.dumps(str(ENGINE)), "body": body}, encoding="utf-8")
        done = subprocess.run([RUNTIME, str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def calculate(**terms):
    terms.setdefault("closingDate", CLOSING)
    return run("return E.calculate(%s);" % json.dumps(terms))


# ------------------------------------------------------------ the reference


def _cents(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def level_payment(principal: int, rate: str, per_year: int, count: int) -> int:
    """The payment, in cents, that pays off `principal` cents in `count` equal periods."""
    r = Decimal(rate) / 100 / per_year
    if not r:
        return _cents(Decimal(principal) / count)
    return _cents(principal * r / (1 - (1 + r) ** -count))


def schedule(principal: int, rate: str, per_year: int, payment: int, count: int | None = None):
    """Payments and total interest, in cents.

    The last payment clears the loan: the `count`th, or with no count, the
    first that covers what is owed.
    """
    r = Decimal(rate) / 100 / per_year
    balance, paid, interest_total = principal, [], 0
    while True:
        interest = _cents(balance * r)
        last = len(paid) + 1 == count if count else balance + interest <= payment
        pay = balance + interest if last else payment
        balance += interest - pay
        paid.append(pay)
        interest_total += interest
        if last:
            return paid, interest_total


def apr(amount_financed: int, payments: list[int], per_year: int) -> float:
    """Regulation Z's actuarial APR for payments a regular period apart, the
    first a period after closing: the rate at which they are worth the amount
    financed."""
    lo, hi = 0.0, 1.0
    for _ in range(100):
        i = (lo + hi) / 2
        worth = sum(p / (1 + i) ** k for k, p in enumerate(payments, 1))
        lo, hi = (i, hi) if worth > amount_financed else (lo, i)
    return i * per_year * 100


# ------------------------------------------------------------ published figures


def test_dinkytowns_default_loan_to_the_cent():
    """$15,000 at 13.24% for 60 months, as both banks' Dinkytown pages open:
    59 payments of $343.14, a last one of $343.34, and $5,588.60 of interest.
    #136 asks for a match to the cent, and the last payment is where a
    different rounding shows."""
    r = calculate(amount=15000, rate=13.24, payments=60)
    assert r["ok"]
    assert [(g["count"], g["amount"]) for g in r["groups"]] == [(59, 34314), (1, 34334)]
    assert r["totals"]["totalInterest"] == 558860


@pytest.mark.parametrize("style, payment", [("last", 116909), ("extra", 115190)])
def test_doaks_balloon_reads_both_ways(style, payment):
    """$100,000 at 5%, 60 monthly payments and a $50,000 balloon (#134). As the
    60th payment, the balloon leaves 59 of $1,169.09; on top of the 60th, all
    60 are $1,151.90. The officer says which, and the last payment clears what
    is left either way."""
    r = calculate(loanType="balloon", balloonMethod="amount", balloonAmount=50000,
                  balloonStyle=style, amount=100000, rate=5, payments=60)
    assert r["ok"] and r["payment"] == payment
    paid, interest = schedule(10_000_000, "5", 12, payment, 60)
    assert [row["payment"] for row in r["schedule"]] == paid
    assert r["totals"]["totalInterest"] == interest
    assert r["balloon"] == (paid[-1] if style == "last" else paid[-1] - payment)


def test_doaks_loan_without_the_balloon():
    """The same loan paid off in full: $1,887.12 a month."""
    r = calculate(amount=100000, rate=5, payments=60)
    assert r["payment"] == 188712 == level_payment(10_000_000, "5", 12, 60)


def test_a_single_payment_loan_has_the_apr_carleton_shows():
    """Carleton's example: $10,000 repaid with one $10,799.80 payment after 12
    months has an APR of 7.998%. Modeled as eleven $0 payments and a balloon,
    the same loan comes out at 7.719%, outside Regulation Z's 1/8-point
    tolerance, which is why a single payment is its own loan type."""
    r = calculate(loanType="single", amount=10000, rate=7.998, termMonths=12)
    assert r["ok"] and r["count"] == 1 and r["payment"] == 1_079_980
    assert round(r["apr"], 3) == 7.998
    as_a_balloon = run(
        "var f = []; for (var i = 1; i <= 12; i++) f.push({ amount: i === 12 ? 10799.80 : 0, t: i, f: 0 });"
        "return E.aprFromFlows(10000, f, 12);"
    )
    assert round(as_a_balloon, 3) == 7.719


# ------------------------------------------------------------ against the reference


def test_a_quarterly_balloon_with_fees_and_insurance():
    """What a business banker quotes most (#136): $250,000 at 7.25%, quarterly,
    amortized over 40 quarters with the balloon at the 20th. A 0.5% loan fee
    is financed and is a finance charge; a $45 filing fee is paid in cash and
    isn't one; a $1,850 optional insurance premium is financed with the loan."""
    r = calculate(
        loanClass="business", loanType="balloon", balloonMethod="amortization", frequency="quarterly",
        amount=250000, rate=7.25, payments=20, amortization=40, premium=1850,
        fees=[
            {"name": "Loan fee", "kind": "percent", "value": 0.5, "financed": True, "financeCharge": True},
            {"name": "Filing fee", "kind": "flat", "value": 45, "financed": False, "financeCharge": False},
        ],
    )
    assert r["ok"]
    note = 25_000_000 + 125_000 + 185_000
    amount_financed = note - 125_000
    payment = level_payment(note, "7.25", 4, 40)
    paid, interest = schedule(note, "7.25", 4, payment, 20)

    assert [f["cents"] for f in r["fees"]] == [125_000, 4_500]
    assert r["principal"] == note
    assert r["payment"] == payment
    assert r["balloon"] == r["finalPayment"] == paid[-1]
    totals = r["totals"]
    assert totals["amountFinanced"] == amount_financed
    assert totals["totalInterest"] == interest
    assert totals["totalOfPayments"] == sum(paid)
    assert totals["financeCharge"] == sum(paid) - amount_financed
    assert abs(r["apr"] - apr(amount_financed, paid, 4)) < 0.0001


def test_solving_for_the_amount_rounds_the_exact_amount_down():
    """"How much can I borrow at $500 a month for five years?" $500 a month
    pays off $25,560.4583 exactly, and the engine rounds that down, so no
    payment is more than $500. Rounding to the nearest cent ($25,560.46) also
    keeps every payment at $500; which the bank's documents show is for #136
    to settle against Carleton."""
    r = calculate(solveFor="amount", payment=500, rate=6.49, payments=60)
    rate = Decimal("6.49") / 100 / 12
    exact = 50_000 * (1 - (1 + rate) ** -60) / rate
    assert r["ok"] and r["amount"] == r["solved"]["value"] == int(exact)
    paid, _ = schedule(r["amount"], "6.49", 12, 50_000, 60)
    assert [row["payment"] for row in r["schedule"]] == paid
    assert max(paid) <= 50_000


def test_solving_for_the_number_of_payments_stops_at_the_one_that_clears_it():
    r = calculate(solveFor="term", amount=20000, payment=400, rate=7)
    paid, _ = schedule(2_000_000, "7", 12, 40_000)
    assert r["ok"]
    assert r["count"] == r["solved"]["value"] == len(paid)
    assert r["finalPayment"] == paid[-1]


def test_solving_for_the_balloon_leaves_what_the_payments_dont_cover():
    r = calculate(loanType="balloon", solveFor="balloon", amount=100000, payment=1000, rate=5, payments=60)
    paid, _ = schedule(10_000_000, "5", 12, 100_000, 60)
    assert r["ok"]
    assert r["balloon"] == r["solved"]["value"] == paid[-1]


def test_an_odd_first_period_puts_its_interest_on_the_first_payment():
    """Closing on Oct 5 with the first payment on Nov 20 is a month and 15
    days. The 15 days' interest, $25.00 on $10,000 at 6% on 30/360, goes on
    the first payment, and every payment after it is the regular schedule's."""
    r = calculate(amount=10000, rate=6, payments=12, firstPaymentDate="2026-11-20")
    regular = level_payment(1_000_000, "6", 12, 12)
    paid, _ = schedule(1_000_000, "6", 12, regular, 12)
    assert r["ok"] and r["oddDays"] == 15
    assert r["schedule"][0]["payment"] == regular + 2_500
    assert [row["payment"] for row in r["schedule"][1:]] == paid[1:]


def test_a_zero_rate_loan_is_its_principal_in_equal_parts():
    r = calculate(amount=1200, rate=0, payments=12)
    assert r["ok"] and [row["payment"] for row in r["schedule"]] == [10_000] * 12
    assert r["totals"]["financeCharge"] == 0 and abs(r["apr"]) < 1e-9


# ------------------------------------------------------------ refusals


def test_the_engine_never_supplies_a_rate_of_its_own():
    """Lenni never quotes a rate (#134). Every rate the engine uses is an input,
    on every surface the bank's own, so a missing one is asked for, not
    filled in."""
    r = calculate(amount=10000, payments=12)
    assert r == {"ok": False, "errors": [{"field": "rate", "message": "Enter the interest rate."}]}


@pytest.mark.parametrize("terms, field", [
    ({"state": "OK", "amount": 10000, "rate": 6, "payments": 12}, "state"),
    ({"loanType": "single", "solveFor": "term", "amount": 10000, "rate": 6, "termMonths": 12}, "solveFor"),
    ({"loanType": "balloon", "amount": 10000, "rate": 6, "payments": 60, "amortization": 60}, "amortization"),
    ({"amount": 10000, "rate": 6, "payments": 12, "firstPaymentDate": "2026-10-01"}, "firstPaymentDate"),
], ids=["outside-texas", "single-payment-term", "balloon-without-a-balloon", "first-payment-before-closing"])
def test_terms_it_cant_quote_are_refused_with_the_reason(terms, field):
    r = calculate(**terms)
    assert not r["ok"]
    assert field in [e["field"] for e in r["errors"]]


def test_a_request_outside_the_banks_ranges_is_refused_not_adjusted():
    """The bank sets each product's ranges (#137). A request outside them gets
    the reason in the bank's name, and the quote isn't moved inside them."""
    product = {
        "name": "Consumer Auto", "amountMin": 5000, "amountMax": 75000, "paymentsMin": 12, "paymentsMax": 84,
        "rateMin": 5.49, "rateMax": 11.99, "types": ["installment"], "frequencies": ["monthly"],
    }
    terms = {"amount": 80000, "rate": 4.99, "payments": 60, "closingDate": CLOSING}
    out = run(
        "var q = %s; var r = E.calculate(q);"
        "return { amount: r.amount, rate: r.input.rate, reasons: E.outsideRange(%s, r.input, r, 'Bluebonnet Community Bank') };"
        % (json.dumps(terms), json.dumps(product))
    )
    assert out["amount"] == 8_000_000 and out["rate"] == 4.99
    assert out["reasons"] == [
        "Bluebonnet Community Bank lends $5,000 to $75,000 on Consumer Auto. $80,000 is more than that.",
        "Bluebonnet Community Bank\u2019s rate on Consumer Auto is 5.49% to 11.99%. 4.99% is below it.",
    ]


# ------------------------------------------------------------ "say it"


@pytest.mark.parametrize("said, heard", [
    ("100,000 at 5% for 60 months with a 50,000 balloon",
     {"loanType": "balloon", "balloonAmount": 50000, "rate": 5, "term": {"value": 60, "unit": "months"}, "amount": 100000}),
    ("fifty thousand at seven and a quarter percent, five years, quarterly, twenty thousand dollar balloon",
     {"frequency": "quarterly", "loanType": "balloon", "balloonAmount": 20000, "rate": 7.25,
      "term": {"value": 5, "unit": "years"}, "amount": 50000}),
    ("how much can I borrow at $500 a month for 5 years",
     {"frequency": "monthly", "payment": 500, "term": {"value": 5, "unit": "years"}, "solveFor": "amount"}),
    ("250k equipment loan, 10 year amortization, 5 year balloon, quarterly, 7.25%",
     {"frequency": "quarterly", "loanType": "balloon", "amortization": {"value": 10, "unit": "years"},
      "term": {"value": 5, "unit": "years"}, "rate": 7.25, "amount": 250000}),
    ("25000 single payment due in 12 months at 8 percent",
     {"loanType": "single", "rate": 8, "term": {"value": 12, "unit": "months"}, "amount": 25000}),
    ("two hundred fifty thousand at seven point two five percent over 60 monthly payments",
     {"frequency": "monthly", "rate": 7.25, "term": {"value": 60, "unit": "payments"}, "amount": 250000}),
    ("10,000 at 6%", {"rate": 6, "amount": 10000}),
    ("a loan for my truck", {}),
])
def test_spoken_terms_are_read_back_as_said(said, heard):
    """The officer says the terms (#139) and checks what was heard before
    anything is calculated, so the parser returns what the sentence holds and
    nothing it doesn't."""
    assert run("return E.parseTerms(%s);" % json.dumps(said)) == heard
