import streamlit as st

def inject_whatsapp_css():
    """
    Injects custom CSS to style Streamlit to look exactly like WhatsApp Web.
    """
    st.markdown(
        """
        <style>
        /* Hide streamlit header and footer */
        header {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* Main chat area background - WhatsApp Web grey-cream */
        .stApp {
            background-color: #efeae2 !important;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }

        /* Container styles */
        .chat-container {
            max-width: 800px;
            margin: 0 auto;
            padding: 10px;
            display: flex;
            flex-direction: column;
        }

        /* WhatsApp Header styling */
        .whatsapp-header {
            background-color: #008069;
            color: white;
            padding: 10px 16px;
            display: flex;
            align-items: center;
            border-radius: 8px 8px 0 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            margin-bottom: 12px;
            font-family: inherit;
        }
        
        .whatsapp-header-avatar {
            width: 40px;
            height: 40px;
            background-color: #e1f5fe;
            color: #0288d1;
            font-weight: bold;
            font-size: 18px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-right: 12px;
        }

        .whatsapp-header-details {
            display: flex;
            flex-direction: column;
        }

        .whatsapp-header-title {
            font-weight: 600;
            font-size: 15px;
            margin: 0;
            line-height: 1.2;
        }

        .whatsapp-header-status {
            font-size: 12px;
            margin: 0;
            opacity: 0.85;
            color: #d1f4cc;
        }

        /* Chat bubbles */
        .message-bubble {
            max-width: 65%;
            padding: 8px 12px;
            margin-bottom: 8px;
            border-radius: 7.5px;
            font-size: 14.2px;
            line-height: 1.4;
            position: relative;
            word-wrap: break-word;
            box-shadow: 0 1px 0.5px rgba(0,0,0,0.13);
            font-family: inherit;
        }

        /* Bot replies - left aligned, white */
        .message-bot {
            align-self: flex-start;
            background-color: #ffffff;
            color: #111b21;
            border-top-left-radius: 0px;
            float: left;
            clear: both;
        }

        /* User messages - right aligned, light green */
        .message-user {
            align-self: flex-end;
            background-color: #d9fdd3;
            color: #111b21;
            border-top-right-radius: 0px;
            float: right;
            clear: both;
        }

        /* Bubble metadata/timestamp */
        .message-time {
            font-size: 10px;
            color: #667781;
            float: right;
            margin-top: 4px;
            margin-left: 8px;
            user-select: none;
        }

        /* Debug container inside bubble */
        .debug-container {
            font-size: 11px;
            font-family: monospace;
            background-color: #f0f2f5;
            border-radius: 4px;
            padding: 6px;
            margin-top: 8px;
            border-left: 3px solid #008069;
            color: #3b3b3b;
        }

        /* Styled Product card */
        .product-card {
            border: 1px solid #e1e9eb;
            border-radius: 8px;
            padding: 0;
            background-color: #ffffff;
            margin-top: 8px;
            margin-bottom: 8px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }
        
        .product-card-img {
            width: 100%;
            height: 160px;
            object-fit: cover;
            border-bottom: 1px solid #e1e9eb;
        }

        .product-card-body {
            padding: 10px 12px;
        }

        .product-card-title {
            font-weight: bold;
            font-size: 14px;
            color: #111b21;
            margin: 0 0 4px 0;
        }

        .product-card-price {
            color: #008069;
            font-weight: 700;
            font-size: 15px;
            margin: 0 0 8px 0;
        }
        
        .product-card-rating {
            font-size: 12px;
            color: #ffb300;
            margin: 0;
        }

        /* Invoice styling */
        .invoice-card {
            background-color: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 14px;
            margin-top: 8px;
            margin-bottom: 8px;
        }

        .invoice-header {
            font-weight: 600;
            font-size: 14px;
            color: #008069;
            border-bottom: 1px solid #dee2e6;
            padding-bottom: 6px;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .invoice-row {
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            margin-bottom: 4px;
        }

        .invoice-total {
            border-top: 1px dashed #dee2e6;
            padding-top: 6px;
            margin-top: 6px;
            font-weight: bold;
            display: flex;
            justify-content: space-between;
            font-size: 14px;
            color: #111b21;
        }

        /* Typing indicator dots */
        .typing-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: #667781;
            animation: bounce 1.3s infinite ease-in-out;
            margin-right: 3px;
        }
        
        .typing-dot:nth-child(2) { animation-delay: 0.15s; }
        .typing-dot:nth-child(3) { animation-delay: 0.3s; }

        @keyframes bounce {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-4px); }
        }
        </style>
        """,
        unsafe_allow_html=True
    )
