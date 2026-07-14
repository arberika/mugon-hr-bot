"""
handlers.py - MUGON.CLUB HR Bot
Full interview pipeline: verification, GPT dialogue, AmoCRM sync, notifications.

FIXES:
  - last_activity: recorded on every candidate message for scheduler
  - finalize_interview: idempotent guard (finalized flag) + removed duplicate trigger
  - import time added
"""
import os
import time
import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from gpt import ask_hr_gpt, generate_ai_resume
from amocrm import AmoCRM
from notifier import notify_ceo_pm
from onboarding_copy import (
    ACTION_CLUB,
    ACTION_MEETUP,
    ACTION_PROFILE,
    CLUB_INFO_TEXT,
    MEETUP_TEXT,
    PROFILE_CONSENT_TEXT,
    START_TEXT,
    split_completion_signal,
)

logger = logging.getLogger(__name__)
router = Router()

amo = AmoCRM(
    domain=os.environ["AMO_DOMAIN"],
    client_id=os.environ["AMO_CLIENT_ID"],
    client_secret=os.environ["AMO_CLIENT_SECRET"],
    redirect_uri=os.environ["AMO_REDIRECT_URI"],
    refresh_token=os.environ["AMO_REFRESH_TOKEN"],
)

CEO_TG_ID   = int(os.environ.get("CEO_TG_ID", "0"))
PM_TG_ID    = int(os.environ.get("PM_TG_ID", "0"))
PIPELINE_ID = int(os.environ.get("AMO_PIPELINE_ID", "10599910"))
STATUS_NEW  = int(os.environ.get("AMO_STATUS_NEW", "83583878"))
CLUB_URL = os.environ.get("MUGON_CLUB_URL", "https://t.me/MUGON_CLUB")
MEETUP_URL = os.environ.get("MUGON_MEETUP_URL", CLUB_URL)
MEETUP_AT = os.environ.get("MUGON_MEETUP_AT", "Дата и время будут опубликованы в группе")


# FSM States
class Interview(StatesGroup):
    choosing_path      = State()
    waiting_contact    = State()
    phone_verified     = State()
    interviewing       = State()
    waiting_resume_file = State()
    completed          = State()


# Keyboards
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ACTION_CLUB), KeyboardButton(text=ACTION_MEETUP)],
            [KeyboardButton(text=ACTION_PROFILE)],
            [KeyboardButton(text="FAQ"), KeyboardButton(text="Связаться с HR")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def share_contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Поделиться номером телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def projects_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="AutoHire - AI рекрутинг",  callback_data="project_autohire")],
        [InlineKeyboardButton(text="DataFlow - ETL платформа", callback_data="project_dataflow")],
        [InlineKeyboardButton(text="Другие проекты",            callback_data="project_other")],
        [InlineKeyboardButton(text="Хочу участвовать!",         callback_data="project_apply")],
    ])


# /start
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    name = message.from_user.first_name or "коллега"
    await message.answer(
        START_TEXT.format(name=name),
        reply_markup=main_menu(),
    )
    await state.set_state(Interview.choosing_path)


@router.message(F.text == ACTION_CLUB)
async def show_club_intro(message: Message):
    await message.answer(CLUB_INFO_TEXT.format(club_url=CLUB_URL), disable_web_page_preview=True)


@router.message(F.text == ACTION_MEETUP)
async def show_meetup(message: Message):
    await message.answer(
        MEETUP_TEXT.format(meetup_at=MEETUP_AT, meetup_url=MEETUP_URL),
        disable_web_page_preview=True,
    )


@router.message(Interview.choosing_path, F.text == ACTION_PROFILE)
async def begin_project_profile(message: Message, state: FSMContext):
    await state.set_state(Interview.waiting_contact)
    await message.answer(PROFILE_CONSENT_TEXT, reply_markup=share_contact_kb())


