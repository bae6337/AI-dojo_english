import asyncio
import base64
import http.server
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Dict
from enum import Enum
from dataclasses import dataclass

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

st.set_page_config(page_title="AI English Dojo", layout="wide")

load_dotenv()
API_KEY = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
BUILD_ID = "2026-02-04-v17.10-syntax-fix-final"
SERVER_PORT = 8016 

if 'user_settings' not in st.session_state:
    st.session_state.user_settings = {}

# ===============================================
# TARGET SPEED CONTROL TOWER
# st.session_state.target_speed를 전역 컨트롤 타워로 사용
# ===============================================

def initialize_target_speed(level_value: str) -> float:
    """
    레벨에 따라 st.session_state.target_speed를 초기화하고 반환
    브라우저 playbackRate 제어 (클라이언트 측)
    - 왕초보: 0.6 (매우 느림)
    - 초급: 0.8 (느림)
    - 중급: 1.0 (보통)
    - 고급: 1.1 (빠름)
    """
    speed_map = {
        "Wangchobo (왕초보)": 0.6,
        "Beginner (초급)": 0.8,
        "Intermediate (중급)": 1.0,
        "Advanced (고급)": 1.1
    }
    target_speed = speed_map.get(level_value, 1.0)
    st.session_state.target_speed = target_speed
    return target_speed

def get_target_speed() -> float:
    """현재 설정된 target_speed 반환 (없으면 1.0)"""
    return st.session_state.get('target_speed', 1.0)

def update_target_speed(level_value: str) -> float:
    """
    레벨 변경 시 target_speed를 즉시 업데이트하고 
    session_update_required 플래그를 설정
    """
    new_speed = initialize_target_speed(level_value)
    st.session_state.session_update_required = True
    st.session_state.speed_changed_at = time.time()
    return new_speed

def get_speed_instruction(target_speed: float) -> str:
    """
    AI에게 전달할 발화 속도 지시문 생성
    """
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

# 앱 시작 시 초기화 (기본값: 중급)
if 'target_speed' not in st.session_state:
    st.session_state.target_speed = 1.0
if 'session_update_required' not in st.session_state:
    st.session_state.session_update_required = False

class UserLevel(Enum):
    WANGCHOBO = "Wangchobo (왕초보)"
    BEGINNER = "Beginner (초급)"
    INTERMEDIATE = "Intermediate (중급)"
    ADVANCED = "Advanced (고급)"
    
    @property
    def default_speed(self) -> float:
        """Returns the audio playback speed for each level (Realtime API voice speed)"""
        speed_map = {
            "Wangchobo (왕초보)": 0.7,       # Slow for absolute beginners
            "Beginner (초급)": 0.8,          # Slightly slow for beginners
            "Intermediate (중급)": 0.9,      # Near normal speed
            "Advanced (고급)": 1.0           # Normal speed
        }
        return speed_map.get(self.value, 1.0)

class ChatMode(Enum):
    NATURAL = "자연스러운 대화만"
    CORRECTION_CHAT = "교정 후 대답해주기"
    SPARTA = "스파르타 교정"

@dataclass
class DojoConfig:
    level: UserLevel
    mode: ChatMode
    is_missile_mode: bool
    topic: str
    missile_timeout: float = 2.0
    model: str = "gpt-4o-mini-realtime-preview"

class PromptManager:
    @staticmethod
    def build(config: DojoConfig, target_speed: float = None) -> str:
        """
        프롬프트 빌드 - target_speed 값을 받아서 AI에게 속도 지시문 주입
        """
        topic_en = {"여행 (Travel)": "Travel", "비즈니스 (Business)": "Business", "일상 수다 (Daily)": "Daily Chat"}.get(config.topic, config.topic)
        
        # target_speed가 None이면 레벨 기본값 사용
        if target_speed is None:
            target_speed = config.level.default_speed
        
        # [MODIFIED] NOISE FILTERING & INSTRUCTIONS
        prompt = "### CRITICAL NOISE FILTERING ###\n"
        prompt += (
            "1. IGNORE NOISE: If the user's audio input is short, unclear, or sounds like background noise (breathing, typing, static), IGNORE IT completely.\n"
            "2. DO NOT HALLUCINATE: Do not invent words if the input is unintelligible.\n"
            "3. CONFIRMATION: If you are unsure what the user said, ask \"Could you say that again?\" instead of trying to correct it.\n"
            "4. STRICT CORRECTION: Only correct the user's English if they spoke a clear, complete sentence or phrase.\n\n"
        )
        
        # [NEW] Audio Cutoff Fix
        prompt += "### AUDIO OUTPUT INSTRUCTION ###\n"
        if config.is_missile_mode:
            prompt += "IMPORTANT: Start your response with 0.2 seconds of silence before speaking (missile mode: reduce latency).\n\n"
        else:
            prompt += "IMPORTANT: Always start your response with 1 second of silence before speaking. This is to prevent audio cutoff.\n\n"

        # 속도 지시문 생성 (모든 모드에 공통 적용)
        speed_instruction = get_speed_instruction(target_speed)
        
        # ===== SPARTA MODE: COMPLETE OVERRIDE =====
        if config.mode == ChatMode.SPARTA:
            prompt += "### SPARTA MODE: ECHO CORRECTED SENTENCE ONLY ###\n\n"
            
            # Language constraint
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
            
            # 속도 지시문 추가 (SPARTA 모드)
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
        # 1. CRITICAL CONSTRAINTS (최상단 - 절대 규칙)
        prompt += "### CRITICAL INSTRUCTIONS ###\n\n"
        
        # ★★★ 최우선 규칙: 대화 주도 + 질문 강제 ★★★
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
        
        # 레벨별 언어 정책 (최우선 강제)
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

        # 2. ROLE & CONTEXT
        prompt += (
            f"Role: English Conversation Teacher (Active Guide).\n"
            f"Topic: {topic_en}.\n"
            f"Target Level: {config.level.name}.\n"
        )
        
        # Add speech rate guidance based on level
        if config.level == UserLevel.WANGCHOBO:
            prompt += "Speech Rate: Speak slowly and clearly for absolute beginners.\n\n"
        elif config.level == UserLevel.BEGINNER:
            prompt += "Speech Rate: Speak at a moderate, comfortable pace.\n\n"
        elif config.level == UserLevel.ADVANCED:
            prompt += "Speech Rate: Speak at a natural, native-like pace.\n\n"
        else:
            prompt += "\n"

        # 3. MODE SPECIFIC RULES (구조 강제)
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
            
            # [Correction] 파트 디테일
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

            # [Response] 파트 디테일 - 질문 강제
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
        
        # 속도 지시문 추가 (NORMAL & CORRECTION 모드)
        prompt += speed_instruction
        
        # 최종 강조
        prompt += (
            "\n### ⚠️ FINAL REMINDER ###\n"
            "EVERY response MUST end with a question to keep the conversation going.\n"
            "This is not optional - it's a core requirement for effective conversation practice.\n"
        )
            
        return prompt

