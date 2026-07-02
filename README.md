# WhatsApp Agent (whatsapp_bot)

A powerful WhatsApp agent/bot codebase built with Python, FastAPI, and Streamlit, featuring intent classification, dynamic prompt management, and persistent memory.

## Key Features

- **FastAPI Backend**: Handles incoming webhook requests from WhatsApp Cloud API.
- **Streamlit Interface**: Interactive web interface for monitoring conversation logs, session states, and configuring prompts/settings.
- **AI Engine**: Core handler structure for orchestrating responses using Gemini, managing context window limits, and utilizing entity extraction.
- **Persistent Memory**: Dynamic database store for session context and history.

## Project Structure

- `main.py`: FastAPI server entrypoint.
- `interface/`: Streamlit dashboard and API components.
- `ai/`: Intent handlers, negotiant logic, context/prompt builders, and memory manager.
- `db/`: Memory store, prompt store, session store, and locks.
- `models/`: Schema definitions.
- `pipeline/`: Routing and setup pipelines.
- `adapter/`: WhatsApp API integration.
```
