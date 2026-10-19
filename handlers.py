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
    ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from gpt import ask_hr_gpt, generate_ai_resume
from amocrm import AmoCRM
from notifier import notify_ceo_pm
from onboarding_copy import (
    ACTION_CLUB, ACTION_MEETUP, ACTION_PROFILE, CLUB_INFO_TEXT, MEETUP_TEXT,
    PROFILE_CONSENT_TEXT, START_TEXT, split_completion_signal,
)

logger = logging.getLogger(__name__)
router = Router()

amo = AmoCRM(
    domain=os.environ.get("KOMMO_DOMAIN") or os.environ.get("AMO_DOMAIN", "adminwowericom.kommo.com"),
    client_id=os.environ.get("AMO_CLIENT_ID", ""),
    client_secret=os.environ.get("AMO_CLIENT_SECRET", ""),
    redirect_uri=os.environ.get("AMO_REDIRECT_URI", ""),
    refresh_token=os.environ.get("AMO_REFRESH_TOKEN", ""),
    long_term_token=os.environ.get("KOMMO_TOKEN") or os.environ.get("AMO_LONG_TERM_TOKEN", ""),
)

CEO_TG_ID   = int(os.environ.get("CEO_TG_ID", "0"))
PM_TG_ID    = int(os.environ.get("PM_TG_ID", "0"))
PIPELINE_ID = int(os.environ.get("AMO_PIPELINE_ID", "14089467"))
STATUS_NEW  = int(os.environ.get("AMO_STATUS_NEW", "108772599"))


CLUB_URL = os.environ.get("MUGON_CLUB_URL", "https://t.me/MUGON_CLUB")
MEETUP_URL = os.environ.get("MUGON_MEETUP_URL") or CLUB_URL
MEETUP_AT = os.environ.get("MUGON_MEETUP_AT") or "Дата и время будут опубликованы в группе"


# FSM States
class Interview(StatesGroup):
    choosing_path      = State()
    paused             = State()
    waiting_contact    = State()
    phone_verified     = State()
    interviewing       = State()
    waiting_resume_file = State()
    completed          = State()
    finalizing         = State()


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
        keyboard=[
            [KeyboardButton(text="Поделиться номером телефона", request_contact=True)],
            [KeyboardButton(text=ACTION_CLUB), KeyboardButton(text=ACTION_MEETUP)],
            [KeyboardButton(text="стоп")],
        ],
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


@router.message(F.text == ACTION_PROFILE)
async def begin_project_profile(message: Message, state: FSMContext, bot: Bot):
    current_state = await state.get_state()
    if current_state == Interview.finalizing.state:
        await retry_finalization(message, state, bot)
        return
    if current_state == Interview.completed.state:
        await completed_state(message)
        return
    if current_state == Interview.paused.state and (await state.get_data()).get("finalization_pending"):
        await retry_finalization(message, state, bot)
        return
    if current_state in (Interview.interviewing.state, Interview.paused.state):
        await state.set_state(Interview.interviewing)
        await state.update_data(last_activity=time.time())
        await message.answer("Продолжим с сохранённого места. Ответьте на последний вопрос.", reply_markup=ReplyKeyboardRemove())
        return
    await state.set_state(Interview.waiting_contact)
    await message.answer(PROFILE_CONSENT_TEXT, reply_markup=share_contact_kb())


