# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Agent streamlit web app"""

import asyncio
import base64
import datetime
from io import BytesIO
import json
import logging
import os
import subprocess
from time import time

import pandas as pd
import streamlit as st

import yfinance as yf
import matplotlib.pyplot as plt

from google.genai.types import Content, Part

from google.adk.events import Event, EventActions
from google.adk.sessions import Session
from shared.firestore_session_service import (FirestoreSessionService
                                            as SessionService)

from PIL import Image

from google.adk.artifacts import GcsArtifactService
from agent_runtime_client import FastAPIEngineRuntime

MAX_RUN_RETRIES = 10
DEFAULT_USER_ID = "user@ai"
DEFAULT_AGENT_NAME = "default-agent"
DEFAULT_TICKERS = [
    "GOOGL",
    "MSFT",
    "AMZN",
    "^GSPC",
    "^DJI",
    "^IXIC",
]


logging.getLogger().setLevel(logging.INFO)

user_agent = st.context.headers.get("User-Agent", "")
if " Mobile" in user_agent:
    initial_sidebar_state = "collapsed"
else:
    initial_sidebar_state = "expanded"

st.set_page_config(layout="wide",
                   page_icon=":material/bar_chart:",
                   page_title="📊 Enterprise Data Agent",
                   initial_sidebar_state=initial_sidebar_state)

