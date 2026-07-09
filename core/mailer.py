"""验证码邮件。SMTP_HOST 未配置 → dev 模式：验证码打印到控制台。"""
import os
import smtplib
import sys
from email.header import Header
from email.mime.text import MIMEText


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def send_code(to_email: str, code: str) -> None:
    """发验证码；dev 模式打印到 stderr；SMTP 发送失败向上抛异常由 UI 提示。"""
    if not smtp_configured():
        print(f"[mailer] DEV 模式：{to_email} 的验证码是 {code}", file=sys.stderr)
        return
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM") or user
    msg = MIMEText(f"你的验证码是 {code}，10 分钟内有效。若非本人操作请忽略。", "plain", "utf-8")
    msg["Subject"] = Header("学长组 Agent 注册验证码", "utf-8")
    msg["From"] = sender
    msg["To"] = to_email
    with smtplib.SMTP_SSL(host, port, timeout=15) as s:
        if user:
            s.login(user, password)
        s.sendmail(sender, [to_email], msg.as_string())
