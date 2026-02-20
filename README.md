# MUGON HR Bot

**Telegram AI-HR бот для автоматизации найма в MUGON.CLUB**

Bot: @MUGON_CLUB_BOT | Stack: Python + aiogram 3 + OpenAI GPT-4 + AmoCRM API

---

## Что делает бот

1. Верифицирует кандидатов через номер телефона (Telegram contact)
2. Проводит полное собеседование через GPT-4 (30 вопросов, 4 блока)
3. Принимает резюме файлом (PDF/DOCX) и загружает в AmoCRM
4. Заполняет все поля РЕЗЮМЕ MUGON в карточке AmoCRM
5. Уведомляет CEO и PM с форматированным отчётом
6. Работает в группах: приветствует новых участников
7. Предотвращает спам через throttling middleware
8. Напоминает неотвечающим кандидатам (scheduler)

---

## Структура файлов

```
bot.py          — точка входа, aiogram Dispatcher, Redis storage
handlers.py     — FSM логика: verify -> interview -> finalize
gpt.py          — OpenAI GPT-4: system prompt HR-агента, генерация AI-резюме
amocrm.py       — AmoCRM API: создание лидов, обновление полей РЕЗЮМЕ MUGON
notifier.py     — уведомления CEO/PM с форматированным отчётом
middleware.py   — ThrottlingMiddleware + VerificationMiddleware
scheduler.py    — re-engagement: напоминания неотвечающим кандидатам
requirements.txt
.env.example
```

---

## FSM States

```
waiting_contact    — ожидание номера телефона
phone_verified     — номер подтверждён, создан лид в AmoCRM
interviewing       — активное собеседование (30 вопросов через GPT-4)
waiting_resume_file — ожидание файла резюме
completed          — интервью завершено
```

---

## AmoCRM Pipeline: MUGON.CLUB - Кандидаты (ID: 10599910)

| Этап | Status ID |
|---|---|
| Неразобранное | 83583874 |
| Новичок в TG | 83583878 |
| Регистрация сайт | 83583882 |
| Заполнен профиль | 83583886 |
| На тесте | 83587734 |
| Тест пройден | 83587738 |
| Активный член | 83587742 |

---

## Поля РЕЗЮМЕ MUGON в AmoCRM

| Поле | Field ID | Тип |
|---|---|---|
| MUGON: Статус интервью | 1739629 | select |
| MUGON: Итог интервью | 1739631 | select |
| MUGON: Формат занятости | 1739633 | select |
| MUGON: Часы в день | 1739635 | numeric |
| MUGON: Основной стек | 1739637 | multiselect |
| MUGON: 3 проекта за 12 мес | 1739639 | textarea |
| MUGON: Самый сложный проект | 1739641 | textarea |
| MUGON: Почему выбран стек | 1739643 | textarea |
| MUGON: Архитектурный подход | 1739645 | textarea |
| MUGON: Кейсы TG/OpenAI API | 1739655 | textarea |
| MUGON: Инструменты мониторинга | 1739667 | textarea |
| MUGON: Security-практика | 1739669 | textarea |
| MUGON: Оценка Engineering | 1739673 | numeric 1-10 |
| MUGON: Оценка AI/Automation | 1739675 | numeric 1-10 |
| MUGON: Оценка Architecture | 1739677 | numeric 1-10 |
| MUGON: Оценка Delivery | 1739679 | numeric 1-10 |
| MUGON: Оценка Communication | 1739681 | numeric 1-10 |
| MUGON: Итоговый score 0-100 | 1739683 | numeric |
| MUGON: Риски кандидата | 1739685 | multiselect |
| MUGON: Следующий шаг | 1739687 | select |
| MUGON: AI РЕЗЮМЕ авто | 1739691 | textarea |

---

## Важно: Существующий Webhook

Бот @MUGON_CLUB_BOT имеет активный webhook от AmoCRM:
`https://amojo.amocrm.ru/~external/hooks/telegram`

При запуске нашего бота через `polling` конфликта нет.
Если хочешь переключить на наш webhook — удали старый через:
`deleteWebhook` Telegram API.

---

## Установка и запуск

```bash
# 1. Клонировать репозиторий
git clone https://github.com/arberika/mugon-hr-bot
cd mugon-hr-bot

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Создать .env файл
cp .env.example .env
# Заполнить все переменные в .env

# 4. Запустить Redis
docker run -d -p 6379:6379 redis

# 5. Запустить бота
python bot.py
```

## AmoCRM OAuth2

1. Перейти в AmoCRM -> Настройки -> Интеграции -> Создать
2. Получить client_id, client_secret
3. Пройти Authorization Code flow для получения refresh_token
4. Прописать в .env

---

## Безопасность

- Верификация телефона обязательна перед интервью
- Throttling: не более 5 сообщений в минуту
- Детали проектов не раскрываются до прохождения интервью
- Все секреты только в .env

---
MUGON.CLUB 2024-2026
