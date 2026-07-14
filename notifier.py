"""
notifier.py - Notification system for MUGON HR Bot
Sends formatted candidate reports to CEO and Project Manager via Telegram
"""
import logging
from aiogram import Bot

logger = logging.getLogger(__name__)


async def notify_ceo_pm(
    bot: Bot,
    ceo_id: int,
    pm_id: int,
    candidate_data: dict,
    ai_resume: dict,
) -> None:
    """Send candidate interview summary to CEO and PM."""
    name = candidate_data.get("full_name", "Неизвестно")
    phone = candidate_data.get("phone", "—")
    username = candidate_data.get("username", "")
    tg_id = candidate_data.get("tg_id", "")
    lead_id = candidate_data.get("lead_id", "")

    score = ai_resume.get("total_score")
    verdict = ai_resume.get("verdict", "—")
    next_step = ai_resume.get("next_step", "—")
    project_fit = ai_resume.get("project_fit", "—")
    summary = ai_resume.get("ai_summary", "—")
    risks = ai_resume.get("risks", [])
    eng = ai_resume.get("engineering_score", "—")
    ai_s = ai_resume.get("ai_automation_score", "—")
    arch = ai_resume.get("architecture_score", "—")
    deliv = ai_resume.get("delivery_score", "—")
    comm = ai_resume.get("communication_score", "—")

    score_emoji = "⚪"
    score_display = "не выставлен"
    if isinstance(score, (int, float)):
        score_emoji = "🟢" if score >= 75 else "🟡" if score >= 50 else "🔴"
        score_display = f"{score}/100"

    report = (
        f"🆕 *ПРОФИЛЬ НА ПРОВЕРКУ — MUGON.CLUB*\n\n"
        f"👤 *Имя:* {name}\n"
        f"📱 *Телефон:* {phone}\n"
        f"💬 *TG:* @{username} (id:{tg_id})\n"
        f"🔗 *AmoCRM:* https://eriarwork2201.amocrm.ru/leads/detail/{lead_id}\n\n"
        f"{score_emoji} *Предварительный балл: {score_display}*\n"
        f"📋 *Вердикт:* {verdict}\n"
        f"📌 *Следующий шаг:* {next_step}\n"
        f"🚀 *Проект:* {project_fit}\n\n"
        f"📊 *Оценки по блокам:*\n"
        f"  • Инжиниринг: {eng}/10\n"
        f"  • AI/Automation: {ai_s}/10\n"
        f"  • Архитектура: {arch}/10\n"
        f"  • Delivery: {deliv}/10\n"
        f"  • Коммуникация: {comm}/10\n\n"
    )

    if risks:
        report += f"⚠️ *Риски:*\n" + "\n".join(f"  • {r}" for r in risks) + "\n\n"

    report += f"🤖 *AI Резюме:*\n{summary}"

    for recipient_id in [ceo_id, pm_id]:
        if recipient_id and recipient_id > 0:
            try:
                await bot.send_message(
                    recipient_id,
                    report,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                logger.info(f"Notified {recipient_id} about candidate {name}")
            except Exception as e:
                logger.error(f"Failed to notify {recipient_id}: {e}")
