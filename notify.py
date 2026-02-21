#!/usr/bin/env python3
"""
AI Task Notify - Claude Code / Codex 任务完成通知脚本

支持的通知渠道:
- 邮件 (Email) - 支持HTML格式

使用方式:
1. Claude Code (Stop hook): 通过 stdin 接收 JSON
2. Codex CLI (notify): 通过命令行参数接收 JSON
"""

import json
import sys
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime
from typing import Optional, Union


def load_env(env_path: Optional[Union[str, Path]] = None) -> dict[str, str]:
    """加载 .env 文件"""
    env: dict[str, str] = {}

    if env_path is None:
        env_file = Path(__file__).parent / ".env"
    else:
        env_file = Path(env_path)

    if not env_file.exists():
        return env

    with env_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()

    return env


def get_config(env: dict[str, str], key: str, default: str = "") -> str:
    """获取配置，优先使用环境变量"""
    value = os.environ.get(key)
    if value is not None:
        return value
    return env.get(key, default)


def get_enabled_channels(env: dict[str, str]) -> list[str]:
    """获取启用的通知渠道列表"""
    channels_str = get_config(env, "NOTIFY_CHANNELS", "")
    if not channels_str:
        return []
    return [c.strip().lower() for c in channels_str.split(",") if c.strip()]


# ============ 邮件 ============

