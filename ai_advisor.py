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
        "summary": {
            "type": "string"
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "company_name": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": [
                            "Apply",
                            "Consider",
                            "Avoid",
                            "Insufficient data"
                        ]
                    },
                    "investment_score": {
                        "type": "integer"
                    },
                    "allotment_score": {
                        "type": "integer"
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["High", "Medium", "Low"]
                    },
                    "reason": {"type": "string"},
                    "key_risks": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": [
                    "source_id",
                    "company_name",
                    "verdict",
                    "investment_score",
                    "allotment_score",
                    "confidence",
                    "reason",
                    "key_risks"
                ]
            }
        }
    },
    "required": ["summary", "recommendations"]
}


def analyze_ipos(ipos, objective="Balanced",
                 risk_tolerance="Moderate",
                 holding_horizon="Listing day"):
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
You are an IPO decision-support analyst.

User objective: {objective}
Risk tolerance: {risk_tolerance}
Holding horizon: {holding_horizon}

Analyze ONLY the structured IPO data supplied below.

Rules:
1. Never invent missing financials, valuation, promoters, subscription,
   GMP, dates, or any other fact.
2. Treat missing/null values as unknown.
3. Separate investment attractiveness from allotment attractiveness.
4. Investment Score is 0-100 and measures attractiveness as an investment.
5. Allotment Score is 0-100 and measures attractiveness from the perspective
   of getting an allotment.
6. GMP is unofficial and must not be treated as guaranteed listing gain.
7. High subscription does not automatically mean a good investment.
8. If the available evidence is inadequate, use "Insufficient data".
9. "Apply" means the available evidence is sufficiently attractive to consider
   applying. It is not a guarantee of allotment or returns.
10. Keep reasons concise and evidence-based.
11. Do not manufacture valuation or financial-quality conclusions when those
    fields are not present.
12. Return one recommendation for every IPO supplied.

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
                temperature=0.2,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini returned an empty response.")

        return response.text
    finally:
        client.close()
