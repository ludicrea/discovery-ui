/**
 * script-discovery-v2.js
 * それ哲ラジオ Discovery UI v2
 * 
 * 特徴:
 * - 2段階選択UI（哲学者/テーマ → サブテーマ）
 * - YouTube サムネイル表示
 * - Notion への遷移なし（YouTube リンクのみ）
 * - Google Analytics 簡略版（重要なイベントのみ）
 */

// ─────────────────────────────────────────────────────────────
// 定数 & グローバル状態
// ─────────────────────────────────────────────────────────────

const API_BASE = "";
const API_CONFIG = `${API_BASE}/api/config`;
const API_DISCOVER = `${API_BASE}/api/discover`;

// YouTube サムネイル URL テンプレート
const YOUTUBE_THUMBNAIL = (videoId) => 
    `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`;

let appState = {
    selectedMain: null,      // 選択された哲学者またはテーマ
    selectedMainType: null,  // "philosopher" or "theme"
    selectedSub: null,       // 選択されたサブテーマ
    config: null,
};

// Google Analytics簡略版（gtag がなくても動作）
function trackEvent(eventName, eventData = {}) {
    try {
        if (typeof gtag !== "undefined") {
            gtag("event", eventName, eventData);
        }
    } catch (e) {
        // GA エラーを無視
    }
    console.log(`[Event] ${eventName}:`, eventData);
}

// ─────────────────────────────────────────────────────────────
// 初期化
// ─────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
    log("🚀 初期化開始");
    
    try {
        trackEvent("page_view", { page_path: "/discovery" });
    } catch (e) {
        console.warn("GA トラッキングエラー（無視）:", e);
    }

    try {
        appState.config = await fetchConfig();
        
        if (!appState.config || !appState.config.philosophers) {
            throw new Error("設定データが無効です");
        }

        renderWordcloud();
        renderSubthemes(null, null);
        attachEventListeners();

        log("✅ 初期化完了");
    } catch (error) {
        logError("初期化失敗", error);
        // アラートを表示しない（ネットワークエラーで中断させない）
        log("⚠️ API エラーですが、フォールバックで継続");
    }
});

// ─────────────────────────────────────────────────────────────
// API 呼び出し
// ─────────────────────────────────────────────────────────────

async function fetchConfig() {
    try {
        const res = await fetch(API_CONFIG);
        if (!res.ok) throw new Error("Config API 失敗");
        return await res.json();
    } catch (error) {
        console.warn("Config API から取得失敗、デフォルト値を使用:", error);
        // フォールバック：ハードコードされたデータを返す
        return {
            philosophers: [
                "荘子", "ホワイトヘッド", "山内得立", "カント", "ヘーゲル",
                "ニーチェ", "ハイデガー", "ウィトゲンシュタイン", "アリストテレス",
                "プラトン", "キルケゴール", "フッサール", "ドゥルーズ", "レヴィナス",
                "ナーガールジュナ", "親鸞", "道元"
            ],
            themes: [
                "存在論", "認識論", "倫理学", "言語哲学", "時間・生成",
                "自由・意志", "関係・他者", "美・創造", "死・無常", "日常・実践",
                "心・意識", "社会・政治", "宗教・信仰", "科学・技術", "意味・価値",
                "西洋", "仏教", "日本哲学"
            ]
        };
    }
}

