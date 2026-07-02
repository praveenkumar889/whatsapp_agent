import streamlit as st
from interface.api import reset_session_on_backend

def render_sidebar():
    """
    Renders the Streamlit sidebar containing customer configurations,
    action buttons, and the developer/debug mode panel.
    """
    st.sidebar.markdown(
        """
        <div style="text-align: center; padding: 10px 0;">
            <h2 style="color: #008069; margin: 0; font-size: 22px;">💬 AI Workforce Demo</h2>
            <p style="color: #667781; font-size: 13px; margin: 5px 0 15px 0;">WhatsApp AI Operations Simulator</p>
        </div>
        """, 
        unsafe_allow_html=True
    )
    
    st.sidebar.markdown("### 👤 Customer Profile")
    
    # Store customer details in session state
    if "customer_name" not in st.session_state:
        st.session_state.customer_name = "Praveen"
    if "customer_phone" not in st.session_state:
        st.session_state.customer_phone = "918897726664"
        
    customer_name = st.sidebar.text_input("Name", value=st.session_state.customer_name)
    customer_phone = st.sidebar.text_input("Phone Number (E.164)", value=st.session_state.customer_phone)
    
    # Update session state when inputs change
    st.session_state.customer_name = customer_name
    st.session_state.customer_phone = customer_phone

    # Reset button
    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⚙️ Session Control")
    
    if st.sidebar.button("🔄 Reset Conversation", use_container_width=True):
        with st.spinner("Clearing database history..."):
            success = reset_session_on_backend(customer_phone)
            if success:
                st.session_state.messages = []
                st.session_state.debug_info = None
                st.sidebar.success("Conversation cleared successfully!")
                st.rerun()
            else:
                st.sidebar.error("Failed to reset session on backend.")

    # Developer Mode toggle
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🛠️ Developer Options")
    dev_mode = st.sidebar.toggle("Enable Developer Mode", value=True)
    st.session_state.dev_mode = dev_mode

    # Debug Panel
    if dev_mode:
        st.sidebar.markdown("### 🔍 Live Debug Panel")
        
        debug = st.session_state.get("debug_info")
        if debug:
            st.sidebar.markdown(
                f"""
                <div style="background-color: #ffffff; border: 1px solid #dee2e6; border-radius: 6px; padding: 12px; font-family: monospace; font-size: 12px; color: #111b21;">
                    <div style="margin-bottom: 6px;"><strong>Intent:</strong> <span style="color: #008069;">{debug.get('intent', 'UNKNOWN')}</span></div>
                    <div style="margin-bottom: 6px;"><strong>Confidence:</strong> {debug.get('confidence', 0.0):.2f}</div>
                    <div style="margin-bottom: 6px;"><strong>Latency:</strong> {debug.get('latency', 0.0)}s</div>
                    <div style="margin-bottom: 6px;"><strong>Route:</strong> <span style="color: #0288d1;">{debug.get('route', 'None')}</span></div>
                    <div><strong>Tenant ID:</strong> {debug.get('tenant_id', 'None')}</div>
                </div>
                """,
                unsafe_allow_html=True
            )
        else:
            st.sidebar.info("Send a message to see pipeline telemetry.")
            
    st.sidebar.markdown(
        """
        <div style="margin-top: 30px; text-align: center; font-size: 11px; color: #667781;">
            Powered by Gemini 3.5 & FastAPI
        </div>
        """,
        unsafe_allow_html=True
    )
