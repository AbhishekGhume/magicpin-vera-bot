"""
Vera Bot — magicpin AI Challenge submission
A FastAPI server implementing the 4-context engagement composer using Claude.
"""

import os
import time
import uuid
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Vera Bot")
START = time.time()

# ─── In-memory state ────────────────────────────────────────────────────────
contexts: dict[tuple[str, str], dict] = {}      # (scope, context_id) -> {version, payload}
conversations: dict[str, list] = {}             # conversation_id -> [turns]
fired_triggers: set[str] = set()                # suppression: trigger ids already used
suppression_keys: set[str] = set()              # suppression by key
sent_bodies: dict[str, str] = {}                # conversation_id -> last body (anti-repeat)
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"


# ─── Pydantic models ──────────────────────────────────────────────────────────

class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str

class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_ctx(scope: str, ctx_id: str) -> Optional[dict]:
    entry = contexts.get((scope, ctx_id))
    return entry["payload"] if entry else None


def is_auto_reply(message: str) -> bool:
    """Detect WhatsApp Business canned auto-replies."""
    AUTO_REPLY_PATTERNS = [
        r"thank you for contacting",
        r"thanks for (reaching out|contacting|messaging)",
        r"we will (get back|respond|reply) (to you )?shortly",
        r"our team will",
        r"you have reached",
        r"we are currently",
        r"outside (of )?our (business|working) hours",
        r"this is an automated",
    ]
    msg_lower = message.lower()
    return any(re.search(p, msg_lower) for p in AUTO_REPLY_PATTERNS)


def is_hostile(message: str) -> bool:
    """Detect hostile / opt-out messages."""
    patterns = [
        r"\bstop\b", r"\bblock\b", r"\bunsubscribe\b",
        r"leave me alone", r"don.t (message|contact|bother)",
        r"why are you (bothering|messaging|contacting)",
        r"this is useless", r"not interested", r"go away",
        r"\bspam\b",
    ]
    msg_lower = message.lower()
    return any(re.search(p, msg_lower) for p in patterns)


def is_explicit_intent(message: str) -> bool:
    """Detect explicit 'let's go' / commit signals."""
    patterns = [
        r"\blet.s do it\b", r"\bgo ahead\b", r"\byes please\b",
        r"\bconfirm\b", r"\bsend it\b", r"\bdo it\b",
        r"\bsure\b", r"\byes\b", r"\bhaan\b", r"\bha\b",
        r"\bok(ay)?\b", r"\bkaro\b", r"\bkarein\b",
    ]
    msg_lower = message.lower().strip()
    return any(re.search(p, msg_lower) for p in patterns)


async def call_gemini(system: str, user: str, max_tokens: int = 500) -> str:
    """Call Gemini 2.5 Flash API and return text."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"{system}\n\n{user}"}
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0,
        }
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(
            f"{GEMINI_API_URL}?key={api_key}",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()
        if "candidates" in data and data["candidates"]:
            content = data["candidates"][0].get("content", {})
            parts = content.get("parts", [])
            if parts:
                return parts[0].get("text", "").strip()
        return ""


def build_system_prompt(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> str:
    voice = category.get("voice", {})
    peer = category.get("peer_stats", {})
    digest = category.get("digest", [])
    offers = category.get("offer_catalog", [])
    seasonal = category.get("seasonal_beats", [])
    trends = category.get("trend_signals", [])
    patient_lib = category.get("patient_content_library", [])

    identity = merchant.get("identity", {})
    sub = merchant.get("subscription", {})
    perf = merchant.get("performance", {})
    signals = merchant.get("signals", [])
    cust_agg = merchant.get("customer_aggregate", {})
    conv_hist = merchant.get("conversation_history", [])
    merch_offers = merchant.get("offers", [])
    review_themes = merchant.get("review_themes", [])

    trigger_kind = trigger.get("kind", "")
    trigger_payload = trigger.get("payload", {})
    urgency = trigger.get("urgency", 2)
    trigger_scope = trigger.get("scope", "merchant")

    owner_name = identity.get("owner_first_name", identity.get("name", "there"))
    lang_pref = identity.get("languages", ["en"])
    use_hindi = "hi" in lang_pref

    system = f"""You are Vera, magicpin's AI marketing assistant that messages merchants on WhatsApp.

## YOUR ROLE
Compose ONE WhatsApp message that will be sent to {"the merchant's customer" if trigger_scope == "customer" else "the merchant"}.