def _escape_html(text: str) -> str:
    """转义HTML特殊字符"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def extract_conversation(data: dict, source: str) -> list[dict]:
    """从原始数据中提取完整对话记录，返回 [{"role": "user"|"assistant", "text": str}]"""
    messages: list[dict] = []

    if source == "claude-code":
        for item in data.get("transcript", []):
            msg_type = item.get("type", "")
            if msg_type not in ("human", "assistant"):
                continue
            role = "user" if msg_type == "human" else "assistant"
            msg = item.get("message", {})
            texts: list[str] = []
            if isinstance(msg, dict):
                for c in msg.get("content", []):
                    if isinstance(c, dict) and c.get("type") == "text":
                        t = c.get("text", "").strip()
                        if t:
                            texts.append(t)
            elif isinstance(msg, str) and msg.strip():
                texts.append(msg.strip())
            if texts:
                messages.append({"role": role, "text": "\n\n".join(texts)})

    elif source == "codex":
        # 解析 input-messages 作为用户消息
        input_msgs = data.get("input-messages", [])
        for item in input_msgs:
            if isinstance(item, str) and item.strip():
                messages.append({"role": "user", "text": item.strip()})
            elif isinstance(item, dict):
                # 消息可能是 {"role": "user", "content": "..."} 格式
                content = item.get("content", "")
                if isinstance(content, str) and content.strip():
                    messages.append({"role": "user", "text": content.strip()})
                elif isinstance(content, list):
                    texts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            t = c.get("text", "").strip()
                            if t:
                                texts.append(t)
                        elif isinstance(c, str) and c.strip():
                            texts.append(c.strip())
                    if texts:
                        messages.append({"role": "user", "text": "\n\n".join(texts)})

        # 解析 last-assistant-message 作为AI回复
        last_msg = data.get("last-assistant-message", "")
        if isinstance(last_msg, str) and last_msg.strip():
            messages.append({"role": "assistant", "text": last_msg.strip()})
        elif isinstance(last_msg, dict):
            content = last_msg.get("content", "")
            if isinstance(content, str) and content.strip():
                messages.append({"role": "assistant", "text": content.strip()})
            elif isinstance(content, list):
                texts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        t = c.get("text", "").strip()
                        if t:
                            texts.append(t)
                    elif isinstance(c, str) and c.strip():
                        texts.append(c.strip())
                if texts:
                    messages.append({"role": "assistant", "text": "\n\n".join(texts)})

        # 如果没有解析到任何消息，回退到原始数据展示
        if not messages:
            raw = json.dumps(data, ensure_ascii=False, indent=2)
            messages.append({"role": "assistant", "text": raw})

    else:
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        messages.append({"role": "assistant", "text": raw})

    return messages


def _text_to_html(text: str) -> str:
    """将文本转换为HTML，支持代码块和基本markdown格式"""
    lines = text.split("\n")
    html_parts: list[str] = []
    in_code = False
    code_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                # 结束代码块
                code_text = _escape_html("\n".join(code_lines))
                html_parts.append(
                    '<pre style="margin:8px 0;padding:12px 16px;background:#1F2937;'
                    "color:#E5E7EB;font-size:12px;line-height:1.6;"
                    "font-family:'SF Mono','Fira Code',Consolas,monospace;"
                    'border-radius:6px;white-space:pre-wrap;word-break:break-all;'
                    'overflow:hidden;">'
                    + code_text
                    + "</pre>"
                )
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
        else:
            escaped = _escape_html(line)
            # 处理粗体
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
            if escaped.strip():
                html_parts.append(escaped + "<br>")
            else:
                html_parts.append("<br>")

    # 处理未闭合的代码块
    if in_code and code_lines:
        code_text = _escape_html("\n".join(code_lines))
        html_parts.append(
            '<pre style="margin:8px 0;padding:12px 16px;background:#1F2937;'
            "color:#E5E7EB;font-size:12px;line-height:1.6;"
            "font-family:'SF Mono','Fira Code',Consolas,monospace;"
            'border-radius:6px;white-space:pre-wrap;word-break:break-all;'
            'overflow:hidden;">'
            + code_text
            + "</pre>"
        )

    return "\n".join(html_parts)


def build_email_html(title: str, source: str, data: dict) -> str:
    """构建美观的HTML邮件，展示完整对话内容，不使用emoji"""
    # 根据来源选择主题色
    if "Claude" in title or source == "claude-code":
        accent = "#D97706"
        accent_light = "#FEF3C7"
        accent_dark = "#92400E"
    elif "Codex" in title or source == "codex":
        accent = "#059669"
        accent_light = "#D1FAE5"
        accent_dark = "#065F46"
    else:
        accent = "#2563EB"
        accent_light = "#DBEAFE"
        accent_dark = "#1E40AF"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 提取元数据
    cwd = data.get("cwd", "") if isinstance(data, dict) else ""
    session_id = data.get("session_id", "") if isinstance(data, dict) else ""

    # 提取 Codex 特有的元数据
    thread_id = data.get("thread-id", "") if isinstance(data, dict) else ""
    turn_id = data.get("turn-id", "") if isinstance(data, dict) else ""
    event_type = data.get("type", "") if isinstance(data, dict) else ""

    meta_rows: list[tuple[str, str]] = [("完成时间", now)]
    if cwd:
        meta_rows.append(("工作目录", str(cwd)))
    if session_id:
        sid = str(session_id)
        meta_rows.append(("会话ID", sid[:12] + "..." if len(sid) > 12 else sid))
    if event_type:
        meta_rows.append(("事件类型", str(event_type)))
    if thread_id:
        tid = str(thread_id)
        meta_rows.append(("线程ID", tid[:16] + "..." if len(tid) > 16 else tid))
    if turn_id:
        tuid = str(turn_id)
        meta_rows.append(("轮次ID", tuid[:16] + "..." if len(tuid) > 16 else tuid))

    meta_html = ""
    for key, value in meta_rows:
        meta_html += (
            "<tr>"
            f'<td style="padding:8px 14px;color:#6B7280;font-size:13px;'
            f"white-space:nowrap;vertical-align:top;"
            f'border-bottom:1px solid #F3F4F6;">{_escape_html(key)}</td>'
            f'<td style="padding:8px 14px;color:#111827;font-size:13px;'
            f"word-break:break-all;"
            f'border-bottom:1px solid #F3F4F6;">{_escape_html(value)}</td>'
            "</tr>"
        )

    # 提取对话
    conversation = extract_conversation(data, source) if isinstance(data, dict) else []

    # 构建对话HTML
    conversation_html = ""
    for msg in conversation:
        if msg["role"] == "user":
            role_label = "USER"
            role_color = "#4F46E5"
            bg_color = "#EEF2FF"
            border_color = "#6366F1"
        else:
            role_label = "AI ASSISTANT"
            role_color = "#047857"
            bg_color = "#F0FDF4"
            border_color = "#10B981"

        content_html = _text_to_html(msg["text"])
        conversation_html += (
            '<tr><td style="padding:0 24px 12px;">'
            '<table width="100%" cellpadding="0" cellspacing="0"'
            ' style="border-collapse:collapse;">'
            f'<tr><td style="padding:14px 16px;background:{bg_color};'
            f"border-left:3px solid {border_color};border-radius:4px;"
            f'">'
            f'<div style="font-size:11px;font-weight:700;color:{role_color};'
            f"text-transform:uppercase;letter-spacing:0.5px;"
            f'margin-bottom:8px;">{role_label}</div>'
            f'<div style="font-size:14px;color:#1F2937;line-height:1.7;">'
            f"{content_html}"
            f"</div>"
            f"</td></tr></table>"
            f"</td></tr>"
        )

    # 无对话时显示原始数据
    if not conversation_html:
        raw_text = _escape_html(
            json.dumps(data, ensure_ascii=False, indent=2)
            if isinstance(data, dict)
            else str(data)
        )
        conversation_html = (
            '<tr><td style="padding:0 24px 16px;">'
            '<pre style="margin:0;padding:16px;background:#1F2937;color:#E5E7EB;'
            "font-size:12px;line-height:1.6;"
            "font-family:'SF Mono','Fira Code',Consolas,monospace;"
            'border-radius:8px;white-space:pre-wrap;word-break:break-all;">'
            f"{raw_text}</pre></td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#F3F4F6;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
             'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="border-collapse:collapse;background:#F3F4F6;">
    <tr><td align="center" style="padding:32px 16px;">

      <!-- 主卡片 -->
      <table width="640" cellpadding="0" cellspacing="0"
             style="border-collapse:collapse;background:#FFFFFF;
                    border-radius:12px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(0,0,0,0.08);">

        <!-- 顶部色条 -->
        <tr><td style="height:4px;background:{accent};
                       font-size:0;line-height:0;">&nbsp;</td></tr>

        <!-- 标题区 -->
        <tr><td style="padding:24px 24px 16px;">
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border-collapse:collapse;">
            <tr>
              <td style="vertical-align:middle;">
                <span style="font-size:20px;font-weight:700;color:#111827;">
                  {_escape_html(title)}</span>
              </td>
              <td align="right" style="vertical-align:middle;">
                <span style="display:inline-block;padding:4px 12px;
                             background:{accent_light};color:{accent_dark};
                             font-size:11px;font-weight:600;border-radius:20px;
                             letter-spacing:0.3px;">COMPLETED</span>
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- 分割线 -->
        <tr><td style="padding:0 24px;">
          <hr style="border:none;border-top:1px solid #E5E7EB;margin:0;">
        </td></tr>

        <!-- 元数据 -->
        <tr><td style="padding:16px 24px;">
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border-collapse:collapse;background:#F9FAFB;
                        border-radius:8px;border:1px solid #E5E7EB;">
            {meta_html}
          </table>
        </td></tr>

        <!-- 对话区域标题 -->
        <tr><td style="padding:8px 24px 12px;">
          <span style="font-size:12px;font-weight:600;color:#6B7280;
                       text-transform:uppercase;letter-spacing:0.5px;">
            Conversation</span>
        </td></tr>

        <!-- 对话内容 -->
        {conversation_html}

        <!-- 底部 -->
        <tr><td style="padding:16px 24px;background:#F9FAFB;
                       border-top:1px solid #E5E7EB;">
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border-collapse:collapse;">
            <tr>
              <td style="font-size:11px;color:#9CA3AF;">
                AI Task Notify
              </td>
              <td align="right" style="font-size:11px;color:#9CA3AF;">
                {now}
              </td>
            </tr>
          </table>
        </td></tr>

      </table>

      <!-- 页脚 -->
      <table width="640" cellpadding="0" cellspacing="0"
             style="border-collapse:collapse;">
        <tr><td align="center"
                style="padding:16px 0;font-size:11px;color:#9CA3AF;">
          此邮件由系统自动生成，请勿直接回复
        </td></tr>
      </table>

    </td></tr>
  </table>
</body>
</html>"""

    return html


