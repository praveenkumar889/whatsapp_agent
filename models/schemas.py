# models/schemas.py

from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class IncomingMessage:
    # ── Tracing ───────────────────────────────────────────────────────────────
    trace_id:        str
    message_id:      str
    session_id:      str
    channel:         str
    timestamp:       int

    # ── Tenant ────────────────────────────────────────────────────────────────
    tenant_id:       str
    waba_id:         str
    phone_number_id: str
    biz_name:        str
    region:          str
    timezone:        str
    language:        str

    # ── Sender ────────────────────────────────────────────────────────────────
    sender_name:  str
    sender_phone: str

    # ── Message ───────────────────────────────────────────────────────────────
    text:          str
    original_type: str
    received_at:   str

    # ── Billing fields ────────────────────────────────────────────────────────
    tagline:      Optional[str] = None
    city:         Optional[str] = None
    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    website:      Optional[str] = None
    upi_id:       Optional[str] = None
    account_name: Optional[str] = None
    gstin:        Optional[str] = None
    state_code:   Optional[str] = None
    gst_rate:     float = 0.18

    # ── Core handler prompts (from tenants table) ─────────────────────────────
    intent_system_prompt:   Optional[str] = None
    greeting_system_prompt: Optional[str] = None
    entity_system_prompt:   Optional[str] = None
    escalation_prompt:      Optional[str] = None
    unknown_prompt:         Optional[str] = None

    # ── Invoice handler prompts ───────────────────────────────────────────────
    invoice_inquiry_prompt:       Optional[str] = None
    invoice_confirm_prompt:       Optional[str] = None
    invoice_order_confirm_prompt: Optional[str] = None
    invoice_confirmation_prompt:  Optional[str] = None

    # ── Negotiation detection prompts ─────────────────────────────────────────
    neg_is_request_prompt:        Optional[str] = None
    neg_extract_qty_prompt:       Optional[str] = None
    neg_detect_counter_prompt:    Optional[str] = None
    neg_more_discount_prompt:     Optional[str] = None
    neg_detect_accept_prompt:     Optional[str] = None
    neg_detect_qty_change_prompt: Optional[str] = None

    # ── Negotiation reply prompts ─────────────────────────────────────────────
    neg_no_discount_prompt:    Optional[str] = None
    neg_first_offer_prompt:    Optional[str] = None
    neg_counter_offer_prompt:  Optional[str] = None
    neg_final_price_prompt:    Optional[str] = None

    # ── Fast confirm prompt ───────────────────────────────────────────────────
    fast_confirm_prompt: Optional[str] = None

    # ── Product summary + recommendation prompt (shown on quantity updates) ───
    product_summary_recommendation_prompt: Optional[str] = None

    max_negotiation_rounds: Optional[int] = None
    neg_floor_disc_pct:     Optional[int] = None
    neg_floor_multiplier:   Optional[float] = None
    intent_min_confidence:  Optional[float] = None
    require_offer_disclosure: Optional[bool] = None
    max_image_products:     Optional[int] = None


    # ── Per-tenant config ─────────────────────────────────────────────────────
    valid_intents:    Optional[List[str]] = None
    graphrag_api_url: Optional[str] = None
    products_api_url: Optional[str] = None
    access_token:     Optional[str] = None

    # ── Quick action phrases (tenant config, pre-normalized at load time) ────
    # Stored as JSONB in tenants.quick_actions. Normalized to frozenset of
    # casefold()ed strings by _apply_tenant() — zero allocation at message time.
    # Shape: {"ORDER_CONFIRM": frozenset({"yes","ok",...}), "ORDER_CANCEL": ...}
    # See utils/conversation_actions.py for the matching helper.
    quick_actions: Optional[Dict[str, frozenset]] = None

    # ── Media ─────────────────────────────────────────────────────────────────
    media_id:        Optional[str]   = None
    media_mime_type: Optional[str]   = None
    media_binary:    Optional[bytes] = None
    media_url:       Optional[str]   = None

    # ── Quoted message ────────────────────────────────────────────────────────
    quoted_message_id: Optional[str] = None
    quoted_caption:    Optional[str] = None

    # ── Output ────────────────────────────────────────────────────────────────
    captured_replies: List[dict] = field(default_factory=list)
    raw:              dict       = field(default_factory=dict)
    _cached_neg_state: Optional[dict] = None

    # ── Runtime-computed attributes (set after construction) ──────────────────
    _routing:         Optional["RoutingDecision"] = field(default=None, repr=False)
    _cached_arc:      Optional[object]            = field(default=None, repr=False)
    resolved_product: Optional[str]               = field(default=None, repr=False)
    resolved_query:   Optional[str]               = field(default=None, repr=False)



@dataclass
class RoutingDecision:
    """
    Tiny, tenant-agnostic routing signal — deliberately NOT the full
    AIRequestContext. Answers exactly one question: which subsystems does
    this message actually need? Computed once, as part of the intent
    classification call that already runs on every message — no extra
    LLM call added.

    operation is a semantic label, not a domain-specific keyword match —
    "MODIFY_WORKFLOW" means the same thing whether the workflow is an
    order, an appointment, a claim, or a shipment; the LLM decides this
    from meaning, not from tenant-specific vocabulary.
    """
    operation:              str   # "NEW_SEARCH" | "MODIFY_WORKFLOW" | "OTHER"
    needs_graphrag:         bool
    needs_memory:           bool
    needs_workflow_state:   bool
    needs_product_context:  bool


@dataclass
class IntentResult:
    intent:           str
    confidence_score: float
    raw_text:         str
    routing:           Optional[RoutingDecision] = None


@dataclass
class OrderItem:
    product_name:   Optional[str]
    quantity_value: Optional[int]
    quantity_unit:  Optional[str]

    @property
    def is_complete(self) -> bool:
        return self.product_name is not None and self.quantity_value is not None

    @property
    def missing(self) -> List[str]:
        m = []
        if not self.product_name:       m.append("product_name")
        if self.quantity_value is None: m.append("quantity")
        return m

    @property
    def quantity_str(self) -> Optional[str]:
        if self.quantity_value and self.quantity_unit:
            return f"{self.quantity_value} {self.quantity_unit}"
        return str(self.quantity_value) if self.quantity_value else None


@dataclass
class EntityResult:
    items:             List[OrderItem]
    delivery_date:     str
    invoice_number:    Optional[str]
    payment_reference: Optional[str]
    missing_entities:  List[str]
    raw_text:          str
    tenant_id:         str

    @property
    def product_name(self)   -> Optional[str]: return self.items[0].product_name   if self.items else None
    @property
    def quantity_value(self) -> Optional[int]: return self.items[0].quantity_value if self.items else None
    @property
    def quantity_unit(self)  -> Optional[str]: return self.items[0].quantity_unit  if self.items else None
    @property
    def quantity(self)       -> Optional[str]: return self.items[0].quantity_str   if self.items else None
    @property
    def all_complete(self)   -> bool: return bool(self.items) and all(i.is_complete for i in self.items)
    @property
    def is_multi_product(self) -> bool: return len(self.items) > 1