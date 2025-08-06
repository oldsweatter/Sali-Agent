import json
import requests
import os
import config as config
from flask import Flask, request, jsonify, session
from flask_cors import CORS
import base64
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.ai.projects import AIProjectClient
from azure.search.documents import SearchClient
import azure.cognitiveservices.speech as speechsdk



#Global clients (initialize once)
try: 
    project_client = AIProjectClient(
        credential=DefaultAzureCredential(),
        endpoint="https://salihub.services.ai.azure.com/api/projects/saliproject"
    )
    conversation_thread = project_client.agents.threads.create()
    print("--- Azure AI Clients Initialized Successfully ---")
except Exception as e:
    print(f"FATAL ERROR: Failed to initialize Azure AI clients. The application will not work. Error: {e}")

starting_number = "6"
   
#---------------HELPER FUNCTIONS------------------

# transform transport number string to base64 representation
def encode_azure_key(readable_key: str) -> str:
    """
    Encodes a readable string into the Base64 format used by Azure Search keys.
    """
    if not readable_key:
        return ""
    
    # Convert the string to bytes, then encode it
    key_bytes = readable_key.encode('utf-8')
    encoded_bytes = base64.b64encode(key_bytes)
    
    # Convert the encoded bytes back to a string and remove any '=' padding
    encoded_string = encoded_bytes.decode('utf-8').rstrip('=')
    
    return encoded_string

def decode_azure_key(encoded_key: str) -> str:
    """Decodes a Base64 string from Azure AI Search, handling padding issues."""
    if not encoded_key: return "Not Available"
    missing_padding = len(encoded_key) % 4
    if missing_padding: encoded_key += '=' * (4 - missing_padding)
    try:
        return base64.b64decode(encoded_key).decode('utf-8')
    except Exception:
        return encoded_key

def search_knowledge_base(query: str) -> str:
    """
    Searches the Azure AI Search index for relevant information.
    """
    try:
        # Überprüfen, ob die Konfiguration vorhanden ist.
        if not all([config.SEARCH_ENDPOINT, config.SEARCH_INDEX_NAME, config.SEARCH_API_KEY]):
            return "Wissensbasis ist nicht konfiguriert."

        search_client = SearchClient(
            endpoint=config.SEARCH_ENDPOINT,
            index_name=config.SEARCH_INDEX_NAME,
            credential=AzureKeyCredential(config.SEARCH_API_KEY)
        )
        
        encoded_query = encode_azure_key(query)
        results = list(search_client.search(search_text=encoded_query, top=1))

        # WICHTIGE PRÜFUNG: Nur fortfahren, wenn Ergebnisse vorhanden sind.
        if not results:
            return "Keine Informationen zu dieser Transportnummer gefunden."

        # Sicherer Zugriff auf das Ergebnis
        record = results[0]
        
        if 'elo_transport' in record:
            record['elo_transport'] = decode_azure_key(record['elo_transport'])

        # Formatieren und zurückgeben der Daten
        row_data = ", ".join(
            f"{key}: {value}" for key, value in record.items() 
            if value is not None and not key.startswith(('@', 'metadata_', 'AzureSearch_'))
        )
        return f"--- Kontext aus der Wissensbasis ---\nGefundener Datensatz: {row_data}\n---------------------------------"

    except Exception as e:
        print(f"Fehler bei der Suche in der Wissensbasis: {e}")
        return "Fehler beim Zugriff auf die Wissensbasis."

def speak_text(text_to_speak):
    print(f"SALI (speaking): {text_to_speak}")
    speech_config = speechsdk.SpeechConfig(subscription=config.SPEECH_KEY, region= config.SPEECH_REGION)
    speech_config.speech_synthesis_voice_name = "de-AT-Ingrid" # An Austrian voice
    speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)
    result = speech_synthesizer.speak_text_async(text_to_speak).get()
    if result.reason == speechsdk.ResultReason.Canceled:
        cancellation_details = result.cancellation_details
        print(f"Speech synthesis canceled: {cancellation_details.reason}")
        if cancellation_details.reason == speechsdk.CancellationReason.Error:
            print(f"Error details: {cancellation_details.error_details}")





#-----------------------MAIN LOGIC FUNCTION-----------------------------------
app = Flask(__name__)
CORS(app)
app.secret_key = os.urandom(24) 

@app.route('/api/get-speech-token', methods=['POST'])
def get_speech_token():
    """Fetches a temporary authorization token for the Speech service."""
    speech_key = os.environ.get('SPEECH_KEY')
    speech_region = os.environ.get('SPEECH_REGION')

    if not speech_key or not speech_region:
        return jsonify({"error": "Speech service credentials not configured."}), 500

    token_url = f"https://{speech_region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    headers = {
        'Ocp-Apim-Subscription-Key': speech_key
    }
    
    try:
        response = requests.post(token_url, headers=headers)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        # Return the token and region to the frontend
        return jsonify({
            "token": response.text,
            "region": speech_region
        })
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500



@app.route('/')
def index():
    return  app.send_static_file('frontend_agent.html')

@app.route('/chat', methods=['POST'])

def chat():
    
    if not project_client: 
        return jsonify({"reply": "Error: AI services are not initialized. Please check the server logs."}), 500
    
    #get the users current thread_id:
    thread_id = session.get('thread_id')
    
    if not thread_id:
        print("Creating a new conversation thread.")
        new_thread = project_client.agents.threads.create()
        session['thread_id'] = new_thread.id
        thread_id = new_thread.id

    user_input = request.json.get('message')
    language = request.json.get('language', 'en-US')

    
    if not user_input:
        return jsonify({"error": "No message was provided."}), 400

    # --- RAG LOGIC ---
    cleaned_input = user_input.replace(" ", "")
    is_transport_number = cleaned_input.isdigit() and len(user_input) == 8

    if is_transport_number:
        knowledge = search_knowledge_base(user_input)
        augmented_prompt = (
            f"{knowledge}\n\nAnweisung: Der Benutzer hat nach der Transportnummer '{user_input}' gefragt. "
            f"Fasse die Informationen aus dem 'Gefundener Datensatz' klar und einfach zusammen."
        )
    else:
        augmented_prompt = f"{user_input}\n\nAnweisung: Antworte IMMER in der Sprache mit dem Code: {language}."
    
    # --- AGENT INTERACTION ---
    try:
        project_client.agents.messages.create(thread_id=thread_id, role="user", content=augmented_prompt)
        run = project_client.agents.runs.create_and_process(thread_id=thread_id, agent_id=config.AGENT_ID)

        response_text = "I seem to be having trouble. Please try again."
        if run.status == "completed":
            messages_iterator = project_client.agents.messages.list(thread_id=thread_id, limit=1, order="desc")
            try:
                latest_message = next(messages_iterator)
                if latest_message.role == "assistant" and latest_message.text_messages:
                    response_text = latest_message.text_messages[0].text.value
            except StopIteration:
                response_text = "No new message was received from the agent."
        else:
            response_text = f"The agent run failed with status: {run.status}."
            if run.last_error: 
                response_text += f" Error: {run.last_error.message}"
        
        return jsonify({"reply": response_text})

    except Exception as e:
        print(f"Error during agent interaction: {e}")
        return jsonify({"reply": "An error occurred while communicating with the agent."}), 500



if __name__ == "__main__":
    app.run(debug = True)