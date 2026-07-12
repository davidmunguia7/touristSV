import os
import json
import requests
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

app = Flask(__name__)
# CORREGIDO: os.getenv recibe el NOMBRE de la variable del .env, nunca la key directa
client = genai.Client(api_key=os.getenv("AQ.Ab8RN6KUeb25x5H8dvU3JL7vzhDeHYQvjw5heu9i-Ha9kKwviA"))

# ============ NUEVO: CATÁLOGO DE SITIOS ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "data", "places.json"), encoding="utf-8") as f:
    CATALOG = json.load(f)["places"]

# Versión compacta del catálogo para inyectar al prompt
catalog_lines = "\n".join(
    f"- {p['name']} | lat: {p['lat']}, lng: {p['lng']} | {p['category']} | "
    f"tags: {', '.join(p['tags'])} | {p['description']}"
    for p in CATALOG
)

SYSTEM_PROMPT = f"""Eres el asistente de TouristSV, un guía turístico experto en El Salvador.
Tu trabajo: recomendar lugares reales de El Salvador según el perfil del turista
(playa/montaña, mochilero/premium, tranquilidad/adrenalina) y responder preguntas
sobre cultura, comida, historia y rutas.

CATÁLOGO OFICIAL DE SITIOS (usa SIEMPRE estos nombres y coordenadas exactas
cuando recomiendes un lugar que esté aquí):
{catalog_lines}

REGLAS DE RESPUESTA:
1. Responde SIEMPRE únicamente con un objeto JSON válido, sin markdown, sin ```.
2. Estructura exacta:
{{
  "reply": "tu respuesta conversacional en español, cálida y breve (máx 3 oraciones)",
  "places": [
    {{
      "name": "Nombre del lugar",
      "lat": 13.4936,
      "lng": -89.3853,
      "description": "una línea de contexto (qué es, por qué ir)"
    }}
  ]
}}
3. "places" puede tener de 0 a 5 lugares. Si la pregunta no requiere lugares
   (ej: "¿qué son las pupusas?"), devuelve "places": [].
4. Prioriza lugares del catálogo oficial. Si el turista pide algo que no está
   en el catálogo, puedes sugerir otro lugar real de El Salvador con sus
   coordenadas correctas.
5. Ordena los lugares como una ruta lógica (de un punto de partida al final).
"""


@app.route("/")
def index():
    return render_template("index.html")


# ============ NUEVO: endpoint del catálogo ============
@app.route("/api/places")
def places():
    """Devuelve el catálogo completo para pintar los pines por defecto."""
    return jsonify({"places": CATALOG})


# ============ NUEVO: RUTAS POR CARRETERA (API Pulgarcito) ============
PULGARCITO_BASE = "https://api.pulgarcito.dev/v1"


def get_road_route(waypoints, profile="car"):
    """
    Llama a la API Pulgarcito (GET /v1/route?from=lat,lng&to=lat,lng&profile=car).
    La API solo acepta 2 puntos por llamada, así que hacemos una llamada por
    tramo y unimos las geometrías.

    Devuelve: (geometry [[lat,lng],...], distancia_total_m, duracion_total_s)
    """
    full_geometry = []
    total_distance = 0
    total_duration = 0

    for i in range(len(waypoints) - 1):
        origin = f"{waypoints[i][0]},{waypoints[i][1]}"
        dest = f"{waypoints[i + 1][0]},{waypoints[i + 1][1]}"

        resp = requests.get(
            f"{PULGARCITO_BASE}/route",
            params={"from": origin, "to": dest, "profile": profile},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # GeoJSON viene en [lng, lat]; Leaflet necesita [lat, lng]
        coords = data.get("geometry", {}).get("coordinates", [])
        full_geometry.extend([[lat, lng] for lng, lat in coords])

        total_distance += data.get("distance", 0)
        total_duration += data.get("duration", 0)

    return full_geometry, total_distance, total_duration


@app.route("/api/route", methods=["POST"])
def route():
    data = request.get_json()
    waypoints = data.get("waypoints", [])  # [[lat, lng], ...]

    if len(waypoints) < 2:
        return jsonify({"geometry": [], "error": "Se necesitan al menos 2 puntos"}), 400

    try:
        geometry, distance, duration = get_road_route(waypoints)
        return jsonify({
            "geometry": geometry,
            "distance_km": round(distance / 1000, 1),
            "duration_min": round(duration / 60),
        })
    except Exception as e:
        # Si la API falla, el frontend dibuja línea recta como respaldo
        print(f"[Pulgarcito] Error: {e}")
        return jsonify({"geometry": [], "error": str(e)})


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