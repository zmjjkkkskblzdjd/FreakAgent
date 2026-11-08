import os
from dataclasses import dataclass, field
@dataclass
class LLMConfig:
    model: str                 
    base_url: str              
    api_key_env: str           
    temperature: float         
    timeout: int               
    max_retries: int           

@dataclass
class ChunkConfig:
    chunk_size: int = 400      
    chunk_overlap: int = 50    

@dataclass
class RetrievalConfig:
    top_k: int = 5             
    score_threshold: float = 0.5  
    use_reranker: bool = False 
    vector_weight: float = 0.6 
    reranker_local: str = os.getenv("QA_RERANKER_LOCAL", "")  
    reranker_hf: str = "jinaai/jina-reranker-v3"  

@dataclass
class GuardrailConfig:
    max_loops: int = 10              
    max_fix_rounds: int = 2          
    max_escalations: int = 1         
    duplicate_call_threshold: int = 2  

@dataclass
class TokenBudgetConfig:
    window_tokens: int = 120000  
    cleanup_ratio: float = 0.4   
    compress_ratio: float = 0.7  

@dataclass
class PathConfig:
    kb_doc_dir: str = os.getenv("QA_DOC_DIR", "data/docs")  
    chroma_dir: str = os.getenv("QA_CHROMA_DIR", "data/chroma_db")  
    collection_name: str = "qa_kb"   
    embed_local: str = os.getenv("QA_EMBED_LOCAL", "")  
    embed_hf: str = "BAAI/bge-small-zh-v1.5"  

@dataclass
class TierModelConfig:        
    direct: str = "glm-5.3-flashx"    
    lite: str = "glm-5.3-flashx"
    deep: str = "glm-5.3-flashx"      

@dataclass
class MemoryConfig:
    chroma_dir: str = r"data/memory_db"  
    collection_name: str = "qa_memory"
    recall_top_k: int = 3
    min_importance: float = 0.3  

@dataclass
class AppConfig:
    llm: LLMConfig
    chunk: ChunkConfig
    retrieval: RetrievalConfig
    guardrail: GuardrailConfig
    token_budget: TokenBudgetConfig
    paths: PathConfig
    tier_models: TierModelConfig
    memory: MemoryConfig = field(default_factory=MemoryConfig)