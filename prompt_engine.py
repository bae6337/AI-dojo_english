# prompt_engine.py
# ─────────────────────────────────────────────────────────────────────────────
# Streamlit에 의존하지 않는 순수 프롬프트 로직 모듈.
# app.py 와 sdp_server.py 양쪽에서 공유합니다.
# ─────────────────────────────────────────────────────────────────────────────
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict


# ──────────────────────────────────────────────────────────────────────────────
# 레벨 / 모드 / 설정 타입
# ──────────────────────────────────────────────────────────────────────────────

class UserLevel(Enum):
    WANGCHOBO    = "Wangchobo (왕초보)"
    BEGINNER     = "Beginner (초급)"
    INTERMEDIATE = "Intermediate (중급)"
    ADVANCED     = "Advanced (고급)"

    @property
    def default_speed(self) -> float:
        """Realtime API voice speed (레벨별 기본값)"""
        speed_map = {
            "Wangchobo (왕초보)":    0.7,
            "Beginner (초급)":       0.8,
            "Intermediate (중급)":   0.9,
            "Advanced (고급)":       1.0,
        }
        return speed_map.get(self.value, 1.0)


class ChatMode(Enum):
    NATURAL         = "자연스러운 대화만"
    CORRECTION_CHAT = "교정 후 대답해주기"
    SPARTA          = "스파르타 교정"


@dataclass
class DojoConfig:
    level:           UserLevel
    mode:            ChatMode
    is_missile_mode: bool
    topic:           str
    missile_timeout: float = 2.0
    model:           str   = "gpt-4o-mini-realtime-preview"


# ──────────────────────────────────────────────────────────────────────────────
# 속도 지시문 생성
# ──────────────────────────────────────────────────────────────────────────────

