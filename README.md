# Vera Bot - MagicPin AI Challenge Submission

A FastAPI-based engagement bot that composes personalized WhatsApp messages for merchants using Google's Gemini 2.5 Flash API.

## 📋 Overview

This bot implements a **4-context composer** for generating merchant engagement messages on WhatsApp:
- **Category Context**: Voice, peer stats, digest items, offers
- **Merchant Context**: Identity, performance, conversation history
- **Trigger Context**: Event type, urgency, payload
- **Customer Context**: Customer-facing message context (optional)

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- Google Gemini API Key (free tier available)

### Installation

```bash
# Clone or download this repository
cd magicpin-vera-bot

# Install dependencies
pip install -r requirements.txt
```

### Setup

1. **Get a free Gemini API Key:**
   - Go to https://aistudio.google.com/app/apikey
   - Click "Create API Key"
   - Copy your key

2. **Set environment variable:**
   ```bash
   export GEMINI_API_KEY="your_api_key_here"
   ```

3. **Generate the dataset:**
   ```bash
   python dataset/generate_dataset.py
   ```

4. **Run the bot:**
   ```bash
   python bot.py
   ```
   - Bot runs on `http://localhost:8000`
   - API docs at `http://localhost:8000/docs`

### Generate Submission

To generate the 30 test case responses:

```bash
export GEMINI_API_KEY="your_api_key_here"
python generate_submission.py
```

This creates `submission.jsonl` with bot responses for all test cases.

---

## 📁 Project Structure

```
magicpin-vera-bot/
├── bot.py                          # Main FastAPI bot
├── generate_submission.py           # Generate submission.jsonl
├── submission.jsonl                 # 30 test responses (output)
├── README.md                        # This file
├── README_SUBMISSION.md             # Approach documentation
├── requirements.txt                 # Python dependencies
├── dataset/
│   ├── generate_dataset.py         # Dataset generator
│   ├── categories/                 # Category contexts
│   ├── merchants_seed.json         # Merchant templates
│   ├── customers_seed.json         # Customer templates
│   ├── triggers_seed.json          # Trigger templates
│   └── expanded/                   # Generated test data
│       ├── merchants/
│       ├── customers/
│       ├── categories/
│       ├── triggers/
│       └── test_pairs.json
└── .env                            # (optional) Environment config
```

---

## 🔧 API Endpoints

### `/health` - Health Check
```bash
curl http://localhost:8000/health
```

### `/context` - Load Context
```bash
curl -X POST http://localhost:8000/context \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "merchant",
    "context_id": "m_001_drmeera_dentist_delhi",
    "version": 1,
    "payload": {...}
  }'
```

### `/tick` - Get Available Triggers
```bash
curl -X POST http://localhost:8000/tick \
  -H "Content-Type: application/json" \
  -d '{"now": "2026-04-29T12:00:00Z"}'
```

### `/reply` - Handle Customer Reply
```bash
curl -X POST http://localhost:8000/reply \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv_123",
    "from_role": "customer",
    "message": "Yes, I want to book",
    "received_at": "2026-04-29T12:00:00Z",
    "turn_number": 2
  }'
```

---

## 📊 Submission File Format

`submission.jsonl` contains 30 test cases (one per line):

```json
{
  "test_id": "T01",
  "body": "Hi Aditya! Just a friendly reminder about your appointment tomorrow at Karim's Salon.",
  "cta": "multi_choice_slot",
  "send_as": "merchant_on_behalf",
  "suppression_key": "appointment_tomorrow:m_019_karim_salon_lucknow",
  "rationale": "Trigger: appointment_tomorrow (urgency 3). Category: salons. Merchant: m_019_karim_salon_lucknow. Customer-scoped, sending on behalf of merchant."
}
```

### Fields:
- **test_id**: Test case identifier (T01-T30)
- **body**: Composed message text (under 1600 chars)
- **cta**: Call-to-action type (`multi_choice_slot`, `binary_yes_no`, `open_ended`)
- **send_as**: Sender role (`merchant`, `vera`, `merchant_on_behalf`)
- **suppression_key**: Deduplication key
- **rationale**: Why this message was composed

---

## 🤖 How the Bot Works

### 1. Context Loading
Loads merchant, customer, category, and trigger data before composition.

### 2. System Prompt Building
Creates a detailed system prompt with:
- Category voice guidelines
- Merchant performance metrics
- Relevant digest items
- Peer benchmarks

### 3. User Prompt Building
Specifies:
- Trigger kind
- Required message length
- CTA type expectations
- Any customer context

### 4. Gemini API Call
Sends to Google Gemini 2.5 Flash for composition.

### 5. Post-Processing
- Strips URLs (Meta policy compliance)
- Removes malformed text
- Sets appropriate CTA type based on trigger kind

---

## 🛡️ Safety Features

✅ **Auto-reply detection** - Ignores canned WhatsApp responses
✅ **Hostile message detection** - Avoids opt-out signals
✅ **Anti-repetition** - Prevents duplicate message sends
✅ **Suppression keys** - Tracks already-sent triggers
✅ **Intent detection** - Recognizes commitment phrases

---

## 📝 Configuration

### Environment Variables
```bash
GEMINI_API_KEY          # Required: Google Gemini API key
GEMINI_API_URL          # Optional: Custom API endpoint
```

### Optional: .env File
Create `.env` file in project root:
```
GEMINI_API_KEY=your_key_here
```

---

## 🧪 Testing

Run the submission generator to test against 30 test cases:

```bash
python generate_submission.py
```

Expected output:
```
[OK] T01: active_planning_intent
[OK] T03: appointment_tomorrow
[OK] T05: category_seasonal
...
[OK] Generated 30 responses
[OK] Saved to: submission.jsonl
```

---

## 📚 Documentation

- **Approach Document**: See `README_SUBMISSION.md` for detailed explanation of:
  - Architecture decisions
  - Trade-offs made
  - Model choice justification
  - What would help with real data

---

## 🔗 Links

- **Gemini API**: https://aistudio.google.com/app/apikey
- **FastAPI Docs**: http://localhost:8000/docs
- **MagicPin**: https://www.magicpin.in/

---

## 📧 Support

For questions about:
- **Setup**: Check Quick Start section
- **API**: Visit FastAPI docs at `/docs` endpoint
- **Submission**: See `README_SUBMISSION.md`

---

## ✅ Submission Checklist

- [ ] Python 3.10+ installed
- [ ] `pip install -r requirements.txt` completed
- [ ] Gemini API key generated and set
- [ ] `python dataset/generate_dataset.py` ran successfully
- [ ] `python bot.py` starts without errors
- [ ] `python generate_submission.py` creates `submission.jsonl`
- [ ] All 30 test responses in `submission.jsonl`
- [ ] `submission.jsonl` uploaded to repository

---

**Last Updated**: April 29, 2026
**Status**: Ready for submission
