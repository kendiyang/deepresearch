from pydantic import BaseModel, Field
import json
import copy
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from utils.struct_parser import safe_parse
from langchain_core.output_parsers import PydanticOutputParser

from config.config import config

class SearchQuery(BaseModel):
    thought_process: str = Field(
        None, 
        description="Reasoning process analyzing the user's intent and determining optimal search strategy."
    )
    queries: list[str] = Field(
        None, 
        description="List of optimized search queries derived from user intent analysis.",
        min_length=1
    )

class IntentQuery:
    def __init__(self):
        self.base_url = config.OPENAI_BASE_URL
        self.api_key = config.OPENAI_API_KEY
        self.llm = ChatOpenAI(
                model="gpt-4o",
                max_retries=3,
                base_url=self.base_url,
                api_key=self.api_key,
            )
        
        if self.llm is None:
            raise ValueError("intent_translate实例未正确初始化")
        
    async def translate(self, topic: str) -> list[str]:

        parser = PydanticOutputParser(pydantic_object=SearchQuery)

        few_shot_examples = {
        "thought_process": "User wants a comparison. Key entities: Tesla Model Y, Nio ES6. Context: Winter, Range performance. Need to fetch individual winter test data for both and a direct comparison review. 'Winter' implies cold weather battery efficiency.",
        "queries": [
            "Tesla Model Y winter range test real world results",
            "Nio ES6 winter range performance test",
            "Tesla Model Y vs Nio ES6 winter battery efficiency comparison",
            "EV winter range degradation rates 2026"
        ]
        }

        SYSTEM_PROMPT_INTENTIQ= f"""
        # Role
        You are a Search Query Optimization Expert specializing in converting vague user questions into precise, machine-friendly search queries for the Serper API (Google Search).

        # Task
        Your goal is to analyze the user's raw input and generate a list of optimized search queries using a Chain of Thought (CoT) process.

        # Chain of Thought Process (Step-by-Step)
        Before generating the final list, you must perform the following mental analysis:

        1.  **Intent Analysis:**
            * Identify the core intent: Fact-seeking, Comparative, Instructional (How-to), navigational, or Creative.
            * Determine if the query requires multi-hop reasoning (e.g., "Who is the wife of the actor who played Iron Man?").

        2.  **De-noising & Keyword Extraction:**
            * Remove conversational fillers (e.g., "I want to know", "Please tell me", "What do you think").
            * Identify key entities (People, Products, Events) and technical terms.

        3.  **Temporal & Contextual Check:**
            * Does the query imply a specific time frame (e.g., "latest", "newest")? If so, append the year explicitly.
            * Does it require a specific location?

        4.  **Query Decomposition (The "Rewriting" Strategy):**
            * Break complex questions into sub-queries.
            * **Strategy - Broad to Narrow:** Start with a broad search, then specific aspects.
            * **Strategy - Comparison:** For "A vs B", generate queries for "A specs", "B specs", and "A vs B review".
            * **Strategy - Terminology:** Translate colloquial terms to professional keywords (e.g., "stomach hurts" -> "abdominal pain causes").

        # Rules for Serper API Queries
        * Keep queries concise.
        * Use English for global technical topics, or the user's language if the query is local.
        * Avoid questions; use keyword combinations (e.g., instead of "What is the price of Bitcoin?", use "Bitcoin price USD live").
        * Limit to max 3-5 high-quality queries.

        User Input: "{topic}"

        # Output Format
        Return the result in a JSON format containing the `thought_process` and the `queries` list.
        {parser.get_format_instructions()}
        ---

        # Few-Shot Examples

        **User Input:** "比较特斯拉 Model Y 和蔚来 ES6 在冬天的续航表现"
        **Output:**
        {json.dumps(few_shot_examples, indent=2)}

        """

        
        
        response = await self.llm.ainvoke(SYSTEM_PROMPT_INTENTIQ)
        # 使用安全解析方法
        strategy = await safe_parse(self.llm,parser, response.content)
        return strategy
    
if __name__ == "__main__":
    import asyncio
    user_input = "阿曼绿乳香精油 —— 黄金级淡纹紧致修复力、抗皱力。比普通的眼部精华油增加了皇家顶级的珍稀抗皱精油配方"
    result = asyncio.run(IntentQuery().translate(user_input))
    print("Thought Process:", result.thought_process)
    print("Generated Queries:", result.queries)