material_theme_style = """
    <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@100;200;300&display=swap" rel="stylesheet">
    <style>
        :root {
            --md-sys-color-primary: #8AB4F8; /* Lighter Google Blue for dark theme */
            --md-sys-color-on-primary: #101010; /* Dark text for contrast on light blue */
            --md-sys-color-primary-container: #1E3A5F; /* Dark Blue container */
            --md-sys-color-on-primary-container: #E3F2FD; /* Light text for contrast on dark blue container */
            --md-sys-color-secondary: #81C995; /* Lighter Google Green for dark theme */
            --md-sys-color-on-secondary: #101010; /* Dark text for contrast on light green */
            --md-sys-color-surface: #121212; /* Dark Gray surface */
            --md-sys-color-on-surface: #E8EAED; /* Light Gray text */
            --md-sys-color-surface-container-highest: #3C4043; /* Darker gray for AI chat bubbles */
            --md-sys-color-surface-variant: #202124; /* Dark gray for backgrounds or less prominent cards */
            --md-sys-color-on-surface-variant: #BDC1C6; /* Medium gray text */
            --md-sys-color-background: #000000; /* Black page background */
            --md-sys-color-outline: #5F6368; /* Gray borders */
            --md-sys-color-error: #F28B82; /* Lighter Google Red for dark theme */
            --md-font-family: 'Inter', Verdana;
            --md-border-radius: 8px;
            --md-border-radius-large: 16px; /* For chat bubbles */
            --md-elevation-1: 0px 1px 2px 0px rgba(0, 0, 0, 0.3), 0px 1px 3px 0px rgba(0, 0, 0, 0.15);
            --md-elevation-2: 0px 2px 4px -1px rgba(0,0,0,0.3), 0px 4px 5px 0px rgba(0,0,0,0.15);

        }

        body {
            font-family: var(--md-font-family);
            background-color: var(--md-sys-color-background);
            color: var(--md-sys-color-on-surface);
        }

        /* Streamlit Specific Overrides */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header[data-testid="stHeader"] {
            display:none !important;
        }

        .block-container {
            padding-top: calc(64px + 2rem); /* 64px for app bar + original padding */
            padding-bottom: 7rem; /* Increased space for chat input */
            padding-left: 2.5rem;
            padding-right: 2.5rem;
            max-width: 1600px;
            margin: 0 auto;
        }

        /* Titles and Text */
        h1, h2, h3, h4, h5, h6 {
            font-family: var(--md-font-family);
            color: var(--md-sys-color-on-surface);
        }

        /* Custom st.title styling as AppBar */
        /* This selector targets the first st.markdown/st.title in the main flow */
        .main > div > div[data-testid="stVerticalBlock"] > div:nth-child(1) > div[data-testid="stMarkdownContainer"] > h1 {
            font-size: 1.25rem; /* Material AppBar Title Size (20sp) */
            font-weight: 500;
            line-height: 1.6;
            padding: 0 24px;
            background-color: var(--md-sys-color-primary);
            color: var(--md-sys-color-on-primary);
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 998;
            height: 64px;
            display: flex;
            align-items: center;
            box-shadow: var(--md-elevation-2);
        }
        .main > div > div[data-testid="stVerticalBlock"] > div:nth-child(1) > div[data-testid="stMarkdownContainer"] > h1 .material-icons {
            color: var(--md-sys-color-on-primary) !important;
            font-size: 1.8rem !important;
            margin-right: 16px !important;
            vertical-align: middle;
        }

        /* Subheader and other markdown text */
        div[data-testid="stMarkdownContainer"] h3 { /* For st.subheader */
            color: var(--md-sys-color-on-surface);
            font-weight: 400;
            font-size: 1.3rem; /* Larger than h4 */
            margin-top: 2rem;
            margin-bottom: 1rem;
        }
         div[data-testid="stMarkdownContainer"] h4 { /* "Examples of questions:" */
            color: var(--md-sys-color-on-surface-variant);
            font-weight: 500; /* Make it slightly bolder */
            font-size: 1.1rem;
            margin-top: 1.5rem;
            margin-bottom: 0.75rem;
        }


        /* Example questions list items */
        div[data-testid="stMarkdownContainer"] ul {
            list-style: none;
            padding-left: 0;
        }

        div[data-testid="stMarkdownContainer"] ul li .material-icons {
            vertical-align: middle;
            margin-right: 12px;
            color: var(--md-sys-color-primary);
            font-size: 1.4rem;
        }

        span[data-testid="stIconMaterial"] {
            color: var(--md-sys-color-secondary);
        }

        a, a:visited {
            color: var(--md-sys-color-primary);
            text-decoration: none;
            font-weight: 500;
        }
        a:hover {
            text-decoration: underline;
        }

        /* Sidebar */
        [data-testid="stSidebar"] {
            background-color: var(--md-sys-color-surface);
            border-right: 1px solid var(--md-sys-color-outline);
            /*padding-top: 1rem; /* Add some padding at the top */
            box-shadow: var(--md-elevation-1);
        }
         [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 { /* "Sessions" title */
            color: var(--md-sys-color-primary);
            font-size: 1.1rem; /* Slightly smaller */
            font-weight: 500;
            padding: 0 1rem;
            margin-top: 0.5rem;
            margin-bottom: 0.5rem;
        }
        [data-testid="stSidebar"] .stButton > button {
            background-color: var(--md-sys-color-primary);
            color: var(--md-sys-color-on-primary);
            border: none;
            /* padding: 10px 16px; */
            border-radius: var(--md-border-radius);
            font-weight: 500;
            text-align: center;
            width: calc(100% - 4rem);
            margin: 1rem 1rem; /* More margin for spacing */
            transition: background-color 0.2s ease, box-shadow 0.2s ease;
            box-shadow: var(--md-elevation-1);
        }
        [data-testid="stSidebar"] .stButton > button:hover {
            background-color: #1A73E8; /* Darker shade of primary for hover */
            box-shadow: var(--md-elevation-2);
        }
        [data-testid="stSidebar"] .stButton > button:active {
            background-color: #1765C7; /* Even darker for active */
        }
        [data-testid="stSidebar"] .stButton > button:focus-visible { /* For keyboard nav */
            outline: 2px solid var(--md-sys-color-primary-container);
            outline-offset: 2px;
        }

        [data-testid="stSidebar"] [data-testid="stSelectbox"] label {
             color: var(--md-sys-color-on-surface-variant);
             font-weight: 500;
             font-size: 0.9rem;
             padding: 0.5rem 1rem 0.25rem 1rem; /* Adjust padding */
        }
        [data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div[data-baseweb="select"] > div {
            background-color: var(--md-sys-color-surface-variant);
            border: 1px solid var(--md-sys-color-outline);
            border-radius: var(--md-border-radius);
            color: var(--md-sys-color-on-surface-variant);
            margin: 0 1rem; /* Add horizontal margin */
            width: calc(100% - 2rem); /* Adjust width */
        }
        div[data-baseweb="popover"] ul[role="listbox"] { /* Selectbox dropdown */
            background-color: var(--md-sys-color-surface);
            border-radius: var(--md-border-radius);
            box-shadow: var(--md-elevation-2);
            padding: 4px 0;
        }
         div[data-baseweb="popover"] ul[role="listbox"] li {
            color: var(--md-sys-color-on-surface);
            padding: 8px 12px;
        }
        div[data-baseweb="popover"] ul[role="listbox"] li[aria-selected="true"] {
            background-color: var(--md-sys-color-primary-container);
            color: var(--md-sys-color-on-primary-container);
        }
         div[data-baseweb="popover"] ul[role="listbox"] li:hover {
            background-color: var(--md-sys-color-surface-variant);
        }


        /* Chat Input */
        [data-testid="stChatInput"] textarea {
            background-color: var(--md-sys-color-surface);
            border: 1px solid var(--md-sys-color-outline);
            border-radius: var(--md-border-radius-large); /* More rounded for chat */
            color: var(--md-sys-color-on-surface);
            padding: 10px 16px;
            box-shadow: var(--md-elevation-1);
            height: 40px;
        }
        [data-testid="stChatInput"] textarea::placeholder {
            color: var(--md-sys-color-on-surface-variant);
            opacity: 0.8;
        }
        [data-testid="stChatInput"] button { /* Send button */
            border-radius: 50% !important; /* Perfect circle */
            background-color: var(--md-sys-color-primary) !important;
            color: var(--md-sys-color-on-primary) !important;
            border: none !important;
            width: 32px !important; /* Smaller width */
            height: 32px !important; /* Smaller height */
            padding: 4px !important; /* Correct padding for the icon */
            margin-left: 8px;
            box-shadow: var(--md-elevation-1);
            /* Corrected transform to shift UP and further LEFT */
            transform: translateY(-4px) translateX(-8px);
            transition: background-color 0.2s ease, box-shadow 0.2s ease;
            z-index: 999;
        }
        [data-testid="stChatInput"] button:hover {
            background-color: #1A73E8 !important;
            box-shadow: var(--md-elevation-2);
        }
        [data-testid="stChatInput"] button:active {
            background-color: #1765C7 !important;
        }
        [data-testid="stChatInput"] button svg {
            fill: var(--md-sys-color-on-primary) !important;
            width: 24px; height: 24px;
        }


        /* Chat Messages */
        [data-testid="stChatMessage"] {
            padding: 10px 14px;
            border-radius: var(--md-border-radius-large); /* Chat bubble style */
            margin-bottom: 10px;
            box-shadow: var(--md-elevation-1);
            max-width: 85%; /* Max width for messages */
            border: none; /* Remove default streamlit border */
        }

        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-user"] {
            background-color: var(--md-sys-color-primary-container);
            margin-left: auto; /* Align user messages to the right */
        }
        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-user"] .stMarkdown p {
             color: var(--md-sys-color-on-primary-container);
        }

        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-model"],
        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-assistant"] {
            background-color: var(--md-sys-color-surface-container-highest); /* Use a slightly distinct surface for AI */
            margin-right: auto; /* Align AI messages to the left */
        }
        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-model"] .stMarkdown p,
        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-assistant"] .stMarkdown p {
             color: var(--md-sys-color-on-surface);
        }

        /* Avatar styling */
        [data-testid="stChatMessage"] [data-testid="stAvatar"] {
            border: none; /* Remove border */
            box-shadow: none;
            width: 36px;
            height: 36px;
        }
        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-user"] [data-testid="stAvatar"] > div {
            background-color: var(--md-sys-color-primary);
            color: var(--md-sys-color-on-primary);
        }
        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-model"] [data-testid="stAvatar"] > div,
        [data-testid="stChatMessage"][data-testid^="stChatMessageOutputContent-assistant"] [data-testid="stAvatar"] > div {
            background-color: var(--md-sys-color-secondary);
            color: var(--md-sys-color-on-secondary);
        }


        /* Expanders */
        [data-testid="stExpander"] {
            border: 1px solid var(--md-sys-color-outline);
            border-radius: var(--md-border-radius);
            margin-bottom: 1rem;
            background-color: var(--md-sys-color-surface);
            box-shadow: var(--md-elevation-1);
        }
        [data-testid="stExpander"] summary {
            padding: 12px 16px;
            font-weight: 500;
            font-size: 0.95rem;
            color: var(--md-sys-color-on-surface);
            background-color: transparent;
            border-radius: var(--md-border-radius) var(--md-border-radius) 0 0; /* Match top corners */
        }
        [data-testid="stExpander"] summary:hover {
             background-color: var(--md-sys-color-surface-variant);
        }
        [data-testid="stExpander"] [data-testid="stMarkdownContainer"] {
            padding: 0 16px 16px 16px;
        }


        /* Dataframes, JSON, Images, Charts in card-like style */
        .stImage, /* Streamlit uses class for st.image */
        [data-testid="stDataFrame"],
        [data-testid="stJson"],
        [data-testid="stVegaLiteChart"],
        [data-testid="stPlotlyChart"] { /* Add plotly if used */
            background-color: var(--md-sys-color-surface);
            padding: 16px;
            border-radius: var(--md-border-radius);
            box-shadow: var(--md-elevation-1);
            margin-bottom: 1rem;
            border: 1px solid var(--md-sys-color-outline);
        }
        [data-testid="stDataFrame"] table {
            width: 100%;
            border-collapse: collapse;
        }
        [data-testid="stDataFrame"] th, [data-testid="stDataFrame"] td {
            border: 1px solid var(--md-sys-color-outline);
            padding: 10px 12px; /* More padding */
            text-align: left;
            color: var(--md-sys-color-on-surface);
            font-size: 0.9rem;
        }
        [data-testid="stDataFrame"] th {
            background-color: var(--md-sys-color-surface-variant);
            color: var(--md-sys-color-on-surface-variant);
            font-weight: 500;
        }

        /* Spinner color */
        .stSpinner > div > div { /* The actual spinner element */
            border-top-color: var(--md-sys-color-primary) !important;
            border-right-color: var(--md-sys-color-primary) !important;
            border-bottom-color: var(--md-sys-color-primary) !important;
            border-left-color: transparent !important; /* Common spinner style */
        }
        .stSpinner > div:last-child { /* Spinner text */
            color: var(--md-sys-color-on-surface-variant);
            font-size: 0.9rem;
            margin-top: 8px;
        }

        /* Progress bar color */
        [data-testid="stProgressBar"] > div > div {
            background-color: var(--md-sys-color-primary);
        }

        /* TICKERS */
        /* Main container for each ticker row */
        .ticker-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.5rem 1rem;
            border-bottom: 1px solid #2c2c34;
            transition: background-color 0.2s ease-in-out;
        }
        .ticker-row:hover {
            background-color: #2c2c34;
            border-radius: 8px;
        }

        /* Left side: Ticker symbol and company name */
        .ticker-info {
            flex: 2; /* Takes up more space */
            min-width: 0;
        }
        .ticker-symbol {
            font-size: 1.1em;
            font-weight: bold;
            color: #FFFFFF;
            margin: 0;
            white-space: nowrap; /* Prevents symbol from wrapping */
        }
        .company-name {
            font-size: 0.85em;
            color: #a0a0b0; /* Lighter grey for subtitle */
            margin: 0;
            text-overflow: ellipsis; /* Adds '...' if name is too long */
        }

        /* Middle: Sparkline chart */
        .sparkline {
            flex: 2;
            text-align: center;
            padding: 0 10px;
            min-width: 0; /* << FIX: Robustness for all flex items */
        }
        .sparkline img {
            height: 30px;
            width: 100%;
            filter: brightness(1.2); /* Optional: make colors pop a bit more */
        }

        /* Right side: Price and percentage change */
        .price-info {
            flex: 1.5;
            text-align: right;
            min-width: 0; /* << FIX: Robustness for all flex items */
        }
        .price {
            font-size: 1.1em;
            font-weight: bold;
            color: #FFFFFF;
            margin: 0;
        }
        .change {
            font-size: 0.9em;
            margin-top: 2px;
            display: flex;
            align-items: center;
            justify-content: flex-end; /* Align items to the right */
        }
        .positive {
            color: #26A69A; /* Green from the image */
        }
        .negative {
            color: #EF5350; /* Red from the image */
        }

        /* Arrow icon styling */
        .arrow {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            color: white;
            font-size: 12px;
            font-weight: bold;
            margin-left: 8px;
            flex-shrink: 0;
        }
        .arrow-up {
            background-color: #26A69A;
        }
        .arrow-down {
            background-color: #EF5350;
            padding-top: 3px;
        }
    </style>
"""
st.markdown(material_theme_style, unsafe_allow_html=True)

