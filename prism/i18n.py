"""Lightweight i18n: zero-dependency translation dictionaries for prism.

Usage in templates: {{ t('key') }} — the `t` function is injected into every
template context and falls back to the English string if a key is missing.
"""

from functools import lru_cache

LANGUAGES = {
    "zh-TW": "繁體中文",
    "en": "English",
}

STRINGS = {
    # --- base.html sidebar ---
    "client": {"zh-TW": "客戶", "en": "Client"},
    "manage_clients": {"zh-TW": "管理客戶 →", "en": "Manage clients →"},
    "dashboard": {"zh-TW": "儀表板", "en": "Dashboard"},
    "overview": {"zh-TW": "總覽", "en": "Overview"},
    "visibility": {"zh-TW": "可見度", "en": "Visibility"},
    "share_of_voice": {"zh-TW": "聲量佔比", "en": "Share of Voice"},
    "citations": {"zh-TW": "引用來源", "en": "Citations"},
    "opportunities": {"zh-TW": "成長機會", "en": "Opportunities"},
    "settings": {"zh-TW": "設定", "en": "Settings"},
    "brand_competitors": {"zh-TW": "品牌與競爭者", "en": "Brand & competitors"},
    "prompts": {"zh-TW": "提問", "en": "Prompts"},
    "operator": {"zh-TW": "管理員", "en": "Operator"},
    "clients": {"zh-TW": "客戶", "en": "Clients"},
    "engine_keys": {"zh-TW": "引擎金鑰", "en": "Engine keys"},
    "setup_brand": {"zh-TW": "+ 設定 {name}", "en": "+ Set up {name}"},
    "footer_credit": {
        "zh-TW": "重新詮釋 GEO 概念<br>靈感來自 elmohq/elmo",
        "en": "rewrite of the GEO concept<br>inspired by elmohq/elmo",
    },

    # --- title blocks ---
    "title_default": {"zh-TW": "prism · AI 可見度", "en": "prism · AI visibility"},
    "title_overview": {"zh-TW": "總覽 · prism", "en": "Overview · prism"},
    "title_visibility": {"zh-TW": "可見度 · prism", "en": "Visibility · prism"},
    "title_sov": {"zh-TW": "聲量佔比 · prism", "en": "Share of Voice · prism"},
    "title_citations": {"zh-TW": "引用來源 · prism", "en": "Citations · prism"},
    "title_opportunities": {"zh-TW": "成長機會 · prism", "en": "Opportunities · prism"},
    "title_job_running": {"zh-TW": "執行評估中 · prism", "en": "Running evaluation · prism"},
    "title_setup_review": {"zh-TW": "工作區已就緒 · prism", "en": "Workspace ready · prism"},
    "title_clients": {"zh-TW": "客戶 · prism", "en": "Clients · prism"},
    "title_settings_brand": {"zh-TW": "品牌設定 · prism", "en": "Brand settings · prism"},
    "title_settings_keys": {"zh-TW": "引擎金鑰 · prism", "en": "Engine keys · prism"},
    "title_settings_prompts": {"zh-TW": "提問管理 · prism", "en": "Prompt management · prism"},
    "title_setup": {"zh-TW": "設定工作區 · prism", "en": "Set up your brand · prism"},

    # --- overview page ---
    "no_brand_title": {"zh-TW": "尚未設定品牌", "en": "No brand configured yet"},
    "no_brand_desc": {"zh-TW": "設定您的品牌與競爭者，開始追蹤 AI 可見度。", "en": "Set up your brand and competitors to start tracking AI visibility."},
    "setup_your_brand": {"zh-TW": "設定您的品牌", "en": "Set up your brand"},
    "overview_of": {"zh-TW": "{name} 總覽", "en": "{name} overview"},
    "overview_desc": {"zh-TW": "過去 {days} 天內答案引擎如何提及您的品牌。", "en": "How answer engines talked about your brand in the last {days} days."},
    "last_n_days": {"zh-TW": "過去 {n} 天", "en": "Last {n} days"},
    "visibility_pct": {"zh-TW": "可見度", "en": "Visibility"},
    "runs_mention_brand": {"zh-TW": "的評估提及 {name}", "en": "of runs mention {name}"},
    "prompts_tracked": {"zh-TW": "追蹤提問數", "en": "Prompts tracked"},
    "evaluations": {"zh-TW": "評估次數", "en": "Evaluations"},
    "total_citations": {"zh-TW": "引用總數", "en": "Total citations"},
    "last_evaluation": {"zh-TW": "上次評估", "en": "Last evaluation"},
    "never": {"zh-TW": "從未", "en": "Never"},
    "sov_top_brands": {"zh-TW": "聲量佔比 — 熱門品牌", "en": "Share of voice — top brands"},
    "full_breakdown": {"zh-TW": "完整分析 →", "en": "Full breakdown →"},

    # --- visibility page ---
    "visibility_page_title": {"zh-TW": "可見度", "en": "Visibility"},
    "visibility_page_desc": {"zh-TW": "查看答案引擎如何評估與您品牌相關的提問。", "en": "See how answer engines evaluate prompts related to your brand."},
    "all_models": {"zh-TW": "所有模型", "en": "All models"},
    "run_all": {"zh-TW": "⟳ 全部執行", "en": "⟳ Run all"},
    "visibility_label": {"zh-TW": "可見度", "en": "visibility"},
    "prompts_x_runs": {"zh-TW": "{prompts} 個提問 · {runs} 次評估", "en": "{prompts} prompts · {runs} runs"},
    "run_now": {"zh-TW": "立即執行 →", "en": "Run now →"},
    "runs_count": {"zh-TW": "{n} 次評估", "en": "{n} runs"},

    # --- share of voice page ---
    "sov_page_title": {"zh-TW": "聲量佔比", "en": "Share of Voice"},
    "sov_page_desc": {"zh-TW": "哪些品牌在所有追蹤提問的回答中佔據主導地位。", "en": "Which brands dominate the answers across all tracked prompts."},
    "daily_sov": {"zh-TW": "每日聲量佔比", "en": "Daily share of voice"},
    "mention_share": {"zh-TW": "提及佔比", "en": "Mention share"},
    "brand_col": {"zh-TW": "品牌", "en": "Brand"},
    "mentions_col": {"zh-TW": "提及次數", "en": "Mentions"},
    "share_col": {"zh-TW": "佔比", "en": "Share"},
    "avg_position_col": {"zh-TW": "平均排名", "en": "Avg. position"},
    "prompts_col": {"zh-TW": "提問數", "en": "Prompts"},
    "you_tag": {"zh-TW": "您", "en": "You"},

    # --- citations page ---
    "citations_page_title": {"zh-TW": "引用來源", "en": "Citations"},
    "citations_page_desc": {"zh-TW": "答案引擎引用的來源。在這些網站上獲得曝光以提升可見度。", "en": "The sources answer engines ground on. Get covered here to grow visibility."},
    "total_citations_stat": {"zh-TW": "引用總數", "en": "Total citations"},
    "cited_domains": {"zh-TW": "引用網域數", "en": "Cited domains"},
    "source_stability": {"zh-TW": "來源穩定度", "en": "Source stability"},
    "stability_desc": {"zh-TW": "引用來源組合的每日變動程度", "en": "how much the cited-source mix churns daily"},
    "by_source_type": {"zh-TW": "依來源類型", "en": "By source type"},
    "top_domains": {"zh-TW": "熱門網域", "en": "Top domains"},
    "top_cited_pages": {"zh-TW": "熱門引用頁面", "en": "Top cited pages"},

    # --- opportunities page ---
    "opportunities_page_title": {"zh-TW": "成長機會", "en": "Opportunities"},
    "opportunities_page_desc": {"zh-TW": "競爭者出現但您未出現的提問 — 依可贏取程度排名。", "en": "Prompts where competitors show up but you don't — ranked by how winnable they look."},
    "no_gaps": {"zh-TW": "未發現差距 — 您的品牌出現在每個追蹤提問中。表現優秀！", "en": "No gaps found — your brand is mentioned in every tracked prompt. Nice."},
    "competitors_present": {"zh-TW": "出現的競爭者：", "en": "competitors present:"},
    "geo_lever": {"zh-TW": "GEO 策略：在此提問引用的網域上獲得內容曝光，並發布直接回答此問題的內容。", "en": "GEO lever: earn coverage on the domains this prompt's answers cite, and publish content that directly answers it."},
    "your_visibility": {"zh-TW": "您的可見度", "en": "your visibility"},
    "opportunity_score": {"zh-TW": "機會分數 {score}/100", "en": "opportunity {score}/100"},

    # --- job page ---
    "running_evaluation": {"zh-TW": "執行評估中", "en": "Running evaluation"},
    "running_prompt": {"zh-TW": "執行提問中", "en": "Running prompt"},
    "job_desc": {"zh-TW": "正在背景查詢答案引擎。此頁面會即時更新。", "en": "Querying the answer engine in the background. This page updates live."},
    "cancel": {"zh-TW": "取消", "en": "Cancel"},
    "back_to_results": {"zh-TW": "← 返回結果", "en": "← Back to results"},
    "error_singular": {"zh-TW": "個錯誤", "en": "error"},
    "error_plural": {"zh-TW": "個錯誤", "en": "errors"},

    # --- tenants page ---
    "tenants_page_title": {"zh-TW": "客戶", "en": "Clients"},
    "tenants_page_desc": {
        "zh-TW": "每個客戶都是一個獨立工作區 — 包含一個追蹤品牌及其網域、競爭者和提問組合。引擎金鑰在所有客戶間共用，請在<a href=\"/settings/keys\" class=\"font-medium text-indigo-600 hover:underline\">引擎金鑰</a>中設定。",
        "en": "Each client is a workspace — one tracked brand with its own domain, competitors and prompt set. Engine keys are shared across all clients and configured once under <a href=\"/settings/keys\" class=\"font-medium text-indigo-600 hover:underline\">Engine keys</a>.",
    },
    "new_client_placeholder": {"zh-TW": "新客戶品牌 — 例如 Allbirds", "en": "New client brand — e.g. Allbirds"},
    "add_client": {"zh-TW": "新增客戶", "en": "Add client"},
    "client_col": {"zh-TW": "客戶", "en": "Client"},
    "domain_col": {"zh-TW": "網域", "en": "Domain"},
    "competitors_col": {"zh-TW": "競爭者", "en": "Competitors"},
    "prompts_table_col": {"zh-TW": "提問數", "en": "Prompts"},
    "runs_col": {"zh-TW": "評估數", "en": "Runs"},
    "active_tag": {"zh-TW": "使用中", "en": "Active"},
    "delete": {"zh-TW": "刪除", "en": "Delete"},
    "no_clients": {"zh-TW": "尚無客戶 — 請在上方新增第一個。", "en": "No clients yet — add your first above."},
    "confirm_delete": {"zh-TW": "確定要刪除 {name} 及其所有評估、提問和競爭者資料？", "en": "Delete {name} and all its runs, prompts and competitors?"},

    # --- settings: prompts ---
    "settings_prompts_title": {"zh-TW": "提問管理", "en": "Prompt management"},
    "settings_prompts_desc": {"zh-TW": "對每個提問定期透過每個啟用的答案引擎執行評估。使用 /visibility 頁面查看結果。", "en": "Each prompt is run against every enabled answer engine on a recurring cadence. Use /visibility to see results."},
    "total_runs_label": {"zh-TW": "總評估數", "en": "Total runs"},
    "new_prompt_placeholder": {"zh-TW": "新提問 — 例如「最好的跑鞋是什麼？」", "en": "New prompt — e.g. What are the best running shoes?"},
    "tags_optional": {"zh-TW": "標籤（選填，逗號分隔）", "en": "Tags (optional, comma-separated)"},
    "add_prompt": {"zh-TW": "新增提問", "en": "Add prompt"},
    "no_prompts": {"zh-TW": "尚無提問 — 請在上方新增第一個。", "en": "No prompts yet — add your first above."},

    # --- settings: brand ---
    "brand_settings_title": {"zh-TW": "品牌與競爭者", "en": "Brand & competitors"},
    "brand_settings_desc": {"zh-TW": "prism 追蹤的內容以及如何在回答文字中識別每個品牌。", "en": "What prism tracks and how it recognizes each brand in answer text."},
    "your_brand_label": {"zh-TW": "您的品牌名稱", "en": "Your brand name"},
    "website_label": {"zh-TW": "官網", "en": "Website"},
    "competitors_label": {"zh-TW": "競爭者", "en": "Competitors"},
    "competitors_hint": {"zh-TW": "逗號分隔 — 將在聲量佔比中並排顯示", "en": "comma separated — you'll show up side-by-side in Share of Voice"},
    "save": {"zh-TW": "儲存", "en": "Save"},
    "saving": {"zh-TW": "處理中", "en": "Saving"},
    "saving_setup_hint": {"zh-TW": "正在擷取網站資訊並生成提問，請稍候", "en": "Fetching website info and generating prompts — this may take a moment"},
    "edit": {"zh-TW": "編輯", "en": "Edit"},
    "add_competitor_placeholder": {"zh-TW": "競爭者名稱 — 例如 Nike", "en": "Competitor name — e.g. Nike"},
    "add_competitor_btn": {"zh-TW": "新增", "en": "Add"},
    "competitors_empty": {"zh-TW": "尚未新增競爭者。", "en": "No competitors added yet."},

    # --- settings: keys ---
    "keys_title": {"zh-TW": "引擎金鑰", "en": "Engine keys"},
    "keys_desc": {"zh-TW": "每個答案引擎使用獨立的 API 金鑰。金鑰儲存在此處，在 UI 設定時優先於環境變數。評估會針對所有已啟用且有金鑰的引擎執行。", "en": "Each answer engine uses its own API key. Keys set here take priority over environment variables. Evaluations fan out across every enabled engine that has a key — that's what makes cross-engine visibility real."},
    "provider_keys": {"zh-TW": "提供者金鑰", "en": "Provider keys"},
    "api_key_label": {"zh-TW": "API 金鑰", "en": "API key"},
    "base_url_label": {"zh-TW": "Base URL（選填）", "en": "Base URL (optional)"},
    "model_label": {"zh-TW": "模型（選填）", "en": "Model (optional)"},
    "enabled": {"zh-TW": "已啟用", "en": "Enabled"},
    "disabled": {"zh-TW": "已停用", "en": "Disabled"},
    "save_keys": {"zh-TW": "儲存金鑰", "en": "Save keys"},
    "schedule_title": {"zh-TW": "每日自動執行", "en": "Daily Auto-Run"},
    "schedule_desc": {"zh-TW": "每天在指定時間自動執行所有提問的評估", "en": "Automatically runs all prompts against enabled engines once per day"},
    "clear": {"zh-TW": "清除", "en": "Clear"},
    "saved_badge": {"zh-TW": "✓ 已儲存", "en": "✓ Saved"},
    "no_keys_warning": {"zh-TW": "尚未設定引擎金鑰 — 無法執行即時評估。新增金鑰以啟用「立即執行」功能。", "en": "No engine keys configured — live evaluations are disabled. Add a key to enable the Run now buttons."},

    # --- setup ---
    "setup_title": {"zh-TW": "設定工作區", "en": "Set up your workspace"},
    "setup_desc": {"zh-TW": "告訴 prism 要追蹤哪些品牌和提問。", "en": "Tell prism which brands and prompts to track."},
    "setup_brand_label": {"zh-TW": "品牌名稱", "en": "Brand name"},
    "setup_website_label": {"zh-TW": "官網", "en": "Website"},
    "setup_website_hint": {"zh-TW": "用於了解您的市場並識別您的引用來源", "en": "used to learn your market + spot your own citations"},
    "setup_competitors_label": {"zh-TW": "競爭者", "en": "Competitors"},
    "setup_competitors_hint": {"zh-TW": "逗號分隔 — 將在聲量佔比中並排顯示", "en": "comma separated — you'll show up side-by-side in Share of Voice"},
    "generate_starter": {"zh-TW": "使用 AI 生成入門提問組合", "en": "Generate a starter prompt set with AI"},
    "generate_starter_desc": {"zh-TW": "分析您的官網並生成約 10 個值得追蹤的買家型提問（比較、替代、預算、信任）。之後可以編輯。", "en": "Analyzes your website and drafts ~10 buyer-style questions (comparisons, alternatives, budget, trust) worth tracking. You can edit them after."},
    "update_workspace": {"zh-TW": "更新工作區", "en": "Update workspace"},
    "create_workspace": {"zh-TW": "建立工作區", "en": "Create workspace"},
    "no_llm_key": {"zh-TW": "未設定 LLM API 金鑰 — 提問生成將使用通用模板，即時評估已停用。", "en": "No LLM API key set — prompt generation will use generic templates and live runs are disabled."},

    # --- setup review ---
    "setup_done_title": {"zh-TW": "{name} 已設定完成", "en": "{name} is set up"},
    "setup_done_desc": {"zh-TW": "正在追蹤 {n} 個競爭者。已生成 {m} 個入門提問。", "en": "Tracking against {n} competitor(s). {m} starter prompt(s) generated."},
    "competitors_section": {"zh-TW": "競爭者", "en": "Competitors"},
    "none_added": {"zh-TW": "尚未新增。", "en": "None added."},
    "manage": {"zh-TW": "管理 →", "en": "Manage →"},
    "starter_prompts": {"zh-TW": "入門提問", "en": "Starter prompts"},
    "more_prompts": {"zh-TW": "+ 還有 {n} 個", "en": "+ {n} more"},
    "edit_prompts": {"zh-TW": "編輯提問 →", "en": "Edit prompts →"},
    "run_first_eval": {"zh-TW": "執行首次評估", "en": "Run your first evaluation"},
    "run_first_eval_desc": {"zh-TW": "立即將每個提問發送給答案引擎，用真實的提及和引用資料填滿您的儀表板。", "en": "Sends every prompt to the answer engine now and fills your dashboards with real mention + citation data."},
    "run_evaluation": {"zh-TW": "執行評估", "en": "Run evaluation"},
    "eval_time_note": {"zh-TW": "根據提問數量，可能需要一兩分鐘 — 完成後頁面會自動重新載入。", "en": "This can take a minute or two depending on how many prompts you have — the page will reload when done."},

    # --- prompt detail ---
    "prompt_detail_title": {"zh-TW": "提問詳情 · prism", "en": "Prompt detail · prism"},
    "visibility_over_n": {"zh-TW": "過去 {n} 天的可見度", "en": "visibility over the last {n} days"},
    "response_log": {"zh-TW": "回應記錄", "en": "Response log"},
    "full_text": {"zh-TW": "完整文字", "en": "Full text"},
    "engine": {"zh-TW": "引擎", "en": "Engine"},
    "citations_section": {"zh-TW": "引用來源", "en": "Citations"},
    "mentions_section": {"zh-TW": "提及", "en": "Mentions"},
    "no_runs": {"zh-TW": "尚無評估記錄。", "en": "No runs recorded yet."},

    # --- language switcher ---
    "language": {"zh-TW": "語言", "en": "Language"},
}


def _format(s: str, **kwargs) -> str:
    for k, v in kwargs.items():
        s = s.replace("{" + k + "}", str(v))
    return s


@lru_cache(maxsize=1)
def translations(lang: str) -> dict:
    """Return a lookup dict {key: translated_string} for the given language.

    Falls back to English for missing keys, then to the key itself.
    """
    out: dict[str, str] = {}
    for key, vals in STRINGS.items():
        out[key] = vals.get(lang) or vals.get("en", key)
    return out


def t(lang: str, key: str, **kwargs) -> str:
    """Translate a key into the given language, with optional formatting."""
    s = translations(lang).get(key, STRINGS.get(key, {}).get("en", key))
    if kwargs:
        return _format(s, **kwargs)
    return s
