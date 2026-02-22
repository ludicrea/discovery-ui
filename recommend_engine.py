"""
recommend_engine.py
────────────────────────────────────────────────────────────────────────────────
それ哲ラジオ 推薦エンジン

【機能】
1. Notionから400エピソードを読み込み → Summary + Full Log を取得
2. 日本語対応Embeddingで各エピソードをベクトル化 → SQLite に保存
3. ユーザーの「気になる問い」（3~5個）を Embedding → コサイン類似度で上位5件
4. タグマッチング（哲学者・テーマ）でブースト調整

【セットアップ】
  pip install sentence-transformers requests python-dotenv numpy scikit-learn

"""

import os
import json
import sqlite3
import logging
import time
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from pathlib import Path

import requests
import numpy as np
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()

# ─── ログ設定 ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("recommend_engine.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── 定数 ─────────────────────────────────────────────────────
NOTION_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('NOTION_TOKEN', '')}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}
SORETETSU_DATABASE_ID = os.getenv("SORETETSU_DATABASE_ID", "30def4a3aa6b80c0a9afd3059538c7f2")
EMBEDDING_DB_PATH = "episode_embeddings.db"
RATE_LIMIT_SLEEP = 0.4

# 日本語対応の軽量Embeddingモデル
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-minilm-l12-v2"


@dataclass
class Episode:
    """エピソードメタデータ"""
    notion_id: str
    title: str
    url: str
    summary: str
    full_log: str
    philosophers: List[str]
    themes: List[str]
    episode_type: str
    difficulty: str
    ludicrea_relevance: str
    embedding: Optional[np.ndarray] = None

    def to_dict(self) -> dict:
        return {
            "notion_id": self.notion_id,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "episode_type": self.episode_type,
            "difficulty": self.difficulty,
            "ludicrea_relevance": self.ludicrea_relevance,
            "philosophers": self.philosophers,
            "themes": self.themes,
        }


# ════════════════════════════════════════════════════════════════════════════
# Notion API ユーティリティ
# ════════════════════════════════════════════════════════════════════════════

def fetch_all_episodes() -> List[Episode]:
    """Notionから全エピソードを読み込む"""
    episodes = []
    cursor = None
    count = 0

    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        resp = requests.post(
            f"https://api.notion.com/v1/databases/{SORETETSU_DATABASE_ID}/query",
            headers=NOTION_HEADERS,
            json=body,
        )

        if resp.status_code != 200:
            log.error(f"Notion API エラー: {resp.status_code} {resp.text[:200]}")
            break

        data = resp.json()

        for page in data.get("results", []):
            ep = _parse_page(page)
            if ep:
                episodes.append(ep)
                count += 1

        log.info(f"  読み込み中... {count} 件")

        if not data.get("has_more"):
            break

        cursor = data.get("next_cursor")
        time.sleep(RATE_LIMIT_SLEEP)

    log.info(f"✅ Notion読み込み完了: {len(episodes)} 件")
    return episodes


def _parse_page(page: dict) -> Optional[Episode]:
    """Notionページからエピソードデータを抽出"""
    try:
        props = page.get("properties", {})

        # タイトル
        title = "".join(
            t.get("plain_text", "")
            for t in props.get("Name", {}).get("title", [])
        )

        # URL
        url = props.get("URL", {}).get("url", "")

        # Summary
        summary = "".join(
            t.get("plain_text", "")
            for t in props.get("Summary", {}).get("rich_text", [])
        )

        # Full Log（本文）
        full_log = _fetch_page_blocks_text(page["id"])

        # タグ情報
        philosophers = [
            opt["name"]
            for opt in props.get("哲学者", {}).get("multi_select", [])
        ]
        themes = [
            opt["name"]
            for opt in props.get("テーマ", {}).get("multi_select", [])
        ]
        episode_type = props.get("エピソード種別", {}).get("select", {}).get("name", "")
        difficulty = props.get("難易度", {}).get("select", {}).get("name", "")
        ludicrea_relevance = props.get("ルディクレア関連度", {}).get("select", {}).get("name", "")

        if not title or not url:
            return None

        return Episode(
            notion_id=page["id"],
            title=title,
            url=url,
            summary=summary,
            full_log=full_log,
            philosophers=philosophers,
            themes=themes,
            episode_type=episode_type,
            difficulty=difficulty,
            ludicrea_relevance=ludicrea_relevance,
        )
    except Exception as e:
        log.warning(f"ページパース失敗: {e}")
        return None


def _fetch_page_blocks_text(page_id: str) -> str:
    """Notionページの本文をすべて取得"""
    try:
        resp = requests.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
            headers=NOTION_HEADERS,
            timeout=30,
        )
        if resp.status_code != 200:
            return ""

        texts = []
        for block in resp.json().get("results", []):
            b_type = block.get("type", "")
            for rt in block.get(b_type, {}).get("rich_text", []):
                texts.append(rt.get("plain_text", ""))

        return " ".join(texts)[:5000]  # 容量制限

    except Exception as e:
        log.debug(f"本文取得エラー [{page_id}]: {e}")
        return ""