def build_instructions_from_dict(settings: Dict[str, Any], target_speed: float = None) -> str:
    """
    설정 딕셔너리에서 지시문 생성
    target_speed: st.session_state.target_speed에서 전달받은 속도값
    """
    try: level = UserLevel(settings.get("level", UserLevel.INTERMEDIATE.value))
    except: level = UserLevel.INTERMEDIATE
    try: mode = ChatMode(settings.get("mode", ChatMode.NATURAL.value))
    except: mode = ChatMode.NATURAL
    
    # target_speed가 None이면 settings에서 추출하거나 레벨 기본값 사용
    if target_speed is None:
        target_speed = settings.get("target_speed", level.default_speed)
    
    config = DojoConfig(
        level=level, mode=mode, is_missile_mode=settings.get("is_missile_mode", False),
        topic=settings.get("topic", "Daily"), missile_timeout=settings.get("missile_timeout", 2.0),
        model=settings.get("model", "gpt-4o-mini-realtime-preview")
    )
    return PromptManager.build(config, target_speed)

def get_audio_speed_from_settings(settings: Dict[str, Any]) -> float:
    """Extract voice speed based on user level for Realtime API"""
    try: 
        level = UserLevel(settings.get("level", UserLevel.INTERMEDIATE.value))
        return level.default_speed
    except: 
        return 1.0

def get_voice_speed_from_level(level_value: str) -> float:
    """
    Realtime API voice speed 반환 (0.6 ~ 1.2 범위)
    - 왕초보: 0.7 (느림)
    - 초급: 0.8 (약간 느림)
    - 중급: 0.9 (보통)
    - 고급: 1.0 (정상)
    """
    speed_map = {
        "Wangchobo (왕초보)": 0.7,
        "Beginner (초급)": 0.8,
        "Intermediate (중급)": 0.9,
        "Advanced (고급)": 1.0
    }
    return speed_map.get(level_value, 1.0)


# ===============================================
# HTML / JS (Debug Enhanced)
# ===============================================

