// Based on the data structure from web_server.py and scraper.py

export interface ProductInfo {
  product_title: string;
  current_price: string;
  original_price?: string;
  wants_count?: string | number;
  product_tags?: string[];
  shipping_region?: string;
  seller_nickname?: string;
  product_link: string;
  publish_time?: string;
  item_id: string;
  image_list?: string[];
  main_image_url?: string;
  view_count?: string | number;
}

export interface SellerInfo {
  seller_nickname?: string;
  seller_avatar?: string;
  seller_bio?: string;
  seller_items_count?: string;
  seller_total_ratings?: string;
  seller_credit_level?: string;
  buyer_credit_level?: string;
  sesame_credit?: string;
  registration_duration?: string;
  seller_positive_ratings?: string;
  seller_positive_rate?: string;
  buyer_positive_ratings?: string;
  buyer_positive_rate?: string;
  seller_items?: any[]; // Define more strictly if needed
  seller_ratings?: any[]; // Define more strictly if needed
}

export interface AiAnalysis {
  is_recommended: boolean;
  reason: string;
  analysis_source?: 'ai' | 'keyword';
  keyword_hit_count?: number;
  value_score?: number;
  value_summary?: string;
  prompt_version?: string;
  risk_tags?: string[];
  criteria_analysis?: Record<string, any>;
  matched_keywords?: string[];
  error?: string;
}

export interface PriceInsight {
  observation_count: number;
  current_price?: number | null;
  avg_price?: number | null;
  median_price?: number | null;
  min_price?: number | null;
  max_price?: number | null;
  market_avg_price?: number | null;
  market_median_price?: number | null;
  price_change_amount?: number | null;
  price_change_percent?: number | null;
  deal_score?: number | null;
  deal_label?: string;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
}

export interface ResultInsights {
  market_summary: {
    sample_count: number;
    avg_price: number | null;
    median_price: number | null;
    min_price: number | null;
    max_price: number | null;
    snapshot_time?: string | null;
  };
  history_summary: {
    unique_items: number;
    sample_count: number;
    avg_price: number | null;
    median_price: number | null;
    min_price: number | null;
    max_price: number | null;
  };
  daily_trend: Array<{
    day: string;
    sample_count: number;
    avg_price: number | null;
    median_price: number | null;
    min_price: number | null;
    max_price: number | null;
  }>;
  latest_snapshot_at?: string | null;
}

export interface ResultItem {
  scraped_at: string;
  search_keyword: string;
  task_name: string;
  product_info: ProductInfo;
  seller_info: SellerInfo;
  ai_analysis: AiAnalysis;
  price_insight?: PriceInsight;
}