st.markdown("""
<a href="/" target="_blank" style="color: var(--md-sys-color-on-primary-container); text-decoration: none;">
<h1><i class='material-icons' style="color: var(--md-sys-color-primary)">leaderboard</i> Chat with your Data</h1>
</a>
""".strip(), unsafe_allow_html=True)
st.subheader("This Agent can perform Data Analytics tasks "
             "over Salesforce data in BigQuery.")
st.markdown("[github.com/vladkol/crm-data-agent]"
            "(https://goo.gle/cloud-crm-data-agent?utm_campaign=CDR_0xc245fc42_default_b417442301&utm_medium=external&utm_source=blog)")
st.markdown("<h4>Examples of questions:</h4>", unsafe_allow_html=True)
st.markdown("""
<ul style="list-style: none; padding-left: 0;">
    <li><i class='material-icons'>trending_up</i> Lead conversion trends in the US.</li>
    <li><i class='material-icons'>campaign</i> What are our best lead sources?</li>
    <li><i class='material-icons'>leaderboard</i> Top 10 customers in every country. Make it a bar chart with filtering by country.</li>
    <li><i class='material-icons'>support_agent</i> How did our support team perform in 2021-2022?</li>
</ul>
""".strip(), unsafe_allow_html=True)

hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {
    display:none !important;
}
.block-container {
    padding-top: 2rem;
    padding-bottom: 5rem;
    padding-left: 2rem;
    padding-right: 2rem;
}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