REALTIME_CLIENT_HTML_TEMPLATE = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI English Dojo</title>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  
  <!-- [DEBUG] Global Log Capture System (Must be first) -->
  <script>
    (function() {
        window.logBuffer = [];
        const originalLog = console.log;
        const originalWarn = console.warn;
        const originalError = console.error;
        
        function formatArgs(args) {
            return args.map(arg => {
                if (typeof arg === 'object') {
                    try { return JSON.stringify(arg); } catch(e) { return String(arg); }
                }
                return String(arg);
            }).join(' ');
        }
        
        function pushLog(level, args) {
            const time = new Date().toISOString().split('T')[1].slice(0, -1);
            window.logBuffer.push(`[${time}] [${level}] ${formatArgs(args)}`);
            // Limit buffer size
            if (window.logBuffer.length > 5000) window.logBuffer.shift();
        }

        console.log = function(...args) { pushLog('INFO', args); originalLog.apply(console, args); };
        console.warn = function(...args) { pushLog('WARN', args); originalWarn.apply(console, args); };
        console.error = function(...args) { pushLog('ERROR', args); originalError.apply(console, args); };
        
        window.downloadLogs = function() {
            const blob = new Blob([window.logBuffer.join('\n')], {type: 'text/plain'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `debug_log_${Date.now()}.txt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        };
    })();
  </script>

  <style>
    :root { --bg: #f8f9fa; --card-bg: #fff; --primary: #007bff; --text: #212529; --shadow: 0 4px 12px rgba(0,0,0,0.1); --err: #dc3545; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 20px; margin: 0; overflow-y: hidden; }
    
    .status-bar { display: flex; justify-content: space-between; align-items: center; background: var(--card-bg); padding: 15px 25px; border-radius: 12px; box-shadow: var(--shadow); margin-bottom: 20px; font-weight: 600; }
    
    button { padding: 12px 24px; cursor: pointer; border: none; background: var(--primary); color: white; border-radius: 8px; font-weight: bold; margin: 0 5px; }
    button:disabled { opacity: 0.5; cursor: not-allowed; background: #6c757d; }
    
    .log-box { font-family: monospace; font-size: 11px; color: #333; height: 120px; overflow-y: auto; background: #f1f3f5; padding: 10px; border-radius: 8px; margin-top: 10px; border: 1px solid #dee2e6; }
    
    /* === MISSILE MODE UI === */
    .missile-box { height: 180px; background: #e9ecef; border-radius: 12px; position: relative; overflow: hidden; margin-bottom: 20px; display: none; border: 2px solid #dee2e6; }
    .missile-box.active { display: block; }
    
    .ninja-icon { position: absolute; top: 50%; left: 50px; transform: translateY(-50%) scaleX(-1); font-size: 80px; z-index: 5; }
    .robot-icon { position: absolute; top: 50%; right: 50px; transform: translateY(-50%); font-size: 80px; z-index: 5; }
    
    /* 미사일 애니메이션 (좌표 보정됨) */
    @keyframes fly {
        0% { right: 100px; opacity: 1; transform: translateY(-50%) scale(1); }
        90% { right: calc(100% - 120px); opacity: 1; transform: translateY(-50%) scale(1); }
        100% { right: calc(100% - 100px); opacity: 0; transform: translateY(-50%) scale(1.5); }
    }
    
    .missile-obj {
        position: absolute;
        top: 50%;
        right: 100px; /* 로봇 앞 */
        font-size: 40px;
        transform: translateY(-50%);
        display: none;
        z-index: 10;
    }
    
    .missile-obj.firing {
        display: block;
        animation: fly var(--flight-time, 0.6s) ease-in forwards;
    }

    /* === LIVE TRANSCRIPT UI === */
    .transcript-box {
        background: #fff;
        border: 2px solid #007bff;
        border-radius: 12px;
        padding: 15px;
        margin: 15px 0;
        box-shadow: 0 4px 12px rgba(0,123,255,0.1);
    }
    .t-row {
        margin-bottom: 8px;
        font-size: 16px;
        line-height: 1.4;
    }
    .t-label {
        display: inline-block;
        width: 60px;
        font-weight: 800;
        text-transform: uppercase;
    }
    .t-user .t-label { color: #fd7e14; }
    .t-ai .t-label { color: #007bff; }
    .t-content { font-weight: 500; color: #343a40; }
    
    .mic-meter { width: 200px; height: 10px; background: #ddd; border-radius: 5px; overflow: hidden; margin: 0 auto 10px; }
    .mic-bar { height: 100%; background: #28a745; width: 0%; transition: width 0.05s; }
    .button-container { display: flex; justify-content: center; margin-bottom: 10px; }
  </style>
</head>
<body>
  <div id="root"></div>
  <audio id="remoteAudio" autoplay playsinline></audio>
  <script id="dogo-settings" type="application/json">__SETTINGS_JSON__</script>

<script>
  // === GLOBAL VARIABLES for Logic Control ===
  let pc = null, dc = null, micStream = null;
  let missileWaitTimer = null;   // 0.5초 대기 타이머
  let missileFlightTimer = null; // 비행 시간 타이머
  let isMissileActive = false;
  let pendingUserItems = [];     // AI 말 중 수집한 사용자 발화 아이템
  let aiSpeaking = false;        // AI 발화 구간 플래그
  
  // [IMPORTANT] VAD State for Manual Detection
  let vadState = "SILENCE"; 
  let lastSpeechTime = 0;
  let speechStartTime = null; // 후보 발화 시작 시각 (잡음 필터용)
  let lastAvg = 0;            // 급격한 소리 변화 감지용
  let speechActive = false;   // 현재 발화 블록 활성 여부
  let speechAccumMs = 0;      // 누적 발화 시간(ms)
  let lastVoiceMs = 0;        // 마지막 음성 감지 시각
  
  window.debug_vad_count = 0;    // VAD 디버그 카운터

  window.log = function(msg, type="norm") {
      const el = document.querySelector(".log-box");
      if(el) { 
          const color = type === 'err' ? 'red' : (type === 'sys' ? 'blue' : 'black');
          el.innerHTML += `<div style="color:${color}">[${new Date().toLocaleTimeString()}] ${msg}</div>`; 
          el.scrollTop = el.scrollHeight; 
      }
      console.log(msg);
  }

  // ★ CENTRALIZED EVENT PROCESSOR (Manual VAD -> Here -> Logic) ★
  // Returns false if echo-blocked (caller must revert vadState)
  function processVadEvent(type) {
      // 1. SPEECH STARTED (Cancel Attack)
      if (type === 'input_audio_buffer.speech_started') {
          if (aiSpeaking) {
              console.warn("%c[ECHO BLOCKED] 🛡️ AI is speaking! Ignoring VAD.", "background: red; color: white; font-weight: bold");
              return false;
          }

          window.debug_vad_count++;
          console.log(`%c[VAD #${window.debug_vad_count}] 🎤 START`, 'color: yellow; background: #333; font-weight: bold');
          
          if(window.updateStatus) window.updateStatus({userSpk: true});

          if (missileWaitTimer || missileFlightTimer) {
              console.log("%c[DEFENSE] 🛡️ Attack Canceled! (User kept speaking)", "color: orange; font-weight: bold");
              clearTimeout(missileWaitTimer);
              clearTimeout(missileFlightTimer);
              missileWaitTimer = null;
              missileFlightTimer = null;
              isMissileActive = false;
              if(window.updateStatus) window.updateStatus({ firing: false, hit: false });
          }
          return true;
      }
      
      // 2. SPEECH STOPPED (Commit & Launch)
      if (type === 'input_audio_buffer.speech_stopped') {
          if (aiSpeaking) {
              console.warn("%c[ECHO BLOCKED] 🛡️ AI speaking! Ignoring speech_stopped.", "background: red; color: white; font-weight: bold");
              return false;
          }

          console.log(`%c[VAD #${window.debug_vad_count}] 🔇 STOP`, 'color: #ccc; background: #333');
          if(window.updateStatus) window.updateStatus({userSpk: false});

          if (SETTINGS.is_missile_mode) {
              // Missile Mode: commit하지 않음 → 버퍼에 축적, HIT 시 한 번에 commit
              console.log("%c[MISSILE] 📦 Audio buffered (not committed)", "color: #ffc107");

              console.log("%c[TIMER] ⏳ Wait Timer STARTED (0.5s)", "color: orange");
              missileWaitTimer = setTimeout(() => {
                  missileWaitTimer = null;
                  console.log("%c[MISSILE] 🚀 LAUNCHED!", "color: white; background: #dc3545; font-weight: bold");
                  
                  if(window.updateStatus) window.updateStatus({ firing: true });
                  isMissileActive = true;
                  
                  const audioEl = document.getElementById("remoteAudio");
                  console.log("[DEBUG] 🚀 Launching! Pausing Audio. CurrentTime:", audioEl ? audioEl.currentTime : "null");
                  if(audioEl) audioEl.pause();

                  const flightTimeMs = (SETTINGS.missile_duration || 0.6) * 1000;
                  missileFlightTimer = setTimeout(() => {
                      missileFlightTimer = null;
                      if (isMissileActive) {
                          console.log("%c[HIT] 💥 Impact! Committing ALL audio & Requesting Response...", "color: #00ff00; background: black; font-weight: bold");
                          if(window.updateStatus) window.updateStatus({ firing: false, hit: true });
                          
                          if(dc && dc.readyState === 'open') {
                              dc.send(JSON.stringify({type: "input_audio_buffer.commit"}));
                              const isCorrection = SETTINGS.mode === "교정 후 대답해주기";
                              const createMsg = {type: "response.create"};
                              if (isCorrection) {
                                  createMsg.response = {
                                      instructions: "MANDATORY: Say 'Correction.' first and correct the user's sentence (even if they asked a question), THEN say 'Response.' and give your answer. NEVER skip the correction step."
                                  };
                              }
                              dc.send(JSON.stringify(createMsg));
                          }
                          
                          const audioEl = document.getElementById("remoteAudio");
                          if(audioEl) {
                              console.log("[DEBUG] 💥 Impact! Playing Audio. Before Play CurrentTime:", audioEl.currentTime);
                              setTimeout(() => {
                                  console.log(`[AUDIO] 🔊 Playing response. CurrentTime: ${audioEl.currentTime}`);
                                  audioEl.play().then(() => {
                                      console.log("[AUDIO] Play success");
                                  }).catch(e => console.error("[AUDIO] Play failed", e));
                              }, 30);
                          }

                          setTimeout(() => {
                              if(window.updateStatus) window.updateStatus({ hit: false });
                              isMissileActive = false;
                          }, 1500);
                      }
                  }, flightTimeMs);
              }, 500);
          } else {
              console.log("%c[SEND] 📤 Audio Commit + Response", "color: green");
              if(dc && dc.readyState === 'open') {
                  dc.send(JSON.stringify({type: "input_audio_buffer.commit"}));
                  const isCorrection = SETTINGS.mode === "교정 후 대답해주기";
                  const createMsg = {type: "response.create"};
                  if (isCorrection) {
                      createMsg.response = {
                          instructions: "MANDATORY: Say 'Correction.' first and correct the user's sentence (even if they asked a question), THEN say 'Response.' and give your answer. NEVER skip the correction step."
                      };
                  }
                  dc.send(JSON.stringify(createMsg));
              }
          }
          return true;
      }
      return true;
  }

  // Audio Visualizer
  function startVisualizer(stream) {
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);
      const buffer = new Uint8Array(analyser.frequencyBinCount);
      
      function draw() {
          analyser.getByteFrequencyData(buffer);
          let sum = 0;
          let max = 0;
          for(let i=0; i<buffer.length; i++) {
              const v = buffer[i];
              sum += v;
              if (v > max) max = v;
          }
          const avg = sum / buffer.length;
          if(window.updateStatus) window.updateStatus({micLevel: avg});
          
          // ★★★ MANUAL VAD LOGIC (Noise Robust) ★★★
          if (SETTINGS.is_missile_mode) {
              const VAD_THRESHOLD = isMissileActive ? 50 : 35;
              const CANCEL_BOOST = 80;
              const WORD_GAP_MS = 600;
              const TOTAL_MIN_MS = isMissileActive ? 200 : 400;
              
              const now = Date.now();
              const delta = avg - lastAvg;

              // [핵심] 침묵 중 주기적 버퍼 클리어 (AI 응답 후 쌓이는 침묵/잡음 제거)
              if (vadState === "SILENCE" && !speechActive && window.needsBufferClear) {
                  if (!window.lastBufferClearTime || now - window.lastBufferClearTime > 1000) {
                      if (dc && dc.readyState === 'open') {
                          dc.send(JSON.stringify({type: "input_audio_buffer.clear"}));
                      }
                      window.lastBufferClearTime = now;
                  }
              }

              if (avg > VAD_THRESHOLD || (isMissileActive && avg > CANCEL_BOOST)) {
                  if (!speechStartTime) speechStartTime = now;
                  const duration = now - speechStartTime;

                  const isThumpSpike = (delta > 40 && max > 180 && duration < 300);
                  if (isThumpSpike) {
                      speechStartTime = null;
                  } else {
                      if (!speechActive) speechActive = true;
                      lastVoiceMs = now;
                      speechAccumMs += duration;
                      speechStartTime = now;

                      if (speechAccumMs >= TOTAL_MIN_MS || (isMissileActive && avg > CANCEL_BOOST)) {
                          lastSpeechTime = now;
                          if (vadState === "SILENCE") {
                              const accepted = processVadEvent('input_audio_buffer.speech_started');
                              if (accepted) {
                                  vadState = "SPEAKING";
                                  window.needsBufferClear = false;
                              } else {
                                  speechActive = false;
                                  speechAccumMs = 0;
                              }
                          }
                      }
                  }
              } else {
                  speechStartTime = null;
                  if (speechActive && (now - lastVoiceMs > WORD_GAP_MS)) {
                      speechActive = false;
                      speechAccumMs = 0;
                      if (vadState === "SPEAKING") {
                          const accepted = processVadEvent('input_audio_buffer.speech_stopped');
                          if (accepted) {
                              vadState = "SILENCE";
                          }
                      }
                  } else if (vadState === "SPEAKING") {
                      if (now - lastSpeechTime > 500) {
                          const accepted = processVadEvent('input_audio_buffer.speech_stopped');
                          if (accepted) {
                              vadState = "SILENCE";
                          }
                      }
                  }
              }

              lastAvg = avg;
          }
          
          requestAnimationFrame(draw);
      }
      draw();
  }

  window.connectSystem = async function() {
      try {
          // [NEW] Audio Debug Timer
          if (window.audioDebugTimer) clearInterval(window.audioDebugTimer);
          window.audioDebugTimer = setInterval(() => {
              const el = document.getElementById("remoteAudio");
              if (el && !el.paused) {
                  console.log(`%c[AUDIO TRACK] Time: ${el.currentTime.toFixed(2)}s | Ended: ${el.ended} | Muted: ${el.muted}`, "color: #aaa; font-size: 10px");
              }
          }, 100);

          window.log("Connecting...", "sys");
          micStream = await navigator.mediaDevices.getUserMedia({ 
              audio: {
                  echoCancellation: true,
                  noiseSuppression: true,
                  autoGainControl: true
              } 
          });
          startVisualizer(micStream);
          
          pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
          
          pc.ontrack = (event) => {
              const el = document.getElementById("remoteAudio");
              el.srcObject = event.streams[0];
              
              // [NEW] Audio Debug Listeners
              el.onplay = () => console.log("%c[EVENT] 🔊 Audio 'play' event fired", "color: lime");
              el.onpause = () => console.log("%c[EVENT] ⏸️ Audio 'pause' event fired", "color: orange");
              el.onseeking = () => console.log("%c[EVENT] ⏩ Audio 'seeking' (Reset?)", "color: magenta");

              // [중요] 오디오 초기 상태는 Pause (타격 시 재생)
              if (SETTINGS.is_missile_mode) {
                  el.pause();
                  console.log("[AUDIO] Paused initially for Missile Mode");
              } else {
                  el.play();
              }
          };

          micStream.getTracks().forEach(track => pc.addTrack(track, micStream));
          dc = pc.createDataChannel("oai-events");
          
          dc.onopen = () => {
             window.log("Connected! Session Ready.", "sys");
             console.log("%c[CONNECTION] DataChannel OPEN! Setting status to CONNECTED", "color: green; font-weight: bold");
             if(window.updateStatus) window.updateStatus({conn: "CONNECTED"}); // 강제 업데이트
          };
          
          dc.onmessage = (e) => {
              const ev = JSON.parse(e.data);
              
              // 1. VAD START (Speech Started) - SERVER SIDE (Ignored in Missile Mode due to Manual VAD)
              // NOTE: If server VAD is disabled, we won't get this event, which is why Manual VAD is critical.
              // However, if we are in Normal Mode, we use this.
              if (!SETTINGS.is_missile_mode) {
                  if(ev.type === 'input_audio_buffer.speech_started') {
                      processVadEvent(ev.type);
                  }
                  if(ev.type === 'input_audio_buffer.speech_stopped') {
                      processVadEvent(ev.type);
                  }
              }

              // [NEW] 3. STT/TTS EVENTS FOR DEBUGGING
              // User Transcript (STT)
              if (ev.type === 'conversation.item.input_audio_transcription.completed') {
                  const text = ev.transcript.trim();
                  const itemId = ev.item_id || ev.conversation_item_id || (ev.item && ev.item.id);
                  console.log(`%c[STT] User said: "${text}"`, 'color: #fd7e14; font-weight: bold; background: #fff3cd; padding: 2px');
                  if (window.updateTranscript) window.updateTranscript({ user: text });
                  if (itemId && aiSpeaking) {
                      pendingUserItems.push({ id: itemId, ts: Date.now() });
                      console.log(`%c[STT] (during AI) item_id stored: ${itemId}`, 'color: #ffb347; font-weight: bold');
                  }
              }
              
              // AI Transcript (TTS)
              if (ev.type === 'response.audio_transcript.done') {
                  const text = ev.transcript.trim();
                  console.log(`%c[TTS] AI said: "${text}"`, 'color: #007bff; font-weight: bold; background: #e7f5ff; padding: 2px');
                  if (window.updateTranscript) window.updateTranscript({ ai: text });
              }
              
              if (ev.type === 'response.created') {
                  aiSpeaking = true;
                  console.log("%c[AI] Speaking started (response.created)", "color: #20c997; font-weight: bold");
              }
              
              // [BOUNDARY] AI 말 끝 이벤트(가능한 모든 종료 신호)에서 삭제 + 버퍼 클리어 예약
              if (ev.type === 'response.audio_transcript.done' || ev.type === 'response.done' || ev.type === 'response.audio.done') {
                  aiSpeaking = false;
                  if (dc && dc.readyState === 'open') {
                      if (pendingUserItems.length > 0) {
                          pendingUserItems.forEach(({ id }) => {
                              console.log(`%c[AI DONE] 🗑️ Deleting pre-AI-end item: ${id}`, "color: #ff6b6b; font-weight: bold");
                              dc.send(JSON.stringify({ type: "conversation.item.delete", item_id: id, id }));
                          });
                      }
                      // [핵심] 즉시 클리어하지 않고 플래그 설정 → draw() 루프에서 침묵 중 주기적으로 클리어
                      if (SETTINGS.is_missile_mode) {
                          window.needsBufferClear = true;
                          window.lastBufferClearTime = null;
                          console.log("%c[BUFFER] 🔖 Buffer clear scheduled (will clear during silence)", "color: #17a2b8; font-weight: bold");
                      }
                  }
                  pendingUserItems = [];
              }
          };

          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);
          const resp = await fetch(`${API_BASE}/sdp`, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ sdp: offer.sdp, settings: SETTINGS })
          });
          const data = await resp.json();
          await pc.setRemoteDescription({ type: "answer", sdp: data.sdp });

      } catch (e) {
          window.log("Connection Failed: " + e.message, "err");
      }
  };

  window.disconnectSystem = () => {
      if(pc) pc.close();
      if(micStream) micStream.getTracks().forEach(t => t.stop());
      if(window.updateStatus) window.updateStatus({conn: "IDLE", micLevel: 0});
  };