async function discover(philosophers = [], themes = [], subtheme = "") {
    showLoading(true);
    
    try {
        const payload = {
            philosophers,
            themes,
            search_query: subtheme,
            top_k: 5,  // ← 常に5個に固定
        };

        try {
            trackEvent("discovery_search", {
                philosopher_selected: philosophers.length > 0,
                theme_selected: themes.length > 0,
                has_subtheme: subtheme.length > 0,
            });
        } catch (e) {
            console.warn("GA トラッキングエラー（無視）:", e);
        }

        const res = await fetch(API_DISCOVER, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        if (!res.ok) {
            const errorData = await res.json();
            throw new Error(errorData.error || "検索API 失敗");
        }

        const data = await res.json();
        log(`✅ ${data.results.length} 件のエピソードを発見`);

        try {
            trackEvent("discovery_results", {
                results_count: data.results.length,
                fallback_level: data.fallback_level,
            });
        } catch (e) {
            console.warn("GA トラッキングエラー（無視）:", e);
        }

        // グローバルに保存（フォールバック情報を使用）
        appState.lastSearchFallbackLevel = data.fallback_level;
        appState.lastSearchMessage = data.message;

        return data.results;

    } catch (error) {
        logError("検索失敗", error);
        alert("検索に失敗しました。しばらく待ってから再度お試しください。");
        return null;
    } finally {
        showLoading(false);
    }
}

// ─────────────────────────────────────────────────────────────
// UI レンダリング
// ─────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────
// UI レンダリング
// ─────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────
// ワードクラウド ページング
// ─────────────────────────────────────────────────────────────

// デバイスに応じてページサイズを切り替え
function getWordcloudItemsPerPage() {
    if (window.innerWidth < 480) {
        return 10;  // スマホ: 10個
    } else if (window.innerWidth < 768) {
        return 12;  // タブレット: 12個
    } else {
        return 14;  // デスクトップ: 12個
    }
}

let WORDCLOUD_ITEMS_PER_PAGE = getWordcloudItemsPerPage();

let wordcloudState = {
    allItems: [],
    currentPageItems: [],
    currentPageIndex: 0,
};

function renderWordcloud() {
    const container = document.getElementById("wordcloud");
    if (!container || !appState.config) return;

    // クリア
    container.innerHTML = "";

    // 哲学者とテーマを混ぜる（全アイテムを保存）
    let allItems = [
        ...appState.config.philosophers.map(p => ({ name: p, type: "philosopher", color: getPhilosopherColor(p) })),
        ...appState.config.themes.map(t => ({ name: t, type: "theme", color: getThemeColor(t) })),
    ];

    // 最低5個を確保 ← ここから追加
    if (allItems.length < 5) {
        const needed = 5 - allItems.length;
        const duplicated = allItems.slice(0, needed);
        allItems = [...allItems, ...duplicated];
    }
    // ← ここまで追加

    // グローバル状態に保存
    wordcloudState.allItems = allItems;
    wordcloudState.currentPageIndex = 0;

    // ワードクラウドの高さをリセット（PCサイズ用）
    const wrapper = document.querySelector(".wordcloud-wrapper");
    if (wrapper) {
        wrapper.style.height = "";  // インラインスタイルをクリア
    }

    // 最初のページを表示
    displayWordcloudPage();

    // 「他を表示」ボタンを作成（初回のみ、ワードクラウドコンテナ内に配置）
    if (!document.getElementById("wordcloud-next-btn-container")) {
        const wordcloudContainer = document.querySelector(".wordcloud-container");
        const btnContainer = document.createElement("div");
        btnContainer.id = "wordcloud-next-btn-container";
        btnContainer.className = "wordcloud-button-container";
        
        const btn = document.createElement("button");
        btn.id = "wordcloud-next-btn";
        btn.className = "btn btn-secondary";
        btn.textContent = "🔄 他の関心を表示";
        
        btn.addEventListener("click", () => {
            wordcloudState.currentPageIndex = (wordcloudState.currentPageIndex + 1) % Math.ceil(wordcloudState.allItems.length / WORDCLOUD_ITEMS_PER_PAGE);
            
            // ボタン押下時も高さをリセット
            if (wrapper) {
                wrapper.style.height = "";  // インラインスタイルをクリア
            }
            
            displayWordcloudPage();
            
            try {
                trackEvent("wordcloud_page_switched", {
                    page_index: wordcloudState.currentPageIndex,
                });
            } catch (e) {
                console.warn("GA トラッキングエラー（無視）:", e);
            }
        });
        
        btnContainer.appendChild(btn);
        // ワードクラウドコンテナの中の instruction の下に挿入
        const instruction = wordcloudContainer.querySelector(".wordcloud-instruction");
        if (instruction) {
            instruction.parentElement.insertBefore(btnContainer, instruction.nextSibling);
        } else {
            wordcloudContainer.appendChild(btnContainer);
        }
    }

    // アニメーションを定義（最初のページのみ）
    if (!document.getElementById("wordcloud-animation")) {
        const style = document.createElement("style");
        style.id = "wordcloud-animation";
        style.textContent = `
            @keyframes fadeInWord {
                0% {
                    opacity: 0;
                    transform: scale(0.4) translateY(20px);
                }
                70% {
                    transform: scale(1.05);
                }
                100% {
                    opacity: 1;
                    transform: scale(1) translateY(0);
                }
            }
        `;
        document.head.appendChild(style);
    }
}

function displayWordcloudPage() {
    const container = document.getElementById("wordcloud");
    if (!container) return;

    // クリア
    container.innerHTML = "";

    // 現在のページのアイテムを取得
    const startIdx = wordcloudState.currentPageIndex * WORDCLOUD_ITEMS_PER_PAGE;
    const endIdx = startIdx + WORDCLOUD_ITEMS_PER_PAGE;
    wordcloudState.currentPageItems = wordcloudState.allItems.slice(startIdx, endIdx);

    // シャッフル（毎回異なる配置にする）
    const shuffled = wordcloudState.currentPageItems.sort(() => Math.random() - 0.5);

    // コンテナのサイズ
    const containerWidth = container.clientWidth - 15;
    const containerHeight = container.clientHeight - 15;

    // デバイスに応じた配置パラメータを調整
    const isMobile = containerWidth < 768;
    const isSmallMobile = containerWidth < 480;

    // 配置：改善されたスパイラル配置（重なり回避）
    const placed = [];
    let angle = 0;
    let radius = isMobile ? 50 : 80;  // モバイルではさらに小さい開始半径
    
    // 配置座標の最大値を追跡（高さ調整用）
    let maxY = 0;

    shuffled.forEach((item, idx) => {
        const size = getRandomSize();
        const itemWidth = getItemWidth(size);
        const itemHeight = 40;

        // 重なりを避けるまでループ
        let attempts = 0;
        let x, y;
        
        do {
            const angleOffset = (Math.random() - 0.5) * (isSmallMobile ? 0.3 : 0.5);
            const radiusOffset = (Math.random() - 0.5) * (isSmallMobile ? 40 : 60);
            
            const centerX = containerWidth / 2;
            const centerY = containerHeight / 2;
            x = centerX + Math.cos(angle + angleOffset) * (radius + radiusOffset) - itemWidth / 2;
            y = centerY + Math.sin(angle + angleOffset) * (radius + radiusOffset) - itemHeight / 2;

            x = Math.max(5, Math.min(x, containerWidth - itemWidth - 5));
            y = Math.max(5, Math.min(y, containerHeight - itemHeight - 5));

            attempts++;
        } while (
            isCollidingStrict(x, y, itemWidth, itemHeight, placed) && 
            attempts < 20
        );

        if (attempts < 20) {
            placed.push({ x, y, width: itemWidth, height: itemHeight });
            maxY = Math.max(maxY, y + itemHeight);  // 最大高さを記録

            const wordEl = document.createElement("div");
            wordEl.className = `wordcloud-item size-${size}`;
            wordEl.dataset.name = item.name;
            wordEl.dataset.type = item.type;
            wordEl.textContent = item.name;
            wordEl.style.left = `${x}px`;
            wordEl.style.top = `${y}px`;
            wordEl.style.color = item.color;
            wordEl.style.animation = `fadeInWord 0.8s cubic-bezier(0.34, 1.56, 0.64, 1) ${Math.random() * 0.8}s both`;

            wordEl.addEventListener("click", () => {
                selectMain(item.name, item.type);
            });

            container.appendChild(wordEl);
        }

        angle += 0.5 + Math.random() * 0.3;
        radius += (isMobile ? 12 : 20) + Math.random() * (isMobile ? 8 : 15);
    });

    // 高さ調整（デバイスに応じて）
    const wrapper = container.parentElement;
    if (wrapper) {
        const containerWidth = container.clientWidth;
        const isDesktop = containerWidth >= 768;
        const isMobile = containerWidth < 768;

        if (isDesktop) {
            // デスクトップではインラインスタイルをクリア（CSSの固定値を使用）
            wrapper.style.height = "";
        } else if (isMobile && maxY > 0) {
            // モバイルで空白が目立たないよう、動的に高さを調整
            const desiredHeight = Math.min(Math.max(maxY + 40, 350), container.clientHeight);
            wrapper.style.height = `${desiredHeight}px`;
        }
    }

    // ページ情報をログ出力
    const totalPages = Math.ceil(wordcloudState.allItems.length / WORDCLOUD_ITEMS_PER_PAGE);
    console.log(`📄 ワードクラウド: ページ ${wordcloudState.currentPageIndex + 1}/${totalPages} (${wordcloudState.currentPageItems.length}個)`);
}

function getItemWidth(size) {
    // サイズに応じた幅を返す（小さいと狭く、大きいと広く）
    const widths = [80, 100, 120, 140, 160];
    return widths[size - 1] || 120;
}

function isCollidingStrict(x, y, width, height, placed) {
    // より厳しい衝突判定（パディング付き）
    const padding = 15;
    
    return placed.some(item => {
        return !(
            x + width + padding < item.x ||
            x > item.x + item.width + padding ||
            y + height + padding < item.y ||
            y > item.y + item.height + padding
        );
    });
}

function getRandomSize() {
    // 1～5 のサイズを確率的に割り当て
    const rand = Math.random();
    if (rand < 0.4) return 2;
    if (rand < 0.7) return 3;
    if (rand < 0.85) return 1;
    if (rand < 0.95) return 4;
    return 5;
}

function getPhilosopherColor(name) {
    // 哲学者ごとに色を分配（淡い色）
    const colors = [
        "#5ba3d0",  // 淡い青
        "#7ec8a0",  // パステル緑
        "#c9a8d8",  // 薄紫
        "#9fb8d4",  // ライト青
        "#a8d4a0",  // ライト緑
        "#d4a8c9",  // ライト紫
        "#6b9cbd",  // 深い淡い青
        "#7fb8a0",  // 深いパステル緑
    ];
    return colors[name.charCodeAt(0) % colors.length];
}

function getThemeColor(name) {
    // テーマごとに色を分配（淡い色）
    const colors = [
        "#d4a8b8",  // 淡いピンク
        "#b8d4a8",  // 淡い黄緑
        "#a8b8d4",  // 淡い紫青
        "#d4c9a8",  // 淡いオレンジ
        "#c9a8d4",  // 薄紫
        "#a8d4c9",  // 淡い青緑
    ];
    return colors[name.charCodeAt(0) % colors.length];
}

function selectMain(name, type) {
    appState.selectedMain = name;
    appState.selectedMainType = type;

    // ワードクラウドアイテムの選択状態を更新
    document.querySelectorAll(".wordcloud-item").forEach(item => {
        const isSelected = item.dataset.name === name;
        item.classList.toggle("selected", isSelected);
    });

    // 選択表示を更新
    const displayEl = document.getElementById("selected-main");
    if (displayEl) {
        displayEl.textContent = name;
    }
    document.getElementById("selection-display").style.display = "block";

    // ステップ2へ遷移
    setTimeout(() => {
        renderSubthemes(name, type);
        showStep(2);
    }, 300);

    try {
        trackEvent("main_selected", {
            main_type: type,
            main_name: name,
        });
    } catch (e) {
        console.warn("GA トラッキングエラー（無視）:", e);
    }
}

function renderSubthemes(mainName, mainType) {
    const container = document.getElementById("subtheme-grid");
    const displayEl = document.getElementById("selected-main-display");

    if (displayEl) {
        displayEl.textContent = mainName;
    }

    // サブテーマを「～について」形式に統一
    const subthemes = [
        { icon: "🧬", label: "生き方について", desc: "人生・倫理・実践" },
        { icon: "📜", label: "歴史について", desc: "思想史・時代背景" },
        { icon: "💭", label: "認識の仕方について", desc: "知識・意識・真理" },
        { icon: "🌍", label: "世界観について", desc: "存在・自然・宇宙" },
        { icon: "🔗", label: "関係性について", desc: "他者・共同・相互作用" },
        { icon: "✨", label: "創造について", desc: "美・芸術・表現" },
    ];

    container.innerHTML = subthemes
        .map((s, idx) => `
            <button class="subtheme-card" data-subtheme-idx="${idx}">
                <span class="subtheme-icon">${s.icon}</span>
                <div class="subtheme-label">${s.label}</div>
                <div class="subtheme-desc">${s.desc}</div>
            </button>
        `)
        .join("");

    container.querySelectorAll(".subtheme-card").forEach(card => {
        card.addEventListener("click", () => {
            selectSubtheme(card, subthemes);
        });
    });
}

function selectSubtheme(card, subthemes) {
    const idx = parseInt(card.dataset.subthemeIdx);
    const selected = subthemes[idx].label;  // 「～について」形式のラベルをそのまま使用

    appState.selectedSub = selected;

    // UI更新
    document.querySelectorAll(".subtheme-card").forEach(c => {
        c.classList.toggle("selected", c === card);
    });

    const displayEl = document.getElementById("selected-sub");
    if (displayEl) {
        displayEl.textContent = selected;
    }
    document.getElementById("subtheme-display").style.display = "block";

    try {
        trackEvent("subtheme_selected", {
            subtheme: selected,
        });
    } catch (e) {
        console.warn("GA トラッキングエラー（無視）:", e);
    }
}

function renderResults(results) {
    const container = document.getElementById("results-grid");
    if (!container) return;

    // フォールバック通知を表示（必要な場合）
    const parent = container.parentElement;
    
    // 既存の通知があれば削除
    const existingNotice = parent.querySelector(".fallback-notice");
    if (existingNotice) {
        existingNotice.remove();
    }
    
    // フォールバック通知を追加
    if (appState.lastSearchMessage) {
        const notice = document.createElement("div");
        notice.className = "fallback-notice";
        notice.innerHTML = `
            <div class="fallback-message">
                ${escapeHtml(appState.lastSearchMessage)}
            </div>
        `;
        parent.insertBefore(notice, container);
    }

    // エピソードカードをレンダリング
    container.innerHTML = results
        .map((ep, idx) => {
            const videoId = extractYouTubeVideoId(ep.url);
            const thumbnailUrl = videoId 
                ? YOUTUBE_THUMBNAIL(videoId)
                : "https://via.placeholder.com/320x180?text=No+Image";

            return `
                <a href="${escapeHtml(ep.url)}" target="_blank" class="episode-card">
                    <div class="episode-thumbnail">
                        <img src="${thumbnailUrl}" alt="${escapeHtml(ep.title)}" onerror="this.src='https://via.placeholder.com/320x180?text=Video'">
                    </div>
                    <div class="episode-info">
                        <div class="episode-title">${escapeHtml(ep.title)}</div>
                        <div class="episode-meta">
                            ${ep.episode_type ? `<span class="meta-badge type">${escapeHtml(ep.episode_type)}</span>` : ""}
                            ${ep.difficulty ? `<span class="meta-badge difficulty">難易度: ${escapeHtml(ep.difficulty)}</span>` : ""}
                        </div>
                        <div class="episode-summary">${escapeHtml(ep.summary?.substring(0, 80) || "")}</div>
                        <a href="${escapeHtml(ep.url)}" class="episode-link" onclick="event.stopPropagation();">
                            🎙️ YouTube で聴く
                        </a>
                    </div>
                </a>
            `;
        })
        .join("");

    // Google Analytics: 結果表示
    try {
        trackEvent("results_viewed", {
            results_count: results.length,
            main_selected: appState.selectedMain,
            subtheme_selected: appState.selectedSub || "なし",
            fallback_level: appState.lastSearchFallbackLevel || 0,
        });
    } catch (e) {
        console.warn("GA トラッキングエラー（無視）:", e);
    }
}

// ─────────────────────────────────────────────────────────────
// イベントハンドラ
// ─────────────────────────────────────────────────────────────

function attachEventListeners() {
    // 検索ボタン（ステップ2）
    const searchBtn = document.getElementById("search-btn");
    if (searchBtn) {
        searchBtn.addEventListener("click", async () => {
            if (!appState.selectedMain) {
                alert("まず哲学者またはテーマを選んでください");
                return;
            }

            const philosophers = appState.selectedMainType === "philosopher" 
                ? [appState.selectedMain] 
                : [];
            const themes = appState.selectedMainType === "theme" 
                ? [appState.selectedMain] 
                : [];
            
            const results = await discover(
                philosophers,
                themes,
                appState.selectedSub || ""
            );

            if (results) {
                renderResults(results);
                showStep(3);
            }
        });
    }

    // 戻るボタン（ステップ2）
    const backBtn = document.getElementById("back-btn");
    if (backBtn) {
        backBtn.addEventListener("click", () => {
            resetSelection();
            showStep(1);
            try {
                trackEvent("back_to_step1", {});
            } catch (e) {
                console.warn("GA トラッキングエラー（無視）:", e);
            }
        });
    }

    // 別の関心で探す（ステップ3）
    const backToStep1Btn = document.getElementById("back-to-step1-btn");
    if (backToStep1Btn) {
        backToStep1Btn.addEventListener("click", () => {
            resetSelection();
            showStep(1);
            try {
                trackEvent("restart", {});
            } catch (e) {
                console.warn("GA トラッキングエラー（無視）:", e);
            }
        });
    }
}

function resetSelection() {
    appState.selectedMain = null;
    appState.selectedMainType = null;
    appState.selectedSub = null;

    document.querySelectorAll(".tag-item").forEach(item => {
        item.classList.remove("selected");
    });

    document.querySelectorAll(".subtheme-card").forEach(card => {
        card.classList.remove("selected");
    });

    document.getElementById("selection-display").style.display = "none";
    document.getElementById("subtheme-display").style.display = "none";
}

// ─────────────────────────────────────────────────────────────
// UI 制御
// ─────────────────────────────────────────────────────────────

function showStep(stepNumber) {
    document.querySelectorAll(".step").forEach(step => {
        const stepNum = step.id.match(/\d+/)[0];
        step.style.display = stepNum == stepNumber ? "block" : "none";
    });

    window.scrollTo({ top: 0, behavior: "smooth" });
}

function showLoading(show) {
    const overlay = document.getElementById("loading-overlay");
    if (overlay) {
        overlay.style.display = show ? "flex" : "none";
    }
}

// ─────────────────────────────────────────────────────────────
// ユーティリティ
// ─────────────────────────────────────────────────────────────

function extractYouTubeVideoId(url) {
    try {
        // youtube.com の場合
        const urlObj = new URL(url);
        if (urlObj.hostname.includes('youtube.com')) {
            return urlObj.searchParams.get("v") || null;
        }
        // youtu.be の場合
        if (urlObj.hostname.includes('youtu.be')) {
            return urlObj.pathname.substring(1);
        }
        return null;
    } catch {
        return null;
    }
}

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function log(message) {
    console.log(`[Discovery] ${message}`);
}

function logError(message, error) {
    console.error(`[Discovery] ${message}:`, error);
}
