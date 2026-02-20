"""
amocrm.py - AmoCRM API integration for MUGON HR Bot
Handles: lead creation, field updates, file uploads, contact management

FIXES:
  - Token expiry: refresh every 23h (tokens live 24h)
  - tech_stack: real stack from GPT, not hardcoded Python
  - upload_resume_file: correct /drives/files endpoint
"""
import os
import time
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

# AmoCRM field IDs for RESUME MUGON group
FIELD_IDS = {
    "status":               1739629,
    "verdict":              1739631,
    "employment_format":    1739633,
    "hours_per_day":        1739635,
    "tech_stack":           1739637,
    "projects_12m":         1739639,
    "hard_project":         1739641,
    "stack_rationale":      1739643,
    "architecture":         1739645,
    "tg_openai_cases":      1739655,
    "monitoring":           1739667,
    "security_practice":    1739669,
    "engineering_score":    1739673,
    "ai_automation_score":  1739675,
    "architecture_score":   1739677,
    "delivery_score":       1739679,
    "communication_score":  1739681,
    "total_score":          1739683,
    "risks":                1739685,
    "next_step":            1739687,
    "ai_summary":           1739691,
}

PIPELINE_ID = 10599910
STATUS_NEW     = 83583878
STATUS_PROFILE = 83583886
STATUS_TEST    = 83587734

ENUMS = {
    "status": {
        "В процессе":    50054429,
        "Отклонён":      50054431,
        "Перспективный": 50054433,
        "Trial Task":    50054437,
        "Активный":      50054439,
    },
    "verdict": {
        "Trial Task": 50054441,
        "Отказать":   50054443,
        "На паузе":   50054445,
    },
    "employment_format": {
        "Full-time": 50054447,
        "Part-time": 50054449,
        "Проект":    50054451,
    },
    "tech_stack": {
        "Python":     50054453,
        "JavaScript": 50054455,
        "TypeScript": 50054457,
        "Go":         50054459,
        "Rust":       50054461,
        "Java":       50054463,
        "C++":        50054465,
        "React":      50054467,
        "FastAPI":    50054469,
        "Node.js":    50054471,
    },
    "risks": {
        "Нет production кейсов": 50054473,
        "Завышенная самооценка": 50054475,
        "Нет AI опыта":          50054477,
        "Неполный стек":         50054479,
        "Нет":                   50054481,
    },
    "next_step": {
        "Тестовое задание": 50054487,
        "Отказать":         50054489,
        "На паузе":         50054491,
        "Активный член":    50054493,
    },
}

# FIX 1: AmoCRM tokens live 24h — refresh every 23h to avoid silent 401 errors
TOKEN_TTL = 23 * 3600


