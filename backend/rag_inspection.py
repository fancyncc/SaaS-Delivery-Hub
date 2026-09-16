"""Read-only routing inspection, separate from answer generation."""
import re

from pydantic import BaseModel, Field

class RetrievalDecision(BaseModel):
    needs_rag: bool
    reason: str = Field(min_length=1, max_length=500)


async def decide_retrieval(question: str) -> RetrievalDecision:
    # This inspection path never calls an LLM, regardless of MODEL_MODE.
    question = question.strip()
    internal = re.search(r"公司|企业|项目|知识库|资料|文档|制度|流程|权限|平台|产品|实施|导入|审批|上线|我们|你们|本系统", question)
    general = re.fullmatch(r"[\d\s.+*/()=×÷−-]+[?？]?", question) or re.fullmatch(
        r"(你好|您好|谢谢|再见|早上好|hello|hi)[!！。,.，?？\s]*", question, re.I) or re.search(
        r"^(什么是|解释一下|科普|写一首|讲个笑话|翻译|润色|帮我翻译|帮我润色|(?:帮我)?写一个.*(?:函数|算法)|如何用\s*(?:Python|JavaScript|Java)\b)", question, re.I)
    needed = bool(internal) or not bool(general)
    return RetrievalDecision(needs_rag=needed, reason=(
        "本地规则判断：涉及业务资料或无法确定可直接回答，尝试知识库检索。" if needed
        else "本地规则判断：属于问候、通用说明、文本处理、编程或计算，无需查询内部资料。"))
