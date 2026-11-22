from flask import Flask, render_template, request, jsonify, send_from_directory
from google import genai
from google.genai import types
from pathlib import Path
import os
import time

app = Flask(__name__, template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 9 * 1024 * 1024

API_KEY = os.environ.get("GEMINI_API_KEY") or (Path(__file__).parent / ".gemini_key").read_text().strip()
client = genai.Client(api_key=API_KEY)
MODEL = "gemini-3.1-flash-lite"


@app.route('/service-worker.js')
def service_worker():
    response = send_from_directory(
        app.static_folder,
        'service-worker.js',
        mimetype='application/javascript'
    )
    response.headers['Service-Worker-Allowed'] = '/'
    return response

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/ask', methods=['POST'])
def ask_ai():
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        data = request.form
    else:
        data = request.get_json(silent=True) or {}

    message = (data.get("message") or "").strip()
    language = (data.get("language") or "auto").strip()
    uploaded = request.files.get("image")
    audio_uploaded = request.files.get("audio")

    if not message and not uploaded and not audio_uploaded:
        return jsonify({"error": "கேள்வி, Photo அல்லது Audio அனுப்பவும்"}), 400

    if language == "auto":
        instruction = (
                "Identify the language of the latest user message carefully. "
                "If the message uses Tamil script, answer entirely in Tamil. "
                "If it uses Devanagari Hindi, answer in Hindi. "
                "If it uses English, answer in English. "
                "For other languages, answer in the language used by the user. "
                "If the user writes Tanglish using English letters, understand it as Tamil "
                "and reply in natural Tamil script. "
                "Never switch to Hindi or another language unless the user asks for it."
            )
    else:
        instruction = f"Answer in {language}."

    try:
        contents = []

        history_raw = data.get("history", "[]")
        try:
            import json
            history = json.loads(history_raw) if isinstance(history_raw, str) else history_raw
        except (ValueError, TypeError):
            history = []

        if isinstance(history, list):
            for item in history[-8:]:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                text = item.get("text")
                if role in ("user", "ai") and isinstance(text, str) and text.strip():
                    label = "User" if role == "user" else "TY AI"
                    contents.append(f"{label}: {text}")

        if message:
            contents.append(f"User: {message}")

        if uploaded:
            allowed = ["image/jpeg", "image/png", "image/webp"]
            if uploaded.mimetype not in allowed:
                return jsonify({"error": "JPG, PNG, WEBP படங்களை மட்டும் அனுப்பவும்"}), 400

            image_data = uploaded.read()
            if not image_data or len(image_data) > 8 * 1024 * 1024:
                return jsonify({"error": "Photo காலியாக உள்ளது அல்லது 8 MB-ஐ விடப் பெரியது"}), 400

            contents.append(types.Part.from_bytes(
                data=image_data,
                mime_type=uploaded.mimetype
            ))

        if audio_uploaded:
            audio_mime = (audio_uploaded.mimetype or "").split(";")[0].lower()
            allowed_audio = [
                "audio/webm", "audio/mp4", "audio/ogg",
                "audio/mpeg", "audio/wav", "audio/aac"
            ]
            if audio_mime not in allowed_audio:
                return jsonify({"error": "இந்த Audio வடிவம் ஆதரிக்கப்படவில்லை"}), 400

            audio_data = audio_uploaded.read()
            if not audio_data or len(audio_data) > 8 * 1024 * 1024:
                return jsonify({"error": "Audio காலியாக உள்ளது அல்லது 8 MB-ஐ விடப் பெரியது"}), 400

            contents.append("Listen to the audio and answer the speaker's request.")
            contents.append(types.Part.from_bytes(
                data=audio_data,
                mime_type=audio_mime
            ))

        system_instruction = (
            "You are TY AI, an accurate, helpful and context-aware AI assistant. "
            + instruction
            + " Always reply in the same language the user uses. "
            "If the user mixes languages, follow their natural language style. "
            "Understand the full conversation context before answering. "
            "Use relevant previous messages. "
            "Answer the exact question directly. "
            "Be clear, concise and useful. "
            "Never invent facts, dates, statistics, routes or timings. "
            "If unsure, clearly say so instead of guessing. "
            "Admit mistakes and correct them clearly. "
            "Avoid unnecessary questions, repetition and unrelated suggestions. "
            "Use simple, natural language. "
            "Complete answers without stopping mid-sentence."
            " Understand what the user is actually asking before answering. "
            "Consider the full conversation, not just the latest message. "
            "For follow-up questions, connect them to the correct earlier topic. "
            "For calculations, work through the values carefully and verify the result. "
            "For educational questions, give accurate answers at the appropriate study level. "
            "For history and factual questions, distinguish established facts from uncertainty. "
            "If a question has multiple meanings, ask one short clarification instead of guessing. "
            "Do not agree with an incorrect statement just to please the user. "
            "Correct errors politely and explain the correct point briefly. "
            "Do not add unrelated information or repeat the question unnecessarily."
        )

        answer_parts = []
        generation_contents = list(contents)

        for attempt in range(5):
            for retry in range(3):
                try:
                    response = client.models.generate_content(
                        model=MODEL,
                        contents=generation_contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.5,
                            max_output_tokens=2048
                        )
                    )
                    break
                except Exception as api_error:
                    error_text = str(api_error).upper()
                    temporary_error = (
                        any(
                            code in error_text
                            for code in ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"]
                        )
                        or any(
                            code in error_text
                            for code in [
                                "ERRNO 103",
                                "CONNECTION ABORT",
                                "SOFTWARE CAUSED CONNECTION ABORT",
                                "CONNECTION RESET",
                                "TIMED OUT",
                                "TIMEOUT"
                            ]
                        )
                    )
                    if not temporary_error or retry == 2:
                        raise
                    wait_seconds = 2 ** (retry + 1)
                    print(
                        f"Gemini temporary error. Retry {retry + 1}/3 after {wait_seconds}s",
                        flush=True
                    )
                    time.sleep(wait_seconds)

            part = response.text or ""
            if not part:
                break

            answer_parts.append(part)

            candidates = getattr(response, "candidates", None) or []
            finish_reason = str(candidates[0].finish_reason) if candidates else ""

            if "MAX_TOKENS" not in finish_reason:
                break

            generation_contents = list(contents) + [
                "Continue the answer below from exactly where it stopped. "
                "Do not repeat any previous sentences. Finish the entire answer.\n\n"
                "Previous answer ending:\n" + "".join(answer_parts)
            ]

        full_answer = "".join(answer_parts).strip()
        return jsonify({"answer": full_answer or "பதில் கிடைக்கவில்லை."})

    except Exception as e:
        print("Gemini error:", str(e), flush=True)
        return jsonify({"error": "பதில் பெற முடியவில்லை. மீண்டும் முயற்சி செய்யவும்."}), 500


@app.route('/translate', methods=['POST'])
def translate_text():
    data = request.get_json(silent=True) or {}
    message = (data.get("text") or "").strip()
    target = (data.get("target") or "").strip()

    if not message or not target:
        return jsonify({"error": "மொழியையும் உரையையும் சரிபார்க்கவும்"}), 400

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=f"Translate the following text into {target}. Return only the translation, preserving its meaning:\n\n{message}",
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=500
            )
        )
        return jsonify({"translation": response.text or "Translation unavailable."})

    except Exception as e:
        print("Translation error:", str(e), flush=True)
        return jsonify({"error": "மொழிபெயர்ப்பு கிடைக்கவில்லை. மீண்டும் முயற்சி செய்யவும்."}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