######################### Tickers data #########################
# --- DATA FETCHING & HELPERS ---
@st.cache_data(ttl=300, show_spinner=False)  # Cache data for 5 minutes
def get_ticker_data(symbol: str):
    """Fetches historical and current data for a given ticker symbol."""
    try:
        with st.spinner(f"Getting {symbol} data..."):
            ticker = yf.Ticker(symbol)

            # Get historical data for the sparkline (last 7 days, 1-hour interval)
            hist = ticker.history(period="7d", interval="1h")
        if hist.empty:
            return None

        # Get data for price and change (last 2 days)
        daily_hist = ticker.history(period="2d")
        if len(daily_hist) < 2:
            # Fallback if not enough data for change calculation
            price = hist['Close'].iloc[-1]
            change = 0
            percent_change = 0
        else:
            price = daily_hist['Close'].iloc[-1]
            prev_close = daily_hist['Close'].iloc[-2]
            change = price - prev_close
            percent_change = (change / prev_close) * 100

        # Use the provided friendly name or fallback to the ticker info
        info = ticker.info
        company_name = info.get('longName', symbol.upper())
        symbol_display = info.get('symbol', symbol).replace('^', '')

        return {
            "symbol_display": symbol_display,
            "name": company_name,
            "history": hist['Close'],
            "price": price,
            "change": change,
            "percent_change": percent_change
        }
    except Exception as e:
        st.error(f"Could not fetch data for {symbol}", icon="⚠️")
        return None

