print("✅ ORQUESTRADOR TRAFEGO PAGO BRASIL - CARREGANDO...")

import os, requests, uuid, re
from flask import Flask, request, jsonify, send_from_directory, url_for
from dotenv import load_dotenv
from flask_cors import CORS
from urllib.parse import urlparse
import concurrent.futures
import glob

load_dotenv()
app = Flask(__name__, static_folder='static')
CORS(app)

UPLOAD_FOLDER = 'static/ml_temp'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─────────────────────────────────────────
# IA — Claude e Llama
# ─────────────────────────────────────────
def chamar_claude(messages):
    payload = {"model": "claude-sonnet-4-6", "max_tokens": 2048, "messages": messages}
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json=payload, timeout=60)
    if r.status_code == 200:
        return r.json()["content"][0]["text"]
    return f"Erro Claude: {r.text}"

def chamar_llama(messages):
    payload = {"model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "messages": messages, "max_tokens": 2048, "temperature": 0.7}
    r = requests.post("https://api.together.xyz/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('TOGETHER_API_KEY')}", "Content-Type": "application/json"},
        json=payload, timeout=60)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"]
    return f"Erro Llama: {r.text}"

# ─────────────────────────────────────────
# ROTAS IA
# ─────────────────────────────────────────
@app.route('/gerar-criativo', methods=['POST'])
def gerar_criativo():
    data = request.get_json()
    messages = data.get("messages", [])
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_claude = executor.submit(chamar_claude, messages)
        future_llama = executor.submit(chamar_llama, messages)
        texto_claude = future_claude.result()
        texto_llama = future_llama.result()
    return jsonify({"status": "sucesso", "claude": texto_claude, "llama": texto_llama,
        "combinado": f"=== CLAUDE ===\n{texto_claude}\n\n=== LLAMA ===\n{texto_llama}"})

@app.route('/v1/messages', methods=['POST'])
def claude_solo():
    data = request.get_json()
    texto = chamar_claude(data.get("messages", []))
    return jsonify({"resposta": texto, "status": 200})

@app.route('/meta/messages', methods=['POST'])
def llama_solo():
    data = request.get_json()
    texto = chamar_llama(data.get("messages", []))
    return jsonify({"resposta": texto, "status": 200})

# ─────────────────────────────────────────
# BUSCA PRODUTO ML — sem CORS, servidor busca direto
# ─────────────────────────────────────────
def extrair_mlb_id(url):
    """Extrai o MLB ID de qualquer URL do Mercado Livre"""
    if not url:
        return None
    url_limpa = url.split('?')[0].split('#')[0]
    # Formato /p/MLB1234567890
    mp = re.search(r'/p/(MLB\d{7,12})', url_limpa, re.IGNORECASE)
    if mp:
        return mp.group(1).upper()
    # Formato MLB1234567890 em qualquer parte
    m = re.search(r'MLB[-_]?(\d{7,12})', url_limpa, re.IGNORECASE)
    if m:
        return 'MLB' + m.group(1)
    # item_id=MLB...
    mq = re.search(r'item_id[=:](MLB\d{7,12})', url, re.IGNORECASE)
    if mq:
        return mq.group(1).upper()
    return None

@app.route('/buscar-produto-ml', methods=['POST'])
def buscar_produto_ml():
    """Busca dados reais do produto ML pelo servidor — sem problema de CORS"""
    data = request.get_json()
    link = (data.get('link') or data.get('url') or '').strip()

    if not link:
        return jsonify({"error": "link obrigatorio"}), 400

    # Resolver link curto meli.la no servidor
    item_id = extrair_mlb_id(link)
    if not item_id and 'meli.la' in link:
        try:
            r = requests.get(link, allow_redirects=True, timeout=10,
                headers={'User-Agent': 'Mozilla/5.0'})
            item_id = extrair_mlb_id(r.url)
            print(f"Resolvido meli.la -> {r.url} -> {item_id}")
        except Exception as e:
            print(f"Erro resolve meli.la: {e}")

    if not item_id:
        return jsonify({"error": "MLB ID nao encontrado no link"}), 400

    # Buscar na API oficial ML — tenta multiplos endpoints
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
            'Referer': 'https://www.mercadolivre.com.br/',
            'Origin': 'https://www.mercadolivre.com.br',
        }
        # Tentar API oficial primeiro, depois search como fallback
        r = requests.get(f'https://api.mercadolibre.com/items/{item_id}',
            headers=headers, timeout=15)

        # Se der 403, tenta via search API
        if r.status_code == 403:
            print(f"API items 403, tentando search...")
            r2 = requests.get(
                f'https://api.mercadolibre.com/sites/MLB/search?q={item_id}&limit=1',
                headers=headers, timeout=15)
            if r2.ok:
                data2 = r2.json()
                results = data2.get('results', [])
                if results:
                    item = results[0]
                    imagens = []
                    thumb = (item.get('thumbnail') or '').replace('http://','https://')
                    if thumb:
                        thumb_hd = re.sub(r'-[A-Z](\.(jpg|webp|png))$', r'-F', thumb, flags=re.IGNORECASE)
                        imagens.append(thumb_hd)
                    preco = ''
                    if item.get('price'):
                        preco = 'R${:,.2f}'.format(item['price']).replace(',','X').replace('.',',').replace('X','.')
                    return jsonify({
                        'item_id': item_id,
                        'titulo': item.get('title',''),
                        'preco': preco,
                        'descricao': '',
                        'imagens': imagens,
                        'permalink': item.get('permalink', ''),
                        'fonte': 'mercadolivre_search'
                    })
            return jsonify({"error": f"ML API retornou {r.status_code}", "item_id": item_id}), 400

        if not r.ok:
            return jsonify({"error": f"ML API retornou {r.status_code}", "item_id": item_id}), 400

        d = r.json()

        # Processar imagens em alta resolução
        imagens = []
        for pic in (d.get('pictures') or []):
            url_img = (pic.get('url') or pic.get('secure_url') or '').replace('http://', 'https://')
            url_img = re.sub(r'-[A-Z](\.(jpg|webp|png))$', r'-F\1', url_img, flags=re.IGNORECASE)
            if url_img:
                imagens.append(url_img)

        # Fallback thumbnail
        if not imagens and d.get('thumbnail'):
            thumb = d['thumbnail'].replace('http://', 'https://')
            thumb = re.sub(r'-[A-Z](\.(jpg|webp|png))$', r'-F\1', thumb, flags=re.IGNORECASE)
            imagens.append(thumb)

        imagens = list(dict.fromkeys(imagens))[:5]  # dedup + max 5

        preco = ''
        if d.get('price'):
            preco = 'R${:,.2f}'.format(d['price']).replace(',', 'X').replace('.', ',').replace('X', '.')

        resultado = {
            "item_id": item_id,
            "titulo": d.get('title', ''),
            "preco": preco,
            "descricao": d.get('warranty', '') or '',
            "imagens": imagens,
            "permalink": d.get('permalink', link),
            "status": d.get('status', ''),
            "fonte": "mercadolivre_oficial"
        }

        print(f"✅ Produto encontrado: {resultado['titulo']} | {len(imagens)} imagens")
        return jsonify(resultado)

    except Exception as e:
        print(f"❌ Erro buscar ML: {e}")
        return jsonify({"error": str(e), "item_id": item_id}), 500

