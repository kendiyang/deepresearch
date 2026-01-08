import os
import json
import logging
from typing import List, Optional
from dotenv import load_dotenv

# 企业级重试库
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# LangChain 组件
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, Field, model_validator

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= 1. 配置 OpenRouter / Alibaba Model =================

# 你指定的模型 ID。如果 OpenRouter 尚未完全上线此具体 ID，
# 建议回退到 "alibaba/qwen-qwq-32b" (推理能力强) 或 "alibaba/qwen-2.5-72b-instruct"
MODEL_NAME = "alibaba/tongyi-deepresearch-30b-a3b" 
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_API_KEY:
    raise ValueError("请在环境变量中设置 OPENROUTER_API_KEY")

# 初始化 LLM 客户端 (连接 OpenRouter)
llm = ChatOpenAI(
    model=MODEL_NAME,
    openai_api_key=OPENROUTER_API_KEY,
    openai_api_base="https://openrouter.ai/api/v1",
    temperature=0.1, # 降低温度，由 Critic 做严格逻辑判断
    max_tokens=2048
)

# ================= 2. 定义严格的数据契约 (Schema) =================

class CriticDecision(BaseModel):
    """
    Critic 节点的决策结构体。
    """
    thought_process: str = Field(..., description="Internal monologue: Analyze the evidence against the Depth Scale. Why is this deep or shallow?")
    depth_score: int = Field(..., description="Integer score 1-10. 1-4=Surface/Functional, 5-7=Situational, 8-10=Identity/Psychological.")
    critique: str = Field(..., description="Specific feedback. If rejecting, explain strictly what is missing.")
    is_satisfied: bool = Field(..., description="Set to True ONLY if depth_score >= 8.")
    
    # 强制要求生成极其具体的下一步搜索词
    next_step_queries: List[str] = Field(
        default=[], 
        description="3 very niche, specific search queries to find hidden needs if not satisfied."
    )
    
    commercial_value_tag: str = Field(
        ..., 
        description="Category tag: 'Product_Defect', 'Feature_Request', 'Social_Friction', 'Emotional_Payoff', etc."
    )

    # 验证器：确保逻辑自洽
    @model_validator(mode="after")
    def validate_score_match(self):
        if self.is_satisfied and self.depth_score < 8:
            raise ValueError("Logical Error: Cannot be satisfied if score < 8.")
        if not self.is_satisfied and self.depth_score >= 8:
            raise ValueError("Logical Error: Must be satisfied if score >= 8.")
        return self

# 初始化解析器
parser = PydanticOutputParser(pydantic_object=CriticDecision)

# ================= 3. 构建 Prompt (CoT + Role Playing) =================

SYSTEM_PROMPT = """
# Role: Chief Insight Officer (CIO)
You are the gatekeeper of a high-end Deep Research Agent. 
Your ONLY job is to filter out "noise" (surface complaints) and identify "signal" (psychological/identity insights).

# The "Iceberg" Depth Scale (Strict Grading)
* **1-4 (Surface - REJECT):** "Battery is bad", "Too expensive", "Ugly color". (Functional)
* **5-7 (Situational - DIG DEEPER):** "Fails during meetings", "Hard to set up while driving". (Contextual)
* **8-10 (Identity - APPROVE):** "I feel ashamed using this in public", "This restores my sense of control", "It signals I am an insider". (Psychological/Cultural)

# Instructions
1.  **Be Harsh:** Do not approve generic feedback. If it sounds like an Amazon review, reject it.
2.  **Look for Emotion:** Fear, Shame, Pride, Belonging. These are the money makers.
3.  **Output JSON:** You must strictly follow the JSON schema provided.

{format_instructions}
"""

prompt_template = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("user", "Current Hypothesis: {hypothesis}\n\nEvidence Evidence:\n{evidence}")
])

# ================= 4. 执行逻辑 (带重试机制) =================

# 组合 Chain
critic_chain = prompt_template | llm | parser

