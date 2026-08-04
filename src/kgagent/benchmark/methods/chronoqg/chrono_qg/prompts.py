"""
All prompt templates for the TKGQG pipeline.
Edit this file to adjust prompts used by rewriting and verification.
"""

# ---------------------------------------------------------------------------
#  LLM rewrite
# ---------------------------------------------------------------------------

REWRITE_PROMPT = """\
You are rewriting a machine-generated temporal knowledge-graph question \
into a fluent, natural-sounding English question that reads like something \
a human would actually ask.

CORE GOAL: produce a question indistinguishable from human-written text.

Rules:
1. Preserve the exact logical meaning — the rewritten question must have \
the same unique answer as the original.
2. Do NOT reveal the answer in the question.
3. Do NOT use any internal labels (tr-1, tp-26, "current step", etc.).
4. CRITICAL — preserve temporal direction exactly. If the original says \
A happened BEFORE B, your rewrite must keep A before B. Never reverse \
"before/after", "starts/ends", "during/contains", or any temporal ordering.
5. CRITICAL — use ONLY natural human language. Absolutely forbidden:
   - KG jargon: "entity", "relation", "linked from", "connected to", \
"current step period", "candidate's period", "the period for that relation"
   - Quoted relation names: do NOT write "member of sports team" or \
"position held" in quotes — instead say "played for", "served as", \
"worked at", etc., using the natural verb for that relationship
   - Template phrases: "Which entity has the relation X to Y where..."
6. For multi-hop questions ("Following the unique entity via..."), merge \
both hops into one seamless natural-language question. For example, \
"Who coached the team that [person] played for before [year]?" instead of \
"Following the unique entity via 'head coach', which next entity?".
7. The question must read like a natural trivia or history question — \
concise, specific, and immediately comprehensible to a non-expert.
8. Output exactly one rewritten English question and nothing else.

Original question:
{original_question}

Subgraph facts (use these to understand the domain and pick natural verbs):
{facts}

Target answer (do NOT include in your question):
{target_answer}"""


REWRITE_PROMPT_ZH = """\
你正在把机器生成的时序知识图谱问题改写成自然、流畅的中文问题，
问题应当像真人会提出的问题。

核心目标：生成一个与人工撰写难以区分的问题。

规则：
1. 保留完全相同的逻辑含义，改写后的问题必须与原问题拥有同一个唯一答案。
2. 不要在问题中泄露答案。
3. 不要使用任何内部标签，例如 tr-1、tp-26、“current step”等。
4. 必须严格保持时间方向。如果原问题表示 A 发生在 B 之前，改写后也必须保持 A 在 B 之前。不要颠倒 before/after、starts/ends、during/contains 或任何时间顺序。
5. 必须只使用自然的人类语言。禁止使用：
   - 知识图谱术语，例如“实体”“关系”“链接到”“连接到”“当前步骤时间段”“候选时间段”“该关系的时间段”
   - 带引号的关系名，不要写“member of sports team”或“position held”，而应根据语义自然表达
   - 模板化说法，例如“哪个实体与 Y 存在关系 X 且...”
6. 对于多跳问题，例如 “Following the unique entity via...”，要把两个跳步合并成一个自然连贯的问题。
7. 问题应像自然的常识、历史或事实类问题，简洁、具体，非专业读者也能理解。
8. 只输出一个改写后的中文问题，不要输出其他内容。

原始问题：
{original_question}

子图事实（用于理解领域并选择自然表达）：
{facts}

目标答案（不要包含在问题中）：
{target_answer}"""


REWRITE_PROMPTS = {
    "en": REWRITE_PROMPT,
    "zh": REWRITE_PROMPT_ZH,
}


# ---------------------------------------------------------------------------
#  Stage 1: answer from scratch
# ---------------------------------------------------------------------------

STAGE1_ANSWER_PROMPT = """\
You are answering a temporal question using only the provided knowledge \
graph subgraph. All timestamps are integer years.

Work through the reasoning scaffold step by step. Your final answer must \
be a single entity name exactly as it appears in the subgraph facts.

Return strict JSON (no extra text):
{{
  "steps": [
    {{"sub_question": "<sub-question>", "answer": "<your answer>"}}
  ],
  "final_answer": "<single entity name>"
}}

Question:
{question}

Subgraph facts:
{facts}

Reasoning scaffold (answer each sub-question in order):
{cot_scaffold}"""


# ---------------------------------------------------------------------------
#  Stage 2: Verify gold answer with strict Allen constraint definition
#  Runs only on Stage 1 failures.
# ---------------------------------------------------------------------------

