from flask import Flask, render_template, request, jsonify, send_from_directory, Response, stream_with_context
from pathlib import Path
import os
import json
import base64
import requests

app = Flask(__name__, template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 9 * 1024 * 1024

API_KEY = os.environ.get("GEMINI_API_KEY") or (Path(__file__).parent / ".gemini_key").read_text().strip()
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
        history_raw = data.get("history", "[]")
        try:
            history = json.loads(history_raw) if isinstance(history_raw, str) else history_raw
        except (ValueError, TypeError):
            history = []

        conversation = []
        if isinstance(history, list):
            for item in history[-8:]:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                old_text = item.get("text")
                if role in ("user", "ai") and isinstance(old_text, str) and old_text.strip():
                    label = "User" if role == "user" else "TY AI"
                    conversation.append(f"{label}: {old_text}")

        if message:
            conversation.append(f"User: {message}")

        parts = [{"text": "\n".join(conversation) or "Please respond to the attached file."}]

        if uploaded:
            allowed = ["image/jpeg", "image/png", "image/webp"]
            if uploaded.mimetype not in allowed:
                return jsonify({"error": "JPG, PNG, WEBP படங்களை மட்டும் அனுப்பவும்"}), 400

            image_data = uploaded.read()
            if not image_data or len(image_data) > 8 * 1024 * 1024:
                return jsonify({"error": "Photo காலியாக உள்ளது அல்லது 8 MB-ஐ விடப் பெரியது"}), 400

            parts.append({
                "inlineData": {
                    "mimeType": uploaded.mimetype,
                    "data": base64.b64encode(image_data).decode("ascii")
                }
            })

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

            parts.append({"text": "Listen to the audio and answer the speaker's request."})
            parts.append({
                "inlineData": {
                    "mimeType": audio_mime,
                    "data": base64.b64encode(audio_data).decode("ascii")
                }
            })

        system_instruction = (
            "You are TY AI, a friendly, intelligent and reliable AI assistant. "
            + instruction
            + "Talk naturally like a helpful friend, not like a robot. "
            "Always reply in the user's language and natural style: Tamil, Tanglish, English or mixed language. "
            "Understand informal words, spelling mistakes and the full conversation context. "
            "Be warm, friendly, patient and respectful. "
            "Give short answers for simple questions and detailed explanations when needed. "
            "Remember relevant details from earlier messages in the current conversation. "
            "Answer the exact question clearly and accurately. "
            "Use simple natural language and examples when helpful. "
            "Avoid robotic, repetitive or generic replies. "
            "Ask a short clarification only when necessary. "
            "Never invent facts or pretend to know something you do not know. "
            "Admit mistakes and correct them politely. "
            "Do not agree with incorrect statements just to please the user. "
            "For educational questions, explain clearly at the appropriate level. "
            "For images and audio, carefully understand the supplied content before answering. "
            "Do not add unrelated information. "
            "Make every conversation natural, useful and friendly."
        )

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.5,
                "maxOutputTokens": 2048
            }
        }

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + MODEL + ":streamGenerateContent"
        )

        def generate():
            import time

            retry_codes = {429, 500, 502, 503, 504}

            for attempt in range(3):
                try:
                    with requests.post(
                        url,
                        params={"key": API_KEY, "alt": "sse"},
                        json=payload,
                        stream=True,
                        timeout=(15, 120)
                    ) as upstream:

                        if upstream.status_code != 200:
                            status = upstream.status_code
                            error_detail = upstream.text[:500]
                            print(
                                "Gemini REST error:",
                                status, error_detail, flush=True
                            )

                            if status in retry_codes and attempt < 2:
                                wait = 2 ** (attempt + 1)
                                print(
                                    f"Retrying Gemini in {wait}s...",
                                    flush=True
                                )
                                time.sleep(wait)
                                continue

                            yield "data: " + json.dumps(
                                {"error": "Gemini API பிழை: " + str(status)},
                                ensure_ascii=False
                            ) + "\n\n"
                            return

                        for raw_line in upstream.iter_lines(decode_unicode=False):
                            if not raw_line or not raw_line.startswith(b"data:"):
                                continue

                            try:
                                chunk_data = json.loads(
                                    raw_line[5:].decode("utf-8").strip()
                                )
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                continue

                            for candidate in chunk_data.get("candidates", []):
                                for part in candidate.get("content", {}).get("parts", []):
                                    chunk_text = part.get("text")
                                    if chunk_text:
                                        yield "data: " + json.dumps(
                                            {"text": chunk_text},
                                            ensure_ascii=False
                                        ) + "\n\n"

                    yield "data: " + json.dumps({"done": True}) + "\n\n"
                    return

                except Exception as error:
                    print("Gemini streaming error:", str(error), flush=True)

                    if attempt < 2:
                        time.sleep(2 ** (attempt + 1))
                        continue

                    yield "data: " + json.dumps(
                        {"error": "பதில் பெற முடியவில்லை. மீண்டும் முயற்சி செய்யவும்."},
                        ensure_ascii=False
                    ) + "\n\n"
                    return

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no"
            }
        )

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
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + MODEL + ":generateContent"
        )
        payload = {
            "contents": [{
                "parts": [{
                    "text": (
                        f"Translate the following text into {target}. "
                        "Return only the translation, preserving its meaning:\n\n"
                        + message
                    )
                }]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 500
            }
        }

        response = requests.post(
            url,
            params={"key": API_KEY},
            json=payload,
            timeout=45
        )

        if response.status_code != 200:
            print("Translation REST error:", response.status_code, response.text[:500], flush=True)
            return jsonify({"error": "மொழிபெயர்ப்பு கிடைக்கவில்லை. மீண்டும் முயற்சி செய்யவும்."}), 500

        result = response.json()
        translated = ""
        for candidate in result.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                translated += part.get("text", "")

        return jsonify({"translation": translated.strip() or "Translation unavailable."})

    except Exception as e:
        print("Translation error:", str(e), flush=True)
        return jsonify({"error": "மொழிபெயர்ப்பு கிடைக்கவில்லை. மீண்டும் முயற்சி செய்யவும்."}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
