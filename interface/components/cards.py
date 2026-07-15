import os
import re
import streamlit as st

def parse_and_render_invoice(text: str) -> bool:
    """
    Checks if a text message is an invoice delivery.
    If yes, extracts the PDF link and order ID, and renders a WhatsApp-style Invoice Card.
    Returns True if rendered as a card, False otherwise.
    """
    if "Download Invoice PDF" in text or ".pdf" in text:
        # Try to extract the URL
        url_match = re.search(r'(https?://[^\s]+)', text)
        # Try to extract order ID (e.g. *ORD_...* or *INV_...*)
        order_match = re.search(r'\*(ORD_[a-f0-9]+|\d+)\*', text)
        
        if url_match:
            pdf_url = url_match.group(1).rstrip('.')
            order_id = order_match.group(1) if order_match else "INV-ORDER"
            
            # Extract business name if present
            biz_match = re.search(r'business with \*([^*]+)\*', text)
            biz_name = biz_match.group(1) if biz_match else os.getenv("DEFAULT_BIZ_NAME", "Inventaa LED Lights")
            
            st.markdown(
                f"""
                <div class="invoice-card" style="font-family: inherit;">
                    <div class="invoice-header">
                        <span>📄 TAX INVOICE</span>
                        <span>{order_id}</span>
                    </div>
                    <div style="margin-bottom: 12px; font-size: 13.5px; color: #111b21;">
                        Your tax invoice from <strong>{biz_name}</strong> is generated and ready for download.
                    </div>
                    <div style="display: flex; gap: 10px; margin-top: 10px;">
                        <a href="{pdf_url}" target="_blank" style="
                            background-color: #008069;
                            color: white;
                            padding: 8px 16px;
                            border-radius: 4px;
                            text-decoration: none;
                            font-size: 12.5px;
                            font-weight: 600;
                            display: inline-block;
                            box-shadow: 0 1px 3px rgba(0,0,0,0.12);
                        ">📥 Download PDF</a>
                        <a href="{pdf_url}" target="_blank" style="
                            background-color: #ffffff;
                            color: #008069;
                            border: 1px solid #008069;
                            padding: 8px 16px;
                            border-radius: 4px;
                            text-decoration: none;
                            font-size: 12.5px;
                            font-weight: 600;
                            display: inline-block;
                        ">👁️ View Online</a>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            return True
            
    return False

def render_image_bubble(img_url: str, caption: str):
    """
    Renders an image message in a WhatsApp-style bubble container.
    """
    st.markdown(
        f"""
        <div style="display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 8px; clear: both;">
            <div class="message-bubble message-bot" style="padding: 4px; max-width: 320px;">
                <img src="{img_url}" style="width: 100%; border-radius: 6px; display: block;" />
                {f'<div style="padding: 8px 10px 4px 10px; font-size: 13.5px; white-space: pre-wrap; color: #111b21;">{caption}</div>' if caption else ''}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )