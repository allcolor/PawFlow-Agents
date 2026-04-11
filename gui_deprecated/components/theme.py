"""Global iOS-style theme for Streamlit UI.

Call `inject_theme()` once per page to apply the styles.
"""

import streamlit as st

# iOS-inspired design tokens
_PRIMARY = "#007AFF"
_PRIMARY_HOVER = "#0066D6"
_PRIMARY_ACTIVE = "#004EA8"
_DANGER = "#FF3B30"
_DANGER_HOVER = "#D63126"
_SUCCESS = "#34C759"
_SUCCESS_HOVER = "#2DA44E"
_WARNING = "#FF9500"
_SECONDARY_BG = "rgba(0, 122, 255, 0.08)"
_SECONDARY_HOVER = "rgba(0, 122, 255, 0.15)"
_TERTIARY_BG = "rgba(0, 0, 0, 0.05)"
_TERTIARY_HOVER = "rgba(0, 0, 0, 0.10)"
_DISABLED_BG = "rgba(0, 0, 0, 0.04)"
_DISABLED_TEXT = "rgba(0, 0, 0, 0.25)"
_RADIUS = "10px"
_RADIUS_SM = "8px"
_TRANSITION = "all 0.18s ease"

THEME_CSS = f"""
<style>
/* ============================================================
   iOS-STYLE GLOBAL THEME
   ============================================================ */

/* --- Primary buttons (filled blue) --- */
button[kind="primary"],
.stButton > button[kind="primary"] {{
    background: {_PRIMARY} !important;
    color: white !important;
    border: none !important;
    border-radius: {_RADIUS} !important;
    font-weight: 600 !important;
    padding: 0.5rem 1.25rem !important;
    transition: {_TRANSITION} !important;
    box-shadow: none !important;
}}
button[kind="primary"]:hover {{
    background: {_PRIMARY_HOVER} !important;
    box-shadow: 0 2px 8px rgba(0, 122, 255, 0.3) !important;
}}
button[kind="primary"]:active {{
    background: {_PRIMARY_ACTIVE} !important;
    transform: scale(0.97);
}}

/* --- Secondary buttons (light blue bg, blue text) --- */
button[kind="secondary"],
.stButton > button[kind="secondary"] {{
    background: {_SECONDARY_BG} !important;
    color: {_PRIMARY} !important;
    border: none !important;
    border-radius: {_RADIUS} !important;
    font-weight: 600 !important;
    padding: 0.5rem 1.25rem !important;
    transition: {_TRANSITION} !important;
    box-shadow: none !important;
}}
button[kind="secondary"]:hover {{
    background: {_SECONDARY_HOVER} !important;
}}
button[kind="secondary"]:active {{
    background: rgba(0, 122, 255, 0.22) !important;
    transform: scale(0.97);
}}

/* --- Disabled buttons --- */
button:disabled,
.stButton > button:disabled {{
    background: {_DISABLED_BG} !important;
    color: {_DISABLED_TEXT} !important;
    border: none !important;
    cursor: not-allowed !important;
    box-shadow: none !important;
}}

/* --- Download buttons --- */
.stDownloadButton > button {{
    background: {_SECONDARY_BG} !important;
    color: {_PRIMARY} !important;
    border: none !important;
    border-radius: {_RADIUS} !important;
    font-weight: 600 !important;
    transition: {_TRANSITION} !important;
}}
.stDownloadButton > button:hover {{
    background: {_SECONDARY_HOVER} !important;
}}

/* --- Form submit button --- */
.stFormSubmitButton > button {{
    background: {_PRIMARY} !important;
    color: white !important;
    border: none !important;
    border-radius: {_RADIUS} !important;
    font-weight: 600 !important;
    transition: {_TRANSITION} !important;
}}
.stFormSubmitButton > button:hover {{
    background: {_PRIMARY_HOVER} !important;
    box-shadow: 0 2px 8px rgba(0, 122, 255, 0.3) !important;
}}

/* --- Link buttons --- */
.stLinkButton > a {{
    background: transparent !important;
    color: {_PRIMARY} !important;
    border: none !important;
    border-radius: {_RADIUS} !important;
    font-weight: 600 !important;
    text-decoration: none !important;
    transition: {_TRANSITION} !important;
}}
.stLinkButton > a:hover {{
    background: {_SECONDARY_BG} !important;
    text-decoration: none !important;
}}

/* --- Toggle / checkbox iOS style --- */
.stCheckbox > label > div[data-testid="stCheckbox"] {{
    transition: {_TRANSITION} !important;
}}

/* --- Selectbox, multiselect, text input — rounded --- */
.stSelectbox > div > div,
.stMultiSelect > div > div,
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stTextArea > div > textarea {{
    border-radius: {_RADIUS_SM} !important;
    border: 1.5px solid rgba(0, 0, 0, 0.12) !important;
    transition: {_TRANSITION} !important;
}}
.stSelectbox > div > div:focus-within,
.stMultiSelect > div > div:focus-within,
.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus,
.stTextArea > div > textarea:focus {{
    border-color: {_PRIMARY} !important;
    box-shadow: 0 0 0 2px rgba(0, 122, 255, 0.2) !important;
}}

/* --- Tabs — pill style --- */
.stTabs [data-baseweb="tab-list"] {{
    gap: 4px;
    background: {_TERTIARY_BG};
    border-radius: {_RADIUS};
    padding: 3px;
}}
.stTabs [data-baseweb="tab"] {{
    border-radius: {_RADIUS_SM} !important;
    padding: 0.4rem 1rem !important;
    font-weight: 500 !important;
    transition: {_TRANSITION} !important;
    background: transparent !important;
    border: none !important;
}}
.stTabs [data-baseweb="tab"][aria-selected="true"] {{
    background: white !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1) !important;
    color: {_PRIMARY} !important;
}}
.stTabs [data-baseweb="tab-highlight"] {{
    display: none !important;
}}
.stTabs [data-baseweb="tab-border"] {{
    display: none !important;
}}

/* --- Expander — clean --- */
.streamlit-expanderHeader {{
    border-radius: {_RADIUS_SM} !important;
    font-weight: 600 !important;
    transition: {_TRANSITION} !important;
}}
.streamlit-expanderHeader:hover {{
    background: {_TERTIARY_BG} !important;
}}

/* --- Metrics — subtle card look --- */
[data-testid="stMetric"] {{
    background: {_TERTIARY_BG};
    border-radius: {_RADIUS};
    padding: 0.75rem 1rem;
}}
[data-testid="stMetricValue"] {{
    font-weight: 700 !important;
}}

/* --- Alerts — rounded --- */
.stAlert {{
    border-radius: {_RADIUS} !important;
}}

/* --- Sidebar — cleaner --- */
section[data-testid="stSidebar"] {{
    background: #FAFAFA;
}}

/* --- Hide Streamlit deploy button --- */
.stDeployButton, [data-testid="stToolbar"] .stDeployButton,
header [data-testid="stToolbar"] button[kind="header"] {{
    display: none !important;
}}

/* ============================================================
   SEMANTIC BUTTON CLASSES (via st.markdown wrapper)
   Use: st.markdown('<div class="btn-danger">', unsafe_allow_html=True)
        st.button("Delete")
        st.markdown('</div>', unsafe_allow_html=True)
   ============================================================ */

.btn-danger + div button,
.btn-danger button {{
    background: {_DANGER} !important;
    color: white !important;
}}
.btn-danger + div button:hover,
.btn-danger button:hover {{
    background: {_DANGER_HOVER} !important;
    box-shadow: 0 2px 8px rgba(255, 59, 48, 0.3) !important;
}}

.btn-success + div button,
.btn-success button {{
    background: {_SUCCESS} !important;
    color: white !important;
}}
.btn-success + div button:hover,
.btn-success button:hover {{
    background: {_SUCCESS_HOVER} !important;
    box-shadow: 0 2px 8px rgba(52, 199, 89, 0.3) !important;
}}

.btn-warning + div button,
.btn-warning button {{
    background: {_WARNING} !important;
    color: white !important;
}}
.btn-warning + div button:hover,
.btn-warning button:hover {{
    background: #E08600 !important;
}}

.btn-tertiary + div button,
.btn-tertiary button {{
    background: {_TERTIARY_BG} !important;
    color: #333 !important;
}}
.btn-tertiary + div button:hover,
.btn-tertiary button:hover {{
    background: {_TERTIARY_HOVER} !important;
}}

</style>
"""


def inject_theme():
    """Inject the global iOS theme CSS. Call once per page."""
    st.markdown(THEME_CSS, unsafe_allow_html=True)
