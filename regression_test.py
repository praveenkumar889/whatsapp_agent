#!/usr/bin/env python3
"""
regression_test.py — End-to-end regression suite for the WhatsApp AI bot.

WHY THIS EXISTS:
    Every fix this project has made so far was validated by hand, one
    Streamlit/curl message at a time. That's how the two file-lineage
    regressions slipped through undetected for as long as they did — there
    was no repeatable way to re-check "does the whole conversation still
    work?" after a change. This script is that repeatable check.

WHAT IT COVERS (one scenario function per flow):
    1.  Greeting
    2.  Category browsing → product listing
    3.  Product comparison
    4.  Product selection by number (list-position, not quantity)
    5.  Product follow-up (brief, FAQ — "is it waterproof")
    6.  Store offers inquiry
    7.  Ordering with quantity
    8.  Quantity update (SET vs ADD — the exact bug fixed in migration 017)
    9.  Negotiation (counter-offer, acceptance)
    10. Order confirmation → invoice generation
    11. Fresh conversation isolation (two different phone numbers never leak
        state into each other)
    12. Memory recall ("recommend something for me" — exercises MemoryPolicy)
    13. Mid-flow interruption (switch topic during negotiation, confirm the
        bot doesn't crash or silently merge unrelated state)
    14. Human escalation

HOW TO RUN:
    1. Start your server:      uvicorn main:app --reload --port 8000
    2. Make sure the tenant's phone_number_id/tenant_id are configured
       (or set DEFAULT_PHONE_NUMBER_ID / DEFAULT_TENANT_ID in .env).
    3. Run:                    python regression_test.py
    4. Optional flags:
         --base-url http://localhost:8000   (default)
         --phone 918897726611               (default — change to avoid
                                              colliding with real test data)
         --tenant-id tenant_inventaa_led_001 (only needed for /reset)
         --verbose                          (print full replies, not just pass/fail)

WHAT THIS DOES NOT DO:
    - It does not run itself as part of CI (no CI is configured for this
      project) — it's meant to be run manually after any change, or wired
      into CI later once one exists.
    - It does not validate exact reply wording — replies are DB-configurable
      prompts now (by design), so wording can legitimately change per
      tenant. Assertions check structural signals instead: HTTP status,
      debug.intent, debug.route, absence of error markers, presence of
      expected data (e.g. an invoice number pattern, a Rs. amount).
    - It cannot substitute for testing against your actual Supabase/Azure
      OpenAI credentials — it calls your real running server over HTTP,
      the same way the Streamlit test UI does.
"""

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_TENANT_ID = "tenant_inventaa_led_001"  # used as reset() fallback so a forgotten
                                                 # --tenant-id can't silently no-op every reset

try:
    import requests
except ImportError:
    print("This script needs the 'requests' package: pip install requests")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# Test harness
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Session:
    """One conversation — wraps /chat calls for a single phone number."""
    base_url: str
    phone: str
    phone_number_id: Optional[str] = None
    verbose: bool = False
    last_response: dict = field(default_factory=dict)

    @staticmethod
    def _normalize_reply(rep) -> dict:
        """Ensure each reply item is a dict with 'type' and 'body' keys.
        The /chat endpoint sometimes returns plain strings instead of objects."""
        if isinstance(rep, str):
            return {"type": "text", "body": rep}
        return rep  # already a dict

    def send(self, message: str, sender_name: str = "Test User") -> dict:
        payload = {"phone": self.phone, "message": message, "sender_name": sender_name}
        if self.phone_number_id:
            payload["phone_number_id"] = self.phone_number_id
        r = requests.post(f"{self.base_url}/chat", json=payload, timeout=60)
        try:
            data = r.json()
        except Exception:
            data = {"_raw_text": r.text, "_status_code": r.status_code}
        data["_status_code"] = r.status_code
        self.last_response = data
        if self.verbose or r.status_code != 200:
            print(f"    >> {message}")
            if r.status_code != 200:
                print(f"    !! HTTP {r.status_code}: {data.get('error') or data.get('_raw_text') or data}")
            raw_replies = data.get("replies", [])
            replies = [self._normalize_reply(rep) for rep in (raw_replies if isinstance(raw_replies, list) else [])]
            for rep in replies:
                if rep.get("type") == "text":
                    print(f"    << {rep.get('body', '')[:200]}")
                else:
                    print(f"    << [{rep.get('type')}] {rep.get('body', '')[:120]}")
            print(f"    debug: {data.get('debug', {})}")
        return data

    def reply_text(self) -> str:
        """Concatenates all text-type replies from the last response."""
        raw_replies = self.last_response.get("replies", [])
        replies = [self._normalize_reply(r) for r in (raw_replies if isinstance(raw_replies, list) else [])]
        return "\n".join(r.get("body", "") for r in replies if r.get("type") == "text")

    def debug(self) -> dict:
        return self.last_response.get("debug", {})