## CATEGORY: {category.get("display_name", category.get("slug", ""))}
Voice tone: {voice.get("tone", "")} | Register: {voice.get("register", "")}
Language: {"Hindi-English code-mix welcome" if use_hindi else "English"} (match merchant's language preference)
Allowed vocab: {", ".join(voice.get("vocab_allowed", [])[:8])}
FORBIDDEN words: {", ".join(voice.get("vocab_taboo", []))}

## PEER BENCHMARKS (for context)
{json.dumps(peer, indent=2)}

## CATEGORY DIGEST (this week's knowledge items — cite sources, never hallucinate)
{json.dumps(digest, indent=2)}

## OFFER CATALOG (use service+price format like "Dental Cleaning @ ₹299", not "flat 30% off")
{json.dumps(offers[:5], indent=2)}

## SEASONAL CONTEXT
{json.dumps(seasonal, indent=2)}

## TREND SIGNALS
{json.dumps(trends[:3], indent=2)}

## PATIENT/CUSTOMER CONTENT LIBRARY (content the merchant can reshare)
{json.dumps(patient_lib, indent=2)}

---

## MERCHANT: {identity.get("name", "")}
Owner: {owner_name} | City: {identity.get("city", "")} | Locality: {identity.get("locality", "")}
Verified: {identity.get("verified", False)} | Plan: {sub.get("plan", "")} | Days remaining: {sub.get("days_remaining", "?")}
Performance (30d): views={perf.get("views", 0)}, calls={perf.get("calls", 0)}, CTR={perf.get("ctr", 0):.3f} (peer avg CTR={peer.get("avg_ctr", 0):.3f})
7d delta: {json.dumps(perf.get("delta_7d", {}))}
Active offers: {[o["title"] for o in merch_offers if o.get("status") == "active"]}
Signals: {signals}
Customer aggregate: {json.dumps(cust_agg)}
Review themes: {json.dumps(review_themes)}
Recent conversation: {json.dumps(conv_hist[-3:] if conv_hist else [])}

---

## TRIGGER
Kind: {trigger_kind} | Urgency: {urgency}/5 | Source: {trigger.get("source", "")} | Scope: {trigger_scope}
Payload: {json.dumps(trigger_payload, indent=2)}

{"## CUSTOMER (message is SENT ON BEHALF OF MERCHANT to this patient/customer)" + chr(10) + json.dumps(customer, indent=2) if customer else ""}

---

## COMPOSITION RULES (follow exactly):
1. **No URLs** — never include http:// or www links (Meta will reject)
2. **One CTA only** — single call-to-action at the END of the message
3. **No preamble** — don't say "I hope you're doing well" or "I'm reaching out"
4. **No re-introduction** — don't say "This is Vera from magicpin" in follow-up messages
5. **Specificity wins** — use concrete numbers, dates, source citations from the digest
6. **Service+price format** — "Haircut @ ₹99" not "discount offer"
7. **Category voice** — clinical/peer tone for dentists; warm/practical for salons; etc.
8. **Language match** — {"use Hindi-English code-mix naturally" if use_hindi else "use English"}
9. **No hallucination** — only cite digest items that exist in the context above
10. **Max ~160 words** — WhatsApp-length, punchy
11. If trigger is customer-scoped, write FROM the merchant's perspective to the customer
12. Use emojis sparingly (1-2 max, only where natural)

## COMPULSION LEVERS (use 1-2 per message):
- Specificity: concrete numbers, dates, citations
- Loss aversion: "before this window closes", "only X slots left"
- Social proof: "3 dentists in your area did Y this month"
- Effort externalization: "I've drafted it — just say go"
- Curiosity: "Want to see the full list?"
- Single binary commitment: Reply YES / Reply 1 for Wed (not multi-choice)

## OUTPUT FORMAT
Return ONLY the WhatsApp message text. No explanation, no "Here is the message:", no quotes around it."""
    return system


def build_user_prompt(trigger_kind: str, merchant: dict, trigger: dict, customer: Optional[dict]) -> str:
    identity = merchant.get("identity", {})
    owner = identity.get("owner_first_name", "there")
    return f"""Compose the WhatsApp message for trigger kind: {trigger_kind}
Merchant owner name: {owner}
{"Customer: " + (customer.get("identity", {}).get("name", "customer") if customer else "") }

Write the message now:"""


async def compose_message(trigger: dict, merchant_id: str) -> Optional[dict]:
    """Compose a message using Claude for a given trigger + merchant."""
    merchant = get_ctx("merchant", merchant_id)
    if not merchant:
        return None

    cat_slug = merchant.get("category_slug", "")
    category = get_ctx("category", cat_slug)
    if not category:
        return None

    trigger_kind = trigger.get("kind", "")
    customer_id = trigger.get("customer_id")
    customer = get_ctx("customer", customer_id) if customer_id else None
    trigger_scope = trigger.get("scope", "merchant")

    system = build_system_prompt(category, merchant, trigger, customer)
    user = build_user_prompt(trigger_kind, merchant, trigger, customer)

    body = await call_gemini(system, user, max_tokens=400)
    if not body:
        return None

    # Post-process: strip URLs just in case
    body = re.sub(r'https?://\S+', '', body).strip()
    body = re.sub(r'www\.\S+', '', body).strip()

    send_as = "merchant_on_behalf" if trigger_scope == "customer" else "vera"
    cta = "multi_choice_slot" if trigger_kind in ("recall_due", "appointment_tomorrow", "trial_followup") else "open_ended"
    if trigger_kind in ("renewal_due", "gbp_unverified", "winback_eligible"):
        cta = "binary_yes_no"

    owner = merchant.get("identity", {}).get("owner_first_name", merchant_id)
    template_name = f"vera_{trigger_kind}_v1"

    return {
        "conversation_id": f"conv_{merchant_id}_{trigger.get('id', uuid.uuid4().hex[:8])}",
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "send_as": send_as,
        "trigger_id": trigger.get("id", ""),
        "template_name": template_name,
        "template_params": [owner, trigger_kind, body[:80]],
        "body": body,
        "cta": cta,
        "suppression_key": trigger.get("suppression_key", f"{trigger_kind}:{merchant_id}"),
        "rationale": f"Trigger: {trigger_kind} (urgency {trigger.get('urgency', 2)}). "
                     f"Category: {cat_slug}. Merchant: {merchant_id}. "
                     f"{'Customer-scoped, sending on behalf of merchant.' if customer else 'Merchant-scoped, sent as Vera.'}"
    }


async def compose_reply(conversation_id: str, merchant_id: str, customer_id: Optional[str],
                        message: str, turn_history: list) -> dict:
    """Compose a reply to an incoming merchant/customer message."""
    merchant = get_ctx("merchant", merchant_id)
    customer = get_ctx("customer", customer_id) if customer_id else None

    if not merchant:
        return {"action": "send", "body": "Got it, give me a moment.", "cta": "open_ended",
                "rationale": "Missing merchant context; holding."}

    cat_slug = merchant.get("category_slug", "")
    category = get_ctx("category", cat_slug)
    identity = merchant.get("identity", {})
    owner = identity.get("owner_first_name", "there")
    lang_pref = identity.get("languages", ["en"])
    use_hindi = "hi" in lang_pref

    history_text = "\n".join([f"[{t['from']}]: {t['msg']}" for t in turn_history[-6:]])
    last_sent = sent_bodies.get(conversation_id, "")

    voice = category.get("voice", {}) if category else {}
    digest = category.get("digest", []) if category else []
    offers = category.get("offer_catalog", []) if category else []

    system = f"""You are Vera, magicpin's AI marketing assistant.
You are replying to a message in an ongoing WhatsApp conversation with the merchant.

## MERCHANT
Name: {identity.get("name", "")} | Owner: {owner} | Category: {cat_slug}
Language: {"Hindi-English code-mix" if use_hindi else "English"}

## CONVERSATION HISTORY (most recent 6 turns)
{history_text}

## YOUR LAST MESSAGE (DO NOT REPEAT THIS VERBATIM)
{last_sent}

## AVAILABLE KNOWLEDGE
Voice tone: {voice.get("tone", "")}
Forbidden words: {voice.get("vocab_taboo", [])}
Digest items: {json.dumps(digest[:3], indent=2)}
Offer catalog: {json.dumps(offers[:4], indent=2)}
Merchant signals: {merchant.get("signals", [])}
Customer aggregate: {json.dumps(merchant.get("customer_aggregate", {}), indent=2)}

## REPLY RULES
1. No URLs
2. One CTA at the end
3. No preamble ("Great question!", "Absolutely!")
4. If merchant said YES / committed / "let's do it" → immediately take action (draft something, confirm next step), don't ask another question
5. If merchant asked a question → answer it concisely, then add a next-step CTA
6. If merchant seems confused → clarify simply
7. Max ~120 words
8. Never repeat the exact previous message
9. {"Use Hindi-English code-mix naturally" if use_hindi else "Use English"}

Return ONLY the reply message text. No explanation."""

    user_msg = f"""Merchant just replied: "{message}"

Write your reply now:"""

    body = await call_gemini(system, user_msg, max_tokens=300)
    if not body:
        body = f"Got it, {owner}! I'll take care of that. Anything else you need?"

    # Strip URLs
    body = re.sub(r'https?://\S+', '', body).strip()
    body = re.sub(r'www\.\S+', '', body).strip()

    # Anti-repeat: if body too similar to last sent, rephrase instruction
    if last_sent and body.lower()[:50] == last_sent.lower()[:50]:
        body = f"Understood! Let me get that sorted for you right away. Just confirm and I'll proceed."

    return {
        "action": "send",
        "body": body,
        "cta": "open_ended",
        "rationale": f"Replied to merchant message (turn). Intent detected, advancing conversation."
    }


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _) in contexts:
        if scope in counts:
            counts[scope] += 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Vera Challenge Bot",
        "team_members": ["challenger"],
        "model": "gemini-2.5-flash",
        "approach": "4-context composer (category+merchant+trigger+customer) with trigger-kind routing, auto-reply detection, intent-transition handling, and anti-repetition guard",
        "contact_email": "vera@magicpin.com",
        "version": "1.0.0",
        "submitted_at": datetime.now(timezone.utc).isoformat()
    }


@app.post("/v1/context")
async def push_context(body: CtxBody):
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
    contexts[key] = {"version": body.version, "payload": body.payload}
    ack_id = f"ack_{body.context_id}_v{body.version}"
    stored_at = datetime.now(timezone.utc).isoformat()
    return {"accepted": True, "ack_id": ack_id, "stored_at": stored_at}


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    for trg_id in body.available_triggers:
        # Skip already-fired triggers
        if trg_id in fired_triggers:
            continue

        trg_entry = contexts.get(("trigger", trg_id))
        if not trg_entry:
            continue
        trg = trg_entry["payload"]

        # Check suppression key
        sup_key = trg.get("suppression_key", "")
        if sup_key in suppression_keys:
            continue

        # Check expiry
        expires = trg.get("expires_at", "")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                now_dt = datetime.fromisoformat(body.now.replace("Z", "+00:00"))
                if now_dt > exp_dt:
                    continue
            except Exception:
                pass

        merchant_id = trg.get("merchant_id")
        if not merchant_id:
            continue

        action = await compose_message(trg, merchant_id)
        if action:
            # Track sent body for anti-repeat
            conv_id = action["conversation_id"]
            sent_bodies[conv_id] = action["body"]
            conversations.setdefault(conv_id, []).append({"from": "vera", "msg": action["body"]})

            fired_triggers.add(trg_id)
            if sup_key:
                suppression_keys.add(sup_key)

            actions.append(action)

            # Limit to reasonable batch per tick
            if len(actions) >= 3:
                break

    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv_id = body.conversation_id
    message = body.message

    # Record incoming message
    conversations.setdefault(conv_id, []).append({"from": body.from_role, "msg": message})
    turn_history = conversations[conv_id]

    # Count auto-replies in this conversation
    auto_reply_count = sum(
        1 for t in turn_history
        if t["from"] != "vera" and is_auto_reply(t["msg"])
    )

    # --- Auto-reply detection ---
    if is_auto_reply(message):
        if auto_reply_count >= 3:
            return {"action": "end",
                    "rationale": "Auto-reply detected 3+ times. No real engagement. Closing conversation."}
        elif auto_reply_count == 2:
            return {"action": "wait", "wait_seconds": 86400,
                    "rationale": "Same auto-reply twice in a row. Owner not at phone. Waiting 24h."}
        else:
            return {
                "action": "send",
                "body": "Looks like an auto-reply 😊 When you see this, just reply 'Yes' and I'll continue.",
                "cta": "binary_yes_no",
                "rationale": "First auto-reply detected. Flagging for the owner."
            }

    # --- Hostile / opt-out detection ---
    if is_hostile(message):
        return {"action": "end",
                "rationale": "Merchant expressed frustration or opt-out. Closing gracefully."}

    # --- Explicit intent transition ---
    if is_explicit_intent(message) and body.turn_number <= 4:
        merchant = get_ctx("merchant", body.merchant_id or "")
        if merchant:
            identity = merchant.get("identity", {})
            owner = identity.get("owner_first_name", "there")
            agg = merchant.get("customer_aggregate", {})
            high_risk = agg.get("high_risk_adult_count", agg.get("total_active_members", 0))
            cat_slug = merchant.get("category_slug", "")
            category = get_ctx("category", cat_slug)
            offers = category.get("offer_catalog", []) if category else []
            offer_ex = offers[0]["title"] if offers else "your top service"

            sent_bodies[conv_id] = f"action_confirmation_{conv_id}"
            return {
                "action": "send",
                "body": f"Perfect {owner}! Starting now — I'll draft the message for your {high_risk or 'active'} customers. Reply CONFIRM to send, or CHANGE to adjust.",
                "cta": "binary_confirm_cancel",
                "rationale": "Merchant explicitly committed. Switching from pitch to action mode immediately."
            }

    # --- Normal reply composition ---
    result = await compose_reply(
        conv_id,
        body.merchant_id or "",
        body.customer_id,
        message,
        turn_history
    )

    # Record our reply
    if result.get("action") == "send":
        sent_bodies[conv_id] = result["body"]
        conversations[conv_id].append({"from": "vera", "msg": result["body"]})

    return result


@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    fired_triggers.clear()
    suppression_keys.clear()
    sent_bodies.clear()
    return {"status": "wiped"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)