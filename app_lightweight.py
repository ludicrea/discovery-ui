"""
app_lightweight_v3.py
────────────────────────────────────────────────────────────────────────────────
それ哲 Discovery UI v2 - 最適化版（選択式キーワード対応）

改善点:
  1. Nameフィールド確実返却（フロント表示問題の解決）
  2. 哲学者重視のスコアリング（マッチ強度を数値化）
  3. 選択式キーワード前提の最適化
  4. スコア内訳の返却（デバッグ・透明性向上）
  5. ★ CSV非同期読み込み（Renderのヘルスチェック対応）
"""

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import csv
import os
import logging
import threading
from typing import List, Dict, Tuple

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# グローバル変数
# ─────────────────────────────────────────────────────────

EPISODES = []
csv_loaded = False

# 選択式キーワード（フロント側と共通）
VALID_PHILOSOPHERS = [
    "アウグスティヌス", "アリストテレス", "アーノルド・ミンデル", "アーレント",
    "ウィトゲンシュタイン", "エピクロス", "カント", "キルケゴール",
    "サルトル", "シェリング", "ショーペンハウアー", "ジョン・ロック",
    "スピノザ", "ソクラテス", "ディオゲネス", "デカルト", "デリダ",
    "トマス・アクィナス", "ドゥルーズ", "ナーガールジュナ", "ニーチェ",
    "ハイデガー", "バタイユ", "ヒューム", "ピエール・アド", "フィヒテ",
    "フッサール", "ブッダ", "プラトン", "ヘーゲル", "ホワイトヘッド",
    "マルクス", "ライプニッツ", "レヴィナス", "レヴィ＝ストロース",
    "一遍", "九鬼周造", "栄西", "空海", "朱熹", "法然", "王陽明",
    "和辻哲郎", "西田幾多郎", "親鸞", "老子", "荘子", "道元"
]

VALID_THEMES = [
    "存在論", "認識論", "倫理学", "言語哲学", "時間・生成",
    "自由・意志", "関係・他者", "美・創造", "死・無常", "日常・実践",
    "心・意識", "社会・政治", "宗教・信仰", "科学・技術", "意味・価値",
    "西洋", "仏教", "日本哲学"
]

# ─────────────────────────────────────────────────────────
# CSV 読み込み
# ─────────────────────────────────────────────────────────

def load_episodes_from_csv(csv_path: str = "soretetsudb_260223.csv") -> List[Dict]:
    """
    CSV ファイルからエピソードを読み込む
    
    重要: 
      - Nameフィールドを "name" として保持
      - 難易度を数値化（スコアリングの補助因子として機能）
    """
    episodes = []
    
    if not os.path.exists(csv_path):
        log.warning(f"CSV ファイルが見つかりません: {csv_path}")
        return episodes
    
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            
            for idx, row in enumerate(reader):
                
                # 哲学者とテーマをリスト化
                philosophers = [
                    p.strip() for p in row.get("哲学者", "").split(",") 
                    if p.strip()
                ]
                themes = [
                    t.strip() for t in row.get("テーマ", "").split(",") 
                    if t.strip()
                ]
                
                # ルディクレア関連度を数値化
                relevance = row.get("ルディクレア関連度", "中").strip()
                relevance_score = {"高": 3, "中": 2, "低": 1}.get(relevance, 1)
                
                # 難易度を数値化
                difficulty = row.get("難易度", "中級").strip()
                difficulty_score = {"入門": 1, "中級": 2, "上級": 3}.get(difficulty, 2)
                
                episode = {
                    "notion_id": f"csv_{idx}",
                    "name": row.get("Name", ""),  # ★ Nameフィールド確実保持
                    "title": row.get("Name", ""),  # これは既存
                    "url": row.get("URL", ""),
                    "summary": row.get("Summary", ""),
                    "episode_type": row.get("エピソード種別", ""),
                    "difficulty": difficulty,
                    "difficulty_score": difficulty_score,
                    "philosophers": philosophers,
                    "themes": themes,
                    "relevance_score": relevance_score,
                }
                
                episodes.append(episode)
        
        log.info(f"✅ CSV から {len(episodes)} 件のエピソードを読み込み")
    
    except Exception as e:
        log.error(f"CSV 読み込みエラー: {e}")
    
    return episodes

