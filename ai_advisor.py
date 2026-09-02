import json
import os

from google import genai
from google.genai import types


MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite",
)


def get_gemini_api_key():
    key = os.getenv("GEMINI_API_KEY")

    if key:
        return key

    try:
        import streamlit as st
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return None


def _client():
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
                    "verdict": {
                        "type": "string",
                        "enum": [
                            "Apply",
                            "Consider",
                            "Avoid",
                            "Insufficient data",
                        ],
                    },
                    "investment_score": {
                        "type": "integer",
                    },
                    "allotment_score": {
                        "type": "integer",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["High", "Medium", "Low"],
                    },
                    "reason": {"type": "string"},
                    "key_risks": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "source_id",
                    "company_name",
                    "verdict",
                    "investment_score",
                    "allotment_score",
                    "confidence",
                    "reason",
                    "key_risks",
                ],
            },
        },
    },
    "required": ["summary", "recommendations"],
}


def _base_instruction():
    return """
You are the IPO Intelligence decision-support analyst.

Evaluate Indian IPOs using ONLY the structured data supplied by the app.

Rules:
- Never invent missing facts.
- Missing data means unknown.
- Separate investment attractiveness from allotment attractiveness.
- GMP is unofficial and is not a guaranteed listing price or return.
- High subscription does not automatically mean a good investment.
- If evidence is insufficient, use "Insufficient data".
- "Apply" means the IPO currently looks attractive enough to consider applying
  based on available evidence. It is not a guarantee of profit or allotment.
- Scores are 0-100.
- Do not fabricate financial quality or valuation when the supplied dataset
  does not contain those fields.
- Keep reasons concise and evidence-based.
"""


def analyze_ipos(
    ipo_rows,
    objective="Balanced",
    risk_tolerance="Moderate",
    horizon="Listing day",
):
    prompt = f"""
{_base_instruction()}

User objective: {objective}
Risk tolerance: {risk_tolerance}
Holding horizon: {horizon}

Analyze every IPO supplied below and rank the most relevant ones first.

Current IPO dataset:
{json.dumps(ipo_rows, ensure_ascii=False, default=str)}
"""

    response = _client().models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=RECOMMENDATION_SCHEMA,
            temperature=0.2,
        ),
    )

    return json.loads(response.text)


def chat_with_advisor(
    ipo_rows,
    previous_analysis,
    question,
):
    prompt = f"""
{_base_instruction()}

Current IPO dataset:
{json.dumps(ipo_rows, ensure_ascii=False, default=str)}

Previous analysis:
{json.dumps(previous_analysis, ensure_ascii=False, default=str)}

User question:
{question}

Answer directly. Compare relevant IPOs when asked.
Clearly distinguish supplied facts from interpretation.
"""

    response = _client().models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
        ),
    )

    return response.text
