
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

_LOCAL_BGE_DIR = PROJECT_ROOT.parent.parent.parent / "BAAI--bge-small-zh-v1.5"

__all__ = [
    "LLMConfig", "GatewayConfig", "SQLGuardConfig", "RetrievalConfig",
    "GraphConfig", "TokenBudgetConfig", "PathConfig", "CollectionConfig",
    "TierModelConfig", "ChartConfig", "CostConfig", "MetricsConfig",
    "AppConfig", "get_app_config", "PROJECT_ROOT",
]


@dataclass
class LLMConfig:
    model: str = "glm-5.3-flash"
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/"   
    api_key_env: str = "GLM_API_KEY"                          
    temperature: float = 0.1        
    timeout: int = 180              
    max_retries: int = 2            


@dataclass
class GatewayConfig:
    base_url: str = "http://127.0.0.1:8130"     
    database: str = "sales_dw"                  
    timeout_ms: int = 30_000
    max_rows: int = 1000                        
    secret: str = ""                            
    catalog_ttl_sec: int = 300                  
    health_check_on_start: bool = False         


@dataclass
class MetricsConfig:
    dictionary_path: str = "metrics/dictionary.yaml"   
    qa_harness_url: str = ""                    
    qa_harness_timeout_ms: int = 10_000


@dataclass
class SQLGuardConfig:
    max_rows: int = 1000                
    inject_limit: int = 200             
    max_sql_repairs: int = 2            
    enable_write_lane: bool = True      
    use_reviewer: bool = True          
    use_auto_repair: bool = True       
    max_write_rows: int = 1000                


@dataclass
class RetrievalConfig:
    glossary_top_k: int = 5
    fewshot_top_k: int = 3


@dataclass
class GraphConfig:


    max_repair_rounds: int = 2          
    max_escalations: int = 1            
    max_clarify_rounds: int = 2         
    empty_result_preview_rows: int = 50  
    recursion_limit: int = 25           
    max_approval_asks: int = 2           


@dataclass
class TokenBudgetConfig:
    window_tokens: int = 120_000
    cleanup_ratio: float = 0.4          
    compress_ratio: float = 0.7         


@dataclass
class PathConfig:
    checkpoint_dir: str = "data/checkpoints"    
    memory_dir: str = "data/memory_db"          
    chart_dir: str = "data/charts"              
    log_path: str = "logs/trace.jsonl"
    embed_local: str = str(_LOCAL_BGE_DIR)      
    embed_hf: str = "BAAI/bge-small-zh-v1.5"    


@dataclass
class CollectionConfig:
    memory_collection: str = "da_memory"        


@dataclass
class TierModelConfig:                          
    chitchat: str = "glm-5.3-flash"             
    lite: str = "glm-5.3-flash"                 
    deep: str = "glm-5.3-flash"                 
    clarify: str = "glm-5.3-flash"              


@dataclass
class CostConfig:

    currency: str = "CNY"
    price_in_per_m: float = 3.0
    price_out_per_m: float = 14.0
    prices: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {"glm-4.7": (3.0, 14.0)})

    def unit_price(self, model: str) -> tuple[float, float]:
        return self.prices.get(model, (self.price_in_per_m, self.price_out_per_m))


@dataclass
class MemoryConfig:

    user_id: str = "local"                      


@dataclass
class ChartConfig:

    enabled: bool = True                        
    dpi: int = 150
    figsize: tuple[float, float] = (10.0, 6.0)
    min_chart_rows: int = 2                     
    max_pie_slices: int = 8                     
    max_bar_rows: int = 30                      
    max_line_points: int = 60                   
    title_max_len: int = 40                     
    font_candidates: tuple[str, ...] = ("SimHei", "Microsoft YaHei", "SimSun", "KaiTi")


@dataclass
class AppConfig:
    llm: LLMConfig
    gateway: GatewayConfig
    sql_guard: SQLGuardConfig = field(default_factory=SQLGuardConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    token_budget: TokenBudgetConfig = field(default_factory=TokenBudgetConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    collections: CollectionConfig = field(default_factory=CollectionConfig)
    tier_models: TierModelConfig = field(default_factory=TierModelConfig)
    chart: ChartConfig = field(default_factory=ChartConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


_CONFIG: AppConfig | None = None


def get_app_config() -> AppConfig:
    global _CONFIG
    if _CONFIG is None:
        load_dotenv(PROJECT_ROOT / ".env")      

        default_model = os.getenv("GLM_MODEL", "glm-5.3-flash")
        tier_models = TierModelConfig(
            chitchat=os.getenv("MODEL_CHITCHAT", default_model),
            lite=os.getenv("MODEL_LITE", default_model),
            deep=os.getenv("MODEL_DEEP", default_model),
            clarify=os.getenv("MODEL_CLARIFY", os.getenv("MODEL_LITE", default_model)),
        )

        price_in = float(os.getenv("COST_PRICE_IN_PER_M", "3"))
        price_out = float(os.getenv("COST_PRICE_OUT_PER_M", "14"))
        cost = CostConfig(
            price_in_per_m=price_in,
            price_out_per_m=price_out,
            prices={default_model: (price_in, price_out)},
        )

        _CONFIG = AppConfig(
            llm=LLMConfig(
                model=default_model,
                base_url=os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
            ),
            gateway=GatewayConfig(
                base_url=os.getenv("QUERY_GATEWAY_URL", "http://127.0.0.1:8130"),
                database=os.getenv("GATEWAY_DATABASE", "sales_dw"),
                secret=os.getenv("AF_GATEWAY_SECRET", ""),
            ),
            metrics=MetricsConfig(
                qa_harness_url=os.getenv("QA_HARNESS_URL", ""),
            ),
            tier_models=tier_models,
            cost=cost,
            memory=MemoryConfig(
                user_id=(os.getenv("DA_MEMORY_USER", "local") or "local").strip() or "local"),
        )
    return _CONFIG
