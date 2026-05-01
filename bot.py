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
        r"thank you for (contacting|reaching out|messaging|your (message|inquiry))",
        r"thanks for (reaching out|contacting|messaging|your (message|inquiry))",
        r"we will (get back|respond|reply|contact you) (to you )?(shortly|soon|asap)",
        r"our team will",
        r"you have reached",
        r"we are (currently|at the moment)",
        r"outside (of )?our (business|working|office) hours",
        r"this is an automated",
        r"auto.?reply",
        r"out of office",
        r"we appreciate your (patience|message)",
        r"response time",
        r"get back to you",
    ]
    msg_lower = message.lower()
    return any(re.search(p, msg_lower, re.IGNORECASE) for p in AUTO_REPLY_PATTERNS)


def is_hostile(message: str) -> bool:
    """Detect hostile / opt-out messages."""
    patterns = [
        r"\bstop\b", r"\bblock\b", r"\bunsubscribe\b", r"\bdelete\b",
        r"leave me alone", r"don.?t (message|contact|bother|text|call)",
        r"why are you (bothering|messaging|contacting|texting|calling)",
        r"(this is |you.re )?useless", r"(not interested|not interested in|not needed)",
        r"(go away|get lost)", r"\bspam\b", r"do not (contact|message|call)",
        r"(remove|stop) (me|this|this number)",
        r"(never|don.?t) (message|text|call) (me|again)",
    ]
    msg_lower = message.lower()
    return any(re.search(p, msg_lower, re.IGNORECASE) for p in patterns)


def is_explicit_intent(message: str) -> bool:
    """Detect explicit 'let's go' / commit signals."""
    patterns = [
        r"\blet.?s (do it|go|proceed|start|go ahead)\b",
        r"\bgo ahead\b", r"\byes please\b", r"\bsure\b",
        r"\bconfirm\b", r"\bsend it\b", r"\bdo it\b",
        r"\byes\b", r"\bhaan\b", r"\bha\b", r"\bhmmm?\b",
        r"\bok(ay)?\b", r"\bkaro\b", r"\bkarein\b", r"\bthik hai\b",
        r"\bstarted\b", r"\bproceed\b", r"send the (message|sms|text|draft)",
        r"go for it", r"sounds good", r"let me do it", r"ready to",
    ]
    msg_lower = message.lower().strip()
    return any(re.search(p, msg_lower, re.IGNORECASE) for p in patterns)


async def call_gemini(system: str, user: str, max_tokens: int = 800) -> str:
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
            "temperature": 0.3,
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
    offers = category.get("offer_catalog", [])[:3]  # Limit to 3

    identity = merchant.get("identity", {})
    sub = merchant.get("subscription", {})
    perf = merchant.get("performance", {})
    signals = merchant.get("signals", [])
    cust_agg = merchant.get("customer_aggregate", {})
    conv_hist = merchant.get("conversation_history", [])[-2:]  # Last 2 only
    merch_offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"][:2]
    review_themes = merchant.get("review_themes", [])[:2]

    trigger_kind = trigger.get("kind", "")
    trigger_payload = trigger.get("payload", {})
    urgency = trigger.get("urgency", 2)
    trigger_scope = trigger.get("scope", "merchant") 

    owner_name = identity.get("owner_first_name", identity.get("name", "there"))
    lang_pref = identity.get("languages", ["en"])
    use_hindi = "hi" in lang_pref

    # Format offers compactly
    offer_str = ", ".join([f"{o['title']}" for o in offers]) if offers else "service offerings"
    active_offers_str = ", ".join([f"{o['title']}" for o in merch_offers]) if merch_offers else "current offers"

    # Format digest as bullet points, not JSON
    digest_str = "\n".join([f"• {d.get('title', '')}: {d.get('detail', '')[:60]}" for d in digest[:3]]) if digest else "(no digest)"

    # Format review themes as summaries
    theme_str = ", ".join([f"{t['theme']}({t['sentiment']})" for t in review_themes]) if review_themes else "mixed feedback"

    # Compact signals
    signals_str = ", ".join(signals[:5]) if signals else "no signals"

    # Format conversation history compactly
    conv_str = "\n".join([f"[{h['from']}]: {h.get('body', '')[:80]}" for h in conv_hist]) if conv_hist else "(no history)"

    system = f"""You are Vera, magicpin's WhatsApp marketing assistant for merchants.

## YOUR TASK
Compose ONE short WhatsApp message ({("to " + customer.get('identity', {}).get('name', 'customer')) if customer else "to the merchant"}).
Keep it punchy, specific, and end with ONE clear CTA.

## CATEGORY: {category.get("display_name", category.get("slug", ""))}
Tone: {voice.get("tone", "professional")} | {voice.get("register", "")}
Language: {"Hindi-English mix OK" if use_hindi else "English"}
Forbidden: {", ".join(voice.get("vocab_taboo", [])[:3])}

## MERCHANT
{identity.get("name", "")}, {identity.get("city", "")} | Owner: {owner_name} | Status: {sub.get("plan", "active")}
Performance: {perf.get('views', 0)} views, {perf.get('calls', 0)} calls (30d) | CTR: {perf.get('ctr', 0):.2%} vs peer avg {peer.get('avg_ctr', 0):.2%}
Signals: {signals_str}
Active offers: {active_offers_str}
Recent feedback: {theme_str}
Last message: {conv_str}

## TRIGGER
{trigger_kind.upper()} (urgency {urgency}/5) | Scope: {trigger_scope}
Context: {json.dumps(trigger_payload) if trigger_payload else "standard"}

## RULES
1. No URLs, no links
2. ONE CTA only, at the end
3. No preamble ("I hope you're well")
4. Use specifics: concrete numbers, dates, service names
5. Max 160 words
6. Service+price format: "Dental Cleaning @ ₹299" not "30% off"
7. No hallucination - only mention offers/digest items that exist
8. {("Send FROM merchant TO customer" if customer else "Send FROM Vera TO merchant")}

Return ONLY the message text. No explanation."""
    return system


