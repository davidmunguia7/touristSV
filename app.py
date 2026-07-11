import os
import json
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

app = Flask(__name__)
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

SYSTEM_PROMPT = """Eres el asistente de TouristSV, un guía turístico experto en El Salvador.
Tu trabajo: recomendar lugares reales de El Salvador según el perfil del turista
(playa/montaña, mochilero/premium, tranquilidad/adrenalina) y responder preguntas
sobre cultura, comida, historia y rutas.

REGLAS DE RESPUESTA:
1. Responde SIEMPRE únicamente con un objeto JSON válido, sin markdown, sin ```.
2. Estructura exacta:
{
  "reply": "tu respuesta conversacional en español, cálida y breve (máx 3 oraciones)",
  "places": [
    {
      "name": "Nombre del lugar",
      "lat": 13.4936,
      "lng": -89.3853,
      "description": "una línea de contexto (qué es, por qué ir)"
    }
  ]
}
3. "places" puede tener de 0 a 5 lugares. Si la pregunta no requiere lugares
   (ej: "¿qué son las pupusas?"), devuelve "places": [].
4. Usa coordenadas reales de El Salvador. Ejemplos de referencia:
   - El Tunco: 13.4936, -89.3811
   - El Zonte: 13.4958, -89.4408
   - Santa Ana (catedral): 13.9942, -89.5598
   - Juayúa: 13.8411, -89.7458
   - Ataco: 13.8703, -89.8500
   - Suchitoto: 13.9381, -89.0278
   - Lago de Coatepeque: 13.8667, -89.5500
   - Volcán de Santa Ana: 13.8536, -89.6297
   - Centro Histórico SS (Iglesia El Rosario): 13.6975, -89.1908
   - El Boquerón: 13.7344, -89.2867
   - Playa El Cuco: 13.1758, -88.1075
   - Cerro Verde: 13.8267, -89.6233
5. Ordena los lugares como una ruta lógica (de un punto de partida al final).
"""


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "")
    history = data.get("history", [])  # [{role, content}, ...]

    # Convertir historial al formato de Gemini
    # (Gemini usa "user" y "model" en lugar de "user" y "assistant")
    contents = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

    raw = ""
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                max_output_tokens=1024,
            ),
        )
        raw = response.text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        return jsonify(parsed)
    except json.JSONDecodeError:
        return jsonify({"reply": raw, "places": []})
    except Exception as e:
        return jsonify({"reply": f"Ups, hubo un problema: {str(e)}", "places": []}), 500


if __name__ == "__main__":
    app.run(debug=True)
