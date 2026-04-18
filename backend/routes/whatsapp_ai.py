"""
WhatsApp AI Reply Engine — ASR Enterprises
Provides intelligent AI-generated replies for unhandled WhatsApp messages.

Priority chain:
  1. OpenAI GPT-4o-mini  (if OPENAI_API_KEY env var is set)
  2. Google Gemini Flash  (if GEMINI_API_KEY env var is set)
  3. Returns None → caller falls back to no-reply (graceful degradation)

Designed to be called only when keyword/menu matching fails, so it handles
mid-conversation free-text messages like:
  "Mere ghar ke liye solar lagwana hai, budget 80,000 hai"
  "Documentation kya chahiye subsidy ke liye?"
  "Kya 3 kW panel chhoti chhat pe lag sakta hai?"
"""

import os
import time
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt — WhatsApp-optimised (shorter, no markdown tables)
# ---------------------------------------------------------------------------
WHATSAPP_AI_SYSTEM_PROMPT = """You are "Solar Mitra", the official WhatsApp AI assistant for ASR Enterprises — Patna's top solar rooftop company.

ROLE: Help customers understand solar options and capture their lead for a FREE site visit.

LANGUAGE RULE: Always reply in friendly Hinglish (mix of Hindi + English, like how people actually chat in Bihar/UP). Never use formal Hindi. Keep it natural, warm, and conversational.

WHATSAPP STYLE RULES:
- Max 4 short lines per reply. No lengthy paragraphs.
- No markdown (no *, no #, no bullet tables). Just plain chat.
- Use one relevant emoji per reply (not more).
- End every reply with exactly ONE follow-up question.

BUSINESS FACTS (use only these — do not invent):
- Company: ASR Enterprises, Patna, Bihar
- MNRE Registered + PM Surya Ghar Yojana Partner
- Phone: 9296389097 | Web: asrenterprises.in
- Prices: 2kW=₹1.5L, 3kW=₹2.1L, 5kW=₹3.5L (includes installation + 5yr free maintenance)
- Govt Subsidy (residential): 2kW=₹60,000 | 3kW=₹78,000 | 5kW+=₹78,000
- EMI from ₹2,000/month | Bank loans at 6–9%
- Free site visit available | Installation: 2–3 days

CONVERSATION GOALS:
1. Answer their question in 1–2 lines
2. Move them toward: telling their bill amount → district → FREE site visit booking
3. If they ask for a human agent / say "call karo" / "baat karni hai" → say you're connecting them and ask for their name + district

DO NOT:
- Discuss non-solar topics
- Invent prices, subsidies, or technical specs not listed above
- Send long walls of text"""

# ---------------------------------------------------------------------------
# In-memory rate limiting (per phone, max AI calls/hour)
# ---------------------------------------------------------------------------
_rate_limit_store: Dict[str, List[float]] = {}
MAX_AI_CALLS_PER_HOUR_PER_PHONE = 12


def _within_rate_limit(phone: str) -> bool:
    """True if this phone number hasn't exceeded AI call limits."""
    now = time.time()
    cutoff = now - 3600  # 1 hour window
    history = _rate_limit_store.get(phone, [])
    history = [t for t in history if t > cutoff]
    if len(history) >= MAX_AI_CALLS_PER_HOUR_PER_PHONE:
        return False
    history.append(now)
    _rate_limit_store[phone] = history
    return True


# ---------------------------------------------------------------------------
# Spam / noise filter
# ---------------------------------------------------------------------------
_IGNORE_MESSAGES = {
    "ok", "okay", "k", "kk", "hmm", "hm", "ha", "haan", "nahi", "no",
    "yes", "ya", "yep", "nope", "👍", "👎", "🙏", "❤️", "😊", "thanks",
    "thank you", "dhanyawad", "shukriya", "bye", "bye bye", "ok bhai",
    "theek hai", "accha", "thik hai", "ji", "ji haan", "ji nahi",
}

_IGNORE_PATTERNS = [
    r"^[👍👎🙏❤️😊😁😄🤝✅❌]+$",  # pure emoji
    r"^\s*$",                          # empty / whitespace
]


def is_noise_message(content: str) -> bool:
    """Return True if the message is too trivial to warrant an AI reply."""
    import re
    c = content.strip().lower()
    if len(c) <= 2:
        return True
    if c in _IGNORE_MESSAGES:
        return True
    for pat in _IGNORE_PATTERNS:
        if re.match(pat, content.strip()):
            return True
    return False


# ---------------------------------------------------------------------------
# Conversation history (from DB)
# ---------------------------------------------------------------------------
async def _get_conversation_history(phone: str, db, limit: int = 8) -> List[Dict]:
    """
    Fetch recent messages for context.  Returns OpenAI-format list:
    [{"role": "user"|"assistant", "content": "..."}]
    """
    try:
        phone_suffix = phone[-10:] if len(phone) >= 10 else phone
        docs = await db.whatsapp_messages.find(
            {
                "phone": {"$regex": phone_suffix},
                "content": {"$exists": True, "$ne": ""},
            },
            {"_id": 0, "direction": 1, "content": 1, "created_at": 1},
        ).sort("created_at", -1).limit(limit).to_list(limit)

        history = []
        for doc in reversed(docs):  # oldest first
            role = "user" if doc.get("direction") == "incoming" else "assistant"
            text = (doc.get("content") or "").strip()
            if text:
                history.append({"role": role, "content": text})
        return history
    except Exception as exc:
        logger.warning(f"[WhatsApp AI] Could not load history for {phone}: {exc}")
        return []


# ---------------------------------------------------------------------------
# OpenAI caller
# ---------------------------------------------------------------------------
async def _call_openai(messages: List[Dict], api_key: str) -> Optional[str]:
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": messages,
                "max_tokens": 220,
                "temperature": 0.65,
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            return text if text else None
        logger.warning(f"[WhatsApp AI] OpenAI HTTP {resp.status_code}: {resp.text[:200]}")
        return None


# ---------------------------------------------------------------------------
# Gemini caller
# ---------------------------------------------------------------------------
async def _call_gemini(messages: List[Dict], api_key: str) -> Optional[str]:
    import httpx

    system_text = ""
    chat_msgs = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        else:
            role = "user" if m["role"] == "user" else "model"
            chat_msgs.append({"role": role, "parts": [{"text": m["content"]}]})

    if not chat_msgs:
        return None

    payload: Dict = {
        "contents": chat_msgs,
        "generationConfig": {"maxOutputTokens": 220, "temperature": 0.65},
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    text = parts[0].get("text", "").strip()
                    return text if text else None
        logger.warning(f"[WhatsApp AI] Gemini HTTP {resp.status_code}: {resp.text[:200]}")
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
async def generate_ai_reply(phone: str, message: str, db) -> Optional[str]:
    """
    Generate an AI reply for a WhatsApp message.

    Returns the reply string, or None if:
    - Message is trivial noise (ok, 👍, etc.)
    - Rate limit exceeded for this phone
    - No AI provider is configured
    - AI call fails
    """
    # 1. Noise filter
    if is_noise_message(message):
        logger.info(f"[WhatsApp AI] Noise message from {phone}, skipping AI")
        return None

    # 2. Rate limit
    if not _within_rate_limit(phone):
        logger.warning(f"[WhatsApp AI] Rate limit hit for {phone}")
        return None

    # 3. Build message list with history
    history = await _get_conversation_history(phone, db)
    messages: List[Dict] = [{"role": "system", "content": WHATSAPP_AI_SYSTEM_PROMPT}]
    messages.extend(history)
    # Append current message if not already the last entry
    if not history or history[-1].get("content") != message:
        messages.append({"role": "user", "content": message})

    # 4. Try OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key and openai_key not in ("your-key", "sk-xxx"):
        try:
            reply = await _call_openai(messages, openai_key)
            if reply:
                logger.info(f"[WhatsApp AI] OpenAI reply OK for {phone}")
                return reply
        except Exception as exc:
            logger.warning(f"[WhatsApp AI] OpenAI error: {exc}")

    # 5. Try Gemini
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key and gemini_key not in ("your-key", "AIzaxxx"):
        try:
            reply = await _call_gemini(messages, gemini_key)
            if reply:
                logger.info(f"[WhatsApp AI] Gemini reply OK for {phone}")
                return reply
        except Exception as exc:
            logger.warning(f"[WhatsApp AI] Gemini error: {exc}")

    logger.info(f"[WhatsApp AI] No AI provider available, skipping reply for {phone}")
    return None


# ---------------------------------------------------------------------------
# Utility: check if any AI provider is configured (for admin status display)
# ---------------------------------------------------------------------------
def ai_provider_configured() -> Dict:
    """Return which AI providers are configured."""
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    return {
        "openai": bool(openai_key and openai_key not in ("your-key", "sk-xxx")),
        "gemini": bool(gemini_key and gemini_key not in ("your-key", "AIzaxxx")),
        "any_configured": bool(
            (openai_key and openai_key not in ("your-key", "sk-xxx"))
            or (gemini_key and gemini_key not in ("your-key", "AIzaxxx"))
        ),
    }