def build_user_prompt(trigger_kind: str, merchant: dict, trigger: dict, customer: Optional[dict]) -> str:
    identity = merchant.get("identity", {})
    owner = identity.get("owner_first_name", "there")
    cust_name = customer.get("identity", {}).get("name", "valued customer") if customer else ""
    cust_info = f" to {cust_name}" if customer else ""

    return f"""Trigger: {trigger_kind}
Merchant: {owner}
{f"Customer: {cust_name}" if customer else ""}

Compose a compelling WhatsApp message now:"""


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

    body = await call_gemini(system, user, max_tokens=800)
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

    history_text = "\n".join([f"[{t['from']}]: {t['msg'][:100]}" for t in turn_history[-4:]])
    last_sent = sent_bodies.get(conversation_id, "")

    voice = category.get("voice", {}) if category else {}
    digest = category.get("digest", []) if category else []
    offers = category.get("offer_catalog", []) if category else []

    system = f"""You are Vera, magicpin's marketing assistant.
Replying to {owner}'s message in an ongoing WhatsApp conversation.

## MERCHANT: {identity.get("name", "")} ({cat_slug}) | Owner: {owner}
Language: {"Hindi-English code-mix" if use_hindi else "English"}

## CONVERSATION (last 4 turns)
{history_text}

## YOUR LAST MESSAGE (never repeat verbatim)
{last_sent[:150]}

## CONTEXT
Available offers: {", ".join([f"{o['title']}" for o in offers[:3]])}
Merchant signals: {", ".join(merchant.get("signals", [])[:3])}

## REPLY RULES
1. Max 120 words, punchy
2. One CTA at end
3. No preamble
4. If merchant said YES/committed → take action immediately, don't ask more questions
5. If asked question → answer briefly, then CTA
6. If confused → clarify simply
7. Never repeat your last message
8. No URLs
9. {"Use Hindi-English naturally" if use_hindi else "Use English"}

Return ONLY the reply message. No explanation."""

    user_msg = f"""Merchant replied: "{message}"

Write your reply now:"""

    body = await call_gemini(system, user_msg, max_tokens=600)
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
    if is_explicit_intent(message) and body.turn_number <= 6:
        merchant = get_ctx("merchant", body.merchant_id or "")
        if merchant:
            identity = merchant.get("identity", {})
            owner = identity.get("owner_first_name", "there")
            agg = merchant.get("customer_aggregate", {})
            customer_count = agg.get("high_risk_adult_count", agg.get("total_unique_ytd", 0))

            sent_bodies[conv_id] = f"action_confirmation_{conv_id}"
            return {
                "action": "send",
                "body": f"Perfect {owner}! 🎯 Starting now — I'll draft the message for your {customer_count or 'active'} customers. Reply CONFIRM to send, or CHANGE to adjust.",
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