class AmoCRM:
    """AmoCRM API client with automatic OAuth2 token refresh."""

    def __init__(self, domain: str, client_id: str, client_secret: str,
                 redirect_uri: str, refresh_token: str):
        self.domain = domain
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.refresh_token = refresh_token
        self.access_token: Optional[str] = None
        self._token_obtained_at: float = 0  # FIX 1: track token fetch time
        self.base_url = f"https://{domain}/api/v4"

    async def _get_token(self) -> str:
        """Get or refresh access token. Auto-refreshes every 23 hours."""
        # FIX 1: reuse token only if it is still fresh
        if self.access_token and (time.time() - self._token_obtained_at) < TOKEN_TTL:
            return self.access_token

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://{self.domain}/oauth2/access_token",
                json={
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type":    "refresh_token",
                    "refresh_token": self.refresh_token,
                    "redirect_uri":  self.redirect_uri,
                }
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Token refresh failed {resp.status}: {text}")
                    raise RuntimeError(f"AmoCRM token refresh failed: {resp.status}")
                data = await resp.json()
                self.access_token = data["access_token"]
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                self._token_obtained_at = time.time()  # FIX 1: record fetch time
                logger.info("AmoCRM access token refreshed successfully")
                return self.access_token

    async def _headers(self) -> dict:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def find_or_create_lead(self, name: str, phone: str, tg_id: str,
                                   pipeline_id: int, status_id: int) -> int:
        """Find existing lead by phone or create new one."""
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/contacts",
                params={"query": phone},
                headers=headers
            ) as resp:
                data = await resp.json()
                contacts = data.get("_embedded", {}).get("contacts", [])
                if contacts:
                    contact_id = contacts[0]["id"]
                    async with session.get(
                        f"{self.base_url}/contacts/{contact_id}?with=leads",
                        headers=headers
                    ) as resp2:
                        cdata = await resp2.json()
                        leads = cdata.get("_embedded", {}).get("leads", [])
                        if leads:
                            return leads[-1]["id"]

            async with session.post(
                f"{self.base_url}/contacts",
                headers=headers,
                json=[{
                    "name": name,
                    "custom_fields_values": [
                        {"field_code": "PHONE",
                         "values": [{"value": phone, "enum_code": "WORK"}]},
                    ]
                }]
            ) as resp:
                cdata = await resp.json()
                contact_id = cdata["_embedded"]["contacts"][0]["id"]

            async with session.post(
                f"{self.base_url}/leads",
                headers=headers,
                json=[{
                    "name":        name,
                    "pipeline_id": pipeline_id,
                    "status_id":   status_id,
                    "_embedded":   {"contacts": [{"id": contact_id}]},
                    "custom_fields_values": [
                        {"field_id": 1739629, "values": [{"enum_id": 50054429}]},
                    ]
                }]
            ) as resp:
                ldata = await resp.json()
                lead_id = ldata["_embedded"]["leads"][0]["id"]
                logger.info(f"Created lead {lead_id} for {name}")
                return lead_id

    async def update_lead_fields(self, lead_id: int, ai_resume: dict) -> None:
        """Update all MUGON RESUME fields in AmoCRM lead."""
        headers = await self._headers()
        custom_fields = []

        def add_text(field_key: str, value):
            if value and field_key in FIELD_IDS:
                custom_fields.append({
                    "field_id": FIELD_IDS[field_key],
                    "values": [{"value": str(value)}]
                })

        def add_select(field_key: str, value: str):
            if value and field_key in FIELD_IDS and field_key in ENUMS:
                enum_id = ENUMS[field_key].get(value)
                if enum_id:
                    custom_fields.append({
                        "field_id": FIELD_IDS[field_key],
                        "values": [{"enum_id": enum_id}]
                    })

        def add_numeric(field_key: str, value):
            if value is not None and field_key in FIELD_IDS:
                custom_fields.append({
                    "field_id": FIELD_IDS[field_key],
                    "values": [{"value": int(value)}]
                })

        def add_multiselect(field_key: str, values: list):
            if values and field_key in FIELD_IDS and field_key in ENUMS:
                enum_values = [
                    {"enum_id": ENUMS[field_key][v]}
                    for v in values if v in ENUMS[field_key]
                ]
                if enum_values:
                    custom_fields.append({
                        "field_id": FIELD_IDS[field_key],
                        "values": enum_values
                    })

        add_select("status",            ai_resume.get("status", "В процессе"))
        add_select("verdict",           ai_resume.get("verdict", "Trial Task"))
        add_select("employment_format", ai_resume.get("employment_format", "Full-time"))
        add_numeric("hours_per_day",    ai_resume.get("hours_per_day", 8))
        add_text("projects_12m",        ai_resume.get("projects_12m"))
        add_text("hard_project",        ai_resume.get("hard_project"))
        add_text("stack_rationale",     ai_resume.get("stack_rationale"))
        add_text("architecture",        ai_resume.get("architecture"))
        add_text("tg_openai_cases",     ai_resume.get("tg_openai_cases"))
        add_text("monitoring",          ai_resume.get("monitoring"))
        add_text("security_practice",   ai_resume.get("security_practice"))
        add_numeric("engineering_score",   ai_resume.get("engineering_score"))
        add_numeric("ai_automation_score", ai_resume.get("ai_automation_score"))
        add_numeric("architecture_score",  ai_resume.get("architecture_score"))
        add_numeric("delivery_score",      ai_resume.get("delivery_score"))
        add_numeric("communication_score", ai_resume.get("communication_score"))
        add_numeric("total_score",         ai_resume.get("total_score"))
        add_multiselect("risks",    ai_resume.get("risks", []))
        add_select("next_step",     ai_resume.get("next_step", "Тестовое задание"))
        add_text("ai_summary",      ai_resume.get("ai_summary"))

        # FIX 4: real tech stack from GPT, not hardcoded Python
        stack = ai_resume.get("tech_stack", [])
        if stack:
            if isinstance(stack, str):
                stack = [s.strip() for s in stack.replace(",", " ").split() if s.strip()]
            if isinstance(stack, list):
                add_multiselect("tech_stack", stack)

        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{self.base_url}/leads/{lead_id}",
                headers=headers,
                json={"custom_fields_values": custom_fields}
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    logger.error(f"AmoCRM update error {resp.status}: {text}")
                else:
                    logger.info(f"Updated lead {lead_id} with {len(custom_fields)} fields")

    # FIX 5: correct resume upload via /drives/files then attach as note
    async def upload_resume_file(self, lead_id: int, file_bytes, file_name: str) -> None:
        """Upload resume file via AmoCRM Drives API, then attach to lead note."""
        token = await self._get_token()
        auth_header = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            # Step 1: upload to AmoCRM Drives
            form = aiohttp.FormData()
            form.add_field(
                "file",
                file_bytes,
                filename=file_name,
                content_type="application/octet-stream"
            )
            async with session.post(
                f"https://{self.domain}/api/v4/drives/files",
                headers=auth_header,
                data=form
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    logger.warning(f"Resume upload to drives failed {resp.status}: {text}")
                    await self.add_note(lead_id, f"Резюме кандидата: {file_name} (авто-загрузка не удалась)")
                    return
                file_data = await resp.json()
                file_uuid = file_data.get("uuid") or file_data.get("id")

            if not file_uuid:
                logger.warning("No file UUID returned from AmoCRM drives API")
                await self.add_note(lead_id, f"Резюме: {file_name} (UUID не получен)")
                return

            # Step 2: attach uploaded file to lead as note
            async with session.post(
                f"{self.base_url}/leads/{lead_id}/notes",
                headers={**auth_header, "Content-Type": "application/json"},
                json=[{
                    "note_type": "attachment",
                    "params": {
                        "file_uuid": file_uuid,
                        "file_name": file_name
                    }
                }]
            ) as resp:
                if resp.status not in (200, 201):
                    logger.warning(f"Note attachment failed {resp.status}")
                else:
                    logger.info(f"Resume '{file_name}' attached to lead {lead_id}")

    async def add_note(self, lead_id: int, text: str) -> None:
        """Add a plain text note to a lead."""
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{self.base_url}/leads/{lead_id}/notes",
                headers=headers,
                json=[{"note_type": "common", "params": {"text": text}}]
            )

    async def move_lead_to_stage(self, lead_id: int, status_id: int) -> None:
        """Move lead to a different pipeline stage."""
        headers = await self._headers()
        async with aiohttp.ClientSession() as session:
            await session.patch(
                f"{self.base_url}/leads/{lead_id}",
                headers=headers,
                json={"status_id": status_id}
            )
            logger.info(f"Moved lead {lead_id} to status {status_id}")
