import os
import asyncio
import json
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import google.generativeai as genai
import edge_tts
import requests
from dotenv import load_dotenv

# Load environment variables from the .env file securely
load_dotenv()

# Initialize FastAPI application
app = FastAPI()

# Securely retrieve API keys from environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OBSIDIAN_API_KEY = os.getenv("OBSIDIAN_API_KEY")
OBSIDIAN_API_URL = os.getenv("OBSIDIAN_API_URL", "https://127.0.0.1:27124")

# Validate essential keys are present before starting the architecture
if not GEMINI_API_KEY or not OBSIDIAN_API_KEY:
    raise ValueError("CRITICAL ERROR: GEMINI_API_KEY or OBSIDIAN_API_KEY is missing in the .env file!")

# Configure Google Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Setup the Gemini model with the interviewer persona
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction=(
        "You are a personal AI interviewer. Your task is to talk to the user, "
        "ask deep and engaging questions about their day, programming projects, "
        "and studies. Extract valuable thoughts and format them as markdown notes "
        "with double brackets [[like this]] for Obsidian integration. "
        "Keep your conversational responses short, natural, and friendly."
    )
)
# Start a persistent chat session to maintain conversation history
#chat_session = model.start_chat(history=[])

# Settings for Text-to-Speech
VOICE = "ru-RU-DmitryNeural"

# Request headers for Obsidian REST API authentication
OBSIDIAN_HEADERS = {
    "Authorization": f"Bearer {OBSIDIAN_API_KEY}",
    "Content-Type": "text/markdown"
}


def save_to_obsidian_via_api(content: str) -> bool:
    """Saves extracted insights directly into Obsidian vault via Local REST API."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # Automated note creation within an 'AI_Inbox' folder of your vault
    note_path = f"/AI_Inbox/AI_Note_{timestamp}.md"
    url = f"{OBSIDIAN_API_URL}/vault{note_path}"

    markdown_body = f"# AI Note {timestamp}\n\n{content}"

    try:
        # verify=False ignores local self-signed SSL certificates produced by the plugin
        response = requests.put(url, headers=OBSIDIAN_HEADERS, data=markdown_body.encode('utf-8'), verify=False)
        if response.status_code in [200, 201]:
            print(f"Successfully synced note to Obsidian via API: {note_path}")
            return True
        else:
            print(f"Obsidian API error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Failed to connect to Obsidian Local REST API: {e}")
        return False


async def generate_speech(text: str, output_path: str):
    """Converts text to speech using Microsoft Edge TTS asynchronously."""
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(output_path)


@app.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    """Handles continuous WebSocket connection for real-time AI communication."""
    await websocket.accept()
    print("Client connected via WebSocket")

    # ДОБАВЬ СТРОКУ СЮДА:
    chat_session = model.start_chat(history=[])

    try:
        while True:
            # 1. Receive text payload from the client application
            data = await websocket.receive_text()
            message_data = json.loads(data)
            user_text = message_data.get("text", "")

            if not user_text:
                continue

            print(f"User said: {user_text}")

            # 2. Generate response from Gemini
            response = chat_session.send_message(user_text)
            ai_text = response.text
            print(f"AI responded: {ai_text}")

            # 3. Check if the response contains markdown links for Obsidian
            if "[[" in ai_text or "]]" in ai_text:
                # Offload blocking HTTP requests to a separate thread to prevent UI freezing
                asyncio.to_thread(save_to_obsidian_via_api, ai_text)

            # 4. Generate audio file for the AI's response
            audio_filename = f"temp_response_{datetime.now().timestamp()}.mp3"
            await generate_speech(ai_text, audio_filename)

            # 5. Read the generated audio file into bytes
            with open(audio_filename, "rb") as f:
                audio_bytes = f.read()

            # 6. Send text metadata and binary audio stream back to the client
            await websocket.send_text(json.dumps({"type": "text", "content": ai_text}))
            await websocket.send_bytes(audio_bytes)

            # 7. Cleanup the temporary audio file
            if os.path.exists(audio_filename):
                os.remove(audio_filename)

    except WebSocketDisconnect:
        print("Client disconnected from WebSocket")
    except Exception as e:
        print(f"An error occurred in the WebSocket loop: {e}")
        if not websocket.client_state.name == "DISCONNECTED":
            await websocket.close()


if __name__ == "__main__":
    import uvicorn
    import urllib3

    # Mute local insecure certificate warnings caused by self-signed SSL
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Start FastAPI development server
    uvicorn.run(app, host="0.0.0.0", port=8000)