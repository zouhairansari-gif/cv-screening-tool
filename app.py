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

tab_setup, tab_shortlist, tab_interview, tab_chat = st.tabs(
    ["1. Setup", "2. Shortlist", "3. Interview guide", "4. Ask questions"]
)

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

        if data.get("hard_filters"):
            st.markdown("**Eligibility requirements (pass/fail, not scored):**")
            for f in data["hard_filters"]:
                st.markdown(f"- **{f['name']}**: {f['requirement']}")

        st.divider()
        st.subheader("Add your own requirements (optional)")
        st.caption(
            "Mark something 'Hard requirement' if it should be a pass/fail eligibility check — "
            "it will never be silently used to rank or exclude candidates by nationality; only explicit "
            "visa/residency statements in the CV are used for work-authorization checks. Leave it unchecked "
            "for something that should just raise or lower a candidate's score."
        )

        req_fields = [
            ("Industry experience", "e.g. FMCG / consumer goods only"),
            ("Regional experience", "e.g. GCC or MENA market exposure"),
            ("Language requirement", "e.g. fluent Arabic and English"),
            ("Work authorization", "e.g. valid UAE work visa or visa-transferable"),
        ]
        requirements = []
        for label, placeholder in req_fields:
            col1, col2 = st.columns([3, 1])
            with col1:
                val = st.text_input(label, placeholder=placeholder, key=f"req_{label}")
            with col2:
                is_hard = st.checkbox("Hard requirement", key=f"hard_{label}")
            if val.strip():
                requirements.append({"category": label, "requirement": val.strip(), "hard_requirement": is_hard})

        if st.button("Apply requirements to rubric"):
            if not requirements:
                st.warning("No requirements entered — nothing to apply.")
            else:
                with st.spinner("Merging your requirements into the rubric..."):
                    merged = sc.merge_requirements_into_rubric(
                        client, data["role_title"], data["criteria"], requirements
                    )
                data["criteria"] = merged["criteria"]
                data["hard_filters"] = merged["hard_filters"]
                data["candidates"] = []  # rubric changed — old scores no longer apply
                save_data(data)
                st.success("Requirements applied. Re-score candidates below to use the updated rubric.")
                st.rerun()

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

                scored = sc.score_candidate(
                    client, parsed["text"], data["criteria"], data.get("hard_filters", [])
                )
                total = sc.weighted_score(scored["scores"], data["criteria"])
                new_candidates.append({
                    "filename": uploaded.name,
                    "candidate_name": scored.get("candidate_name") or uploaded.name,
                    "cv_text": parsed["text"],
                    "weighted_score": total,
                    "scores": scored["scores"],
                    "filter_results": scored.get("filter_results", []),
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
        st.caption(
            "Eligibility flags are shown, not hidden — a candidate who fails a hard "
            "requirement still appears here for you to review and decide."
        )
        for i, c in enumerate(data["candidates"]):
            ocr_tag = " · OCR used" if c["used_ocr"] else ""
            filter_results = c.get("filter_results", [])
            not_met = [f for f in filter_results if f["status"] == "not_met"]
            unclear = [f for f in filter_results if f["status"] == "cannot_determine"]
            flag_tag = ""
            if not_met:
                flag_tag = f"  ⚠️ Fails: {', '.join(f['filter'] for f in not_met)}"
            elif unclear:
                flag_tag = f"  ❓ Verify: {', '.join(f['filter'] for f in unclear)}"

            with st.expander(f"#{i+1}  {c['candidate_name']}  —  {c['weighted_score']}/5{ocr_tag}{flag_tag}"):
                if filter_results:
                    st.markdown("**Eligibility requirements:**")
                    for f in filter_results:
                        icon = {"met": "✅", "not_met": "❌", "cannot_determine": "❓"}.get(f["status"], "•")
                        st.markdown(f"{icon} **{f['filter']}**: {f['rationale']}")
                    st.markdown("---")
                for s in c["scores"]:
                    st.markdown(f"**{s['score']}/5 — {s['criterion']}**  \n{s['rationale']}")

# --- Tab 3: Interview guide --------------------------------------------------
with tab_interview:
    if not data["candidates"]:
        st.info("No candidates scored yet — go to the Setup tab.")
    else:
        st.subheader("Interview guide for top candidates")
        st.caption(
            "Questions are targeted at each candidate's specific weak or uncertain scoring areas — "
            "not generic questions — so you know exactly what to verify in the room."
        )
        top_n = st.number_input("How many top candidates?", min_value=1, max_value=10, value=4)

        if st.button("Generate interview guide"):
            top_candidates = data["candidates"][:top_n]
            guide = {}
            progress = st.progress(0.0)
            for i, c in enumerate(top_candidates):
                progress.progress(i / len(top_candidates), text=f"Generating questions for {c['candidate_name']}...")
                guide[c["candidate_name"]] = sc.generate_interview_questions(client, c, data["role_title"])
            progress.progress(1.0, text="Done.")
            data["interview_guide"] = guide
            save_data(data)

        if data.get("interview_guide"):
            for name, questions in data["interview_guide"].items():
                with st.expander(f"🎤  {name}"):
                    for q in questions:
                        st.markdown(f"**Targets: {q['topic']}**")
                        st.markdown(f"**Question:** {q['question']}")
                        st.markdown(f"✅ *Strong answer:* {q['strong_answer_signal']}")
                        st.markdown(f"❌ *Weak answer:* {q['weak_answer_signal']}")
                        st.markdown("---")

# --- Tab 4: Chat -------------------------------------------------------------
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
