"""
inbox.py — Full Gmail reader & sender for ULTRON via IMAP/SMTP.

Public API:
    get_unread_emails(hours=72, limit=10)
    get_unread_count(hours=72)
    get_email_summary(hours=24, limit=10)
    get_detailed_summary(hours=72, limit=10)
    read_email_content(target)
    get_emails_from(sender_query, limit=5, unread_only=True)
    get_emails_about(topic, limit=5)
    get_spam_summary(limit=10)
    get_category_summary(category, limit=10)   # "promotions"/"social"/"updates"/"forums"
    get_company_breakdown(hours=24)
    get_attachments_summary(hours=48, limit=5)
    get_important_summary(limit=5)
    get_oldest_unread()
    get_emails_on_date(date_str)
    read_specific_email(sender_query)
    send_email(to_addr, subject, body, from_account=None)
    get_active_account_name()
    get_active_account_email()
    switch_active_account()
    set_active_account(account_name)
    get_last_listed_emails()
    set_last_listed_emails(emails)
"""

import imaplib
import smtplib
import email
import html
from html.parser import HTMLParser
import os
import re
import socket
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
TIMEOUT_SEC = 15


# =====================================================================
# HTML PARSER & BODY EXTRACTION
# =====================================================================
class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head', 'title', 'meta', '[document]'):
            self.skip = True
        elif tag in ('p', 'br', 'div', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
            self.fed.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head', 'title', 'meta', '[document]'):
            self.skip = False
        elif tag in ('p', 'div', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
            self.fed.append('\n')

    def handle_data(self, d):
        if not self.skip:
            self.fed.append(d)

    def get_text(self):
        return ''.join(self.fed)


def _extract_body_from_msg(msg, max_len: int = 4000) -> str:
    """Extract clean plain text or HTML-converted text from email message."""
    plain_text = ""
    html_text = ""

    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                cdisp = str(part.get_content_disposition() or "")
                if "attachment" in cdisp.lower():
                    continue
                if ctype == "text/plain" and not plain_text:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        plain_text = payload.decode(charset, errors="replace")
                elif ctype == "text/html" and not html_text:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html_text = payload.decode(charset, errors="replace")
        else:
            ctype = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
                if ctype == "text/plain":
                    plain_text = decoded
                elif ctype == "text/html":
                    html_text = decoded
    except Exception as e:
        print(f"[Inbox] extract body error: {e}")

    body = ""
    if plain_text and plain_text.strip():
        body = plain_text.strip()
    elif html_text and html_text.strip():
        try:
            extractor = HTMLTextExtractor()
            extractor.feed(html_text)
            body = extractor.get_text()
        except Exception:
            clean = re.sub(r'(?is)<style.*?</style>', '', html_text)
            clean = re.sub(r'(?is)<script.*?</script>', '', clean)
            clean = re.sub(r'<[^<]+?>', ' ', clean)
            body = clean
        body = html.unescape(body)

    lines = [line.strip() for line in body.splitlines()]
    clean_lines = []
    consecutive_empty = 0
    for line in lines:
        if not line:
            consecutive_empty += 1
            if consecutive_empty <= 1:
                clean_lines.append("")
        else:
            consecutive_empty = 0
            clean_lines.append(line)
    body = "\n".join(clean_lines).strip()

    if len(body) > max_len:
        body = body[:max_len] + "\n... [content truncated for display]"

    return body


# =====================================================================
# LOW-LEVEL HELPERS
# =====================================================================
def _decode_header(raw):
    if not raw:
        return ""
    try:
        parts = decode_header(raw)
        out = []
        for part, enc in parts:
            if isinstance(part, bytes):
                out.append(part.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(str(part))
        return "".join(out).strip()
    except Exception:
        return str(raw).strip()


def _short_sender(from_header):
    if not from_header:
        return "unknown"
    name, addr = parseaddr(from_header)
    if name:
        return name.strip().strip('"')
    if addr and "@" in addr:
        domain = addr.split("@", 1)[1]
        return domain.split(".")[0]
    return from_header or "unknown"


def _sender_domain(from_header):
    _, addr = parseaddr(from_header or "")
    if addr and "@" in addr:
        domain = addr.split("@", 1)[1].lower()
        # strip common prefixes
        for prefix in ("mail.", "email.", "e.", "no-reply.", "noreply.", "info."):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
                break
        return domain
    return ""


# Active account and listing cache
_current_active_account = "primary"
_last_listed_emails = []


def get_last_listed_emails() -> list:
    global _last_listed_emails
    return list(_last_listed_emails)


def set_last_listed_emails(emails: list):
    global _last_listed_emails
    _last_listed_emails = list(emails or [])


def get_active_account_name() -> str:
    global _current_active_account
    return _current_active_account


def get_active_account_email() -> str:
    global _current_active_account
    cfg = _get_email_config(_current_active_account)
    return cfg[0] if cfg and cfg[0] else "Not configured"


def switch_active_account() -> str:
    global _current_active_account
    _current_active_account = "secondary" if _current_active_account == "primary" else "primary"
    return get_active_account_email()


def set_active_account(account_name: str) -> str:
    global _current_active_account
    _current_active_account = account_name
    return get_active_account_email()


def _get_email_config(account: str = "primary"):
    acc_clean = (account or "primary").lower().strip()
    sec_email = os.getenv("GMAIL_EMAIL_2", "").strip()
    sec_pwd = os.getenv("GMAIL_APP_PASSWORD_2", "").strip().replace(" ", "")
    if sec_email and (acc_clean in ("secondary", "2", "alt", "work") or acc_clean == sec_email.lower()):
        if sec_email and sec_pwd:
            return sec_email, sec_pwd
    email_addr = os.getenv("GMAIL_EMAIL", "").strip()
    app_pwd = os.getenv("GMAIL_APP_PASSWORD", "").strip().replace(" ", "")
    if not email_addr or not app_pwd:
        return None, None
    return email_addr, app_pwd


def _connect(account: str = None):
    """Return an authenticated IMAP connection, or None."""
    email_addr, app_pwd = _get_email_config(account or _current_active_account)
    if not email_addr:
        print("[Inbox] GMAIL_EMAIL / GMAIL_APP_PASSWORD not set")
        return None, None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=TIMEOUT_SEC)
        mail.login(email_addr, app_pwd)
        return mail, email_addr
    except imaplib.IMAP4.error as e:
        print(f"[Inbox] auth error: {e}")
    except (socket.timeout, OSError) as e:
        print(f"[Inbox] network error: {e}")
    except Exception as e:
        print(f"[Inbox] connect error: {type(e).__name__}: {e}")
    return None, None


def _close(mail):
    if mail is not None:
        try:
            mail.logout()
        except Exception:
            pass


def _list_folders(mail):
    """Return a list of folder names as Gmail exposes them."""
    try:
        status, data = mail.list()
        if status != "OK":
            return []
        folders = []
        for line in data:
            try:
                decoded = line.decode(errors="ignore")
                m = re.search(r'"([^"]+)"\s*$', decoded)
                if m:
                    folders.append(m.group(1))
            except Exception:
                continue
        return folders
    except Exception:
        return []


def _find_folder(mail, candidates):
    """
    Return the first folder name from `candidates` that exists on this account.
    Candidates are compared case-insensitively.
    """
    folders = _list_folders(mail)
    lower_map = {f.lower(): f for f in folders}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def _select_folder(mail, folder):
    """Select a folder. Returns True on success."""
    try:
        if re.search(r"[\s\[\]]", folder):
            name = f'"{folder}"'
        else:
            name = folder
        status, _ = mail.select(name)
        return status == "OK"
    except Exception as e:
        print(f"[Inbox] select {folder} failed: {e}")
        return False


# =====================================================================
# CORE: FETCH MESSAGES FROM A FOLDER
# =====================================================================
def _fetch_headers(mail, ids, full_body=False, folder="INBOX", use_uid=True):
    """
    Given a list of UIDs / sequence numbers, fetch headers (and optionally full body).
    Returns a list of dicts.
    """
    results = []
    for item_id in ids:
        try:
            fetch_spec = "(RFC822)" if full_body else "(RFC822.HEADER)"
            msg_data = None
            if use_uid:
                status, msg_data = mail.uid('fetch', item_id, fetch_spec)
                if status != "OK" or not msg_data or not msg_data[0]:
                    status, msg_data = mail.fetch(item_id, fetch_spec)
            else:
                status, msg_data = mail.fetch(item_id, fetch_spec)

            if not msg_data:
                continue

            raw = None
            for part in msg_data:
                if isinstance(part, tuple) and len(part) > 1 and isinstance(part[1], (bytes, bytearray)):
                    raw = part[1]
                    break
            if not raw:
                continue

            msg = email.message_from_bytes(raw)

            subject = _decode_header(msg.get("Subject", "(no subject)"))
            sender_raw = _decode_header(msg.get("From", ""))
            sender_name = _short_sender(sender_raw)
            sender_domain = _sender_domain(sender_raw)

            date_str = msg.get("Date", "")
            try:
                dt = parsedate_to_datetime(date_str)
                date_display = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_display = date_str

            has_attach = False
            try:
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_disposition() == "attachment":
                            has_attach = True
                            break
            except Exception:
                pass

            body_content = ""
            if full_body:
                body_content = _extract_body_from_msg(msg, max_len=4000)

            results.append({
                "uid": item_id.decode() if isinstance(item_id, bytes) else str(item_id),
                "from": sender_name,
                "sender_email": parseaddr(sender_raw)[1],
                "sender_domain": sender_domain,
                "subject": subject,
                "date": date_display,
                "has_attachment": has_attach,
                "body": body_content,
                "folder": folder,
            })
        except Exception as e:
            print(f"[Inbox] fetch {item_id} error: {e}")
            continue
    return results


def _search_and_fetch(mail, criteria, limit=10, newest_first=True, full_body=False, folder="INBOX"):
    """Search a selected folder and return header dicts."""
    try:
        status, data = mail.uid('search', None, criteria)
        use_uid = True
        if status != "OK" or not data or not data[0]:
            status, data = mail.search(None, criteria)
            use_uid = False
        if status != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()
        if newest_first:
            ids = list(reversed(ids))
        ids = ids[:limit]
        return _fetch_headers(mail, ids, full_body=full_body, folder=folder, use_uid=use_uid)
    except Exception as e:
        print(f"[Inbox] search error: {e}")
        return []


def fetch_email_body_by_uid(uid: str, folder: str = "INBOX") -> str:
    """Fetch full body for a specific email UID from Gmail IMAP."""
    if not uid:
        return ""
    mail, _ = _connect()
    if not mail:
        return ""
    try:
        if not _select_folder(mail, folder):
            return ""
        items = _fetch_headers(mail, [uid], full_body=True, folder=folder, use_uid=True)
        if items and items[0].get("body"):
            return items[0]["body"]
        items = _fetch_headers(mail, [uid], full_body=True, folder=folder, use_uid=False)
        if items and items[0].get("body"):
            return items[0]["body"]
        return ""
    except Exception as e:
        print(f"[Inbox] fetch_email_body_by_uid failed: {e}")
        return ""
    finally:
        _close(mail)


# =====================================================================
# PUBLIC API
# =====================================================================
def get_unread_emails(hours: int = 72, limit: int = 10):
    """Unread emails in INBOX from the last N hours."""
    global _last_listed_emails
    mail, _ = _connect()
    if not mail:
        return []
    try:
        if not _select_folder(mail, "INBOX"):
            return []
        since = (datetime.now() - timedelta(hours=hours)).strftime("%d-%b-%Y")
        items = _search_and_fetch(mail, f'(UNSEEN SINCE {since})', limit=limit, folder="INBOX")
        if not items and hours < 168:
            items = _search_and_fetch(mail, 'UNSEEN', limit=limit, folder="INBOX")
        if items:
            _last_listed_emails = items
        return items
    finally:
        _close(mail)


def get_unread_count(hours: int = 72) -> int:
    mail, _ = _connect()
    if not mail:
        return 0
    try:
        if not _select_folder(mail, "INBOX"):
            return 0
        since = (datetime.now() - timedelta(hours=hours)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(UNSEEN SINCE {since})')
        if status != "OK" or not data or not data[0].split():
            status, data = mail.search(None, 'UNSEEN')
        if status != "OK" or not data or not data[0].split():
            return 0
        return len(data[0].split())
    finally:
        _close(mail)


def get_emails_from(sender_query: str, limit: int = 5, unread_only: bool = True):
    """Emails from a specific sender (substring match on From)."""
    global _last_listed_emails
    mail, _ = _connect()
    if not mail:
        return []
    try:
        if not _select_folder(mail, "INBOX"):
            return []
        base = "UNSEEN " if unread_only else ""
        criteria = f'({base}FROM "{sender_query}")'
        items = _search_and_fetch(mail, criteria, limit=limit, folder="INBOX")
        if items:
            _last_listed_emails = items
        return items
    finally:
        _close(mail)


def get_emails_about(topic: str, limit: int = 5):
    """Emails whose subject contains a topic."""
    global _last_listed_emails
    mail, _ = _connect()
    if not mail:
        return []
    try:
        if not _select_folder(mail, "INBOX"):
            return []
        criteria = f'(SUBJECT "{topic}")'
        items = _search_and_fetch(mail, criteria, limit=limit, folder="INBOX")
        if items:
            _last_listed_emails = items
        return items
    finally:
        _close(mail)


def get_spam_summary(limit: int = 10):
    """Read the Spam folder."""
    global _last_listed_emails
    mail, _ = _connect()
    if not mail:
        return "Could not connect to Gmail."
    try:
        folder = _find_folder(mail, ["[Gmail]/Spam", "[Gmail]/Junk", "Spam", "Junk"])
        if not folder:
            return "I could not find the Spam folder."
        if not _select_folder(mail, folder):
            return "I could not open the Spam folder."
        items = _search_and_fetch(mail, "ALL", limit=limit, folder=folder)
        if not items:
            return "Your Spam folder is empty."
        _last_listed_emails = items
        total = len(items)
        lines = [f"You have {total} email{'s' if total != 1 else ''} in Spam:"]
        for i, e in enumerate(items[:8], 1):
            subj = (e["subject"] or "")[:70]
            lines.append(f"{i}. From {e['from']}: {subj}")
        if total > 8:
            lines.append(f"And {total - 8} more.")
        lines.append("\n(Say 'what is the message for <number>' to read any email)")
        return "\n".join(lines)
    finally:
        _close(mail)


def get_category_summary(category: str, limit: int = 10):
    """
    category: "promotions" | "social" | "updates" | "forums" | "primary"
    Reads the corresponding Gmail tab folder.
    """
    global _last_listed_emails
    candidates = {
        "promotions": ["[Gmail]/Category Promotions", "Promotions"],
        "social":     ["[Gmail]/Category Social", "Social"],
        "updates":    ["[Gmail]/Category Updates", "Updates"],
        "forums":     ["[Gmail]/Category Forums", "Forums"],
        "primary":    ["[Gmail]/Category Personal", "Primary"],
    }
    names = candidates.get(category.lower())
    if not names:
        return f"I don't know the category '{category}'."

    mail, _ = _connect()
    if not mail:
        return "Could not connect to Gmail."
    try:
        folder = _find_folder(mail, names)
        if not folder:
            return f"I could not find the {category} folder."
        if not _select_folder(mail, folder):
            return f"I could not open the {category} folder."
        items = _search_and_fetch(mail, "UNSEEN", limit=limit, folder=folder)
        if not items:
            return f"No unread emails in {category}."
        _last_listed_emails = items
        total = len(items)
        lines = [f"You have {total} unread {category} email{'s' if total != 1 else ''}:"]
        for i, e in enumerate(items[:6], 1):
            subj = (e["subject"] or "")[:70]
            lines.append(f"{i}. From {e['from']}: {subj}")
        if total > 6:
            lines.append(f"And {total - 6} more.")
        lines.append("\n(Say 'what is the message for <number>' to read any email)")
        return "\n".join(lines)
    finally:
        _close(mail)


def get_company_breakdown(hours: int = 24):
    """Group unread emails by sender domain to see which companies wrote."""
    emails = get_unread_emails(hours=hours, limit=50)
    if not emails:
        return f"No unread emails in the last {hours} hours."

    counts = {}
    for e in emails:
        d = e.get("sender_domain") or e.get("from") or "unknown"
        key = e["from"] if e["from"] and e["from"] != "unknown" else d
        counts[key] = counts.get(key, 0) + 1

    ordered = sorted(counts.items(), key=lambda x: -x[1])

    total = len(emails)
    lines = [f"You have {total} unread email{'s' if total != 1 else ''} from {len(ordered)} sender{'s' if len(ordered) != 1 else ''}."]
    for name, c in ordered[:10]:
        lines.append(f"{c} from {name}")
    if len(ordered) > 10:
        lines.append(f"and {len(ordered) - 10} others.")
    return ". ".join(lines) + "."


def get_attachments_summary(hours: int = 48, limit: int = 5):
    """Unread emails with attachments."""
    global _last_listed_emails
    emails = get_unread_emails(hours=hours, limit=40)
    with_att = [e for e in emails if e.get("has_attachment")]
    if not with_att:
        return f"No unread emails with attachments in the last {hours} hours."
    _last_listed_emails = with_att
    total = len(with_att)
    lines = [f"You have {total} unread email{'s' if total != 1 else ''} with attachments:"]
    for i, e in enumerate(with_att[:limit], 1):
        subj = (e["subject"] or "")[:70]
        lines.append(f"{i}. From {e['from']}: {subj}")
    lines.append("\n(Say 'what is the message for <number>' to read any email)")
    return "\n".join(lines)


def get_important_summary(limit: int = 5):
    """Emails in Gmail's 'Important' folder."""
    global _last_listed_emails
    mail, _ = _connect()
    if not mail:
        return "Could not connect to Gmail."
    try:
        folder = _find_folder(mail, ["[Gmail]/Important", "Important"])
        if not folder:
            return "I could not find the Important folder."
        if not _select_folder(mail, folder):
            return "I could not open the Important folder."
        items = _search_and_fetch(mail, "UNSEEN", limit=limit, folder=folder)
        if not items:
            return "No unread important emails."
        _last_listed_emails = items
        total = len(items)
        lines = [f"You have {total} unread important email{'s' if total != 1 else ''}:"]
        for i, e in enumerate(items[:limit], 1):
            subj = (e["subject"] or "")[:70]
            lines.append(f"{i}. From {e['from']}: {subj}")
        lines.append("\n(Say 'what is the message for <number>' to read any email)")
        return "\n".join(lines)
    finally:
        _close(mail)


def get_oldest_unread():
    """Oldest unread email in INBOX."""
    mail, _ = _connect()
    if not mail:
        return "Could not connect to Gmail."
    try:
        if not _select_folder(mail, "INBOX"):
            return "Could not open INBOX."
        status, data = mail.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0].split():
            return "You have no unread emails."
        first_uid = data[0].split()[0]
        items = _fetch_headers(mail, [first_uid], folder="INBOX", use_uid=False)
        if not items:
            return "Could not fetch the email."
        e = items[0]
        return f"Your oldest unread email is from {e['from']}, dated {e['date']}: {e['subject']}"
    finally:
        _close(mail)


def get_emails_on_date(date_str: str):
    """
    date_str: 'today', 'yesterday', or 'DD-Mon-YYYY' e.g. '20-Sep-2026'
    """
    global _last_listed_emails
    mail, _ = _connect()
    if not mail:
        return "Could not connect to Gmail."
    try:
        if not _select_folder(mail, "INBOX"):
            return "Could not open INBOX."

        if date_str.lower() == "today":
            d = datetime.now()
        elif date_str.lower() == "yesterday":
            d = datetime.now() - timedelta(days=1)
        else:
            try:
                d = datetime.strptime(date_str, "%d-%b-%Y")
            except Exception:
                return f"I don't understand the date '{date_str}'."

        imap_date = d.strftime("%d-%b-%Y")
        items = _search_and_fetch(mail, f'(SINCE {imap_date} BEFORE {(d + timedelta(days=1)).strftime("%d-%b-%Y")})', limit=10, folder="INBOX")
        if not items:
            return f"No emails on {date_str}."
        _last_listed_emails = items
        total = len(items)
        lines = [f"You had {total} email{'s' if total != 1 else ''} on {date_str}:"]
        for i, e in enumerate(items[:6], 1):
            subj = (e["subject"] or "")[:70]
            lines.append(f"{i}. From {e['from']}: {subj}")
        lines.append("\n(Say 'what is the message for <number>' to read any email)")
        return "\n".join(lines)
    finally:
        _close(mail)


def read_email_content(target: str) -> str:
    """
    Read the full content of an email directly in the chat.
    target can be:
      - an integer/index string: "4", "#4", "email 4", "item 4"
      - a sender or subject keyword: "samruka", "sam@samruka.com", "google"
    """
    global _last_listed_emails
    raw = str(target or "").strip()
    if not raw:
        return "Please specify which email to read (e.g., 'what is the message for 4')."

    # Match numeric index
    num_match = re.search(r"\b(\d+)\b", raw)
    idx = None
    if raw.isdigit():
        idx = int(raw)
    elif num_match and len(raw) <= 12:
        idx = int(num_match.group(1))

    if idx is not None:
        # Check cache first
        if 1 <= idx <= len(_last_listed_emails):
            e = _last_listed_emails[idx - 1]
            body = e.get("body")
            if not body:
                body = fetch_email_body_by_uid(e.get("uid"), folder=e.get("folder", "INBOX"))
                if body:
                    e["body"] = body

            if not body:
                body = "(No readable text content found in email body. It may contain only images or attachments.)"

            return (
                f"[GMAIL MESSAGE #{idx}]\n"
                f"• From: {e.get('from', 'Unknown')} <{e.get('sender_email', '')}>\n"
                f"• Date: {e.get('date', '')}\n"
                f"• Subject: {e.get('subject', '(no subject)')}\n\n"
                f"Message:\n{body}"
            )

        # If cache is empty or idx is beyond cache, fetch from INBOX
        fresh = get_unread_emails(hours=72, limit=max(idx, 10))
        if fresh and 1 <= idx <= len(fresh):
            e = fresh[idx - 1]
            body = e.get("body")
            if not body:
                body = fetch_email_body_by_uid(e.get("uid"), folder=e.get("folder", "INBOX"))
                if body:
                    e["body"] = body
            if not body:
                body = "(No readable text content found in email body. It may contain only images or attachments.)"

            return (
                f"[GMAIL MESSAGE #{idx}]\n"
                f"• From: {e.get('from', 'Unknown')} <{e.get('sender_email', '')}>\n"
                f"• Date: {e.get('date', '')}\n"
                f"• Subject: {e.get('subject', '(no subject)')}\n\n"
                f"Message:\n{body}"
            )
        else:
            total = len(_last_listed_emails) or len(fresh)
            return (
                f"Email #{idx} was not found. "
                f"You currently have {total} recent email(s) in your inbox list. "
                f"Say 'read my emails' to view the latest inbox list."
            )

    # Search by keyword / sender in cache first
    query = raw.lower()
    for i, e in enumerate(_last_listed_emails, 1):
        if (query in e.get("from", "").lower()
                or query in e.get("sender_email", "").lower()
                or query in e.get("subject", "").lower()):
            body = e.get("body")
            if not body:
                body = fetch_email_body_by_uid(e.get("uid"), folder=e.get("folder", "INBOX"))
                if body:
                    e["body"] = body
            if not body:
                body = "(No readable text content found in email body.)"

            return (
                f"[GMAIL MESSAGE #{i} - {e.get('from', 'Unknown')}]\n"
                f"• From: {e.get('from', 'Unknown')} <{e.get('sender_email', '')}>\n"
                f"• Date: {e.get('date', '')}\n"
                f"• Subject: {e.get('subject', '(no subject)')}\n\n"
                f"Message:\n{body}"
            )

    # Search via IMAP live
    mail, _ = _connect()
    if not mail:
        return (
            "Could not connect to Gmail to search for that email. "
            "If you are on college Wi-Fi, port 993 is blocked; please connect to a mobile hotspot."
        )
    try:
        if not _select_folder(mail, "INBOX"):
            return "Could not open INBOX."
        status, data = mail.search(None, f'(FROM "{raw}")')
        if status != "OK" or not data or not data[0].split():
            status, data = mail.search(None, f'(SUBJECT "{raw}")')
        if status != "OK" or not data or not data[0].split():
            return f"I couldn't find any emails from or about '{raw}'."

        newest = data[0].split()[-1]
        items = _fetch_headers(mail, [newest], full_body=True, folder="INBOX", use_uid=False)
        if not items:
            return f"Could not retrieve email content for '{raw}'."
        e = items[0]
        body = e.get("body") or "(No readable text content found in email body.)"
        return (
            f"[GMAIL MESSAGE - {e.get('from', 'Unknown')}]\n"
            f"• From: {e.get('from', 'Unknown')} <{e.get('sender_email', '')}>\n"
            f"• Date: {e.get('date', '')}\n"
            f"• Subject: {e.get('subject', '(no subject)')}\n\n"
            f"Message:\n{body}"
        )
    finally:
        _close(mail)


def read_specific_email(sender_query: str):
    """Read the body of the newest email from a specific sender."""
    return read_email_content(sender_query)


# =====================================================================
# NATURAL-LANGUAGE SUMMARIES
# =====================================================================
def get_email_summary(hours: int = 24, limit: int = 10) -> str:
    """Grouped-by-sender summary — the default 'what are my emails' output."""
    emails = get_unread_emails(hours=hours, limit=limit)
    if not emails:
        return f"No unread emails in the last {hours} hours."

    total = len(emails)
    counts = {}
    order = []
    for e in emails:
        s = e["from"] or "unknown"
        if s not in counts:
            counts[s] = 0
            order.append(s)
        counts[s] += 1

    sender_parts = []
    for s in order:
        c = counts[s]
        sender_parts.append(f"{c} from {s}" if c > 1 else f"1 from {s}")

    plural = "email" if total == 1 else "emails"
    summary = f"You have {total} unread {plural}"
    summary += " from today. " if hours == 24 else f" from the last {hours} hours. "
    summary += "Sources: " + ", ".join(sender_parts) + ". "

    details = []
    for e in emails[:3]:
        subj = e["subject"]
        if len(subj) > 60:
            subj = subj[:57] + "..."
        details.append(f"{e['from']} — {subj}")
    summary += "Recent: " + ". ".join(details)
    if total > 3:
        summary += f". And {total - 3} more."
    summary += " (Say 'what is the message for 1' or 'read email 1' to view full content)"

    return summary.strip()


def get_detailed_summary(hours: int = 72, limit: int = 10) -> str:
    emails = get_unread_emails(hours=hours, limit=limit)
    if not emails:
        return f"No unread emails in the last {hours} hours."
    total = len(emails)
    lines = [f"You have {total} unread email{'s' if total != 1 else ''}:"]
    for i, e in enumerate(emails, 1):
        subj = (e["subject"] or "(no subject)")[:80]
        lines.append(f"{i}. From {e['from']}: {subj}")
    lines.append("\n(Say 'what is the message for <number>' to read any email)")
    return "\n".join(lines)


# =====================================================================
# SEND EMAIL VIA SMTP
# =====================================================================
def send_email(to_addr: str, subject: str, body: str, from_account: str = None) -> str:
    """Send an email to a recipient via SMTP (smtp.gmail.com)."""
    email_addr, app_pwd = _get_email_config(from_account or _current_active_account)
    if not email_addr or not app_pwd:
        return "Email is not configured in .env. Please set GMAIL_EMAIL and GMAIL_APP_PASSWORD."

    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    to_addr = (to_addr or "").strip()
    if not to_addr or "@" not in to_addr:
        return f"Invalid recipient email address: '{to_addr}'."

    msg = MIMEMultipart()
    msg['From'] = email_addr
    msg['To'] = to_addr
    msg['Subject'] = subject or "Message from Ultron"
    msg.attach(MIMEText(body or "", 'plain'))

    last_err = None
    for port, use_ssl in [(587, False), (465, True)]:
        try:
            if use_ssl:
                server = smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=12)
            else:
                server = smtplib.SMTP("smtp.gmail.com", port, timeout=12)
                server.starttls()
            server.login(email_addr, app_pwd)
            server.send_message(msg)
            server.quit()
            return f"Email successfully sent to {to_addr}."
        except Exception as e:
            last_err = e
            continue

    return (
        f"Could not send email ({last_err}). "
        "Your current campus/college network (10.1.x.x) is blocking outgoing mail ports (587/465). "
        "Connecting to a mobile hotspot will allow Ultron to send emails immediately."
    )


if __name__ == "__main__":
    print("=" * 60)
    print("Gmail IMAP — full test suite")
    print("=" * 60)

    cfg_email, cfg_pwd = _get_email_config()
    print(f"Email configured: {cfg_email or 'NO'}")
    print(f"Password configured: {'YES' if cfg_pwd else 'NO'}")
    print()

    print("--- Unread count (24h) ---")
    print(get_unread_count(hours=24))
    print()

    print("--- Summary ---")
    print(get_email_summary(hours=24, limit=10))
    print()

    print("--- Company breakdown ---")
    print(get_company_breakdown(hours=24))
    print()

    print("--- Spam ---")
    print(get_spam_summary(limit=5))
    print()

    print("--- Promotions ---")
    print(get_category_summary("promotions", limit=5))
    print()

    print("--- Oldest unread ---")
    print(get_oldest_unread())
    print()

    print()