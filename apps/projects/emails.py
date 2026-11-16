import logging

from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_invitation_email(invitation, accept_url):
    """Email the invitation link. Returns False if sending failed; the link can still be passed on by hand."""
    inviter = invitation.invited_by.get_full_name() or invitation.invited_by.email
    context = {"invitation": invitation, "accept_url": accept_url, "inviter": inviter}
    subject = render_to_string("projects/emails/invitation_subject.txt", context).strip()
    body = render_to_string("projects/emails/invitation_email.txt", context)
    try:
        send_mail(subject, body, None, [invitation.email])
    except Exception:  # SMTP errors must not break the invitation itself
        logger.exception("Could not send invitation %s to %s", invitation.pk, invitation.email)
        return False
    return True
