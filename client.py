import os
import sys
import asyncio
import json
import threading
import speech_recognition as sr
from PySide6.QtCore import Qt, Signal, Slot, QThread
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QTextEdit, QWidget, QLabel
import websockets

# WebSocket server endpoint URL
SERVER_URL = "ws://127.0.0.1:8000/ws/chat"


class AudioBackendWorker(QThread):
    """
    Background worker thread handling real-time microphone capture,
    local voice transcription, and active WebSocket transmission.
    """
    text_received = Signal(str, str)  # Signals UI to append (sender, text)
    status_changed = Signal(str)  # Signals UI to update status label

    def __init__(self):
        super().__init__()
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.is_running = True
        self.loop = None

        # Calibrate microphone for background room noise dynamically
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)

    def run(self):
        # Establish a dedicated asynchronous event loop for WebSockets within this thread
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.network_loop())

    async def network_loop(self):
        self.status_changed.emit("Connecting to AI Brain server...")
        try:
            async with websockets.connect(SERVER_URL) as websocket:
                self.status_changed.emit("Connected. Start speaking naturally...")

                # Run the continuous server reader task concurrently
                listen_task = asyncio.create_task(self.receive_from_server(websocket))

                while self.is_running:
                    # Capture and transcribe voice input without freezing the socket
                    user_speech_text = await asyncio.to_thread(self.listen_to_microphone())

                    if user_speech_text and self.is_running:
                        self.text_received.emit("You", user_speech_text)
                        # Construct payload and stream it through the socket tunnel
                        payload = json.dumps({"text": user_speech_text})
                        await websocket.send(payload)
                        self.status_changed.emit("AI is thinking and speaking...")

                listen_task.cancel()
        except Exception as e:
            self.status_changed.emit(f"Server Connection Error: {e}")

    def listen_to_microphone(self) -> str:
        """Captures audio streams and transcribes them when the user pauses talking."""
        with self.microphone as source:
            try:
                # Listens for speech phrases, auto-cutting logs on silence thresholds
                audio = self.recognizer.listen(source, timeout=None, phrase_time_limit=10)
                text = self.recognizer.recognize_google(audio, language="ru-RU")
                return text
            except sr.UnknownValueError:
                # Catch-all block for ambient room clicking, breathing or noise
                return ""
            except Exception:
                return ""

    async def receive_from_server(self, websocket):
        """Monitors incoming server frames containing text tokens or raw audio buffers."""
        try:
            while self.is_running:
                message = await websocket.recv()

                if isinstance(message, str):
                    # Process structured JSON text payloads
                    data = json.loads(message)
                    if data.get("type") == "text":
                        self.text_received.emit("AI", data.get("content", ""))

                elif isinstance(message, bytes):
                    # Process binary audio packets (.mp3 streams)
                    # Hand over audio execution to a clean background thread to avoid stutter
                    threading.Thread(target=self.play_audio_bytes, args=(message,), daemon=True).start()
                    self.status_changed.emit("Connected. Start speaking naturally...")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error reading server stream: {e}")

    def play_audio_bytes(self, audio_bytes: bytes):
        """Caches binary arrays and processes media output based on host OS parameters."""
        temp_file = "client_playback_temp.mp3"
        try:
            with open(temp_file, "wb") as f:
                f.write(audio_bytes)

            # Cross-platform execution fallback
            if sys.platform == "win32":
                os.system(f"start /min cmd /c start /b "" {temp_file}")
            else:
                os.system(f"afplay {temp_file} || mpv {temp_file} || play {temp_file}")
        except Exception as e:
            print(f"Audio playback crash: {e}")

    def stop(self):
        """Gracefully tears down network sockets and async event queues."""
        self.is_running = False
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


class MainWindow(QMainWindow):
    """Main Qt Structural Window UI for the Voice Interface."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Second Brain - Infinite Audio Channel")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout()

        self.status_label = QLabel("Initializing application...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-weight: bold; color: #4A90E2; font-size: 14px;")
        layout.addWidget(self.status_label)

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        layout.addWidget(self.chat_display)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        # Map backend thread signals straight into main UI thread slots
        self.worker = AudioBackendWorker()
        self.worker.text_received.connect(self.update_chat_display)
        self.worker.status_changed.connect(self.update_status)
        self.worker.start()

    @Slot(str, str)
    def update_chat_display(self, sender: str, text: str):
        self.chat_display.append(f"<b>{sender}:</b> {text}<br>")

    @Slot(str)
    def update_status(self, status: str):
        self.status_label.setText(status)

    def closeEvent(self, event):
        # Enforce thread destruction routine on application exit
        self.worker.stop()
        self.worker.wait()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())