</script>

<script type="text/babel">
  let SETTINGS = {};
  try { SETTINGS = JSON.parse(document.getElementById("dogo-settings").textContent); } catch(e) {}
  // ★ CLOUD FIX: sdp_server_url 있으면 사용, 없으면 로컬 폴백
  const API_BASE = SETTINGS.sdp_server_url
    || ("http://" + window.location.hostname + ":" + (SETTINGS.server_port || 8016));
  const { useState, useEffect } = React;

  const App = () => {
    const [status, setStatus] = useState({ conn: "IDLE", userSpk: false, firing: false, hit: false, micLevel: 0 });
    const [transcript, setTranscript] = useState({ user: "(Waiting...)", ai: "(Waiting...)" });

    useEffect(() => {
        window.updateStatus = (s) => setStatus(p => ({ ...p, ...s }));
        window.updateTranscript = (data) => setTranscript(p => ({ ...p, ...data }));
        
        // CSS 변수 업데이트 (비행 시간)
        const duration = SETTINGS.missile_duration || 0.6;
        document.documentElement.style.setProperty('--flight-time', duration + 's');
        
        // [DEBUG] 상태 변경 로그
        console.log(`%c[STATUS CHANGE] Conn: ${status.conn}, UserSpk: ${status.userSpk}`, "color: cyan");
    }, [status.conn, status.userSpk]);

    const handleConnect = () => {
        setStatus(p => ({ ...p, conn: "CONNECTING..." }));
        window.connectSystem();
    };

    return (
      <div style={{display:'flex', flexDirection:'column', height:'calc(100vh - 40px)'}}>
        <div className="status-bar">
           <div>AI ENGLISH DOJO | {SETTINGS.level}</div>
           <div style={{color: status.conn==='CONNECTED'?'#28a745':'#6c757d'}}>{status.conn}</div>
        </div>

        {/* === UI: MISSILE BOX === */}
        <div className={`missile-box ${SETTINGS.is_missile_mode ? 'active' : ''}`}>
           <div className="ninja-icon">
               {status.hit ? '😵' : '🥷'}
           </div>
           
           {/* 미사일 객체 */}
           <div className={`missile-obj ${status.firing ? 'firing' : ''}`}>🚀</div>

           <div className="robot-icon">🤖</div>
           
           <div style={{position:'absolute', bottom:'10px', width:'100%', textAlign:'center', fontSize:'16px', fontWeight:'bold', color: status.hit?'var(--err)':'#666'}}>
               {status.hit ? "HIT! (Responding...)" : (status.firing ? "INCOMING!" : "DEFEND!")}
           </div>
        </div>

        {/* === NEW: LIVE TRANSCRIPT LOG === */}
        <div className="transcript-box">
            <div style={{textAlign:'center', marginBottom:'10px', color:'#6c757d', fontSize:'12px', fontWeight:'bold', letterSpacing:'1px'}}>📢 LIVE TRANSCRIPT</div>
            <div className="t-row t-user">
                <span className="t-label">[YOU]</span>
                <span className="t-content">{transcript.user}</span>
            </div>
            <div className="t-row t-ai">
                <span className="t-label">[AI]</span>
                <span className="t-content">{transcript.ai}</span>
            </div>
            {(status.conn === "CONNECTED" && !status.userSpk) && (
              <div style={{marginTop:'10px', textAlign:'center', fontSize:'14px', color:'#495057', fontWeight:'700'}}>
                🎤 지금 말씀해주세요. AI가 듣고 있어요.
              </div>
            )}
        </div>

        <div style={{flex:1}}></div>

        <div style={{textAlign:'center', marginBottom:10}}>
            <div style={{fontSize:12, color:'#666', marginBottom:2}}>MICROPHONE ({status.userSpk ? 'Talking' : 'Silent'})</div>
            <div className="mic-meter"><div className="mic-bar" style={{width: `${Math.min(100, status.micLevel * 2)}%`}}></div></div>
        </div>

        <div className="button-container">
           <button onClick={handleConnect} disabled={status.conn==='CONNECTED' || status.conn==='CONNECTING...'}>Connect</button>
           <button onClick={() => window.disconnectSystem()} disabled={status.conn!=='CONNECTED'}>Disconnect</button>
           {/* [LOG DOWNLOAD BUTTON] */}
           <button onClick={() => window.downloadLogs()} style={{backgroundColor:'#6f42c1'}}>📥 Save Logs</button>
        </div>
        
        <div className="log-box">Ready. Missile Mode: {SETTINGS.is_missile_mode ? "ON" : "OFF"}</div>
      </div>
    );
  };
  const root = ReactDOM.createRoot(document.getElementById('root'));
  root.render(<App />);
