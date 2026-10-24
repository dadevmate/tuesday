"""Email over plain IMAP/SMTP, straight between this Mac and the owner's mail provider: no third party, nothing stored
by Tuesday except what he asks it to remember.

Setup (once): `./run.sh --email-setup` asks for the address and an app password (Gmail: Google Account -> Security ->
2-Step Verification -> App passwords) and keeps the password in the macOS Keychain, never in a file.

Reading, searching and drafting are free. Sending needs confirmed=true AND the owner's own latest words saying yes
(the same code-level gate as the browser): a model can't send on its own, and an email can't talk it into sending.
"""
import email
import email.header
import email.policy
import imaplib
import re
import smtplib
import subprocess
from email.message import EmailMessage
from email.utils import parseaddr

from . import config

ADDRESS = config.env("TUESDAY_EMAIL_ADDRESS")
IMAP_HOST = config.env("TUESDAY_IMAP_HOST", "imap.gmail.com")
SMTP_HOST = config.env("TUESDAY_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = config.env_int("TUESDAY_SMTP_PORT", 465)
KEYCHAIN = "tuesday-email"
GMAIL = "gmail" in IMAP_HOST


def configured() -> bool:
    return bool(ADDRESS and _password())


def _password() -> str:
    if not ADDRESS:
        return ""
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN, "-a", ADDRESS, "-w"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _imap() -> imaplib.IMAP4_SSL:
    if not configured():
        raise RuntimeError("email isn't set up: run ./run.sh --email-setup")
    m = imaplib.IMAP4_SSL(IMAP_HOST)
    m.login(ADDRESS, _password())
    return m


def _decode(value) -> str:
    return str(email.header.make_header(email.header.decode_header(value or ""))) if value else ""


def _text(msg) -> str:
    """The readable body: plain text if there is one, else the HTML with its tags stripped."""
    part = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else None
    if part is None:
        return ""
    body = part.get_content() if hasattr(part, "get_content") else ""
    if part.get_content_type() == "text/html":
        body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
        body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", body)).strip()


def _gmail(task: str, writes: bool | str = False):
    """Gmail through Fable's Claude connector, when IMAP isn't set up here."""
    from . import fable
    r = fable.connector_run("In Gmail: " + task, allow_writes=writes)
    if not r["ok"]:
        raise RuntimeError(r["reason"])
    return r["result"]


def search(query: str = "", unread_only: bool = False, limit: int = 10, folder: str = "INBOX"):
    """Newest first. With Gmail, `query` uses Gmail's own search syntax (from:, subject:, newer_than:2d, …)."""
    if not configured():
        return _gmail(f"search {query or 'the inbox'}{' (unread only)' if unread_only else ''}; list the newest {limit} with id, "
                      "sender, subject, date, unread or not, and a one-line snippet each.")
    m = _imap()
    try:
        m.select(f'"{folder}"', readonly=True)
        if query and GMAIL:
            crit = ["X-GM-RAW", f'"{query}{" is:unread" if unread_only else ""}"']
        else:
            crit = (["UNSEEN"] if unread_only else ["ALL"]) + (["TEXT", f'"{query}"'] if query else [])
        _, data = m.uid("SEARCH", None, *crit)
        uids = data[0].split()[-max(1, min(int(limit or 10), 30)):][::-1]
        out = []
        for uid in uids:
            _, d = m.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] BODY.PEEK[TEXT]<0.600>)")
            parts = [x for x in d if isinstance(x, tuple)]
            head = email.message_from_bytes(parts[0][1]) if parts else {}
            snippet = parts[1][1].decode(errors="replace") if len(parts) > 1 else ""
            snippet = re.sub(r"<[^>]+>|=\r?\n|\s+", " ", snippet)[:200].strip()
            flags = (d[0][0] if d and isinstance(d[0], tuple) else b"").decode(errors="replace")
            out.append({"id": uid.decode(), "from": _decode(head.get("From")), "subject": _decode(head.get("Subject")),
                        "date": head.get("Date", ""), "unread": "\\Seen" not in flags, "snippet": snippet})
        return out
    finally:
        m.logout()


def read(message_id: str, folder: str = "INBOX"):
    if not configured():
        return _gmail(f"open message (or thread) {message_id} and give sender, recipients, date, subject and the full text.")
    m = _imap()
    try:
        m.select(f'"{folder}"', readonly=True)
        _, d = m.uid("FETCH", str(message_id), "(BODY.PEEK[])")
        raw = next((x[1] for x in d if isinstance(x, tuple)), None)
        if not raw:
            return {"error": f"no email {message_id}"}
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        return {"id": str(message_id), "from": str(msg["From"]), "to": str(msg["To"]), "date": str(msg["Date"]), "subject": str(msg["Subject"]),
                "text": _text(msg)[:6000], "message_id": str(msg["Message-ID"] or "")}
    finally:
        m.logout()


def _compose(to: str, subject: str, body: str, reply_to_id: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = ADDRESS or "me", to, subject
    if reply_to_id and configured():   # threading a reply needs IMAP's headers
        orig = read(reply_to_id)
        if "error" not in orig:
            msg["To"] = to or parseaddr(orig["from"])[1]
            msg["Subject"] = subject or ("Re: " + re.sub(r"^\s*re:\s*", "", orig["subject"], flags=re.I))
            if orig["message_id"]:
                msg["In-Reply-To"] = msg["References"] = orig["message_id"]
    msg.set_content(body)
    return msg


def draft(to: str, subject: str, body: str, reply_to_id: str | None = None) -> dict:
    """Save a draft in his mailbox (nothing is sent): he can review it in his mail app."""
    if not configured():
        return _gmail(f"create a draft{' replying to message ' + reply_to_id if reply_to_id else ''} to {to or '(the original sender)'}, "
                      f"subject {subject!r}, body:\n{body}\nDo not send it.")
    msg = _compose(to, subject, body, reply_to_id)
    m = _imap()
    try:
        folder = "[Gmail]/Drafts" if GMAIL else "Drafts"
        typ, _ = m.append(f'"{folder}"', "\\Draft", None, msg.as_bytes())
        return {"drafted": typ == "OK", "to": msg["To"], "subject": msg["Subject"], "folder": folder}
    finally:
        m.logout()


def send(to: str, subject: str, body: str, reply_to_id: str | None = None, confirmed: bool = False) -> dict:
    from .browser import YES, owner_said
    msg = _compose(to, subject, body, reply_to_id)
    if not (confirmed and YES.search(owner_said.get("text", ""))):
        return {"needs_confirmation": True, "to": msg["To"], "subject": msg["Subject"], "body": body[:500],
                "say": "Read the email back to the owner and ask him to say yes before sending."}
    if not configured():
        out = _gmail(f"send this email{' as a reply to message ' + reply_to_id if reply_to_id else ''} to {msg['To']}, subject "
                     f"{msg['Subject']!r}, body:\n{body}", writes="Gmail")
        owner_said["text"] = ""
        return {"sent": not out.startswith("NEEDS_APPROVAL"), "to": msg["To"], "subject": msg["Subject"], "detail": out[:300]}
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.login(ADDRESS, _password())
        s.send_message(msg)
    owner_said["text"] = ""   # one yes, one email
    return {"sent": True, "to": msg["To"], "subject": msg["Subject"]}