######################### Event rendering #########################
# Add a callback function to handle feedback
async def handle_feedback(feedback_key: str, feedback_type: str):
    """
    This function is called when a feedback button is clicked.
    """
    with st.spinner(text="Sending..."):
        if feedback_type == "like":
            st.toast(
                "Great! Thanks for your feedback!",
                icon=":material/thumb_up:"
            )
        else:
            st.toast(
                "Thank you for the feedback!",
                icon=":material/thumb_down:"
            )
        await st.session_state.session_service.append_event(
            session=st.session_state.adk_session,
            event=Event(
                author="InternalUpdater",
                actions= EventActions(
                    state_delta={
                        f"{feedback_key}": feedback_type
                    }
                )
            )
        )

@st.fragment
def _process_function_calls(function_calls):
  title = f"⚡ {', '.join([fc.name for fc in function_calls])}"
  with st.expander(title):
    for fc in function_calls:
      title = f'**{fc.name}**'
      if fc.id:
        title += f' ({fc.id})'
      st.write(title)
      st.write(fc.args)


@st.fragment
def _process_function_responses(function_responses):
  title = f"✔️ {', '.join([fr.name for fr in function_responses])}"
  with st.expander(title):
    for fr in function_responses:
      title = f'**{fr.name}**'
      if fr.id:
        title += f' ({fr.id})'
      st.write(title)
      st.write(fr.response)


