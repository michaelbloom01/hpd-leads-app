"""
Email service for agent briefings.

Sends HTML email briefings via SMTP with graceful degradation.
If SMTP is not configured, briefings are saved for download instead.
"""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# In-memory briefing storage (single-user app, no persistence needed)
_briefings: dict[str, str] = {}


def _smtp_configured() -> bool:
    """Check if SMTP is configured via environment variables."""
    return all([
        os.environ.get("SMTP_HOST"),
        os.environ.get("SMTP_PORT"),
        os.environ.get("SMTP_USER"),
        os.environ.get("SMTP_PASSWORD"),
    ])


def build_briefing_html(subject: str, leads: list[dict]) -> str:
    """Build a clean HTML email briefing from lead data."""
    rows_html = ""
    for lead in leads:
        phone_icon = "✓" if lead.get("phone") else "—"
        email_icon = "✓" if lead.get("email") else "—"
        web_icon = "✓" if lead.get("website") else "—"
        boros = ", ".join(lead.get("boros", []))

        rows_html += f"""
        <tr>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; font-weight: 500;">
                {lead.get('company_name', 'Unknown')}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: center;">
                {lead.get('portfolio_size', 0)}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: center;">
                {lead.get('total_units', 0):,}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: center;">
                {lead.get('score', 0):.1f}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: right;">
                ${lead.get('estimated_annual_revenue', 0):,.0f}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: center;">
                {phone_icon} / {email_icon} / {web_icon}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb;">
                {boros}
            </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{subject}</title></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #f9fafb; color: #111827;">
    <div style="max-width: 900px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden;">
        <div style="padding: 24px 32px; border-bottom: 1px solid #e5e7eb;">
            <h1 style="margin: 0 0 4px; font-size: 20px; font-weight: 700;">{subject}</h1>
            <p style="margin: 0; color: #6b7280; font-size: 14px;">
                {len(leads)} leads · Generated {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
            </p>
        </div>
        <div style="padding: 0;">
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                <thead>
                    <tr style="background: #f9fafb;">
                        <th style="padding: 10px 12px; text-align: left; font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase;">Company</th>
                        <th style="padding: 10px 12px; text-align: center; font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase;">Bldgs</th>
                        <th style="padding: 10px 12px; text-align: center; font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase;">Units</th>
                        <th style="padding: 10px 12px; text-align: center; font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase;">Score</th>
                        <th style="padding: 10px 12px; text-align: right; font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase;">Revenue</th>
                        <th style="padding: 10px 12px; text-align: center; font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase;">📞/✉/🌐</th>
                        <th style="padding: 10px 12px; text-align: left; font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase;">Boroughs</th>
                    </tr>
                </thead>
                <tbody>{rows_html}
                </tbody>
            </table>
        </div>
        <div style="padding: 16px 32px; background: #f9fafb; text-align: center; color: #9ca3af; font-size: 12px;">
            HPD Leads App · AI Agent Briefing
        </div>
    </div>
</body>
</html>"""
    return html


def save_briefing(html: str) -> str:
    """Save a briefing HTML and return a briefing_id for later retrieval."""
    briefing_id = str(uuid4())
    _briefings[briefing_id] = html
    return briefing_id


def get_briefing(briefing_id: str) -> Optional[str]:
    """Retrieve a saved briefing by ID."""
    return _briefings.get(briefing_id)


def send_briefing_email(subject: str, html: str, recipient: str) -> dict:
    """
    Send an HTML email briefing via SMTP.

    Returns:
        {"sent": True} on success
        {"sent": False, "error": "..."} on failure
    """
    if not _smtp_configured():
        return {"sent": False, "error": "SMTP not configured"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = os.environ.get("EMAIL_FROM", os.environ["SMTP_USER"])
        msg["To"] = recipient

        # Plain text fallback
        plain = f"{subject}\n\nThis briefing requires an HTML email client to view."
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        smtp_host = os.environ["SMTP_HOST"]
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ["SMTP_USER"]
        smtp_pass = os.environ["SMTP_PASSWORD"]

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logger.info(f"Email sent to {recipient}: {subject}")
        return {"sent": True}
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return {"sent": False, "error": str(e)}
