import logging

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format

logger = logging.getLogger(__name__)

ROLE_HINTS = {
    "foreman": "Вы будете вести этапы и задачи, составлять смету и назначать исполнителей.",
    "executor": "Вы увидите назначенные вам задачи, сможете отчитываться фото и отвечать на замечания.",
}


def local_only(url):
    """A link to 127.0.0.1 / localhost opens only on the computer where the site runs, not on a phone."""
    host = url.split("//", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]").lower()
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def send_invitation_email(invitation, accept_url):
    """Email the invitation link (HTML in the style of the site + plain text).

    Returns False if sending failed; the link can still be passed on by hand."""
    inviter = invitation.invited_by.get_full_name() or invitation.invited_by.email
    context = {
        "invitation": invitation, "accept_url": accept_url, "inviter": inviter,
        "role_hint": ROLE_HINTS.get(invitation.role, ""), "local_only": local_only(accept_url),
        "expires": date_format(timezone.localtime(invitation.expires_at), "j E Y, H:i"),
    }
    subject = render_to_string("projects/emails/invitation_subject.txt", context).strip()
    body = render_to_string("projects/emails/invitation_email.txt", context)
    html = render_to_string("projects/emails/invitation_email.html", context)
    try:
        message = EmailMultiAlternatives(subject, body, None, [invitation.email])
        message.attach_alternative(html, "text/html")
        message.send()
    except Exception:  # SMTP errors must not break the invitation itself
        logger.exception("Could not send invitation %s to %s", invitation.pk, invitation.email)
        return False
    return True
