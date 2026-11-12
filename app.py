from flask import Flask, render_template, request, jsonify
from google import genai
from google.genai import types
from pathlib import Path

app = Flask(__name__, template_folder='templates')

API_KEY = (Path(__file__).parent / ".gemini_key").read_text().strip()
client = genai.Client(api_key=API_KEY)
MODEL = "gemini-3.1-flash-lite"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/ask', methods=['POST'])
def ask_ai():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    language = (data.get("language") or "auto").strip()

    if not message:
        return jsonify({"error": "கேள்வியை உள்ளிடவும்"}), 400

    if language == "auto":
        instruction = "Detect the language used in the user's message and answer in that same language. If the message uses multiple languages, respond in the main language used."
    else:
        instruction = f"Answer in {language}."

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction="You are TY AI, a helpful assistant. " + instruction,
                temperature=0.5,
                max_output_tokens=300
            )
        )
        return jsonify({"answer": response.text or "பதில் கிடைக்கவில்லை."})

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
