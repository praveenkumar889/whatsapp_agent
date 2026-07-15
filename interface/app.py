import os
import sys
import streamlit as st
import time

# Ensure the root directory is in python path so that 'interface' imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set up page configurations
st.set_page_config(
    page_title="WhatsApp AI Assistant Simulator",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded"
)

from interface.styles.css import inject_whatsapp_css
from interface.components.sidebar import render_sidebar
from interface.components.chat import render_chat_header, render_chat_messages
from interface.components.typing import render_typing_indicator
from interface.api import send_message_to_backend

# 1. Inject WhatsApp styles
inject_whatsapp_css()

# 2. Render sidebar options
render_sidebar()

# Initialize session state lists
if "messages" not in st.session_state:
    st.session_state.messages = []
if "debug_info" not in st.session_state:
    st.session_state.debug_info = None

# Chat area container
st.markdown('<div class="chat-container">', unsafe_allow_html=True)

# 3. Render top contact bar
render_chat_header(biz_name=os.getenv("DEFAULT_BIZ_NAME", "Inventaa LED Lights"))

# 4. Render active conversation bubbles
render_chat_messages()

# Close container
st.markdown('</div>', unsafe_allow_html=True)

# 5. Capture bottom chat input
user_input = st.chat_input("Type a message...")

if user_input:
    # Append user message
    st.session_state.messages.append({
        "role": "user",
        "text": user_input
    })
    
    # Rerun to render user message immediately
    st.rerun()

# 6. If user just sent a message and bot has not replied yet, trigger backend call
if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
    last_user_message = st.session_state.messages[-1]["text"]
    
    # Render bouncing typing dots
    render_typing_indicator()
    
    # Run the background call to local FastAPI backend
    response = send_message_to_backend(
        phone=st.session_state.customer_phone,
        message=last_user_message,
        sender_name=st.session_state.customer_name
    )
    
    if response:
        # Save telemetry details to populate debug dashboard
        st.session_state.debug_info = response.get("debug")
        
        # Append assistant replies
        st.session_state.messages.append({
            "role": "assistant",
            "replies": response.get("replies", []),
            "debug": response.get("debug")
        })
        
        # Trigger reload to display bot replies
        st.rerun()