# Contact / Phone Verification
@router.message(Interview.waiting_contact, F.contact)
async def got_contact(message: Message, state: FSMContext, bot: Bot):
    contact = message.contact
    phone   = contact.phone_number
    user    = message.from_user

    await state.update_data(
        phone=phone,
        tg_id=user.id,
        username=user.username or "",
        full_name=f"{user.first_name or ''} {user.last_name or ''}".strip(),
        last_activity=time.time(),   # FIX 2b: record activity time
    )
    await state.set_state(Interview.phone_verified)

    lead_id = await amo.find_or_create_lead(
        name=f"{user.first_name or 'Кандидат'} @{user.username or user.id}",
        phone=phone,
        tg_id=str(user.id),
        pipeline_id=PIPELINE_ID,
        status_id=STATUS_NEW,
    )
    await state.update_data(lead_id=lead_id)

    await message.answer(
        f"Номер {phone} подтверждён!\n\n"
        "Теперь соберём короткий профиль для проектного подбора: роль, стек, один "
        "реальный кейс, интересы и доступность. Обычно это 6–8 вопросов.\n"
        "Отвечайте свободно — это не экзамен. Оценки по самоописанию не считаются "
        "подтверждением навыков; доказательства проверяются отдельно и только с вашего согласия.\n\n"
        "Начнём?",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Interview.interviewing)
    await state.update_data(history=[], questions_asked=0, scores={}, finalized=False)

    first_q = await ask_hr_gpt([], "START_INTERVIEW", user_name=user.first_name)
    await message.answer(first_q)


@router.message(Interview.waiting_contact)
async def request_contact_again(message: Message):
    await message.answer(
        "Для продолжения необходимо поделиться номером телефона.\n"
        "Нажмите кнопку ниже",
        reply_markup=share_contact_kb(),
    )


# Main Interview Flow
@router.message(Interview.interviewing, F.text)
async def interview_message(message: Message, state: FSMContext, bot: Bot):
    data            = await state.get_data()
    history         = data.get("history", [])
    questions_asked = data.get("questions_asked", 0)
    lead_id         = data.get("lead_id")
    user_name       = message.from_user.first_name

    # FIX 2b: update last_activity so scheduler knows candidate is active
    await state.update_data(last_activity=time.time())

    history.append({"role": "user", "content": message.text})

    # Keep initial project profiling short. Evidence verification is separate.
    if questions_asked >= 8:
        await finalize_interview(message, state, bot, history, lead_id, user_name)
        return

    gpt_reply = await ask_hr_gpt(history, "CONTINUE", user_name=user_name)
    visible_reply, interview_complete = split_completion_signal(gpt_reply)
    history.append({"role": "assistant", "content": gpt_reply})
    await state.update_data(history=history, questions_asked=questions_asked + 1)
    if visible_reply:
        await message.answer(visible_reply)

    # FIX 2c: only trigger finalize on GPT signal, not on >=28 (caused double call)
    if interview_complete:
        await finalize_interview(message, state, bot, history, lead_id, user_name)


@router.message(Interview.interviewing, F.document | F.photo)
async def interview_resume_file(message: Message, state: FSMContext, bot: Bot):
    """Handle resume file upload during interview."""
    data    = await state.get_data()
    lead_id = data.get("lead_id")

    # FIX 2b: update activity time
    await state.update_data(last_activity=time.time())

    if message.document:
        file_id   = message.document.file_id
        file_name = message.document.file_name or "resume.pdf"
    elif message.photo:
        file_id   = message.photo[-1].file_id
        file_name = "resume_photo.jpg"
    else:
        return

    file       = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)

    if lead_id:
        await amo.upload_resume_file(lead_id, file_bytes, file_name)

    await message.answer(
        "Резюме получено и сохранено в вашей карточке!\n"
        "Я его изучу и задам только недостающие вопросы. Продолжаем"
    )

    history = data.get("history", [])
    history.append({"role": "user", "content": f"[Кандидат прислал резюме: {file_name}]"})
    gpt_reply = await ask_hr_gpt(history, "RESUME_RECEIVED", user_name=message.from_user.first_name)
    history.append({"role": "assistant", "content": gpt_reply})
    await state.update_data(history=history)
    await message.answer(gpt_reply)


async def finalize_interview(
    message: Message, state: FSMContext, bot: Bot,
    history: list, lead_id: int, user_name: str
):
    """Finalize interview: generate AI resume, update AmoCRM, notify CEO/PM."""
    data = await state.get_data()

    # FIX 2d: idempotent guard - prevent double execution
    if data.get("finalized"):
        logger.warning(f"finalize_interview called twice for lead {lead_id} - skipping")
        return
    await state.update_data(finalized=True)

    await message.answer(
        "Основная часть профиля готова. Сейчас аккуратно сохраню ответы для команды."
    )

    ai_resume = await generate_ai_resume(history, user_name)

    if lead_id:
        await amo.update_lead_fields(lead_id, ai_resume)

    await notify_ceo_pm(bot, CEO_TG_ID, PM_TG_ID, data, ai_resume)
    await state.set_state(Interview.completed)

    await message.answer(
        "Профиль сохранён. Это ещё не техническая оценка и не решение по проекту.\n\n"
        "Когда появится подходящая задача, команда сверит требования с вашим опытом "
        "и отдельно предложит следующий шаг. Вы сможете согласиться или отказаться.\n\n"
        "А пока можно продолжать участвовать в клубе и встречах.",
        reply_markup=main_menu(),
    )