</script>
<script>
// === 🕵️‍♂️ EMERGENCY DEBUGGER (GLOBAL) ===
(function() {
    console.log("%c[DEBUGGER] Global Monitor Started", "color: white; background: red; font-weight: bold");

    // 1. 오디오 데이터 도착 감시 (DataChannel 해킹)
    const observeDataChannel = () => {
        if(window.dc && !window.dc.hasDebugHook) {
            window.dc.hasDebugHook = true;
            const originalOnMessage = window.dc.onmessage;
            window.dc.onmessage = (e) => {
                const ev = JSON.parse(e.data);
                
                // [원인 분석 1] 오디오 데이터가 벌써 오고 있는가?
                if (ev.type === 'response.audio.delta') {
                    if (!window.audioPacketCount) window.audioPacketCount = 0;
                    window.audioPacketCount++;
                    if (window.audioPacketCount % 50 === 0) { // 너무 많으니 50번마다 출력
                        console.log(`%c[INCOMING AUDIO] 🌊 Data Flowing... (Packets: ${window.audioPacketCount})`, "color: cyan");
                    }
                }
                
                // [원인 분석 2] 응답이 두 번 생성되었는가?
                if (ev.type === 'response.created') {
                    console.log(`%c[RESPONSE START] 🎬 New AI Response Started!`, "color: #00ff00; background: #004400; font-weight: bold; border: 2px solid lime");
                    window.audioPacketCount = 0; // 카운터 리셋
                }

                if (originalOnMessage) originalOnMessage(e);
            };
            console.log("%c[DEBUGGER] DataChannel Hooked!", "color: lime");
        }
    };

    // 2. 오디오 플레이어 상태 강제 조회 (0.5초마다)
    setInterval(() => {
        const el = document.getElementById("remoteAudio");
        observeDataChannel(); // DC 연결되면 즉시 훅 설치 시도

        if (el) {
            const isMuted = el.muted;
            const isPaused = el.paused;
            const time = el.currentTime;
            const hasSource = !!el.srcObject;
            
            // 상태가 변하거나 재생 중일 때만 로그
            if (hasSource && (!isPaused || time > 0)) {
                console.log(`%c[PLAYER] Time: ${time.toFixed(2)}s | Paused: ${isPaused} | Muted: ${isMuted} | Vol: ${el.volume}`, 
                    "color: yellow; background: #222");
            }
        }
    }, 500);
})();
</script>
</body>
</html>
"""

class RealtimeRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        load_dotenv()
        if self.path == "/sdp":
            try:
                length = int(self.headers['Content-Length'])
                data = json.loads(self.rfile.read(length).decode('utf-8'))
                
                api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
                settings = data.get("settings", {})
                
                # target_speed를 settings에서 가져옴 (컨트롤 타워에서 전달됨)
                target_speed = settings.get("target_speed", settings.get("audio_speed", 1.0))
                
                session_url = "https://api.openai.com/v1/realtime/sessions"
                session_headers = { "Authorization": f"Bearer {api_key}", "Content-Type": "application/json" }
                
                # build_instructions_from_dict에 target_speed 전달
                generated_instructions = build_instructions_from_dict(settings, target_speed)
                
                # OpenAI Realtime API 세션 페이로드
                # Note: 오디오 + 텍스트 모두 받음 (정상 모드)
                session_payload = { 
                    "model": settings.get("model", "gpt-4o-mini-realtime-preview"), 
                    "instructions": generated_instructions,
                    "modalities": ["text", "audio"],  # 오디오 복구!
                    "voice": "alloy",
                    "input_audio_transcription": { "model": "whisper-1" },
                }

                # [MISSILE MODE] Manual VAD (turn_detection off)
                # 클라이언트가 직접 commit하므로 서버 VAD는 끕니다.
                if settings.get("is_missile_mode", False):
                    session_payload["turn_detection"] = None
                    print("[CONFIG] Missile Mode ON -> turn_detection DISABLED (Manual VAD)")
                else:
                    session_payload["turn_detection"] = { "type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300, "silence_duration_ms": 500 }
                
                print(f"[CLIENT SPEED CONTROL] Browser playbackRate will be set to: {target_speed}x")
                

                # [Debug] Print instructions and speed from control tower
                print(f"\n=== GENERATED INSTRUCTIONS ===")
                print(f"[CONTROL TOWER] target_speed: {target_speed}x")
                print(f"[LEVEL] {settings.get('level', 'Unknown')}")
                print(f"[INSTRUCTIONS]\n{session_payload['instructions']}")
                print(f"==============================\n")
                
                req = urllib.request.Request(session_url, data=json.dumps(session_payload).encode('utf-8'), headers=session_headers, method="POST")
                with urllib.request.urlopen(req) as resp:
                    token = json.loads(resp.read().decode('utf-8'))['client_secret']['value']
                    
                url = f"https://api.openai.com/v1/realtime?model={session_payload['model']}"
                headers = { "Authorization": f"Bearer {token}", "Content-Type": "application/sdp" }
                req = urllib.request.Request(url, data=data['sdp'].encode('utf-8'), headers=headers, method="POST")
                with urllib.request.urlopen(req) as resp:
                    answer = resp.read().decode('utf-8')

                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                # Return instructions to client for double-check
                self.wfile.write(json.dumps({"sdp": answer, "instructions": session_payload["instructions"]}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    try: 
        # [Debug] Port Re-use logic
        http.server.HTTPServer.allow_reuse_address = True
        httpd = http.server.HTTPServer(('', SERVER_PORT), RealtimeRequestHandler)
        print(f"python_http_server_started_on_port_{SERVER_PORT}")
        httpd.serve_forever()
    except Exception as e: 
        print(f"server_start_error: {e}")

# ★ CLOUD FIX: SDP_SERVER_URL 환경변수가 있으면 외부 서버 사용 → 로컬 스레드 불필요
if not os.getenv("SDP_SERVER_URL"):
    if 'server_thread_v16' not in st.session_state:
        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        st.session_state.server_thread_v16 = t

st.title("AI English Dojo")
if not API_KEY: st.error("No API Key")

with st.sidebar:
    st.header("Settings")
    
    # [3단계] 레벨 변경 시 자동 재연결 트리거
    if 'trigger_reconnect' not in st.session_state:
        st.session_state.trigger_reconnect = False
    if 'previous_level' not in st.session_state:
        st.session_state.previous_level = None
    
    def on_level_change():
        """레벨 변경 시 target_speed를 즉시 업데이트하고 재연결 트리거"""
        new_level = st.session_state.get('level_select', 'Intermediate (중급)')
        old_level = st.session_state.get('previous_level')
        new_speed = update_target_speed(new_level)
        st.session_state.updated = time.time()
        
        # 레벨이 실제로 변경되었으면 재연결 트리거
        if old_level is not None and old_level != new_level:
            st.session_state.trigger_reconnect = True
            print(f"[LEVEL CHANGE] {old_level} -> {new_level}, Speed: {new_speed}x, RECONNECT TRIGGERED!")
        else:
            print(f"[LEVEL INIT] Level: {new_level}, Speed: {new_speed}x")
        
        st.session_state.previous_level = new_level
    
    def update(): 
        st.session_state.updated = time.time()
        # 현재 레벨에 맞게 target_speed도 동기화
        current_level = st.session_state.get('level_select', 'Intermediate (중급)')
        initialize_target_speed(current_level)
        
    level = st.selectbox(
        "Level", 
        [l.value for l in UserLevel], 
        index=2, 
        on_change=on_level_change,
        key='level_select'
    )
    
    # 레벨 선택 후 target_speed 동기화 및 이전 레벨 저장
    initialize_target_speed(level)
    if st.session_state.previous_level is None:
        st.session_state.previous_level = level
    
    topic = st.selectbox("Topic", ["일상 수다 (Daily)", "여행 (Travel)", "비즈니스 (Business)", "식당 주문 (Ordering)"], on_change=update)
    mode = st.selectbox("Mode", [m.value for m in ChatMode], on_change=update)
    st.divider()
    missile = st.checkbox("Missile Mode", False, on_change=update)
    missile_duration = 0.6
    if missile:
        missile_duration = st.slider("Missile Speed (sec)", 0.1, 3.0, 0.6, 0.1, on_change=update)
    if st.button("Reconnect / Apply Settings"): 
        st.session_state.trigger_reconnect = True
        update()

# st.session_state.target_speed를 컨트롤 타워로 사용
current_target_speed = get_target_speed()

# [3단계] 재연결 트리거 확인
trigger_reconnect = st.session_state.get('trigger_reconnect', False)

client_config = {
    "level": level,
    "topic": topic,
    "mode": mode,
    "is_missile_mode": missile,
    "missile_duration": missile_duration,
    "server_port": SERVER_PORT,
    "audio_speed": current_target_speed,  # Deprecated (브라우저 playbackRate용)
    "target_speed": current_target_speed,  # Deprecated
    "voice_speed": get_voice_speed_from_level(level),  # ★ Realtime API voice speed (0.6 ~ 1.2)
    "session_update_required": st.session_state.get('session_update_required', False),
    "trigger_reconnect": trigger_reconnect,  # [3단계] 재연결 트리거
    "api_key": API_KEY,  # TTS API 호출을 위한 API 키
    # ★ CLOUD: Railway SDP 서버 URL (환경변수 SDP_SERVER_URL 로 주입; 없으면 로컬 폴백)
    "sdp_server_url": os.getenv("SDP_SERVER_URL", ""),
    "__update_token__": str(time.time())  # Force HTML/JS reload on setting change
}

# 플래그 리셋
if st.session_state.get('session_update_required', False):
    st.session_state.session_update_required = False
if trigger_reconnect:
    st.session_state.trigger_reconnect = False

# [1단계 Python 로그] 상시 디버그 정보 표시
st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 Debug Panel (1단계)")

# 실시간 상태 표시
debug_col1, debug_col2 = st.sidebar.columns(2)
with debug_col1:
    st.metric("Voice Speed", f"{client_config['voice_speed']}x")
with debug_col2:
    st.metric("Level", level.split(" ")[0])

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎛️ Voice Speed Control Tower")
st.sidebar.code(f"""
[Realtime API Voice Speed]
voice_speed: {client_config['voice_speed']}x
level: {level}
trigger_reconnect: {trigger_reconnect}

