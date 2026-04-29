# Vera Bot — Submission

## Approach

This implementation uses **Google's Gemini 2.5 Flash** API as the language model for composing merchant engagement messages on WhatsApp.

### Architecture

1. **4-Context Composer** — Every message composition combines:
   - CategoryContext (voice, peer stats, digest items, offers)
   - MerchantContext (identity, performance, conversation history)
   - TriggerContext (event type, urgency, payload)
   - CustomerContext (optional, for customer-facing messages)

2. **Trigger-Kind Routing** — Different trigger kinds receive appropriate CTA types:
   - `recall_due`, `appointment_tomorrow`, `trial_followup` → `multi_choice_slot`
   - `renewal_due`, `gbp_unverified`, `winback_eligible` → `binary_yes_no`
   - All others → `open_ended`

3. **Auto-Reply Detection** — Regex patterns detect WhatsApp Business canned responses with 3-strike policy

4. **Intent Transition Detection** — Explicit commitment phrases route to action mode immediately

5. **Anti-Repetition Guard** — Tracks last sent message per conversation and prevents verbatim repeats

### Key Features

- **Specificity focus** — Messages anchor on concrete facts (numbers, dates, source citations)
- **Category-accurate voice** — Different tone for dentists (clinical/peer) vs salons (warm/practical)
- **No hallucination** — Only cites digest items present in context
- **URL stripping** — Removes URLs per Meta policy
- **Suppression tracking** — Prevents duplicate sends via suppression keys

### Model Choice

**Gemini 2.5 Flash** was chosen for:
- Fast inference (sub-30s per message)
- Cost-effective API pricing
- Strong instruction-following for prompt-based composition

### Trade-offs

- Prioritized specificity and category fit over maximal engagement frequency
- Single CTA per message over multi-option complexity
- Deterministic temperature (0) for consistency in evaluation

### What Would Help

- Access to real merchant engagement metrics (reply rates, conversion)
- Examples of actual auto-replies in Hindi/regional languages
- Category-specific voice guidelines (beyond dentists/salons)
- Real-time engagement feedback during development
