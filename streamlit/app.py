"""Quick manual test UI for the drafting backend API.

Run with:
    pip install -r streamlit/requirements.txt
    cp streamlit/.env.example streamlit/.env   # then edit API_BASE_URL if needed
    streamlit run streamlit/app.py

Requires the backend running separately (uvicorn app.main:app --reload).
"""
import logging
from urllib.parse import quote

import requests
import streamlit as st
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("streamlit_app")

st.set_page_config(page_title="Drafting Backend Tester", layout="wide")

API_BASE = st.sidebar.text_input("API base URL", value=settings.API_BASE_URL)

st.sidebar.divider()


def _request(method: str, path: str, **kwargs) -> requests.Response | None:
    url = f"{API_BASE}{path}"
    logger.info("API request: %s %s", method, url)
    try:
        resp = requests.request(method, url, **kwargs)
    except requests.exceptions.RequestException:
        logger.exception("API request failed (no response): %s %s", method, url)
        return None
    if resp.ok:
        logger.info("API response OK: %s %s -> %s", method, url, resp.status_code)
    else:
        logger.warning(
            "API response error: %s %s -> %s | %s", method, url, resp.status_code, resp.text[:500]
        )
    return resp


def api_get(path: str, **kwargs) -> requests.Response | None:
    return _request("GET", path, **kwargs)


def api_post(path: str, **kwargs) -> requests.Response | None:
    return _request("POST", path, **kwargs)


def show_error(resp: requests.Response | None) -> None:
    if resp is None:
        st.error("Could not reach the backend. Check the API base URL (sidebar) and that the server is running.")
        return
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    st.error(f"{resp.status_code}: {detail}")


def path_segment(value: str) -> str:
    return quote(value, safe="")