@retry(
    stop=stop_after_attempt(3), 
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((OutputParserException, Exception))
)
def run_critic_evaluation(case_name: str, hypothesis: str, evidence: str) -> Optional[CriticDecision]:
    """
    运行单次评估，包含自动重试逻辑。
    """
    logger.info(f"--- Running Test: {case_name} ---")
    try:
        # Invoke Chain
        result = critic_chain.invoke({
            "hypothesis": hypothesis,
            "evidence": evidence,
            "format_instructions": parser.get_format_instructions()
        })
        return result
    except Exception as e:
        logger.error(f"Failed to process {case_name}: {e}")
        raise e # 抛出异常触发重试

# ================= 5. 测试数据集 (Golden Set) =================

# Case A: 典型的“垃圾”浅层数据
CASE_SURFACE = {
    "name": "Case A (Surface Noise)",
    "hypothesis": "Users dislike the Humane AI Pin because of hardware specs.",
    "evidence": """
    - "The battery dies in 4 hours, which is annoying."
    - "The projector gets too hot against my skin."
    - "It costs $699 which is too expensive for what it does."
    - "The laser is hard to see in bright sunlight."
    """
}

# Case B: 高价值的“深度”洞察
CASE_DEEP = {
    "name": "Case B (Identity Crisis)",
    "hypothesis": "The AI Pin fails because it breaks social contracts.",
    "evidence": """
    - "I wore it to a dinner and everyone went silent. I felt like a spy." (Social Friction)
    - "It's not a tool, it's a 'Kick Me' sign for tech bros. It looks dorky." (Identity/Shame)
    - "The hand gestures make me look like I'm swatting invisible flies in public." (Social Embarrassment)
    - "I feel vulnerable having a camera on my chest constantly." (Psychological Safety)
    """
}

# ================= 6. 主程序入口 =================

if __name__ == "__main__":
    print("🚀 Starting Enterprise Critic Node Test...\n")
    
    results = {}
    
    try:
        # Run Case A
        res_a = run_critic_evaluation(
            CASE_SURFACE["name"], 
            CASE_SURFACE["hypothesis"], 
            CASE_SURFACE["evidence"]
        )
        results["A"] = res_a
        print(f"\n📊 Result A (Should be LOW score):\n  Score: {res_a.depth_score}\n  Satisfied: {res_a.is_satisfied}\n  Thought: {res_a.thought_process[:100]}...")

        # Run Case B
        res_b = run_critic_evaluation(
            CASE_DEEP["name"], 
            CASE_DEEP["hypothesis"], 
            CASE_DEEP["evidence"]
        )
        results["B"] = res_b
        print(f"\n📊 Result B (Should be HIGH score):\n  Score: {res_b.depth_score}\n  Satisfied: {res_b.is_satisfied}\n  Thought: {res_b.thought_process[:100]}...")

        # ================= 7. 自动化验收 (Final Assertion) =================
        print("\n--- 🏁 Final Calibration Check ---")
        
        passed = True
        
        # Check A (Surface)
        if res_a.depth_score > 5 or res_a.is_satisfied:
            print(f"❌ FAIL Case A: Model was too lenient. Gave score {res_a.depth_score} to functional noise.")
            passed = False
        else:
            print(f"✅ PASS Case A: Correctly rejected surface noise (Score {res_a.depth_score}).")
            
        # Check B (Deep)
        if res_b.depth_score < 8 or not res_b.is_satisfied:
            print(f"❌ FAIL Case B: Model failed to recognize deep insight. Gave score {res_b.depth_score}.")
            passed = False
        else:
            print(f"✅ PASS Case B: Correctly identified psychological insight (Score {res_b.depth_score}).")

        if passed:
            print("\n🎉 SUCCESS: The Critic Node is calibrated and ready for production.")
        else:
            print("\n⚠️ WARNING: Prompt calibration needed. Adjust the system prompt 'Depth Scale' section.")

    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
