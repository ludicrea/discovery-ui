"""
app_v2.py
────────────────────────────────────────────────────────────────────────────────
既存 app.py を簡略化した Discovery UI v2 対応版

変更点:
  • Notion リンク不要（YouTube のみ）
  • レスポンスを最小化
  • Google Analytics は簡略版
"""

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from recommend_engine import EmbeddingCache, Episode, get_episodes
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="static")
CORS(app)

# ─── 定数 ────────────────────────────────────────────────
PHILOSOPHERS = [
    "荘子", "ホワイトヘッド", "山内得立", "カント", "ヘーゲル",
    "ニーチェ", "ハイデガー", "ウィトゲンシュタイン", "アリストテレス",
    "プラトン", "キルケゴール", "フッサール", "ドゥルーズ", "王陽明", "デカルト",
    "ナーガールジュナ", "親鸞", "道元", "白隠",  "インド哲学", "老子",
    "空海", "華厳経", "西田幾多郎", "和辻哲郎", "ピエール・アド"
]

THEMES = [
    "存在論", "認識論", "倫理学", "言語哲学", "時間・生成",
    "自由・意志", "関係・他者", "美・創造", "死・無常", "日常・実践",
    "心・意識", "社会・政治", "宗教・信仰", "科学・技術", "意味・価値",
    "西洋", "仏教", "日本哲学",  "正義", "ヘレニズム", "雑談" ,"禅", "唯識"
]

# ─── キャッシュ初期化 ────────────────────────────────────
CACHE = None
EPISODES = []

def init_cache():
    global CACHE, EPISODES
    if CACHE is None:
        log.info("🔍 キャッシュ初期化中...")
        from recommend_engine import fetch_all_episodes
        episodes = fetch_all_episodes()
        
        CACHE = EmbeddingCache()
        CACHE.generate_and_cache_embeddings(episodes)
        
        EPISODES = CACHE.load_all_episodes()
        log.info(f"✅ {len(EPISODES)} 件のエピソードをロード")

# ─────────────────────────────────────────────────────────
# API エンドポイント
# ─────────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def api_config():
    """メタデータ配信"""
    return jsonify({
        "philosophers": PHILOSOPHERS,
        "themes": THEMES,
    })


@app.route("/api/discover", methods=["POST"])
def api_discover():
    """
    Discovery API（フォールバック機能付き）
    常に最大5個のエピソードを返す
    
    リクエスト:
    {
      "philosophers": ["荘子"],
      "themes": ["存在論"],
      "search_query": "生き方について",
      "top_k": 5  ← 常に5個
    }
    
    レスポンス:
    {
      "results": [最大5個],
      "fallback_level": 0,
      "message": null,
      "query": {...}
    }
    """
    
    try:
        init_cache()
        
        data = request.json or {}
        philosophers = data.get("philosophers", [])
        themes = data.get("themes", [])
        search_query = data.get("search_query", "").strip()
        top_k = 5  # ← 常に5個に固定
        
        if not philosophers and not themes and not search_query:
            return jsonify({
                "error": "philosophers, themes, search_query のいずれかを指定してください"
            }), 400
        
        # ========== フォールバックロジック開始 ==========
        
        candidates = EPISODES
        fallback_level = 0
        
        # Level 0: 両方マッチ（哲学者 AND テーマ）
        if philosophers and themes:
            candidates = [
                ep for ep in EPISODES
                if (any(p in ep.philosophers for p in philosophers)
                    and any(t in ep.themes for t in themes))
            ]
            
            # 5個未満ならフォールバック
            if len(candidates) < 5:
                fallback_level = 1
                # Level 1: 片方マッチ（哲学者 OR テーマ）
                candidates = [
                    ep for ep in EPISODES
                    if (any(p in ep.philosophers for p in philosophers)
                        or any(t in ep.themes for t in themes))
                ]
        
        # Level 1.5: 哲学者またはテーマのいずれかのみ指定
        elif philosophers or themes:
            candidates = [
                ep for ep in EPISODES
                if (any(p in ep.philosophers for p in philosophers)
                    or any(t in ep.themes for t in themes))
            ]
            
            # 5個未満ならフォールバック
            if len(candidates) < 5:
                fallback_level = 2
        
        # Level 2: キーワード検索（サブテーマ）
        if len(candidates) < 5 and search_query:
            fallback_level = 2
            search_lower = search_query.lower()
            candidates = [
                ep for ep in EPISODES
                if (search_lower in ep.title.lower()
                    or search_lower in ep.summary.lower())
            ]
        
        # Level 3: すべてのエピソード（新しい順）
        if len(candidates) < 5:
            fallback_level = 3
            candidates = EPISODES
        
        # ========== フォールバックロジック終了 ==========
        
        # ステップ2: スコア計算
        scores = np.zeros(len(candidates))
        
        if search_query and candidates:
            # Embedding モデルをロード
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(
                "sentence-transformers/paraphrase-multilingual-minilm-l12-v2"
            )
            
            user_embedding = model.encode(search_query, convert_to_numpy=True)
            
            for i, ep in enumerate(candidates):
                if ep.embedding is not None:
                    sim = cosine_similarity(
                        user_embedding.reshape(1, -1),
                        ep.embedding.reshape(1, -1)
                    )[0, 0]
                    scores[i] = float(sim)
        else:
            # タグのみの場合は「新しい順」（後ろのエピソード優先）
            scores = np.arange(len(candidates), 0, -1, dtype=float)
        
        # ステップ3: ソートして TOP K を取得（常に5個）
        if len(candidates) > 0:
            sorted_indices = np.argsort(scores)[::-1][:top_k]
            results = [
                {
                    "notion_id": candidates[i].notion_id,
                    "title": candidates[i].title,
                    "url": candidates[i].url,
                    "summary": candidates[i].summary,
                    "episode_type": candidates[i].episode_type,
                    "difficulty": candidates[i].difficulty,
                }
                for i in sorted_indices
            ]
        else:
            results = []
        
        # フォールバック時のメッセージ
        messages = {
            0: None,  # 厳密なマッチ、通知なし
            1: "⚠️ マッチ数が少ないため、関連エピソードも含めて表示しています",
            2: "⚠️ マッチ数が少ないため、キーワード検索の結果を表示しています",
            3: "⚠️ マッチ数が少ないため、最新のエピソードを表示しています"
        }
        
        log.info(f"✅ {len(results)} 件のエピソードを返却（fallback_level={fallback_level}）")
        
        return jsonify({
            "results": results,
            "fallback_level": fallback_level,
            "message": messages.get(fallback_level),
            "query": {
                "philosophers": philosophers,
                "themes": themes,
                "search_query": search_query,
            }
        })
    
    except Exception as e:
        log.error(f"Discovery API エラー: {e}", exc_info=True)
        return jsonify({
            "error": "検索に失敗しました。しばらく待ってから再度お試しください。",
            "details": str(e) if __name__ == "__main__" else None
    }), 500


@app.route("/", methods=["GET"])
def index():
    """メインページ"""
    return render_template("discovery-v2.html")


@app.route("/api/health", methods=["GET"])
def health():
    """ヘルスチェック"""
    return jsonify({"status": "ok"})


# ─────────────────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("🚀 Flask サーバー起動（Discovery UI v2）...")
    
    port = int(os.environ.get("PORT", 5000))
    log.info(f"📍 http://localhost:{port}")
    
    init_cache()
    
    app.run(host="0.0.0.0", port=port, debug=False)
