""
handlers.py — MUGON.CLUB HR Bot
Full interview pipeline: verification, GPT dialogue, AmoCRM sync, notifications.
"""
import os
import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove,
    KeyboardButtonRequestContact
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from gpt import ask_hr_gpt, generate_ai_resume
from amocrm import AmoCRM
from notifier import notify_ceo_pm

logger = logging.getLogger(__name__)
router = Router()

amo = AmoCRM(
    domain=os.environ["AMO_DOMAIN"],
    client_id=os.environ["AMO_CLIENT_ID"],
    client_secret=os.environ["AMO_CLIENT_SECRET"],
    redirect_uri=os.environ["AMO_REDIRECT_URI"],
    refresh_token=os.environ["AMO_REFRESH_TOKEN"],
)

CEO_TG_ID = int(os.environ.get("CEO_TG_ID", "0"))
PM_TG_ID = int(os.environ.get("PM_TG_ID", "0"))
PIPELINE_ID = int(os.environ.get("AMO_PIPELINE_ID", "10599910"))
STATUS_NEW = int(os.environ.get("AMO_STATUS_NEW", "83583878"))


# ─── FSM States ──────────────────────────────────────────────────────────────

class Interview(StatesGroup):
    waiting_contact = State()
    phone_verified = State()
    interviewing = State()
    waiting_resume_file = State()
    completed = State()


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Пройти собеседование")],
            [KeyboardButton(text="🗂 Наши проекты"), KeyboardButton(text="📄 Отправить резюме")],
            [KeyboardButton(text="📞 Поделиться номером", request_contact=True)],
            [KeyboardButton(text="❓ FAQ"), KeyboardButton(text="📬 Связаться с HR")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def share_contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def projects_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 AutoHire — AI рекрутинг", callback_data="project_autohire")],
        [InlineKeyboardButton(text="📊 DataFlow — ETL платформа", callback_data="project_dataflow")],
        [InlineKeyboardButton(text="🚀 Другие проекты", callback_data="project_other")],
        [InlineKeyboardButton(text="✅ Хочу участвовать!", callback_data="project_apply")],
    ])


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    name = message.from_user.first_name or "кандидат"
    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Я — HR-ассистент MUGON.CLUB. Мы строим команду профессионалов для реализации AI-проектов.\n\n"
        "Чтобы начать — поделитесь номером телефона. Это необходимо для верификации.\n"
        "После этого я проведу вас через короткое собеседование.",
        reply_markup=share_contact_kb(),
    )
    await state.set_state(Interview.waiting_contact)


# ─── Contact / Phone Verification ────────────────────────────────────────────

@router.message(Interview.waiting_contact, F.contact)
async def got_contact(message: Message, state: FSMContext, bot: Bot):
    contact = message.contact
    phone = contact.phone_number
    user = message.from_user

    await state.update_data(
        phone=phone,
        tg_id=user.id,
        username=user.username or "",
        full_name=f"{user.first_name or ''} {user.last_name or ''}".strip(),
    )
    await state.set_state(Interview.phone_verified)

    # Create/find lead in AmoCRM
    lead_id = await amo.find_or_create_lead(
        name=f"{user.first_name or 'Кандидат'} @{user.username or user.id}",
        phone=phone,
        tg_id=str(user.id),
        pipeline_id=PIPELINE_ID,
        status_id=STATUS_NEW,
    )
    await state.update_data(lead_id=lead_id)

    await message.answer(
        f"✅ Номер {phone} подтверждён!\n\n"
        "Отлично, теперь начнём собеседование. Я задам вам несколько вопросов.\n"
        "Отвечайте свободно — это живой диалог, не тест с правильными ответами.\n\n"
        "Готовы? Тогда начнём 🚀",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Interview.interviewing)

    # Initialize dialogue history and ask first question
    await state.update_data(history=[], questions_asked=0, scores={})
    first_q = await ask_hr_gpt([], "START_INTERVIEW", user_name=user.first_name)
    await message.answer(first_q)


@router.message(Interview.waiting_contact)
async def request_contact_again(message: Message):
    await message.answer(
        "Для продолжения необходимо поделиться номером телефона.\n"
        "Нажмите кнопку ниже 👇",
        reply_markup=share_contact_kb(),
    )


# ─── Main Interview Flow ──────────────────────────────────────────────────────

@router.message(Interview.interviewing, F.text)
async def interview_message(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    history = data.get("history", [])
    questions_asked = data.get("questions_asked", 0)
    lead_id = data.get("lead_id")
    user_name = message.from_user.first_name

    # Add user message to history
    history.append({"role": "user", "content": message.text})

    # Check if interview should end (30 questions max or GPT decides)
    if questions_asked >= 30:
        await finalize_interview(message, state, bot, history, lead_id, user_name)
        return

    # Get next GPT response
    gpt_reply = await ask_hr_gpt(history, "CONTINUE", user_name=user_name)
    history.append({"role": "assistant", "content": gpt_reply})

    await state.update_data(history=history, questions_asked=questions_asked + 1)
    await message.answer(gpt_reply)

    # Check if GPT signals end of interview
    if "INTERVIEW_COMPLETE" in gpt_reply or questions_asked >= 28:
        await finalize_interview(message, state, bot, history, lead_id, user_name)


@router.message(Interview.interviewing, F.document | F.photo)
async def interview_resume_file(message: Message, state: FSMContext, bot: Bot):
    """Handle resume file upload during interview."""
    data = await state.get_data()
    lead_id = data.get("lead_id")

    # Download and process resume
    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "resume.pdf"
    elif message.photo:
        file_id = message.photo[-1].file_id
        file_name = "resume_photo.jpg"
    else:
        return

    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)

    # Upload to AmoCRM
    if lead_id:
        await amo.upload_resume_file(lead_id, file_bytes, file_name)

    await message.answer(
        "📎 Резюме получено и сохранено в вашей карточке!\n"
        "Я его изучу и задам только недостающие вопросы. Продолжаем 👇"
    )

    # Continue interview with context that resume was received
    data = await state.get_data()
    history = data.get("history", [])
    history.append({"role": "user", "content": f"[Кандидат прислал резюме: {file_name}]"})
    gpt_reply = await ask_hr_gpt(history, "RESUME_RECEIVED", user_name=message.from_user.first_name)
    history.append({"role": "assistant", "content": gpt_reply})
    await state.update_data(history=history)
    await message.answer(gpt_reply)


async def finalize_interview(message: Message, state: FSMContext, bot: Bot, history: list, lead_id: int, user_name: str):
    """Finalize interview: generate AI resume, update AmoCRM, notify CEO/PM."""
    await message.answer(
        "🏁 Мы завершили основную часть собеседования!\n"
        "Сейчас я формирую итоговое резюме и отправляю команде. Это займёт пару секунд..."
    )

    data = await state.get_data()

    # Generate AI resume and scores
    ai_resume = await generate_ai_resume(history, user_name)

    # Update AmoCRM fields
    if lead_id:
        await amo.update_lead_fields(lead_id, ai_resume)

    # Notify CEO and PM
    await notify_ceo_pm(bot, CEO_TG_ID, PM_TG_ID, data, ai_resume)

    await state.set_state(Interview.completed)
    await message.answer(
        "✅ Отлично! Ваше собеседование завершено.\n\n"
        f"Ваш итоговый балл: *{ai_resume.get('total_score', '—')}/100*\n\n"
        "Наша команда рассмотрит вашу кандидатуру и свяжется с вами в течение 2-3 рабочих дней.\n\n"
        "Хотите узнать больше о наших проектах?",
        reply_markup=projects_inline(),
        parse_mode="Markdown",
    )


# ─── Project Info ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("project_"))
async def project_info(callback: CallbackQuery):
    projects = {
        "project_autohire": (
            "🤖 *AutoHire — AI Recruitment Platform*\n\n"
            "Автоматизация найма через AI-агентов: от идеи до готового специалиста.\n"
            "Стек: Python, FastAPI, Telegram, GPT-4, AmoCRM.\n"
            "Ищем: Python разработчиков, AI-инженеров, PM."
        ),
        "project_dataflow": (
            "📊 *DataFlow — ETL & Analytics Platform*\n\n"
            "Система агрегации и обработки данных с дашбордами.\n"
            "Стек: Python, Celery, PostgreSQL, Redis, React.\n"
            "Ищем: Backend разработчиков, Data Engineers."
        ),
        "project_other": (
            "🚀 *Другие проекты MUGON.CLUB*\n\n"
            "У нас есть ряд активных проектов в области AI, автоматизации и продуктовой разработки.\n"
            "Каждый проект — реальная идея с потенциалом монетизации.\n"
            "Детали предоставляются после прохождения собеседования."
        ),
        "project_apply": None,
    }

    if callback.data == "project_apply":
        await callback.message.answer(
            "Отлично! Пройдите собеседование и мы подберём проект под ваш профиль.\n"
            "Нажмите /start чтобы начать."
        )
    else:
        text = projects.get(callback.data, "Информация недоступна")
        await callback.message.answer(text, parse_mode="Markdown")

    await callback.answer()


# ─── Menu Buttons ─────────────────────────────────────────────────────────────

@router.message(F.text == "📋 Пройти собеседование")
async def start_interview_btn(message: Message, state: FSMContext):
    await cmd_start(message, state)


@router.message(F.text == "🗂 Наши проекты")
async def show_projects(message: Message):
    await message.answer(
        "Выберите проект для подробной информации:",
        reply_markup=projects_inline(),
    )


@router.message(F.text == "❓ FAQ")
async def faq(message: Message):
    await message.answer(
        "*Часто задаваемые вопросы*\n\n"
        "❓ *Что такое MUGON.CLUB?*\n"
        "Команда профессионалов, которая реализует AI-проекты от идеи до продукта.\n\n"
        "❓ *Какой формат работы?*\n"
        "Full-time, part-time или проектная занятость. Удалённо.\n\n"
        "❓ *Нужно ли платить за участие?*\n"
        "Нет. Мы платим за результат.\n\n"
        "❓ *Как проходит отбор?*\n"
        "1. Собеседование в боте\n2. Тестовое задание\n3. Онбординг в проект\n\n"
        "❓ *Сколько длится собеседование?*\n"
        "15-20 минут в формате диалога.",
        parse_mode="Markdown",
    )


@router.message(F.text == "📄 Отправить резюме")
async def request_resume(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("lead_id"):
        await message.answer("Сначала нужно пройти верификацию. Нажмите /start")
        return
    await state.set_state(Interview.waiting_resume_file)
    await message.answer(
        "Отправьте файл резюме (PDF, DOCX или фото). "
        "Я изучу его и задам только недостающие вопросы.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(F.text == "📬 Связаться с HR")
async def contact_hr(message: Message):
    await message.answer(
        "Вы можете связаться с HR напрямую:\n"
        "📧 hr@mugon.club\n"
        "Или напишите свой вопрос, и я передам его команде."
    )


# ─── Group: New member greeting ───────────────────────────────────────────────

@router.message(F.new_chat_members)
async def new_member(message: Message):
    for member in message.new_chat_members:
        if not member.is_bot:
            await message.answer(
                f"Привет, {member.first_name}! 👋 Добро пожаловать в MUGON.CLUB!\n\n"
                f"Подпишитесь на нашего HR-бота @MUGON_CLUB_BOT — там вы сможете:\n"
                f"• Пройти собеседование\n"
                f"• Узнать о наших проектах\n"
                f"• Получить тестовое задание\n\n"
                f"Нажмите: @MUGON_CLUB_BOT"
            )


# ─── Re-engagement: remind non-responding candidates ─────────────────────────

@router.message(Interview.interviewing, F.text.in_({"стоп", "пауза", "позже", "потом"}))
async def pause_interview(message: Message, state: FSMContext):
    await message.answer(
        "Хорошо, сохраняю ваш прогресс. Напишите мне когда будете готовы продолжить.\n"
        "Я напомню о себе через 24 часа 😊"
    )


@router.message(Interview.completed)
async def completed_state(message: Message):
    await message.answer(
        "Вы уже прошли собеседование. Наша команда скоро свяжется с вами!\n"
        "Если хотите узнать о проектах — нажмите '🗂 Наши проекты'",
        reply_markup=main_menu(),
    )