# ─────────────────────────────────────────────────────────
# Flask アプリ初期化
# ─────────────────────────────────────────────────────────

app = Flask(__name__, template_folder="static")
CORS(app)

# ★ バックグラウンドでCSVを読み込む関数
def load_csv_async():
    """バックグラウンドでCSVを読み込む"""
    global EPISODES, csv_loaded
    EPISODES = load_episodes_from_csv()
    csv_loaded = True
    log.info(f"✅ バックグラウンド読み込み完了: {len(EPISODES)} 件")

# ★ アプリ起動直後にバックグラウンド処理を開始
csv_thread = threading.Thread(target=load_csv_async, daemon=True)
csv_thread.start()

log.info("🚀 Flask サーバー起動（Discovery UI v2 - 最適化版）...")

# ─────────────────────────────────────────────────────────
# スコアリングエンジン
# ─────────────────────────────────────────────────────────

def calculate_match_score(
    episode: Dict,
    query_philosophers: List[str],
    query_themes: List[str]
) -> Tuple[float, Dict]:
    
    score_breakdown = {
        "philosopher_exact": 0,
        "theme_exact": 0,
        "relevance_bonus": 0,
        "difficulty_bonus": 0,
        "zatsudanpenalty": 0,  # ← 追加
    }
    
    total_score = 0
    
    # ========== 1. 哲学者マッチング ==========
    if query_philosophers:
        for qp in query_philosophers:
            if qp in episode["philosophers"]:
                if qp in episode["name"]:
                    score_breakdown["philosopher_exact"] += 200
                else:
                    score_breakdown["philosopher_exact"] += 100
        
        total_score += score_breakdown["philosopher_exact"]
    
    # ========== 2. テーママッチング ==========
    if query_themes:
        theme_exact_matches = sum(
            1 for qt in query_themes
            if qt in episode["themes"]
        )
        
        if theme_exact_matches > 0:
            score_breakdown["theme_exact"] = theme_exact_matches * 30
            total_score += score_breakdown["theme_exact"]
    
    # ========== 3. ルディクレア関連度 ==========
    relevance_bonus = {1: 1, 2: 3, 3: 5}.get(episode["relevance_score"], 1)
    score_breakdown["relevance_bonus"] = relevance_bonus
    total_score += relevance_bonus
    
    # ========== 4. 難易度 ==========
    difficulty_bonus = episode["difficulty_score"]
    score_breakdown["difficulty_bonus"] = difficulty_bonus
    total_score += difficulty_bonus
    
    # ========== 5. 雑談ペナルティ（★追加） ==========
    if "雑談" in episode["name"]:
        score_breakdown["zatsudanpenalty"] = -200
        total_score -= 200
    
    return total_score, score_breakdown

# ─────────────────────────────────────────────────────────
# API エンドポイント
# ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("discovery-v2.html")

@app.route("/api/config", methods=["GET"])
def api_config():
    """
    フロント初期化用：利用可能な哲学者・テーマリストを返す
    """
    return jsonify({
        "philosophers": VALID_PHILOSOPHERS,
        "themes": VALID_THEMES,
        "episodes_loaded": len(EPISODES),
        "total_episodes": len(EPISODES),
        "csv_loading": not csv_loaded  # 読み込み中フラグ
    })