# Handle explicit pause before any state-specific catch-all or GPT handler.
@router.message(F.text.lower().in_({"стоп", "пауза", "позже", "потом", "/stop", "/cancel"}))
async def pause_interview(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state in (Interview.interviewing.state, Interview.waiting_resume_file.state, Interview.finalizing.state):
        await state.set_state(Interview.paused)
    elif current_state == Interview.waiting_contact.state:
        await state.set_state(Interview.choosing_path)
    await message.answer(
        "Хорошо. Напоминаний не будет. Сохранённый профиль можно продолжить кнопкой «Профиль для проектов».",
        reply_markup=main_menu(),
    )


# Menu Buttons
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
        "Файл будет передан команде. Опишите ключевой опыт сообщением: бот не читает содержимое вложений.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(F.text == "Связаться с HR")
async def contact_hr(message: Message):
    await message.answer(
        "Вы можете связаться с HR напрямую:\n"
        "hr@mugon.club\n"
        "Напишите на этот адрес, чтобы вопрос получил HR."
    )



# Contact / Phone Verification
@router.message(Interview.waiting_contact, F.contact)
async def got_contact(message: Message, state: FSMContext, bot: Bot):
    contact = message.contact
    user = message.from_user
    # A forwarded/shared third-party contact is not proof of ownership.
    if (
        contact is None
        or user is None
        or contact.user_id is None
        or contact.user_id != user.id
    ):
        await message.answer(
            "Поделитесь своим номером через кнопку ниже. "
            "Чужой контакт нельзя использовать для подтверждения номера.",
            reply_markup=share_contact_kb(),
        )
        return
    phone = contact.phone_number

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
    await state.update_data(history=[], questions_asked=0, scores={}, finalized=False, reminders_sent=0)

    first_q = await ask_hr_gpt([], "START_INTERVIEW", user_name=user.first_name)
    visible_reply, complete = split_completion_signal(first_q)
    history = [{"role": "assistant", "content": first_q}]
    await state.update_data(history=history, questions_asked=1)
    if visible_reply:
        await message.answer(visible_reply)
    if complete:
        await finalize_interview(message, state, bot, history, lead_id, user.first_name)


@router.message(Interview.waiting_contact)
async def request_contact_again(message: Message):
    await message.answer(
        "Номер нужен только для добровольного проектного профиля.\n"
        "Можно поделиться своим контактом кнопкой ниже или нажать «стоп».",
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

    if interview_complete:
        await finalize_interview(message, state, bot, history, lead_id, user_name)


@router.message(Interview.waiting_resume_file, F.document | F.photo)
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

    try:
        if not lead_id:
            raise RuntimeError("Resume has no CRM lead")
        file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file.file_path)
        uploaded = await amo.upload_resume_file(lead_id, file_bytes, file_name)
    except Exception:
        logger.exception("Resume attachment failed")
        uploaded = False
    if not uploaded:
        await message.answer("Не удалось сохранить файл в карточке. Попробуйте отправить его позже; ваш профиль сохранён в текущей сессии.")
        return

    await message.answer(
        "Резюме получено и сохранено в вашей карточке!\n"
        "Файл передан команде. Бот не читает его содержимое: опишите ключевой опыт сообщением."
    )

    history = data.get("history", [])
    history.append({"role": "user", "content": f"[Кандидат прислал резюме: {file_name}]"})
    if data.get("questions_asked", 0) >= 8:
        await finalize_interview(message, state, bot, history, lead_id, message.from_user.first_name)
        return
    gpt_reply = await ask_hr_gpt(history, "RESUME_RECEIVED", user_name=message.from_user.first_name)
    history.append({"role": "assistant", "content": gpt_reply})
    visible_reply, complete = split_completion_signal(gpt_reply)
    await state.update_data(history=history, questions_asked=data.get("questions_asked", 0) + 1)
    await state.set_state(Interview.interviewing)
    if visible_reply:
        await message.answer(visible_reply)
    if complete:
        await finalize_interview(message, state, bot, history, lead_id, message.from_user.first_name)


async def finalize_interview(
    message: Message, state: FSMContext, bot: Bot,
    history: list, lead_id: int, user_name: str
):
    """Finalize interview: generate AI resume, update AmoCRM, notify CEO/PM."""
    data = await state.get_data()

    # Dispatcher event isolation serializes this user's updates via existing Redis.
    if data.get("finalized"):
        return
    await state.set_state(Interview.finalizing)
    await state.update_data(history=history, finalization_pending=True)
    await message.answer("Сохраняю профиль. Если сервис временно недоступен, можно будет повторить.")
    try:
        ai_resume = data.get("generated_resume")
        if ai_resume is None:
            ai_resume = await generate_ai_resume(history, user_name)
            await state.update_data(generated_resume=ai_resume)
        if not lead_id:
            raise RuntimeError("Profile has no CRM lead")
        if not data.get("profile_saved"):
            await amo.update_lead_fields(lead_id, ai_resume)
            await state.update_data(profile_saved=True)
        result = await notify_ceo_pm(
            bot, CEO_TG_ID, PM_TG_ID, data, ai_resume,
            skip_recipients=data.get("notified_recipients", []),
        )
        await state.update_data(notified_recipients=result["delivered"])
        if result["failed"]:
            await message.answer("Профиль сохранён в CRM, но уведомление команде пока не доставлено. Напишите «повторить», чтобы попробовать ещё раз.")
            return
    except Exception:
        logger.exception("Profile finalization interrupted")
        await message.answer("Завершение профиля пока не удалось. Ответы сохранены в сессии. Напишите «повторить», чтобы продолжить без повторного интервью.")
        return
    await state.update_data(finalized=True, finalization_pending=False)
    await state.set_state(Interview.completed)

    await message.answer(
        "Профиль сохранён. Это ещё не техническая оценка и не решение по проекту.\n\n"
        "Когда появится подходящая задача, команда сверит требования с вашим опытом "
        "и отдельно предложит следующий шаг. Вы сможете согласиться или отказаться.\n\n"
        "А пока можно продолжать участвовать в клубе и встречах.",
        reply_markup=main_menu(),
    )


@router.message(Interview.finalizing, F.text.lower().in_({"повторить", "/retry"}))
async def retry_finalization(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await finalize_interview(message, state, bot, data.get("history", []), data.get("lead_id"), message.from_user.first_name)


@router.message(Interview.finalizing)
async def explain_finalization_retry(message: Message):
    await message.answer("Завершение профиля ожидает повтора. Напишите «повторить» или «стоп», чтобы оставить его на паузе.")


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


@router.message(Interview.paused)
async def paused_state(message: Message):
    await message.answer(
        "Профиль на паузе. Чтобы продолжить с сохранённого места, нажмите «Профиль для проектов».",
        reply_markup=main_menu(),
    )


# Completed state handler
@router.message(Interview.completed)
async def completed_state(message: Message):
    await message.answer(
        "Ваш проектный профиль уже сохранён. Пока можно участвовать в обсуждениях "
        "и встречах; если появится подходящая задача, команда напишет отдельно.",
        reply_markup=main_menu(),
    )
