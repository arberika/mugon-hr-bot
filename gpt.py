"""
gpt.py - MUGON HR Bot: OpenAI GPT-4 integration
Professional profile assistant with a short evidence-aware interview
"""
import os
import json
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

# System prompt for the HR agent
SYSTEM_PROMPT = ("""
Ты профессиональный HR-агент MUGON.CLUB. Тебя зовут Алекс.

КОМПАНИЯ: MUGON.CLUB превращает идеи в продукты через AI-агентов.
Проекты: AutoHire (AI-рекрутинг), DataFlow (ETL), Content AI, другие.
Security: детали проектов конфиденциальны — только общие направления.

ЦЕЛЬ: Короткий первичный профиль разработчика для будущего проектного подбора.
Один вопрос за раз. Адаптируй под ответы. Будь живым HR.

ВОПРОСЫ (не более 8, по одному):
1. Текущая роль, seniority, страна и timezone.
2. Основной стек и технологии, с которыми человек реально работал.
3. Один лучший проект: личная роль, сделанный результат и ссылка/доказательство, если готов поделиться.
4. Какие задачи интересны и какие точно не подходят.
5. Доступность в часах в неделю и предпочтительный формат работы.
6. Какой вклад человек хотел бы дать клубу: вопрос, доклад, менторство или помощь команде.
7. Какие материалы можно использовать для отдельной проверки опыта.
8. Что ещё важно учесть при проектном подборе.

ПРАВИЛА:
- 1 вопрос за раз
- Не повторяй уже сказанное
- Уточняй конкретику: пример?
- Если прислали резюме — задай только недостающие вопросы
- Не выставляй человеку публичную оценку и не называй его навык подтверждённым
- Самоописание является предварительным; проверка доказательств проходит отдельно
- Не раскрывай детали внутренних проектов
- Когда профиль достаточен или задано 6-8 вопросов — добавь в конце: INTERVIEW_COMPLETE
""")

# Prompt for generating structured resume
RESUME_PROMPT = ("""
Создай предварительный JSON-профиль кандидата. Верни ТОЛЬКО валидный JSON без markdown.
Не выдумывай отсутствующие факты и не считай самоописание подтверждённым навыком.
Для неизвестных значений используй null или пустой список. Оценки оставляй null,
пока в транскрипте нет проверяемых доказательств.
Обязательные поля: status, verdict, employment_format, hours_per_day,
tech_stack, projects_12m, hard_project, stack_rationale, architecture,
algo_approach, criteria_metrics, scale_approach, modern_tech, tg_openai_cases,
refusals, control_tools, client_mistakes, full_product, ai_projects_3,
learning, best_question, responsibility, last_3_jobs, speed_score,
main_strength, monitoring, security_practice,
engineering_score, ai_automation_score, architecture_score,
delivery_score, communication_score, total_score,
risks, next_step, project_fit, ai_summary
""")


async def ask_hr_gpt(history: list, mode: str, user_name: str = "") -> str:
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if mode == "START_INTERVIEW":
            messages.append({
                "role": "user",
                "content": "Начни собеседование с " + (user_name or "кандидатом") + ". Представься и задай первый вопрос."
            })
        elif mode == "RESUME_RECEIVED":
            messages.extend(history)
            messages.append({"role": "user", "content": "Кандидат прислал резюме. Задай только недостающие вопросы."})
        else:
            messages.extend(history)
        response = await client.chat.completions.create(
            model="gpt-4o", messages=messages, max_tokens=600, temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"GPT error: {e}")
        return "Извините, техническая ошибка. Попробуйте снова."


async def generate_ai_resume(history: list, user_name: str = "") -> dict:
    try:
        transcript = "\n".join([
            ("Кандидат" if m["role"] == "user" else "HR") + ": " + m["content"]
            for m in history
        ])
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": RESUME_PROMPT},
                {"role": "user", "content": "Транскрипт собеседования с " + user_name + ":\n\n" + transcript}
            ],
            max_tokens=2000, temperature=0.3
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            parts = raw.split("\n", 1)
            raw = parts[1].rsplit("```", 1)[0] if len(parts) > 1 else raw
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Resume error: {e}")
        return {
            "status": "В процессе", "verdict": "На паузе",
            "total_score": 0, "next_step": "На паузе",
            "ai_summary": "Ошибка генерации", "risks": []
        }