class Suite:
    def __init__(self, base_url: str, phone: str, tenant_id: Optional[str], verbose: bool,
                 phone_number_id: Optional[str] = None):
        self.base_url = base_url
        self.phone = phone
        self.tenant_id = tenant_id
        self.verbose = verbose
        self.phone_number_id = phone_number_id
        self.results: list = []

    def reset(self, phone: Optional[str] = None):
        p = phone or self.phone
        payload = {"phone": p, "tenant_id": self.tenant_id or DEFAULT_TENANT_ID}
        try:
            r = requests.post(f"{self.base_url}/reset", json=payload, timeout=15)
            if r.status_code != 200:
                print(f"  [WARN] /reset returned HTTP {r.status_code} for {p} — state was NOT cleared. "
                      f"Response: {r.text[:200]}")
                print(f"  [WARN] Every check from here on may be contaminated by leftover state from a prior run.")
        except Exception as e:
            print(f"  [WARN] /reset failed (continuing anyway): {e}")

    def check(self, name: str, condition: bool, detail: str = ""):
        self.results.append(TestResult(name, condition, detail))
        icon = "PASS" if condition else "FAIL"
        print(f"  [{icon}] {name}" + (f" -- {detail}" if (not condition and detail) else ""))

    def new_session(self, phone: Optional[str] = None) -> Session:
        return Session(self.base_url, phone or self.phone, self.phone_number_id, self.verbose)

    def summary(self) -> bool:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        print("\n" + "=" * 70)
        print(f"RESULTS: {passed}/{total} checks passed")
        if passed < total:
            print("\nFailed checks:")
            for r in self.results:
                if not r.passed:
                    print(f"  [FAIL] {r.name}" + (f" -- {r.detail}" if r.detail else ""))
        print("=" * 70)
        return passed == total


# ══════════════════════════════════════════════════════════════════════════════
# Shared assertion helpers
# ══════════════════════════════════════════════════════════════════════════════

ERROR_MARKERS = ["traceback", "pipeline error", "runtimeerror", "keyerror",
                 "attributeerror", "nonetype", "[none]"]

def looks_clean(text: str) -> bool:
    """No leaked stack traces, no literal error markers, no bare 'None' string."""
    low = text.lower()
    if any(m in low for m in ERROR_MARKERS):
        return False
    # A bare "None" surrounded by word boundaries usually means an unfilled
    # variable leaked into a customer-facing reply (e.g. the workflow_context
    # bug fixed in migration 020).
    if re.search(r"\bnone\b", text, re.IGNORECASE):
        return False
    return True


def has_amount(text: str) -> bool:
    """Rough check for a currency amount appearing in the reply."""
    return bool(re.search(r"(rs\.?\s?[\d,]+|\$\s?[\d,]+|[\d,]+\.\d{2})", text, re.IGNORECASE))


# ══════════════════════════════════════════════════════════════════════════════
# Scenarios
# ══════════════════════════════════════════════════════════════════════════════

def scenario_greeting(suite: Suite):
    print("\n[1] Greeting")
    suite.reset()
    s = suite.new_session()
    d = s.send("hi")
    status = d.get("_status_code")
    if status != 200:
        print(f"\n  STOPPING EARLY: first call returned HTTP {status}, not 200.")
        print(f"  Response: {d.get('error') or d.get('_raw_text') or d}")
        print("  This usually means phone_number_id/tenant_id couldn't be resolved.")
        print("  Fix: pass --phone-number-id, or set DEFAULT_PHONE_NUMBER_ID in your .env,")
        print("       then re-run. The remaining 13 scenarios will all fail the same way")
        print("       until this is fixed, so there's no point continuing.")
        suite.check("greeting: HTTP 200", False, detail=f"got {status}")
        return False
    suite.check("greeting: HTTP 200", True)
    suite.check("greeting: intent=GREETING", d.get("debug", {}).get("intent") == "GREETING",
                detail=f"got {d.get('debug', {}).get('intent')}")
    suite.check("greeting: reply looks clean", looks_clean(s.reply_text()))
    suite.check("greeting: non-empty reply", len(s.reply_text().strip()) > 0)
    return True


