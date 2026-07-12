Ohtli

Descripción:
Es un asistente turístico inteligente para El Salvador que resuelve el problema del turista perdido:
no saber qué visitar según su presupuesto y estilo, qué está viendo (lugares, comida, artesanías) 
ni cómo llegar de forma realista.

La aplicación combina un chat con IA de personalidad salvadoreña (voseo y modismos locales)
con un mapa interactivo. El turista arma su ruta eligiendo su "mood" (tipo de lugar,
presupuesto, ambiente, tiempo de viaje y compañía), puede fotografiar lugares, platillos u 
objetos tradicionales para recibir su contexto cultural al instante, y conecta con guías
turísticos certificados por departamento vía WhatsApp. 
Las rutas se trazan por carretera real y se exportan a Google Maps para la navegación final.

Tecnologías utilizadas:

Capa -  Tecnología
Backend: Python 3 + Flask
IA (chat y visión): Google Gemini (gemini-3.1-flash-lite) vía SDK google-genai
FrontendHTML + Tailwind CSS (CDN) + JavaScript vanilla
MapaLeaflet + tiles CARTO/OpenStreetMap (sin API key)
Voz: Web Speech API (reconocimiento y síntesis, ES/EN)
Rutas por carretera: API Pulgarcito (/v1/route, proxy OSRM)
Datos verificados: API Pulgarcito (/v2/places/search, /v1/foods)
Imágenes de lugares: API de Wikipedia (es) con caché local
Navegación final: Google Maps (deep link con waypoints)
Persistencia Archivos JSON (places.json, guias.json, images_cache.json)

Instrucciones de instalación
Requisitos previos: Python 3.10+ y una API key gratuita de Google AI Studio.
# 1. Clonar o descargar el proyecto
cd OhtliAI

# 2. Crear y activar el entorno virtual
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate
# Mac/Linux:
source .venv/bin/activate

# 3. Instalar dependencias
pip install flask python-dotenv google-genai requests


OhtliAI/
├── data/
│   ├── places.json        # Catálogo curado de 48 lugares
│   ├── guias.json         # Red de guías turísticos
│   └── images_cache.json  # Caché de fotos (se genera solo)
├── templates/
│   ├── index.html         # App principal (chat + mapa)
│   ├── guia.html          # Formulario de registro de guías
│   └── portada.html       # Portada (fuera del flujo actual)
├── .env                   # Variables de entorno (crear, no subir a Git)
└── app.py                 # Servidor Flask


Instrucciones de ejecución
# Con el entorno virtual activado:
python app.py

Abrir en el navegador: http://127.0.0.1:5000


Variables de entorno:
Crear un archivo .env junto a app.py:
# OBLIGATORIA — key de Google AI Studio
GEMINI_API_KEY=tu_api_key

# OPCIONAL — modelo de Gemini (default en el código: gemini-3.1-flash-lite)
GEMINI_MODEL=gemini-3.1-flash-lite

# OPCIONAL — URL de la API Pulgarcito (default: https://api.pulgarcito.dev)
PULGARCITO_URL=https://api.pulgarcito.dev


Completas y funcionales

- Chat con IA con personalidad salvadoreña (voseo, modismos con traducción para extranjeros) 
y respuestas estructuradas en JSON (texto + lugares con coordenadas).

- Mood cards de 5 secciones (tipo de lugar, presupuesto, ambiente, tiempo de viaje, 
compañía) con indicador de progreso.

-  Mapa interactivo con catálogo curado de 48 lugares reales, emojis por categoría y
 pines dorados de regeneración cultural (controlados por tag en places.json).

- Popups enriquecidos: foto, departamento, "📖 Más info" (ficha vía IA), "🧭 Cerca
de aquí" (lugares del catálogo con distancia real y navegación encadenada), "👤 Guía turístico" y "🚗 Iniciar ruta".

- Rutas por carretera (API Pulgarcito /v1/route, multi-tramo) con distancia/duración, y respaldo automático de línea
recta si la API falla. Candado geográfico: máx. 3 paradas a menos de 50 km entre sí.

- Red de guías turísticos: 12 guías reales cargados, filtro por coincidencia exacta de departamento (+ cobertura
"Nacional"), contacto directo por WhatsApp y formulario de registro que persiste en guias.json.


<img width="2720" height="1200" alt="ohtli_ai_arquitectura" src="https://github.com/user-attachments/assets/1a6c06c7-d724-49ae-8e88-bbb4d9b97633" />



