import os
import json
import base64
import requests
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

app = Flask(__name__)
# CORREGIDO: os.getenv recibe el NOMBRE de la variable del .env, nunca la key directa
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# URL base de la API Pulgarcito (rutas v1, lugares v2)
PULGARCITO_ROOT = "https://api.pulgarcito.dev"
PULGARCITO_BASE = f"{PULGARCITO_ROOT}/v1"

# ============ CATÁLOGO HÍBRIDO: API Pulgarcito + JSON local ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "data", "places.json"), encoding="utf-8") as f:
    LOCAL_CATALOG = json.load(f)["places"]


def load_pulgarcito_places():
    """Trae los lugares turísticos verificados de la API Pulgarcito.
    Si falla, devolvemos lista vacía y el catálogo local nos respalda."""
    try:
        import requests as _rq  # ya importado arriba; alias por claridad
        resp = _rq.get(
            f"{PULGARCITO_ROOT}/v2/places",
            params={"type": "tourist_place", "limit": 100},
            timeout=6,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        places = []
        for p in results:
            if p.get("lat") and p.get("lng"):
                places.append({
                    "name": p.get("name", "Sin nombre"),
                    "lat": p["lat"],
                    "lng": p["lng"],
                    "category": p.get("category") or "lugar",
                    "tags": p.get("tags") or p.get("keywords") or [],
                    "description": p.get("description") or "",
                    "image_url": p.get("image_url"),
                    "department": p.get("department"),
                })
        print(f"[Pulgarcito] Catálogo cargado: {len(places)} lugares verificados")
        return places
    except Exception as e:
        print(f"[Pulgarcito] No se pudo cargar el catálogo remoto: {e}")
        return []


# Fusionar: los de la API primero (datos verificados), los locales que no estén repetidos
_remote = load_pulgarcito_places()
_remote_names = {p["name"].lower().strip() for p in _remote}
CATALOG = _remote + [p for p in LOCAL_CATALOG if p["name"].lower().strip() not in _remote_names]
print(f"[TouristSV] Catálogo total: {len(CATALOG)} lugares")

# Versión compacta del catálogo para inyectar al prompt (máx 60 para no inflar tokens)
catalog_lines = "\n".join(
    f"- {p['name']} | lat: {p['lat']}, lng: {p['lng']} | {p['category']} | "
    f"tags: {', '.join(p['tags'])} | {p['description']}"
    for p in CATALOG[:60]
)

SYSTEM_PROMPT = f"""Eres el asistente de TouristSV, un guía turístico experto en El Salvador.
Tu trabajo: recomendar lugares reales de El Salvador según el perfil del turista
(playa/montaña/ciudad, mochilero/intermedio/premium, tranquilidad/fiesta/adrenalina)
y responder preguntas sobre cultura, comida, historia y rutas.

CATÁLOGO OFICIAL DE SITIOS (usa SIEMPRE estos nombres y coordenadas exactas
cuando recomiendes un lugar que esté aquí):
{catalog_lines}

REGLAS DE RESPUESTA:
1. Responde SIEMPRE únicamente con un objeto JSON válido, sin markdown, sin ```.
2. Estructura exacta:
{{
  "reply": "tu respuesta conversacional en español, cálida y breve (máx 3 oraciones)",
  "search_query": "término corto para buscar datos verificados, o null",
  "places": [
    {{
      "name": "Nombre del lugar",
      "lat": 13.4936,
      "lng": -89.3853,
      "description": "una línea de contexto (qué es, por qué ir)"
    }}
  ]
}}
   Sobre "search_query": si el usuario pregunta por UN lugar, comida o artesanía
   específica (ej: "¿qué es el Cerro Verde?", "háblame de las pupusas"), pon ahí
   el nombre en 2-4 palabras (ej: "Cerro Verde"). Si pide una ruta general o
   charla casual, pon null.
3. "places" puede tener de 0 a 5 lugares. Si la pregunta no requiere lugares
   (ej: "¿qué son las pupusas?"), devuelve "places": [].
4. Prioriza lugares del catálogo oficial. Si el turista pide algo que no está
   en el catálogo, puedes sugerir otro lugar real de El Salvador con sus
   coordenadas correctas.
5. Ordena los lugares como una ruta lógica (de un punto de partida al final).
"""


# ============ BÚSQUEDA SEMÁNTICA (Pulgarcito /v2/places/search) ============
def search_pulgarcito(query, limit=3):
    """Busca lugares verificados por significado (embeddings).
    Devuelve lista normalizada; si falla (ej. 503), lista vacía."""
    try:
        resp = requests.get(
            f"{PULGARCITO_ROOT}/v2/places/search",
            params={"q": query, "limit": limit},
            timeout=8,
        )
        resp.raise_for_status()
        places = []
        for p in resp.json().get("results", []):
            if p.get("lat") and p.get("lng"):
                places.append({
                    "name": p.get("name", "Sin nombre"),
                    "lat": p["lat"],
                    "lng": p["lng"],
                    "description": p.get("description") or "",
                    "image_url": p.get("image_url"),
                    "department": p.get("department"),
                    "verified": p.get("verified", False),
                })
        return places
    except Exception as e:
        print(f"[Pulgarcito search] Error: {e}")
        return []


def enrich_with_search(parsed):
    """Si la IA sugirió un término de búsqueda, trae datos verificados
    y los fusiona con los lugares de la respuesta (los verificados ganan)."""
    query = parsed.pop("search_query", None)
    if not query:
        return parsed
    verified = search_pulgarcito(query)
    if not verified:
        return parsed
    by_name = {p["name"].lower().strip(): p for p in parsed.get("places", [])}
    for v in verified:
        by_name[v["name"].lower().strip()] = v  # el dato verificado reemplaza al de la IA
    parsed["places"] = list(by_name.values())[:5]
    return parsed


@app.route("/")
def index():
    return render_template("index.html")


# ============ NUEVO: endpoint del catálogo ============
@app.route("/api/places")
def places():
    """Devuelve el catálogo completo para pintar los pines por defecto."""
    return jsonify({"places": CATALOG})


# ============ RUTAS POR CARRETERA (API Pulgarcito) ============


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


# ============ NUEVO: RECONOCIMIENTO DE IMÁGENES ============
VISION_INSTRUCTION = """El turista te envía esta foto tomada en El Salvador.
Identifica qué aparece en ella: puede ser un lugar (iglesia, volcán, playa,
edificio), un platillo típico (pupusas, tamales, yuca frita...), una artesanía
u objeto tradicional (capirucho, trompo, sorpresas de Ilobasco, mimbre...).

En "reply" (máx 4 oraciones): di qué es, un dato de su historia o significado
cultural, y si es un objeto o juego tradicional, explica brevemente cómo se usa
o se juega. Si no logras identificarlo con certeza, dilo honestamente y da tu
mejor hipótesis.

En "places": si la foto es de un lugar reconocible, inclúyelo con sus
coordenadas. Si es comida o artesanía, incluye 1-3 lugares del catálogo donde
probarla o comprarla. Si no aplica, devuelve [].

En "search_query": pon el nombre de lo que identificaste en 2-4 palabras
(ej: "Iglesia El Rosario", "pupusas", "capirucho") para buscar datos
verificados. Si no identificaste nada con certeza, pon null.
"""


@app.route("/api/vision", methods=["POST"])
def vision():
    data = request.get_json()
    image_b64 = data.get("image", "")
    mime_type = data.get("mime_type", "image/jpeg")

    if not image_b64:
        return jsonify({"reply": "No recibí ninguna imagen.", "places": []}), 400

    raw = ""
    try:
        img_bytes = base64.b64decode(image_b64)

        contents = [
            types.Content(role="user", parts=[
                types.Part.from_bytes(data=img_bytes, mime_type=mime_type),
                types.Part(text=VISION_INSTRUCTION),
            ])
        ]

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
        parsed = enrich_with_search(parsed)
        return jsonify(parsed)
    except json.JSONDecodeError:
        return jsonify({"reply": raw, "places": []})
    except Exception as e:
        return jsonify({"reply": f"Ups, hubo un problema: {str(e)}", "places": []}), 500


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
        parsed = enrich_with_search(parsed)
        return jsonify(parsed)
    except json.JSONDecodeError:
        return jsonify({"reply": raw, "places": []})
    except Exception as e:
        return jsonify({"reply": f"Ups, hubo un problema: {str(e)}", "places": []}), 500


if __name__ == "__main__":
    app.run(debug=True)