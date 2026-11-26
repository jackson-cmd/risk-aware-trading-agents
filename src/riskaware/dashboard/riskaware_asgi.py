"""The dashboard as an ASGI app: Streamlit's ``st.App`` running app.py, with the phone-access gate in front of every
page, file and WebSocket (see riskaware/phone.py). ``riskaware dashboard`` starts this file with ``streamlit run``,
which finds ``app`` below and serves it with uvicorn."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
from starlette.middleware import Middleware

from riskaware import phone

phone.GATE["active"] = True

app = st.App(str(Path(__file__).with_name("app.py")), middleware=[Middleware(phone.PhoneGate)])
