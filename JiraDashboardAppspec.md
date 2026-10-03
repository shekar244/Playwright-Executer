# 📋 System Intent & Architecture Specification
## Project Name: Custom Jira Analytics & Pivot Studio

### 1. Product Intent & Vision
The core purpose of this standalone application is to empower developers, project managers, and scrum masters to ingest a raw **Jira Data Dump (CSV/JSON)** containing multi-issue entities (Stories and Defects) and immediately convert them into insightful metrics without writing any post-deployment code. 

Instead of configuring clunky or restrictive built-in Jira dashboards, users will use a lightweight, elegant **Streamlit web framework** paired with an interactive **Pivot/Visualization engine (PyGWalker)**. Once the underlying data framework is built, all chart assembly, metric filtering, and pivot aggregations are performed 100% via a point-and-click graphical user interface.

---

### 2. Core Feature Requirements

#### A. Data Ingestion Layer
*   **File Uploader:** Drag-and-drop mechanism accepting `.csv` data exports directly from Jira.
*   **Data Validation:** Automatic validation to verify required Jira columns exist (e.g., `Issue Type`, `Status`, `Story Points`, `Created`).
*   **Pre-Processing Engine:** Local Python parsing to handle missing values (e.g., mapping empty `Story Points` to `0`) and standardising text fields (e.g., lowercase matching for "Bug" vs "bug").

#### B. Dynamic KPI Analytics
*   Instantaneous calculated KPI cards rendering critical overview metrics upon file upload:
    *   Total User Stories processed.
    *   Total Defects/Bugs logged.
    *   Defect-to-Story ratio or Total Backlog Story Points (Velocity Scope).

#### C. No-Code Pivot Graphic Studio
*   Embedding an interactive Tableau/PowerBI-style visual interface.
*   **Drag-and-Drop Channels:** Rows, Columns, Values, Filters, and Color encodings.
*   **Supported Chart Topologies:** Elegant Bar charts, Line trends, Scatter matrices, Area maps, and deep Pivot multi-level data tables.
*   **Local State Isolation:** Chart manipulation updates in real-time within the client browser without needing script re-runs or server re-compilations.

---

### 3. Architecture Blueprint

```
[ Jira Export File ] ──(Upload)──> [ Streamlit Web Frontend ]
                                            │
                                  [ Data Processing Layer ]
                                   (Pandas Normalization)
                                            │
                                            ▼
                                  [ Streamlit Layout Engine ]
                                   (KPI Cards & Visuals)
                                            │
                                            ▼
                                [ PyGWalker Render Studio ]
                               (Interactive Pivot/Charts)
```

#### Layer Breakdown:
1.  **Frontend Interface (Streamlit):** Serves the reactive execution cycle, layout wrappers, and the state-aware File Upload interface.
2.  **Processing Engine (Pandas):** Ingests raw multi-attribute rows, transforms them into strict DataFrame paradigms, and isolates missing or mismatched entities.
3.  **Visual Render Core (PyGWalker / Vega-Lite):** Compiles the DataFrame into a modular UI package directly rendered inside the browser viewport via web-component containers.

---

### 4. Technical Stack Constraints
*   **Base Language:** Python 3.10+
*   **UI Framework:** `streamlit>=1.30.0`
*   **Data Analysis:** `pandas>=2.0.0`
*   **Graphic Pivot Engine:** `pygwalker>=0.4.0`

---

### 5. Implementation Roadmap (Quick Start Code)

Save the following codebase as `app.py`. This single script fully executes the system architecture defined above.

```python
import streamlit as st
import pandas as pd
import pygwalker as pyg

# 1. Framework & Theme Configuration
st.set_page_config(
    page_title="Jira Analytics Studio",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Application Theme/Styling Wrappers
st.markdown("""
    <style>
    .main .block-container { padding-top: 2rem; }
    div[data-testid="stMetricValue"] { font-size: 28px; color: #1e293b; }
    </style>
""", unsafe_allow_html=True)

# 2. Main Workspace Layout
st.title("🎯 Jira Custom Analytics & Pivot Studio")
st.caption("A standalone framework for code-free visual reporting over stories, tasks, and bugs.")

# Sidebar Configuration for File Loading
with st.sidebar:
    st.header("📂 Data Configuration")
    uploaded_file = st.file_uploader("Upload Jira CSV Export File", type=["csv"])
    
    st.divider()
    st.markdown("""
    **Expected Core Columns:**
    * `Issue Type` (Story, Bug, Task)
    * `Status` (To Do, In Progress, Done)
    * `Story Points` or `Estimation`
    * `Created` / `Resolution Date`
    """)

# 3. Execution Pipeline
if uploaded_file is not None:
    try:
        # Load and clean incoming dataset
        df = pd.read_csv(uploaded_file)
        
        # Standardize standard Jira column strings to guarantee metric calculation
        df.columns = [col.strip() for col in df.columns]
        
        # Safe-fill for analytical numerical logic
        for col in df.columns:
            if 'point' in col.lower() or 'estimate' in col.lower():
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 4. Contextual Metric Calculations
        st.subheader("📈 Core Backlog Health Overview")
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        
        total_issues = len(df)
        
        # Calculate dynamically based on typical Jira naming conventions
        type_col = next((c for c in df.columns if 'type' in c.lower()), None)
        status_col = next((c for c in df.columns if 'status' in c.lower()), None)
        points_col = next((c for c in df.columns if 'point' in c.lower() or 'estimate' in c.lower()), None)
        
        stories = len(df[df[type_col].astype(str).str.lower() == 'story']) if type_col else 0
        defects = len(df[df[type_col].astype(str).str.lower().str.contains('bug|defect')]) if type_col else 0
        total_pts = int(df[points_col].sum()) if points_col else 0
        
        with m_col1:
            st.metric("Total Items Ingested", f"{total_issues:,}")
        with m_col2:
            st.metric("User Stories", f"{stories:,}")
        with m_col3:
            st.metric("Logged Defects", f"{defects:,}")
        with m_col4:
            st.metric("Total Backlog Volume", f"{total_pts} SP")

        st.divider()
        
        # 5. Visual Sandbox Execution
        st.subheader("🛠️ Customizable Visualization & Pivot Workspace")
        st.info("💡 **How to build charts:** Drag attributes from the left panel into **Rows** or **Columns**. Change the **Mark** type dropdown to switch from tables to Bar, Line, or Area charts.")
        
        # Initialize No-Code Interface Wrapper
        pyg.walk(df, env='Streamlit', dark='light')

    except Exception as e:
        st.error(f"Error parsing data file: {str(e)}. Please ensure it is a valid comma-separated format.")
else:
    # Empty State Interface
    st.info("👋 System Idle. Please upload your Jira file using the sidebar panel to unlock the reporting metrics studio.")
```

***

### 6. Deployment and Run Execution
To deploy this architecture locally or on an internal container network:
1. Ensure `app.py` and the above packages are localized.
2. Fire up the application layout environment using:
   ```bash
   streamlit run app.py
   ```