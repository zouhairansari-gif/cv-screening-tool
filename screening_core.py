"""
Parsing and Claude API logic shared by the Streamlit app.
Kept separate from the UI code so it can be unit-tested on its own.
"""
import json
import os
import tempfile

import anthropic

MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# File parsing — same approach validated earlier: native extraction first,
# OCR fallback for scanned content, clear warnings rather than silent failure.
# ---------------------------------------------------------------------------

def extract_text_from_uploaded_file(uploaded_file):
    """
    Takes a Streamlit UploadedFile object, writes it to a temp path, and
    extracts plain text. Returns {text, used_ocr, warning}.
    """
    suffix = os.path.splitext(uploaded_file.name)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getbuffer())
        tmp_path = tmp.name
    try:
        return extract_text_from_file(tmp_path)
    finally:
        os.unlink(tmp_path)


def extract_text_from_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".txt":
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return {"text": f.read(), "used_ocr": False, "warning": None}

    elif ext == ".docx":
        import docx
        try:
            doc = docx.Document(filepath)
            parts = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            parts.append(cell.text)
            text = "\n".join(p for p in parts if p.strip())
            warning = None if text.strip() else "docx parsed but no text found"
            return {"text": text, "used_ocr": False, "warning": warning}
        except Exception as e:
            return {"text": "", "used_ocr": False, "warning": f"Failed to parse .docx: {e}"}

    elif ext == ".doc":
        return {"text": "", "used_ocr": False,
                "warning": "Legacy .doc isn't supported — please re-save as .docx and re-upload"}

    elif ext == ".pdf":
        import pdfplumber
        text_parts, needs_ocr_pages = [], []
        try:
            with pdfplumber.open(filepath) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        text_parts.append(page_text)
                    else:
                        needs_ocr_pages.append(i)
        except Exception as e:
            return {"text": "", "used_ocr": False, "warning": f"Failed to open PDF: {e}"}

        used_ocr = False
        if needs_ocr_pages:
            try:
                from pdf2image import convert_from_path
                import pytesseract
                images = convert_from_path(filepath)
                ocr_chunks = [pytesseract.image_to_string(images[i]) for i in needs_ocr_pages if i < len(images)]
                if ocr_chunks:
                    text_parts.append("\n".join(ocr_chunks))
                    used_ocr = True
            except Exception:
                pass

        full_text = "\n".join(text_parts)
        warning = None
        if needs_ocr_pages and not used_ocr:
            warning = "Some pages had no extractable text and OCR fallback failed"
        elif not full_text.strip():
            warning = "No text could be extracted from this PDF"
        return {"text": full_text, "used_ocr": used_ocr, "warning": warning}

    elif ext in (".png", ".jpg", ".jpeg"):
        from PIL import Image
        import pytesseract
        try:
            text = pytesseract.image_to_string(Image.open(filepath))
            warning = None if text.strip() else "OCR produced no text — check image quality"
            return {"text": text, "used_ocr": True, "warning": warning}
        except Exception as e:
            return {"text": "", "used_ocr": True, "warning": f"OCR failed: {e}"}

    else:
        return {"text": "", "used_ocr": False, "warning": f"Unsupported file type: {ext}"}


# ---------------------------------------------------------------------------
# Claude API calls
# ---------------------------------------------------------------------------

def get_client(api_key):
    return anthropic.Anthropic(api_key=api_key)


def _extract_json(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


def extract_criteria_from_jd(client, jd_text):
    prompt = f"""You are an experienced HR / talent acquisition partner. Given the job
description below, produce a weighted screening rubric with 5-6 criteria that matter
most for this role.

Job description:
{jd_text}

Return ONLY valid JSON in this exact shape, with weights summing to 100:
{{
  "role_title": "short role title inferred from the JD",
  "criteria": [
    {{"name": "...", "weight": 25, "description": "what strong evidence for this looks like"}}
  ]
}}"""
    response = client.messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
    return _extract_json(response.content[0].text)


def score_candidate(client, cv_text, criteria):
    prompt = f"""You are scoring a candidate CV against a weighted hiring rubric. Score
based only on evidence in the CV text below — never on assumptions beyond what's written.
If a criterion has no supporting evidence, say so explicitly and score it low rather
than guessing generously.

Rubric criteria:
{json.dumps(criteria, indent=2)}

Candidate CV text:
{cv_text[:12000]}

For EACH criterion, return a score 1 (no evidence) to 5 (strong evidence), plus a
one-sentence rationale citing specific CV evidence (or noting its absence).

Return ONLY valid JSON in this exact shape:
{{
  "candidate_name": "best-guess name extracted from the CV, or null if not found",
  "scores": [
    {{"criterion": "...", "score": 1, "rationale": "..."}}
  ]
}}"""
    response = client.messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
    return _extract_json(response.content[0].text)


def answer_question(client, question, candidates_bundle, role_title, history=None):
    system_prompt = f"""You are helping a hiring manager or recruiter review a candidate
shortlist for the role of {role_title}.

Answer using ONLY the candidate data provided below.
Rules:
- Always name the specific candidate(s) that support your answer.
- If the data doesn't contain enough information, say so explicitly rather than guessing.
- Never invent details not present in the CV text or scores.
- Keep answers concise unless a list is clearly needed.

Candidate data:
{json.dumps(candidates_bundle, indent=2)}"""

    messages = list(history or [])
    messages.append({"role": "user", "content": question})
    response = client.messages.create(model=MODEL, max_tokens=1000, system=system_prompt, messages=messages)
    answer = response.content[0].text
    messages.append({"role": "assistant", "content": answer})
    return answer, messages


def weighted_score(scores, criteria):
    total = sum(
        s["score"] * next(c["weight"] for c in criteria if c["name"] == s["criterion"])
        for s in scores
    ) / 100
    return round(total, 2)
