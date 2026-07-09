"""
Recruiter CV screening tool — Streamlit app.

Run locally:      streamlit run app.py
Deploy (free):     push to GitHub, then deploy on https://share.streamlit.io

Secrets required (set in .streamlit/secrets.toml locally, or in the
Streamlit Community Cloud "Secrets" panel when deployed):
    ANTHROPIC_API_KEY = "sk-ant-..."
    APP_PASSWORD = "choose-a-shared-password-for-your-recruiters"
"""
import json
import os

import streamlit as st

import screening_core as sc

st.set_page_config(page_title="Candidate screening", layout="wide")

DATA_FILE = os.path.join(os.path.dirname(__file__), "requisition_data.json")


# ---------------------------------------------------------------------------
# Simple shared-password gate — appropriate for a small, known team (2-5
# recruiters), not a substitute for real per-user login. See README for
# the upgrade path if this needs to scale beyond a small trusted group.
# ---------------------------------------------------------------------------
def check_password():
    if st.session_state.get("authenticated"):
        return True

    st.title("Candidate screening — sign in")
    pwd = st.text_input("Team password", type="password")
    if st.button("Sign in"):
        expected = st.secrets.get("APP_PASSWORD")
        if expected and pwd == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# ---------------------------------------------------------------------------
# Persistence: a simple JSON file shared by everyone using this deployed app,
# so one recruiter's scoring run is visible to the whole team without
# re-running it. Good enough for a small pilot; see README for the upgrade
# path (a real database) once this needs to survive redeploys reliably.
# ---------------------------------------------------------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"role_title": None, "jd_text": None, "criteria": [], "candidates": []}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
if not check_password():
    st.stop()

client = sc.get_client(st.secrets["ANTHROPIC_API_KEY"])
data = load_data()

st.title("Candidate screening")
st.caption("Shared by your team — anyone signed in sees the same shortlist and can ask questions about it.")

with st.expander("⚠️ Handling real candidate data — read before uploading", expanded=False):
    st.markdown(
        "- This tool never auto-rejects anyone — every score is a starting point for human review.\n"
        "- Scores and rationale are grounded in CV text only; verify anything surprising against the original file.\n"
        "- Before relying on this for real hiring decisions, spot-check scores against a bias audit and your "
        "company's data handling policy."
    )

tab_setup, tab_shortlist, tab_chat = st.tabs(["1. Setup", "2. Shortlist", "3. Ask questions"])

# --- Tab 1: Setup (JD + scoring) -------------------------------------------
with tab_setup:
    st.subheader("Job description")
    jd_file = st.file_uploader("Upload the JD (.pdf, .docx, .doc, .txt)", type=["pdf", "docx", "doc", "txt"])
    jd_pasted = st.text_area("...or paste the JD text directly", height=150)

    if st.button("Extract screening criteria"):
        jd_text = None
        if jd_file is not None:
            parsed = sc.extract_text_from_uploaded_file(jd_file)
            if parsed["warning"]:
                st.warning(parsed["warning"])
            jd_text = parsed["text"]
        elif jd_pasted.strip():
            jd_text = jd_pasted.strip()

        if not jd_text:
            st.error("Upload a JD file or paste JD text first.")
        else:
            with st.spinner("Extracting weighted criteria..."):
                rubric = sc.extract_criteria_from_jd(client, jd_text)
            data["role_title"] = rubric["role_title"]
            data["jd_text"] = jd_text
            data["criteria"] = rubric["criteria"]
            data["candidates"] = []  # new JD means prior scores no longer apply
            save_data(data)
            st.success(f"Criteria extracted for: {rubric['role_title']}")

    if data["criteria"]:
        st.markdown(f"**Current role:** {data['role_title']}")
        for c in data["criteria"]:
            st.markdown(f"- **{c['weight']}%** — {c['name']}: _{c['description']}_")

    st.divider()
    st.subheader("Candidates")

    if not data["criteria"]:
        st.info("Extract screening criteria above before uploading CVs.")
    else:
        cv_files = st.file_uploader(
            "Upload CV files (you can select multiple at once)",
            type=["pdf", "docx", "doc", "txt", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
        )

        if st.button("Score candidates") and cv_files:
            progress = st.progress(0.0, text="Starting...")
            new_candidates = []
            for i, uploaded in enumerate(cv_files):
                progress.progress((i) / len(cv_files), text=f"Processing {uploaded.name}...")
                parsed = sc.extract_text_from_uploaded_file(uploaded)

                if not parsed["text"].strip():
                    st.warning(f"Skipped {uploaded.name}: {parsed['warning']}")
                    continue
                if parsed["warning"]:
                    st.info(f"{uploaded.name}: {parsed['warning']}")

                scored = sc.score_candidate(client, parsed["text"], data["criteria"])
                total = sc.weighted_score(scored["scores"], data["criteria"])
                new_candidates.append({
                    "filename": uploaded.name,
                    "candidate_name": scored.get("candidate_name") or uploaded.name,
                    "cv_text": parsed["text"],
                    "weighted_score": total,
                    "scores": scored["scores"],
                    "used_ocr": parsed["used_ocr"],
                })

            progress.progress(1.0, text="Done.")
            data["candidates"] = sorted(new_candidates, key=lambda c: c["weighted_score"], reverse=True)
            save_data(data)
            st.success(f"Scored {len(new_candidates)} candidate(s). See the Shortlist tab.")

# --- Tab 2: Shortlist -------------------------------------------------------
with tab_shortlist:
    if not data["candidates"]:
        st.info("No candidates scored yet — go to the Setup tab.")
    else:
        st.subheader(f"Ranked shortlist — {data['role_title']}")
        for i, c in enumerate(data["candidates"]):
            ocr_tag = " · OCR used" if c["used_ocr"] else ""
            with st.expander(f"#{i+1}  {c['candidate_name']}  —  {c['weighted_score']}/5{ocr_tag}"):
                for s in c["scores"]:
                    st.markdown(f"**{s['score']}/5 — {s['criterion']}**  \n{s['rationale']}")

# --- Tab 3: Chat -------------------------------------------------------------
with tab_chat:
    if not data["candidates"]:
        st.info("No candidates scored yet — go to the Setup tab.")
    else:
        st.subheader("Ask about this shortlist")
        st.caption("Answers are grounded only in the scored candidate data — the assistant will say so if evidence isn't there.")

        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []

        for msg in st.session_state.chat_history:
            with st.chat_message("user" if msg["role"] == "user" else "assistant"):
                st.write(msg["content"])

        question = st.chat_input("e.g. Who has distributor P&L experience?")
        if question:
            with st.chat_message("user"):
                st.write(question)

            bundle = [
                {"name": c["candidate_name"], "cv_text": c["cv_text"],
                 "weighted_score": c["weighted_score"], "criterion_scores": c["scores"]}
                for c in data["candidates"]
            ]
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    answer, updated_history = sc.answer_question(
                        client, question, bundle, data["role_title"],
                        history=st.session_state.chat_history,
                    )
                st.write(answer)
            st.session_state.chat_history = updated_history
