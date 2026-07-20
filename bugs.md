# Comprehensive Bug Fix History

This document provides an end-to-end record of all major bugs encountered and resolved in this workspace. Each entry details the symptoms, root cause, code/database changes, and final outcomes to maintain a clear history for future deployments.

---

## 1. AttributeError: 'str' object has no attribute 'get' in FAQ formatting

### Bug Description
* **Symptoms:** The application crashed with an `AttributeError` when generating product detail cards containing FAQs.
* **Root Cause:** External product APIs sometimes return FAQ entries as plain strings rather than the expected `{"question", "answer"}` dictionary format. When the code in [product_followup.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/product_followup.py) attempted to format the FAQs, it called `.get()` on these string items, resulting in a crash.
* **Impact:** Customers received server error messages when asking for product details that included string-based FAQs.

### Solution
* **Code Changes:** Added a helper function `_format_faq_entry` in [product_followup.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/product_followup.py) to check the type of each FAQ entry:
  ```python
  def _format_faq_entry(f) -> dict:
      if isinstance(f, dict):
          return {"q": f.get("question"), "a": f.get("answer")}
      return {"q": f, "a": ""}
  ```
* **Result:** Safe formatting of FAQ data regardless of whether the source API returns them as dictionaries or raw strings.

---

## 2. AttributeError: 'str' object has no attribute 'get' in Policies formatting

### Bug Description
* **Symptoms:** Server crash when preparing the product context details containing delivery/return policies.
* **Root Cause:** Similar to the FAQ bug, external product caches sometimes store policy items as raw strings instead of structured dictionaries. Calling `.get("content")` directly on a string triggered an `AttributeError`.
* **Impact:** Request failures during final checkout or policy explanation steps.

### Solution
* **Code Changes:** Updated the list comprehension in [product_followup.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/product_followup.py) to conditionally handle string values:
  ```python
  "delivery_policy": [
      pol if isinstance(pol, str) else pol.get("content", "")
      for pol in cached_product.get("policies", [])
  ] if "policies" in cached_product else []
  ```
* **Result:** The system safely processes both string and dictionary representations of policy documents.

---

## 3. AttributeError: NoneType has no attribute 'lower' in Intent Routing

### Bug Description
* **Symptoms:** Critical crash in the orchestrator pipeline on incoming requests when determining requested knowledge fields.
* **Root Cause:** The `requested_knowledge_field` returned by the intent classifier could occasionally be `None` (for example, on general greetings or out-of-scope queries). Calling `.lower()` directly on a `NoneType` object caused an unhandled exception.
* **Impact:** Total pipeline failure for any requests that did not resolve to a specific knowledge field.

### Solution
* **Code Changes:** Added validation checks in [router.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/pipeline/router.py) to ensure the variable is not `None` before string methods are invoked:
  ```python
  _req_field = getattr(_routing, "requested_knowledge_field", "none") or "none"
  if _needs_customer_history and _req_field.lower() == "none":
  ```
* **Result:** Robust fallback behavior when no target knowledge field is classified.

---

## 4. Pyright Type Mismatch on `product_context` Update

### Bug Description
* **Symptoms:** Static type checker errors flagged on the `product_context.update(...)` call in [product_followup.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/product_followup.py).
* **Root Cause:** The `product_context` dictionary had a strict type annotation of `dict[str, str]`. However, the update method was populating fields with mixed types, including lists (like `delivery_policy` and `faqs`), integers, and floats.
* **Impact:** CI/CD pipeline type validation failures.

### Solution
* **Code Changes:** Widened the type annotation on `product_context` in [product_followup.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/product_followup.py):
  ```python
  product_context: dict[str, Any] = cached_product.copy()
  ```
* **Result:** Successful Pyright type resolution without code warnings.

---

## 5. NoneType Error on Dynamic Asset Answers

### Bug Description
* **Symptoms:** Blank responses or crashes when customers asked direct questions about product specifications (e.g. "Is this waterproof?").
* **Root Cause:** The dispatcher dumped the raw text block or template directly from the database instead of answering the user's specific query naturally, causing crashes if values were missing.
* **Impact:** Poor customer experience with raw templates being exposed.

