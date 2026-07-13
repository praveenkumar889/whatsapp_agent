import streamlit as st

def render_typing_indicator():
    """
    Renders a WhatsApp-like typing indicator with bouncing dots.
    """
    st.markdown(
        """
        <div style="display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 8px; clear: both;">
            <div class="message-bubble message-bot" style="padding: 12px 16px;">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )
