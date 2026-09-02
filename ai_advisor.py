import json
import os
from google import genai
from google.genai import types

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


def get_gemini_api_key():
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key

    try:
        import streamlit as st
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return None


def _get_client():
    api_key = get_gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return genai.Client(api_key=api_key)


RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "company_name": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["Apply", "Consider", "Avoid", "Insufficient data"]},
                    "investment_score": {"type": "integer"},
                    "allotment_score": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
                    "reason": {"type": "string"},
                    "anchor_signal": {"type": "string"},
                    "valuation_signal": {"type": "string"},
                    "financial_signal": {"type": "string"},
                    "demand_signal": {"type": "string"},
                    "key_risks": {"type": "array", "items": {"type": "string"}},
                    "research_notes": {"type": "string"}
                },
                "required": [
                    "source_id", "company_name", "verdict", "investment_score",
                    "allotment_score", "confidence", "reason", "anchor_signal",
                    "valuation_signal", "financial_signal", "demand_signal",
                    "key_risks", "research_notes"
                ]
            }
        }
    },
    "required": ["summary", "recommendations"]
}


def analyze_ipos(ipos, objective="Balanced",
                 risk_tolerance="Moderate",
                 holding_horizon="Listing day",
                 horizon=None):
    # Backward compatibility with the app UI, which passes `horizon=`.
    if horizon is not None:
        holding_horizon = horizon

    """Analyze the supplied IPO dataset using Gemini.

    The Gemini client is created as a local variable and explicitly closed
    after the request. Do not use `_get_client().models...` directly because
    the temporary client can be garbage-collected before the HTTP request
    completes.
    """
    if not ipos:
        raise ValueError("No IPO data was supplied for analysis.")

    payload = json.dumps(ipos, ensure_ascii=False, default=str)

    prompt = f"""
You are the decision engine for an Indian IPO intelligence application.

User objective: {objective}
Risk tolerance: {risk_tolerance}
Holding horizon: {holding_horizon}

Use the supplied IPO data as the primary source of truth. You also have access
 to Google Search for targeted verification of anchor-investor reputation and
recent institutional information.

IMPORTANT CORRECTION ABOUT SUBSCRIPTION TIMING:
Do NOT penalize an IPO simply because QIB subscription is low early in the
bidding period. QIB and NII demand can be heavily back-loaded and may rise
sharply on the final day. Treat the current subscription as a time-stamped
snapshot, not a final demand signal. Use the close date and subscription
updated timestamp when interpreting demand.

ANCHOR INVESTOR ANALYSIS:
The anchor book is a pre-opening institutional signal and should be considered
separately from live QIB subscription. Use the IPO Ji anchor disclosure supplied
for the issue. Evaluate:
- quality and credibility of named anchor institutions/fund houses;
- breadth/diversity of the anchor book;
- concentration in a few investors versus broad institutional participation;
- domestic mutual fund participation;
- recognised long-term institutional investors versus less informative entities;
- whether reputable institutions have a meaningful allocation;
- any recent, verifiable information about the investor that materially changes
  the signal.
Do NOT treat the presence of a famous investor as proof that the IPO is good.
Anchor participation is one signal, not a guarantee of performance.

OTHER FACTORS TO WEIGH:
- valuation: P/E, P/B and market cap when available;
- financial quality: revenue/profitability proxies, ROE, ROCE, RoNW, PAT margin,
  debt/equity and promoter holding when available;
- issue structure: fresh issue versus OFS;
- GMP and GMP trend, but GMP is unofficial and not guaranteed;
- subscription by category, interpreted with timing and close date;
- issue size, price band and lot size;
- stated strengths and risks from the IPO detail page;
- sector/business quality only when supported by supplied data or targeted web
  verification;
- conflicts, concentration, or unusual anchor-book composition if verifiable.

SCORING:
- Investment Score: 0-100, attractiveness of the business/valuation/issue as
  an investment for the stated horizon.
- Allotment Score: 0-100, attractiveness from the probability/strategy of
  receiving an allotment. Do not confuse this with investment quality.
- Confidence: reflect how complete and reliable the evidence is.
- Apply means the evidence is sufficiently attractive to consider applying,
  not that returns or allotment are guaranteed.

WEB RESEARCH RULES:
- Search only when it materially improves the anchor-investor or other factual
  assessment.
- Prefer IPO Ji, SEBI/exchange disclosures, AMC/institutional sources and
  reputable financial news.
- Do not rely on anonymous social posts for investor reputation.
- Do not fabricate a track record. If reputation evidence is weak, say so.
- Keep research_notes concise and mention what was verified or unavailable.

Return one recommendation for every IPO supplied.

IPO DATA:
{payload}
"""

    client = _get_client()
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RECOMMENDATION_SCHEMA,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini returned an empty response.")

        return json.loads(response.text)
    finally:
        client.close()


def chat_with_advisor(message, analysis=None, ipos=None,
                      objective="Balanced",
                      risk_tolerance="Moderate",
                      holding_horizon="Listing day"):
    """Answer a follow-up question using the current analysis and IPO data."""

    context = {
        "objective": objective,
        "risk_tolerance": risk_tolerance,
        "holding_horizon": holding_horizon,
        "analysis": analysis or {},
        "ipos": ipos or [],
    }

    prompt = f"""
You are the IPO Advisor inside an Indian IPO intelligence application.

Answer the user's question using ONLY the supplied application data and
previous analysis.

Rules:
- Do not invent facts.
- If the data does not contain the answer, say that it is not available.
- Keep investment attractiveness and allotment probability separate.
- GMP is unofficial and not guaranteed.
- Do not promise returns or allotment.
- Be concise and practical.
- If comparing IPOs, explain the key evidence behind the comparison.

CURRENT CONTEXT:
{json.dumps(context, ensure_ascii=False, default=str)}

USER QUESTION:
{message}
"""

    client = _get_client()
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini returned an empty response.")

        return response.text
    finally:
        client.close()