def send_email(
    env: dict[str, str],
    title: str,
    content: str,
    source: str = "",
    data: Optional[dict] = None,
) -> bool:
    """发送邮件通知"""
    smtp_host = get_config(env, "SMTP_HOST")
    smtp_user = get_config(env, "SMTP_USER")
    smtp_password = get_config(env, "SMTP_PASSWORD")
    email_from = get_config(env, "EMAIL_FROM")
    email_to = get_config(env, "EMAIL_TO")

    if not all([smtp_host, smtp_user, smtp_password, email_from, email_to]):
        return False

    smtp_port = int(get_config(env, "SMTP_PORT", "465"))
    use_ssl = get_config(env, "SMTP_USE_SSL", "true").lower() == "true"

    recipients = [e.strip() for e in email_to.split(",") if e.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = title
    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)

    # 纯文本回退
    msg.attach(MIMEText(content, "plain", "utf-8"))
    # 美观HTML版本（使用完整原始数据构建）
    html_content = build_email_html(title, source, data or {})
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.starttls()

        server.login(smtp_user, smtp_password)
        server.sendmail(email_from, recipients, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}", file=sys.stderr)
        return False


# ============ 通知调度 ============

CHANNEL_HANDLERS = {
    "email": send_email,
}


def send_notification(
    env: dict[str, str],
    title: str,
    content: str,
    source: str = "",
    data: Optional[dict] = None,
) -> dict[str, bool]:
    """发送通知到所有启用的渠道"""
    channels = get_enabled_channels(env)
    results = {}

    for channel in channels:
        handler = CHANNEL_HANDLERS.get(channel)
        if handler:
            try:
                results[channel] = handler(
                    env, title, content, source, data
                )
            except Exception as e:
                print(f"Channel {channel} error: {e}", file=sys.stderr)
                results[channel] = False

    return results