# Project Info
@router.callback_query(F.data.startswith("project_"))
async def project_info(callback: CallbackQuery):
    projects = {
        "project_autohire": (
            "*AutoHire - AI Recruitment Platform*\n\n"
            "Автоматизация найма через AI-агентов: от идеи до готового специалиста.\n"
            "Стек: Python, FastAPI, Telegram, GPT-4, AmoCRM.\n"
            "Ищем: Python разработчиков, AI-инженеров, PM."
        ),
        "project_dataflow": (
            "*DataFlow - ETL & Analytics Platform*\n\n"
            "Система агрегации и обработки данных с дашбордами.\n"
            "Стек: Python, Celery, PostgreSQL, Redis, React.\n"
            "Ищем: Backend разработчиков, Data Engineers."
        ),
        "project_other": (
            "*Другие проекты MUGON.CLUB*\n\n"
            "У нас есть ряд активных проектов в области AI, автоматизации и продуктовой разработки.\n"
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


# Menu Buttons
@router.message(F.text == ACTION_PROFILE)
async def start_interview_btn(message: Message, state: FSMContext):
    await begin_project_profile(message, state)


@router.message(F.text == "Наши проекты")
async def show_projects(message: Message):
    await message.answer("Выберите проект для подробной информации:", reply_markup=projects_inline())


@router.message(F.text == "FAQ")
async def faq(message: Message):
    await message.answer(
        "*Часто задаваемые вопросы*\n\n"
        "*Что такое MUGON.CLUB?*\n"
        "Сообщество разработчиков для практических встреч, взаимопомощи и подходящих проектов.\n\n"
        "*Какой формат работы?*\n"
        "Full-time, part-time или проектная занятость. Удалённо.\n\n"
        "*Нужно ли платить за участие?*\n"
        "Нет. Мы платим за результат.\n\n"
        "*Нужно ли сразу заполнять профиль?*\n"
        "Нет. Можно читать группу и приходить на встречи без резюме и телефона.\n\n"
        "*Как попасть в проект?*\n"
        "Добровольно заполнить короткий профиль, затем подтвердить релевантный опыт "
        "по материалам или на технической встрече.",
        parse_mode="Markdown",
    )


@router.message(F.text == "Отправить резюме")
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


@router.message(F.text == "Связаться с HR")
async def contact_hr(message: Message):
    await message.answer(
        "Вы можете связаться с HR напрямую:\n"
        "hr@mugon.club\n"
        "Или напишите свой вопрос, и я передам его команде."
    )


@router.message(Interview.choosing_path)
async def explain_onboarding_choices(message: Message):
    await message.answer(
        "Выберите один из вариантов на клавиатуре. Можно просто узнать о клубе — "
        "личные данные для этого не нужны.",
        reply_markup=main_menu(),
    )


# Group: New member greeting
@router.message(F.new_chat_members)
async def new_member(message: Message):
    for member in message.new_chat_members:
        if not member.is_bot:
            await message.answer(
                f"Привет, {member.first_name}! Добро пожаловать в MUGON.CLUB!\n\n"
                "Можно просто читать, прийти на встречу слушателем или коротко "
                "рассказать, чем занимаетесь. Резюме и показ кода для участия не нужны."
            )


# Pause handler
@router.message(Interview.interviewing, F.text.in_({"стоп", "пауза", "позже", "потом"}))
async def pause_interview(message: Message, state: FSMContext):
    await message.answer(
        "Хорошо, сохраняю ваш прогресс. Напишите мне когда будете готовы продолжить.\n"
        "Я напомню о себе через 24 часа"
    )


# Completed state handler
@router.message(Interview.completed)
async def completed_state(message: Message):
    await message.answer(
        "Ваш проектный профиль уже сохранён. Пока можно участвовать в обсуждениях "
        "и встречах; если появится подходящая задача, команда напишет отдельно.",
        reply_markup=main_menu(),
    )
