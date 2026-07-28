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

