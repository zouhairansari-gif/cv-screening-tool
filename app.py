"""
Recruiter CV screening tool — Streamlit app.

Run locally:      streamlit run app.py
Deploy (free):     push to GitHub, then deploy on https://share.streamlit.io

Secrets required (set in .streamlit/secrets.toml locally, or in the
Streamlit Community Cloud "Secrets" panel when deployed):
    ANTHROPIC_API_KEY = "sk-ant-..."
    APP_PASSWORD = "choose-a-shared-password-for-your-recruiters"
"""
import time
from datetime import datetime

import pandas as pd
import streamlit as st

import screening_core as sc
import storage

st.set_page_config(page_title="Candidate screening", layout="wide")


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


def format_time(ts):
    return datetime.fromtimestamp(ts).strftime("%d %b %Y, %H:%M")


# ---------------------------------------------------------------------------
# Project list — the app's home screen. Every JD search becomes its own
# project here and is never overwritten by a later one.
# ---------------------------------------------------------------------------
def render_project_list(client):
    st.title("Candidate screening — Projects")
    st.caption("Every role you screen becomes its own project. Nothing here gets overwritten by a new search.")

    with st.expander("➕ New project", expanded=True):
        jd_file = st.file_uploader("Upload the JD (.pdf, .docx, .doc, .txt)", type=["pdf", "docx", "doc", "txt"])
        jd_pasted = st.text_area("...or paste the JD text directly", height=150)

        if st.button("Create project"):
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
                with st.spinner("Extracting glossary of technical terms..."):
                    try:
                        glossary = sc.extract_glossary(client, jd_text)
                    except Exception:
                        glossary = []  # glossary is a nice-to-have — don't block project creation if it fails

                new_id = storage.create_project(
                    rubric["role_title"], jd_text, rubric["criteria"], hard_filters=[], glossary=glossary
                )
                st.session_state.current_project_id = new_id
                st.rerun()

    st.divider()
    st.subheader("Your projects")

    projects = storage.list_projects()
    if not projects:
        st.info("No projects yet — create one above to get started.")
        return

    for p in projects:
        col1, col2, col3, col4 = st.columns([3, 1.3, 1.6, 1])
        with col1:
            st.markdown(f"**{p['role_title']}**")
        with col2:
            st.markdown(f"{p['candidate_count']} candidate(s)")
        with col3:
            st.caption(f"Updated {format_time(p['updated_at'])}")
        with col4:
            if st.button("Open", key=f"open_{p['id']}"):
                st.session_state.current_project_id = p["id"]
                st.rerun()