async def _process_event(event: Event) -> bool:
    if not event:
        return False
    session = st.session_state.adk_session
    artifact_service = st.session_state.artifact_service

    function_calls = []
    function_responses = []

    if event.content and event.content.parts:
        content = event.content
        for part in content.parts: # type: ignore
            if part.text and not part.text.strip():
                continue
            if part.thought and part.text:
                msg = '\n'.join('> %s' % f
                            for f in part.text.strip().split()) # type: ignore
            else:
                msg = part.text or ""
                msg = msg.strip()
            if part.function_call:
                function_calls.append(part.function_call)
            elif part.function_response:
                function_responses.append(part.function_response)
        if msg:
            if content.role == "model":
                msg_role = "ai"
            elif content.role == "user":
                msg_role = "human"
            else:
                msg_role = "assistant"
            with st.chat_message(
                msg_role,
                avatar=(":material/person:" if msg_role == "human"
                        else ":material/robot_2:")
            ):
                st.markdown(msg, unsafe_allow_html=True)
                # Add feedback buttons for AI responses
                if msg_role in ["ai", "assistant"]:
                    feedback_key = f"feedback_{event.id}"

                    feedback_status = None
                    feedback_states = session.state
                    if feedback_key in feedback_states:
                        feedback_status = feedback_states[feedback_key]

                    # Adjust column width for better spacing
                    col1, col2, _ = st.columns([0.05, 0.05, 0.9])
                    like_button_type = "secondary"
                    dislike_button_type = "secondary"

                    with col1:
                        if feedback_status == "like":
                            like_button_type = "primary"
                        if st.button(
                            "",
                            icon=":material/thumb_up:",
                            type=like_button_type,
                            key=f"like_{feedback_key}"
                        ):
                            feedback_status = "like"
                            await handle_feedback(feedback_key, "like")
                            st.rerun()

                    with col2:
                        if feedback_status == "dislike":
                            dislike_button_type = "primary"
                        if st.button(
                            "",
                            icon=":material/thumb_down:",
                            type=dislike_button_type,
                            key=f"dislike_{feedback_key}"
                        ):
                            feedback_status = "dislike"
                            await handle_feedback(feedback_key, "dislike")
                            st.rerun()





    if event.actions.artifact_delta:
        for filename, version in event.actions.artifact_delta.items():
            artifact = await artifact_service.load_artifact(
                app_name=session.app_name, user_id=session.user_id,
                session_id=session.id, filename=filename, version=version
            )
            if not artifact.inline_data or not artifact.inline_data.data:
                continue
            if (artifact.inline_data.mime_type.startswith('image/')):
                    # skip images with the invocation id filename
                    if filename.startswith(f"{event.invocation_id}."):
                        continue
                    with BytesIO(artifact.inline_data.data) as image_io:
                        with Image.open(image_io) as img:
                            st.image(img)
            elif (artifact.inline_data.mime_type ==
                        "application/vnd.vegalite.v5+json"
                  or filename.endswith(".vg")
                      and (artifact.inline_data.mime_type in
                            ["application/json", "text/plain"])
            ):
                # find a parquet file to supply the chart with data
                data_file_name = filename.rsplit(".", 1)[0] + ".parquet"
                parquet_file = await artifact_service.load_artifact(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id,
                    filename=data_file_name,
                    version=version)
                if parquet_file and parquet_file.inline_data:
                    pq_bytes = parquet_file.inline_data.data # type: ignore
                else:
                    pq_bytes = None
                text = artifact.inline_data.data.decode("utf-8")
                chart_dict = json.loads(text)
                if pq_bytes:
                    with BytesIO(pq_bytes) as pq_file:
                        df = pd.read_parquet(pq_file)
                    st.dataframe(df)
                    chart_dict.pop("data", None)
                else:
                    df = None
                st.vega_lite_chart(data=df,
                                    spec=chart_dict,
                                    use_container_width=False)
            elif artifact.inline_data.mime_type == "application/json":
                st.json(artifact.inline_data.data.decode("utf-8"))
            elif artifact.inline_data.mime_type == "text/markdown":
                st.markdown(artifact.inline_data.data.decode("utf-8"),
                            unsafe_allow_html=True)
            elif artifact.inline_data.mime_type == "text/x-sql":
                st.markdown("```sql\n" +
                            artifact.inline_data.data.decode("utf-8") +
                            "\n```\n",
                            unsafe_allow_html=True)
            elif artifact.inline_data.mime_type == "text/csv":
                st.markdown(
                    "```csv\n" +
                    artifact.inline_data.data.decode("utf-8") + "\n```",
                    unsafe_allow_html=True)
            elif artifact.inline_data.mime_type.startswith("text/"):
                st.text(artifact.inline_data.data.decode("utf-8"))

    if function_calls:
        _process_function_calls(function_calls)
    if function_responses:
        _process_function_responses(function_responses)
    return True


async def _render_chat(events):
    for event in events:
        await _process_event(event)


######################### Configuration management #########################

def _get_user_id() -> str:
    """Retrieves user id (email) from the environment
    for using with the session service

    Returns:
        str: user id for the session service
    """
    if "agent_user_id" in st.session_state:
        return st.session_state["agent_user_id"]

    user_id = st.context.headers.get(
            "X-Goog-Authenticated-User-Email", "").split(":", 1)[-1]
    if not user_id:
        try:
            user_id = (
                subprocess.check_output(
                    (
                        "gcloud config list account "
                        "--format \"value(core.account)\" "
                        f"--project {os.environ['GOOGLE_CLOUD_PROJECT']} "
                        "-q"
                    ),
                    shell=True,
                )
                .decode()
                .strip()
            )
        except subprocess.CalledProcessError:
            user_id = ""
    if not user_id:
            user_id = DEFAULT_USER_ID
    st.session_state["agent_user_id"] = user_id
    st.session_state["agent_user_name"] = user_id
    return user_id


