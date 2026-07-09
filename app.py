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

import pandas as pd
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

tab_setup, tab_shortlist, tab_compare, tab_interview, tab_chat = st.tabs(
    ["1. Setup", "2. Shortlist", "3. Compare", "4. Interview guide", "5. Ask questions"]
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

        st.caption("Edit weights, names, or descriptions below if the auto-generated rubric isn't quite right. Weights are automatically rebalanced to sum to 100 when you save.")
        criteria_df = pd.DataFrame(data["criteria"])[["name", "weight", "description"]]
        edited_df = st.data_editor(
            criteria_df, num_rows="dynamic", width='stretch', key="criteria_editor",
            column_config={"weight": st.column_config.NumberColumn("weight", min_value=0, max_value=100)},
        )

        if st.button("Save rubric edits"):
            new_criteria = edited_df.to_dict("records")
            new_criteria = [c for c in new_criteria if str(c.get("name", "")).strip()]  # drop blank rows
            if not new_criteria:
                st.warning("At least one criterion is required.")
            else:
                data["criteria"] = sc.normalize_weights(new_criteria)
                data["candidates"] = []  # rubric changed — old scores no longer apply
                save_data(data)
                st.success("Rubric updated. Re-score candidates below to use it.")
                st.rerun()

        if data.get("hard_filters"):
            st.markdown("**Eligibility requirements (pass/fail, not scored):**")
            for f in data["hard_filters"]:
                st.markdown(f"- **{f['name']}**: {f['requirement']}")

        st.divider()
        st.subheader("Golden profile (optional)")
        st.caption(
            "A holistic 'ideal candidate' description — complements the rubric above by capturing "
            "career shape and trajectory, not just individual criterion scores."
        )
        if st.button("Generate golden profile"):
            with st.spinner("Generating golden profile..."):
                gp = sc.generate_golden_profile(
                    client, data["role_title"], data["jd_text"], data["criteria"], data.get("hard_filters", [])
                )
            data["golden_profile"] = gp
            save_data(data)
            st.success("Golden profile generated.")

        if data.get("golden_profile"):
            gp = data["golden_profile"]
            st.markdown(f"💡 *{gp['summary']}*")
            col_a, col_b = st.columns(2)
            with col_a:
                if gp.get("key_indicators"):
                    st.markdown("**✅ Strong-fit signals**")
                    st.markdown("\n".join(f"- {k}" for k in gp["key_indicators"]))
            with col_b:
                if gp.get("red_flags"):
                    st.markdown("**🚩 Red flags**")
                    st.markdown("\n".join(f"- {r}" for r in gp["red_flags"]))

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
                candidate_record = {
                    "filename": uploaded.name,
                    "candidate_name": scored.get("candidate_name") or uploaded.name,
                    "cv_text": parsed["text"],
                    "weighted_score": total,
                    "scores": scored["scores"],
                    "filter_results": scored.get("filter_results", []),
                    "used_ocr": parsed["used_ocr"],
                }

                if data.get("golden_profile"):
                    try:
                        candidate_record["golden_profile_comparison"] = sc.compare_candidate_to_golden_profile(
                            client, candidate_record, data["golden_profile"], data["role_title"]
                        )
                    except Exception as e:
                        st.info(f"Golden profile comparison skipped for {candidate_record['candidate_name']}: {e}")

                new_candidates.append(candidate_record)

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

                if c.get("golden_profile_comparison"):
                    comp = c["golden_profile_comparison"]
                    fit_icon = {"Strong": "🟢", "Moderate": "🟡", "Weak": "🔴"}.get(comp["fit_level"], "⚪")
                    st.markdown("---")
                    st.markdown(f"{fit_icon} **Golden profile fit: {comp['fit_level']}** — {comp['one_liner']}")
                    if comp.get("matches"):
                        st.markdown("✅ " + " · ".join(comp["matches"]))
                    if comp.get("gaps"):
                        st.markdown("🚩 " + " · ".join(comp["gaps"]))

                st.markdown("---")
                pdf_bytes = sc.generate_candidate_pdf(
                    c, data["role_title"], data["criteria"],
                    data.get("golden_profile"),
                    data.get("interview_guide", {}).get(c["candidate_name"]),
                )
                st.download_button(
                    "📄 Download candidate summary (PDF)",
                    data=pdf_bytes,
                    file_name=f"{c['candidate_name'].replace(' ', '_')}_summary.pdf",
                    mime="application/pdf",
                    key=f"pdf_{c['filename']}",
                )

# --- Tab 3: Compare -----------------------------------------------------------
with tab_compare:
    if not data["candidates"]:
        st.info("No candidates scored yet — go to the Setup tab.")
    else:
        st.subheader("Side-by-side comparison")
        st.caption("Useful for a calibration conversation — every candidate against every criterion in one view.")

        rows = []
        for c in data["candidates"]:
            row = {"Candidate": c["candidate_name"], "Weighted score": c["weighted_score"]}
            for s in c["scores"]:
                row[s["criterion"]] = s["score"]
            if c.get("golden_profile_comparison"):
                row["Golden profile fit"] = c["golden_profile_comparison"]["fit_level"]
            not_met = [f["filter"] for f in c.get("filter_results", []) if f["status"] == "not_met"]
            row["Eligibility"] = ("⚠️ " + ", ".join(not_met)) if not_met else "OK"
            rows.append(row)

        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

# --- Tab 4: Interview guide --------------------------------------------------
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
            guide = data.get("interview_guide", {})
            progress = st.progress(0.0)
            errors = []
            for i, c in enumerate(top_candidates):
                progress.progress(i / len(top_candidates), text=f"Generating questions for {c['candidate_name']}...")
                try:
                    guide[c["candidate_name"]] = sc.generate_interview_questions(client, c, data["role_title"])
                except Exception as e:
                    errors.append((c["candidate_name"], str(e)))
            progress.progress(1.0, text="Done.")
            data["interview_guide"] = guide
            save_data(data)
            if errors:
                for name, err in errors:
                    st.warning(f"Couldn't generate questions for {name} — try again for just this candidate. ({err[:150]})")
            st.success(f"Generated questions for {len(top_candidates) - len(errors)}/{len(top_candidates)} candidate(s).")

        if data.get("interview_guide"):
            for name, questions in data["interview_guide"].items():
                with st.expander(f"🎤  {name}"):
                    for q in questions:
                        st.markdown(f"**Targets: {q['topic']}**")
                        st.markdown(f"**Question:** {q['question']}")
                        st.markdown(f"✅ *Strong answer:* {q['strong_answer_signal']}")
                        st.markdown(f"❌ *Weak answer:* {q['weak_answer_signal']}")
                        st.markdown("---")

# --- Tab 5: Chat -------------------------------------------------------------
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