# ---------------------------------------------------------------------------
# Workspace — everything for one open project. Same five tabs as before,
# now scoped to whichever project is currently selected.
# ---------------------------------------------------------------------------
def render_workspace(client, project_id):
    data = storage.load_project(project_id)
    if data is None:
        st.error("This project could not be found — it may have been deleted.")
        if st.button("← Back to projects"):
            st.session_state.current_project_id = None
            st.rerun()
        return

    top_col1, top_col2 = st.columns([1, 5])
    with top_col1:
        if st.button("← All projects"):
            st.session_state.current_project_id = None
            st.rerun()
    with top_col2:
        if st.button("🗑 Delete this project"):
            storage.delete_project(project_id)
            st.session_state.current_project_id = None
            st.rerun()

    st.title(data["role_title"])
    st.caption(f"Created {format_time(data['created_at'])} · Last updated {format_time(data['updated_at'])}")

    with st.expander("⚠️ Handling real candidate data — read before uploading", expanded=False):
        st.markdown(
            "- This tool never auto-rejects anyone — every score is a starting point for human review.\n"
            "- Scores and rationale are grounded in CV text only; verify anything surprising against the original file.\n"
            "- Before relying on this for real hiring decisions, spot-check scores against a bias audit and your "
            "company's data handling policy."
        )

    if data.get("glossary"):
        st.subheader("📖 Glossary")
        st.caption("Click a term to see its plain-English definition.")
        for term in data["glossary"]:
            with st.expander(term["term"]):
                st.write(term["definition"])
        st.divider()

    tab_setup, tab_shortlist, tab_compare, tab_interview, tab_chat = st.tabs(
        ["1. Setup", "2. Shortlist", "3. Compare", "4. Interview guide", "5. Ask questions"]
    )

    # --- Tab 1: Setup (rubric, golden profile, requirements, CV upload) ----
    with tab_setup:
        st.subheader("Screening rubric")
        st.caption("Edit weights, names, or descriptions if the auto-generated rubric isn't quite right. Weights are automatically rebalanced to sum to 100 when you save.")
        criteria_df = pd.DataFrame(data["criteria"])[["name", "weight", "description"]]
        edited_df = st.data_editor(
            criteria_df, num_rows="dynamic", width="stretch", key="criteria_editor",
            column_config={"weight": st.column_config.NumberColumn("weight", min_value=0, max_value=100)},
        )

        if st.button("Save rubric edits"):
            new_criteria = edited_df.to_dict("records")
            new_criteria = [c for c in new_criteria if str(c.get("name", "")).strip()]
            if not new_criteria:
                st.warning("At least one criterion is required.")
            else:
                data["criteria"] = sc.normalize_weights(new_criteria)
                data["candidates"] = []
                storage.save_project(project_id, data)
                st.success("Rubric updated. Re-score candidates below to use it.")
                st.rerun()

        if data.get("hard_filters"):
            st.markdown("**Eligibility requirements (pass/fail, not scored):**")
            for f in data["hard_filters"]:
                st.markdown(f"- **{f['name']}**: {f['requirement']}")

        st.divider()
        st.subheader("Golden profile (optional)")
        st.caption("A holistic 'ideal candidate' snapshot — complements the rubric by capturing career shape, not just individual scores.")
        if st.button("Generate golden profile"):
            with st.spinner("Generating golden profile..."):
                gp = sc.generate_golden_profile(
                    client, data["role_title"], data["jd_text"], data["criteria"], data.get("hard_filters", [])
                )
            data["golden_profile"] = gp
            storage.save_project(project_id, data)
            st.success("Golden profile generated.")

        if data.get("golden_profile"):
            gp = data["golden_profile"]
            st.markdown(f"💡 *{gp['summary']}*")

            row1 = st.columns(3)
            with row1[0]:
                st.markdown("**🎓 Education & certifications**")
                items = gp.get("ideal_education", []) + gp.get("ideal_certifications", [])
                st.markdown("\n".join(f"- {x}" for x in items) if items else "_Not specified_")
            with row1[1]:
                st.markdown("**🏭 Industry experience**")
                items = gp.get("ideal_industry_experience", [])
                st.markdown("\n".join(f"- {x}" for x in items) if items else "_Not specified_")
            with row1[2]:
                st.markdown("**🏢 Target companies**")
                items = gp.get("targeted_companies", [])
                st.markdown("\n".join(f"- {x}" for x in items) if items else "_Not specified_")

            row2 = st.columns(2)
            with row2[0]:
                st.markdown("**💼 Similar roles to target**")
                items = gp.get("targeted_similar_roles", [])
                st.markdown("\n".join(f"- {x}" for x in items) if items else "_Not specified_")
            with row2[1]:
                st.markdown("**✅ Evidence of strong experience**")
                items = gp.get("evidence_of_strong_experience", [])
                st.markdown("\n".join(f"- {x}" for x in items) if items else "_Not specified_")

            if gp.get("red_flags"):
                st.markdown("**🚩 Red flags**")
                st.markdown("\n".join(f"- {r}" for r in gp["red_flags"]))

        st.divider()
        st.subheader("Add your own requirements (optional)")
        st.caption(
            "Mark something 'Hard requirement' if it should be a pass/fail eligibility check — "
            "it will never be silently used to rank or exclude candidates by nationality; only explicit "
            "visa/residency statements in the CV are used for work-authorization checks."
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
                    merged = sc.merge_requirements_into_rubric(client, data["role_title"], data["criteria"], requirements)
                data["criteria"] = merged["criteria"]
                data["hard_filters"] = merged["hard_filters"]
                data["candidates"] = []
                storage.save_project(project_id, data)
                st.success("Requirements applied. Re-score candidates below to use the updated rubric.")
                st.rerun()

        st.divider()
        st.subheader("Candidates")
        cv_files = st.file_uploader(
            "Upload CV files (you can select multiple at once)",
            type=["pdf", "docx", "doc", "txt", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="cv_uploader",
        )

        if st.button("Score candidates") and cv_files:
            progress = st.progress(0.0, text="Starting...")
            new_candidates = []
            for i, uploaded in enumerate(cv_files):
                progress.progress(i / len(cv_files), text=f"Processing {uploaded.name}...")
                parsed = sc.extract_text_from_uploaded_file(uploaded)

                if not parsed["text"].strip():
                    st.warning(f"Skipped {uploaded.name}: {parsed['warning']}")
                    continue
                if parsed["warning"]:
                    st.info(f"{uploaded.name}: {parsed['warning']}")

                scored = sc.score_candidate(client, parsed["text"], data["criteria"], data.get("hard_filters", []))
                total = sc.weighted_score(scored["scores"], data["criteria"])
                candidate_record = {
                    "filename": uploaded.name,
                    "candidate_name": scored.get("candidate_name") or uploaded.name,
                    "cv_text": parsed["text"],
                    "weighted_score": total,
                    "scores": scored["scores"],
                    "filter_results": scored.get("filter_results", []),
                    "used_ocr": parsed["used_ocr"],
                    "comments": [],
                    "score_history": [],
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
            storage.save_project(project_id, data)
            st.success(f"Scored {len(new_candidates)} candidate(s). See the Shortlist tab.")

    # --- Tab 2: Shortlist ---------------------------------------------------
    with tab_shortlist:
        if not data["candidates"]:
            st.info("No candidates scored yet — go to the Setup tab.")
        else:
            st.subheader(f"Ranked shortlist — {data['role_title']}")
            st.caption("Eligibility flags are shown, not hidden — a candidate who fails a hard requirement still appears here for you to review.")

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
                    if c.get("score_history"):
                        prev = c["score_history"][-1]
                        st.caption(f"↻ Updated after recruiter note (was {prev['weighted_score']}/5 on {format_time(prev['timestamp'])})")

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
                    st.markdown("**Recruiter notes**")
                    if c.get("comments"):
                        for cm in c["comments"]:
                            author = f" — {cm['author']}" if cm.get("author") else ""
                            st.caption(f"{format_time(cm['timestamp'])}{author}")
                            st.markdown(f"> {cm['text']}")
                    else:
                        st.caption("No notes yet.")

                    new_comment = st.text_area(
                        "Add a note (interview findings, verification, corrections)",
                        key=f"comment_text_{c['filename']}", height=80,
                    )
                    author_name = st.text_input("Your name/initials (optional)", key=f"comment_author_{c['filename']}")

                    col_a, col_b = st.columns(2)
                    with col_a:
                        if st.button("Add note", key=f"add_comment_{c['filename']}"):
                            if new_comment.strip():
                                c.setdefault("comments", []).append({
                                    "text": new_comment.strip(),
                                    "author": author_name.strip(),
                                    "timestamp": time.time(),
                                })
                                storage.save_project(project_id, data)
                                st.rerun()
                            else:
                                st.warning("Note is empty — nothing added.")
                    with col_b:
                        if st.button("🔄 Re-score with notes", key=f"rescore_{c['filename']}"):
                            if not c.get("comments"):
                                st.warning("Add at least one note before re-scoring.")
                            else:
                                with st.spinner("Re-scoring with recruiter notes..."):
                                    result = sc.rescore_with_comments(
                                        client, c, data["criteria"], data.get("hard_filters", []), c["comments"]
                                    )
                                old_score = c["weighted_score"]
                                c.setdefault("score_history", []).append({
                                    "timestamp": time.time(), "trigger": "comment_update",
                                    "weighted_score": old_score, "scores": c["scores"],
                                })
                                c["score_history"] = c["score_history"][-5:]  # cap history length
                                c["scores"] = result["scores"]
                                c["filter_results"] = result.get("filter_results", c.get("filter_results", []))
                                c["weighted_score"] = sc.weighted_score(c["scores"], data["criteria"])

                                if data.get("golden_profile"):
                                    try:
                                        c["golden_profile_comparison"] = sc.compare_candidate_to_golden_profile(
                                            client, c, data["golden_profile"], data["role_title"]
                                        )
                                    except Exception:
                                        pass

                                data["candidates"] = sorted(data["candidates"], key=lambda x: x["weighted_score"], reverse=True)
                                storage.save_project(project_id, data)
                                st.success(f"Re-scored: {old_score}/5 → {c['weighted_score']}/5")
                                st.rerun()

                    st.markdown("---")
                    pdf_bytes = sc.generate_candidate_pdf(
                        c, data["role_title"], data["criteria"],
                        data.get("golden_profile"),
                        data.get("interview_guide", {}).get(c["candidate_name"]),
                    )
                    st.download_button(
                        "📄 Download candidate summary (PDF)", data=pdf_bytes,
                        file_name=f"{c['candidate_name'].replace(' ', '_')}_summary.pdf",
                        mime="application/pdf", key=f"pdf_{c['filename']}",
                    )

    # --- Tab 3: Compare -------------------------------------------------------
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
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # --- Tab 4: Interview guide ------------------------------------------------
    with tab_interview:
        if not data["candidates"]:
            st.info("No candidates scored yet — go to the Setup tab.")
        else:
            st.subheader("Interview guide for top candidates")
            st.caption("Short, targeted questions aimed at each candidate's most consequential gaps — a quick reference, not a report.")
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
                storage.save_project(project_id, data)
                if errors:
                    for name, err in errors:
                        st.warning(f"Couldn't generate questions for {name} — try again. ({err[:150]})")
                st.success(f"Generated questions for {len(top_candidates) - len(errors)}/{len(top_candidates)} candidate(s).")

            if data.get("interview_guide"):
                for name, questions in data["interview_guide"].items():
                    with st.expander(f"🎤  {name}"):
                        for q in questions:
                            st.markdown(f"**{q['topic']}**")
                            st.markdown(f"**Q:** {q['question']}")
                            st.markdown(f"✅ {q['strong_answer_signal']}   ❌ {q['weak_answer_signal']}")
                            st.markdown("---")

    # --- Tab 5: Chat -------------------------------------------------------------
    with tab_chat:
        if not data["candidates"]:
            st.info("No candidates scored yet — go to the Setup tab.")
        else:
            st.subheader("Ask about this shortlist")
            st.caption("Answers are grounded only in the scored candidate data — the assistant will say so if evidence isn't there.")

            history_key = f"chat_history_{project_id}"
            if history_key not in st.session_state:
                st.session_state[history_key] = []

            for msg in st.session_state[history_key]:
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
                            history=st.session_state[history_key],
                        )
                    st.write(answer)
                st.session_state[history_key] = updated_history


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------
if not check_password():
    st.stop()

client = sc.get_client(st.secrets["ANTHROPIC_API_KEY"])

if "current_project_id" not in st.session_state:
    st.session_state.current_project_id = None

if st.session_state.current_project_id is None:
    render_project_list(client)
else:
    render_workspace(client, st.session_state.current_project_id)