async def _initialize_configuration():
    if "adk_configured" in st.session_state:
        return st.session_state.adk_configured
    agent_app_name = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID",
                                os.getenv("AGENT_NAME", DEFAULT_AGENT_NAME))
    vertex_ai_bucket = os.environ["AI_STORAGE_BUCKET"]
    session_service = SessionService(
          database=os.environ["FIRESTORE_SESSION_DATABASE"],
          sessions_collection=os.getenv("FIRESTORE_SESSION_COLLECTION", "/")
    )
    artifact_service = GcsArtifactService(
        bucket_name=vertex_ai_bucket
    )
    st.session_state.artifact_service = artifact_service
    st.session_state.session_service = session_service
    st.session_state.app_name = agent_app_name
    st.session_state.adk_configured = True
    st.session_state.last_prompt = ""



######################### Session management #########################

async def _create_session() -> Session:
    if "adk_session" not in st.session_state:
        session = await st.session_state.session_service.create_session(
            app_name=st.session_state.app_name,
            user_id=_get_user_id()
        )
        st.session_state.adk_session = session
        st.session_state.all_adk_sessions = (st.session_state.all_adk_sessions
                                             or [])
        st.session_state.all_adk_sessions.insert(0, session)
    return st.session_state.adk_session


async def _get_all_sessions() -> list[Session]:
    if "all_adk_sessions" in st.session_state:
        return st.session_state.all_adk_sessions
    sessions_response = await st.session_state.session_service.list_sessions(
            app_name=st.session_state.app_name,
            user_id=_get_user_id())
    sessions = sessions_response.sessions
    st.session_state.all_adk_sessions = sessions or []
    return sessions

### Watchlist ###

def create_sparkline_svg(data, color):
    fig, ax = plt.subplots(figsize=(4, 1))
    ax.plot(data.index, data.values, color=color, linewidth=2)
    ax.set_yticklabels([]); ax.set_xticklabels([])
    ax.tick_params(axis='both', which='both', length=0)
    for spine in ax.spines.values(): spine.set_visible(False)
    fig.patch.set_alpha(0.0); ax.patch.set_alpha(0.0)
    svg_buffer = BytesIO()
    fig.savefig(svg_buffer, format='svg', bbox_inches='tight', pad_inches=0, transparent=True)
    plt.close(fig)
    # << ENCODING STEP >> Encode the SVG to Base64
    svg_base64 = base64.b64encode(svg_buffer.getvalue()).decode("utf-8")
    return f"data:image/svg+xml;base64,{svg_base64}"

def load_watchlist():
    with open(os.path.join(os.path.dirname(__file__), "images/logo.svg")) as f:
        svg = base64.b64encode(f.read().encode("utf-8")).decode("utf-8")
        st.markdown(f"""
        <a href="/" target="_blank" style="align-items: center; color: var(--md-sys-color-primary); text-decoration: none">
        <h2><img style="transform: translateY(-4px);" width="64px" src='data:image/svg+xml;base64,{svg}' /> Enterprise Data Agent</h2>
        <hr/>
        </a>
        """.strip(), unsafe_allow_html=True)
    st.markdown("### Watchlist")
    for symbol in DEFAULT_TICKERS:
        data = get_ticker_data(symbol)

        if data:
            is_positive = data['change'] >= 0
            color = "#26A69A" if is_positive else "#EF5350"
            arrow_class = "arrow-up" if is_positive else "arrow-down"
            change_class = "positive" if is_positive else "negative"
            arrow_char = "▲" if is_positive else "▼"
            sparkline_uri = create_sparkline_svg(data['history'], color=color)

            # Create the HTML structure for one ticker row
            html = f"""
            <div class="ticker-row">
                <div class="ticker-info">
                    <p class="ticker-symbol">{data['symbol_display']}</p>
                    <p class="company-name">{data['name']}</p>
                </div>
                <div class="sparkline">
                    <img src="{sparkline_uri}" alt="Sparkline chart">
                </div>
                <div class="price-info">
                    <p class="price">{data['price']:.2f}</p>
                    <p class="change {change_class}">
                        {data['percent_change']:+.2f}%
                        <span class="arrow {arrow_class}">{arrow_char}</span>
                    </p>
                </div>
            </div>
            """
            st.html(html)

