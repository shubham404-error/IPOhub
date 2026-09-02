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


def _friendly_gemini_error(exc):
    """Turn common Gemini API failures into useful app messages."""
    text = str(exc)
    lowered = text.lower()

    if "429" in lowered or "resource_exhausted" in lowered or "rate limit" in lowered:
        return (
            "Gemini free-tier rate limit reached. "
            "Please wait a little and try again. The app will not retry automatically."
        )

    if "quota" in lowered:
        return (
            "Gemini free-tier quota has been reached. "
            "Please try again after the quota resets."
        )

    return f"Gemini request failed: {text}"


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
                    "verdict": {
                        "type": "string",
                        "enum": ["Apply", "Consider", "Avoid", "Insufficient data"],
                    },
                    "investment_score": {"type": "integer"},
                    "allotment_score": {"type": "integer"},
                    "confidence": {
                        "type": "string",
                        "enum": ["High", "Medium", "Low"],
                    },
                    "reason": {"type": "string"},
                    "anchor_signal": {"type": "string"},
                    "valuation_signal": {"type": "string"},
                    "financial_signal": {"type": "string"},
                    "demand_signal": {"type": "string"},
                    "key_risks": {"type": "array", "items": {"type": "string"}},
                    "research_notes": {"type": "string"},
                },
                "required": [
                    "source_id",
                    "company_name",
                    "verdict",
                    "investment_score",
                    "allotment_score",
                    "confidence",
                    "reason",
                    "anchor_signal",
                    "valuation_signal",
                    "financial_signal",
                    "demand_signal",
                    "key_risks",
                    "research_notes",
                ],
            },
        },
    },
    "required": ["summary", "recommendations"],
}


def _generate(client, prompt, structured=False):
    """
    Make exactly one Gemini API request.

    IMPORTANT:
    This project is designed for the Gemini free tier, so there is deliberately
    no Google Search grounding and no automatic retry. A retry after a 429 would
    only consume more quota without helping.
    """
    kwargs = {}

    if structured:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = RECOMMENDATION_SCHEMA

    # Do not pass GoogleSearch(), tools, temperature, top_p or top_k.
    # The free-tier app should use only the model + our supplied IPO data.
    try:
        return client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(**kwargs),
        )
    except Exception as exc:
        raise RuntimeError(_friendly_gemini_error(exc)) from exc


def analyze_ipos(
    ipos,
    objective="Balanced",
    risk_tolerance="Moderate",
    holding_horizon="Listing day",
    horizon=None,
):
    if horizon is not None:
        holding_horizon = horizon

    if not ipos:
        raise ValueError("No IPO data was supplied for analysis.")

    payload = json.dumps(ipos, ensure_ascii=False, default=str)

    prompt = f"""
You are the decision engine for an Indian IPO intelligence application.

User objective: {objective}
Risk tolerance: {risk_tolerance}
Holding horizon: {holding_horizon}

IMPORTANT DATA RULE:
Use ONLY the supplied application IPO data. Do not use web search,
external browsing, outside facts, or assumed investor track records.
If a fact is not present in the supplied data, say that it is unavailable.

IMPORTANT SUBSCRIPTION TIMING:
- Do NOT penalize an IPO simply because QIB subscription is low early in
  the bidding period.
- QIB and NII demand can be heavily back-loaded and may rise sharply on
  the final day.
- Treat subscription as a time-stamped snapshot, not final demand.

ANCHOR INVESTOR ANALYSIS:
- Use the supplied anchor amount, count, price, mutual-fund percentage,
  summary and named investors.
- Evaluate breadth, diversity and concentration from the supplied data.
- Consider domestic mutual fund participation when supplied.
- Do not treat a famous investor name as proof that an IPO is good.
- Do not invent or infer an investor's historical performance.

OTHER FACTORS:
- valuation: P/E, P/B and market cap when available;
- financial quality: ROE, ROCE, RoNW, PAT margin, debt/equity and
  promoter holding when available;
- fresh issue versus OFS;
- GMP and GMP trend, clearly marked unofficial;
- category subscription with timing;
- issue size, price band and lot size;
- supplied strengths and risks;
- business/sector quality only when supported by supplied application data.

SCORING:
- Investment Score: 0-100 for business/valuation/issue attractiveness.
- Allotment Score: 0-100 for allotment attractiveness. Keep it separate
  from investment quality.
- Confidence reflects evidence completeness and reliability.
- Apply means attractive enough to consider applying, not guaranteed
  returns or allotment.

RESEARCH NOTES:
Because this is a free-tier, database-only AI system, research_notes must
describe only what can be verified from the supplied application data.
Do not claim that anything was checked online.

Return one recommendation for every IPO supplied.

IPO DATA:
{payload}
"""

    client = _get_client()
    try:
        response = _generate(client, prompt, structured=True)
        if not response.text:
            raise RuntimeError("Gemini returned an empty response.")
        return json.loads(response.text)
    finally:
        client.close()


def chat_with_advisor(
    message,
    analysis=None,
    ipos=None,
    objective="Balanced",
    risk_tolerance="Moderate",
    holding_horizon="Listing day",
    **kwargs,
):
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
- Do not use web search or external information.
- Do not invent facts.
- If the supplied data does not contain the answer, say it is not available.
- Keep investment attractiveness and allotment probability separate.
- GMP is unofficial and not guaranteed.
- Do not promise returns or allotment.
- Be concise and practical.
- If comparing IPOs, explain the key evidence from the supplied data.

CURRENT CONTEXT:
{json.dumps(context, ensure_ascii=False, default=str)}

USER QUESTION:
{message}
"""

    client = _get_client()
    try:
        response = _generate(client, prompt, structured=False)
        if not response.text:
            raise RuntimeError("Gemini returned an empty response.")
        return response.text
    finally:
        client.close()
