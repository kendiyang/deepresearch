from langchain_core.output_parsers import PydanticOutputParser
import re
from langchain_openai import ChatOpenAI

def _clean_json_from_markdown(text: str) -> str:
        """清理LLM输出中的Markdown包裹和多余的格式
        
        处理以下情况：
        - ```json ... ```
        - ```
          ...
          ```
        - 多余的换行和空格
        """
        # 移除 Markdown 代码块标记
        text = re.sub(r'^```(?:json)?\s*\n', '', text.strip(), flags=re.MULTILINE)
        text = re.sub(r'\n```\s*$', '', text.strip(), flags=re.MULTILINE)
        
        # 移除可能的注释（// 或 #）
        text = re.sub(r'//.*?$', '', text, flags=re.MULTILINE)
        text = re.sub(r'#.*?$', '', text, flags=re.MULTILINE)
        
        return text.strip()
    
async def safe_parse(llm: ChatOpenAI, parser: PydanticOutputParser, text: str) -> any:
    """安全地解析LLM输出，带有自动修复功能
    
    Args:
        parser: PydanticOutputParser实例
        text: LLM的原始输出文本
        model_name: 模型名称（用于日志）
        
    Returns:
        解析后的Pydantic对象
        
    Raises:
        Exception: 如果所有解析尝试都失败
    """
    # 清理Markdown包裹
    cleaned_text = _clean_json_from_markdown(text)
    
    # 尝试1: 直接解析清理后的文本
    try:
        return parser.parse(cleaned_text)
    except Exception as e1:        
        # 尝试2: 如果清理后的文本解析失败，尝试重新用LLM修复
        try:
            # 构造修复提示词
            fix_prompt = f"""
            The following JSON output has parsing errors. Please fix it and return ONLY valid JSON:

            {cleaned_text}

            Requirements:
            1. Fix any JSON syntax errors (missing quotes, commas, brackets)
            2. Ensure all strings are properly quoted
            3. Return ONLY the corrected JSON, no explanations
            4. Do NOT wrap in markdown code blocks
            """
            fix_response = await llm.ainvoke(fix_prompt)
            fixed_text = _clean_json_from_markdown(fix_response.content)
            return parser.parse(fixed_text)
        except Exception as e2:
            
            # 尝试3: 尝试手动修复常见的JSON错误
            try:
                # 修复常见的单引号问题
                fixed = cleaned_text.replace("'", '"')
                # 修复尾随逗号
                fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)
                return parser.parse(fixed)
            except Exception as e3:
                raise Exception(f"解析失败，已尝试3种方法: {str(e3)}")