def show_loading_overlay(message: str):
    placeholder = st.empty()
    placeholder.markdown(
        f"""
        <style>
        @keyframes drafting-spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
        </style>
        <div style="
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(0, 0, 0, 0.6); z-index: 9999;
            display: flex; flex-direction: column; align-items: center; justify-content: center;
        ">
            <div style="
                border: 6px solid rgba(255, 255, 255, 0.2); border-top: 6px solid #fff;
                border-radius: 50%; width: 56px; height: 56px;
                animation: drafting-spin 0.9s linear infinite;
            "></div>
            <p style="color: #fff; margin-top: 18px; font-size: 18px; font-family: sans-serif;">
                {message}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return placeholder


def refresh_cases() -> None:
    resp = api_get("/session/cases")
    if resp is not None and resp.ok:
        st.session_state["cases"] = resp.json()["cases"]
    else:
        show_error(resp)


def refresh_process_types() -> None:
    resp = api_get("/template-generation/process-types")
    if resp is not None and resp.ok:
        st.session_state["process_types"] = resp.json()["process_types"]
    else:
        show_error(resp)


# Auto-load the dropdown sources once per session, so they aren't empty
# before the user visits the Cases/Templates tabs first.
if "cases" not in st.session_state:
    refresh_cases()
if "process_types" not in st.session_state:
    refresh_process_types()


def case_select(label: str, key: str):
    case_names = [c["case_name"] for c in st.session_state.get("cases", [])]
    if not case_names:
        st.caption("No cases yet — create one in the Cases tab.")
        return None
    return st.selectbox(label, case_names, key=key)


def process_type_select(label: str, key: str):
    process_types = st.session_state.get("process_types", [])
    if not process_types:
        st.caption("No process types yet — upload templates for one in the Templates tab.")
        return None
    return st.selectbox(label, process_types, key=key)


def show_upload_result(data: dict) -> None:
    uploaded = data.get("uploaded", [])
    failed = data.get("failed", {})
    if uploaded:
        st.success(f"Uploaded {len(uploaded)} template(s) for '{data.get('process_type')}'")
        st.dataframe(uploaded)
    if failed:
        st.error(f"{len(failed)} file(s) failed")
        st.dataframe([{"filename": name, "error": err} for name, err in failed.items()])


def show_templates(data: dict) -> None:
    templates = data.get("templates", [])
    if not templates:
        st.info(f"No templates stored for '{data.get('process_type')}' yet.")
        return
    st.write(f"**{len(templates)} template(s)** for `{data.get('process_type')}`")
    st.dataframe(
        [
            {
                "filename": t["filename"],
                "is_active": t["is_active"],
                "text_length": len(t.get("ocr_text") or ""),
                "id": t["id"],
            }
            for t in templates
        ]
    )
    for t in templates:
        with st.expander(f"View structure: {t['filename']}"):
            st.text(t.get("ocr_text") or "(empty)")


def show_ingest_result(data: dict) -> None:
    results = data.get("results", {})
    failed = data.get("failed", {})
    if results:
        st.write(f"**{len(results)} file(s) processed**")
        st.dataframe(
            [
                {
                    "filename": name,
                    "type": doc.get("type"),
                    "text_length": len(doc.get("text") or ""),
                }
                for name, doc in results.items()
            ]
        )
        for name, doc in results.items():
            with st.expander(f"View text: {name}"):
                st.text(doc.get("text") or "(no text)")
    if failed:
        st.error(f"{len(failed)} file(s) failed")
        st.dataframe([{"filename": name, "error": err} for name, err in failed.items()])


def show_case_ingestion(data: dict) -> None:
    files = data.get("files", [])
    if not files:
        st.info("No files ingested for this case yet.")
        return
    st.dataframe(
        [
            {
                "filename": f["filename"],
                "doc_type": f.get("doc_type"),
                "fields_extracted": f.get("fields_extracted"),
                "error": f.get("error"),
            }
            for f in files
        ]
    )
    for f in files:
        with st.expander(f["filename"]):
            if f.get("error"):
                st.error(f["error"])
            if f.get("ocr_text"):
                st.text_area("OCR text", f["ocr_text"], height=150, key=f"ocr_{f['id']}")
            if f.get("fields"):
                st.write("Extracted fields:")
                st.json(f["fields"])


st.sidebar.subheader("Fee / Address Scraping")
st.sidebar.caption("Independent of case flow — can be run anytime.")
if st.sidebar.button("Trigger fee scrape"):
    resp = api_post("/fees/scrape")
    if resp is not None and resp.ok:
        st.sidebar.success(resp.json())
    else:
        show_error(resp)
if st.sidebar.button("Trigger address scrape"):
    resp = api_post("/addresses/scrape")
    if resp is not None and resp.ok:
        st.sidebar.success(resp.json())
    else:
        show_error(resp)

st.title("Drafting Backend — Quick Test UI")

tab_cases, tab_templates, tab_ingest, tab_draft, tab_scrape = st.tabs(
    ["Cases", "Templates", "Ingestion", "Draft Generation", "Fee / Address Data"]
)

# ---- Cases ----
with tab_cases:
    st.header("Create a Case")
    with st.form("create_case_form"):
        case_name = st.text_input("Case name")
        submitted = st.form_submit_button("Create case")
    if submitted:
        if not case_name:
            st.warning("Provide a case name.")
        else:
            resp = api_post("/session/start", json={"case_name": case_name})
            if resp is not None and resp.ok:
                data = resp.json()
                st.success(f"Created case_id={data['case_id']} for '{data['case_name']}'")
                refresh_cases()
            else:
                show_error(resp)

    st.header("Existing Cases")
    if st.button("Refresh case list"):
        refresh_cases()
    cases = st.session_state.get("cases", [])
    if cases:
        st.table(cases)
    else:
        st.caption("No cases loaded yet — click Refresh.")

# ---- Templates ----
with tab_templates:
    st.header("Upload Templates")
    with st.form("upload_templates_form"):
        process_type = st.text_input("Process type (new or existing)")
        template_files = st.file_uploader(
            "Sample .docx files (max 5 per process_type)", type=["docx"], accept_multiple_files=True
        )
        submitted = st.form_submit_button("Upload")
    if submitted:
        if not process_type or not template_files:
            st.warning("Provide a process_type and at least one .docx file.")
        else:
            file_payload = [("files", (f.name, f.getvalue(), f.type)) for f in template_files]
            overlay = show_loading_overlay(f"Parsing {len(template_files)} template file(s)...")
            resp = api_post(f"/template-generation/{path_segment(process_type)}", files=file_payload)
            overlay.empty()
            if resp is not None and resp.ok:
                show_upload_result(resp.json())
                refresh_process_types()
            else:
                show_error(resp)

    st.header("Existing Process Types")
    if st.button("Refresh process types"):
        refresh_process_types()
    process_types = st.session_state.get("process_types", [])
    if process_types:
        st.write(process_types)
    else:
        st.caption("No process types loaded yet — click Refresh.")

    st.header("View Stored Templates")
    view_pt = process_type_select("process_type to view", key="view_pt")
    if st.button("Fetch templates"):
        if not view_pt:
            st.warning("Select a process_type.")
        else:
            resp = api_get(f"/template-generation/{path_segment(view_pt)}")
            if resp is not None and resp.ok:
                show_templates(resp.json())
            else:
                show_error(resp)

# ---- Ingestion ----
with tab_ingest:
    st.header("Ingest Documents")
    with st.form("ingest_form"):
        case_name_ingest = case_select("Case name", key="ingest_case")
        ingest_files = st.file_uploader(
            "Documents (PDF)", type=["pdf"], accept_multiple_files=True, key="ingest_files"
        )
        submitted = st.form_submit_button("Ingest")
    if submitted:
        if not case_name_ingest or not ingest_files:
            st.warning("Select a case and provide at least one PDF.")
        else:
            file_payload = [("files", (f.name, f.getvalue(), f.type)) for f in ingest_files]
            overlay = show_loading_overlay(f"OCR + classification running on {len(ingest_files)} file(s)...")
            resp = api_post(f"/ingest/{path_segment(case_name_ingest)}", files=file_payload)
            overlay.empty()
            if resp is not None and resp.ok:
                show_ingest_result(resp.json())
            else:
                show_error(resp)

    st.header("View Ingested Data")
    view_case = case_select("Case name to view", key="view_case")
    if st.button("Fetch ingested data"):
        if not view_case:
            st.warning("Select a case.")
        else:
            resp = api_get(f"/ingest/{path_segment(view_case)}")
            if resp is not None and resp.ok:
                show_case_ingestion(resp.json())
            else:
                show_error(resp)

# ---- Draft Generation ----
with tab_draft:
    st.header("Draft Generation & Review")
    d_case = case_select("Case name", key="draft_case")
    d_process_type = process_type_select("Process type", key="draft_process_type")

    if not d_case or not d_process_type:
        st.caption("Select a case and process type to start a draft session.")
    else:
        if "draft_threads" not in st.session_state:
            st.session_state["draft_threads"] = {}
        thread_key = (d_case, d_process_type)
        rounds = st.session_state["draft_threads"].setdefault(thread_key, [])

        for i, rnd in enumerate(rounds, start=1):
            with st.chat_message("assistant"):
                st.write(f"Draft v{i} generated for **{d_case}** / **{d_process_type}**")
                st.download_button(
                    f"Download draft v{i}.docx",
                    data=rnd["draft_bytes"],
                    file_name=f"draft_{d_case}_v{i}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key=f"dl_{thread_key}_{i}",
                )
            if rnd["decision"] is not None:
                with st.chat_message("user"):
                    if rnd["decision"] == "Approve":
                        st.write("Approved")
                    else:
                        st.write(f"Rejected — feedback: {rnd['feedback']}")

        pending = bool(rounds) and rounds[-1]["decision"] is None

        if not rounds:
            if st.button("Generate draft", key="gen_draft_btn"):
                overlay = show_loading_overlay("Generating draft (filing-data lookup + drafting agent)...")
                resp = api_post(f"/draft/{path_segment(d_case)}/{path_segment(d_process_type)}/generate")
                overlay.empty()
                if resp is not None and resp.ok:
                    rounds.append(
                        {"draft_bytes": resp.content, "decision": None, "feedback": None}
                    )
                    st.rerun()
                else:
                    show_error(resp)
        elif pending:
            round_num = len(rounds)
            st.subheader("Review this draft")
            decision = st.radio("Decision", ["Approve", "Reject"], key=f"decision_{thread_key}_{round_num}")
            feedback = st.text_area(
                "Feedback (required if rejecting)", key=f"feedback_{thread_key}_{round_num}"
            )
            if st.button("Submit review", key=f"submit_{thread_key}_{round_num}"):
                if decision == "Reject" and not feedback.strip():
                    st.warning("Feedback is required when rejecting.")
                else:
                    body = {"approved": decision == "Approve", "feedback": feedback or None}
                    overlay_msg = (
                        "Regenerating draft with your feedback..."
                        if decision == "Reject"
                        else "Finalizing approval..."
                    )
                    overlay = show_loading_overlay(overlay_msg)
                    resp = api_post(
                        f"/draft/{path_segment(d_case)}/{path_segment(d_process_type)}/approve",
                        json=body,
                    )
                    overlay.empty()
                    if resp is not None and resp.ok:
                        rounds[-1]["decision"] = decision
                        rounds[-1]["feedback"] = feedback if decision == "Reject" else None
                        approved = resp.headers.get("X-Approved") == "true"
                        max_reached = resp.headers.get("X-Max-Revisions-Reached") == "true"
                        if not approved and not max_reached:
                            rounds.append(
                                {"draft_bytes": resp.content, "decision": None, "feedback": None}
                            )
                        elif max_reached:
                            st.session_state[f"capped_{thread_key}"] = True
                        st.rerun()
                    else:
                        show_error(resp)
        elif rounds[-1]["decision"] == "Approve":
            st.success("Draft approved — this session is complete.")
        elif st.session_state.get(f"capped_{thread_key}"):
            st.warning("Max revisions reached without approval — this session has ended.")

# ---- Fee / Address Data ----
with tab_scrape:
    st.caption("Use the sidebar to trigger a scrape — this tab is just for viewing results.")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Fees")
        if st.button("List fees"):
            resp = api_get("/fees")
            if resp is not None and resp.ok:
                st.dataframe(resp.json())
            else:
                show_error(resp)
    with col2:
        st.subheader("Addresses")
        if st.button("List addresses"):
            resp = api_get("/addresses")
            if resp is not None and resp.ok:
                st.dataframe(resp.json())
            else:
                show_error(resp)
