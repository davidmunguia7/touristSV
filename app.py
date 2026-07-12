import os
import json
import base64
import requests
from flask import Flask, render_template, request, jsonify, redirect, abort
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

app = Flask(__name__)
# CORREGIDO: os.getenv recibe el NOMBRE de la variable del .env, nunca la key directa
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# URL base de la API Pulgarcito (rutas v1, lugares v2)
# Configurable desde .env por si la API cambia de servidor o se corre localmente
PULGARCITO_ROOT = os.getenv("PULGARCITO_URL", "https://api.pulgarcito.dev")
PULGARCITO_BASE = f"{PULGARCITO_ROOT}/v1"

# ============ CATÁLOGO DEL MAPA: SOLO LUGARES CURADOS (JSON local) ============
# Decisión de diseño: el mapa muestra únicamente nuestro catálogo curado.
# La API de Pulgarcito se usa bajo demanda: búsqueda semántica cuando el
# turista pregunta/manda foto, y negocios cercanos al hacer clic en un pin.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "data", "places.json"), encoding="utf-8") as f:
    CATALOG = json.load(f)["places"]
print(f"[TouristSV] Catálogo curado: {len(CATALOG)} lugares")

# ============ GUÍAS TURÍSTICOS (data/guias.json) ============
GUIAS_PATH = os.path.join(BASE_DIR, "data", "guias.json")


def load_guias():
    try:
        with open(GUIAS_PATH, encoding="utf-8") as f:
            return json.load(f).get("guias", [])
    except FileNotFoundError:
        return []


def save_guias(guias):
    with open(GUIAS_PATH, "w", encoding="utf-8") as f:
        json.dump({"guias": guias}, f, ensure_ascii=False, indent=2)


# Centro aproximado de cada departamento, para relacionar guías con pines
DEPT_CENTERS = {
    "Ahuachapán": (13.90, -89.90),
    "Santa Ana": (14.05, -89.55),
    "Sonsonate": (13.65, -89.70),
    "Chalatenango": (14.15, -89.05),
    "La Libertad": (13.60, -89.35),
    "San Salvador": (13.72, -89.19),
    "Cuscatlán": (13.85, -89.05),
    "La Paz": (13.45, -88.95),
    "Cabañas": (13.90, -88.75),
    "San Vicente": (13.60, -88.75),
    "Usulután": (13.40, -88.45),
    "San Miguel": (13.50, -88.15),
    "Morazán": (13.80, -88.10),
    "La Unión": (13.40, -87.90),
}


def haversine_km(lat1, lng1, lat2, lng2):
    from math import radians, sin, cos, asin, sqrt
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


# ============ CATÁLOGO DE COMIDAS TÍPICAS (Pulgarcito /v1/foods) ============
def load_pulgarcito_foods():
    """Carga el catálogo curado de comidas típicas. No tiene búsqueda por
    texto, así que lo cacheamos completo y hacemos match local por nombre."""
    try:
        try:
            resp = requests.get(
                f"{PULGARCITO_BASE}/foods",
                params={"limit": 200},
                timeout=8,
            )
            resp.raise_for_status()
        except Exception:
            # Reintento con defaults del servidor por si el limit alto falla
            resp = requests.get(f"{PULGARCITO_BASE}/foods", timeout=8)
            resp.raise_for_status()
        foods = resp.json().get("results", [])
        print(f"[Pulgarcito] Catálogo de comidas: {len(foods)} platillos")
        return foods
    except Exception as e:
        print(f"[Pulgarcito] No se pudo cargar el catálogo de comidas: {e}")
        return []


FOODS = load_pulgarcito_foods()


def _norm(s):
    """Normaliza texto para comparar: minúsculas y sin tildes."""
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower().strip())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def match_food(query):
    """Busca la comida del catálogo que mejor coincida con el término."""
    q = _norm(query)
    best, best_score = None, 0
    for f in FOODS:
        name = _norm(f.get("name", ""))
        score = 0
        if q == name:
            score = 3
        elif q in name or name in q:
            score = 2
        elif any(q in _norm(t) or _norm(t) in q for t in (f.get("tags") or [])):
            score = 1
        if score > best_score:
            best, best_score = f, score
    return best

# Versión compacta del catálogo para inyectar al prompt (máx 60 para no inflar tokens)
catalog_lines = "\n".join(
    f"- {p['name']} | lat: {p['lat']}, lng: {p['lng']} | {p['category']} | "
    f"tags: {', '.join(p['tags'])} | {p['description']}"
    for p in CATALOG[:60]
)