# ─────────────────────────────────────────
# PROXY IMAGEM ML — para Instagram acessar
# ─────────────────────────────────────────
@app.route('/proxy-image-ml', methods=['POST'])
def proxy_image_ml():
    data = request.json
    ml_image_url = data.get('image_url') or data.get('url')
    if not ml_image_url:
        return jsonify({"error": "image_url obrigatoria"}), 400
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.mercadolivre.com.br/',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
        }
        img_response = requests.get(ml_image_url, headers=headers, timeout=15, stream=True)
        img_response.raise_for_status()
        ext = os.path.splitext(urlparse(ml_image_url).path)[1] or '.jpg'
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        with open(filepath, 'wb') as f:
            for chunk in img_response.iter_content(1024):
                f.write(chunk)
        # URL publica: usa ngrok se configurado
        ngrok_url = os.getenv('NGROK_URL', '').rstrip('/')
        if ngrok_url:
            public_url = ngrok_url + url_for('static', filename=f'ml_temp/{filename}')
        else:
            public_url = request.host_url.rstrip('/') + url_for('static', filename=f'ml_temp/{filename}')
            public_url = public_url.replace('http://', 'https://')
        print(f"✅ Imagem proxiada: {filename} -> {public_url}")
        return jsonify({"public_url": public_url})
    except Exception as e:
        print(f"❌ Erro proxy: {e}")
        return jsonify({"error": f"Falha no proxy: {str(e)}"}), 500

# ─────────────────────────────────────────
# NGROK — salvar URL para proxy de imagens
# ─────────────────────────────────────────
@app.route('/set-ngrok', methods=['POST'])
def set_ngrok():
    data = request.get_json()
    url = data.get('url', '').rstrip('/')
    if url:
        os.environ['NGROK_URL'] = url
        print(f'✅ NGROK_URL configurada: {url}')
        return jsonify({'status': 'ok', 'ngrok_url': url})
    return jsonify({'error': 'url obrigatoria'}), 400

# ─────────────────────────────────────────
# ROTAS SISTEMA
# ─────────────────────────────────────────
@app.route('/')
def index():
    htmls = sorted(glob.glob('TrafegoPago*.html'), reverse=True)
    for nome in htmls:
        if os.path.exists(nome):
            return send_from_directory('.', nome)
    return "HTML nao encontrado", 404

@app.route('/checar-chaves')
def checar():
    return jsonify({
        "ANTHROPIC_API_KEY": "OK" if os.getenv("ANTHROPIC_API_KEY") else "FALTANDO",
        "TOGETHER_API_KEY": "OK" if os.getenv("TOGETHER_API_KEY") else "FALTANDO",
        "NGROK_URL": os.getenv("NGROK_URL", "nao configurado"),
        "status": "online"
    })

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "2.0"})

# ─────────────────────────────────────────
# START
# ─────────────────────────────────────────
if __name__ == '__main__':
    print("\n" + "="*55)
    print("  TRAFEGO PAGO BRASIL — SERVIDOR")
    print("="*55)
    print("  BUSCA ML:    http://localhost:5000/buscar-produto-ml")
    print("  PROXY IMG:   http://localhost:5000/proxy-image-ml")
    print("  HEALTH:      http://localhost:5000/health")
    print("="*55 + "\n")
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
