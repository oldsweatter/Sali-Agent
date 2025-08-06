import os
from dotenv import load_dotenv

# This line loads variables from a .env file for local development
load_dotenv()

# --- Load secrets and settings from environment variables ---
SPEECH_KEY = os.environ.get("SPEECH_KEY")
SPEECH_REGION = os.environ.get("SPEECH_REGION")

SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT")
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY")

AGENT_ID = os.environ.get("AGENT_ID")