[Speed Mapping]
왕초보: 0.7x (slow)
초급: 0.8x (slightly slow)
중급: 0.9x (near normal)
고급: 1.0x (normal)
""")

st.sidebar.markdown("**Realtime API Voice Speed:**")
voice_speed = client_config['voice_speed']
if voice_speed == 0.7:
    st.sidebar.success("🐢 느림 (70%) - 왕초보")
elif voice_speed == 0.8:
    st.sidebar.success("🐌 약간 느림 (80%) - 초급")
elif voice_speed == 0.9:
    st.sidebar.info("🚶 거의 보통 (90%) - 중급")
elif voice_speed == 1.0:
    st.sidebar.info("🏃 보통 (100%) - 고급")

# 속도 변경 이력 표시
if st.session_state.get('speed_changed_at'):
    st.sidebar.caption(f"⏱️ Last speed change: {time.strftime('%H:%M:%S', time.localtime(st.session_state.speed_changed_at))}")

st.sidebar.info("💡 **Question-Driven Dialogue:**\n1. AI의 모든 응답이 질문으로 끝남 (강제)\n2. 후속 질문으로 자연스러운 대화 유도\n3. 모든 레벨에서 적용되는 대화 연습\n4. Voice Speed: 레벨별 자동 조정 (0.7~1.0x)")

components.html(
    REALTIME_CLIENT_HTML_TEMPLATE.replace("__SETTINGS_JSON__", json.dumps(client_config)),
    height=900,
    scrolling=False
)