# ════════════════════════════════════════════════════════════════════════════
# Embedding ユーティリティ
# ════════════════════════════════════════════════════════════════════════════

class EmbeddingCache:
    """エピソードのEmbeddingをSQLiteで管理"""

    def __init__(self, db_path: str = EMBEDDING_DB_PATH):
        self.db_path = db_path
        self.model = None
        self._init_db()

    def _init_db(self):
        """テーブルを作成（なければ）"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                notion_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                summary TEXT,
                full_log TEXT,
                philosophers TEXT,
                themes TEXT,
                episode_type TEXT,
                difficulty TEXT,
                ludicrea_relevance TEXT,
                embedding BLOB,
                embedding_updated_at TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def load_model(self):
        """Embedding モデルをロード（初回のみ遅い）"""
        if self.model is None:
            log.info(f"🤖 モデル読み込み中: {EMBEDDING_MODEL_NAME}")
            self.model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            log.info("✅ モデル読み込み完了")

    def generate_and_cache_embeddings(self, episodes: List[Episode]):
        """全エピソードのEmbeddingを生成してキャッシュ"""
        self.load_model()

        log.info(f"📊 {len(episodes)} 件のEmbedding生成中...")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        for idx, ep in enumerate(episodes):
            # キャッシュ確認
            cursor.execute("SELECT embedding FROM episodes WHERE notion_id = ?", (ep.notion_id,))
            cached = cursor.fetchone()

            if cached and cached[0] is not None:
                log.debug(f"   [{idx+1}/{len(episodes)}] {ep.title[:50]} (キャッシュあり)")
                continue

            # テキスト結合：Summary + Full Log（最初2000字）
            text = f"{ep.summary}\n\n{ep.full_log[:2000]}"

            # Embedding生成
            embedding = self.model.encode(text, convert_to_numpy=True)
            embedding_bytes = embedding.tobytes()

            # DB保存
            cursor.execute("""
                INSERT OR REPLACE INTO episodes
                (notion_id, title, url, summary, full_log, philosophers, themes,
                 episode_type, difficulty, ludicrea_relevance, embedding, embedding_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                ep.notion_id,
                ep.title,
                ep.url,
                ep.summary,
                ep.full_log[:2000],
                json.dumps(ep.philosophers),
                json.dumps(ep.themes),
                ep.episode_type,
                ep.difficulty,
                ep.ludicrea_relevance,
                embedding_bytes,
            ))

            if (idx + 1) % 50 == 0:
                conn.commit()
                log.info(f"   [{idx+1}/{len(episodes)}] {len(embedding)}次元 Embedding")

        conn.commit()
        conn.close()
        log.info(f"✅ Embedding生成・キャッシュ完了")

    def load_all_episodes(self) -> List[Episode]:
        """キャッシュからすべてのエピソードを読み込み"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM episodes ORDER BY title")
        rows = cursor.fetchall()
        conn.close()

        episodes = []
        for row in rows:
            notion_id, title, url, summary, full_log, philosophers, themes, \
            episode_type, difficulty, ludicrea_relevance, embedding_bytes, _ = row

            embedding = None
            if embedding_bytes:
                embedding = np.frombuffer(embedding_bytes, dtype=np.float32)

            ep = Episode(
                notion_id=notion_id,
                title=title,
                url=url,
                summary=summary,
                full_log=full_log,
                philosophers=json.loads(philosophers) if philosophers else [],
                themes=json.loads(themes) if themes else [],
                episode_type=episode_type,
                difficulty=difficulty,
                ludicrea_relevance=ludicrea_relevance,
                embedding=embedding,
            )
            episodes.append(ep)

        log.info(f"📦 キャッシュから {len(episodes)} 件読み込み")
        return episodes


# ════════════════════════════════════════════════════════════════════════════
# 推薦エンジン
# ════════════════════════════════════════════════════════════════════════════

class RecommendationEngine:
    """ユーザー入力 → 推薦エピソード"""

    def __init__(self, episodes: List[Episode]):
        self.episodes = episodes
        self.model = SentenceTransformer(EMBEDDING_MODEL_NAME)

        # Embeddingをnumpy配列に統合
        self.embedding_matrix = np.array([
            ep.embedding if ep.embedding is not None else np.zeros(384)
            for ep in episodes
        ])

    def recommend(
        self,
        questions: List[str],
        philosopher_boosts: Optional[List[str]] = None,
        theme_boosts: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> List[Tuple[Episode, float]]:
        """
        ユーザーの「気になる問い」から推薦エピソードを取得

        Args:
            questions: ユーザーが選択した問い（3~5個）
            philosopher_boosts: マッチすると推薦度を上げる哲学者リスト
            theme_boosts: マッチすると推薦度を上げるテーマリスト
            top_k: 推薦件数

        Returns:
            [(Episode, スコア), ...] のリスト（スコア降順）
        """
        if not questions:
            log.warning("質問が空です")
            return []

        # ユーザー入力をEmbedding化
        user_text = " ".join(questions)
        user_embedding = self.model.encode(user_text, convert_to_numpy=True)

        # コサイン類似度を計算
        similarities = cosine_similarity(
            user_embedding.reshape(1, -1),
            self.embedding_matrix,
        ).flatten()

        # タグマッチングでブースト
        boosts = np.ones(len(self.episodes))

        if philosopher_boosts:
            for i, ep in enumerate(self.episodes):
                if any(p in ep.philosophers for p in philosopher_boosts):
                    boosts[i] *= 1.2  # 20% ブースト

        if theme_boosts:
            for i, ep in enumerate(self.episodes):
                if any(t in ep.themes for t in theme_boosts):
                    boosts[i] *= 1.2

        # スコア = 類似度 × タグブースト
        scores = similarities * boosts

        # 上位k件を取得
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = [
            (self.episodes[idx], float(scores[idx]))
            for idx in top_indices
        ]

        return results


# ════════════════════════════════════════════════════════════════════════════
# メイン処理
# ════════════════════════════════════════════════════════════════════════════

def init_cache():
    """初期化：Notionから読み込み → Embeddingを生成・キャッシュ"""
    log.info("🚀 推薦エンジン初期化開始")

    # Notionから全エピソードを読み込み
    episodes = fetch_all_episodes()

    # Embeddingを生成・キャッシュ
    cache = EmbeddingCache()
    cache.generate_and_cache_embeddings(episodes)

    log.info("✅ 初期化完了")


def get_episodes(
    philosophers: Optional[List[str]] = None,
    themes: Optional[List[str]] = None,
    search_query: str = "",
) -> Tuple[List[Episode], int]:
    """
    最新仕様：哲学者・テーマ・キーワードで検索
    
    Args:
        philosophers: 選択された哲学者リスト
        themes: 選択されたテーマリスト
        search_query: サブテーマキーワード（例：「生き方について」）
    
    Returns:
        (エピソードリスト, フォールバックレベル)
    """
    cache = EmbeddingCache()
    episodes = cache.load_all_episodes()
    
    fallback_level = 0
    candidates = episodes
    
    # Level 0: 両方マッチ（哲学者 AND テーマ）
    if philosophers and themes:
        candidates = [
            ep for ep in episodes
            if (any(p in ep.philosophers for p in philosophers)
                and any(t in ep.themes for t in themes))
        ]
        
        # 5個未満ならフォールバック
        if len(candidates) < 5:
            fallback_level = 1
            # Level 1: 片方マッチ（哲学者 OR テーマ）
            candidates = [
                ep for ep in episodes
                if (any(p in ep.philosophers for p in philosophers)
                    or any(t in ep.themes for t in themes))
            ]
    
    # Level 1.5: 哲学者またはテーマのいずれかのみ指定
    elif philosophers or themes:
        candidates = [
            ep for ep in episodes
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
            ep for ep in episodes
            if (search_lower in ep.title.lower()
                or search_lower in ep.summary.lower())
        ]
    
    # Level 3: すべてのエピソード（新しい順）
    if len(candidates) < 5:
        fallback_level = 3
        candidates = episodes
    
    # スコア計算（Embedding による類似度または新しい順）
    if search_query and candidates:
        cache.load_model()
        user_embedding = cache.model.encode(search_query, convert_to_numpy=True)
        
        scores = []
        for ep in candidates:
            if ep.embedding is not None:
                sim = cosine_similarity(
                    user_embedding.reshape(1, -1),
                    ep.embedding.reshape(1, -1)
                )[0, 0]
                scores.append(float(sim))
            else:
                scores.append(0.0)
        
        # スコア降順でソート
        sorted_indices = np.argsort(scores)[::-1]
        candidates = [candidates[i] for i in sorted_indices]
    else:
        # タグのみの場合は「新しい順」（後ろのエピソード優先）
        candidates = candidates[::-1]  # 逆順（新しい順）
    
    # 最大5個を返す
    return candidates[:5], fallback_level


def get_recommendations(
    questions: List[str],
    philosopher_boosts: Optional[List[str]] = None,
    theme_boosts: Optional[List[str]] = None,
    top_k: int = 5,
) -> List[Dict]:
    """
    【非推奨】古い推薦ロジック
    新しいコードは get_episodes() を使用してください
    """
    cache = EmbeddingCache()
    episodes = cache.load_all_episodes()

    engine = RecommendationEngine(episodes)
    results = engine.recommend(
        questions,
        philosopher_boosts=philosopher_boosts,
        theme_boosts=theme_boosts,
        top_k=top_k,
    )

    return [
        {
            **ep.to_dict(),
            "score": float(score),
        }
        for ep, score in results
    ]


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "init":
        init_cache()
    else:
        # テスト推薦
        results = get_recommendations(
            questions=["存在とは何か", "生成と変化の関係性", "言語と意味の問題"],
            theme_boosts=["存在論", "言語哲学"],
        )

        log.info(f"\n📌 推薦結果（上位5件）:")
        for i, res in enumerate(results, 1):
            log.info(f"  {i}. {res['title'][:60]} (スコア: {res['score']:.3f})")