VERIFY_ANSWER_PROMPT = """\
You are verifying whether a proposed answer to a temporal question is \
correct and UNIQUELY satisfied.

The key temporal constraint has a strict formal definition — use it exactly \
when comparing timestamps (all timestamps are integer years):

Constraint ({allen_code}):
  Plain English: {allen_natural}
  Strict definition: {allen_strict_def}

Step-by-step: check the proposed answer against every constraint, showing \
the explicit year values used. Then scan ALL other entities in the subgraph \
and check whether any of them also satisfy the full set of constraints.

Return strict JSON (no extra text):
{{
  "verification_steps": [
    {{
      "constraint": "<one constraint>",
      "years_used": "<e.g. ts(A)=1955 te(A)=1957 ts(B)=1952 te(B)=1972>",
      "check": "<inequality evaluated, e.g. 1952 < 1955 AND 1957 < 1972>",
      "satisfied": true or false
    }}
  ],
  "all_satisfied": true or false,
  "other_valid_entities": ["<entity>"],
  "verdict": "pass" or "fail",
  "reason": "<one sentence>"
}}

Question:
{question}

Proposed answer:
{proposed_answer}

Subgraph facts:
{facts}

Constraints to verify (in order):
{cot_scaffold}"""


# ---------------------------------------------------------------------------
#  Answer equivalence judge
# ---------------------------------------------------------------------------

VERIFY_JUDGE_PROMPT = """\
You are checking whether two answers should be treated as the same gold \
answer for a temporal KG question.

Return strict JSON:
{{
  "equivalent": 0 or 1,
  "reason": "short explanation"
}}

Gold answer: {gold_answer}
Predicted answer: {pred_answer}

Question:
{question}

Subgraph facts:
{facts}"""


# ---------------------------------------------------------------------------
#  Rewrite fix: teacher model correction on failed rewrite
# ---------------------------------------------------------------------------

REWRITE_FIX_PROMPT = """\
A previously rewritten temporal question failed verification — an LLM \
could not arrive at the correct answer from the subgraph using the \
rewritten question. This likely means the rewrite introduced semantic drift.

Your task: diagnose what went wrong and produce a corrected question.

Rules:
1. The corrected question MUST have the same answer as the original.
2. Do NOT reveal the answer in the question.
3. Do NOT use internal labels (tr-1, tp-26, etc.).
4. CRITICAL — preserve temporal direction: if the original says event A \
happened BEFORE event B, your rewrite must keep A before B. The most \
common failure is reversing "before/after" or swapping which event is the \
reference. Double-check that your corrected question preserves the exact \
temporal ordering from the original.
5. Make the question concise and natural.
6. Output exactly one corrected English question and nothing else.

Original (template) question:
{original_question}

Failed rewrite:
{failed_rewrite}

LLM's wrong answer to the failed rewrite:
{wrong_answer}

Correct answer (do NOT include in your question):
{target_answer}

Subgraph facts:
{facts}"""


REWRITE_FIX_PROMPT_ZH = """\
一个之前改写过的时序问题没有通过验证，LLM 无法根据子图从该改写问题推理出正确答案。
这通常说明改写引入了语义漂移。

你的任务：诊断问题并生成一个修正后的中文问题。

规则：
1. 修正后的问题必须与原始问题拥有相同答案。
2. 不要在问题中泄露答案。
3. 不要使用内部标签，例如 tr-1、tp-26 等。
4. 必须保持时间方向：如果原始问题表示事件 A 发生在事件 B 之前，修正后也必须保持 A 在 B 之前。最常见的错误是颠倒 before/after，或交换参考事件。请仔细检查修正问题是否保持了原始时间顺序。
5. 问题要简洁、自然。
6. 只输出一个修正后的中文问题，不要输出其他内容。

原始模板问题：
{original_question}

失败的改写：
{failed_rewrite}

LLM 对失败改写给出的错误答案：
{wrong_answer}

正确答案（不要包含在问题中）：
{target_answer}

子图事实：
{facts}"""


REWRITE_FIX_PROMPTS = {
    "en": REWRITE_FIX_PROMPT,
    "zh": REWRITE_FIX_PROMPT_ZH,
}


# ---------------------------------------------------------------------------
#  Stage-2 failure diagnosis
# ---------------------------------------------------------------------------

FAILURE_DIAGNOSIS_PROMPT = """\
A strong LLM failed to verify that the proposed answer to this temporal \
question is correct. Your task is to diagnose the root cause.

Diagnose carefully:
- "question_issue": the question itself has a problem (e.g., the gold answer \
does not actually satisfy the constraints, multiple entities satisfy the \
constraints, the constraints are self-contradictory, or the question wording \
is genuinely ambiguous).
- "model_capability": the question is logically sound and the gold answer is \
uniquely correct, but the model failed to reason through the temporal \
arithmetic correctly.

Return strict JSON:
{{
  "diagnosis": "question_issue" or "model_capability",
  "confidence": "high" or "low",
  "reason": "<one or two sentences explaining why>"
}}

Question:
{question}

Proposed gold answer:
{gold_answer}

Subgraph facts (with year-level timestamps):
{facts}

Verification steps the model produced (may be incomplete or wrong):
{verify_steps}

Model's stated reason for failing:
{fail_reason}"""
