import os
import streamlit as st
from datetime import datetime
from interface.components.cards import parse_and_render_invoice, render_image_bubble

def render_chat_header(biz_name: str = os.getenv("DEFAULT_BIZ_NAME", "Inventaa LED Lights")):
    """
    Renders the WhatsApp Web style top bar with avatar, status, and names.
    """
    st.markdown(
        f"""
        <div class="whatsapp-header">
            <div class="whatsapp-header-avatar">
                {biz_name[0] if biz_name else 'A'}
            </div>
            <div class="whatsapp-header-details">
                <div class="whatsapp-header-title">{biz_name}</div>
                <div class="whatsapp-header-status">🟢 online</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

def render_chat_messages():
    """
    Renders the list of user and bot messages using custom HTML bubbles and card renderers.
    """
    if "messages" not in st.session_state:
        st.session_state.messages = []
        
    # Render system/greeting indicator if chat is empty
    if not st.session_state.messages:
        st.markdown(
            """
            <div style="display: flex; justify-content: center; margin: 15px 0;">
                <div style="background-color: #ffe596; color: #111b21; padding: 5px 12px; border-radius: 6.5px; font-size: 11.5px; box-shadow: 0 1px 0.5px rgba(0,0,0,0.13);">
                    🔒 Messages are end-to-end encrypted. No real WhatsApp charges apply.
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        return

    # Loop through session messages and render
    for msg in st.session_state.messages:
        role = msg.get("role")
        
        if role == "user":
            text = msg.get("text", "")
            st.markdown(
                f"""
                <div style="display: flex; flex-direction: column; align-items: flex-end; margin-bottom: 8px; clear: both;">
                    <div class="message-bubble message-user">
                        {text}
                        <span class="message-time">{datetime.now().strftime("%H:%M")}</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            
        elif role == "assistant":
            replies = msg.get("replies", [])
            for reply in replies:
                rtype = reply.get("type")
                
                if rtype == "text":
                    body = reply.get("body", "")
                    
                    # Try to parse and render invoice card first
                    if parse_and_render_invoice(body):
                        continue
                        
                    # Otherwise render standard text bubble with markdown support
                    st.markdown(
                        f"""
                        <div style="display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 8px; clear: both;">
                            <div class="message-bubble message-bot">
                                <div style="white-space: pre-wrap;">{body}</div>
                                <span class="message-time">{datetime.now().strftime("%H:%M")}</span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                    
                elif rtype == "image":
                    render_image_bubble(reply.get("link"), reply.get("caption", ""))
                    
            # Render debug toggle details inside conversation bubble if enabled and developer mode
            debug = msg.get("debug")
            if debug and st.session_state.get("dev_mode"):
                st.markdown(
                    f"""
                    <div style="display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 8px; clear: both; margin-left: 20px;">
                        <div class="debug-container" style="max-width: 60%; padding: 8px 12px; border-radius: 6px;">
                            <strong>🔍 Pipeline Debug</strong><br/>
                            • Intent: {debug.get('intent')}<br/>
                            • Latency: {debug.get('latency')}s<br/>
                            • Route: {debug.get('route')}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )