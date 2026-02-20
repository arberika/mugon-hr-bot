"""
gpt.py - MUGON HR Bot: OpenAI GPT-4 integration
Professional HR agent with 30-question interview protocol
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

ЦЕЛЬ: Собеседование Fullstack/AI Automation Developer.
Один вопрос за раз. Адаптируй под ответы. Будь живым HR.

БЛОКИ (30 вопросов — по 1 за раз):
БЛОК 1: 3 проекта 12 мес. Сложный проект. Стек и причины выбора.
БЛОК 2: Архитектура сервиса. Алгоритм ТЗ->релиз. Метрики. Масштабирование.
БЛОК 3: Новые технологии. Кейсы TG/OpenAI API. Отказы от проектов. Контроль качества.
БЛОК 4: Полный цикл продукта. 3 AI-проекта. Обучение. Вопрос заказчику. Ответственность. 3 места работы.
БЛОК 5: Скорость 1-10. Сильная сторона. Часы/формат. Риски. Неопределённое ТЗ.
БЛОК 6: Документируемость кода. Оптимизация. Баги в проде. Оценка задач. Мониторинг. Security.

ПРАВИЛА:
- 1 вопрос за раз
- Не повторяй уже сказанное
- Уточняй конкретику: пример?
- Если прислали резюме — задай только недостающие вопросы
- Не раскрывай детали внутренних проектов
- Когда задано 28-30 вопросов — добавь в конце: INTERVIEW_COMPLETE
""")

# Prompt for generating structured resume
RESUME_PROMPT = ("""
Создай JSON резюме кандидата. Верни ТОЛЬКО валидный JSON без markdown.
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