SYSTEM_PROMPT = f"""Eres Ohtli AI, tu cuate guía turístico experto en El Salvador.
Tu trabajo: recomendar lugares reales de El Salvador según el perfil del turista
(playa/montaña/ciudad, mochilero/intermedio/premium, tranquilidad/fiesta/adrenalina)
y responder preguntas sobre cultura, comida, historia y rutas.

PERSONALIDAD Y FORMA DE HABLAR:
- Hablás como salvadoreño: usás el voseo de forma natural ("vos", "querés",
  "andás buscando", "mirá", "fijate").
- Salpicás tus respuestas con 1 o 2 palabras salvadoreñas por mensaje, de
  forma natural, nunca forzada. Ejemplos que podés usar: "chivo" (genial),
  "cabal" (exacto), "va pue" (está bien / vamos), "cheque" (de acuerdo),
  "de un solo" (directamente), "al suave" (con calma), "bien alegre",
  "cipote/cipota" (niño/niña), "pisto" (dinero), "chucho" (perro).
- Si el mensaje del turista sugiere que es extranjero (escribe en otro idioma
  o pregunta cosas básicas del país), explicá el modismo entre paréntesis la
  primera vez que lo usés. Ej: "¡Está bien chivo (genial) ese plan!". Si
  escribe en otro idioma, respondé en su idioma pero podés dejar caer una
  palabrita salvadoreña con su traducción, como toque cultural.
- Nunca usés palabras vulgares o de doble sentido, aunque sean comunes.
  Mantené un tono cálido, alegre y hospitalario: sos el amigo salvadoreño
  que todo turista quisiera tener.

CATÁLOGO OFICIAL DE SITIOS (usa SIEMPRE estos nombres y coordenadas exactas
cuando recomiendes un lugar que esté aquí):
{catalog_lines}

REGLAS DE RESPUESTA:
1. Responde SIEMPRE únicamente con un objeto JSON válido, sin markdown, sin ```.
2. Estructura exacta:
{{
  "reply": "tu respuesta conversacional en español, cálida y breve (máx 3 oraciones; PERO si el turista pide detalles o 'más información' sobre un lugar, extiéndete a 6-8 oraciones cubriendo historia, qué hacer, costos aproximados y un consejo práctico)",
  "search_query": "término corto para buscar datos verificados, o null",
  "search_type": "place | food | toy | null",
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
   Sobre "search_type": clasifica el search_query: "place" si es un lugar,
   "food" si es comida o bebida típica, "toy" si es un juguete o artesanía
   tradicional. Si search_query es null, pon null.
3. REGLA DE ORO sobre "places": recomienda UN (1) SOLO lugar, el que mejor
   encaje con lo que pide el turista. Solo incluye 2 o 3 lugares cuando el
   usuario pida EXPLÍCITAMENTE una ruta, itinerario o "varios lugares", y en
   ese caso TODOS deben estar en la misma zona (a menos de ~30 km entre sí,
   ej: solo Ruta de las Flores, o solo costa de La Libertad). NUNCA mezcles
   occidente y oriente del país en una misma respuesta. Si la pregunta no
   requiere lugares (ej: "¿qué son las pupusas?"), devuelve "places": [].
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


def cluster_places(places, max_km=50):
    """Candado geográfico: descarta lugares a más de max_km del principal,
    para que las rutas nunca crucen el país entero. Máximo 3 paradas."""
    if not places or len(places) <= 1:
        return places
    base = places[0]
    kept = [p for p in places
            if haversine_km(base["lat"], base["lng"], p["lat"], p["lng"]) <= max_km]
    return kept[:3]


def enrich_with_search(parsed):
    """Enruta la búsqueda de datos verificados según lo que la IA identificó:
    - place → búsqueda semántica en /v2/places/search (pines verificados)
    - food  → match en el catálogo de comidas (info + ingredientes en el chat)
    - toy   → pendiente: se conectará cuando tengamos la doc del endpoint
    """
    query = parsed.pop("search_query", None)
    search_type = parsed.pop("search_type", None)
    if not query:
        return parsed

    if search_type == "food":
        food = match_food(query)
        if food:
            extra = f"\n\n✅ Dato verificado — {food.get('name')}: {food.get('description', '')}"
            ingredients = food.get("ingredients") or []
            if ingredients:
                extra += f"\n🥘 Ingredientes típicos: {', '.join(ingredients)}."
            occasion = food.get("occasion")
            if occasion and occasion != "everyday":
                labels = {"christmas": "Navidad", "easter": "Semana Santa",
                          "festival": "festivales", "patronal": "fiestas patronales"}
                extra += f"\n🎊 Se disfruta especialmente en: {labels.get(occasion, occasion)}."
            parsed["reply"] = parsed.get("reply", "") + extra
        return parsed

    if search_type == "toy":
        # TODO: conectar cuando tengamos la documentación del endpoint de juguetes
        return parsed

    # Por defecto: lugares (búsqueda semántica)
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
    """La app principal (chat + mapa) es lo primero que se ve."""
    return render_template("index.html")


@app.route("/turista")
def turista():
    """Alias por si quedó algún enlace viejo apuntando aquí."""
    return render_template("index.html")


# @app.route("/guia")
# (reemplazada abajo por la versión activa)


@app.route("/guia")
def guia():
    """Formulario de registro para guías turísticos."""
    return render_template("guia.html")


# ============ API DE GUÍAS TURÍSTICOS ============
@app.route("/api/guides")
def get_guides():
    """Devuelve SOLO los guías del departamento del pin (más los de
    cobertura Nacional). Si el pin no trae departamento, se infiere
    el más cercano por distancia."""
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"results": []}), 400

    dept = (request.args.get("dept") or "").strip()

    # Si el pin no trae departamento válido, inferimos el más cercano
    if dept not in DEPT_CENTERS:
        dept = min(
            DEPT_CENTERS,
            key=lambda d: haversine_km(lat, lng, DEPT_CENTERS[d][0], DEPT_CENTERS[d][1]),
        )

    results = []
    for g in load_guias():
        depts = g.get("departamentos") or ([g["departamento"]] if g.get("departamento") else [])
        if "Nacional" in depts or dept in depts:
            results.append(g)
    return jsonify({"results": results, "department": dept})


@app.route("/api/guides", methods=["POST"])
def add_guide():
    """Registra un nuevo guía desde el formulario de /guia."""
    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    telefono = (data.get("telefono") or "").strip()
    departamento = (data.get("departamento") or "").strip()

    if not nombre or not telefono or departamento not in DEPT_CENTERS:
        return jsonify({"ok": False, "error": "Nombre, teléfono y departamento válido son obligatorios."}), 400

    guias = load_guias()
    guias.append({
        "nombre": nombre,
        "telefono": telefono,
        "correo": (data.get("correo") or "").strip(),
        "idiomas": (data.get("idiomas") or "").strip(),
        "descripcion": (data.get("descripcion") or "").strip(),
        "departamentos": [departamento],
    })
    save_guias(guias)
    return jsonify({"ok": True})


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

En "search_type": clasifica lo identificado: "place" (lugar), "food" (comida
o bebida típica) o "toy" (juguete o artesanía tradicional). Null si no aplica.
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
        parsed["places"] = cluster_places(parsed.get("places", []))
        return jsonify(parsed)
    except json.JSONDecodeError:
        return jsonify({"reply": raw, "places": []})
    except Exception as e:
        return jsonify({"reply": f"Ups, hubo un problema: {str(e)}", "places": []}), 500


# ============ IMÁGENES DE LUGARES (Wikipedia + caché local) ============
IMAGES_CACHE_PATH = os.path.join(BASE_DIR, "data", "images_cache.json")

try:
    with open(IMAGES_CACHE_PATH, encoding="utf-8") as f:
        IMG_CACHE = json.load(f)
except FileNotFoundError:
    IMG_CACHE = {}


def save_img_cache():
    with open(IMAGES_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(IMG_CACHE, f, ensure_ascii=False, indent=2)


def fetch_wikipedia_image(name):
    """Busca la foto principal del artículo de Wikipedia que mejor
    coincida con el lugar. Devuelve la URL de la miniatura o None."""
    try:
        resp = requests.get(
            "https://es.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"{name} El Salvador",
                "gsrlimit": 1,
                "prop": "pageimages",
                "piprop": "thumbnail",
                "pithumbsize": 600,
                "format": "json",
            },
            headers={"User-Agent": "OhtliAI/1.0 (hackathon project)"},
            timeout=6,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        if not pages:
            return None
        page = next(iter(pages.values()))
        return page.get("thumbnail", {}).get("source")
    except Exception as e:
        print(f"[Wikipedia img] Error con '{name}': {e}")
        return None


@app.route("/api/place-image")
def place_image():
    """Redirige a la foto del lugar. Busca en Wikipedia la primera vez
    y cachea el resultado (incluso los 'no encontrados') en un JSON local."""
    name = (request.args.get("name") or "").strip()
    if not name:
        abort(404)

    if name not in IMG_CACHE:
        IMG_CACHE[name] = fetch_wikipedia_image(name) or ""
        save_img_cache()

    url = IMG_CACHE[name]
    if not url:
        abort(404)
    return redirect(url)


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
        parsed["places"] = cluster_places(parsed.get("places", []))
        return jsonify(parsed)
    except json.JSONDecodeError:
        return jsonify({"reply": raw, "places": []})
    except Exception as e:
        return jsonify({"reply": f"Ups, hubo un problema: {str(e)}", "places": []}), 500


if __name__ == "__main__":
    app.run(debug=True)