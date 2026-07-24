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

    def __init__(self, domain: str, client_id: str = "", client_secret: str = "",
                 redirect_uri: str = "", refresh_token: str = "",
                 long_term_token: str = ""):
        self.domain = domain.replace("https://", "").replace("http://", "").strip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.refresh_token = refresh_token
        self.access_token: Optional[str] = long_term_token or None
        self.long_term_token = bool(long_term_token)
        self._token_obtained_at: float = 0  # FIX 1: track token fetch time
        self.base_url = f"https://{self.domain}/api/v4"

    async def _get_token(self) -> str:
        """Get or refresh access token. Auto-refreshes every 23 hours."""
        if self.long_term_token and self.access_token:
            return self.access_token
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
                }]
            ) as resp:
                ldata = await resp.json()
                lead_id = ldata["_embedded"]["leads"][0]["id"]
                logger.info(f"Created lead {lead_id} for {name}")
                return lead_id

    async def update_lead_fields(self, lead_id: int, ai_resume: dict) -> None:
        """Preserve the complete interview result in a portable Kommo note."""
        headers = await self._headers()
        lines = ["MUGON HR — итог собеседования"]
        for key, value in ai_resume.items():
            if value not in (None, "", []):
                rendered = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                lines.append(f"{key}: {rendered}")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/leads/{lead_id}/notes",
                headers=headers,
                json=[{"note_type": "common", "params": {"text": "\n".join(lines)}}],
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    logger.error(f"Kommo note error {resp.status}: {text}")
                else:
                    logger.info(f"Stored interview result on lead {lead_id}")

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
