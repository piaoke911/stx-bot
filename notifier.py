# ===================== notifier.py =====================
"""
notifier.py
============
告警通知模块：Telegram + SMTP 邮件。

设计原则：
1. 使用标准库（urllib / smtplib / email），零第三方依赖。
2. 双通道可独立启用，全部未配置时自动降级为 no-op（仅写日志）。
3. 所有网络异常均在内部捕获，绝不让告警失败影响交易主流程。
4. 内置速率限制，防止主循环异常时的告警风暴。
5. 与 trading 主流程完全解耦：仅依赖 config 中的开关与自身环境变量。

环境变量：
    NOTIFY_ENABLED       总开关（true/false，默认 true）
    NOTIFY_ON_STARTUP    是否发送启动通知（true/false，默认 true，由 main.py 读取）
    NOTIFY_RATE_LIMIT    同一消息在 N 秒内最多发送一次（默认 30，0 = 关闭）
    TELEGRAM_BOT_TOKEN   Telegram Bot Token（@BotFather 获取）
    TELEGRAM_CHAT_ID     Telegram Chat ID
    TELEGRAM_SILENT      是否静默发送（true/false，默认 false）
    SMTP_HOST            邮件服务器
    SMTP_PORT            邮件端口（465 = SSL；587 / 25 = STARTTLS）
    SMTP_USER            发件邮箱
    SMTP_PASSWORD        邮箱密码或应用专用密码
    SMTP_TO              收件邮箱（多个以逗号分隔）
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Final, Literal, Optional

logger = logging.getLogger("stx_quant.notifier")

# 告警等级类型别名（供 IDE 与静态检查使用）
NotifyLevel = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]

# 各等级对应的 emoji 前缀（Telegram 使用）
_LEVEL_EMOJI: Final[dict[str, str]] = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "🚨",
}

_VALID_LEVELS: Final[frozenset[str]] = frozenset(
    {"INFO", "WARNING", "ERROR", "CRITICAL"}
)


def _env_bool(name: str, default: bool = False) -> bool:
    """
    解析布尔类型环境变量。
    接受 true / 1 / yes / on（大小写不敏感）视为 True，其余为 False。
    未设置时返回 default。
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