######################### Agent Request Handler #########################

async def ask_agent(question: str):
    start = time()
    session = st.session_state.adk_session
    st.session_state.last_prompt = question
    content = Content(parts=[
        Part.from_text(text=question)
    ],role="user")

    user_event = Event(author="user", content=content)
    await _render_chat([user_event])

    runtime_name = os.environ["RUNTIME_ENVIRONMENT"].lower()
    if runtime_name == "local":
        runtime = FastAPIEngineRuntime(session)
    else:
        ValueError(f"`{runtime_name}` is not a valid runtime name.")

    model_events_cnt = 0 # Count valid model events in this run
    await st.session_state.session_service.append_event(
        session=st.session_state.adk_session,
        event=Event(
            author="user",
            actions=EventActions(
                state_delta={
                    "RUNNING_QUERY": True,
                    "user_name": st.session_state.get("agent_user_name", "")
                }
            )
        )
    )
    try:
        st.session_state.thinking = True
        for _ in range(MAX_RUN_RETRIES):
            if model_events_cnt > 0:
                break
            async for event in runtime.stream_query(question):
                # If no valid model events in this run, but got an func call error,
                # retry the run
                if (event.error_code
                        and event.error_code == "MALFORMED_FUNCTION_CALL"
                        and model_events_cnt == 0):
                    print("Retrying the run")
                    break
                if event.content and event.content.role == "model":
                    model_events_cnt += 1
                await _render_chat([event])
        await st.session_state.session_service.append_event(
            session=st.session_state.adk_session,
            event=Event(
                author="user",
                actions=EventActions(
                    state_delta={
                        "RUNNING_QUERY": False
                    }
                )
            )
        )
        # Re-retrieve the session
        st.session_state.adk_session = await st.session_state.session_service.get_session(
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session.id
        )
    finally:
        st.session_state.thinking = False
    end = time()
    st.text(f"Flow duration: {end - start:.2f}s")


######################### Streamlit main flow #########################

async def app():
    top = st.container()

    if "adk_configured" not in st.session_state:
        with st.spinner("Initializing...", show_time=False):
            await _initialize_configuration()
    sessions_list = await _get_all_sessions()
    session_ids = [s.id for s in sessions_list]
    session_service = st.session_state.session_service
    current_session = None
    current_index = 0
    if "session" in st.query_params:
        selected_session_id = st.query_params["session"]
        if selected_session_id in session_ids:
            selected_session_id = st.query_params["session"]
            if (
                "adk_session" in st.session_state
                and st.session_state.adk_session.id != selected_session_id
            ):
                st.session_state.pop("adk_session")
            current_index = session_ids.index(selected_session_id)
        else:
            st.query_params.pop("session")
            selected_session_id = -1
    else:
        selected_session_id = -1
    if "adk_session" in st.session_state:
        current_session = st.session_state.adk_session
    elif selected_session_id != -1:
        selected_session = sessions_list[current_index]
        with st.spinner("Loading...", show_time=False):
            current_session = await session_service.get_session(
                app_name=selected_session.app_name,
                user_id=selected_session.user_id,
                session_id=selected_session.id
            )
            st.session_state.adk_session = current_session

    if not current_session:
        with st.spinner("Creating a new session...", show_time=False):
            current_session = await _create_session()
        st.session_state.adk_session = current_session
        st.rerun()
    else:
        st.query_params["session"] = current_session.id

    with st.sidebar:
        load_watchlist()
        with st.popover("Sessions"):
            if st.button("New Session"):
                st.query_params["session"] = "none"
                st.session_state.pop("adk_session", None)
                st.rerun()

            sessions_list = st.session_state.all_adk_sessions
            session_ids = [s.id for s in sessions_list]
            selected_option = st.selectbox("Select a session:",
                                        session_ids,
                                        index=current_index)
            if selected_option and selected_option != current_session.id: # type: ignore
                with st.spinner("Loading...", show_time=False):
                    st.query_params["session"] = selected_option
                    st.rerun()
    with top:
        await _render_chat(st.session_state.adk_session.events) # type: ignore
    with st.spinner("Thinking...", show_time=False):
        question = st.chat_input(
            "Ask a question about your data.",
            disabled=st.session_state.get("thinking", False)
        )
        if "question" not in current_session.state:
            current_session.state["question"] = question
        with top:
            with st.spinner("Thinking...", show_time=True):
                if question:
                    await ask_agent(question)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(app())
