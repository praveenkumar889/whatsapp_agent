"""
ai/pricing.py — PricingResult: single source of truth for all pricing

All pricing in the system derives from this one dataclass:
  - Negotiator  → builds PricingResult → passes to order summary
  - Summary     → reads PricingResult → formats WhatsApp message
  - Invoice     → reads PricingResult → formats PDF rows
  - Database    → reads PricingResult → writes order fields
  - Prompts     → read PricingResult fields as named variables

Previously: subtotal, GST, total, store_disc, neg_disc were recalculated
independently in product_followup.py, router.py, negotiator.py, and
invoice_handler.py. This caused subtle mismatches (e.g. invoice showing
Rs.2650.30 while order summary showed Rs.2650) because each calculation
used slightly different rounding or different source fields.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PricingResult:
    """
    Immutable snapshot of all pricing for a single product order.

    Construct via PricingResult.build() — never set fields manually.
    All monetary values are in INR, rounded to 2 decimal places.
    """
    # ── Inputs ────────────────────────────────────────────────────────────────
    regular_unit_price:  float   # List price before any discount
    quantity:            int
    gst_rate:            float   # e.g. 0.18 for 18%

    # ── Store tier offer ──────────────────────────────────────────────────────
    store_disc_pct:      int     # e.g. 8 for 8% OFF
    store_unit_price:    float   # regular × (1 - store_disc_pct/100)

    # ── Negotiation ───────────────────────────────────────────────────────────
    negotiated_unit_price: float  # Final agreed price (= store_unit_price if no negotiation)
    negotiation_rounds:    int    # 0 = no negotiation happened

    # ── Derived totals (computed in build()) ──────────────────────────────────
    store_discount_amount:       float = field(default=0.0)  # (regular - store) × qty
    negotiation_discount_amount: float = field(default=0.0)  # (store - negotiated) × qty
    total_discount_amount:       float = field(default=0.0)  # (regular - negotiated) × qty
    subtotal:                    float = field(default=0.0)   # negotiated × qty
    gst_amount:                  float = field(default=0.0)
    total_payable:               float = field(default=0.0)

    @classmethod
    def build(
        cls,
        regular_unit_price: float,
        quantity: int,
        gst_rate: float = 0.18,
        store_disc_pct: int = 0,
        negotiated_unit_price: Optional[float] = None,
        negotiation_rounds: int = 0,
    ) -> "PricingResult":
        """
        Build a PricingResult from raw inputs. All derived fields are
        computed once here — no other code needs to recalculate them.
        """
        store_unit = round(regular_unit_price * (1 - store_disc_pct / 100), 2)
        neg_unit   = negotiated_unit_price if negotiated_unit_price is not None else store_unit

        store_disc_amt = round((regular_unit_price - store_unit) * quantity, 2)
        neg_disc_amt   = round((store_unit - neg_unit) * quantity, 2) if neg_unit < store_unit else 0.0
        total_disc_amt = round((regular_unit_price - neg_unit) * quantity, 2)

        subtotal    = round(neg_unit * quantity, 2)
        gst_amount  = round(subtotal * gst_rate, 2)
        total_pay   = round(subtotal + gst_amount, 2)

        return cls(
            regular_unit_price           = round(regular_unit_price, 2),
            quantity                     = quantity,
            gst_rate                     = gst_rate,
            store_disc_pct               = store_disc_pct,
            store_unit_price             = store_unit,
            negotiated_unit_price        = round(neg_unit, 2),
            negotiation_rounds           = negotiation_rounds,
            store_discount_amount        = store_disc_amt,
            negotiation_discount_amount  = neg_disc_amt,
            total_discount_amount        = total_disc_amt,
            subtotal                     = subtotal,
            gst_amount                   = gst_amount,
            total_payable                = total_pay,
        )

    @property
    def was_negotiated(self) -> bool:
        return self.negotiation_rounds > 0 and self.negotiation_discount_amount > 0

    @property
    def gst_pct(self) -> int:
        return int(self.gst_rate * 100)

    def to_whatsapp_summary(self, product_name: str, sender_name: str) -> str:
        """
        Renders the pre-confirm order summary in WhatsApp format.
        Single source — used by both the negotiation acceptance path and
        the plain-order confirmation path.
        """
        lines = [
            f"Here's your order summary, {sender_name}! Please review:",
            "",
            f"• *Product:* {product_name}",
            f"• *Quantity:* {self.quantity} units",
            f"• *Regular price:* Rs.{self.regular_unit_price:,.0f}/unit",
        ]
        if self.was_negotiated:
            lines += [
                f"• *Store offer {self.store_disc_pct}% OFF:* Rs.{self.store_unit_price:,.0f}/unit",
                f"• *Negotiated price:* Rs.{self.negotiated_unit_price:,.0f}/unit",
            ]
        elif self.store_disc_pct > 0:
            lines.append(f"• *Store offer {self.store_disc_pct}% OFF:* Rs.{self.negotiated_unit_price:,.0f}/unit")
        else:
            lines.append(f"• *Price per unit:* Rs.{self.negotiated_unit_price:,.0f}")

        lines += [
            f"• *Subtotal:* Rs.{self.subtotal:,.2f}",
            f"• *GST ({self.gst_pct}%):* Rs.{self.gst_amount:,.2f}",
            f"• *Total Payable:* Rs.{self.total_payable:,.2f}",
        ]

        if self.total_discount_amount > 0:
            lines.append("")
            if self.was_negotiated and self.store_discount_amount > 0:
                lines += [
                    f"🎁 *Total savings: Rs.{self.total_discount_amount:,.0f}*",
                    f"   • Store offer: Rs.{self.store_discount_amount:,.0f}",
                    f"   • Negotiation: Rs.{self.negotiation_discount_amount:,.0f}",
                ]
            else:
                lines.append(f"🎁 *You save Rs.{self.total_discount_amount:,.0f} on this order!*")

        lines += ["", "Reply *Confirm* to place your order and receive your invoice! 🎉"]
        return "\n".join(lines)

    def to_invoice_fields(self) -> dict:
        """
        Returns the dict of extra fields to pass to create_order / invoice PDF.
        Maps directly onto the fields the invoice generator reads.
        """
        return {
            "original_amount":              round(self.regular_unit_price * self.quantity, 2),
            "store_discount_pct":           self.store_disc_pct,
            "store_discount_amount":        self.store_discount_amount if self.store_discount_amount > 0 else None,
            "negotiation_discount_amount":  self.negotiation_discount_amount if self.negotiation_discount_amount > 0 else None,
        }

    def to_negotiation_prompt_vars(self) -> dict:
        """
        Returns named variables for use in negotiation prompt templates.
        The prompt receives structured data and decides how to phrase it —
        no format string like "Rs.X → Rs.Y" is hardcoded here.
        """
        return {
            "regular_unit_price":       f"{self.regular_unit_price:,.0f}",
            "store_disc_pct":           self.store_disc_pct,
            "store_unit_price":         f"{self.store_unit_price:,.0f}",
            "negotiated_unit_price":    f"{self.negotiated_unit_price:,.0f}",
            "quantity":                 self.quantity,
            "subtotal":                 f"{self.subtotal:,.2f}",
            "gst_pct":                  self.gst_pct,
            "gst_amount":               f"{self.gst_amount:,.2f}",
            "total_payable":            f"{self.total_payable:,.2f}",
            "store_discount_amount":    f"{self.store_discount_amount:,.0f}",
            "negotiation_discount_amount": f"{self.negotiation_discount_amount:,.0f}",
            "total_saved":              f"{self.total_discount_amount:,.0f}",
        }

    @classmethod
    def from_neg_state(cls, neg_state: dict, gst_rate: float = 0.18) -> "PricingResult":
        """
        Reconstruct PricingResult from a negotiation state dict.
        Used in router.py and product_followup.py confirmation paths.
        """
        return cls.build(
            regular_unit_price    = float(neg_state.get("price_num") or 0),
            quantity              = int(neg_state.get("quantity") or 0),
            gst_rate              = gst_rate,
            store_disc_pct        = int(neg_state.get("auto_offer_disc_pct") or 0),
            negotiated_unit_price = float(neg_state.get("last_offer_price") or
                                          neg_state.get("auto_offer_unit_price") or 0),
            negotiation_rounds    = int(neg_state.get("rounds") or 0),
        )