def _html_escape(text: str) -> str:
    """
    转义 Telegram HTML 模式下的保留字符。
    Telegram 要求 '<'、'>'、'&' 必须转义，否则 API 会返回 400 Bad Request。
    先转义 '&' 避免二次转义。
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class Notifier:
    """
    多通道告警器：Telegram + 邮件，均可独立启用。

    使用方式：
        notifier = Notifier()
        notifier.send("开仓成功", level="INFO")
        notifier.send("熔断触发", level="CRITICAL")

    特性：
    - 全部渠道未配置 → 自动降级为 no-op，仅写日志
    - 所有异常内部捕获，绝不影响交易主流程
    - 内置速率限制，防止重复消息造成告警风暴
    - is_ready() / active_channels() 可用于运行时探测
    """

    # Telegram Bot API 端点（集中管理，便于测试时替换）
    _TELEGRAM_API: Final[str] = "https://api.telegram.org"

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        smtp_host: Optional[str] = None,
        smtp_port: int = 465,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        smtp_to: Optional[str] = None,
        enabled: bool = True,
        rate_limit_sec: Optional[int] = None,
    ) -> None:
        """
        初始化告警器。

        所有参数均可省略，此时从环境变量读取（推荐方式）。
        显式参数优先级高于环境变量，便于单元测试。

        参数：
            telegram_token:    Telegram Bot Token
            telegram_chat_id:  Telegram 目标 Chat ID
            smtp_host:         SMTP 服务器地址
            smtp_port:         SMTP 端口（465 = SSL；587 / 25 = STARTTLS）
            smtp_user:         发件邮箱
            smtp_password:     邮箱密码或应用专用密码
            smtp_to:           收件邮箱（多个以逗号分隔）
            enabled:           是否启用告警（与 NOTIFY_ENABLED 取 AND）
            rate_limit_sec:    速率限制窗口（秒），None = 从环境变量读取
        """
        # ---- 总开关：显式 enabled 与 NOTIFY_ENABLED 取 AND ----
        self.enabled: bool = enabled and _env_bool("NOTIFY_ENABLED", True)

        # ---- Telegram ----
        self.telegram_token: str = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id: str = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.telegram_silent: bool = _env_bool("TELEGRAM_SILENT", False)

        # ---- SMTP ----
        self.smtp_host: str = smtp_host or os.getenv("SMTP_HOST", "")
        self.smtp_port: int = int(os.getenv("SMTP_PORT", str(smtp_port)))
        self.smtp_user: str = smtp_user or os.getenv("SMTP_USER", "")
        self.smtp_password: str = smtp_password or os.getenv("SMTP_PASSWORD", "")
        self.smtp_to: str = smtp_to or os.getenv("SMTP_TO", "")

        # ---- 速率限制 ----
        if rate_limit_sec is None:
            rate_limit_sec = int(os.getenv("NOTIFY_RATE_LIMIT", "30"))
        self.rate_limit_sec: int = max(int(rate_limit_sec), 0)
        # 上次发送时间记录：{(level, message_hash): monotonic_timestamp}
        self._last_sent: dict[tuple[str, int], float] = {}

        # ---- 渠道就绪状态 ----
        self._telegram_ready: bool = bool(self.telegram_token and self.telegram_chat_id)
        self._smtp_ready: bool = bool(
            self.smtp_host and self.smtp_user and self.smtp_password and self.smtp_to
        )

        # ---- 启动日志（清晰告知运维当前能力）----
        if not self.enabled:
            logger.info("Notifier 已禁用（NOTIFY_ENABLED=false 或 enabled=False）")
        elif not (self._telegram_ready or self._smtp_ready):
            logger.info("Notifier 未配置任何渠道，将仅记录到日志")
        else:
            channels: list[str] = []
            if self._telegram_ready:
                channels.append("Telegram")
            if self._smtp_ready:
                channels.append(f"Email({self.smtp_host}:{self.smtp_port})")
            logger.info(
                "Notifier 初始化完成 | 活跃渠道=%s | 速率限制=%ds",
                "+".join(channels), self.rate_limit_sec,
            )

    # ---------------------- 状态查询接口 ----------------------

    def is_ready(self) -> bool:
        """返回是否至少有一个可用渠道（且总开关为开）。"""
        return self.enabled and (self._telegram_ready or self._smtp_ready)

    def active_channels(self) -> list[str]:
        """返回当前活跃的渠道名称列表，便于运维排查。"""
        channels: list[str] = []
        if self.enabled and self._telegram_ready:
            channels.append("telegram")
        if self.enabled and self._smtp_ready:
            channels.append("email")
        return channels

    def __repr__(self) -> str:
        return (
            f"Notifier(enabled={self.enabled}, "
            f"telegram={self._telegram_ready}, "
            f"smtp={self._smtp_ready}, "
            f"rate_limit={self.rate_limit_sec}s)"
        )

    # ---------------------- 对外发送接口 ----------------------

    def send(self, message: str, level: str = "INFO") -> None:
        """
        发送通知到所有已配置渠道。

        参数：
            message: 消息正文
            level:   告警等级，推荐 INFO / WARNING / ERROR / CRITICAL
                     （非法值会自动降级为 INFO，不抛异常）

        保证：
            - 无论渠道是否可用，都会写一条日志（可被文件日志捕获）
            - 任何网络异常均被内部捕获，不会传播到调用方
            - 相同 (level, message) 在 rate_limit_sec 秒内只发送一次
        """
        # 规范化 level（非法值降级为 INFO，保证接口健壮）
        normalized_level = (level or "INFO").upper()
        if normalized_level not in _VALID_LEVELS:
            logger.debug("未知告警等级 %r，按 INFO 处理", level)
            normalized_level = "INFO"

        # 无论渠道是否可用，都写一条日志（可被文件日志捕获）
        log_fn = {
            "INFO": logger.info,
            "WARNING": logger.warning,
            "ERROR": logger.error,
            "CRITICAL": logger.critical,
        }[normalized_level]
        log_fn("[NOTIFY] %s", message)

        # 未启用 → 到此为止
        if not self.enabled:
            return

        # 速率限制：相同 (level, message) 在窗口内只发一次
        if self._is_rate_limited(normalized_level, message):
            logger.debug("命中速率限制，跳过发送：level=%s", normalized_level)
            return

        # 分发到各渠道
        if self._telegram_ready:
            self._send_telegram(message, normalized_level)
        if self._smtp_ready:
            self._send_email(message, normalized_level)

    # ---------------------- 速率限制 ----------------------

    def _is_rate_limited(self, level: str, message: str) -> bool:
        """
        速率限制判定：相同 (level, message) 在 rate_limit_sec 秒内返回 True。
        - rate_limit_sec <= 0 时永不限制。
        - 定期清理过期条目，避免内存无限增长。
        """
        if self.rate_limit_sec <= 0:
            return False

        now = time.monotonic()
        key = (level, hash(message))
        last = self._last_sent.get(key)

        # 定期清理（每超过 100 条时清理一次）
        if len(self._last_sent) > 100:
            cutoff = now - self.rate_limit_sec
            self._last_sent = {
                k: v for k, v in self._last_sent.items() if v > cutoff
            }

        if last is not None and (now - last) < self.rate_limit_sec:
            return True

        self._last_sent[key] = now
        return False

    # ---------------------- Telegram 实现 ----------------------

    def _send_telegram(self, message: str, level: str) -> None:
        """通过 Telegram Bot API 发送文本消息（HTML 模式）。"""
        emoji = _LEVEL_EMOJI.get(level, "ℹ️")
        safe_message = _html_escape(message)
        text = f"{emoji} <b>STX Bot</b>\n{safe_message}"

        url = f"{self._TELEGRAM_API}/bot{self.telegram_token}/sendMessage"
        payload: dict[str, str] = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": "true" if self.telegram_silent else "false",
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning("Telegram 通知返回非 200: %s", resp.status)
                else:
                    logger.debug("Telegram 通知发送成功")
        except urllib.error.HTTPError as exc:
            # Telegram 返回的具体错误体（如 400 参数错误、403 Bot 被拉黑）
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            logger.warning("Telegram HTTP 错误 %s: %s", exc.code, body[:200])
        except urllib.error.URLError as exc:
            logger.warning("Telegram 网络错误: %s", exc.reason)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram 通知发送失败: %s", exc)

    # ---------------------- Email 实现 ----------------------

    def _send_email(self, message: str, level: str) -> None:
        """通过 SMTP 发送纯文本邮件，支持 465（SSL）与 587 / 25（STARTTLS）。"""
        try:
            msg = MIMEText(message, "plain", "utf-8")
            msg["Subject"] = f"[STX Bot][{level}] 交易系统通知"
            msg["From"] = self.smtp_user
            msg["To"] = self.smtp_to
            msg["Date"] = formatdate(localtime=True)

            recipients = [x.strip() for x in self.smtp_to.split(",") if x.strip()]
            if not recipients:
                logger.warning("邮件收件人列表为空，跳过发送")
                return

            if self.smtp_port == 465:
                # SMTPS（隐式 SSL）
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(
                    self.smtp_host, self.smtp_port,
                    context=context, timeout=15,
                ) as server:
                    server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_user, recipients, msg.as_string())
            else:
                # 明文连接 + STARTTLS（587 / 25）
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                    server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_user, recipients, msg.as_string())

            logger.debug("邮件通知发送成功 → %s", recipients)
        except smtplib.SMTPAuthenticationError as exc:
            logger.warning(
                "邮件认证失败（请检查 SMTP_USER / SMTP_PASSWORD）: %s", exc
            )
        except smtplib.SMTPException as exc:
            logger.warning("邮件发送 SMTP 异常: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("邮件通知发送失败: %s", exc)