def format_message(source: str, event_type: str, data: dict) -> tuple:
    """格式化通知消息，返回 (title, content)，不截断内容"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if source == "claude-code":
        transcript = data.get("transcript", [])
        last_user_msg = ""
        last_ai_msg = ""

        # 遍历transcript提取最后的用户消息和AI完整回复
        for item in transcript:
            msg_type = item.get("type", "")
            msg = item.get("message", {})
            texts: list[str] = []
            if isinstance(msg, dict):
                for c in msg.get("content", []):
                    if isinstance(c, dict) and c.get("type") == "text":
                        t = c.get("text", "").strip()
                        if t:
                            texts.append(t)
            elif isinstance(msg, str) and msg.strip():
                texts.append(msg.strip())

            full_text = "\n\n".join(texts)
            if full_text:
                if msg_type == "human":
                    last_user_msg = full_text
                elif msg_type == "assistant":
                    last_ai_msg = full_text

        title = "Claude Code 任务完成"
        content = f"""**时间**: {now}
**工作目录**: {data.get('cwd', 'N/A')}
**会话ID**: {data.get('session_id', 'N/A')[:8]}...

**用户指令**:
{last_user_msg or '(无内容)'}

**AI 回复**:
{last_ai_msg or '(无内容)'}"""

    elif source == "codex":
        title = "Codex 任务完成"

        # 解析用户输入消息
        input_msgs = data.get("input-messages", [])
        user_texts: list[str] = []
        for item in input_msgs:
            if isinstance(item, str) and item.strip():
                user_texts.append(item.strip())
            elif isinstance(item, dict):
                content = item.get("content", "")
                if isinstance(content, str) and content.strip():
                    user_texts.append(content.strip())
        user_msg = "\n".join(user_texts) if user_texts else "(无内容)"

        # 解析AI回复
        last_msg = data.get("last-assistant-message", "")
        if isinstance(last_msg, str):
            ai_msg = last_msg.strip() or "(无内容)"
        elif isinstance(last_msg, dict):
            ai_msg = str(last_msg.get("content", "")).strip() or "(无内容)"
        else:
            ai_msg = "(无内容)"

        content = f"""**时间**: {now}
**工作目录**: {data.get('cwd', 'N/A')}
**事件类型**: {event_type}

**用户指令**:
{user_msg}

**AI 回复**:
{ai_msg}"""

    else:
        title = "AI 任务完成"
        content = f"""**时间**: {now}
**来源**: {source}

**数据**:
```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```"""

    return title, content


def parse_input() -> tuple:
    """
    解析输入，返回 (source, event_type, data)

    Claude Code: 通过 stdin 传入 JSON
    Codex: 通过命令行参数传入 JSON
    """
    data = {}
    source = "unknown"
    event_type = ""

    # 尝试从命令行参数读取 (Codex 方式)
    if len(sys.argv) > 1:
        try:
            data = json.loads(sys.argv[1])
            source = "codex"
            event_type = data.get("type", "")

            # Codex 只处理 agent-turn-complete 事件
            if event_type != "agent-turn-complete":
                return source, event_type, None

        except json.JSONDecodeError:
            pass

    # 尝试从 stdin 读取 (Claude Code 方式)
    if not data and not sys.stdin.isatty():
        try:
            stdin_data = sys.stdin.read()
            if stdin_data.strip():
                data = json.loads(stdin_data)
                source = "claude-code"
                event_type = "stop"
        except json.JSONDecodeError:
            pass

    return source, event_type, data


def main() -> int:
    # 加载配置
    env = load_env()

    # 检查是否有启用的渠道
    channels = get_enabled_channels(env)
    if not channels:
        print("No notification channels enabled", file=sys.stderr)
        return 0

    # 解析输入
    source, event_type, data = parse_input()

    if data is None:
        # 事件类型不需要处理
        return 0

    if not data:
        print("No valid input data", file=sys.stderr)
        return 1

    # 格式化消息
    title, content = format_message(source, event_type, data)

    # 发送通知
    results = send_notification(env, title, content, source=source, data=data)

    # 输出结果
    success_count = sum(1 for v in results.values() if v)
    print(f"Notifications sent: {success_count}/{len(results)}")
    for channel, success in results.items():
        status = "✓" if success else "✗"
        print(f"  {status} {channel}")

    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