@app.route("/api/discover", methods=["POST"])
def api_discover():
    """
    Discovery API（最適化版）
    
    リクエスト:
    {
      "philosophers": ["フィヒテ"],
      "themes": ["認識論"]
    }
    
    レスポンス:
    {
      "results": [
        {
          "name": "エピソード名",
          "philosophers": ["フィヒテ"],
          "themes": ["認識論"],
          "url": "...",
          "summary": "...",
          "score": 130,
          "score_breakdown": {...}
        }
      ],
      "total_found": マッチ件数,
      "query": {...}
    }
    """
    
    try:
        data = request.json or {}
        philosophers = data.get("philosophers", [])
        themes = data.get("themes", [])
        
        # バリデーション
        if not philosophers and not themes:
            return jsonify({
                "error": "philosophers または themes を指定してください"
            }), 400
        
        # ========== バリデーション: 有効な値のみを受け入れる ==========
        philosophers = [p for p in philosophers if p in VALID_PHILOSOPHERS]
        themes = [t for t in themes if t in VALID_THEMES]
        
        if not philosophers and not themes:
            return jsonify({
                "error": "有効な哲学者またはテーマを指定してください"
            }), 400
        
        # ========== スコアリング ==========
        scored_episodes = []
        
        for episode in EPISODES:
            score, breakdown = calculate_match_score(
                episode, philosophers, themes
            )
            
            # スコアが0より大きいエピソードのみ候補に
            if score > 0:
                scored_episodes.append({
                    "episode": episode,
                    "score": score,
                    "breakdown": breakdown,
                })
        
        # スコア順（降順）にソート
        scored_episodes.sort(key=lambda x: x["score"], reverse=True)
        
        log.info(
            f"✅ {len(scored_episodes)} 件マッチ "
            f"(philosophers={philosophers}, themes={themes})"
        )
        
        # ========== レスポンス構築 ==========
        results = []
        for ep in scored_episodes[:5]:  # 最大5件
            result = {
                "notion_id": ep["episode"]["notion_id"],
                "name": ep["episode"]["name"],  # ★ Nameフィールド
                "title": ep["episode"]["name"], 
                "url": ep["episode"]["url"],
                "summary": ep["episode"]["summary"],
                "episode_type": ep["episode"]["episode_type"],
                "difficulty": ep["episode"]["difficulty"],
                "philosophers": ep["episode"]["philosophers"],
                "themes": ep["episode"]["themes"],
                "score": ep["score"],
                "score_breakdown": ep["breakdown"],
            }
            results.append(result)
        
        return jsonify({
            "results": results,
            "total_found": len(scored_episodes),
            "query": {
                "philosophers": philosophers,
                "themes": themes,
            }
        })
    
    except Exception as e:
        log.error(f"Discovery API エラー: {e}", exc_info=True)
        return jsonify({
            "error": "検索に失敗しました。しばらく待ってから再度お試しください。"
        }), 500

@app.route("/api/keywords", methods=["GET"])
def api_keywords():
    """
    利用可能なキーワード一覧を返す（UI初期化用）
    """
    return jsonify({
        "philosophers": VALID_PHILOSOPHERS,
        "themes": VALID_THEMES,
    })

@app.route("/api/stats", methods=["GET"])
def api_stats():
    """
    データベース統計情報（デバッグ用）
    """
    philosopher_counts = {}
    theme_counts = {}
    
    for ep in EPISODES:
        for p in ep["philosophers"]:
            philosopher_counts[p] = philosopher_counts.get(p, 0) + 1
        for t in ep["themes"]:
            theme_counts[t] = theme_counts.get(t, 0) + 1
    
    return jsonify({
        "total_episodes": len(EPISODES),
        "philosophers_count": len(philosopher_counts),
        "themes_count": len(theme_counts),
        "philosopher_distribution": philosopher_counts,
        "theme_distribution": theme_counts,
        "csv_loaded": csv_loaded,
    })

# ─────────────────────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("🚀 Flask サーバー起動（Discovery UI v2 - 最適化版）...")
    log.info("📍 http://localhost:5000")
    
    app.run(host="0.0.0.0", port=5000, debug=False)