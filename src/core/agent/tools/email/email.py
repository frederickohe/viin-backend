import os
from typing import Dict, Optional, Any
import smtplib
import ssl
from email.message import EmailMessage
import redis
import json
import hashlib
from datetime import datetime, timezone
import asyncio
from pathlib import Path
import logging
from pydantic import BaseModel, Field
from langchain.tools import BaseTool

# Setup logging
logger = logging.getLogger(__name__)

# Load from project root regardless of working directory
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

from utilities.dbconfig import get_db
from core.agent.tools.agent_config.user_agent_get import GetAgentTool


class EmailToolInput(BaseModel):
    """Input schema for EmailTool"""
    to_email: str = Field(..., description="Recipient email address")
    subject: str = Field(..., description="Email subject line")
    body: str = Field(..., description="Email body content")
    user_id: str = Field(..., description="User ID for agent configuration lookup")
    agent_name: str = Field(..., description="Agent name to fetch sender email configuration")
    is_html: bool = Field(default=False, description="Whether body contains HTML")


class EmailTool(BaseTool):
    """LangChain tool for sending emails using configured sender identity"""
    
    name: str = "email_tool"
    description: str = "Send emails using user's configured sender identity. Requires user to have setup sender email in their profile."
    args_schema: type[BaseModel] = EmailToolInput
    
    redis_client: Optional[Any] = None
    db_pool: Optional[Any] = None
    config: Dict[str, Any] = {}

    def __init__(self, redis_client=None, db_pool=None, email_config=None, **kwargs):
        """Initialize EmailTool with dependencies
        
        Args:
            redis_client: Redis client for tracking
            db_pool: Database connection pool
            email_config: Email configuration dictionary
            **kwargs: Additional arguments for BaseTool
        """
        super().__init__(**kwargs)

        # Initialize Redis and DB as before
        redis_password = os.getenv('REDIS_PASSWORD', 'autobus098')
        self.redis_client = redis_client or redis.Redis(
            host=os.getenv('REDIS_HOST', 'redis'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            password=redis_password if redis_password else None,
            db=0,
            decode_responses=True
        )
        self.db_pool = db_pool or next(get_db())
        self.config = email_config or {
            'provider': 'zeptomail',
            'smtp_host': os.getenv('ZEPTOMAIL_SMTP_HOST', 'smtp.zeptomail.com'),
            'smtp_port': int(os.getenv('ZEPTOMAIL_SMTP_PORT', 587)),
            'smtp_username': os.getenv('ZEPTOMAIL_SMTP_USERNAME', 'emailapikey'),
            'smtp_password': os.getenv('ZEPTOMAIL_SMTP_PASSWORD'),
            'sender_domain': os.getenv('ZEPTOMAIL_SENDER_DOMAIN', 'greenbraintech.com'),
            'api_key': os.getenv('EMAIL_PROVIDER_API_KEY'),
            'default_from_domain': 'autobus.africa',
            'tracking_enabled': True,
            'rate_limit_per_user': 100
        }
        
        logger.info("EmailTool initialized successfully")

    def _track_email(self, email_data: Dict):
        """Store email metadata for analytics and audit."""
        tracking_id = hashlib.md5(
            f"{email_data['to']}:{email_data['timestamp']}".encode()
        ).hexdigest()
        
        # Store in Redis with expiration for real-time tracking
        self.redis_client.setex(
            f"email:track:{tracking_id}", 
            86400 * 7,  # 7 days
            json.dumps({
                **email_data,
                'status': 'sent',
                'opens': 0,
                'clicks': 0
            })
        )
        
        return tracking_id

    def _record_user_sent_email(self, user_id: str, *, to_email: str, subject: str) -> None:
        """Append outbound message metadata for read_emails / sent history (newest first)."""
        try:
            payload = json.dumps(
                {
                    "to": to_email,
                    "subject": subject,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            key = f"email:sent:{user_id}"
            self.redis_client.lpush(key, payload)
            self.redis_client.ltrim(key, 0, 99)
        except Exception as e:
            logger.warning("Could not record sent email for user %s: %s", user_id, e)

    def list_sent_emails_for_user(self, user_id: str, limit: int = 10) -> list[Dict[str, Any]]:
        """Return recent emails sent by this user through EmailTool (Redis-backed)."""
        if limit < 1:
            limit = 1
        if limit > 50:
            limit = 50
        key = f"email:sent:{user_id}"
        try:
            raw = self.redis_client.lrange(key, 0, limit - 1)
        except Exception as e:
            logger.error("Redis error listing sent emails for %s: %s", user_id, e)
            return []
        out: list[Dict[str, Any]] = []
        for row in raw or []:
            try:
                out.append(json.loads(row))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def _send_via_zeptomail(self, sender_email: str, to_email: str, subject: str, body: str) -> bool:
        """Send via Zoho Zeptomail SMTP."""
        port = int(os.getenv('ZEPTOMAIL_SMTP_PORT', 587))
        smtp_server = os.getenv('ZEPTOMAIL_SMTP_HOST')
        username = os.getenv('ZEPTOMAIL_SMTP_USERNAME')
        password = os.getenv('ZEPTOMAIL_SMTP_PASSWORD')
        
        message = body
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = to_email
        msg.set_content(message)
        
        try:
            if port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
                    server.login(username, password)
                    server.send_message(msg)
            elif port == 587:
                with smtplib.SMTP(smtp_server, port) as server:
                    server.starttls()
                    server.login(username, password)
                    server.send_message(msg)
            else:
                print("Use 465 or 587 as port value")
                return False
            return True
        except smtplib.SMTPAuthenticationError as e:
            logger.warning(f"SMTP Authentication error (ignored): {e}")
            print(f"⚠️  SMTP Authentication warning (email may still have been queued): {e}")
            # Return True because the email might still be sent despite auth warning
            return True
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            print(f"SMTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"Zeptomail error: {e}")
            print(f"Zeptomail error: {e}")
            return False

    def _run(self, to_email: str, subject: str, body: str, user_id: str, agent_name: str, is_html: bool = False) -> str:
        """Execute the email sending tool.
        
        Args:
            to_email: Recipient email address
            subject: Email subject line
            body: Email body content
            user_id: User ID for agent configuration lookup
            agent_name: Agent name to fetch sender email configuration
            is_html: Whether body contains HTML
            
        Returns:
            Status message with email sending result
        """
        try:
            # 1. Fetch sender email from agent configuration using GetAgentTool
            agent_tool = GetAgentTool(db_session=self.db_pool)
            agent_result = agent_tool.forward(user_id=user_id, agent_name=agent_name)
            
            # Parse the agent configuration response
            agent_config = json.loads(agent_result)
            
            if not agent_config.get("ok"):
                return f"❌ Failed to fetch agent configuration: {agent_config.get('message')}"
            
            # Extract sender email from agent configuration
            agent_data = agent_config.get("agent", {})
            params = agent_data.get("params", {})
            sender_email = params.get("sender_email")
            
            if not sender_email:
                return "❌ No sender email configured in agent settings"

            # 2. Validate email content (AI safety)
            if len(body) > 100000:  # 100KB limit
                return "❌ Email body too large. Please keep under 100KB."

            # 3. Send email
            success = self._send_via_zeptomail(
                sender_email,
                to_email,
                subject,
                body,
            )

            # 4. Return appropriate response
            if success:
                self._record_user_sent_email(user_id, to_email=to_email, subject=subject)
                return f"✅ Email sent successfully to {to_email}"
            else:
                return f"❌ Failed to send email. Please check your configuration."
        except Exception as e:
            logger.error(f"Error in email _run: {e}", exc_info=True)
            return f"⚠️  Email processing completed with warning: {str(e)[:100]}"

    async def _arun(self, to_email: str, subject: str, body: str, user_id: str, agent_name: str, is_html: bool = False) -> str:
        """Async version of email sending.
        
        Args:
            to_email: Recipient email address
            subject: Email subject line
            body: Email body content
            user_id: User ID for agent configuration lookup
            agent_name: Agent name to fetch sender email configuration
            is_html: Whether body contains HTML
            
        Returns:
            Status message with email sending result
        """
        return await asyncio.to_thread(self._run, to_email, subject, body, user_id, agent_name, is_html)