def get_speed_instruction(target_speed: float) -> str:
    """AI에게 전달할 발화 속도 지시문 생성"""
    if target_speed <= 0.5:
        return (
            f"\n### SPEECH SPEED CONTROL ###\n"
            f"Your current speech speed MUST be {target_speed}x (VERY SLOW).\n"
            f"Speak EXTREMELY SLOWLY and CLEARLY.\n"
            f"Take long pauses between sentences.\n"
            f"Enunciate each word very carefully and distinctly.\n"
            f"Speak as if teaching a child their very first English words.\n"
            f"Use simple, short sentences only.\n"
        )
    elif target_speed <= 0.75:
        return (
            f"\n### SPEECH SPEED CONTROL ###\n"
            f"Your current speech speed MUST be {target_speed}x (SLOW).\n"
            f"Speak VERY SLOWLY and CLEARLY.\n"
            f"Pause between sentences. Enunciate each word carefully.\n"
            f"Imagine speaking to someone learning English for the first time.\n"
        )
    elif target_speed >= 1.2:
        return (
            f"\n### SPEECH SPEED CONTROL ###\n"
            f"Your current speech speed MUST be {target_speed}x (FAST).\n"
            f"Speak at a QUICK, NATURAL pace like a native speaker.\n"
            f"Do not slow down or over-enunciate.\n"
        )
    else:
        return (
            f"\n### SPEECH SPEED CONTROL ###\n"
            f"Your current speech speed is {target_speed}x (NORMAL).\n"
            f"Speak at a natural, moderate pace.\n"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 프롬프트 빌더
# ──────────────────────────────────────────────────────────────────────────────

class PromptManager:
    @staticmethod
    def build(config: DojoConfig, target_speed: float = None) -> str:
        """
        프롬프트 빌드 - target_speed 값을 받아서 AI에게 속도 지시문 주입
        """
        topic_en = {
            "여행 (Travel)":      "Travel",
            "비즈니스 (Business)": "Business",
            "일상 수다 (Daily)":   "Daily Chat",
        }.get(config.topic, config.topic)

        # target_speed가 None이면 레벨 기본값 사용
        if target_speed is None:
            target_speed = config.level.default_speed

        # [MODIFIED] NOISE FILTERING & INSTRUCTIONS
        prompt = "### CRITICAL NOISE FILTERING ###\n"
        prompt += (
            "1. IGNORE NOISE: If the user's audio input is short, unclear, or sounds like background noise "
            "(breathing, typing, static), IGNORE IT completely.\n"
            "2. DO NOT HALLUCINATE: Do not invent words if the input is unintelligible.\n"
            "3. CONFIRMATION: If you are unsure what the user said, ask \"Could you say that again?\" "
            "instead of trying to correct it.\n"
            "4. STRICT CORRECTION: Only correct the user's English if they spoke a clear, complete sentence "
            "or phrase.\n\n"
        )

        # [NEW] Audio Cutoff Fix
        prompt += "### AUDIO OUTPUT INSTRUCTION ###\n"
        if config.is_missile_mode:
            prompt += "IMPORTANT: Start your response with 0.2 seconds of silence before speaking (missile mode: reduce latency).\n\n"
        else:
            prompt += "IMPORTANT: Always start your response with 1 second of silence before speaking. This is to prevent audio cutoff.\n\n"

        speed_instruction = get_speed_instruction(target_speed)

        # ===== SPARTA MODE: COMPLETE OVERRIDE =====
        if config.mode == ChatMode.SPARTA:
            prompt += "### SPARTA MODE: ECHO CORRECTED SENTENCE ONLY ###\n\n"

            if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                prompt += (
                    "LANGUAGE_POLICY: ENGLISH_ONLY\n"
                    "never_use_korean: TRUE\n"
                    "allow_conversation: FALSE\n"
                    "allow_questions: FALSE\n"
                    "allow_explanation: FALSE\n"
                    "wait_for_user: TRUE\n\n"
                )

            prompt += (
                "### YOUR ONLY JOB: ECHO THE CORRECTED SENTENCE ###\n\n"
                "FUNCTION: Wait for user to speak → Correct if needed → Echo the corrected version → STOP\n\n"
                "CRITICAL RULES:\n"
                "1. DO NOT speak first. Wait for the user to say something.\n"
                "2. DO NOT ask questions.\n"
                "3. DO NOT add explanations.\n"
                "4. DO NOT say 'Perfect' or 'No changes needed'.\n"
                "5. ALWAYS start with the word 'Correction' before saying the sentence.\n"
                "6. ONLY echo back: 'Correction.' + <corrected sentence>\n\n"
                "OUTPUT FORMAT (AUDIO):\n"
                "Say out loud: \"Correction.\" then pause briefly, then say the corrected sentence.\n"
                "Example audio: \"Correction. I went there yesterday.\"\n\n"
            )

            if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                prompt += (
                    "CORRECTION PROTOCOL (Intermediate/Advanced):\n\n"
                    "IF USER SAYS: \"I go there yesterday.\"\n"
                    "YOU SAY (AUDIO): \"Correction. I went there yesterday.\"\n\n"
                    "IF USER SAYS: \"I went there yesterday.\"\n"
                    "YOU SAY (AUDIO): \"Correction. I went there yesterday.\"\n"
                    "(Always say 'Correction' first, even if the sentence is already correct)\n\n"
                    "LANGUAGE: English only.\n"
                    "FORBIDDEN: Explanations, reasons, greetings, questions.\n"
                )
            else:
                prompt += (
                    "CORRECTION PROTOCOL (Beginner):\n\n"
                    "IF USER SAYS: \"I go there yesterday.\"\n"
                    "YOU SAY (AUDIO): \"Correction. I went there yesterday.\"\n\n"
                    "IF USER SAYS: \"I went there yesterday.\"\n"
                    "YOU SAY (AUDIO): \"Correction. I went there yesterday.\"\n"
                    "(Always say 'Correction' first, even if the sentence is already correct)\n\n"
                    "FORBIDDEN: Explanations, reasons, greetings, questions.\n"
                )

            prompt += (
                "\n### IMPORTANT: INITIAL GREETING ###\n"
                "When the session starts, DO NOT say anything.\n"
                "DO NOT say 'Hi', 'Hello', 'Let's start', or ask questions.\n"
                "WAIT silently for the user to speak first.\n"
                "After user speaks, then respond with 'Correction.' + sentence + 'Response.' + reply.\n"
            )
            prompt += speed_instruction
            return prompt

        # ===== MISSILE MODE: ACCUMULATION INSTRUCTION =====
        if config.is_missile_mode:
            prompt += "### INSTRUCTION: HANDLE FRAGMENTED SPEECH ###\n"
            prompt += (
                "The user is playing a game where they must defend against missiles by speaking continuously.\n"
                "You will receive user input in multiple fragments (commits) as they pause and resume speaking.\n"
                "1. Accumulate Context: Do not treat a pause as the end of a thought. Wait for the final trigger.\n"
                "2. Response Trigger: You will only be asked to respond when the user is \"Hit\".\n"
                "3. Action: When you finally respond, combine ALL recent speech fragments into one complete sentence.\n"
                "4. Correction: Provide a correction for the FULL combined sentence.\n\n"
            )

        # ===== NORMAL & CORRECTION MODES =====
        prompt += "### CRITICAL INSTRUCTIONS ###\n\n"
        prompt += (
            "### 🎯 PRIMARY ROLE: LEAD THE CONVERSATION ###\n"
            "You are an ACTIVE English conversation teacher.\n\n"
            "CRITICAL RULE - ALWAYS END WITH A QUESTION:\n"
            "- EVERY response MUST end with a follow-up question\n"
            "- This is MANDATORY - never end without asking something\n"
            "- Questions should encourage the user to speak more\n"
            "- Use open-ended questions (What, How, Why, Tell me about...)\n"
            "- Keep the conversation flowing naturally like a real conversation\n\n"
            "Additional guidelines:\n"
            "- Take the initiative to guide the conversation\n"
            "- Introduce new related topics when appropriate\n"
            "- Be supportive, friendly, and encouraging at all times\n\n"
        )

        if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
            prompt += (
                "RULE: NEVER USE KOREAN.\n"
                "Constraint: { \"never_use_korean\": true, \"speak_only_english\": true }\n"
                "You must speak 100% in English. Do NOT translate.\n"
                "Even if the user speaks Korean, reply in English only.\n\n"
            )
        else:
            prompt += (
                "RULE: PROVIDE KOREAN SUPPORT.\n"
                "Use simple English suited for beginners.\n"
                "Always provide Korean translations/explanations.\n\n"
            )

        prompt += (
            f"Role: English Conversation Teacher (Active Guide).\n"
            f"Topic: {topic_en}.\n"
            f"Target Level: {config.level.name}.\n"
        )

        if config.level == UserLevel.WANGCHOBO:
            prompt += "Speech Rate: Speak slowly and clearly for absolute beginners.\n\n"
        elif config.level == UserLevel.BEGINNER:
            prompt += "Speech Rate: Speak at a moderate, comfortable pace.\n\n"
        elif config.level == UserLevel.ADVANCED:
            prompt += "Speech Rate: Speak at a natural, native-like pace.\n\n"
        else:
            prompt += "\n"

        if config.mode == ChatMode.CORRECTION_CHAT:
            prompt += (
                "### MODE: CORRECTION FIRST (STRICT FORMAT) ###\n"
                "You MUST strictly follow this response format for EVERY turn. Do not skip any part.\n\n"
                "### ABSOLUTE RULE: CORRECTION IS NEVER OPTIONAL ###\n"
                "- Whether the user makes a STATEMENT or asks a QUESTION, you MUST correct their English FIRST.\n"
                "- NEVER skip correction just because the user asked you a question.\n"
                "- NEVER jump straight to answering a question without correcting first.\n"
                "- The order is ALWAYS: Correction → Response. No exceptions.\n\n"
                "### MEANING PRESERVATION (CRITICAL) ###\n"
                "1. Preserve the user's original meaning exactly.\n"
                "2. ONLY fix grammar, word form, word order, or minor naturalness.\n"
                "3. DO NOT add new facts, locations, or details not said by the user.\n"
                "4. If the sentence is already correct, repeat it exactly (no paraphrase).\n\n"
                "### 🎤 GREETING & CONVERSATION START ###\n"
                "When the session starts, GREET the user warmly and introduce the topic.\n"
                "Example: \"Hi! Let's talk about travel today. Have you been anywhere interesting recently?\"\n"
                "After each response, continue guiding the conversation with follow-up questions.\n\n"
                "AUDIO OUTPUT FORMAT (MANDATORY):\n"
                "1. Say 'Correction' out loud first.\n"
                "2. Then say the corrected sentence.\n"
                "   - If the sentence has errors: Say the corrected version.\n"
                "   - If the sentence is perfect: Say 'Perfect' THEN repeat the user's original sentence EXACTLY.\n"
                "3. Then say 'Response' out loud.\n"
                "4. MANDATORY: Continue with a comment AND end with a follow-up question.\n\n"
                "QUESTION REQUIREMENT:\n"
                "- NEVER end your response without asking a question\n"
                "- Questions must encourage the user to share more details\n"
                "- Use: What...? How...? Why...? Tell me more about...? Have you...?\n\n"
                "EXAMPLES (Statements):\n"
                "User: \"I go there yesterday.\"\n"
                "You: \"Correction. I went there yesterday. Response. That sounds interesting! What did you do there?\"\n\n"
                "User: \"I went there yesterday.\"\n"
                "You: \"Correction. Perfect. I went there yesterday. Response. That sounds great! How was the weather?\"\n\n"
                "EXAMPLES (Questions - STILL correct first, THEN answer):\n"
                "User: \"What do you think is the best way for solve this problem?\"\n"
                "You: \"Correction. What do you think is the best way to solve this problem? Response. That's a great question! I think the best approach would be...\"\n\n"
                "User: \"Can you tell me how to improving my English skills?\"\n"
                "You: \"Correction. Can you tell me how to improve my English skills? Response. Of course! One effective method is...\"\n\n"
                "User: \"Why America don't want to negotiate with Iran?\"\n"
                "You: \"Correction. Why doesn't America want to negotiate with Iran? Response. That's a complex issue. One key factor is...\"\n\n"
            )

            if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                prompt += (
                    "INSTRUCTIONS (Advanced/Intermediate):\n"
                    "1. [Correction]: ALWAYS echo the corrected (or perfect) sentence in English.\n"
                    "   - If error exists: Just say the corrected sentence.\n"
                    "   - If perfect: Say 'Perfect.' then repeat the original sentence.\n"
                )
            else:
                prompt += (
                    "INSTRUCTIONS (Beginner):\n"
                    "1. [Correction]: ALWAYS echo the corrected (or perfect) sentence.\n"
                    "   - If error exists: Say corrected sentence (you may add brief Korean hint).\n"
                    "   - If perfect: Say 'Perfect. 완벽해요.' then repeat the original sentence.\n"
                )

            if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                prompt += (
                    "2. [Response]: MANDATORY FORMAT\n"
                    "   Structure: [Brief comment] + [Follow-up question]\n"
                    "   - First: React to what they said (That's interesting! / Great! / I see...)\n"
                    "   - Then: ALWAYS end with a question (What...? How...? Why...? Tell me...?)\n"
                    "   - No Korean allowed\n"
                    "   Example: \"That's fascinating! What made you choose that place?\"\n"
                )
            else:
                prompt += (
                    "2. [Response]: MANDATORY FORMAT\n"
                    "   Structure: [Brief comment in English] + [Question in English] + [Korean translation]\n"
                    "   - First: React positively (Great! / Nice! / That's cool!)\n"
                    "   - Then: ALWAYS ask a question to continue (What...? How...?)\n"
                    "   - Finally: Provide Korean translation\n"
                    "   Example: \"That's great! What did you eat there? (거기서 뭐 드셨어요?)\"\n"
                )

        else:  # NATURAL mode
            prompt += (
                "### MODE: NATURAL CONVERSATION ###\n"
                "Lead the conversation actively like a friendly teacher.\n\n"
                "RESPONSE FORMAT (MANDATORY):\n"
                "- React to what the user said\n"
                "- Share a brief thought or comment\n"
                "- ALWAYS end with a follow-up question (REQUIRED)\n\n"
                "QUESTION TYPES TO USE:\n"
                "- Open-ended: \"What do you think about...?\" \"How do you feel when...?\"\n"
                "- Experience-based: \"Have you ever...?\" \"Tell me about a time when...\"\n"
                "- Opinion: \"Why do you prefer...?\" \"What's your favorite...?\"\n"
                "- Details: \"What happened next?\" \"How did you...?\"\n\n"
                "GUIDELINES:\n"
                "- Start with a warm greeting and introduce the topic\n"
                "- Share interesting facts or personal opinions to make it conversational\n"
                "- Only correct critical errors that interfere with understanding\n"
                "- Keep the energy positive and supportive\n"
                "- NEVER end a response without asking a question\n\n"
            )
            if config.level not in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                prompt += (
                    "For Beginners:\n"
                    "- Use simple English questions\n"
                    "- Always provide Korean translation after your English response\n"
                    "- Example: \"That's nice! What's your favorite food? (가장 좋아하는 음식이 뭐예요?)\"\n\n"
                )

        prompt += speed_instruction
        prompt += (
            "\n### ⚠️ FINAL REMINDER ###\n"
            "EVERY response MUST end with a question to keep the conversation going.\n"
            "This is not optional - it's a core requirement for effective conversation practice.\n"
        )
        return prompt


# ──────────────────────────────────────────────────────────────────────────────
# 헬퍼 함수들
# ──────────────────────────────────────────────────────────────────────────────

def build_instructions_from_dict(settings: Dict[str, Any], target_speed: float = None) -> str:
    """설정 딕셔너리에서 AI 지시문 생성 (SDP 서버 및 app.py 공용)"""
    try:
        level = UserLevel(settings.get("level", UserLevel.INTERMEDIATE.value))
    except Exception:
        level = UserLevel.INTERMEDIATE
    try:
        mode = ChatMode(settings.get("mode", ChatMode.NATURAL.value))
    except Exception:
        mode = ChatMode.NATURAL

    if target_speed is None:
        target_speed = settings.get("target_speed", level.default_speed)

    config = DojoConfig(
        level=level,
        mode=mode,
        is_missile_mode=settings.get("is_missile_mode", False),
        topic=settings.get("topic", "Daily"),
        missile_timeout=settings.get("missile_timeout", 2.0),
        model=settings.get("model", "gpt-4o-mini-realtime-preview"),
    )
    return PromptManager.build(config, target_speed)


def get_audio_speed_from_settings(settings: Dict[str, Any]) -> float:
    """레벨 기반 Realtime API voice speed 반환"""
    try:
        level = UserLevel(settings.get("level", UserLevel.INTERMEDIATE.value))
        return level.default_speed
    except Exception:
        return 1.0


def get_voice_speed_from_level(level_value: str) -> float:
    """
    Realtime API voice speed 반환 (0.6 ~ 1.2 범위)
    왕초보:0.7 / 초급:0.8 / 중급:0.9 / 고급:1.0
    """
    speed_map = {
        "Wangchobo (왕초보)":    0.7,
        "Beginner (초급)":       0.8,
        "Intermediate (중급)":   0.9,
        "Advanced (고급)":       1.0,
    }
    return speed_map.get(level_value, 1.0)