### Solution
* **Code Changes:** Added a dynamic answering block in [router.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/pipeline/router.py) that uses `knowledge_asset_answer_prompt` to pass the query and the raw asset text to the LLM.
* **Result:** Friendly, direct answers to specific queries (e.g., "Yes, this lamp is waterproof and designed for outdoor use...").

---

## 6. Empty Product Specifications/Warranty Responses

### Bug Description
* **Symptoms:** Bot answered that it couldn't find specifications or warranty details, even though they existed in the database under keys like `feature_descriptions` or `guarantee`.
* **Root Cause:** The mapping of intent fields to product cache columns was static and hardcoded.
* **Impact:** Incomplete details presented to the customer.

### Solution
* **Code Changes:** Replaced static key lookups with a dynamic database-driven mapping. The system reads the mapping dynamically from `product_field_aliases` in the `prompt_templates` table.
* **Result:** Zero hardcoding; easily accommodates schema changes across different tenants.

---

## 7. Category Searches Overriding New Search Check (e.g., "Outdoor")

### Bug Description
* **Symptoms:** Typing a category keyword like `"Outdoor"` returned a comparison table instead of matching product cards with preview images (which worked for `"solar"`).
* **Root Cause:** A loose substring check in [product_followup.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/product_followup.py) evaluated `"Outdoor"` (length 7) as a substring match for `"Nura 4W Four Way LED Outdoor Wall Light"`. This overrode the LLM's `is_new_search` classification to `False`.
* **Impact:** Broad category searches were treated as product details follow-ups.

### Solution
* **Code Changes:** Restricted `_matches_selection` to exact product name matches only (`_msg_lower == name`) in [product_followup.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/product_followup.py).
* **Result:** Category searches route to GraphRAG correctly and display catalog options.

---

## 8. Multi-Invoice Requests Hardcoded to Single Latest Order

### Bug Description
* **Symptoms:** Asking for `"past 2 invoices"` or `"previous orders invoices"` only returned the latest invoice (`INV#24B32`).
* **Root Cause:** The `handle_invoice_request` function was hardcoded to fetch only the single latest order via `get_last_order_from_orders`.
* **Impact:** Inability to retrieve historical invoices.

### Solution
* **Code Changes:** Added a dynamic LLM-based parser `_extract_invoice_limit` using `invoice_limit_extract_prompt` to extract the quantity. If the limit > 1, it queries `get_last_n_orders_from_orders` and outputs a list of invoice links.
* **Result:** Multi-invoice queries successfully list all requested download URLs.

---

## 9. Order History Limit Word-Form Numbers Not Parsed

### Bug Description
* **Symptoms:** Querying `"whats my previous two orders?"` returned 3 orders instead of 2.
* **Root Cause:** The database-driven prompt `memory_query_classifier_prompt` only specified digit examples (e.g. `"2"`, `"3"`) for extracting the count. Words like `"two"` fell back to the default limit of `3`.
* **Impact:** Limit parsing failed for written numbers.

### Solution
* **Code Changes:** Changed the fallback default in [customer_history_handler.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/customer_history_handler.py) from `3` to `1` (safest fallback).
* **Database Prompt Changes:** Created a migration to update the DB prompt `memory_query_classifier_prompt` to explicitly handle written numbers (`"two"`, `"three"`, etc.) alongside digits.
* **Result:** Correct count extraction for both digits and word-form numbers.

---

## 10. Stale Product Context on Fresh Order

### Bug Description
* **Symptoms:** After discussing a specific product (e.g. `"Mery Matha"`), starting a checkout (e.g. `"i want to order 5 units"`) resolved to the first product in the catalog (`"Vinayagar"`).
* **Root Cause:** The resolved product selection was not persisted to the database during the selection resolution step, leading to stale session state lookup.
* **Impact:** Checkout flows could process the wrong product.

### Solution
* **Code Changes:** Updated `ProductContextResolver.resolve` in [product_context_resolver.py](file:///c:/Users/gudal/Downloads/whatsapp-bot/whatsapp-bot/ai/product_context_resolver.py) to save the resolved selection using `save_last_discussed_product` immediately upon resolution.
* **Result:** Checkout flows correctly pick up the most recently discussed product.