def scenario_browse_and_list(suite: Suite) -> Session:
    print("\n[2] Category browsing -> product listing")
    s = suite.new_session()
    d = s.send("help me to order garden lights")
    suite.check("browse: category clarification returned", len(s.reply_text().strip()) > 0)
    suite.check("browse: reply looks clean", looks_clean(s.reply_text()))

    # Reply with a category name seen in the clarification (adjust if your
    # catalog differs -- this assumes an Inventaa-style LED catalog; swap the
    # category name for your own tenant's data).
    d2 = s.send("Outdoor Garden Bollard Light")
    suite.check("browse: product list returned", "1." in s.reply_text() or "*1.*" in s.reply_text(),
                detail="expected a numbered product list")
    suite.check("browse: reply looks clean (2)", looks_clean(s.reply_text()))
    return s


def scenario_comparison(suite: Suite, s: Session):
    print("\n[3] Product comparison")
    d = s.send("compare 1,2")
    suite.check("compare: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("compare: reply looks clean", looks_clean(s.reply_text()))
    # A comparison reply should NOT ask "how many units" -- that would mean
    # the comparison intent leaked into the quantity-ask branch.
    suite.check("compare: does not ask for quantity",
                "how many units" not in s.reply_text().lower())


def scenario_select_and_followup(suite: Suite, s: Session):
    print("\n[4] Product selection by number (list-position, not quantity)")
    d = s.send("i want to order 1")
    suite.check("select: reply asks for quantity (not a quantity itself)",
                "how many" in s.reply_text().lower() or "units" in s.reply_text().lower())
    suite.check("select: reply looks clean", looks_clean(s.reply_text()))

    print("\n[5] Product follow-up (FAQ)")
    d2 = s.send("is it waterproof")
    suite.check("followup: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("followup: reply looks clean", looks_clean(s.reply_text()))
    # REGRESSION CHECK for the exact bug fixed in migration 020: a bare
    # product-selection number from earlier in the conversation must NOT
    # resurface as if it were an order quantity.
    suite.check("followup: no stray '1 units' / '1 unit' quantity leak",
                not re.search(r"\b1\s*units?\b", s.reply_text(), re.IGNORECASE),
                detail="the product-number-vs-quantity bug (migration 020) may have regressed")


def scenario_offers(suite: Suite, s: Session):
    print("\n[6] Store offers inquiry")
    d = s.send("is there any offers?")
    suite.check("offers: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("offers: reply looks clean", looks_clean(s.reply_text()))


def scenario_order_and_qty_update(suite: Suite, s: Session):
    print("\n[7] Ordering with quantity")
    d = s.send("i want to order 2 units")
    suite.check("order: reply mentions 2 units", "2" in s.reply_text())
    suite.check("order: reply has an amount", has_amount(s.reply_text()))
    suite.check("order: reply looks clean", looks_clean(s.reply_text()))

    print("\n[8] Quantity update -- ADD semantics (migration 017 regression check)")
    d2 = s.send("add 3 more units")
    reply = s.reply_text()
    suite.check("qty update: reply mentions 5 (2+3, not just 3)",
                "5" in reply, detail=f"reply was: {reply[:200]}")
    suite.check("qty update: reply does NOT show 3 as the new total",
                not re.search(r"from\s*\*?2\*?\s*to\s*\*?3\*?\s*units", reply, re.IGNORECASE),
                detail="this is the exact 'add N more units' bug from earlier this project -- "
                       "if it reappears, check ai/negotiator.py's SET/ADD/REMOVE handling")
    suite.check("qty update: reply looks clean", looks_clean(reply))


def scenario_negotiation(suite: Suite, s: Session):
    print("\n[9] Negotiation")
    d = s.send("can i get it for a lower price")
    suite.check("negotiate: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("negotiate: reply has an amount", has_amount(s.reply_text()))
    suite.check("negotiate: reply looks clean", looks_clean(s.reply_text()))

    d2 = s.send("yes confirm this")
    suite.check("negotiate accept: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("negotiate accept: reply looks clean", looks_clean(s.reply_text()))


def scenario_confirmation_and_invoice(suite: Suite, s: Session):
    print("\n[10] Order confirmation -> invoice")
    d = s.send("yes place this")
    suite.check("confirm: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("confirm: reply looks clean", looks_clean(s.reply_text()))

    d2 = s.send("yes share me the invoice")
    reply = s.reply_text()
    suite.check("invoice: reply mentions an invoice/order id pattern",
                bool(re.search(r"INV[#\-]?\w+", reply, re.IGNORECASE)),
                detail=f"reply was: {reply[:200]}")
    suite.check("invoice: reply looks clean", looks_clean(reply))


def scenario_fresh_conversation_isolation(suite: Suite):
    print("\n[11] Fresh conversation isolation (two phones never leak state)")
    phone_a = suite.phone
    phone_b = f"{suite.phone}9"  # a different number
    suite.reset(phone_a)
    suite.reset(phone_b)

    sa = suite.new_session(phone_a)
    sa.send("hi")
    sa.send("i want to order 2 units")  # A has a product in context, B does not

    sb = suite.new_session(phone_b)
    d = sb.send("add 3 more units")  # B never ordered anything -- should NOT silently succeed
    reply_b = sb.reply_text()
    suite.check("isolation: session B doesn't inherit session A's order context",
                "5" not in reply_b,
                detail=f"session B replied: {reply_b[:200]} -- if it references quantity 5, "
                       "state leaked from session A")
    suite.check("isolation: session B reply looks clean", looks_clean(reply_b))


def scenario_memory_recall(suite: Suite):
    print("\n[12] Memory recall (exercises MemoryPolicy gating)")
    suite.reset()
    s = suite.new_session()
    s.send("hi")
    d = s.send("recommend something for me based on what I usually buy")
    suite.check("memory: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("memory: reply looks clean", looks_clean(s.reply_text()))
    # Not asserting on MEM0 SEARCH firing here since that requires reading
    # server logs, which this HTTP-only script can't see -- this just
    # confirms the request doesn't crash or hang.


def scenario_interruption(suite: Suite):
    print("\n[13] Mid-flow interruption (switch topic during negotiation)")
    suite.reset()
    s = suite.new_session()
    s.send("hi")
    s.send("help me to order garden lights")
    s.send("Outdoor Garden Bollard Light")
    s.send("i want to order 1")
    s.send("i want to order 2 units")
    s.send("can i get it for a lower price")  # now mid-negotiation

    # Interrupt with an unrelated question
    d = s.send("what is your business address")
    suite.check("interrupt: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("interrupt: reply looks clean", looks_clean(s.reply_text()))
    suite.check("interrupt: doesn't crash the pipeline",
                d.get("debug", {}).get("intent") != "ERROR",
                detail=f"debug was: {d.get('debug', {})}")


def scenario_escalation(suite: Suite):
    print("\n[14] Human escalation")
    suite.reset()
    s = suite.new_session()
    d = s.send("I want to speak to a real human agent, this is urgent")
    suite.check("escalation: reply non-empty", len(s.reply_text().strip()) > 0)
    suite.check("escalation: reply looks clean", looks_clean(s.reply_text()))
    suite.check("escalation: intent=HUMAN_ESCALATION",
                d.get("debug", {}).get("intent") == "HUMAN_ESCALATION",
                detail=f"got {d.get('debug', {}).get('intent')}")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Regression test suite for the WhatsApp AI bot")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--phone", default="918897726611")
    parser.add_argument("--phone-number-id", default=None,
                         help="Required unless DEFAULT_PHONE_NUMBER_ID is set in your .env — "
                              "the /chat endpoint refuses to guess a tenant's phone_number_id "
                              "(a deliberate safety fix; see main.py::chat_endpoint).")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print(f"Regression suite against {args.base_url} (phone={args.phone})")
    if not args.phone_number_id:
        print("NOTE: --phone-number-id not given. This will only work if DEFAULT_PHONE_NUMBER_ID")
        print("      is set in your .env — otherwise every /chat call returns HTTP 400 and every")
        print("      check below will fail (empty replies, intent=None). If you see that, re-run")
        print("      with: python regression_test.py --phone-number-id <your_phone_number_id>")
    print("=" * 70)

    suite = Suite(args.base_url, args.phone, args.tenant_id, args.verbose, args.phone_number_id)

    try:
        ok = scenario_greeting(suite)
        if not ok:
            all_passed = suite.summary()
            sys.exit(1)
        s = scenario_browse_and_list(suite)
        scenario_comparison(suite, s)
        scenario_select_and_followup(suite, s)
        scenario_offers(suite, s)
        scenario_order_and_qty_update(suite, s)
        scenario_negotiation(suite, s)
        scenario_confirmation_and_invoice(suite, s)
        scenario_fresh_conversation_isolation(suite)
        scenario_memory_recall(suite)
        scenario_interruption(suite)
        scenario_escalation(suite)
    except requests.exceptions.ConnectionError:
        print("\nERROR: Could not connect to the server. Is it running?")
        print(f"   Expected at: {args.base_url}")
        print("   Start it with: uvicorn main:app --reload --port 8000")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: Suite crashed unexpectedly: {type(e).__name__}: {e}")
        raise

    all_passed = suite.summary()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()